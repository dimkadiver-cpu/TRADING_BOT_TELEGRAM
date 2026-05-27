# Structural Move Stop With Risk Cap Design

Date: 2026-05-27
Status: Draft
Scope: parser_v2 canonical contract + runtime_v2 lifecycle behavior for Telegram updates like `Стоп лосс переносим за указанный минимум, тем самым сокращаем риск до 0.5%`

## 1. Goal

Supportare update Telegram che:

- chiedono di spostare lo stop rispetto a un livello strutturale, non a un prezzo numerico;
- esprimono un vincolo di rischio risultante;
- devono essere risolti nel runtime operativo, non nel parser testuale.

Caso target:

```text
Стоп лосс переносим за указанный минимум, тем самым сокращаем риск до 0.5%
```

Semantica desiderata:

- intent operativo: `MOVE_STOP`
- riferimento stop: `dietro il minimo indicato`
- vincolo: `il rischio residuo della posizione deve diventare <= 0.5%`

## 2. Problema attuale

### 2.1 Parser attuale

Il parser canonico supporta oggi tre semantiche per `SET_STOP`:

- `ENTRY`
- `PRICE`
- `TP_LEVEL`

Riferimenti:

- `src/parser_v2/contracts/canonical_message.py`
- `src/parser_v2/translation/canonical_translator.py`

Il caso `MOVE_STOP` senza prezzo numerico oggi degrada a `ENTRY` come fallback BE:

- warning: `move_stop_no_price_defaulted_to_be`

Questo fallback era accettabile per frasi vaghe tipo:

```text
Стоп лосс переносим за минимум как показано на графике
```

ma non e' corretto quando il messaggio contiene anche un vincolo esplicito di rischio.

### 2.2 Lifecycle attuale

Il lifecycle esegue realmente solo:

- `SET_STOP target_type=ENTRY` -> `MOVE_STOP_TO_BREAKEVEN`

I target `PRICE` e `TP_LEVEL` oggi non vengono ancora applicati e finiscono in:

- `REVIEW_REQUIRED`
- reason: `unsupported_set_stop_target_type`

Riferimento:

- `src/runtime_v2/lifecycle/entry_gate.py`

### 2.3 Gap semantico

Il messaggio target esprime due vincoli simultanei:

1. vincolo strutturale: `за указанный минимум`
2. vincolo quantitativo: `сокращаем риск до 0.5%`

Il sistema attuale non ha un contratto per rappresentare entrambi senza:

- inventare un prezzo nel parser;
- perdere il significato del livello strutturale;
- confondere il `0.5%` con un normale `risk_hint` da segnale.

## 3. Decisione

Non introdurre un nuovo intent.

Restare su:

- `MOVE_STOP`

ed estendere il contratto di `SET_STOP` con una nuova semantica runtime-resolved.

## 4. Contratto proposto

### 4.1 Nuovo target type

Aggiungere a `SetStopTargetType`:

- `STRUCTURAL_LEVEL`

### 4.2 Payload `SetStopOperation`

Estendere `SetStopOperation` con:

- `structural_level: "MINIMUM" | "MAXIMUM" | None`
- `reference_scope: "INDICATED" | "LOCAL" | None`
- `risk_cap_percent: float | None`

Schema concettuale:

```python
class SetStopOperation(CanonicalModel):
    target_type: Literal["ENTRY", "PRICE", "TP_LEVEL", "STRUCTURAL_LEVEL"]
    price: Price | None = None
    tp_level: int | None = None
    structural_level: Literal["MINIMUM", "MAXIMUM"] | None = None
    reference_scope: Literal["INDICATED", "LOCAL"] | None = None
    risk_cap_percent: float | None = None
```

Validazione:

- `PRICE` richiede solo `price`
- `TP_LEVEL` richiede solo `tp_level`
- `ENTRY` proibisce gli altri campi
- `STRUCTURAL_LEVEL` richiede `structural_level`
- `risk_cap_percent` e' opzionale ma ammessa solo con `STRUCTURAL_LEVEL`

### 4.3 Output canonico atteso

Per il caso target:

```json
{
  "action_type": "SET_STOP",
  "set_stop": {
    "target_type": "STRUCTURAL_LEVEL",
    "structural_level": "MINIMUM",
    "reference_scope": "INDICATED",
    "risk_cap_percent": 0.5
  },
  "source_intent": "MOVE_STOP"
}
```

## 5. Parser responsibilities

Il parser deve:

- riconoscere il marker `MOVE_STOP`
- estrarre il riferimento strutturale:
  - `за указанный минимум`
  - `под минимум`
  - future varianti simmetriche `за максимум`, `над максимум`
- estrarre il cap rischio se espresso come risultato operativo:
  - `сокращаем риск до 0.5%`
  - `риск до 0.5%`

Il parser non deve:

- calcolare il prezzo stop finale;
- scegliere un minimo/massimo dal mercato;
- reinterpretare questo `0.5%` come normale `risk_hint` del segnale di apertura.

## 6. Lifecycle responsibilities

La risoluzione del target deve avvenire nel lifecycle, non nel parser.

### 6.1 Quando e' ammesso

`STRUCTURAL_LEVEL` e' ammesso solo per chain in:

- `OPEN`
- `PARTIALLY_CLOSED`

Non e' ammesso per:

- `WAITING_ENTRY`

Motivo:

- il vincolo di rischio risultante dipende dalla posizione reale e dall'`entry_avg_price`

### 6.2 Input runtime richiesti

Per applicare l'update servono:

- `entry_avg_price`
- `current_stop_price` o `expected_stop_price`
- lato posizione (`LONG` / `SHORT`)
- stato della chain
- riferimento strutturale risolto dal motore

### 6.3 Nuova porta di risoluzione strutturale

Il lifecycle deve usare un resolver dedicato, non logica parser-side.

Contratto concettuale:

```python
class StructuralStopResolver(Protocol):
    def resolve(
        self,
        *,
        chain: TradeChain,
        structural_level: str,
        reference_scope: str | None,
    ) -> float | None: ...
```

Semantica minima:

- `MINIMUM` su `LONG` -> prezzo stop sotto il minimo rilevante
- `MAXIMUM` su `SHORT` -> prezzo stop sopra il massimo rilevante

Il meccanismo concreto di lookup del minimo/massimo e' fuori scope di questa spec.
Può iniziare con un provider statico/test double e poi evolvere verso fonte exchange/chart.

## 7. Risk rule

### 7.1 Interpretazione di `0.5%`

Nel caso target, `0.5%` va interpretato come:

- cap massimo del rischio residuo della posizione

Non come:

- distanza percentuale del prezzo stop dall'entry
- rischio del nuovo segnale

### 7.2 Formula

Dato:

- `entry_avg_price`
- `candidate_stop_price`
- `open_position_qty`

il rischio residuo in quote currency e':

```text
residual_risk_abs = open_position_qty * abs(entry_avg_price - candidate_stop_price)
```

La percentuale di rischio risultante va confrontata con la stessa base usata dal runtime per il sizing/risk accounting della chain.

Per questa prima versione, la base va letta dal `risk_snapshot` / config effettiva della chain, senza introdurre un nuovo modello di account risk.

### 7.3 Regola di accettazione

Il lifecycle:

1. risolve il prezzo strutturale candidato;
2. calcola il rischio residuo risultante;
3. verifica `resulting_risk_pct <= risk_cap_percent`;
4. se la regola passa, emette un normale comando `MOVE_STOP` con `new_stop_price`;
5. se la regola fallisce, produce `REVIEW_REQUIRED`.

## 8. Conflict handling

Il riferimento strutturale e il cap di rischio possono entrare in conflitto.

Esempio:

- `za указанным минимумом` produce un prezzo troppo lontano;
- il rischio risultante resta > `0.5%`.

In quel caso il sistema non deve:

- stringere artificialmente lo stop inventando un altro minimo;
- ignorare il vincolo strutturale;
- ignorare il vincolo di rischio.

Deve invece fare:

- `REVIEW_REQUIRED`
- reason: `structural_stop_risk_cap_unmet`

## 9. Command emission

Se la risoluzione ha successo, il lifecycle deve emettere:

- `command_type="MOVE_STOP"`

payload:

```json
{
  "symbol": "<chain.symbol>",
  "side": "<chain.side>",
  "new_stop_price": 123.45,
  "is_breakeven": false,
  "position_idx": 1,
  "protection_style": "attached_full"
}
```

Questa parte riusa il gateway esistente. Non serve un nuovo command type exchange.

## 10. Rollout strategy

### 10.1 Fase 1

Introdurre il nuovo contratto parser + lifecycle con resolver stub/manuale e test unitari.

Se il resolver non sa determinare il livello strutturale:

- `REVIEW_REQUIRED`
- reason: `structural_stop_reference_unresolved`

### 10.2 Fase 2

Introdurre una fonte reale per i livelli strutturali:

- mercato/exchange
- eventuale fonte chart-derived
- o provider esterno dedicato

Questa fase e' esplicitamente fuori scope della presente spec.

## 11. Acceptance criteria

Done significa:

- il parser non degrada più questo caso a BE implicito;
- il canonical message conserva sia il riferimento strutturale sia il cap di rischio;
- il lifecycle prova a risolvere il prezzo reale solo quando la chain e' operativamente pronta;
- il comando finale verso exchange resta un normale `MOVE_STOP`;
- i casi irrisolvibili o incoerenti vanno in review con reason esplicito.

Pass/fail observables:

1. `Стоп лосс переносим за указанный минимум, тем самым сокращаем риск до 0.5%` produce `SET_STOP/STRUCTURAL_LEVEL`, non `ENTRY`.
2. Una chain `OPEN` con resolver che restituisce un prezzo coerente genera un comando `MOVE_STOP`.
3. Una chain `WAITING_ENTRY` non tenta la risoluzione e finisce in review.
4. Un prezzo strutturale che lascia rischio > `0.5%` finisce in review.
5. Il vecchio caso `MOVE_STOP` con prezzo numerico continua a funzionare come `SET_STOP/PRICE`.

## 12. Test plan

Parser / translator:

- `MOVE_STOP` + `за указанный минимум` -> `STRUCTURAL_LEVEL/MINIMUM`
- stesso caso + `риск до 0.5%` -> `risk_cap_percent=0.5`
- `MOVE_STOP` con prezzo numerico resta `PRICE`
- `MOVE_STOP_TO_BE` resta `ENTRY`

Lifecycle:

- chain `OPEN` + structural resolver success + risk cap pass -> `MOVE_STOP`
- chain `OPEN` + resolver success + risk cap fail -> `REVIEW_REQUIRED`
- chain `OPEN` + resolver unresolved -> `REVIEW_REQUIRED`
- chain `WAITING_ENTRY` -> `REVIEW_REQUIRED`

Regressioni:

- nessun impatto su `MOVE_STOP_TO_BREAKEVEN`
- nessun impatto sul fallback legacy `move_stop_no_price_defaulted_to_be` per i casi che non espongono semantica strutturale esplicita

## 13. File likely affected

Parser contract / translation:

- `src/parser_v2/contracts/enums.py`
- `src/parser_v2/contracts/canonical_message.py`
- `src/parser_v2/contracts/entities.py`
- `src/parser_v2/translation/canonical_translator.py`
- trader profile intent extractors rilevanti

Runtime:

- `src/runtime_v2/lifecycle/entry_gate.py`
- nuovo resolver o modulo helper lifecycle per structural stop resolution
- eventuali test lifecycle dedicati

Docs/tests:

- `src/parser_v2/tests/...`
- `src/runtime_v2/.../tests/...`

## 14. Non-goals

Fuori scope:

- definire in questa spec come il sistema trova il minimo/massimo “vero” sul grafico;
- introdurre trailing stop generico;
- cambiare il modello globale di `risk_remaining`;
- riconciliare automaticamente `risk_remaining` dopo ogni manual stop move.

## 15. Recommended implementation order

1. Estendere il contratto canonico con `STRUCTURAL_LEVEL`.
2. Estrarre `structural_level` + `risk_cap_percent` nel parser.
3. Far passare l'update nel gate enrichment senza cambiare la policy `MOVE_STOP`.
4. Implementare nel lifecycle il ramo `SET_STOP/STRUCTURAL_LEVEL`.
5. Inserire un resolver stub che può restituire `None`.
6. Aggiungere review reasons esplicite.
7. Coprire con test parser + lifecycle.

# MOVE_STOP Risk-Target Design

Data: 2026-06-14
Stato: Draft approvato in chat, in attesa di review utente

## Obiettivo

Estendere `MOVE_STOP` per supportare update trader che non indicano un prezzo SL esplicito o un livello TP, ma un rischio residuo target espresso come:

- percentuale del rischio iniziale allocato alla posizione
- multiplo `R` / `RR` del rischio iniziale allocato alla posizione

Esempi in scope:

- `сокращаем риск до 0.4%`
- `сокращаем риск до 0.4R`
- frasi equivalenti introdotte da marker dedicati accanto a `MOVE_STOP`

Il comportamento legacy di `MOVE_STOP` deve restare invariato:

- prezzo esplicito -> move stop a prezzo
- livello TP -> move stop al prezzo di quel TP
- nessun dato operativo estratto -> fallback attuale a `ENTRY` / breakeven

## Scope

Scope parser/profilo per questa iterazione:

- lavorare solo sul profilo `trader_prova`

Scope shared comunque necessario:

- contratti parser condivisi
- translator canonico condiviso
- lifecycle update path condiviso

Fuori scope per questa iterazione:

- estendere subito i marker/extractor di `trader_a`, `trader_b`, `trader_c`, `trader_d`
- generalizzare i marker risk-based a tutti i profili senza evidenza di utilizzo

## Contesto attuale

Oggi `MOVE_STOP` esiste gia' end-to-end, ma solo in forma price-based:

- parser entities: `MoveStopEntities` supporta solo `new_stop_price` e `stop_to_tp_level`
- canonical: `MOVE_STOP` produce `SET_STOP` con target `PRICE`, `TP_LEVEL` oppure `ENTRY`
- lifecycle: `ENTRY` va su `_apply_move_to_be()`, `PRICE` e `TP_LEVEL` vanno su `_apply_move_stop_price()`
- gateway/exchange: il comando finale e' sempre un move-stop a prezzo gia' risolto

Quindi il sistema oggi non ha una semantica nativa "riduci il rischio residuo a X". Il rischio target va modellato prima del gateway, nel parser/canonical/lifecycle.

## Decisione di design

Non introduciamo un nuovo intent separato.

Manteniamo `MOVE_STOP` come intent principale e aggiungiamo una semantica opzionale risk-based tramite un indicatore strutturato estratto dal parser.

### Motivazione

- evita di spezzare la tassonomia esistente
- mantiene compatibilita' con i rami attuali di `MOVE_STOP`
- segue il pattern gia' usato in altri casi dove l'intent resta invariato ma il dettaglio operativo ne modifica la risoluzione
- limita l'impatto su gateway/exchange, che continuano a ricevere solo un `new_stop_price`

## Acceptance Contract

Done significa:

- `MOVE_STOP` continua a funzionare per prezzo esplicito, TP level e fallback BE come oggi
- `MOVE_STOP` supporta anche un target rischio residuo `%` o `R/RR`
- il lifecycle converte il target rischio in un `new_stop_price` usando lo stato reale della chain aperta
- il gateway non cambia contratto operativo
- il sistema di clean log e multi-chain summary non perde chiarezza per i move stop risk-based

### Pass/Fail Criteria

1. Un messaggio `MOVE_STOP` con `0.4%` viene tradotto in un move stop che lascia `0.4%` del rischio iniziale allocato alla chain.
2. Un messaggio `MOVE_STOP` con `0.4R` viene tradotto in un move stop che lascia `0.4 * initial_risk_amount`.
3. I casi legacy `PRICE`, `TP_LEVEL`, `ENTRY` restano invariati.
4. Se mancano i dati runtime necessari (`initial_risk_amount`, `entry_avg_price`, `open_position_qty`), il caso non degrada in modo silenzioso a prezzo errato.
5. `UPDATE_DONE`, `UPDATE_PARTIAL` e `MULTI_CHAIN_SUMMARY` restano renderizzabili e leggibili.

### Segnali secondari

- test parser
- test canonical translator
- test lifecycle entry gate
- test clean log formatter / synthesis

## Approcci considerati

### Opzione A - nuovo intent separato

Esempio: aggiungere un intent tipo `REDUCE_RISK_STOP`.

Pro:

- separazione semantica forte

Contro:

- frammenta una famiglia operativa che oggi e' chiaramente `MOVE_STOP`
- aumenta il costo di ammissione update, formatter e summary

### Opzione B - mantenere `MOVE_STOP` e aggiungere un indicatore risk-based

Pro:

- compatibile con il modello attuale
- gateway invariato
- minor surface area

Contro:

- richiede estensione esplicita delle entities e del canonical contract

### Raccomandazione

Usare l'opzione B.

## Design dei contratti

### Parser entities

Estendere `MoveStopEntities` con un target di riduzione rischio strutturato.

Forma concettuale:

```python
class RiskReductionTarget:
    unit: Literal["PERCENT_OF_INITIAL_RISK", "R_MULTIPLE"]
    value: float


class MoveStopEntities(IntentEntities):
    new_stop_price: Price | None = None
    stop_to_tp_level: int | None = None
    risk_reduction_target: RiskReductionTarget | None = None
```

### Semantica dei valori

- `0.4%` -> `unit=PERCENT_OF_INITIAL_RISK`, `value=0.4`
- `0.4R` o `0.4RR` -> `unit=R_MULTIPLE`, `value=0.4`

Convenzione fissata:

- `1R = 100% del rischio iniziale allocato alla chain`

Quindi:

- `0.4R = 40% del rischio iniziale`

## Semantic markers

Accanto ai marker gia' esistenti di `MOVE_STOP`, aggiungere marker dedicati per contesto di riduzione rischio, per esempio:

- `сокращаем риск до`
- varianti equivalenti russe
- eventuali varianti inglesi/ibride se gia' usate dal trader

Questi marker non introducono un nuovo intent distinto: servono ad aiutare l'entity extractor di `MOVE_STOP` a popolare `risk_reduction_target`.

## Design canonical

### Target type

Estendere `SetStopTargetType` con:

- `RISK_TARGET`

Traduzione di `MOVE_STOP`:

- `new_stop_price` -> `SET_STOP target_type=PRICE`
- `stop_to_tp_level` -> `SET_STOP target_type=TP_LEVEL`
- `risk_reduction_target` -> `SET_STOP target_type=RISK_TARGET`
- nessun dato operativo -> `SET_STOP target_type=ENTRY` come fallback legacy

Questa scelta evita che il lifecycle debba inferire il ramo risk-based dal testo grezzo o da warning.

## Design lifecycle

### Ramo nuovo

Aggiungere un nuovo ramo nel gate update, ad esempio:

- `_apply_move_stop_risk_target()`

Branching previsto:

- `ENTRY` -> `_apply_move_to_be()`
- `PRICE` -> `_apply_move_stop_price()`
- `TP_LEVEL` -> risoluzione TP price + `_apply_move_stop_price()`
- `RISK_TARGET` -> `_apply_move_stop_risk_target()`

### Fonte del rischio iniziale

Ordine di risoluzione:

1. `chain.initial_risk_amount`
2. fallback controllato a `risk_snapshot_json.risk_amount`

Il fallback esiste per compatibilita' con chain legacy o dati storici incompleti, ma `initial_risk_amount` resta la fonte primaria.

### Formula di conversione

Definizioni:

- `base_initial_risk`
- `target_abs_risk`
- `open_position_qty`
- `entry_avg_price`

Conversione target:

- `%`:
  - `target_abs_risk = base_initial_risk * value / 100`
- `R`:
  - `target_abs_risk = base_initial_risk * value`

Conversione in distanza prezzo:

- `distance = target_abs_risk / open_position_qty`

Conversione in nuovo stop:

- `LONG`:
  - `new_stop_price = entry_avg_price - distance`
- `SHORT`:
  - `new_stop_price = entry_avg_price + distance`

### Invarianti di sicurezza

Il ramo risk-based deve rispettare:

1. Non allargare il rischio rispetto allo stop attuale.
2. Non produrre un prezzo senza avere `entry_avg_price`.
3. Non produrre un prezzo senza avere `open_position_qty > 0`.
4. Non usare un rischio iniziale nullo o assente.

### Clamp / degradazione

Regole raccomandate:

- se `target_abs_risk <= 0` -> trattare come BE
- se il prezzo risultante e' a BE o migliore di BE -> usare il ramo BE
- se il target richiesto e' peggiore o uguale al rischio attuale -> `NOOP`

La regola importante e':

- mai peggiorare la protezione della chain a causa di un update risk-based ambiguo o mal calcolato

## Logging e clean log

### Verifica di compatibilita'

Il sistema attuale di logging update non dipende dal tipo parser/canonical originale, ma dal payload degli eventi `TELEGRAM_UPDATE_ACCEPTED` in `entry_gate.py`.

Per `MOVE_STOP`, oggi il clean log usa:

- `action = "MOVE_STOP"`
- `old_sl_price`
- `new_sl_price`
- opzionale `reference` (`Price`, `TP_1`, ...)

Questo significa che il ramo risk-based non rompe automaticamente il logging se continua a emettere:

```json
{
  "action": "MOVE_STOP",
  "old_sl_price": ...,
  "new_sl_price": ...
}
```

### Rischio reale

Il rischio vero non e' la rottura del pipeline, ma la perdita di chiarezza nel testo utente:

- oggi `MOVE_STOP` puo' mostrare `Reference: TP_1` o `Reference: Price`
- un ramo risk-based non deve fingere di essere un `TP` o un `Price` se non lo e'

### Decisione

Il ramo risk-based deve continuare a emettere `action: "MOVE_STOP"` per compatibilita' con:

- `_write_update_clean_log()`
- `_render_update_display_lines()`
- `MULTI_CHAIN_SUMMARY`

Ma deve aggiungere metadata dedicato, ad esempio:

```json
{
  "action": "MOVE_STOP",
  "old_sl_price": 100.0,
  "new_sl_price": 105.0,
  "reference": "Risk",
  "risk_target_unit": "PERCENT_OF_INITIAL_RISK",
  "risk_target_value": 0.4
}
```

### Rendering utente

Il formatter / synthesis deve mostrare il ramo risk-based in modo leggibile, per esempio:

```text
SL: 100 -> 105
Reference: Risk
```

oppure, se si decide di esporre maggiore dettaglio:

```text
SL: 100 -> 105
Reference: Risk 0.4%
```

Raccomandazione:

- fase 1: `Reference: Risk` per mantenere basso il cambiamento
- fase 2 opzionale: arricchire il formatter con `Risk 0.4%` / `Risk 0.4R`

### Vincolo di compatibilita'

Non va riusato `MOVE_SL_TO_BE` per il ramo risk-based, salvo il caso in cui il target clampi esplicitamente a BE.

Quindi:

- risk-based normale -> `MOVE_STOP`
- clamp a BE -> ramo BE con `MOVE_SL_TO_BE`

## Gateway ed exchange

Nessun cambio di contratto richiesto.

Il gateway continua a ricevere:

- `command_type = MOVE_STOP`
- `payload.new_stop_price`

La semantica risk-based viene risolta interamente nel lifecycle prima del gateway.

Questo evita modifiche a:

- `execution_gateway/gateway.py`
- `order_builder.py`
- adapter Bybit

## File coinvolti

### Parser / contracts

- `src/parser_v2/contracts/entities.py`
- `src/parser_v2/contracts/enums.py`
- `src/parser_v2/contracts/canonical_message.py`
- `src/parser_v2/translation/canonical_translator.py`

### Parser profiles

- solo `trader_prova` in questa iterazione:
  - semantic markers relativi
  - extractor `MOVE_STOP`

### Lifecycle

- `src/runtime_v2/lifecycle/entry_gate.py`
- eventuale helper puro nuovo per il calcolo `risk target -> stop price`

### Test

- `tests/parser_v2/...`
- `tests/runtime_v2/lifecycle/test_entry_gate.py`
- `tests/runtime_v2/control_plane/test_clean_log_formatter.py`
- eventuali test `outbox_writer` / summary se il formatter viene arricchito

## Strategia di test

### Parser

- `MOVE_STOP` con `0.4%`
- `MOVE_STOP` con `0.4R`
- regressione prezzo esplicito
- regressione TP level
- regressione fallback BE

### Canonical

- `risk_reduction_target` -> `RISK_TARGET`
- assenza target -> fallback `ENTRY`

### Lifecycle

- `LONG` con `%`
- `SHORT` con `%`
- `LONG` con `R`
- `SHORT` con `R`
- `target_abs_risk <= 0` -> BE
- target peggiore dell'attuale -> NOOP
- dati runtime mancanti -> review/noop controllato

### Logging

- `UPDATE_DONE` con move stop risk-based produce payload renderizzabile
- `MULTI_CHAIN_SUMMARY` con move stop risk-based non perde la riga SL
- il ramo BE continua a usare `MOVE_SL_TO_BE`

## Migrazioni e rollout

### DB

Nessuna migrazione DB obbligatoria prevista se:

- `initial_risk_amount` continua a essere gia' presente e valorizzato per le nuove chain
- il fallback a `risk_snapshot_json.risk_amount` copre la compatibilita' storica

### Compatibilita'

- nessuna modifica al contratto exchange
- nessuna modifica ai command type esistenti
- nessuna modifica ai branch legacy di `MOVE_STOP`

## Rischi aperti

1. Alcune chain storiche potrebbero avere `initial_risk_amount` nullo e `risk_snapshot_json` incompleto.
2. Il formatter clean log potrebbe richiedere un piccolo adeguamento per distinguere bene `Reference: Risk`.
3. I semantic markers dei trader potrebbero richiedere tuning per distinguere "riduciamo il rischio" da testo narrativo non operativo.

## Out of scope

- introduzione di un nuovo intent separato da `MOVE_STOP`
- modifica del gateway o dell'adapter exchange
- redesign generale del sistema di logging update

## Suggested Commit Message

`docs: specify move stop risk-target design`

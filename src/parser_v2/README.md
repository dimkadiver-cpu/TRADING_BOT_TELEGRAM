# parser_v2 — Guida struttura e configurazione `rules.json`

---

## Struttura generale

```
parser_v2/
├── contracts/          modelli Pydantic (input/output)
├── core/               pipeline di processing
├── translation/        traduzione → CanonicalMessage
├── profiles/           logica specifica per trader
│   └── trader_a/
│       ├── profile.py
│       ├── signal_extractor.py
│       ├── intent_entity_extractor.py
│       ├── semantic_markers.json   ← pattern testuali
│       └── rules.json              ← regole di disambiguazione
└── tests/
```

---

## Pipeline di parsing

```
testo grezzo
    │
    ▼
TextNormalizer          lowercase, normalizza unicode, rimuove righe vuote
    │
    ▼
MarkerMatcher           cerca pattern da semantic_markers.json
    │
    ▼
MarkerEvidenceResolver  applica regole di soppressione (da rules.json)
    │
    ▼
Profile.extract_signal()            estrae setup di trading (simbolo, lato, entrate, SL, TP)
Profile.extract_intent_entities()   estrae intenti e entità per ogni intent
    │
    ▼
LocalDisambiguator      disambiguazione intenti (da rules.json)
    │
    ▼
TargetHintsExtractor    trova riferimenti target (link, reply, simbolo, scope globale)
    │
    ▼
TargetBindingResolver   lega ogni intent al suo target (per riga)
    │
    ▼
ParsedMessageBuilder    costruisce ParsedMessage con confidence e diagnostics
    │
    ▼
CanonicalTranslator     produce CanonicalMessage (contratto finale)
```
  Il flusso è questo:

  1. MarkerMatcher trova i marker.
  2. MarkerEvidenceResolver li filtra.
  3. extract_intent_entities(...) produce gli intent.
  4. LocalDisambiguator.resolve(...) elimina CLOSE_FULL perché coesiste con SL_HIT.
  5. ParsedMessageBuilder riceve solo gli intent rimasti.
---

## Due file di configurazione per profilo

| File | Ruolo |
|------|-------|
| `semantic_markers.json` | Definisce i pattern testuali (marker) per intent, field, side, ecc. |
| `rules.json` | Definisce le regole su come usare quei marker: soppressione, disambiguazione, precedenza |

---

## `semantic_markers.json` — struttura sintetica

```json
{
  "intent_markers": {
    "MOVE_STOP_TO_BE": {
      "strong": ["стоп в бу", "стоп в безубыток", "move sl to be"],
      "weak":   ["бу", "be"]
    },
    "CLOSE_FULL": {
      "strong": ["close all", "закрываю все", "exit full"],
      "weak":   ["закрыть"]
    }
  },
  "field_markers": {
    "stop_loss":    { "strong": ["sl:", "стоп:"], "weak": [] },
    "take_profit":  { "strong": ["tp1:", "тейк 1:"], "weak": ["тейк"] }
  },
  "side_markers": {
    "LONG":  { "strong": ["long", "buy", "лонг"], "weak": [] },
    "SHORT": { "strong": ["short", "sell", "шорт"], "weak": [] }
  },
  "target_hint_markers": {
    "scope": {
      "ALL_LONG":      { "strong": ["all long", "все лонги"] },
      "ALL_POSITIONS": { "strong": ["all positions", "все позиции"] }
    }
  },
  "info_markers": {
    "ADMIN": { "strong": ["maintenance", "bot offline"] }
  }
}
```

---

## `rules.json` — struttura completa

`rules.json` ha quattro sezioni principali.

---

### 1. `marker_resolution` — soppressione marker

#### 1a. `suppress_weak_inside_strong_same_intent`

Se un marker **weak** è contenuto all'interno di un marker **strong** dello stesso intent, il weak viene soppresso.

```json
"marker_resolution": {
  "suppress_weak_inside_strong_same_intent": true
}
```

Esempio: `"стоп в бу"` è strong per `MOVE_STOP_TO_BE`.
Se il testo contiene `"стоп в бу"`, il weak `"бу"` standalone viene soppresso perché è già coperto.

---

#### 1b. `weak_context_exclusions` — soppressione weak per contesto

Sopprime marker weak quando il contesto circostante corrisponde a certe condizioni.

```json
"weak_context_exclusions": [
  {
    "intent": "TP_HIT",
    "marker": "tp1",
    "scope": "same_sentence",
    "if_contains_any": ["после", "взял до"],
    "unless_contains_any": ["тейк взят", "tp hit"]
  }
]
```

| Campo | Tipo | Significato |
|-------|------|-------------|
| `intent` | string | Intent a cui appartiene il marker |
| `marker` | string | Il marker weak da sopprimere |
| `scope` | enum | Dove cercare: `same_sentence`, `same_line`, `window`, `whole_message` |
| `if_contains_any` | list[str] | Sopprimi SE il contesto contiene almeno uno di questi |
| `if_regex_any` | list[str] | Sopprimi SE il contesto fa match con almeno una di queste regex |
| `unless_contains_any` | list[str] | NON sopprimere SE il contesto contiene almeno uno di questi |

**Esempio pratico:** il testo `"dopo tp1 ho chiuso"` contiene `"tp1"` (weak di `TP_HIT`) e `"dopo"` (`if_contains_any`) → il marker viene soppresso → nessun TP_HIT rilevato.

---

#### 1c. `marker_context_exclusions` — soppressione per contesto (weak **e** strong)

Meccanismo unificato che sopprime marker di qualsiasi forza (`weak` o `strong`) quando il contesto testuale corrisponde a certe condizioni. Complementa `weak_context_exclusions` (che rimane per backward compat) e lo estende ai marker forti.

```json
"marker_context_exclusions": [
  {
    "name": "all_short_in_ps_informational_context",
    "strength": "strong",
    "marker_name": "ALL_SHORT",
    "markers": {"source": "semantic"},
    "scope": "whole_message",
    "if_contains_any": ["p.s.", "у вас прибыль по шортам"],
    "reason": "scope_hint_in_postscript_not_actionable"
  }
]
```

| Campo | Tipo | Significato |
|-------|------|-------------|
| `name` | string | Nome identificativo della regola |
| `strength` | `"weak"` \| `"strong"` | Forza del marker da sopprimere |
| `marker_name` | `string` \| `list[string]` | Nome del marker (o lista di nomi) — match su `match.name` |
| `markers` | `list[str]` \| `{"source": "semantic"}` | Filtro sul testo del marker: lista esplicita oppure tutti i marker definiti in `semantic_markers.json` per quel nome+strength |
| `scope` | enum | Contesto di analisi: `same_sentence`, `same_line`, `window`, `whole_message` |
| `if_contains_any` | list[str] | Sopprimi SE il contesto contiene almeno uno di questi |
| `if_regex_any` | list[str] | Sopprimi SE il contesto fa match con almeno una di queste regex |
| `unless_contains_any` | list[str] | NON sopprimere SE il contesto contiene almeno uno di questi |
| `reason` | string | Label diagnostica (appare nei diagnostics del messaggio) |

**`marker_name` come lista:** una regola può coprire più marker con la stessa logica di soppressione:
```json
{
  "name": "be_false_positive_context",
  "strength": "weak",
  "marker_name": ["EXIT_BE", "MOVE_STOP_TO_BE"],
  "markers": {"source": "semantic"},
  "scope": "same_sentence",
  "if_contains_any": ["фактически в бу закрылись"]
}
```

**`{"source": "semantic"}`:** pesca la lista dei testi da `semantic_markers.json` per quel `marker_name` e `strength`. Equivalente al vecchio `{"source": "intent_weak"}` di `weak_context_exclusions`, ma generico su kind e strength.

**Nota scope:** usare `"whole_message"` quando la condizione può trovarsi in una parte del testo separata da punteggiatura (es. "p.s." contiene un punto che rompe `same_sentence`).

---

#### 1d. `cross_intent_suppression` — soppressione cross-intent

Quando un intent **forte** è presente, sopprime intent più deboli che sarebbero ridondanti o in conflitto.

```json
"cross_intent_suppression": [
  {
    "if_strong_intent": "MOVE_STOP_TO_BE",
    "suppress_weak_intent": "EXIT_BE",
    "condition": "any"
  },
  {
    "if_strong_intent": "SL_HIT",
    "suppress_weak_intent": "MOVE_STOP_TO_BE",
    "condition": "any"
  },
  {
    "if_strong_intent": "SL_HIT",
    "suppress_weak_intent": "CLOSE_FULL",
    "condition": "any"
  }
]
```

| Campo | Significato |
|-------|-------------|
| `if_strong_intent` | Intent che deve essere presente con evidenza forte |
| `suppress_weak_intent` | Intent da sopprimere se ha solo evidenza weak |
| `condition` | `"any"` = sempre quando il forte è presente |

**Logica:** `MOVE_STOP_TO_BE` implica già breakeven → `EXIT_BE` weak sarebbe rumore, viene soppresso.

---

### 2. `disambiguation` — disambiguazione intenti

Risolve conflitti tra intenti che coesistono nello stesso messaggio.

```json
"disambiguation": [
  {
    "rule": "prefer_move_stop_to_be_over_move_stop",
    "prefer": "MOVE_STOP_TO_BE",
    "over":   "MOVE_STOP",
    "scope":  "whole_message"
  },
  {
    "rule": "close_full_redundant_with_sl_hit",
    "prefer": "SL_HIT",
    "over":   "CLOSE_FULL",
    "scope":  "same_span"
  },
  {
    "rule": "context_market_marker",
    "type": "market_entry_context",
    "if_marker": "вход по рынку",
    "requires_signal": true,
    "effect": "treat_as_market_entry_type"
  }
]
```

| Campo | Significato |
|-------|-------------|
| `rule` | Nome identificativo (solo per leggibilità) |
| `prefer` | Intent da mantenere |
| `over` | Intent da rimuovere |
| `scope` | `whole_message`, `same_span`, `same_line` |
| `type` | Per regole speciali: `"market_entry_context"` |
| `requires_signal` | Applica solo se il messaggio ha un segnale estratto |
| `effect` | Azione speciale invece di soppressione |

**Scope `same_span`:** la regola si applica solo se i due intenti sono individuati sullo stesso span testuale (stesse posizioni).

---

### 3. `primary_intent_precedence` — ordine di priorità

Lista ordinata (alta → bassa priorità) usata dal `ParsedMessageBuilder` per scegliere l'intent primario del messaggio.

```json
"primary_intent_precedence": [
  "EXIT_BE",
  "SL_HIT",
  "TP_HIT",
  "CLOSE_FULL",
  "CLOSE_PARTIAL",
  "MOVE_STOP_TO_BE",
  "MOVE_STOP",
  "CANCEL_PENDING",
  "REENTER",
  "ADD_ENTRY",
  "MODIFY_ENTRY",
  "MODIFY_TARGETS",
  "ENTRY_FILLED",
  "REPORT_RESULT",
  "INVALIDATE_SETUP",
  "INFO_ONLY"
]
```

Il primo intent della lista che è presente nel messaggio diventa il `primary_intent`.

---

### 4. `extraction_markers` — marker per estrazione entità

Usati da `IntentEntityExtractor` per trovare valori numerici associati a un intent.

```json
"extraction_markers": {
  "risk": {
    "prefix": ["rischio", "risk", "риск", "%"],
    "suffix": ["%", "percent"]
  }
}
```

Questi non sono marker di rilevamento intent — sono pattern che guidano l'estrazione di valori (es. percentuale di rischio) nel contesto dell'intent rilevato.

---

## Aggiungere un nuovo intent — checklist

1. **`semantic_markers.json`** — aggiungere entry in `intent_markers` con `strong` e `weak`
2. **`rules.json` → `marker_resolution.cross_intent_suppression`** — se il nuovo intent è incompatibile con altri, aggiungere regola di soppressione
3. **`rules.json` → `disambiguation`** — se può coesistere con altri intent, definire la precedenza
4. **`rules.json` → `primary_intent_precedence`** — inserire nella posizione corretta
5. **`contracts/enums.py`** — aggiungere valore a `IntentType` e `IntentCategory`
6. **`contracts/entities.py`** — aggiungere classe `XxxEntities(IntentEntities)`
7. **`profiles/trader_X/intent_entity_extractor.py`** — aggiungere builder per le entità

---

## Esempio completo: nuovo intent `PARTIAL_CLOSE_ALL`

### semantic_markers.json
```json
"PARTIAL_CLOSE_ALL": {
  "strong": ["close half all", "chiudi metà tutto", "закрыть половину всего"],
  "weak":   ["half all", "metà tutto"]
}
```

### rules.json — cross_intent_suppression
```json
{
  "if_strong_intent": "PARTIAL_CLOSE_ALL",
  "suppress_weak_intent": "CLOSE_PARTIAL",
  "condition": "any"
}
```

### rules.json — disambiguation
```json
{
  "rule": "partial_close_all_over_close_partial",
  "prefer": "PARTIAL_CLOSE_ALL",
  "over":   "CLOSE_PARTIAL",
  "scope":  "same_span"
}
```

### rules.json — primary_intent_precedence
```json
"primary_intent_precedence": [
  "EXIT_BE",
  "SL_HIT",
  "CLOSE_FULL",
  "PARTIAL_CLOSE_ALL",   ← inserito dopo CLOSE_FULL
  "CLOSE_PARTIAL",
  ...
]
```

---

## Note operative

- I marker sono cercati nel testo **normalizzato** (lowercase, unicode normalizzato).
- `strong` contribuisce con peso `1.0`, `weak` con peso `0.4`.
- Un intent è rilevato se ha almeno un marker (strong o weak) presente.
- La confidence finale è combinazione di: peso marker + completezza segnale (se SIGNAL).
- Modificare `semantic_markers.json` non richiede toccare il codice Python — solo i JSON.
- `rules.json` può essere testato con i test di integrazione in `tests/`.
- **`scope_hint` e telegram link:** `TargetHintsExtractor` scansiona il testo completo autonomamente (pipeline separata dal `MarkerEvidenceResolver`). Se il messaggio contiene `telegram_message_ids` (link forti a signal specifici), qualsiasi `scope_hint` estratto dal testo (es. "по шортам" in un p.s.) viene azzerato a `UNKNOWN` — i link puntano già a target precisi e lo scope testuale sarebbe fuorviante.

# Markers e rules

## Separazione netta

Usare due file distinti:

```text
semantic_markers.json = vocabolario
rules.json            = logica
```

Non mescolare.

---

# `semantic_markers.json`

Contiene solo marker. Vedi [06_1_SEMANTIC_MARKERS_COMPLETO.md](06_1_SEMANTIC_MARKERS_COMPLETO.md) per il file completo Trader A.

Esempio minimo:

```json
{
  "language": "ru",
  "intent_markers": {
    "MOVE_STOP_TO_BE": {
      "strong": ["стоп в бу", "стоп в безубыток"],
      "weak": ["бу", "безубыток"]
    },
    "EXIT_BE": {
      "strong": ["закрылась в бу", "ушел в бу"],
      "weak": ["бу", "безубыток"]
    }
  }
}
```

---

## Marker strong

Uno strong marker deve essere quasi sufficiente da solo.

Esempio:

```text
стоп в бу
```

è strong per:

```text
MOVE_STOP_TO_BE
```

---

## Marker weak

Un weak marker è ambiguo.

Esempio:

```text
бу
```

Può significare:

```text
MOVE_STOP_TO_BE
EXIT_BE
INFO_ONLY
```

Quindi non deve decidere da solo se c'è un marker più specifico nello stesso span.

---

# `rules.json`

Contiene logica.

```json
{
  "marker_resolution": {},
  "disambiguation": [],
  "primary_intent_precedence": []
}
```

---

# Marker resolution

## Regola 1: weak dentro strong stesso intent

```json
{
  "marker_resolution": {
    "suppress_weak_inside_strong_same_intent": true
  }
}
```

Esempio:

```text
стоп в бу
```

Match:

```text
MOVE_STOP_TO_BE strong "стоп в бу"
MOVE_STOP_TO_BE weak   "бу"
```

Output:

```text
MOVE_STOP_TO_BE strong
```

---

## Regola 2: cross-intent suppression

```json
{
  "marker_resolution": {
    "cross_intent_suppression": [
      {
        "if_strong": "MOVE_STOP_TO_BE",
        "suppress_weak": ["EXIT_BE", "INFO_ONLY"],
        "reason": "command_marker_dominates_be_status_marker"
      }
    ]
  }
}
```

Esempio:

```text
стоп в бу
```

Sopprime:

```text
EXIT_BE weak "бу"
```

Perché `стоп в бу` è comando, non report.

---

# Disambiguation locale

Serve dopo marker resolution.

Esempio:

```json
{
  "disambiguation": [
    {
      "name": "prefer_move_stop_to_be_over_move_stop",
      "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
      "prefer": "MOVE_STOP_TO_BE",
      "over": ["MOVE_STOP"]
    },
    {
      "name": "prefer_exit_be_over_close_full",
      "when_all_detected": ["EXIT_BE", "CLOSE_FULL"],
      "if_contains_any": ["ушел в бу", "закрылся в бу", "закрылась в безубыток"],
      "prefer": "EXIT_BE",
      "over": ["CLOSE_FULL"]
    },
    {
      "name": "suppress_close_full_if_partial",
      "when_all_detected": ["CLOSE_FULL", "CLOSE_PARTIAL"],
      "prefer": "CLOSE_PARTIAL",
      "over": ["CLOSE_FULL"]
    }
  ]
}
```

---

## Regola contestuale: MARKET (signal) vs MODIFY_ENTRY/MARKET_NOW (update)

I marker `"вход по рынку"`, `"с текущих"`, `"по рынку"` compaiono sia in `entry_type_markers.MARKET` sia in `modify_entry_mode_markers.MARKET_NOW`. La discriminazione è **contestuale**, non lessicale:

```json
{
  "disambiguation": [
    {
      "name": "context_market_marker",
      "when_marker_in": ["вход по рынку", "с текущих", "по рынку", "по текущим"],
      "if_signal_payload_present": {
        "interpret_as": "ENTRY_TYPE_MARKET",
        "intent": null
      },
      "if_signal_payload_absent": {
        "interpret_as": "MODIFY_ENTRY_MARKET_NOW",
        "intent": "MODIFY_ENTRY",
        "mode": "MARKET_NOW"
      }
    }
  ]
}
```

Algoritmo applicato dal `LocalDisambiguator`:

```text
1. Esegui SignalExtractor.
2. Se signal_payload != None ed è almeno PARTIAL (ha symbol e side):
     i marker MARKET sono interpretati come entry_type del leg di ingresso.
     Non emettere intent MODIFY_ENTRY.
3. Altrimenti:
     emetti ParsedIntent(type=MODIFY_ENTRY, mode=MARKET_NOW).
```

> Vedi anche [09_MODIFY_ENTRY_MODE_MARKERS.md](09_MODIFY_ENTRY_MODE_MARKERS.md) per le altre regole di precedenza che coinvolgono `MODIFY_ENTRY` (vs `ADD_ENTRY`, `CANCEL_PENDING`, `REENTER`).

---

# Primary intent precedence

```json
{
  "primary_intent_precedence": [
    "SL_HIT",
    "EXIT_BE",
    "TP_HIT",
    "REPORT_RESULT",
    "CLOSE_FULL",
    "CLOSE_PARTIAL",
    "CANCEL_PENDING",
    "INVALIDATE_SETUP",
    "MOVE_STOP_TO_BE",
    "MOVE_STOP",
    "MODIFY_TARGETS",
    "MODIFY_ENTRY",
    "ADD_ENTRY",
    "REENTER",
    "ENTRY_FILLED",
    "INFO_ONLY"
  ]
}
```

Logica:

```text
primary_class domina per categoria
primary_intent domina per rischio semantico
```

Esempio:

```text
SL_HIT + CLOSE_FULL
```

è più pericoloso interpretarlo come comando di chiusura se in realtà è report di stop. Quindi `SL_HIT` deve dominare.

---

# Cosa non mettere in rules.json

Non mettere:

```text
- query DB
- lifecycle state
- target exists
- position open/closed
- order exists
```

Queste sono regole post-parser.

---

# Schema minimo realistico

```json
{
  "marker_resolution": {
    "suppress_weak_inside_strong_same_intent": true,
    "cross_intent_suppression": [
      {
        "if_strong": "MOVE_STOP_TO_BE",
        "suppress_weak": ["EXIT_BE", "INFO_ONLY"]
      },
      {
        "if_strong": "SL_HIT",
        "suppress_weak": ["MOVE_STOP", "MOVE_STOP_TO_BE"]
      },
      {
        "if_strong": "CLOSE_PARTIAL",
        "suppress_weak": ["CLOSE_FULL"]
      }
    ]
  },
  "disambiguation": [
    {
      "name": "prefer_move_stop_to_be_over_move_stop",
      "when_all_detected": ["MOVE_STOP_TO_BE", "MOVE_STOP"],
      "prefer": "MOVE_STOP_TO_BE",
      "over": ["MOVE_STOP"]
    },
    {
      "name": "context_market_marker",
      "when_marker_in": ["вход по рынку", "с текущих", "по рынку", "по текущим"],
      "if_signal_payload_present": {"interpret_as": "ENTRY_TYPE_MARKET"},
      "if_signal_payload_absent": {"interpret_as": "MODIFY_ENTRY_MARKET_NOW"}
    }
  ],
  "primary_intent_precedence": [
    "SL_HIT",
    "EXIT_BE",
    "TP_HIT",
    "REPORT_RESULT",
    "CLOSE_FULL",
    "CLOSE_PARTIAL",
    "CANCEL_PENDING",
    "MOVE_STOP_TO_BE",
    "MOVE_STOP"
  ]
}
```

---

# Regola pratica

```text
semantic_markers.json dice: quali parole possono significare cosa
rules.json dice: cosa fare quando più cose sembrano vere
```

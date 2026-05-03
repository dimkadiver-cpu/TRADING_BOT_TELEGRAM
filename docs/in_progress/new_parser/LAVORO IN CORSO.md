  Stato catena parser trader_a vs spec

  ┌──────────────────────────────────────┬─────────────────────────────────────┬──────────────────┐
  │              Fase spec               │            Implementato             │  Integrato nel   │
  │                                      │                                     │      flusso      │
  ├──────────────────────────────────────┼─────────────────────────────────────┼──────────────────┤
  │ 1. Classificazione                   │ runtime.py                          │ ✅               │
  ├──────────────────────────────────────┼─────────────────────────────────────┼──────────────────┤
  │ 2. Detection intents da              │ runtime.py + extractors.py          │ ✅               │
  │ semantic_markers.json                │                                     │                  │
  ├──────────────────────────────────────┼─────────────────────────────────────┼──────────────────┤
  │ 3. Estrazione entità (extractors.py) │ extractors.py                       │ ✅               │
  ├──────────────────────────────────────┼─────────────────────────────────────┼──────────────────┤
  │ 4. ParsedMessage (PENDING)           │ runtime.py                          │ ✅               │
  ├──────────────────────────────────────┼─────────────────────────────────────┼──────────────────┤
  │ 5. intent_validator →                │ intent_validator/validator.py       │ ❌ non chiamato  │
  │ CONFIRMED/INVALID                    │ esiste                              │                  │
  ├──────────────────────────────────────┼─────────────────────────────────────┼──────────────────┤
  │ 6. ParsedMessage (VALIDATED)         │ —                                   │ ❌ mai avviene   │
  ├──────────────────────────────────────┼─────────────────────────────────────┼──────────────────┤
  │ 7. disambiguation_engine (solo su    │ shared/disambiguation_engine.py     │ ❌ non chiamato  │
  │ CONFIRMED)                           │ esiste                              │                  │
  ├──────────────────────────────────────┼─────────────────────────────────────┼──────────────────┤
  │ 8. ParsedMessage finale              │ runtime.py                          │ ✅ (ma           │
  │                                      │                                     │ incompleto)      │
  └──────────────────────────────────────┴─────────────────────────────────────┴──────────────────┘

  ---
  Problema aggiuntivo — due percorsi paralleli:

  Il replay del messaggio 218 ha usato parse_canonical() (percorso legacy, ~3000 righe), non parse()
  (percorso nuovo, 9 righe). Il percorso legacy usa self._rules → parsing_rules.json, che manca dei 3
  marker CLOSE_FULL. Ecco perché "давайте все закроем" non viene rilevato.

  Il percorso nuovo (parse() → runtime.py → extractors.py) usa correttamente semantic_markers.json +
  rules.json e li vedrebbe — ma non viene chiamato dal replay.

  ---
  Riassunto gap:

  1. Steps 5-7 non integrati — intent_validator e disambiguation_engine esistono ma non sono collegati
   a runtime.py
  2. Percorso legacy ancora attivo — il replay usa parse_canonical() invece di parse(), quindi bypassa
   tutta la nuova architettura
  3. parsing_rules.json ancora presente come file legacy — genera confusione e divergenza marker

  Il problema del messaggio 218 è conseguenza diretta del punto 2. Vuoi che lavoriamo
  sull'integrazione dei step 5-7 in runtime.py, o prima colleghiamo il replay al percorso nuovo?
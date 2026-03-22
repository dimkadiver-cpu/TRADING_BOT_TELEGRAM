---
name: rules-engine
description: Usa questa skill quando devi aggiungere o modificare la logica di classificazione del RulesEngine, aggiornare parsing_rules.json di un profilo, aggiungere combination_rules, modificare i pesi strong/weak, o debuggare una classificazione errata o confidence bassa.
---

# Obiettivo

Il `RulesEngine` è il layer dichiarativo di classificazione: legge `parsing_rules.json`, fa merge con il vocabolario condiviso, e produce un `ClassificationResult` (tipo messaggio + confidence + matched_markers). Non estrae entità — quella responsabilità appartiene al `profile.py`.

# Quando usarla

- aggiunta di nuovi marcatori a `parsing_rules.json`
- debug di messaggi classificati male (UNCLASSIFIED inatteso, tipo sbagliato)
- confidence troppo bassa o troppo alta su un trader
- aggiunta di `combination_rules` per casi ambigui
- modifica ai pesi `strong` / `weak`
- aggiunta di un nuovo vocabolario condiviso in `trader_profiles/shared/`

# File coinvolti

```
src/parser/rules_engine.py                           ← motore (non toccare salvo bug)
src/parser/trader_profiles/shared/
├── russian_trading.json                             ← vocabolario russo condiviso
└── english_trading.json                             ← vocabolario inglese condiviso
src/parser/trader_profiles/<trader>/parsing_rules.json  ← profilo specifico
```

# API RulesEngine

```python
from src.parser.rules_engine import RulesEngine

engine = RulesEngine.load("src/parser/trader_profiles/trader_3/parsing_rules.json")

result = engine.classify(text)
# → ClassificationResult(
#       message_type="NEW_SIGNAL",
#       confidence=1.4,           # non capped a 1.0 prima del merge in TraderParseResult
#       matched_markers=["new_signal/лонг", "new_signal/sl:"],
#       intents_hint=["U_MOVE_STOP"]
#   )

intents = engine.detect_intents(text)
# → ["U_CLOSE_FULL"]

engine.is_blacklisted(text)
# → False

engine.number_format
# → {"decimal_separator": ".", "thousands_separator": " "}
```

Per i test, creare l'engine direttamente da dict senza file JSON:
```python
engine = RulesEngine.from_dict({
    "classification_markers": {
        "new_signal": {"strong": ["лонг"], "weak": ["сетап"]}
    }
})
```

# Struttura parsing_rules.json

```json
{
  "language": "ru",
  "shared_vocabulary": "russian_trading",
  "number_format": {
    "decimal_separator": ".",
    "thousands_separator": " "
  },
  "classification_markers": {
    "new_signal": {
      "strong": ["лонг", "long", "sl:", "tp1:"],
      "weak":   ["сигнал", "сетап"]
    },
    "update": {
      "strong": ["стоп в бу", "close all", "закрываю все"],
      "weak":   ["по этим", "тут не актуально"]
    },
    "info_only": {
      "strong": ["обзор", "VIP MARKET UPDATE"],
      "weak":   ["анализ", "мнение"]
    }
  },
  "combination_rules": [
    {
      "if": ["weak_sl_ref", "strong_be_marker"],
      "then": "update",
      "confidence_boost": 0.3
    }
  ],
  "intent_markers": {
    "U_MOVE_STOP":           ["стоп в бу", "move stop to be"],
    "U_CLOSE_FULL":          ["закрываю все", "close all"],
    "U_CLOSE_PARTIAL":       ["частичная фиксация", "partial close"],
    "U_CANCEL_PENDING":      ["убираем лимитку", "cancel pending"],
    "U_REENTER":             ["повторный вход", "reenter"],
    "U_ADD_ENTRY":           ["добавляю вход", "add entry"],
    "U_MODIFY_ENTRY":        ["меняю вход", "modify entry"],
    "U_UPDATE_TAKE_PROFITS": ["меняю тейки", "update tp"]
  },
  "target_ref_markers": {
    "strong": {
      "telegram_link": "t\\.me/",
      "explicit_id": ["SIGNAL ID:\\s*#?\\d+", "#\\d{3,}"]
    },
    "weak": {
      "pronouns": ["по этим", "по этому", "тут"]
    }
  },
  "blacklist": ["#admin", "#stats", "weekly recap"],
  "fallback_hook": {
    "enabled": false,
    "provider": null,
    "model": null
  }
}
```

# Algoritmo di classificazione

1. Normalizza il testo (lowercase + strip)
2. Per ogni categoria (`new_signal`, `update`, `info_only`):
   - conta `strong` × 1.0 + `weak` × 0.4
3. Applica `combination_rules`: se tutti i marker `if` corrispondono, aggiunge `confidence_boost` alla categoria `then`
4. La categoria con score più alto vince
5. Se nessun marker corrisponde → `UNCLASSIFIED`, confidence=0.0
6. `intents_hint` viene calcolato sempre, indipendentemente dalla classificazione

# Merge vocabolario condiviso

Se `shared_vocabulary` è dichiarato nel profilo, il motore fa il merge automatico:
- il profilo ha **sempre precedenza** in caso di chiave duplicata
- `classification_markers`: unione per categoria e per strong/weak
- `intent_markers`, `target_ref_markers`: unione per chiave
- `blacklist`: unione con deduplicazione

# Regole

- modificare i marcatori in `parsing_rules.json`, non in `rules_engine.py`
- `rules_engine.py` non va toccato salvo bug nel motore (logica di merge, pesi)
- marcatori devono essere in minuscolo (normalizzazione automatica al load)
- un marcatore `strong` vale 1.0, un `weak` vale 0.4 — calibrare di conseguenza
- `combination_rules` servono per casi dove due weak insieme equivalgono a uno strong
- `intents_hint` nel ClassificationResult è un suggerimento — il `profile.py` è responsabile della classificazione finale degli intents
- non aggiungere logica specifica trader in `rules_engine.py`

# Debug classificazione errata

Quando un messaggio viene classificato male:
1. Stampare `result.matched_markers` per vedere quali marcatori hanno matchato
2. Verificare che i marcatori siano in minuscolo nel JSON
3. Verificare che il testo del messaggio contenga effettivamente il marcatore
4. Aggiungere marcatore nella categoria corretta con peso appropriato
5. Rilanciare il replay: `python parser_test/scripts/replay_parser.py --trader <trader>`

# Output richiesto

Quando usi questa skill, restituisci:
- marcatori aggiunti/modificati con categoria e peso
- combination_rules aggiunte se presenti
- confidence score prima/dopo per i casi test
- messaggi che migliorano e messaggi che potrebbero regredire

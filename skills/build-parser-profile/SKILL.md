---
name: build-parser-profile
description: Usa questa skill quando devi costruire o aggiornare un parser per un trader specifico seguendo la nuova architettura RulesEngine + profile.py + Pydantic.
---

# Obiettivo

Produrre parser trader-specific robusti che producono TraderParseResult canonico senza toccare il layer generico legacy.

# Quando usarla

- nuovo profilo trader
- aggiornamento vocabolario di un trader
- fix di classificazione messaggi
- aggiunta di nuovi intents o entità
- miglioramento linking target_ref

# Architettura profilo

```
parsing_rules.json    → vocabolario dichiarativo (classificatori, intents, target ref)
profile.py            → logica procedurale (estrazione entità, edge cases)
```

Il `RulesEngine` legge `parsing_rules.json` e produce la classificazione. Il `profile.py` usa quella classificazione per estrarre entità e intents specifici del trader.

# Struttura parsing_rules.json

```json
{
  "language": "ru | en | it",
  "shared_vocabulary": "russian_trading | english_trading | null",
  "number_format": {
    "decimal_separator": ".",
    "thousands_separator": " "
  },
  "classification_markers": {
    "new_signal": {
      "strong": ["лонг", "long", "entry", "вход", "sl:", "tp1:"],
      "weak": ["сигнал", "сетап"]
    },
    "update": {
      "strong": ["стоп в бу", "close all", "закрываю все"],
      "weak": ["по этим", "тут не актуально"]
    },
    "info_only": {
      "strong": ["обзор", "VIP MARKET UPDATE", "MARKET ANALYSIS"],
      "weak": ["анализ", "мнение"]
    }
  },
  "combination_rules": [
    {
      "if": ["weak_sl_ref", "strong_be_marker"],
      "then": "U_MOVE_STOP",
      "confidence_boost": 0.3
    },
    {
      "if": ["weak_ref_1", "weak_ref_2"],
      "then": "target_kind:STRONG",
      "note": "due weak insieme valgono strong"
    }
  ],
  "intent_markers": {
    "U_MOVE_STOP": ["стоп в бу", "стоп на точку входа", "move stop to be", "stop to breakeven"],
    "U_CLOSE_FULL": ["закрываю все", "close all", "фиксация 100%"],
    "U_CLOSE_PARTIAL": ["частичная фиксация", "partial close"],
    "U_CANCEL_PENDING": ["убираем лимитку", "убрать лимитные ордера", "cancel pending"],
    "U_REENTER": ["повторный вход", "reenter"],
    "U_ADD_ENTRY": ["добавляю вход", "add entry", "усреднение добавляю"],
    "U_MODIFY_ENTRY": ["меняю вход", "modify entry"],
    "U_UPDATE_TAKE_PROFITS": ["меняю тейки", "update tp", "новые тейки"]
  },
  "target_ref_markers": {
    "strong": {
      "telegram_link": "t\\.me/",
      "explicit_id": ["SIGNAL ID:\\s*#?\\d+", "#\\d{3,}"]
    },
    "weak": {
      "pronouns": ["по этим", "по этому", "тут", "этот сетап"]
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

# Struttura profile.py

```python
from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.rules_engine import RulesEngine
from src.parser.models.canonical import Intent, TargetRef
from src.parser.models.new_signal import NewSignalEntities
from src.parser.models.update import UpdateEntities

class TraderXProfileParser:
    def __init__(self):
        self.rules = RulesEngine.load("trader_x/parsing_rules.json")

    def parse_message(self, text: str, context: ParserContext) -> TraderParseResult:
        # 1. classificazione da RulesEngine
        classification = self.rules.classify(text, context)

        # 2. estrazione entità specifica trader
        if classification.message_type == "NEW_SIGNAL":
            entities = self._extract_new_signal(text, context)
        elif classification.message_type == "UPDATE":
            entities = self._extract_update(text, context)
        else:
            entities = None

        # 3. intents (solo per UPDATE)
        intents = []
        if classification.message_type == "UPDATE":
            intents = self._extract_intents(text, classification)

        # 4. target_ref
        target_ref = self._resolve_target_ref(text, context, classification)

        return TraderParseResult(
            message_type=classification.message_type,
            confidence=classification.confidence,
            entities=entities,
            intents=intents,
            target_ref=target_ref,
        )
```

# Profilo di riferimento

**Trader 3** è il primo profilo migrato e il riferimento per tutti gli altri. Prima di costruire un nuovo profilo, leggi `trader_profiles/trader_3/` come esempio completo.

# Ordine di migrazione profili

```
1. trader_3   ← già fatto, è il riferimento
2. trader_b   ← russo semplice, formato lineare
3. trader_c   ← 
4. trader_d   ←
5. trader_a   ← ultimo, più complesso (russo + multi-fase)
```

# Entità da estrarre — NEW_SIGNAL

```
symbol          str — normalizzato uppercase, es. BTCUSDT
direction       LONG | SHORT
entry_type      MARKET | LIMIT | AVERAGING | ZONE
entries         lista Price (opzionale per MARKET)
stop_loss       Price — sempre obbligatorio
take_profits    lista Price — almeno uno
leverage        float | None
risk_pct        float | None
conditions      str | None — testo libero non parsato
```

Completeness:
- `COMPLETE` se tutti i campi obbligatori presenti
- `INCOMPLETE` se manca stop_loss, take_profits, o entries (per LIMIT/AVERAGING/ZONE)

# Entità da estrarre — UPDATE per intent

```
U_MOVE_STOP           → new_sl_level: Price | None
U_CLOSE_FULL          → nessuna
U_CLOSE_PARTIAL       → close_pct: float
U_CANCEL_PENDING      → nessuna
U_REENTER             → entries: lista Price, entry_type
U_ADD_ENTRY           → new_entry_price: Price, entry_type
U_MODIFY_ENTRY        → old_entry_price: Price, new_entry_price: Price | None
U_UPDATE_TAKE_PROFITS → old_take_profits: lista Price | None, new_take_profits: lista Price
```

# Price — normalizzazione

Il modello `Price` Pydantic normalizza automaticamente il valore grezzo.
Fornisci sempre il valore `raw` (stringa originale) e lascia che Pydantic calcoli `value`.

Il `number_format` in `parsing_rules.json` determina come interpretare separatori:
```json
{ "decimal_separator": ".", "thousands_separator": " " }
```
Esempio: `"90 000.5"` → `Price(raw="90 000.5", value=90000.5)`

# Regole

- non inventare valori non presenti nel testo
- se un campo non è affidabile, non estrarlo (meglio INCOMPLETE che sbagliato)
- separa detection, extraction, normalization — tre responsabilità distinte
- non mescolare logica trader-specific con utilities condivise
- confidence bassa se linking debole o classificazione ambigua
- il profilo NON decide se eseguire — solo estrae e classifica

# Test minimi richiesti per ogni profilo

- golden case NEW_SIGNAL completo
- golden case NEW_SIGNAL incompleto (senza SL o TP)
- golden case UPDATE con STRONG ref
- golden case UPDATE con SYMBOL ref
- golden case INFO_ONLY
- caso UNCLASSIFIED
- almeno un caso ambiguo con confidence < 0.7

# Output richiesto

Quando usi questa skill, restituisci sempre:
- file toccati
- marcatori chiave aggiunti/modificati in parsing_rules.json
- edge case identificati
- test aggiunti
- rischi residui

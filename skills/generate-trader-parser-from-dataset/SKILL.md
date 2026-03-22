\---

name: generate-trader-parser-from-dataset
description: Usa questa skill quando devi costruire o aggiornare un parser trader-specific partendo da esempi reali (CSV o testo grezzo dal DB). Produce parsing\_rules.json, skeleton profile.py e test minimi.
---

# Obiettivo

Generare in modo ripetibile un parser trader-specifico partendo da messaggi reali, seguendo la nuova architettura RulesEngine + profile.py + Pydantic.

# Quando usarla

* hai un nuovo trader e vuoi estrarre pattern dai messaggi reali nel DB
* devi migliorare un parser esistente con nuovi pattern trovati nei CSV di debug
* vuoi convertire osservazioni manuali in regole dichiarative

# Input accettati

```
1. CSV di debug export (da parser\_test/reports/)
   colonne minime: raw\_text, message\_type, warnings

2. CSV con colonne: text, comment
   comment = annotazione manuale sul tipo di messaggio

3. Testo grezzo dal DB (tramite replay\_parser.py --trader X)
```

# Workflow

## Step 1 — analizza i messaggi

Leggi il CSV dello scope più problematico (tipicamente `trader\_X\_unclassified.csv` o `trader\_X\_update.csv`).

Per ogni messaggio identifica:

* lingua e caratteristiche stilistiche del trader
* marcatori forti di classificazione (parole/frasi che identificano univocamente il tipo)
* marcatori deboli (suggeriscono un tipo ma non in modo certo)
* pattern di linking (reply, link Telegram, ID esplicito, pronomi)
* formato prezzi (separatori decimali e migliaia)

## Step 2 — costruisci parsing\_rules.json

Parti da questo skeleton e riempilo:

```json
{
  "language": "ru | en | it",
  "shared\_vocabulary": "russian\_trading | english\_trading | null",
  "number\_format": {
    "decimal\_separator": ".",
    "thousands\_separator": " "
  },
  "classification\_markers": {
    "new\_signal": { "strong": \[], "weak": \[] },
    "update": { "strong": \[], "weak": \[] },
    "info\_only": { "strong": \[], "weak": \[] }
  },
  "combination\_rules": \[],
  "intent\_markers": {
    "U\_MOVE\_STOP": \[],
    "U\_CLOSE\_FULL": \[],
    "U\_CLOSE\_PARTIAL": \[],
    "U\_CANCEL\_PENDING": \[],
    "U\_REENTER": \[],
    "U\_ADD\_ENTRY": \[],
    "U\_MODIFY\_ENTRY": \[],
    "U\_UPDATE\_TAKE\_PROFITS": \[]
  },
  "target\_ref\_markers": {
    "strong": {
      "telegram\_link": "t\\\\.me/",
      "explicit\_id": \[]
    },
    "weak": {
      "pronouns": \[]
    }
  },
  "blacklist": \[],
  "fallback\_hook": {
    "enabled": false,
    "provider": null,
    "model": null
  }
}
```

Regole per riempire i marcatori:

* marcatore `strong` → identifica il tipo con alta certezza (es. "SIGNAL ID:" per trader\_3)
* marcatore `weak` → suggerisce il tipo ma può essere ambiguo
* usa lowercase per i marcatori (il testo viene normalizzato prima del confronto)
* preferisci frasi brevi e specifiche a parole singole ambigue

## Step 3 — skeleton profile.py

```python
"""Trader X profile parser."""
from \_\_future\_\_ import annotations

import re
from pathlib import Path

from src.parser.trader\_profiles.base import ParserContext, TraderParseResult
from src.parser.rules\_engine import RulesEngine
from src.parser.models.canonical import Intent, TargetRef
from src.parser.models.new\_signal import NewSignalEntities, EntryLevel, StopLoss, TakeProfit
from src.parser.models.update import UpdateEntities
from src.parser.models.canonical import Price

\_RULES\_PATH = Path(\_\_file\_\_).resolve().parent / "parsing\_rules.json"

# regex specifici del trader — aggiungere qui
\_SYMBOL\_RE = re.compile(r"\\b\[A-Z0-9]{2,20}(?:USDT|USDC|USD|BTC|ETH)(?:\\.P)?\\b")
\_ENTRY\_RE = re.compile(r"ENTRY:\\s\*(?P<value>\[\\d\\s,.-]+)", re.IGNORECASE)
\_SL\_RE = re.compile(r"STOP\\s\*LOSS:\\s\*(?P<value>\[\\d\\s,.-]+)", re.IGNORECASE)
\_TP\_RE = re.compile(r"TARGETS:\\s\*(?P<values>\[\\d\\s,.-]+)", re.IGNORECASE)


class TraderXProfileParser:
    def \_\_init\_\_(self):
        self.rules = RulesEngine.load(str(\_RULES\_PATH))

    def parse\_message(self, text: str, context: ParserContext) -> TraderParseResult:
        classification = self.rules.classify(text, context)

        if classification.message\_type == "NEW\_SIGNAL":
            entities, completeness, missing = self.\_parse\_new\_signal(text, context)
        elif classification.message\_type == "UPDATE":
            entities = self.\_parse\_update(text, context)
            completeness = None
            missing = \[]
        else:
            entities = None
            completeness = None
            missing = \[]

        intents = \[]
        if classification.message\_type == "UPDATE":
            intents = self.\_extract\_intents(text, classification)

        target\_ref = self.\_resolve\_target\_ref(text, context, classification)

        return TraderParseResult(
            message\_type=classification.message\_type,
            completeness=completeness,
            missing\_fields=missing,
            entities=entities,
            intents=intents,
            target\_ref=target\_ref,
            confidence=classification.confidence,
            trader\_id=context.trader\_code,
            raw\_text=text,
        )

    def \_parse\_new\_signal(self, text, context):
        # TODO: implementa estrazione entità NEW\_SIGNAL
        raise NotImplementedError

    def \_parse\_update(self, text, context):
        # TODO: implementa estrazione entità UPDATE
        raise NotImplementedError

    def \_extract\_intents(self, text, classification):
        # TODO: mappa marcatori intent a oggetti Intent
        raise NotImplementedError

    def \_resolve\_target\_ref(self, text, context, classification):
        # reply → STRONG/REPLY
        if context.reply\_to\_message\_id:
            return TargetRef(
                kind="STRONG",
                method="REPLY",
                ref=context.reply\_to\_message\_id,
            )
        # TODO: aggiungi TELEGRAM\_LINK, EXPLICIT\_ID, SYMBOL, GLOBAL
        return None
```

## Step 4 — test minimi

Crea `trader\_profiles/trader\_X/tests/test\_profile\_real\_cases.py` con almeno:

```python
import pytest
from src.parser.trader\_profiles.trader\_X import TraderXProfileParser
from src.parser.trader\_profiles.base import ParserContext

@pytest.fixture
def parser():
    return TraderXProfileParser()

def make\_context(text, reply\_to=None):
    return ParserContext(
        trader\_code="trader\_x",
        message\_id=1,
        reply\_to\_message\_id=reply\_to,
        channel\_id="-100123",
        raw\_text=text,
    )

def test\_new\_signal\_complete(parser):
    text = "..."  # messaggio reale dal CSV
    result = parser.parse\_message(text, make\_context(text))
    assert result.message\_type == "NEW\_SIGNAL"
    assert result.completeness == "COMPLETE"
    assert result.entities.symbol == "BTCUSDT"

def test\_update\_with\_reply(parser):
    text = "..."
    result = parser.parse\_message(text, make\_context(text, reply\_to=265))
    assert result.message\_type == "UPDATE"
    assert result.target\_ref.kind == "STRONG"
    assert result.target\_ref.method == "REPLY"

def test\_info\_only(parser):
    text = "..."
    result = parser.parse\_message(text, make\_context(text))
    assert result.message\_type == "INFO\_ONLY"
```

# Profilo di riferimento

Leggi `src/parser/trader\_profiles/trader\_3/` come esempio completo prima di costruire un nuovo profilo.

# Guardrail

* non inventare marcatori non trovati nei messaggi reali
* se un campo non è affidabile, non estrarlo — meglio INCOMPLETE che sbagliato
* confidence bassa (< 0.6) se classificazione ambigua
* non mischiare logica trader-specific con utilities condivise
* usa `shared\_vocabulary` quando il trader scrive in russo o inglese standard

# Output richiesto

Restituisci sempre:

1. `parsing\_rules.json` compilato
2. skeleton `profile.py` con TODO espliciti
3. casi di test minimi
4. marcatori ambigui identificati
5. edge case da gestire nel profilo


# intake — Istruzioni d'uso

## Come istanziare RuntimeV2IntakeProcessor

```python
from src.runtime_v2.intake.models import IntakeConfig
from src.runtime_v2.intake.eligibility import IntakeEligibilityCheck
from src.runtime_v2.intake.processor import RuntimeV2IntakeProcessor
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelConfigResolver
from src.runtime_v2.trader_resolution.resolver import RuntimeV2TraderResolver
from src.storage.raw_messages import RawMessageStore
from src.telegram.effective_trader import EffectiveTraderResolver

DB_PATH = "db/live.db"
CHANNELS_YAML = "config/channels.yaml"

# Dipendenze
repo = RawMessageRepository(db_path=DB_PATH)
channel_config = ChannelConfigResolver(config_path=CHANNELS_YAML)

raw_store = RawMessageStore(DB_PATH)
effective_resolver = EffectiveTraderResolver(
    source_mapper=...,
    raw_store=raw_store,
    trader_aliases=...,
    known_trader_ids=...,
)

resolver = RuntimeV2TraderResolver(
    channel_config_resolver=channel_config,
    effective_trader_resolver=effective_resolver,
)
eligibility = IntakeEligibilityCheck(raw_store=raw_store)
config = IntakeConfig(reply_chain_depth_limit=5)

processor = RuntimeV2IntakeProcessor(
    repo=repo,
    eligibility=eligibility,
    resolver=resolver,
    channel_config=channel_config,
    config=config,
)
```

## Come chiamare process()

```python
from src.runtime_v2.intake.models import RawIngestItem
from datetime import datetime, timezone

item = RawIngestItem(
    source_chat_id="-100123456789",
    source_chat_title="Trader A Signals",
    source_type="channel",
    source_topic_id=None,           # None se il canale non ha topic
    telegram_message_id=12345,
    reply_to_message_id=None,
    raw_text="BUY BTC 45000 SL 44000 TP 47000",
    message_ts=datetime.now(timezone.utc),
    acquisition_mode="live",        # "live" | "catchup" | "import"
    has_media=False,
    media_kind=None,
    media_mime_type=None,
    media_filename=None,
)

candidate = processor.process(item)

if candidate is None:
    # Il messaggio è stato scartato (blacklisted, media-only, review, unresolved)
    # Controlla processing_status in DB per dettagli
    pass
else:
    # Messaggio pronto per il parser
    print(candidate.parser_profile)       # es. "trader_a"
    print(candidate.resolved_trader.trader_id)
    print(candidate.parser_context)
```

## Cosa fare se il messaggio torna None

| Causa | processing_status | acquisition_status | Azione |
|-------|------------------|--------------------|--------|
| Blacklist globale | `blacklisted` | `BLACKLISTED` | Ignorare |
| Media senza testo | `skipped` | `MEDIA_ONLY_SKIPPED` | Ignorare |
| Breve update non collegato | `review` | `ACQUIRED` | Revisione manuale |
| Trader ambiguo o non trovato | `review` | `ACQUIRED` | Revisione manuale |
| Nessun profilo parser_v2 | `review` | `ACQUIRED` | Aggiungere profilo o rivedere |

## Configurazione reply_chain_depth_limit

```python
config = IntakeConfig(reply_chain_depth_limit=10)  # default: 5
```

> **Nota:** Il limite è dichiarato come contratto in `IntakeConfig` ma l'enforcement sull'`EffectiveTraderResolver` è pendente — quest'ultimo usa ancora un depth interno fisso.

## Test

```bash
pytest tests/runtime_v2/test_intake_processor.py -v
pytest tests/runtime_v2/test_acceptance.py -v
```

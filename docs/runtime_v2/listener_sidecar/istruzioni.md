# listener_sidecar — Istruzioni d'uso

## Prerequisiti

1. Migration 023 applicata (`raw_messages` ha le colonne runtime_v2)
2. Migration 024 applicata (`canonical_messages` esiste)
3. `config/channels.yaml` ha almeno un canale con `parser_profile` valido

## Attivazione

```bash
USE_RUNTIME_V2=1 python main.py
```

Il sidecar viene istanziato automaticamente in `main.py` quando `USE_RUNTIME_V2=1`. Log al boot:

```
INFO  runtime_v2 sidecar enabled
```

Per ogni messaggio processato con canale riconosciuto e attivo:

```
INFO  runtime_v2: parsed | raw_message_id=42 profile=trader_a class=SIGNAL status=PARSED canonical_id=1
```

In caso di failure del parser:

```
WARNING  runtime_v2: parse failed | raw_message_id=42 reason=unknown_parser_profile
```

## Uso programmatico

```python
from src.runtime_v2.listener_sidecar import RuntimeV2ListenerSidecar
import logging

sidecar = RuntimeV2ListenerSidecar(
    db_path="db/live.db",
    channels_config_path="config/channels.yaml",
    logger=logging.getLogger("runtime_v2"),
)

# Chiamato da TelegramListener._process_item() per ogni QueueItem:
sidecar.process_queue_item(queue_item)

# Chiamato da TelegramListener.update_config() su hot-reload:
sidecar.reload_config()
```

## Configurazione channels.yaml

Il sidecar legge `channels.yaml` tramite `ChannelConfigResolver`. I campi rilevanti:

```yaml
channels:
  - chat_id: "-1001234567890"
    trader_id: trader_a
    parser_profile: trader_a   # opzionale — default = trader_id
    active: true
    topic_id: null             # null = tutti i topic di questo canale
```

Se `parser_profile` non è specificato, viene usato `trader_id`. Se il profilo non è registrato in `parser_v2`, il messaggio viene saltato silenziosamente con log WARNING.

## Cosa succede per canali non configurati

Il sidecar fa lookup `(source_chat_id, source_topic_id)`. Se non trova entry, o l'entry è `active: false`, ritorna senza fare nulla. Il messaggio viene comunque processato dal legacy router.

## Verifica risultati

```python
from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

repo = CanonicalMessageRepository(db_path="db/live.db")
msg = repo.get_by_raw_message_id(raw_message_id=42)
if msg:
    print(msg.primary_class)   # "SIGNAL", "UPDATE", "REPORT", "INFO"
    print(msg.parse_status)    # "PARSED", "PARTIAL", "UNCLASSIFIED"
```

## Test

```bash
pytest tests/runtime_v2/test_listener_sidecar.py -v
```

# trader_resolution — Istruzioni d'uso

## Configurare channels.yaml

Il file `config/channels.yaml` è la fonte principale di risoluzione config-driven.

```yaml
recovery:
  max_hours: 7

blacklist_global:
  - "#admin"
  - "#pinned"

channels:
  # Canale mono-trader (senza topic)
  - chat_id: -1001234567890
    label: "Trader A — segnali"
    active: true
    trader_id: trader_a
    blacklist: []

  # Canale con topic separati per trader diversi
  - chat_id: -1009876543210
    topic_id: 3
    label: "Trader B — Topic 3"
    active: true
    trader_id: trader_b
    blacklist: ["#info", "#admin"]

  - chat_id: -1009876543210
    topic_id: 7
    label: "Trader C — Topic 7"
    active: true
    trader_id: trader_c
    blacklist: []

  # Override parser_profile (default = trader_id)
  - chat_id: -1005555555555
    label: "Trader D con profilo custom"
    active: true
    trader_id: trader_d
    parser_profile: trader_d_v2    # usa questo invece di "trader_d"
    blacklist: []

  # Canale disattivato → cade su EffectiveTraderResolver
  - chat_id: -1006666666666
    label: "Vecchio canale"
    active: false
    trader_id: trader_e
    blacklist: []
```

**Regole:**
- `chat_id` deve essere intero (con segno negativo per canali/supergruppi)
- `topic_id` è opzionale — omettilo per canali senza forum
- `active: false` → channels.yaml non viene usato, si delega al resolver testuale
- `blacklist` è locale al canale; `blacklist_global` vale per tutti
- Se `parser_profile` non è specificato, viene usato `trader_id` come profilo

## Usare ChannelConfigResolver standalone

```python
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelConfigResolver

resolver = ChannelConfigResolver("config/channels.yaml")

# Lookup (chat_id, topic_id)
entry = resolver.lookup("-1001234567890", topic_id=3)
if entry and entry.active and entry.trader_id:
    print(entry.trader_id)        # "trader_b"
    print(entry.parser_profile)   # "trader_b" (o override da yaml)

# Fallback automatico: topic_id=99 non trovato → cerca (chat_id, None)
entry = resolver.lookup("-1001234567890", topic_id=99)

# Blacklist globale
if resolver.is_globally_blacklisted(raw_text):
    # messaggio da scartare

# Ricarica dopo modifica file
resolver.reload()
```

## Usare RuntimeV2TraderResolver standalone

```python
from src.runtime_v2.trader_resolution.resolver import RuntimeV2TraderResolver

trader_resolver = RuntimeV2TraderResolver(
    channel_config_resolver=channel_config,
    effective_trader_resolver=effective_resolver,
)

ctx = trader_resolver.resolve(envelope)
print(ctx.trader_id)      # es. "trader_a" o None
print(ctx.method)         # es. "source_chat_id"
print(ctx.is_ambiguous)   # True se alias ambiguo
```

## Aggiungere un nuovo trader

1. Aggiungere la voce in `config/channels.yaml`
2. Creare il profilo parser_v2 in `src/parser_v2/profiles/`
3. Verificare che `list_parser_v2_profiles()` includa il nuovo trader_id
4. Opzionale: specificare `parser_profile` se il nome del profilo differisce da `trader_id`

## Test

```bash
pytest tests/runtime_v2/test_channel_config_resolver.py -v
pytest tests/runtime_v2/test_trader_resolver.py -v
pytest tests/runtime_v2/test_trader_resolution_models.py -v
```

# config/channels.yaml — Guida al file di configurazione

## Scopo

`config/channels.yaml` mappa i canali Telegram ai trader registrati nel sistema. È la fonte primaria di risoluzione config-driven: se un canale è configurato qui con `active: true`, il trader viene risolto senza analizzare il testo del messaggio.

## Struttura completa

```yaml
recovery:
  max_hours: 7              # Ore di lookback per il recovery di messaggi persi

blacklist_global:           # Frasi che, se presenti nel testo, scartano il messaggio
  - "#admin"
  - "#pinned"
  - "#info"

channels:
  - chat_id: -1001234567890           # ID Telegram del canale (intero, con segno)
    topic_id: 3                       # Opzionale — solo per canali con forum/topic
    label: "Trader A — segnali"       # Nome leggibile, non usato dal codice
    active: true                      # false = canale ignorato da config-driven
    trader_id: trader_a               # ID trader nel sistema
    parser_profile: trader_a_v2       # Opzionale — override del profilo parser
    blacklist:                        # Frasi blacklist locali al canale
      - "#skip"
```

## Campi

| Campo | Tipo | Obbligatorio | Default | Descrizione |
|-------|------|-------------|---------|-------------|
| `chat_id` | int | sì | — | ID Telegram del canale/supergruppo |
| `topic_id` | int | no | — | ID del topic/forum thread |
| `label` | string | no | — | Nome descrittivo (solo per leggibilità) |
| `active` | bool | sì | — | Se false, cade su EffectiveTraderResolver |
| `trader_id` | string | sì | — | ID del trader (deve esistere in parser_v2 registry) |
| `parser_profile` | string | no | = `trader_id` | Override del profilo parser |
| `blacklist` | list[string] | no | `[]` | Frasi locali che scartano il messaggio |

## Logica di lookup

```
Messaggio con (source_chat_id="-100123", source_topic_id=3)

1. Cerca (-100123, 3)        → match esatto → method="source_topic_config"
2. Se non trovato: (-100123, None) → fallback chat-level → method="source_chat_id"
3. Se non trovato: nessun config → EffectiveTraderResolver
```

## Regola parser_profile

Il `parser_profile` determina quale profilo parser_v2 verrà usato per il messaggio.

- Se specificato in yaml: usa quello
- Se non specificato: usa `trader_id`
- Se il profilo non esiste in `list_parser_v2_profiles()`: messaggio → `review`

```yaml
# Esempio: trader_e ha profilo diverso dal proprio ID
- chat_id: -1005555555555
  trader_id: trader_e
  parser_profile: trader_e_experimental   # profilo alternativo
  active: true
  blacklist: []
```

## Blacklist

Le frasi blacklist vengono cercate per substring nel testo del messaggio.

```yaml
blacklist_global:
  - "#admin"       # qualunque testo che contiene "#admin" → BLACKLISTED

channels:
  - chat_id: -100123
    blacklist:
      - "pinned message"   # solo per questo canale
```

## Ricarica a caldo

`ChannelConfigResolver.reload()` rilegge il file senza riavviare il processo:

```python
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelConfigResolver

resolver = ChannelConfigResolver("config/channels.yaml")

# Dopo aver modificato il file:
resolver.reload()
```

Il watchdog esterno (se configurato) può chiamare `reload()` automaticamente al cambio file.

## Percorso del file

Il percorso viene passato esplicitamente a `ChannelConfigResolver`. Non c'è un path hardcoded nel codice — il chiamante decide quale file usare (utile per test con file temporanei).

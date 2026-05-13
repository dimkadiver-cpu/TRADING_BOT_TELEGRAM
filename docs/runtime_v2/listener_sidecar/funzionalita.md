# listener_sidecar — Funzionalità

## Responsabilità

`RuntimeV2ListenerSidecar` è il bridge tra il listener Telegram legacy e la pipeline runtime_v2. Gira in modalità shadow: viene invocato dopo `legacy_router.route(item)` in `TelegramListener._process_item()`, senza interferire con il flusso legacy.

Non re-ingest il messaggio. Non modifica `raw_messages`. Legge l'envelope già persistito dal listener legacy e lo processa tramite `ParserPipelineProcessor`.

## Componenti

### `RuntimeV2ListenerSidecar`

**Costruttore:** `RuntimeV2ListenerSidecar(*, db_path: str, channels_config_path: str, logger: logging.Logger)`

Costruisce internamente:
- `ChannelConfigResolver` — lookup channels.yaml per `parser_profile`
- `RawMessageRepository` — lettura envelope dal DB
- `ParserPipelineProcessor` + `CanonicalMessageRepository`

**Metodi pubblici:**

| Metodo | Descrizione |
|--------|-------------|
| `process_queue_item(item)` | Entry point. Wrapper con except/log totale — non solleva mai eccezioni. |
| `reload_config()` | Ricarica channels.yaml. Chiamato da `TelegramListener.update_config()` su hot-reload. |

## Flusso interno `_process(item)`

```
item.source_chat_id + item.source_topic_id
      ↓
ChannelConfigResolver.lookup()     → entry None o inactive → return (silent skip)
      ↓
RawMessageRepository.get_by_id()   ← legge envelope già in DB
      ↓
build ResolvedTraderContext         ← method="source_chat_id", trader_id=entry.trader_id
      ↓
build ParserContext                 ← da envelope (raw_text, message_id, reply_to, ...)
      ↓
build ParserDispatchCandidate       ← parser_profile=entry.parser_profile
      ↓
ParserPipelineProcessor.process()
      ↓
CanonicalParseResult → log INFO     |  ParserJobStatus → log WARNING
      ↓
canonical_messages (DB)
```

## Differenze con RuntimeV2IntakeProcessor

| | `RuntimeV2IntakeProcessor` | `RuntimeV2ListenerSidecar` |
|---|---|---|
| Contesto | Pipeline autonoma (senza legacy) | Shadow affiancato al legacy router |
| Salva raw message | Sì (via `save_raw`) | No — legge envelope già in DB |
| Blacklist check | Sì | No — già fatto dal listener legacy |
| Eligibility check | Sì | No — già fatto dal listener legacy |
| Trader resolution | ChannelConfigResolver + fallback | Solo ChannelConfigResolver |
| Aggiorna processing_status | Sì | No |
| Non solleva mai | No | Sì — tutto swallowed in `process_queue_item` |

## Integrazione con TelegramListener

`TelegramListener` accetta `sidecar: object | None = None` nel costruttore:

```python
def _process_item(self, item: QueueItem) -> None:
    self._router.route(item)
    if self._sidecar is not None:
        self._sidecar.process_queue_item(item)

def update_config(self, new_config: ChannelsConfig) -> None:
    self._config = new_config
    self._router.update_config(new_config)
    if self._sidecar is not None:
        self._sidecar.reload_config()
```

Il tipo è `object | None` per evitare dipendenza circolare tra `src/telegram/` e `src/runtime_v2/`.

## Garanzie

- **Non blocca il legacy router**: chiamato dopo `router.route(item)`, qualsiasi eccezione è swallowed.
- **Idempotente**: `CanonicalMessageRepository.save()` usa `INSERT OR IGNORE` — stesso messaggio processato due volte non crea duplicati.
- **Config sync**: `reload_config()` è chiamato automaticamente ad ogni hot-reload di channels.yaml.

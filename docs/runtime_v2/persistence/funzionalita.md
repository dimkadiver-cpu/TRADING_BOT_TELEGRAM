# persistence — Funzionalità

## Responsabilità

Il package `persistence` espone un adapter thin su `RawMessageStore` e `ProcessingStatusStore` (storage legacy) con l'aggiunta della gestione delle colonne introdotte dalla migration 023.

## Componente

### `raw_messages.py`

- **`RawMessageRepository`** — unica classe pubblica. Gestisce tutta la persistenza dei messaggi raw nel contesto runtime_v2.

  **Costruttore:** `RawMessageRepository(db_path: str)`

  **Metodi pubblici:**

  | Metodo | Descrizione |
  |--------|-------------|
  | `save_raw(item: RawIngestItem) -> RawMessageEnvelope` | Persiste o recupera per dedup. Dedup per `(source_chat_id, telegram_message_id)`. Imposta `acquisition_status=ACQUIRED`. |
  | `get_by_id(raw_message_id: int) -> RawMessageEnvelope` | Legge una riga completa dal DB. |
  | `set_blacklisted(raw_message_id: int) -> None` | Imposta `acquisition_status=BLACKLISTED` e `processing_status=blacklisted`. |
  | `set_media_only_skipped(raw_message_id: int) -> None` | Imposta `acquisition_status=MEDIA_ONLY_SKIPPED` e `processing_status=skipped`. |
  | `update_processing_status(raw_message_id: int, status: ProcessingStatusV2) -> None` | Aggiorna solo `processing_status`. Non tocca `acquisition_status`. |
  | `update_trader_resolution(raw_message_id: int, ctx: ResolvedTraderContext) -> None` | Scrive `resolved_trader_id`, `resolution_method`, `resolution_detail`. |

## Colonne gestite

Le colonne standard (`raw_text`, `message_ts`, ecc.) sono gestite da `RawMessageStore`.
Le colonne runtime_v2 — aggiunte dalla migration 023 — sono gestite direttamente con SQL:

| Colonna | Tipo | Gestita da |
|---------|------|-----------|
| `acquisition_mode` | TEXT | `save_raw` |
| `resolved_trader_id` | TEXT nullable | `update_trader_resolution` |
| `resolution_method` | TEXT nullable | `update_trader_resolution` |
| `resolution_detail` | TEXT nullable | `update_trader_resolution` |

## Migration DB

La migration che aggiunge queste colonne è:

```
db/migrations/023_runtime_v2_raw_messages.sql
```

Deve essere applicata prima di usare `RawMessageRepository`. Vedi `docs/runtime_v2/persistence/istruzioni.md`.

## Invariante acquisition_status

`acquisition_status` viene scritto in tre soli punti e mai modificato dopo:
- `save_raw()` → `ACQUIRED`
- `set_blacklisted()` → `BLACKLISTED`
- `set_media_only_skipped()` → `MEDIA_ONLY_SKIPPED`

`update_processing_status()` modifica **solo** `processing_status` e non tocca `acquisition_status`.

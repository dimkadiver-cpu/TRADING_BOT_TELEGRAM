# persistence — Funzionalità

## Responsabilità

Il package `persistence` gestisce tutta la persistenza del runtime_v2: messaggi raw (migration 023) e messaggi canonici parsati (migration 024).

## Componenti

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

---

### `canonical_messages.py`

- **`CanonicalMessageRepository`** — unica classe pubblica. Persiste i `CanonicalMessage` prodotti da `ParserPipelineProcessor` nella tabella `canonical_messages`.

  **Costruttore:** `CanonicalMessageRepository(db_path: str)`

  **Metodi pubblici:**

  | Metodo | Descrizione |
  |--------|-------------|
  | `save(raw_message_id, canonical, run_context="live") -> int` | Persiste il `CanonicalMessage`. Idempotente: secondo salvataggio con stesso `(raw_message_id, run_context)` restituisce l'ID esistente senza creare duplicati. |
  | `get_by_raw_message_id(raw_message_id, run_context="live") -> CanonicalMessage \| None` | Recupera il `CanonicalMessage` dal DB. Restituisce `None` se non trovato. |

## Schema tabella `canonical_messages`

| Colonna | Tipo | Note |
|---------|------|------|
| `canonical_message_id` | INTEGER PK | Autoincrement |
| `raw_message_id` | INTEGER NOT NULL | Reference logica a `raw_messages` (no FK) |
| `run_context` | TEXT DEFAULT 'live' | Distingue live da re-parse futuri |
| `parser_profile` | TEXT NOT NULL | Profilo usato |
| `schema_version` | TEXT NOT NULL | `canonical_message_v2` |
| `primary_class` | TEXT NOT NULL | `SIGNAL \| UPDATE \| REPORT \| INFO` |
| `parse_status` | TEXT NOT NULL | `PARSED \| PARTIAL \| UNCLASSIFIED` |
| `primary_intent` | TEXT nullable | Intent principale (UPDATE) |
| `confidence` | REAL NOT NULL | Score classificazione |
| `canonical_json` | TEXT NOT NULL | `CanonicalMessage.model_dump_json()` |
| `warnings_json` | TEXT DEFAULT '[]' | Warning list JSON |
| `diagnostics_json` | TEXT DEFAULT '{}' | Diagnostics dict JSON |
| `parsed_at` | TEXT NOT NULL | ISO 8601 UTC |

**Constraint:** `UNIQUE(raw_message_id, run_context)` — un messaggio raw non produce due righe nello stesso contesto.

**Principio di separazione:** `canonical_messages` appartiene al parser bounded context. Nessuna FK verso tabelle operative. Il `processing_status` di `raw_messages` non viene mai modificato dal parser pipeline — resta `done` come impostato dall'intake.

## Migration DB

```
db/migrations/024_runtime_v2_canonical_messages.sql
```

Additiva. Non modifica tabelle esistenti. Vedi `docs/runtime_v2/config/migration_024.md`.

# Migration 025 — DROP tabelle legacy

**File:** `db/migrations/025_drop_legacy_tables.sql`
**PRD:** 2.c — Legacy Elimination
**Tipo:** distruttiva — DROP senza possibilità di rollback automatico

## Scopo

Rimuove le 16 tabelle del layer legacy (parser v1, operation rules, execution) che non sono più scritte né lette dopo la promozione di runtime_v2 a stack primario.

## Tabelle droppate

| Tabella | Layer legacy |
|---|---|
| `parse_results` | Output parser v1 |
| `parse_results_v1` | Shadow canonical v1 |
| `parsed_messages` | Dual-stack fase 4.5 |
| `review_queue` | Coda revisione manuale |
| `operational_signals` | Operation rules legacy |
| `signals` | Execution legacy |
| `events` | Execution legacy |
| `warnings` | Execution legacy |
| `trades` | Execution legacy |
| `orders` | Execution legacy |
| `fills` | Execution legacy |
| `positions` | Execution legacy |
| `exchange_events` | Execution legacy |
| `backtest_runs` | Backtesting legacy |
| `backtest_trades` | Backtesting legacy |
| `protective_orders_mode` | Execution legacy |

## Tabelle che rimangono

| Tabella | Stack |
|---|---|
| `schema_migrations` | Infrastruttura |
| `raw_messages` | Condivisa — listener + runtime_v2 |
| `canonical_messages` | runtime_v2 — output parser pipeline |

## Note

- Usa `DROP TABLE IF EXISTS` — idempotente, non fallisce se già eseguita o se la tabella non esisteva
- I dati storici nelle tabelle droppate vanno persi — accettato esplicitamente nel design PRD 2.c
- I moduli legacy (`src/telegram/router.py`, `src/storage/`, `src/execution/`, ecc.) restano nel filesystem ma non vengono più istanziati da `main.py`

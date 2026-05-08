# Design — Parser Test v2: Trader Filter & Parser Selection

**Data:** 2026-05-08
**PRD di riferimento:** `parser_test/docs/PRD_parser_test_v2_trader_filter_parser_selection_v2.md`

---

## Obiettivo

Separare quattro concetti oggi mescolati in `replay_parser_v2.py --trader`:

```
source_trader_id      = trader noto dalla sorgente/import
resolved_trader_id    = trader effettivo risolto con logica live
message_trader_filter = quali messaggi includere nel replay
parser_profile        = quale parser/profilo usare per parsare
```

---

## Approccio scelto

**Tre script indipendenti** con responsabilità separate:

```
import_history.py      [+ --default-source-trader]
resolve_traders.py     [nuovo — persiste resolved_trader_id su raw_messages]
replay_parser_v2.py    [refactored — nuovi flag, --trader deprecato]
```

---

## Schema

### `raw_messages` — nuove colonne

```sql
ALTER TABLE raw_messages ADD COLUMN resolved_trader_id TEXT;
ALTER TABLE raw_messages ADD COLUMN resolution_method  TEXT;
```

`resolved_trader_id` è NULL finché `resolve_traders.py` non è stato eseguito.
`resolution_method` traccia come è stato risolto:
`source_trader_id` | `content_alias` | `reply_chain` | `assume_trader` | `unresolved`

### `parser_runs` — nuove colonne

```sql
ALTER TABLE parser_runs ADD COLUMN assume_trader  TEXT;   -- nuovo
ALTER TABLE parser_runs ADD COLUMN parser_profile TEXT;   -- nuovo
-- trader_filter e parser_system esistono già
```

### `parser_results_v2` — nessuna modifica

Solo `OK` e `PARSER_ERROR` vengono scritti. Gli skip non vengono persistiti nel DB.

### Migrazione schema

`apply_parser_test_schema()` in `parser_test/db/schema.py` usa `ADD COLUMN IF NOT EXISTS` — nessuna migration file separata, idempotente su DB esistenti.

---

## Modulo condiviso: `parser_test/scripts/trader_resolution.py`

Estrae da `replay_parser_v2.py` le funzioni condivise:

```python
def build_trader_resolver(db_path: str) -> EffectiveTraderResolver: ...
def normalize_trader_id(value: str | None) -> str | None: ...
```

Importato da `resolve_traders.py` e `replay_parser_v2.py`.

---

## `import_history.py`

### Modifica

Aggiunta del flag:

```bash
--default-source-trader trader_a
```

Comportamento: se fornito, `TelegramIncomingMessage.source_trader_id` viene valorizzato con il valore passato invece di `None`. Nessun'altra logica cambia.

Usare solo per sorgenti mono-trader reali.

---

## `resolve_traders.py` (nuovo)

### CLI

```bash
python parser_test/scripts/resolve_traders.py \
  --db-path parser_test/db/multi.sqlite3 \
  [--db-name ...]
  [--db-per-chat]
  [--assume-trader trader_a]
  [--force-re-resolve]
```

### Logica

Per ogni riga in `raw_messages`:

1. Se `resolved_trader_id` già valorizzato e `--force-re-resolve` non fornito → skip
2. Priorità di risoluzione:
   - `source_trader_id` presente → `resolution_method = "source_trader_id"`
   - `EffectiveTraderResolver` (content_alias, reply_chain, source_map) → `method = resolver.method`
   - `--assume-trader` fornito → `resolution_method = "assume_trader"`
   - Nessun risultato → `resolved_trader_id = NULL`, `resolution_method = "unresolved"`
3. Scrive `resolved_trader_id` + `resolution_method` su `raw_messages`

### Output console

```
[resolve] 1240 messaggi trovati
[resolve] 980 già risolti (skip)
[resolve] 260 da risolvere
[resolve] completato — source_trader_id: 200 | content_alias: 30 | reply_chain: 10 | assume_trader: 5 | unresolved: 15
```

---

## `replay_parser_v2.py` — refactoring

### Nuovi argomenti CLI

| Flag | Significato |
|---|---|
| `--trader-filter trader_a` | Processa solo messaggi con `resolved_trader_id = trader_a` |
| `--assume-trader trader_a` | Fallback se `resolved_trader_id` è NULL |
| `--parser-system parser_v2` | Sistema parser (default: `parser_v2`) |
| `--parser-profile auto\|trader_a` | Parser/profilo da usare (default: `auto`) |
| `--allow-cross-profile-parse` | Permette parser_profile != resolved_trader_id |
| `--audit-csv` | Genera CSV audit con tutti gli stati inclusi gli skip |

### `--trader` deprecato

```
[warning] --trader is deprecated; use --trader-filter for message selection or --assume-trader for fallback.
```

Ancora funzionante come alias di `--trader-filter`.

### Logica per ogni messaggio

```
resolved_trader_id = raw.resolved_trader_id        # dalla colonna persistita
                     OR _resolve_inline(raw)        # fallback se colonna NULL
                     OR assume_trader               # --assume-trader
                     OR None → UNRESOLVED_TRADER (skip, non scritto in DB)

Se --trader-filter e resolved_trader_id != trader_filter:
    → SKIPPED_TRADER_FILTER (skip, non scritto in DB)

parser_profile = resolved_trader_id        se --parser-profile auto (default)
               = valore esplicito          se --parser-profile <nome>

Se parser_profile non esiste nel registry:
    → SKIPPED_UNSUPPORTED_PARSER_PROFILE (skip, non scritto in DB)

Se --parser-profile fixed e parser_profile != resolved_trader_id
   e non --allow-cross-profile-parse:
    → SKIPPED_TRADER_FILTER (protezione cross-profile)

Parsing:
    OK           → scritto in parser_results_v2
    PARSER_ERROR → scritto in parser_results_v2
```

### Stati tracciati (contatori a console)

```
OK
UNRESOLVED_TRADER
SKIPPED_TRADER_FILTER
SKIPPED_UNSUPPORTED_PARSER_PROFILE
PARSER_ERROR
```

### `run_replay()` — nuova firma

```python
def run_replay(
    conn: sqlite3.Connection,
    *,
    db_path: str | None = None,
    trader_filter: str | None = None,       # era: trader
    assume_trader: str | None = None,        # nuovo
    parser_system: str = "parser_v2",        # nuovo
    parser_profile: str = "auto",            # nuovo
    allow_cross_profile_parse: bool = False, # nuovo
    audit_csv_path: Path | None = None,      # nuovo
    chat_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int | None = None,
    only_unparsed: bool = False,
    force_reparse: bool = False,
    show_samples: int = 0,
    trader_resolver: EffectiveTraderResolver | None = None,
) -> int:
```

---

## Reporting

### `report_export_v2.py` — nessuna modifica alla logica esistente

Continua a esportare solo `error_status = 'OK'`.

### Audit CSV — nuova funzione `export_audit_csv()`

Invocata solo se `--audit-csv`. Produce `audit_run_<run_id>.csv` nella directory del run.

**Colonne:**

```
raw_message_id | source_trader_id | resolved_trader_id | parser_profile
error_status   | error_message    | source_chat_id     | source_topic_id
telegram_message_id | message_ts  | text_preview
```

Gli skip vengono accumulati in memoria durante il run (lista di `_AuditRow`) e scritti alla fine — non passano per il DB.

### `generate_parser_reports_v2.py`

Aggiornato con gli stessi nuovi flag. `--trader` deprecato con warning anche qui.

---

## Flussi operativi

### Mono-trader

```bash
python parser_test/scripts/import_history.py \
  --db-path parser_test/db/trader_a.sqlite3 \
  --chat-id -3722628653 --topic-id 3 \
  --default-source-trader trader_a

python parser_test/scripts/resolve_traders.py \
  --db-path parser_test/db/trader_a.sqlite3

python parser_test/scripts/replay_parser_v2.py \
  --db-path parser_test/db/trader_a.sqlite3 \
  --trader-filter trader_a \
  --parser-profile trader_a \
  --force-reparse
```

### Multitrader

```bash
python parser_test/scripts/import_history.py \
  --db-path parser_test/db/multi.sqlite3 \
  --chat-id -3722628653

python parser_test/scripts/resolve_traders.py \
  --db-path parser_test/db/multi.sqlite3

python parser_test/scripts/replay_parser_v2.py \
  --db-path parser_test/db/multi.sqlite3 \
  --trader-filter trader_a \
  --parser-profile trader_a \
  --force-reparse
```

### Parser auto su tutto il DB

```bash
python parser_test/scripts/replay_parser_v2.py \
  --db-path parser_test/db/multi.sqlite3 \
  --parser-profile auto \
  --force-reparse
```

---

## File toccati

| File | Tipo modifica |
|---|---|
| `parser_test/db/schema.py` | Aggiunta colonne `resolved_trader_id`, `resolution_method`, `assume_trader`, `parser_profile` |
| `parser_test/scripts/trader_resolution.py` | **Nuovo** — modulo condiviso |
| `parser_test/scripts/import_history.py` | Aggiunta `--default-source-trader` |
| `parser_test/scripts/resolve_traders.py` | **Nuovo** script |
| `parser_test/scripts/replay_parser_v2.py` | Refactoring flag + logica |
| `parser_test/reporting/report_export_v2.py` | Aggiunta `export_audit_csv()` |
| `parser_test/scripts/generate_parser_reports_v2.py` | Aggiornamento flag |

---

## Test di accettazione (dal PRD §15)

1. `--default-source-trader trader_a` → `raw_messages.source_trader_id = trader_a`
2. Import senza default → `source_trader_id = NULL`
3. `resolve_traders.py` → `source_trader_id` ha priorità su resolver live
4. `--trader-filter trader_a --parser-profile trader_a` → CSV solo `trader_id = trader_a`
5. `--parser-profile trader_a_experimental` su messaggi Trader A → parsati con profilo alternativo
6. `--parser-profile auto` → profilo non disponibile → `SKIPPED_UNSUPPORTED_PARSER_PROFILE`

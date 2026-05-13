# db/migrations/024_runtime_v2_canonical_messages.sql

## Scopo

Migration additive che crea la tabella `canonical_messages` per il runtime live. Non modifica tabelle esistenti — sicura da applicare su DB con dati PRD 01.

## Contenuto

```sql
CREATE TABLE IF NOT EXISTS canonical_messages (
    canonical_message_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_message_id        INTEGER NOT NULL,
    run_context           TEXT    NOT NULL DEFAULT 'live',
    parser_profile        TEXT    NOT NULL,
    schema_version        TEXT    NOT NULL,
    primary_class         TEXT    NOT NULL,
    parse_status          TEXT    NOT NULL,
    primary_intent        TEXT,
    confidence            REAL    NOT NULL,
    canonical_json        TEXT    NOT NULL,
    warnings_json         TEXT    NOT NULL DEFAULT '[]',
    diagnostics_json      TEXT    NOT NULL DEFAULT '{}',
    parsed_at             TEXT    NOT NULL,
    UNIQUE(raw_message_id, run_context)
);

CREATE INDEX IF NOT EXISTS idx_canonical_messages_raw
    ON canonical_messages(raw_message_id);

CREATE INDEX IF NOT EXISTS idx_canonical_messages_class
    ON canonical_messages(primary_class, parse_status);

CREATE INDEX IF NOT EXISTS idx_canonical_messages_profile
    ON canonical_messages(parser_profile);

CREATE INDEX IF NOT EXISTS idx_canonical_messages_parsed_at
    ON canonical_messages(parsed_at);
```

## Note di design

- Nessuna FK verso `raw_messages` — separabilità fisica futura in `parser_db`.
- `run_context` distingue `live` da re-parse (`reparse_YYYYMMDD`). Garantisce idempotenza.
- `canonical_json` = `CanonicalMessage.model_dump_json()` — source of truth per layer downstream.
- `warnings_json` e `diagnostics_json` colonne separate per query di debug senza deserializzare il JSON completo.

## Applicazione

```bash
python -c "
import sqlite3, pathlib
conn = sqlite3.connect('db/live.db')
conn.executescript(pathlib.Path('db/migrations/024_runtime_v2_canonical_messages.sql').read_text())
conn.commit()
tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")]
print('OK' if 'canonical_messages' in tables else 'MANCANTE')
conn.close()
"
```

## Verifica

```bash
python -c "
import sqlite3
conn = sqlite3.connect('db/live.db')
cols = [r[1] for r in conn.execute('PRAGMA table_info(canonical_messages)')]
needed = {'raw_message_id','run_context','parser_profile','canonical_json','parsed_at'}
missing = needed - set(cols)
print('OK' if not missing else f'MANCANTI: {missing}')
conn.close()
"
```

# db/migrations/023_runtime_v2_raw_messages.sql

## Scopo

Migration additive che aggiunge le colonne necessarie a `runtime_v2` nella tabella `raw_messages`. Non modifica colonne esistenti — è sicura da applicare su DB in produzione con dati.

## Contenuto

```sql
ALTER TABLE raw_messages ADD COLUMN acquisition_mode TEXT NOT NULL DEFAULT 'live';
ALTER TABLE raw_messages ADD COLUMN resolved_trader_id TEXT;
ALTER TABLE raw_messages ADD COLUMN resolution_method TEXT;
ALTER TABLE raw_messages ADD COLUMN resolution_detail TEXT;

CREATE INDEX IF NOT EXISTS idx_raw_messages_resolved_trader_id
    ON raw_messages(resolved_trader_id);
```

## Colonne aggiunte

| Colonna | Tipo | Default | Descrizione |
|---------|------|---------|-------------|
| `acquisition_mode` | TEXT NOT NULL | `'live'` | Modalità di acquisizione: `live`, `catchup`, `import` |
| `resolved_trader_id` | TEXT nullable | NULL | ID trader risolto dall'intake pipeline |
| `resolution_method` | TEXT nullable | NULL | Metodo usato per la risoluzione (es. `source_chat_id`) |
| `resolution_detail` | TEXT nullable | NULL | Dettaglio aggiuntivo (es. alias trovato, motivo ambiguità) |

## Messaggi pre-migration

I messaggi già in DB prima della migration ricevono:
- `acquisition_mode = 'live'` (default SQL)
- `resolved_trader_id`, `resolution_method`, `resolution_detail` = NULL

## Applicazione

```bash
# Una tantum per ogni DB (live, test, catchup)
python -c "
import sqlite3, pathlib
for db in ['db/live.db']:
    conn = sqlite3.connect(db)
    conn.executescript(pathlib.Path('db/migrations/023_runtime_v2_raw_messages.sql').read_text())
    conn.commit()
    conn.close()
    print(f'OK: {db}')
"
```

## Verifica

```bash
python -c "
import sqlite3
conn = sqlite3.connect('db/live.db')
cols = [r[1] for r in conn.execute('PRAGMA table_info(raw_messages)')]
needed = {'acquisition_mode','resolved_trader_id','resolution_method','resolution_detail'}
missing = needed - set(cols)
print('OK' if not missing else f'MANCANTI: {missing}')
"
```

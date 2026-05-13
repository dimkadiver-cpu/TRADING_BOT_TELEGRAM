# persistence — Istruzioni d'uso

## Applicare la migration 023

Prima di usare `RawMessageRepository`, le colonne runtime_v2 devono esistere nel DB.

```bash
# Applicazione manuale (una tantum per ogni DB)
python -c "
import sqlite3, pathlib
conn = sqlite3.connect('db/live.db')
conn.executescript(pathlib.Path('db/migrations/023_runtime_v2_raw_messages.sql').read_text())
conn.commit()
cols = [r[1] for r in conn.execute('PRAGMA table_info(raw_messages)')]
print('Colonne:', [c for c in cols if c in ('acquisition_mode','resolved_trader_id','resolution_method','resolution_detail')])
"
```

Output atteso:
```
Colonne: ['acquisition_mode', 'resolved_trader_id', 'resolution_method', 'resolution_detail']
```

## Usare RawMessageRepository

```python
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.intake.models import RawIngestItem

repo = RawMessageRepository(db_path="db/live.db")

# Salva (o recupera se già esistente — dedup automatico)
env = repo.save_raw(item)
print(env.raw_message_id)       # ID assegnato dal DB
print(env.acquisition_status)   # "ACQUIRED"
print(env.processing_status)    # "pending"

# Leggi per ID
env = repo.get_by_id(42)

# Aggiorna stato processing
repo.update_processing_status(42, "review")
repo.update_processing_status(42, "done")

# Marca come blacklisted (acquisition_status diventa BLACKLISTED, non cambia più)
repo.set_blacklisted(42)

# Marca come media-only skipped
repo.set_media_only_skipped(42)

# Persiste risoluzione trader
from src.runtime_v2.trader_resolution.models import ResolvedTraderContext
from datetime import datetime, timezone

ctx = ResolvedTraderContext(
    raw_message_id=42,
    trader_id="trader_a",
    method="source_chat_id",
    detail=None,
    is_ambiguous=False,
    resolved_at=datetime.now(timezone.utc),
)
repo.update_trader_resolution(42, ctx)
```

## Schema migration 023

```sql
-- db/migrations/023_runtime_v2_raw_messages.sql
ALTER TABLE raw_messages ADD COLUMN acquisition_mode TEXT NOT NULL DEFAULT 'live';
ALTER TABLE raw_messages ADD COLUMN resolved_trader_id TEXT;
ALTER TABLE raw_messages ADD COLUMN resolution_method TEXT;
ALTER TABLE raw_messages ADD COLUMN resolution_detail TEXT;

CREATE INDEX IF NOT EXISTS idx_raw_messages_resolved_trader_id
    ON raw_messages(resolved_trader_id);
```

## Note implementative

- `RawMessageRepository` usa `sqlite3` sincrono. Se il listener usa `aiosqlite`, sarà necessaria una versione asincrona in una fase successiva.
- Le colonne legacy (`raw_text`, `message_ts`, ecc.) rimangono gestite da `RawMessageStore` — non duplicare quella logica.
- Il metodo `_update_column` usa f-string per il nome colonna: è safe perché i nomi colonna sono costanti interne, mai input utente.

## Test

```bash
pytest tests/runtime_v2/test_raw_message_repository.py -v
```

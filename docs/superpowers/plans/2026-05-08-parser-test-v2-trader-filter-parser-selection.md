# Parser Test v2 — Trader Filter & Parser Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separare source_trader_id / resolved_trader_id / trader_filter / parser_profile in quattro concetti indipendenti nel sistema parser_test, eliminando il comportamento ambiguo di `--trader`.

**Architecture:** (1) Schema aggiunge `resolved_trader_id` + `resolution_method` a `raw_messages`. (2) Nuovo script `resolve_traders.py` persiste la risoluzione trader. (3) `replay_parser_v2.py` usa `resolved_trader_id` già pronto e applica `--trader-filter` e `--parser-profile` separatamente. Modulo condiviso `trader_resolution.py` evita duplicazione.

**Tech Stack:** Python 3.12+, SQLite via sqlite3, Pydantic v2, pytest, unittest.mock

---

## File Map

| File | Azione |
|---|---|
| `parser_test/db/schema.py` | Modifica — aggiungi `resolved_trader_id`, `resolution_method` |
| `parser_test/db/tests/test_schema.py` | Modifica — aggiungi test nuove colonne |
| `parser_test/scripts/trader_resolution.py` | **Nuovo** — modulo condiviso |
| `parser_test/scripts/tests/test_trader_resolution.py` | **Nuovo** — test modulo condiviso |
| `parser_test/scripts/import_history.py` | Modifica — `--default-source-trader` |
| `parser_test/scripts/tests/test_import_history_topics.py` | Modifica — test nuovo flag |
| `parser_test/scripts/resolve_traders.py` | **Nuovo** — script risoluzione |
| `parser_test/scripts/tests/test_resolve_traders.py` | **Nuovo** — test risoluzione |
| `parser_test/scripts/replay_parser_v2.py` | Modifica — refactoring completo |
| `parser_test/scripts/tests/test_replay_parser_v2.py` | **Nuovo** — test replay (sostituisce test_replay_trader_resolution.py) |
| `parser_test/scripts/tests/test_replay_trader_resolution.py` | Elimina — sostituito da test_replay_parser_v2.py |
| `parser_test/scripts/generate_parser_reports_v2.py` | Modifica — nuovi flag |

---

## Task 1: Schema — nuove colonne su raw_messages

**Files:**
- Modify: `parser_test/db/schema.py`
- Modify: `parser_test/db/tests/test_schema.py`

- [ ] **Step 1.1 — Scrivi i test fallenti**

Aggiungi in fondo a `parser_test/db/tests/test_schema.py`:

```python
def test_raw_messages_has_resolved_trader_id():
    conn = _make_memory_conn()
    apply_parser_test_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(raw_messages)")}
    assert "resolved_trader_id" in cols


def test_raw_messages_has_resolution_method():
    conn = _make_memory_conn()
    apply_parser_test_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(raw_messages)")}
    assert "resolution_method" in cols


def test_add_new_columns_to_legacy_db_without_them():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE raw_messages (
            raw_message_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            source_chat_id       TEXT    NOT NULL,
            telegram_message_id  INTEGER NOT NULL,
            message_ts           TEXT    NOT NULL,
            acquired_at          TEXT    NOT NULL,
            UNIQUE(source_chat_id, telegram_message_id)
        );
        CREATE TABLE parser_runs (
            run_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at     TEXT    NOT NULL,
            parser_system  TEXT    NOT NULL DEFAULT 'parser_v2',
            force_reparse  INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE parser_results_v2 (
            parser_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id           INTEGER NOT NULL,
            raw_message_id   INTEGER NOT NULL,
            error_status     TEXT    NOT NULL DEFAULT 'OK',
            created_at       TEXT    NOT NULL,
            UNIQUE(run_id, raw_message_id)
        );
    """)
    conn.commit()
    apply_parser_test_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(raw_messages)")}
    assert "resolved_trader_id" in cols
    assert "resolution_method" in cols
```

- [ ] **Step 1.2 — Esegui i test per verificare che falliscano**

```bash
pytest parser_test/db/tests/test_schema.py -v -k "resolved_trader_id or resolution_method or legacy_db"
```

Atteso: FAILED (colonne non esistono ancora).

- [ ] **Step 1.3 — Implementa le modifiche a schema.py**

Sostituisci il contenuto di `parser_test/db/schema.py` con:

```python
from __future__ import annotations

import sqlite3


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    col_type: str,
) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def apply_parser_test_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS raw_messages (
            raw_message_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            source_chat_id       TEXT    NOT NULL,
            source_chat_title    TEXT,
            source_type          TEXT,
            source_trader_id     TEXT,
            source_topic_id      INTEGER,
            telegram_message_id  INTEGER NOT NULL,
            reply_to_message_id  INTEGER,
            raw_text             TEXT,
            message_ts           TEXT    NOT NULL,
            acquired_at          TEXT    NOT NULL,
            acquisition_status   TEXT,
            has_media            INTEGER DEFAULT 0,
            media_kind           TEXT,
            media_mime_type      TEXT,
            media_filename       TEXT,
            media_blob           BLOB,
            UNIQUE(source_chat_id, telegram_message_id)
        );

        CREATE TABLE IF NOT EXISTS parser_runs (
            run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at      TEXT    NOT NULL,
            completed_at    TEXT,
            db_scope        TEXT,
            trader_filter   TEXT,
            parser_system   TEXT    NOT NULL DEFAULT 'parser_v2',
            parser_version  TEXT,
            force_reparse   INTEGER NOT NULL DEFAULT 0,
            notes           TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_parser_runs_started_at
            ON parser_runs(started_at);

        CREATE TABLE IF NOT EXISTS parser_results_v2 (
            parser_result_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id            INTEGER NOT NULL,
            raw_message_id    INTEGER NOT NULL,
            trader_id         TEXT,
            parser_profile    TEXT,
            primary_class     TEXT,
            parse_status      TEXT,
            primary_intent    TEXT,
            confidence        REAL,
            canonical_json    TEXT,
            warnings_json     TEXT,
            diagnostics_json  TEXT,
            error_status      TEXT NOT NULL DEFAULT 'OK',
            error_message     TEXT,
            created_at        TEXT NOT NULL,
            UNIQUE(run_id, raw_message_id),
            FOREIGN KEY(run_id)           REFERENCES parser_runs(run_id),
            FOREIGN KEY(raw_message_id)   REFERENCES raw_messages(raw_message_id)
        );

        CREATE INDEX IF NOT EXISTS idx_parser_results_v2_run
            ON parser_results_v2(run_id);
        CREATE INDEX IF NOT EXISTS idx_parser_results_v2_raw
            ON parser_results_v2(raw_message_id);
        CREATE INDEX IF NOT EXISTS idx_parser_results_v2_trader
            ON parser_results_v2(trader_id);
        CREATE INDEX IF NOT EXISTS idx_parser_results_v2_class_status
            ON parser_results_v2(primary_class, parse_status);
        CREATE INDEX IF NOT EXISTS idx_parser_results_v2_error
            ON parser_results_v2(error_status);
    """)
    _add_column_if_missing(conn, "raw_messages", "resolved_trader_id", "TEXT")
    _add_column_if_missing(conn, "raw_messages", "resolution_method", "TEXT")
    conn.commit()


__all__ = ["apply_parser_test_schema"]
```

- [ ] **Step 1.4 — Esegui tutti i test schema**

```bash
pytest parser_test/db/tests/test_schema.py -v
```

Atteso: tutti PASSED.

- [ ] **Step 1.5 — Commit**

```bash
git add parser_test/db/schema.py parser_test/db/tests/test_schema.py
git commit -m "feat(schema): add resolved_trader_id and resolution_method to raw_messages"
```

---

## Task 2: Modulo condiviso trader_resolution.py

**Files:**
- Create: `parser_test/scripts/trader_resolution.py`
- Create: `parser_test/scripts/tests/test_trader_resolution.py`
- Modify: `parser_test/scripts/replay_parser_v2.py` (rimuovi funzioni duplicate)

- [ ] **Step 2.1 — Scrivi i test fallenti**

Crea `parser_test/scripts/tests/test_trader_resolution.py`:

```python
from __future__ import annotations

from parser_test.scripts.trader_resolution import normalize_trader_id


def test_normalize_none_returns_none():
    assert normalize_trader_id(None) is None


def test_normalize_whitespace_returns_none():
    assert normalize_trader_id("   ") is None


def test_normalize_known_trader_id():
    assert normalize_trader_id("trader_a") == "trader_a"


def test_normalize_known_alias_ta():
    assert normalize_trader_id("ta") == "trader_a"


def test_normalize_unknown_falls_back_to_lowercase():
    assert normalize_trader_id("UNKNOWN_TRADER_XYZ") == "unknown_trader_xyz"


def test_normalize_mixed_case_known():
    assert normalize_trader_id("TRADER_A") == "trader_a"
```

- [ ] **Step 2.2 — Esegui i test per verificare che falliscano**

```bash
pytest parser_test/scripts/tests/test_trader_resolution.py -v
```

Atteso: FAILED (modulo non esiste).

- [ ] **Step 2.3 — Crea trader_resolution.py**

Crea `parser_test/scripts/trader_resolution.py`:

```python
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.parser_v2.profiles.registry import canonicalize_trader_v2, list_parser_v2_profiles
from src.storage.raw_messages import RawMessageStore
from src.telegram.effective_trader import EffectiveTraderResolver
from src.telegram.trader_mapping import TelegramSourceTraderMapper

_TRADER_ALIASES_PATH = PROJECT_ROOT / "config" / "trader_aliases.json"
_TELEGRAM_SOURCE_MAP_PATH = PROJECT_ROOT / "config" / "telegram_source_map.json"


def _load_json_file(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_known_trader_ids() -> set[str]:
    known = {t.strip().lower() for t in list_parser_v2_profiles()}
    payload = _load_json_file(_TRADER_ALIASES_PATH)
    aliases = payload.get("aliases", {})
    if isinstance(aliases, dict):
        for v in aliases.values():
            if isinstance(v, str):
                n = v.strip().lower()
                if n:
                    known.add(n)
    return known


def normalize_trader_id(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return canonicalize_trader_v2(stripped) or stripped.lower()


def build_trader_resolver(db_path: str) -> EffectiveTraderResolver:
    payload = _load_json_file(_TRADER_ALIASES_PATH)
    trader_aliases = payload.get("aliases", {})
    if not isinstance(trader_aliases, dict):
        trader_aliases = {}
    known_trader_ids = load_known_trader_ids()
    source_mapper = TelegramSourceTraderMapper.from_json_file(
        str(_TELEGRAM_SOURCE_MAP_PATH),
        trader_aliases={str(k): str(v) for k, v in trader_aliases.items()},
        known_trader_ids=known_trader_ids,
    )
    return EffectiveTraderResolver(
        source_mapper=source_mapper,
        raw_store=RawMessageStore(db_path=db_path),
        trader_aliases={str(k): str(v) for k, v in trader_aliases.items()},
        known_trader_ids=known_trader_ids,
    )


__all__ = ["normalize_trader_id", "load_known_trader_ids", "build_trader_resolver"]
```

- [ ] **Step 2.4 — Esegui i test**

```bash
pytest parser_test/scripts/tests/test_trader_resolution.py -v
```

Atteso: tutti PASSED.

- [ ] **Step 2.5 — Aggiorna replay_parser_v2.py: rimuovi funzioni duplicate**

Nel file `parser_test/scripts/replay_parser_v2.py`:

1. Aggiungi import in cima (dopo gli altri import da `src`):
```python
from parser_test.scripts.trader_resolution import build_trader_resolver, normalize_trader_id
```

2. Rimuovi le funzioni `_resolve_trader`, `_normalize_trader_id`, `_load_json_file`, `_load_known_trader_ids`, `_build_trader_resolver` (righe 51-108).

3. Sostituisci l'unica chiamata a `_build_trader_resolver(db_path=db_path)` con `build_trader_resolver(db_path)`.

4. Sostituisci le chiamate a `_normalize_trader_id(...)` con `normalize_trader_id(...)`.

- [ ] **Step 2.6 — Verifica che i test esistenti passino ancora**

```bash
pytest parser_test/scripts/tests/test_replay_trader_resolution.py -v
```

Atteso: PASSED (i test importano `_resolve_trader` che esiste ancora per ora).

- [ ] **Step 2.7 — Commit**

```bash
git add parser_test/scripts/trader_resolution.py parser_test/scripts/tests/test_trader_resolution.py parser_test/scripts/replay_parser_v2.py
git commit -m "refactor: extract trader_resolution shared module from replay_parser_v2"
```

---

## Task 3: import_history.py — --default-source-trader

**Files:**
- Modify: `parser_test/scripts/import_history.py`
- Modify: `parser_test/scripts/tests/test_import_history_topics.py`

- [ ] **Step 3.1 — Scrivi i test fallenti**

Aggiungi in fondo a `parser_test/scripts/tests/test_import_history_topics.py`:

```python
def test_parse_args_default_source_trader_present():
    import sys
    from unittest.mock import patch
    with patch.object(sys, "argv", ["prog", "--chat-id", "-123", "--default-source-trader", "trader_a"]):
        from parser_test.scripts.import_history import parse_args
        args = parse_args()
    assert args.default_source_trader == "trader_a"


def test_parse_args_default_source_trader_absent():
    import sys
    from unittest.mock import patch
    with patch.object(sys, "argv", ["prog", "--chat-id", "-123"]):
        from parser_test.scripts.import_history import parse_args
        args = parse_args()
    assert args.default_source_trader is None
```

- [ ] **Step 3.2 — Esegui i test per verificare che falliscano**

```bash
pytest parser_test/scripts/tests/test_import_history_topics.py -v -k "default_source_trader"
```

Atteso: FAILED (argomento non esiste).

- [ ] **Step 3.3 — Aggiungi --default-source-trader a parse_args()**

In `parser_test/scripts/import_history.py`, nella funzione `parse_args()`, aggiungi prima del `return parser.parse_args()`:

```python
    parser.add_argument(
        "--default-source-trader",
        default=None,
        help="Se fornito, valorizza source_trader_id per i messaggi importati senza trader noto.",
    )
```

- [ ] **Step 3.4 — Passa il valore a TelegramIncomingMessage**

Nella funzione `_run_import`, nella costruzione di `incoming`, cambia:

```python
source_trader_id=None,
```

con:

```python
source_trader_id=args.default_source_trader or None,
```

- [ ] **Step 3.5 — Esegui i test**

```bash
pytest parser_test/scripts/tests/test_import_history_topics.py -v
```

Atteso: tutti PASSED.

- [ ] **Step 3.6 — Commit**

```bash
git add parser_test/scripts/import_history.py parser_test/scripts/tests/test_import_history_topics.py
git commit -m "feat(import): add --default-source-trader flag to import_history.py"
```

---

## Task 4: resolve_traders.py (nuovo script)

**Files:**
- Create: `parser_test/scripts/resolve_traders.py`
- Create: `parser_test/scripts/tests/test_resolve_traders.py`

- [ ] **Step 4.1 — Scrivi i test fallenti**

Crea `parser_test/scripts/tests/test_resolve_traders.py`:

```python
from __future__ import annotations

import sqlite3
from collections import Counter
from unittest.mock import MagicMock

import pytest

from parser_test.db.schema import apply_parser_test_schema
from parser_test.scripts.resolve_traders import resolve_all
from src.telegram.effective_trader import EffectiveTraderResult


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_parser_test_schema(conn)
    return conn


def _insert_raw(
    conn: sqlite3.Connection,
    *,
    raw_message_id: int = 1,
    source_chat_id: str = "chat1",
    telegram_message_id: int = 100,
    source_trader_id: str | None = None,
    raw_text: str | None = "hello",
    reply_to_message_id: int | None = None,
    resolved_trader_id: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO raw_messages
        (raw_message_id, source_chat_id, telegram_message_id, source_trader_id,
         raw_text, reply_to_message_id, message_ts, acquired_at, resolved_trader_id)
        VALUES (?, ?, ?, ?, ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', ?)""",
        (raw_message_id, source_chat_id, telegram_message_id, source_trader_id,
         raw_text, reply_to_message_id, resolved_trader_id),
    )
    conn.commit()


def _get_resolved(conn: sqlite3.Connection, raw_message_id: int = 1) -> tuple[str | None, str | None]:
    row = conn.execute(
        "SELECT resolved_trader_id, resolution_method FROM raw_messages WHERE raw_message_id=?",
        (raw_message_id,),
    ).fetchone()
    return row[0], row[1]


def _mock_resolver(trader_id: str | None, method: str = "source_chat_id") -> MagicMock:
    r = MagicMock()
    r.resolve.return_value = EffectiveTraderResult(trader_id=trader_id, method=method)
    return r


def test_priority1_source_trader_id_used_directly():
    conn = _make_db()
    _insert_raw(conn, source_trader_id="trader_a")
    mock = _mock_resolver("trader_b")
    resolve_all(conn, resolver=mock)
    resolved, method = _get_resolved(conn)
    assert resolved == "trader_a"
    assert method == "source_trader_id"
    mock.resolve.assert_not_called()


def test_priority2_live_resolver_used_when_no_source_trader():
    conn = _make_db()
    _insert_raw(conn)
    mock = _mock_resolver("trader_a", method="content_alias")
    resolve_all(conn, resolver=mock)
    resolved, method = _get_resolved(conn)
    assert resolved == "trader_a"
    assert method == "content_alias"


def test_priority3_assume_trader_fallback():
    conn = _make_db()
    _insert_raw(conn)
    mock = _mock_resolver(None, method="unresolved")
    resolve_all(conn, resolver=mock, assume_trader="trader_a")
    resolved, method = _get_resolved(conn)
    assert resolved == "trader_a"
    assert method == "assume_trader"


def test_priority4_unresolved_when_nothing_works():
    conn = _make_db()
    _insert_raw(conn)
    mock = _mock_resolver(None)
    resolve_all(conn, resolver=mock)
    resolved, method = _get_resolved(conn)
    assert resolved is None
    assert method == "unresolved"


def test_already_resolved_skipped_by_default():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id="trader_a")
    mock = _mock_resolver("trader_b")
    counts = resolve_all(conn, resolver=mock, force_re_resolve=False)
    mock.resolve.assert_not_called()
    assert counts["skipped_already_resolved"] == 1
    resolved, _ = _get_resolved(conn)
    assert resolved == "trader_a"


def test_force_re_resolve_overwrites_existing():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id="trader_a", source_trader_id="trader_b")
    mock = _mock_resolver("ignored")
    counts = resolve_all(conn, resolver=mock, force_re_resolve=True)
    resolved, method = _get_resolved(conn)
    assert resolved == "trader_b"
    assert method == "source_trader_id"


def test_counts_returned_correctly():
    conn = _make_db()
    _insert_raw(conn, raw_message_id=1, source_trader_id="trader_a", telegram_message_id=1)
    _insert_raw(conn, raw_message_id=2, telegram_message_id=2)
    _insert_raw(conn, raw_message_id=3, telegram_message_id=3, resolved_trader_id="trader_a")
    mock = _mock_resolver(None)
    counts = resolve_all(conn, resolver=mock, assume_trader="trader_a")
    assert counts["source_trader_id"] == 1
    assert counts["assume_trader"] == 1
    assert counts["skipped_already_resolved"] == 1


def test_source_trader_id_alias_normalized():
    conn = _make_db()
    _insert_raw(conn, source_trader_id="ta")
    mock = _mock_resolver(None)
    resolve_all(conn, resolver=mock)
    resolved, _ = _get_resolved(conn)
    assert resolved == "trader_a"
```

- [ ] **Step 4.2 — Esegui i test per verificare che falliscano**

```bash
pytest parser_test/scripts/tests/test_resolve_traders.py -v
```

Atteso: FAILED (modulo non esiste).

- [ ] **Step 4.3 — Crea resolve_traders.py**

Crea `parser_test/scripts/resolve_traders.py`:

```python
"""Risolve e persiste resolved_trader_id su raw_messages."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from parser_test.db.schema import apply_parser_test_schema
from parser_test.scripts.db_paths import resolve_parser_test_db_path
from parser_test.scripts.trader_resolution import build_trader_resolver, normalize_trader_id
from src.telegram.effective_trader import EffectiveTraderContext, EffectiveTraderResolver


@dataclass(slots=True)
class _RawRow:
    raw_message_id: int
    source_chat_id: str
    telegram_message_id: int
    source_trader_id: str | None
    raw_text: str | None
    reply_to_message_id: int | None
    resolved_trader_id: str | None


def _fetch_rows(conn: sqlite3.Connection) -> list[_RawRow]:
    return [
        _RawRow(
            raw_message_id=r[0],
            source_chat_id=r[1],
            telegram_message_id=r[2],
            source_trader_id=r[3],
            raw_text=r[4],
            reply_to_message_id=r[5],
            resolved_trader_id=r[6],
        )
        for r in conn.execute(
            "SELECT raw_message_id, source_chat_id, telegram_message_id, "
            "source_trader_id, raw_text, reply_to_message_id, resolved_trader_id "
            "FROM raw_messages ORDER BY raw_message_id ASC"
        ).fetchall()
    ]


def _write(
    conn: sqlite3.Connection,
    raw_message_id: int,
    resolved_trader_id: str | None,
    resolution_method: str,
) -> None:
    conn.execute(
        "UPDATE raw_messages SET resolved_trader_id=?, resolution_method=? WHERE raw_message_id=?",
        (resolved_trader_id, resolution_method, raw_message_id),
    )


def resolve_all(
    conn: sqlite3.Connection,
    *,
    resolver: EffectiveTraderResolver | None = None,
    db_path: str | None = None,
    assume_trader: str | None = None,
    force_re_resolve: bool = False,
) -> Counter[str]:
    if resolver is None and db_path:
        resolver = build_trader_resolver(db_path)

    rows = _fetch_rows(conn)
    counts: Counter[str] = Counter()

    for raw in rows:
        if raw.resolved_trader_id is not None and not force_re_resolve:
            counts["skipped_already_resolved"] += 1
            continue

        if raw.source_trader_id:
            _write(conn, raw.raw_message_id, normalize_trader_id(raw.source_trader_id), "source_trader_id")
            counts["source_trader_id"] += 1
            continue

        inferred_id: str | None = None
        inferred_method: str = "unresolved"
        if resolver is not None:
            result = resolver.resolve(
                EffectiveTraderContext(
                    source_chat_id=raw.source_chat_id,
                    source_chat_username=None,
                    source_chat_title=None,
                    raw_text=raw.raw_text,
                    reply_to_message_id=raw.reply_to_message_id,
                )
            )
            if result.trader_id:
                inferred_id = normalize_trader_id(result.trader_id)
                inferred_method = result.method

        if inferred_id:
            _write(conn, raw.raw_message_id, inferred_id, inferred_method)
            counts[inferred_method] += 1
            continue

        if assume_trader:
            _write(conn, raw.raw_message_id, normalize_trader_id(assume_trader), "assume_trader")
            counts["assume_trader"] += 1
            continue

        _write(conn, raw.raw_message_id, None, "unresolved")
        counts["unresolved"] += 1

    conn.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Risolve resolved_trader_id su raw_messages")
    parser.add_argument("--db-path")
    parser.add_argument("--db-name")
    parser.add_argument("--db-per-chat", action="store_true")
    parser.add_argument("--assume-trader")
    parser.add_argument("--force-re-resolve", action="store_true")
    args = parser.parse_args()

    parser_test_dir = Path(__file__).resolve().parents[1]
    db_path = resolve_parser_test_db_path(
        project_root=PROJECT_ROOT,
        parser_test_dir=parser_test_dir,
        explicit_db_path=args.db_path,
        db_name=args.db_name,
        db_per_chat=args.db_per_chat,
        chat_ref=None,
    )
    conn = sqlite3.connect(db_path)
    try:
        apply_parser_test_schema(conn)
        total = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
        print(f"[resolve] {total} messaggi trovati")
        counts = resolve_all(
            conn,
            db_path=db_path,
            assume_trader=args.assume_trader,
            force_re_resolve=args.force_re_resolve,
        )
        summary = " | ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        print(f"[resolve] completato — {summary}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4.4 — Esegui i test**

```bash
pytest parser_test/scripts/tests/test_resolve_traders.py -v
```

Atteso: tutti PASSED.

- [ ] **Step 4.5 — Commit**

```bash
git add parser_test/scripts/resolve_traders.py parser_test/scripts/tests/test_resolve_traders.py
git commit -m "feat: add resolve_traders.py — separate trader resolution phase with persistence"
```

---

## Task 5: replay_parser_v2.py — refactoring completo

**Files:**
- Modify: `parser_test/scripts/replay_parser_v2.py`
- Create: `parser_test/scripts/tests/test_replay_parser_v2.py`
- Delete: `parser_test/scripts/tests/test_replay_trader_resolution.py`

- [ ] **Step 5.1 — Scrivi i test fallenti**

Crea `parser_test/scripts/tests/test_replay_parser_v2.py`:

```python
from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from parser_test.db.schema import apply_parser_test_schema
from parser_test.scripts.replay_parser_v2 import run_replay


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_parser_test_schema(conn)
    return conn


def _insert_raw(
    conn: sqlite3.Connection,
    *,
    raw_message_id: int = 1,
    source_chat_id: str = "chat1",
    telegram_message_id: int = 100,
    source_trader_id: str | None = None,
    raw_text: str = "BUY BTC/USDT",
    resolved_trader_id: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO raw_messages
        (raw_message_id, source_chat_id, telegram_message_id, source_trader_id,
         raw_text, message_ts, acquired_at, resolved_trader_id)
        VALUES (?, ?, ?, ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', ?)""",
        (raw_message_id, source_chat_id, telegram_message_id, source_trader_id,
         raw_text, resolved_trader_id),
    )
    conn.commit()


def _make_mock_canonical(trader: str = "trader_a") -> MagicMock:
    m = MagicMock()
    m.parser_profile = trader
    m.primary_class = "SIGNAL"
    m.parse_status = "PARSED"
    m.primary_intent = None
    m.confidence = 0.9
    m.model_dump_json.return_value = "{}"
    m.warnings = []
    m.diagnostics = {}
    return m


def _patch_profile_and_runtime(canonical: MagicMock):
    profile_patch = patch(
        "parser_test.scripts.replay_parser_v2.get_parser_v2_profile",
        return_value=MagicMock(),
    )
    runtime_patch = patch(
        "parser_test.scripts.replay_parser_v2.UniversalParserRuntime",
    )
    return profile_patch, runtime_patch


# --- TRADER FILTER ---

def test_trader_filter_excludes_other_trader():
    conn = _make_db()
    _insert_raw(conn, raw_message_id=1, telegram_message_id=1, resolved_trader_id="trader_a")
    _insert_raw(conn, raw_message_id=2, telegram_message_id=2, resolved_trader_id="trader_b")
    with patch("parser_test.scripts.replay_parser_v2.get_parser_v2_profile", return_value=MagicMock()):
        with patch("parser_test.scripts.replay_parser_v2.UniversalParserRuntime") as rt:
            rt.return_value.parse.return_value = _make_mock_canonical()
            run_replay(
                conn,
                trader_filter="trader_a",
                parser_profile="trader_a",
                allow_cross_profile_parse=True,
            )
    rows = conn.execute(
        "SELECT trader_id FROM parser_results_v2 WHERE error_status='OK'"
    ).fetchall()
    assert rows == [("trader_a",)]


def test_no_trader_filter_processes_all():
    conn = _make_db()
    _insert_raw(conn, raw_message_id=1, telegram_message_id=1, resolved_trader_id="trader_a")
    _insert_raw(conn, raw_message_id=2, telegram_message_id=2, resolved_trader_id="trader_a")
    with patch("parser_test.scripts.replay_parser_v2.get_parser_v2_profile", return_value=MagicMock()):
        with patch("parser_test.scripts.replay_parser_v2.UniversalParserRuntime") as rt:
            rt.return_value.parse.return_value = _make_mock_canonical()
            run_replay(conn, parser_profile="auto")
    count = conn.execute(
        "SELECT COUNT(*) FROM parser_results_v2 WHERE error_status='OK'"
    ).fetchone()[0]
    assert count == 2


# --- UNRESOLVED TRADER ---

def test_unresolved_trader_not_written_to_db():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id=None)
    run_replay(conn, assume_trader=None)
    count = conn.execute("SELECT COUNT(*) FROM parser_results_v2").fetchone()[0]
    assert count == 0


def test_assume_trader_used_when_resolved_is_null():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id=None)
    with patch("parser_test.scripts.replay_parser_v2.get_parser_v2_profile", return_value=MagicMock()):
        with patch("parser_test.scripts.replay_parser_v2.UniversalParserRuntime") as rt:
            rt.return_value.parse.return_value = _make_mock_canonical()
            run_replay(conn, assume_trader="trader_a", parser_profile="auto")
    rows = conn.execute(
        "SELECT trader_id, error_status FROM parser_results_v2"
    ).fetchall()
    assert rows == [("trader_a", "OK")]


# --- PARSER PROFILE ---

def test_parser_profile_auto_uses_resolved_trader():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id="trader_a")
    captured: list[str] = []

    def capture_profile(name: str) -> MagicMock:
        captured.append(name)
        return MagicMock()

    with patch("parser_test.scripts.replay_parser_v2.get_parser_v2_profile", side_effect=capture_profile):
        with patch("parser_test.scripts.replay_parser_v2.UniversalParserRuntime") as rt:
            rt.return_value.parse.return_value = _make_mock_canonical()
            run_replay(conn, parser_profile="auto")
    assert captured == ["trader_a"]


def test_parser_profile_fixed_uses_fixed_name():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id="trader_a")
    captured: list[str] = []

    def capture_profile(name: str) -> MagicMock:
        captured.append(name)
        return MagicMock()

    with patch("parser_test.scripts.replay_parser_v2.get_parser_v2_profile", side_effect=capture_profile):
        with patch("parser_test.scripts.replay_parser_v2.UniversalParserRuntime") as rt:
            rt.return_value.parse.return_value = _make_mock_canonical()
            run_replay(conn, parser_profile="trader_a", allow_cross_profile_parse=True)
    assert captured == ["trader_a"]


def test_unsupported_parser_profile_not_written_to_db():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id="trader_unknown_xyz")
    run_replay(conn, parser_profile="auto")
    count = conn.execute("SELECT COUNT(*) FROM parser_results_v2").fetchone()[0]
    assert count == 0


# --- CROSS-PROFILE PROTECTION ---

def test_cross_profile_blocked_by_default():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id="trader_b_unknown")
    with patch("parser_test.scripts.replay_parser_v2.get_parser_v2_profile", return_value=MagicMock()):
        run_replay(conn, parser_profile="trader_a", allow_cross_profile_parse=False)
    count = conn.execute(
        "SELECT COUNT(*) FROM parser_results_v2 WHERE error_status='OK'"
    ).fetchone()[0]
    assert count == 0


def test_cross_profile_allowed_with_flag():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id="trader_b_unknown")
    with patch("parser_test.scripts.replay_parser_v2.get_parser_v2_profile", return_value=MagicMock()):
        with patch("parser_test.scripts.replay_parser_v2.UniversalParserRuntime") as rt:
            rt.return_value.parse.return_value = _make_mock_canonical()
            run_replay(conn, parser_profile="trader_a", allow_cross_profile_parse=True)
    count = conn.execute(
        "SELECT COUNT(*) FROM parser_results_v2 WHERE error_status='OK'"
    ).fetchone()[0]
    assert count == 1


# --- DEPRECATED --trader ---

def test_resolve_trader_filter_from_args_deprecated_warning(capsys):
    import argparse
    from parser_test.scripts.replay_parser_v2 import _resolve_trader_filter_from_args
    args = argparse.Namespace(trader="trader_a", trader_filter=None)
    result = _resolve_trader_filter_from_args(args)
    assert result == "trader_a"
    captured = capsys.readouterr()
    assert "--trader is deprecated" in captured.err


def test_resolve_trader_filter_from_args_trader_filter_takes_precedence(capsys):
    import argparse
    from parser_test.scripts.replay_parser_v2 import _resolve_trader_filter_from_args
    args = argparse.Namespace(trader="trader_a", trader_filter="trader_b")
    result = _resolve_trader_filter_from_args(args)
    assert result == "trader_b"


def test_resolve_trader_filter_from_args_no_trader_no_warning(capsys):
    import argparse
    from parser_test.scripts.replay_parser_v2 import _resolve_trader_filter_from_args
    args = argparse.Namespace(trader=None, trader_filter="trader_a")
    result = _resolve_trader_filter_from_args(args)
    assert result == "trader_a"
    captured = capsys.readouterr()
    assert "deprecated" not in captured.err


# --- AUDIT CSV ---

def test_audit_csv_written_when_dir_provided(tmp_path: Path):
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id=None)
    run_replay(conn, audit_csv_dir=tmp_path)
    csv_files = list(tmp_path.glob("audit_run_*.csv"))
    assert len(csv_files) == 1
    content = csv_files[0].read_text(encoding="utf-8-sig")
    assert "UNRESOLVED_TRADER" in content


def test_audit_csv_not_written_when_dir_none():
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id=None)
    run_replay(conn, audit_csv_dir=None)


def test_audit_csv_contains_expected_columns(tmp_path: Path):
    conn = _make_db()
    _insert_raw(conn, resolved_trader_id=None)
    run_replay(conn, audit_csv_dir=tmp_path)
    csv_file = list(tmp_path.glob("audit_run_*.csv"))[0]
    with csv_file.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames or []
    expected = {
        "raw_message_id", "source_trader_id", "resolved_trader_id", "parser_profile",
        "error_status", "error_message", "source_chat_id", "source_topic_id",
        "telegram_message_id", "message_ts", "text_preview",
    }
    assert expected.issubset(set(columns))
```

- [ ] **Step 5.2 — Esegui i test per verificare che falliscano**

```bash
pytest parser_test/scripts/tests/test_replay_parser_v2.py -v
```

Atteso: varie FAILED (funzioni mancanti, signature errata).

- [ ] **Step 5.3 — Riscrivi replay_parser_v2.py**

Sostituisci l'intero contenuto di `parser_test/scripts/replay_parser_v2.py` con:

```python
"""Replay parser_v2 su messaggi raw salvati nel DB parser_test."""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from parser_test.db.schema import apply_parser_test_schema
from parser_test.scripts.db_paths import resolve_parser_test_db_path
from parser_test.scripts.trader_resolution import build_trader_resolver, normalize_trader_id
from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.core.runtime import UniversalParserRuntime
from src.parser_v2.profiles.registry import get_parser_v2_profile
from src.storage.parser_results_v2 import ParserResultV2Record, ParserResultV2Store
from src.storage.parser_runs import ParserRunStore
from src.storage.raw_messages import RawMessageStore
from src.telegram.effective_trader import EffectiveTraderContext, EffectiveTraderResolver
from src.telegram.trader_mapping import TelegramSourceTraderMapper

_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/\d+", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]{2,64})")

_TRADER_DEPRECATED_MSG = (
    "[warning] --trader is deprecated; use --trader-filter for message selection "
    "or --assume-trader for fallback."
)


@dataclass(slots=True)
class _RawRow:
    raw_message_id: int
    source_chat_id: str
    telegram_message_id: int
    source_trader_id: str | None
    raw_text: str | None
    reply_to_message_id: int | None
    source_topic_id: int | None
    message_ts: str
    resolved_trader_id: str | None


@dataclass(slots=True)
class _AuditRow:
    raw_message_id: int
    source_trader_id: str | None
    resolved_trader_id: str | None
    parser_profile: str | None
    error_status: str
    error_message: str | None
    source_chat_id: str
    source_topic_id: int | None
    telegram_message_id: int
    message_ts: str
    text_preview: str | None


def _resolve_trader_filter_from_args(args: argparse.Namespace) -> str | None:
    if args.trader is not None:
        print(_TRADER_DEPRECATED_MSG, file=sys.stderr)
        return args.trader_filter if args.trader_filter is not None else args.trader
    return args.trader_filter


def _resolve_inferred_trader(
    resolver: EffectiveTraderResolver,
    raw: _RawRow,
) -> str | None:
    result = resolver.resolve(
        EffectiveTraderContext(
            source_chat_id=raw.source_chat_id,
            source_chat_username=None,
            source_chat_title=None,
            raw_text=raw.raw_text,
            reply_to_message_id=raw.reply_to_message_id,
        )
    )
    return result.trader_id


def _extract_telegram_links(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _LINK_RE.finditer(text):
        v = m.group(0)
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _extract_hashtags(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _HASHTAG_RE.finditer(text):
        v = m.group(1)
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_raw_rows(
    conn: sqlite3.Connection,
    *,
    chat_id: str | None,
    from_date: str | None,
    to_date: str | None,
    limit: int | None,
    only_unparsed: bool,
) -> list[_RawRow]:
    query = (
        "SELECT raw_message_id, source_chat_id, telegram_message_id, "
        "source_trader_id, raw_text, reply_to_message_id, source_topic_id, "
        "message_ts, resolved_trader_id "
        "FROM raw_messages WHERE 1=1"
    )
    params: list[object] = []
    if chat_id is not None:
        query += " AND source_chat_id = ?"
        params.append(chat_id)
    if from_date is not None:
        query += " AND message_ts >= ?"
        params.append(from_date)
    if to_date is not None:
        query += " AND message_ts <= ?"
        params.append(to_date)
    if only_unparsed:
        query += (
            " AND raw_message_id NOT IN "
            "(SELECT raw_message_id FROM parser_results_v2 WHERE error_status = 'OK')"
        )
    query += " ORDER BY message_ts ASC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    return [
        _RawRow(
            raw_message_id=r[0],
            source_chat_id=r[1],
            telegram_message_id=r[2],
            source_trader_id=r[3],
            raw_text=r[4],
            reply_to_message_id=r[5],
            source_topic_id=r[6],
            message_ts=r[7],
            resolved_trader_id=r[8],
        )
        for r in conn.execute(query, params).fetchall()
    ]


def _build_audit_row(
    raw: _RawRow,
    resolved_trader_id: str | None,
    parser_profile: str | None,
    error_status: str,
    error_message: str | None,
) -> _AuditRow:
    return _AuditRow(
        raw_message_id=raw.raw_message_id,
        source_trader_id=raw.source_trader_id,
        resolved_trader_id=resolved_trader_id,
        parser_profile=parser_profile,
        error_status=error_status,
        error_message=error_message,
        source_chat_id=raw.source_chat_id,
        source_topic_id=raw.source_topic_id,
        telegram_message_id=raw.telegram_message_id,
        message_ts=raw.message_ts,
        text_preview=(raw.raw_text or "")[:120] if raw.raw_text else None,
    )


def _write_audit_csv(rows: list[_AuditRow], path: Path) -> None:
    columns = [
        "raw_message_id", "source_trader_id", "resolved_trader_id", "parser_profile",
        "error_status", "error_message", "source_chat_id", "source_topic_id",
        "telegram_message_id", "message_ts", "text_preview",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "raw_message_id": row.raw_message_id,
                "source_trader_id": row.source_trader_id or "",
                "resolved_trader_id": row.resolved_trader_id or "",
                "parser_profile": row.parser_profile or "",
                "error_status": row.error_status,
                "error_message": row.error_message or "",
                "source_chat_id": row.source_chat_id,
                "source_topic_id": row.source_topic_id if row.source_topic_id is not None else "",
                "telegram_message_id": row.telegram_message_id,
                "message_ts": row.message_ts,
                "text_preview": row.text_preview or "",
            })


def run_replay(
    conn: sqlite3.Connection,
    *,
    db_path: str | None = None,
    trader_filter: str | None = None,
    assume_trader: str | None = None,
    parser_system: str = "parser_v2",
    parser_profile: str = "auto",
    allow_cross_profile_parse: bool = False,
    audit_csv_dir: Path | None = None,
    chat_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int | None = None,
    only_unparsed: bool = False,
    force_reparse: bool = False,
    show_samples: int = 0,
    trader_resolver: EffectiveTraderResolver | None = None,
) -> int:
    apply_parser_test_schema(conn)
    run_store = ParserRunStore(conn)
    result_store = ParserResultV2Store(conn)
    if trader_resolver is None and db_path:
        trader_resolver = build_trader_resolver(db_path)

    run_id = run_store.create_run(
        trader_filter=trader_filter,
        force_reparse=force_reparse,
    )
    print(f"[replay] run_id={run_id} avviato", flush=True)

    rows = _fetch_raw_rows(
        conn,
        chat_id=chat_id,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        only_unparsed=only_unparsed,
    )
    print(f"[replay] {len(rows)} messaggi da processare", flush=True)

    runtime = UniversalParserRuntime()
    counts: Counter[str] = Counter()
    samples: list[str] = []
    audit_rows: list[_AuditRow] | None = [] if audit_csv_dir is not None else None

    for raw in rows:
        effective_trader = normalize_trader_id(raw.resolved_trader_id)
        if effective_trader is None and trader_resolver is not None:
            inferred = _resolve_inferred_trader(trader_resolver, raw)
            effective_trader = normalize_trader_id(inferred)
        if effective_trader is None and assume_trader is not None:
            effective_trader = normalize_trader_id(assume_trader)

        if effective_trader is None:
            counts["UNRESOLVED_TRADER"] += 1
            if audit_rows is not None:
                audit_rows.append(_build_audit_row(raw, None, None, "UNRESOLVED_TRADER", None))
            continue

        if trader_filter is not None and effective_trader != trader_filter:
            counts["SKIPPED_TRADER_FILTER"] += 1
            if audit_rows is not None:
                audit_rows.append(_build_audit_row(
                    raw, effective_trader, None, "SKIPPED_TRADER_FILTER",
                    f"filter={trader_filter}",
                ))
            continue

        effective_profile = effective_trader if parser_profile == "auto" else parser_profile

        try:
            profile = get_parser_v2_profile(effective_profile)
        except KeyError:
            counts["SKIPPED_UNSUPPORTED_PARSER_PROFILE"] += 1
            if audit_rows is not None:
                audit_rows.append(_build_audit_row(
                    raw, effective_trader, effective_profile,
                    "SKIPPED_UNSUPPORTED_PARSER_PROFILE",
                    f"profile={effective_profile}",
                ))
            continue

        if (
            parser_profile != "auto"
            and effective_profile != effective_trader
            and not allow_cross_profile_parse
        ):
            counts["SKIPPED_TRADER_FILTER"] += 1
            if audit_rows is not None:
                audit_rows.append(_build_audit_row(
                    raw, effective_trader, effective_profile,
                    "SKIPPED_TRADER_FILTER",
                    f"cross_profile:profile={effective_profile},trader={effective_trader}",
                ))
            continue

        try:
            text = raw.raw_text or ""
            raw_ctx = RawContext(
                raw_text=text,
                message_id=raw.telegram_message_id,
                reply_to_message_id=raw.reply_to_message_id,
                source_chat_id=raw.source_chat_id,
                source_topic_id=raw.source_topic_id,
                extracted_links=_extract_telegram_links(text),
                hashtags=_extract_hashtags(text),
            )
            context = ParserContext(
                raw_context=raw_ctx,
                message_id=raw.telegram_message_id,
                reply_to_message_id=raw.reply_to_message_id,
                source_chat_id=raw.source_chat_id,
                source_topic_id=raw.source_topic_id,
            )
            canonical = runtime.parse(text, context, profile)
            result_store.insert_result(
                ParserResultV2Record(
                    run_id=run_id,
                    raw_message_id=raw.raw_message_id,
                    trader_id=effective_trader,
                    parser_profile=canonical.parser_profile,
                    primary_class=canonical.primary_class,
                    parse_status=canonical.parse_status,
                    primary_intent=canonical.primary_intent,
                    confidence=canonical.confidence,
                    canonical_json=canonical.model_dump_json(exclude_none=True),
                    warnings_json=json.dumps(canonical.warnings) if canonical.warnings else None,
                    diagnostics_json=json.dumps(canonical.diagnostics) if canonical.diagnostics else None,
                    error_status="OK",
                    error_message=None,
                    created_at=_now_iso(),
                )
            )
            counts[canonical.parse_status] += 1
            if show_samples and len(samples) < show_samples:
                samples.append(f"  [{canonical.primary_class}/{canonical.parse_status}] {text[:80]}")
        except Exception as exc:
            result_store.insert_result(
                ParserResultV2Record(
                    run_id=run_id,
                    raw_message_id=raw.raw_message_id,
                    trader_id=effective_trader,
                    parser_profile=None,
                    primary_class=None,
                    parse_status=None,
                    primary_intent=None,
                    confidence=None,
                    canonical_json=None,
                    warnings_json=None,
                    diagnostics_json=None,
                    error_status="PARSER_ERROR",
                    error_message=repr(exc)[:500],
                    created_at=_now_iso(),
                )
            )
            counts["PARSER_ERROR"] += 1
            if audit_rows is not None:
                audit_rows.append(_build_audit_row(
                    raw, effective_trader, effective_profile,
                    "PARSER_ERROR", repr(exc)[:200],
                ))

    run_store.complete_run(run_id)

    if audit_rows is not None and audit_csv_dir is not None:
        audit_csv_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_csv_dir / f"audit_run_{run_id}.csv"
        _write_audit_csv(audit_rows, audit_path)
        print(f"[replay] audit CSV: {audit_path}", flush=True)

    total = sum(counts.values())
    print(f"\n[replay] run={run_id} completato — {total} messaggi")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")
    if samples:
        print("\n[replay] campioni:")
        for s in samples:
            print(s)

    return run_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay parser_v2 su raw_messages")
    parser.add_argument("--db-path")
    parser.add_argument("--db-name")
    parser.add_argument("--db-per-chat", action="store_true")
    parser.add_argument("--chat-id")
    parser.add_argument("--trader-filter", dest="trader_filter")
    parser.add_argument("--message-trader-filter", dest="trader_filter")
    parser.add_argument("--assume-trader")
    parser.add_argument("--parser-system", default="parser_v2")
    parser.add_argument("--parser-profile", default="auto")
    parser.add_argument("--allow-cross-profile-parse", action="store_true")
    parser.add_argument("--audit-csv", action="store_true")
    parser.add_argument("--trader")
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only-unparsed", action="store_true")
    parser.add_argument("--force-reparse", action="store_true")
    parser.add_argument("--show-samples", type=int, default=0)
    args = parser.parse_args()

    trader_filter = _resolve_trader_filter_from_args(args)

    parser_test_dir = Path(__file__).resolve().parents[1]
    db_path = resolve_parser_test_db_path(
        project_root=PROJECT_ROOT,
        parser_test_dir=parser_test_dir,
        explicit_db_path=args.db_path,
        db_name=args.db_name,
        db_per_chat=args.db_per_chat,
        chat_ref=args.chat_id,
    )
    audit_csv_dir = Path(db_path).parent if args.audit_csv else None
    conn = sqlite3.connect(db_path)
    try:
        run_replay(
            conn,
            db_path=db_path,
            trader_filter=trader_filter,
            assume_trader=args.assume_trader,
            parser_system=args.parser_system,
            parser_profile=args.parser_profile,
            allow_cross_profile_parse=args.allow_cross_profile_parse,
            audit_csv_dir=audit_csv_dir,
            chat_id=args.chat_id,
            from_date=args.from_date,
            to_date=args.to_date,
            limit=args.limit,
            only_unparsed=args.only_unparsed,
            force_reparse=args.force_reparse,
            show_samples=args.show_samples,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5.4 — Elimina il vecchio file di test**

```bash
git rm parser_test/scripts/tests/test_replay_trader_resolution.py
```

- [ ] **Step 5.5 — Esegui i nuovi test**

```bash
pytest parser_test/scripts/tests/test_replay_parser_v2.py -v
```

Atteso: tutti PASSED.

- [ ] **Step 5.6 — Esegui la suite completa per verificare nessuna regressione**

```bash
pytest parser_test/ -v --tb=short
```

Atteso: tutti PASSED (o stessi fallimenti di prima, nessuno nuovo).

- [ ] **Step 5.7 — Commit**

```bash
git add parser_test/scripts/replay_parser_v2.py parser_test/scripts/tests/test_replay_parser_v2.py
git commit -m "feat(replay): refactor replay_parser_v2 with trader-filter, parser-profile, audit-csv, deprecate --trader"
```

---

## Task 6: generate_parser_reports_v2.py — aggiorna flag

**Files:**
- Modify: `parser_test/scripts/generate_parser_reports_v2.py`

- [ ] **Step 6.1 — Sostituisci il contenuto di generate_parser_reports_v2.py**

```python
"""Replay + generazione CSV per parser_v2."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from parser_test.db.schema import apply_parser_test_schema
from parser_test.reporting.report_export_v2 import export_all
from parser_test.scripts.db_paths import resolve_parser_test_db_path
from parser_test.scripts.replay_parser_v2 import _resolve_trader_filter_from_args, run_replay
from src.storage.parser_runs import ParserRunStore

_DEFAULT_REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports_v2"


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay parser_v2 + genera CSV")
    parser.add_argument("--db-path")
    parser.add_argument("--db-name")
    parser.add_argument("--db-per-chat", action="store_true")
    parser.add_argument("--chat-id")
    parser.add_argument("--trader-filter", dest="trader_filter")
    parser.add_argument("--message-trader-filter", dest="trader_filter")
    parser.add_argument("--assume-trader")
    parser.add_argument("--parser-system", default="parser_v2")
    parser.add_argument("--parser-profile", default="auto")
    parser.add_argument("--allow-cross-profile-parse", action="store_true")
    parser.add_argument("--audit-csv", action="store_true")
    parser.add_argument("--trader")
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force-reparse", action="store_true")
    parser.add_argument("--skip-replay", action="store_true")
    parser.add_argument("--run", default="latest", help="'latest' oppure run_id numerico")
    parser.add_argument("--reports-dir", default=str(_DEFAULT_REPORTS_DIR))
    args = parser.parse_args()

    trader_filter = _resolve_trader_filter_from_args(args)

    parser_test_dir = Path(__file__).resolve().parents[1]
    db_path = resolve_parser_test_db_path(
        project_root=PROJECT_ROOT,
        parser_test_dir=parser_test_dir,
        explicit_db_path=args.db_path,
        db_name=args.db_name,
        db_per_chat=args.db_per_chat,
        chat_ref=args.chat_id,
    )
    reports_dir = Path(args.reports_dir)
    conn = sqlite3.connect(db_path)
    apply_parser_test_schema(conn)

    try:
        if not args.skip_replay:
            audit_csv_dir = reports_dir if args.audit_csv else None
            run_id = run_replay(
                conn,
                db_path=db_path,
                trader_filter=trader_filter,
                assume_trader=args.assume_trader,
                parser_system=args.parser_system,
                parser_profile=args.parser_profile,
                allow_cross_profile_parse=args.allow_cross_profile_parse,
                audit_csv_dir=audit_csv_dir,
                chat_id=args.chat_id,
                from_date=args.from_date,
                to_date=args.to_date,
                limit=args.limit,
                force_reparse=args.force_reparse,
            )
        else:
            if args.run == "latest":
                record = ParserRunStore(conn).get_latest_run(trader_filter=trader_filter)
                if record is None:
                    print("[generate] Nessun run trovato. Esegui prima senza --skip-replay.")
                    sys.exit(1)
                run_id = record.run_id
            else:
                run_id = int(args.run)
            print(f"[generate] Uso run_id={run_id}")

        print(f"\n[generate] Produco CSV in {reports_dir}/run_{run_id}/")
        generated = export_all(conn, run_id, trader_filter, reports_dir)
        print(f"\n[generate] {len(generated)} file generati.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6.2 — Esegui la suite completa**

```bash
pytest parser_test/ -v --tb=short
```

Atteso: tutti PASSED.

- [ ] **Step 6.3 — Commit**

```bash
git add parser_test/scripts/generate_parser_reports_v2.py
git commit -m "feat(generate): update generate_parser_reports_v2 with new trader-filter and parser-profile flags"
```

---

## Verifica finale

- [ ] **Esegui tutta la suite una volta sola**

```bash
pytest parser_test/ -v
```

Atteso: tutti PASSED.

- [ ] **Smoke test manuale — controlla il CLI replay**

```bash
python parser_test/scripts/replay_parser_v2.py --help
python parser_test/scripts/resolve_traders.py --help
```

Atteso: entrambi mostrano i nuovi flag senza errori.

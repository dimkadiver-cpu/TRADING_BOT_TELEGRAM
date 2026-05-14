# parser_test v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace parser_test v1 (agganciato a `src/parser`) con un nuovo sistema autonomo agganciato a `src/parser_v2`, con DB schema centralizzato, storage layer dedicato e pipeline CSV riscritta.

**Architecture:** `parser_test/db/schema.py` centralizza la creazione tabelle SQLite; `src/storage/parser_runs.py` + `parser_results_v2.py` gestiscono la persistenza ricevendo `sqlite3.Connection` dall'esterno; `replay_parser_v2.py` guida il loop di parsing; `report_export_v2.py` produce i CSV leggendo da `canonical_json`.

**Tech Stack:** Python 3.12, sqlite3 stdlib, Pydantic v2 (solo per produzione `CanonicalMessage`; i flattener usano `json.loads`), pytest

---

## File map

**Creati:**
- `src/parser_v2/profiles/registry.py`
- `parser_test/db/__init__.py`
- `parser_test/db/schema.py`
- `parser_test/db/tests/__init__.py`
- `parser_test/db/tests/test_schema.py`
- `src/storage/parser_runs.py`
- `src/storage/parser_results_v2.py`
- `tests/parser_v2/__init__.py`
- `tests/parser_v2/test_registry.py`
- `tests/storage/test_parser_runs.py`
- `tests/storage/test_parser_results_v2.py`
- `parser_test/scripts/replay_parser_v2.py`
- `parser_test/scripts/tests/test_replay_trader_resolution.py`
- `parser_test/reporting/report_schema_v2.py`
- `parser_test/reporting/flatteners_v2.py`
- `parser_test/reporting/tests/__init__.py`
- `parser_test/reporting/tests/test_flatteners_v2.py`
- `parser_test/reporting/report_export_v2.py`
- `parser_test/scripts/generate_parser_reports_v2.py`

**Modificati:**
- `parser_test/scripts/watch_parser.py`
- `parser_test/reporting/__init__.py`
- `parser_test/README.md`

**Eliminati:**
- `parser_test/scripts/replay_parser.py`
- `parser_test/scripts/generate_parser_reports.py`
- `parser_test/scripts/export_reports_csv.py`
- `parser_test/scripts/audit_canonical_v1.py`
- `parser_test/reporting/flatteners.py`
- `parser_test/reporting/flatteners_v1.py`
- `parser_test/reporting/report_schema.py`
- `parser_test/reporting/report_schema_v1.py`
- `parser_test/reporting/report_export.py`
- `parser_test/reporting/report_export_v1.py`
- `parser_test/reporting/canonical_v1_audit.py`
- `parser_test/tests/test_report_export.py`
- `parser_test/tests/test_parser_dispatcher_modes.py`
- `parser_test/tests/test_canonical_schema_alignment.py`
- `parser_test/tests/test_parse_result_normalized.py`
- `parser_test/tests/test_ta_profile_refactor.py`
- `parser_test/tests/test_pipeline_semantic_consistency.py`
- `parser_test/scripts/tests/test_replay_parser_phase3.py`
- `parser_test/scripts/tests/test_replay_parser_parsed_messages.py`
- `parser_test/scripts/tests/test_generate_parser_reports.py`
- `parser_test/scripts/tests/test_audit_canonical_v1.py`

---

## Task 1: Elimina moduli v1

**Files:** eliminate only

- [ ] **Step 1: git rm script e reporting v1**

```bash
git rm parser_test/scripts/replay_parser.py
git rm parser_test/scripts/generate_parser_reports.py
git rm parser_test/scripts/export_reports_csv.py
git rm parser_test/scripts/audit_canonical_v1.py
git rm parser_test/reporting/flatteners.py
git rm parser_test/reporting/flatteners_v1.py
git rm parser_test/reporting/report_schema.py
git rm parser_test/reporting/report_schema_v1.py
git rm parser_test/reporting/report_export.py
git rm parser_test/reporting/report_export_v1.py
git rm parser_test/reporting/canonical_v1_audit.py
```

- [ ] **Step 2: git rm test obsoleti**

```bash
git rm parser_test/tests/test_report_export.py
git rm parser_test/tests/test_parser_dispatcher_modes.py
git rm parser_test/tests/test_canonical_schema_alignment.py
git rm parser_test/tests/test_parse_result_normalized.py
git rm parser_test/tests/test_ta_profile_refactor.py
git rm parser_test/tests/test_pipeline_semantic_consistency.py
git rm parser_test/scripts/tests/test_replay_parser_phase3.py
git rm parser_test/scripts/tests/test_replay_parser_parsed_messages.py
git rm parser_test/scripts/tests/test_generate_parser_reports.py
git rm parser_test/scripts/tests/test_audit_canonical_v1.py
```

- [ ] **Step 3: Verifica**

```bash
ls parser_test/reporting/
```

Expected: solo `__init__.py` (e `report_columns_map.md`, `report_structure_proposal.md` se presenti — lasciarli).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(parser-test): rimuovi moduli e test v1"
```

---

## Task 2: `src/parser_v2/profiles/registry.py`

**Files:**
- Create: `src/parser_v2/profiles/registry.py`
- Create: `tests/parser_v2/__init__.py`
- Create: `tests/parser_v2/test_registry.py`

- [ ] **Step 1: Crea directory test e `__init__.py`**

```bash
mkdir -p tests/parser_v2
echo "" > tests/parser_v2/__init__.py
```

- [ ] **Step 2: Scrivi il test**

`tests/parser_v2/test_registry.py`:

```python
from __future__ import annotations

import pytest

from src.parser_v2.profiles.registry import (
    canonicalize_trader_v2,
    get_parser_v2_profile,
    list_parser_v2_profiles,
)


def test_get_trader_a_by_canonical_name():
    profile = get_parser_v2_profile("trader_a")
    assert profile.trader_code == "trader_a"


def test_get_trader_a_by_alias_ta():
    profile = get_parser_v2_profile("ta")
    assert profile.trader_code == "trader_a"


def test_get_trader_a_by_alias_a():
    profile = get_parser_v2_profile("a")
    assert profile.trader_code == "trader_a"


def test_get_unknown_trader_raises_key_error():
    with pytest.raises(KeyError, match="unknown_xyz"):
        get_parser_v2_profile("unknown_xyz")


def test_list_profiles_contains_canonical_name():
    profiles = list_parser_v2_profiles()
    assert "trader_a" in profiles


def test_list_profiles_no_aliases():
    profiles = list_parser_v2_profiles()
    assert "ta" not in profiles
    assert "a" not in profiles


def test_canonicalize_known_alias():
    assert canonicalize_trader_v2("ta") == "trader_a"


def test_canonicalize_canonical_name():
    assert canonicalize_trader_v2("trader_a") == "trader_a"


def test_canonicalize_case_insensitive():
    assert canonicalize_trader_v2("TRADER_A") == "trader_a"
    assert canonicalize_trader_v2("TA") == "trader_a"


def test_canonicalize_unknown_returns_none():
    assert canonicalize_trader_v2("unknown") is None


def test_canonicalize_none_returns_none():
    assert canonicalize_trader_v2(None) is None
```

- [ ] **Step 3: Verifica test fail**

```bash
pytest tests/parser_v2/test_registry.py -v
```

Expected: `ModuleNotFoundError` o `ImportError` — `registry` non esiste ancora.

- [ ] **Step 4: Implementa `registry.py`**

`src/parser_v2/profiles/registry.py`:

```python
from __future__ import annotations

from src.parser_v2.profiles.trader_a.profile import TraderAProfile

_PROFILE_FACTORIES: dict[str, type] = {
    "trader_a": TraderAProfile,
    "ta": TraderAProfile,
    "a": TraderAProfile,
}

_CANONICAL_NAMES: frozenset[str] = frozenset({"trader_a"})


def canonicalize_trader_v2(value: str | None) -> str | None:
    if value is None:
        return None
    key = value.strip().lower()
    factory = _PROFILE_FACTORIES.get(key)
    if factory is None:
        return None
    for canonical in _CANONICAL_NAMES:
        if _PROFILE_FACTORIES.get(canonical) is factory:
            return canonical
    return None


def get_parser_v2_profile(trader_id: str):
    key = trader_id.strip().lower()
    factory = _PROFILE_FACTORIES.get(key)
    if factory is None:
        raise KeyError(f"Unknown parser_v2 trader: {trader_id!r}")
    return factory()


def list_parser_v2_profiles() -> list[str]:
    return sorted(_CANONICAL_NAMES)


__all__ = ["canonicalize_trader_v2", "get_parser_v2_profile", "list_parser_v2_profiles"]
```

- [ ] **Step 5: Verifica test pass**

```bash
pytest tests/parser_v2/test_registry.py -v
```

Expected: tutti PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/parser_v2/profiles/registry.py tests/parser_v2/
git commit -m "feat(parser-v2): aggiungi registry profili trader"
```

---

## Task 3: `parser_test/db/schema.py`

**Files:**
- Create: `parser_test/db/__init__.py`
- Create: `parser_test/db/schema.py`
- Create: `parser_test/db/tests/__init__.py`
- Create: `parser_test/db/tests/test_schema.py`

- [ ] **Step 1: Crea directory**

```bash
mkdir -p parser_test/db/tests
echo "" > parser_test/db/__init__.py
echo "" > parser_test/db/tests/__init__.py
```

- [ ] **Step 2: Scrivi il test**

`parser_test/db/tests/test_schema.py`:

```python
from __future__ import annotations

import sqlite3

import pytest

from parser_test.db.schema import apply_parser_test_schema


def _make_memory_conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


def test_apply_schema_creates_raw_messages():
    conn = _make_memory_conn()
    apply_parser_test_schema(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "raw_messages" in tables


def test_apply_schema_creates_parser_runs():
    conn = _make_memory_conn()
    apply_parser_test_schema(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "parser_runs" in tables


def test_apply_schema_creates_parser_results_v2():
    conn = _make_memory_conn()
    apply_parser_test_schema(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "parser_results_v2" in tables


def test_apply_schema_is_idempotent():
    conn = _make_memory_conn()
    apply_parser_test_schema(conn)
    apply_parser_test_schema(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "parser_results_v2" in tables


def test_raw_messages_has_source_topic_id_column():
    conn = _make_memory_conn()
    apply_parser_test_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(raw_messages)")}
    assert "source_topic_id" in cols


def test_parser_results_v2_unique_run_raw_message():
    conn = _make_memory_conn()
    apply_parser_test_schema(conn)
    conn.execute(
        "INSERT INTO raw_messages (source_chat_id, telegram_message_id, message_ts, acquired_at) VALUES ('c1', 1, '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO parser_runs (started_at, parser_system) VALUES ('2026-01-01', 'parser_v2')"
    )
    conn.execute(
        "INSERT INTO parser_results_v2 (run_id, raw_message_id, error_status, created_at) VALUES (1, 1, 'OK', '2026-01-01')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO parser_results_v2 (run_id, raw_message_id, error_status, created_at) VALUES (1, 1, 'OK', '2026-01-01')"
        )
```

- [ ] **Step 3: Verifica test fail**

```bash
pytest parser_test/db/tests/test_schema.py -v
```

Expected: `ModuleNotFoundError` — `schema` non esiste ancora.

- [ ] **Step 4: Implementa `schema.py`**

`parser_test/db/schema.py`:

```python
from __future__ import annotations

import sqlite3


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
    conn.commit()


__all__ = ["apply_parser_test_schema"]
```

- [ ] **Step 5: Verifica test pass**

```bash
pytest parser_test/db/tests/test_schema.py -v
```

Expected: tutti PASSED.

- [ ] **Step 6: Commit**

```bash
git add parser_test/db/
git commit -m "feat(parser-test): aggiungi schema DB autonomo"
```

---

## Task 4: `src/storage/parser_runs.py`

**Files:**
- Create: `src/storage/parser_runs.py`
- Create: `tests/storage/test_parser_runs.py`

- [ ] **Step 1: Scrivi il test**

`tests/storage/test_parser_runs.py`:

```python
from __future__ import annotations

import sqlite3

from parser_test.db.schema import apply_parser_test_schema
from src.storage.parser_runs import ParserRunRecord, ParserRunStore


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_parser_test_schema(conn)
    return conn


def test_create_run_returns_positive_int():
    store = ParserRunStore(_conn())
    run_id = store.create_run()
    assert isinstance(run_id, int)
    assert run_id >= 1


def test_create_run_stores_parser_system():
    conn = _conn()
    store = ParserRunStore(conn)
    run_id = store.create_run(parser_system="parser_v2")
    row = conn.execute("SELECT parser_system FROM parser_runs WHERE run_id=?", (run_id,)).fetchone()
    assert row[0] == "parser_v2"


def test_complete_run_sets_completed_at():
    conn = _conn()
    store = ParserRunStore(conn)
    run_id = store.create_run()
    store.complete_run(run_id)
    row = conn.execute("SELECT completed_at FROM parser_runs WHERE run_id=?", (run_id,)).fetchone()
    assert row[0] is not None


def test_get_latest_run_returns_most_recent_completed():
    conn = _conn()
    store = ParserRunStore(conn)
    run_id1 = store.create_run(trader_filter="trader_a")
    store.complete_run(run_id1)
    run_id2 = store.create_run(trader_filter="trader_a")
    store.complete_run(run_id2)
    latest = store.get_latest_run(trader_filter="trader_a")
    assert latest is not None
    assert latest.run_id == run_id2


def test_get_latest_run_returns_none_when_no_completed():
    conn = _conn()
    store = ParserRunStore(conn)
    store.create_run()  # not completed
    result = store.get_latest_run()
    assert result is None


def test_get_latest_run_filters_by_trader():
    conn = _conn()
    store = ParserRunStore(conn)
    run_a = store.create_run(trader_filter="trader_a")
    store.complete_run(run_a)
    run_b = store.create_run(trader_filter="trader_b")
    store.complete_run(run_b)
    latest_a = store.get_latest_run(trader_filter="trader_a")
    assert latest_a is not None
    assert latest_a.run_id == run_a


def test_get_latest_run_returns_parser_run_record():
    conn = _conn()
    store = ParserRunStore(conn)
    run_id = store.create_run(trader_filter="trader_a", force_reparse=True)
    store.complete_run(run_id)
    record = store.get_latest_run(trader_filter="trader_a")
    assert isinstance(record, ParserRunRecord)
    assert record.force_reparse is True
    assert record.trader_filter == "trader_a"
```

- [ ] **Step 2: Verifica test fail**

```bash
pytest tests/storage/test_parser_runs.py -v
```

Expected: `ModuleNotFoundError` — `parser_runs` non esiste.

- [ ] **Step 3: Implementa `parser_runs.py`**

`src/storage/parser_runs.py`:

```python
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(slots=True)
class ParserRunRecord:
    run_id: int
    started_at: str
    completed_at: str | None
    db_scope: str | None
    trader_filter: str | None
    parser_system: str
    parser_version: str | None
    force_reparse: bool
    notes: str | None


class ParserRunStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create_run(
        self,
        *,
        parser_system: str = "parser_v2",
        trader_filter: str | None = None,
        db_scope: str | None = None,
        parser_version: str | None = None,
        force_reparse: bool = False,
        notes: str | None = None,
    ) -> int:
        started_at = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            """
            INSERT INTO parser_runs
                (started_at, db_scope, trader_filter, parser_system,
                 parser_version, force_reparse, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (started_at, db_scope, trader_filter, parser_system,
             parser_version, int(force_reparse), notes),
        )
        self._conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def complete_run(self, run_id: int) -> None:
        completed_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE parser_runs SET completed_at = ? WHERE run_id = ?",
            (completed_at, run_id),
        )
        self._conn.commit()

    def get_latest_run(
        self,
        *,
        trader_filter: str | None = None,
        db_scope: str | None = None,
    ) -> ParserRunRecord | None:
        query = (
            "SELECT run_id, started_at, completed_at, db_scope, trader_filter, "
            "parser_system, parser_version, force_reparse, notes "
            "FROM parser_runs WHERE completed_at IS NOT NULL"
        )
        params: list[object] = []
        if trader_filter is not None:
            query += " AND trader_filter = ?"
            params.append(trader_filter)
        if db_scope is not None:
            query += " AND db_scope = ?"
            params.append(db_scope)
        query += " ORDER BY run_id DESC LIMIT 1"
        row = self._conn.execute(query, params).fetchone()
        if row is None:
            return None
        return ParserRunRecord(
            run_id=row[0],
            started_at=row[1],
            completed_at=row[2],
            db_scope=row[3],
            trader_filter=row[4],
            parser_system=row[5],
            parser_version=row[6],
            force_reparse=bool(row[7]),
            notes=row[8],
        )


__all__ = ["ParserRunRecord", "ParserRunStore"]
```

- [ ] **Step 4: Verifica test pass**

```bash
pytest tests/storage/test_parser_runs.py -v
```

Expected: tutti PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/storage/parser_runs.py tests/storage/test_parser_runs.py
git commit -m "feat(storage): aggiungi ParserRunStore"
```

---

## Task 5: `src/storage/parser_results_v2.py`

**Files:**
- Create: `src/storage/parser_results_v2.py`
- Create: `tests/storage/test_parser_results_v2.py`

- [ ] **Step 1: Scrivi il test**

`tests/storage/test_parser_results_v2.py`:

```python
from __future__ import annotations

import sqlite3

from parser_test.db.schema import apply_parser_test_schema
from src.storage.parser_results_v2 import ParserResultV2Record, ParserResultV2Store
from src.storage.parser_runs import ParserRunStore


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_parser_test_schema(conn)
    return conn


def _insert_raw(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO raw_messages (source_chat_id, telegram_message_id, message_ts, acquired_at) "
        "VALUES ('chat1', 42, '2026-05-01T10:00:00', '2026-05-01T10:00:00')"
    )
    conn.commit()
    return cur.lastrowid


def _make_record(run_id: int, raw_message_id: int, **kwargs) -> ParserResultV2Record:
    defaults = dict(
        run_id=run_id,
        raw_message_id=raw_message_id,
        trader_id="trader_a",
        parser_profile="trader_a",
        primary_class="SIGNAL",
        parse_status="PARSED",
        primary_intent="NEW_SIGNAL",
        confidence=0.9,
        canonical_json='{"primary_class":"SIGNAL"}',
        warnings_json=None,
        diagnostics_json=None,
        error_status="OK",
        error_message=None,
        created_at="2026-05-01T10:00:00",
    )
    defaults.update(kwargs)
    return ParserResultV2Record(**defaults)


def test_insert_and_fetch_ok_result():
    conn = _conn()
    raw_id = _insert_raw(conn)
    run_id = ParserRunStore(conn).create_run()
    store = ParserResultV2Store(conn)
    store.insert_result(_make_record(run_id, raw_id))
    results = store.fetch_by_run(run_id)
    assert len(results) == 1
    assert results[0].primary_class == "SIGNAL"
    assert results[0].error_status == "OK"


def test_insert_error_result():
    conn = _conn()
    raw_id = _insert_raw(conn)
    run_id = ParserRunStore(conn).create_run()
    store = ParserResultV2Store(conn)
    store.insert_result(_make_record(
        run_id, raw_id,
        canonical_json=None,
        error_status="PARSER_ERROR",
        error_message="boom",
    ))
    results = store.fetch_by_run(run_id)
    assert results[0].error_status == "PARSER_ERROR"
    assert results[0].error_message == "boom"


def test_fetch_by_run_filters_by_trader():
    conn = _conn()
    raw_id = _insert_raw(conn)
    run_id = ParserRunStore(conn).create_run()
    store = ParserResultV2Store(conn)
    store.insert_result(_make_record(run_id, raw_id, trader_id="trader_a"))
    results_a = store.fetch_by_run(run_id, trader="trader_a")
    results_b = store.fetch_by_run(run_id, trader="trader_b")
    assert len(results_a) == 1
    assert len(results_b) == 0


def test_fetch_latest_run_results():
    conn = _conn()
    raw_id = _insert_raw(conn)
    run_store = ParserRunStore(conn)
    run1 = run_store.create_run()
    store = ParserResultV2Store(conn)
    store.insert_result(_make_record(run1, raw_id))
    results = store.fetch_latest_run_results()
    assert len(results) == 1
    assert results[0].run_id == run1


def test_insert_upserts_on_conflict():
    conn = _conn()
    raw_id = _insert_raw(conn)
    run_id = ParserRunStore(conn).create_run()
    store = ParserResultV2Store(conn)
    store.insert_result(_make_record(run_id, raw_id, parse_status="PARSED"))
    store.insert_result(_make_record(run_id, raw_id, parse_status="PARTIAL"))
    results = store.fetch_by_run(run_id)
    assert len(results) == 1
    assert results[0].parse_status == "PARTIAL"
```

- [ ] **Step 2: Verifica test fail**

```bash
pytest tests/storage/test_parser_results_v2.py -v
```

Expected: `ModuleNotFoundError` — modulo non esiste.

- [ ] **Step 3: Implementa `parser_results_v2.py`**

`src/storage/parser_results_v2.py`:

```python
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(slots=True)
class ParserResultV2Record:
    run_id: int
    raw_message_id: int
    trader_id: str | None
    parser_profile: str | None
    primary_class: str | None
    parse_status: str | None
    primary_intent: str | None
    confidence: float | None
    canonical_json: str | None
    warnings_json: str | None
    diagnostics_json: str | None
    error_status: str
    error_message: str | None
    created_at: str


class ParserResultV2Store:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert_result(self, record: ParserResultV2Record) -> None:
        self._conn.execute(
            """
            INSERT INTO parser_results_v2 (
                run_id, raw_message_id, trader_id, parser_profile,
                primary_class, parse_status, primary_intent, confidence,
                canonical_json, warnings_json, diagnostics_json,
                error_status, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, raw_message_id) DO UPDATE SET
                trader_id        = excluded.trader_id,
                parser_profile   = excluded.parser_profile,
                primary_class    = excluded.primary_class,
                parse_status     = excluded.parse_status,
                primary_intent   = excluded.primary_intent,
                confidence       = excluded.confidence,
                canonical_json   = excluded.canonical_json,
                warnings_json    = excluded.warnings_json,
                diagnostics_json = excluded.diagnostics_json,
                error_status     = excluded.error_status,
                error_message    = excluded.error_message,
                created_at       = excluded.created_at
            """,
            (
                record.run_id, record.raw_message_id, record.trader_id, record.parser_profile,
                record.primary_class, record.parse_status, record.primary_intent, record.confidence,
                record.canonical_json, record.warnings_json, record.diagnostics_json,
                record.error_status, record.error_message, record.created_at,
            ),
        )
        self._conn.commit()

    def fetch_by_run(
        self,
        run_id: int,
        trader: str | None = None,
    ) -> list[ParserResultV2Record]:
        query = _SELECT + " WHERE run_id = ?"
        params: list[object] = [run_id]
        if trader is not None:
            query += " AND trader_id = ?"
            params.append(trader)
        return [_row(r) for r in self._conn.execute(query, params).fetchall()]

    def fetch_latest_run_results(
        self,
        trader: str | None = None,
    ) -> list[ParserResultV2Record]:
        if trader is not None:
            row = self._conn.execute(
                "SELECT MAX(run_id) FROM parser_results_v2 WHERE trader_id = ?", (trader,)
            ).fetchone()
        else:
            row = self._conn.execute("SELECT MAX(run_id) FROM parser_results_v2").fetchone()
        if row is None or row[0] is None:
            return []
        return self.fetch_by_run(row[0], trader=trader)


_SELECT = (
    "SELECT run_id, raw_message_id, trader_id, parser_profile, "
    "primary_class, parse_status, primary_intent, confidence, "
    "canonical_json, warnings_json, diagnostics_json, "
    "error_status, error_message, created_at "
    "FROM parser_results_v2"
)


def _row(r: tuple) -> ParserResultV2Record:
    return ParserResultV2Record(
        run_id=r[0],
        raw_message_id=r[1],
        trader_id=r[2],
        parser_profile=r[3],
        primary_class=r[4],
        parse_status=r[5],
        primary_intent=r[6],
        confidence=r[7],
        canonical_json=r[8],
        warnings_json=r[9],
        diagnostics_json=r[10],
        error_status=r[11],
        error_message=r[12],
        created_at=r[13],
    )


__all__ = ["ParserResultV2Record", "ParserResultV2Store"]
```

- [ ] **Step 4: Verifica test pass**

```bash
pytest tests/storage/test_parser_results_v2.py -v
```

Expected: tutti PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/storage/parser_results_v2.py tests/storage/test_parser_results_v2.py
git commit -m "feat(storage): aggiungi ParserResultV2Store"
```

---

## Task 6: `parser_test/scripts/replay_parser_v2.py`

**Files:**
- Create: `parser_test/scripts/replay_parser_v2.py`
- Create: `parser_test/scripts/tests/test_replay_trader_resolution.py`

- [ ] **Step 1: Scrivi test per `_resolve_trader`**

`parser_test/scripts/tests/test_replay_trader_resolution.py`:

```python
from __future__ import annotations

from parser_test.scripts.replay_parser_v2 import _resolve_trader


def test_resolve_from_explicit_arg():
    assert _resolve_trader(explicit="trader_a", source_trader_id=None) == "trader_a"


def test_resolve_explicit_overrides_source():
    assert _resolve_trader(explicit="trader_a", source_trader_id="trader_b") == "trader_a"


def test_resolve_from_source_trader_id():
    assert _resolve_trader(explicit=None, source_trader_id="ta") == "trader_a"


def test_resolve_unknown_explicit_returns_none():
    assert _resolve_trader(explicit="unknown_xyz", source_trader_id=None) is None


def test_resolve_both_none_returns_none():
    assert _resolve_trader(explicit=None, source_trader_id=None) is None
```

- [ ] **Step 2: Verifica test fail**

```bash
pytest parser_test/scripts/tests/test_replay_trader_resolution.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implementa `replay_parser_v2.py`**

`parser_test/scripts/replay_parser_v2.py`:

```python
"""Replay parser_v2 su messaggi raw salvati nel DB parser_test."""
from __future__ import annotations

import argparse
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
from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.core.runtime import UniversalParserRuntime
from src.parser_v2.profiles.registry import canonicalize_trader_v2, get_parser_v2_profile
from src.storage.parser_results_v2 import ParserResultV2Record, ParserResultV2Store
from src.storage.parser_runs import ParserRunStore

_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/\d+", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]{2,64})")


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


def _resolve_trader(
    *,
    explicit: str | None,
    source_trader_id: str | None,
) -> str | None:
    value = explicit if explicit is not None else source_trader_id
    return canonicalize_trader_v2(value)


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
        "source_trader_id, raw_text, reply_to_message_id, source_topic_id, message_ts "
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
        )
        for r in conn.execute(query, params).fetchall()
    ]


def run_replay(
    conn: sqlite3.Connection,
    *,
    trader: str | None = None,
    chat_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int | None = None,
    only_unparsed: bool = False,
    force_reparse: bool = False,
    show_samples: int = 0,
) -> int:
    apply_parser_test_schema(conn)
    run_store = ParserRunStore(conn)
    result_store = ParserResultV2Store(conn)

    run_id = run_store.create_run(
        trader_filter=trader,
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

    for raw in rows:
        trader_id = _resolve_trader(explicit=trader, source_trader_id=raw.source_trader_id)

        if trader_id is None:
            result_store.insert_result(
                ParserResultV2Record(
                    run_id=run_id,
                    raw_message_id=raw.raw_message_id,
                    trader_id=None,
                    parser_profile=None,
                    primary_class=None,
                    parse_status=None,
                    primary_intent=None,
                    confidence=None,
                    canonical_json=None,
                    warnings_json=None,
                    diagnostics_json=None,
                    error_status="UNRESOLVED_TRADER",
                    error_message=f"raw_message_id={raw.raw_message_id}",
                    created_at=_now_iso(),
                )
            )
            counts["UNRESOLVED_TRADER"] += 1
            continue

        try:
            profile = get_parser_v2_profile(trader_id)
        except KeyError as exc:
            result_store.insert_result(
                ParserResultV2Record(
                    run_id=run_id,
                    raw_message_id=raw.raw_message_id,
                    trader_id=trader_id,
                    parser_profile=None,
                    primary_class=None,
                    parse_status=None,
                    primary_intent=None,
                    confidence=None,
                    canonical_json=None,
                    warnings_json=None,
                    diagnostics_json=None,
                    error_status="PARSER_ERROR",
                    error_message=str(exc)[:500],
                    created_at=_now_iso(),
                )
            )
            counts["PARSER_ERROR"] += 1
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
                    trader_id=trader_id,
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
                    trader_id=trader_id,
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

    run_store.complete_run(run_id)

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
    parser.add_argument("--trader")
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only-unparsed", action="store_true")
    parser.add_argument("--force-reparse", action="store_true")
    parser.add_argument("--show-samples", type=int, default=0)
    args = parser.parse_args()

    parser_test_dir = Path(__file__).resolve().parents[1]
    db_path = resolve_parser_test_db_path(
        project_root=PROJECT_ROOT,
        parser_test_dir=parser_test_dir,
        explicit_db_path=args.db_path,
        db_name=args.db_name,
        db_per_chat=args.db_per_chat,
        chat_ref=args.chat_id,
    )
    conn = sqlite3.connect(db_path)
    try:
        run_replay(
            conn,
            trader=args.trader,
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

- [ ] **Step 4: Verifica test pass**

```bash
pytest parser_test/scripts/tests/test_replay_trader_resolution.py -v
```

Expected: tutti PASSED.

- [ ] **Step 5: Commit**

```bash
git add parser_test/scripts/replay_parser_v2.py parser_test/scripts/tests/test_replay_trader_resolution.py
git commit -m "feat(parser-test): aggiungi replay_parser_v2"
```

---

## Task 7: `report_schema_v2.py` e `flatteners_v2.py`

**Files:**
- Create: `parser_test/reporting/report_schema_v2.py`
- Create: `parser_test/reporting/flatteners_v2.py`
- Create: `parser_test/reporting/tests/__init__.py`
- Create: `parser_test/reporting/tests/test_flatteners_v2.py`

- [ ] **Step 1: Crea `report_schema_v2.py`**

`parser_test/reporting/report_schema_v2.py`:

```python
from __future__ import annotations

_COMMON_COLUMNS = [
    "run_id",
    "raw_message_id",
    "telegram_message_id",
    "source_chat_id",
    "source_topic_id",
    "reply_to_message_id",
    "message_ts",
    "trader_id",
    "parser_profile",
    "schema_version",
    "raw_text",
    "primary_class",
    "parse_status",
    "primary_intent",
    "intents",
    "confidence",
    "warnings",
    "diagnostics_summary",
]

_SIGNAL_COLUMNS = [
    "symbol",
    "side",
    "entry_structure",
    "entries_count",
    "entries_summary",
    "stop_loss_price",
    "take_profit_count",
    "take_profit_prices",
    "risk_hint_raw",
    "risk_hint_value",
    "risk_hint_min_value",
    "risk_hint_max_value",
    "leverage_hint",
    "missing_fields",
    "completeness",
]

_UPDATE_COLUMNS = [
    "operations_count",
    "operations_summary",
    "operation_types",
    "source_intents",
    "operation_confidences",
    "operation_raw_fragments",
    "target_scope_hint",
    "target_reply_to_message_id",
    "target_telegram_message_ids",
    "target_telegram_links",
    "target_explicit_ids",
    "target_symbols",
    "set_stop_target_type",
    "set_stop_price",
    "set_stop_tp_level",
    "close_scope",
    "close_fraction",
    "close_price",
    "cancel_scope_hint",
    "modify_entries_kind",
    "modify_entries_count",
    "modify_entries_summary",
    "modify_entries_entry_structure",
    "modify_targets_mode",
    "modify_targets_count",
    "modify_targets_prices",
    "modify_targets_target_tp_level",
    "invalidate_reason_text",
    "targeted_actions_count",
    "targeted_actions_summary",
]

_REPORT_COLUMNS = [
    "report_events_count",
    "report_events_summary",
    "report_event_types",
    "report_event_levels",
    "report_event_prices",
    "report_event_source_intents",
    "report_event_raw_fragments",
    "report_result_raw_fragment",
    "hit_target",
    "hit_price",
]

_INFO_COLUMNS = [
    "info_raw_fragment",
]

_ERRORS_COLUMNS = [
    "run_id",
    "raw_message_id",
    "telegram_message_id",
    "source_chat_id",
    "source_topic_id",
    "message_ts",
    "trader_id",
    "parser_profile",
    "primary_class",
    "parse_status",
    "primary_intent",
    "error_status",
    "error_message",
    "warnings",
    "diagnostics_summary",
    "raw_text",
]

SCOPE_COLUMNS: dict[str, list[str]] = {
    "ALL": _COMMON_COLUMNS,
    "NEW_SIGNAL": _COMMON_COLUMNS + _SIGNAL_COLUMNS,
    "SETUP_INCOMPLETE": _COMMON_COLUMNS + _SIGNAL_COLUMNS,
    "UPDATE": _COMMON_COLUMNS + _UPDATE_COLUMNS,
    "REPORT": _COMMON_COLUMNS + _REPORT_COLUMNS,
    "INFO_ONLY": _COMMON_COLUMNS + _INFO_COLUMNS,
    "UNCLASSIFIED": _COMMON_COLUMNS,
    "ERRORS": _ERRORS_COLUMNS,
}

__all__ = ["SCOPE_COLUMNS"]
```

- [ ] **Step 2: Scrivi il test per i flattener**

`parser_test/reporting/tests/__init__.py`: file vuoto.

`parser_test/reporting/tests/test_flatteners_v2.py`:

```python
from __future__ import annotations

import json

from parser_test.reporting.flatteners_v2 import ReportRow, flatten_for_scope


def _signal_row() -> ReportRow:
    canonical = {
        "schema_version": "2.0",
        "parser_profile": "trader_a",
        "primary_class": "SIGNAL",
        "parse_status": "PARSED",
        "primary_intent": "NEW_SIGNAL",
        "intents": ["NEW_SIGNAL"],
        "confidence": 0.95,
        "warnings": [],
        "diagnostics": {},
        "signal": {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_structure": "ONE_SHOT",
            "entries": [{"sequence": 1, "entry_type": "LIMIT", "price": {"raw": "30000", "value": 30000.0}, "role": "PRIMARY", "is_optional": False}],
            "stop_loss": {"price": {"raw": "29000", "value": 29000.0}},
            "take_profits": [
                {"sequence": 1, "price": {"raw": "31000", "value": 31000.0}},
                {"sequence": 2, "price": {"raw": "32000", "value": 32000.0}},
            ],
            "risk_hint": {"raw": "1%", "value": 1.0},
            "leverage_hint": None,
            "missing_fields": [],
            "completeness": "COMPLETE",
        },
        "raw_context": {"raw_text": "BUY BTCUSDT @ 30000"},
    }
    return ReportRow(
        run_id=1,
        raw_message_id=10,
        trader_id="trader_a",
        parser_profile="trader_a",
        primary_class="SIGNAL",
        parse_status="PARSED",
        primary_intent="NEW_SIGNAL",
        confidence=0.95,
        canonical_json=json.dumps(canonical),
        warnings_json=None,
        diagnostics_json=None,
        error_status="OK",
        error_message=None,
        telegram_message_id=42,
        source_chat_id="chat1",
        source_topic_id=None,
        reply_to_message_id=None,
        message_ts="2026-05-01T10:00:00",
        raw_text="BUY BTCUSDT @ 30000",
    )


def test_flatten_signal_common_fields():
    row = _signal_row()
    result = flatten_for_scope("NEW_SIGNAL", row)
    assert result["run_id"] == 1
    assert result["raw_message_id"] == 10
    assert result["primary_class"] == "SIGNAL"
    assert result["parse_status"] == "PARSED"
    assert result["trader_id"] == "trader_a"


def test_flatten_signal_symbol_side():
    result = flatten_for_scope("NEW_SIGNAL", _signal_row())
    assert result["symbol"] == "BTCUSDT"
    assert result["side"] == "LONG"


def test_flatten_signal_entries():
    result = flatten_for_scope("NEW_SIGNAL", _signal_row())
    assert result["entries_count"] == 1
    assert "30000" in result["entries_summary"]


def test_flatten_signal_take_profits():
    result = flatten_for_scope("NEW_SIGNAL", _signal_row())
    assert result["take_profit_count"] == 2
    assert "31000" in result["take_profit_prices"]
    assert "32000" in result["take_profit_prices"]


def test_flatten_signal_stop_loss():
    result = flatten_for_scope("NEW_SIGNAL", _signal_row())
    assert result["stop_loss_price"] == 29000.0


def test_flatten_all_scope_no_signal_columns():
    result = flatten_for_scope("ALL", _signal_row())
    assert "symbol" not in result
    assert "entries_count" not in result


def test_flatten_errors_scope():
    row = ReportRow(
        run_id=1,
        raw_message_id=5,
        trader_id=None,
        parser_profile=None,
        primary_class=None,
        parse_status=None,
        primary_intent=None,
        confidence=None,
        canonical_json=None,
        warnings_json=None,
        diagnostics_json=None,
        error_status="PARSER_ERROR",
        error_message="boom",
        telegram_message_id=99,
        source_chat_id="chat1",
        source_topic_id=None,
        reply_to_message_id=None,
        message_ts="2026-05-01",
        raw_text="testo",
    )
    result = flatten_for_scope("ERRORS", row)
    assert result["error_status"] == "PARSER_ERROR"
    assert result["error_message"] == "boom"
    assert "symbol" not in result
```

- [ ] **Step 3: Verifica test fail**

```bash
pytest parser_test/reporting/tests/test_flatteners_v2.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implementa `flatteners_v2.py`**

`parser_test/reporting/flatteners_v2.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from parser_test.reporting.report_schema_v2 import SCOPE_COLUMNS


@dataclass(slots=True)
class ReportRow:
    run_id: int
    raw_message_id: int
    trader_id: str | None
    parser_profile: str | None
    primary_class: str | None
    parse_status: str | None
    primary_intent: str | None
    confidence: float | None
    canonical_json: str | None
    warnings_json: str | None
    diagnostics_json: str | None
    error_status: str
    error_message: str | None
    telegram_message_id: int
    source_chat_id: str
    source_topic_id: int | None
    reply_to_message_id: int | None
    message_ts: str
    raw_text: str | None


def flatten_for_scope(scope: str, row: ReportRow) -> dict[str, Any]:
    canonical: dict[str, Any] = json.loads(row.canonical_json) if row.canonical_json else {}
    all_fields = _build_all_fields(row, canonical)
    columns = SCOPE_COLUMNS[scope]
    return {col: all_fields.get(col) for col in columns}


def _build_all_fields(row: ReportRow, canonical: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}

    fields.update(_common(row, canonical))

    signal = canonical.get("signal") or {}
    if signal:
        fields.update(_signal_fields(signal))

    update = canonical.get("update") or {}
    targeted_actions = canonical.get("targeted_actions") or []
    target_hints = canonical.get("target_hints") or {}
    if update or targeted_actions:
        fields.update(_update_fields(update, targeted_actions, target_hints))

    report = canonical.get("report") or {}
    if report:
        fields.update(_report_fields(report))

    info = canonical.get("info") or {}
    fields["info_raw_fragment"] = info.get("raw_fragment")

    return fields


def _common(row: ReportRow, canonical: dict[str, Any]) -> dict[str, Any]:
    warnings = canonical.get("warnings") or []
    diagnostics = canonical.get("diagnostics") or {}
    return {
        "run_id": row.run_id,
        "raw_message_id": row.raw_message_id,
        "telegram_message_id": row.telegram_message_id,
        "source_chat_id": row.source_chat_id,
        "source_topic_id": row.source_topic_id,
        "reply_to_message_id": row.reply_to_message_id,
        "message_ts": row.message_ts,
        "trader_id": row.trader_id,
        "parser_profile": canonical.get("parser_profile") or row.parser_profile,
        "schema_version": canonical.get("schema_version"),
        "raw_text": (canonical.get("raw_context") or {}).get("raw_text") or row.raw_text,
        "primary_class": canonical.get("primary_class") or row.primary_class,
        "parse_status": canonical.get("parse_status") or row.parse_status,
        "primary_intent": canonical.get("primary_intent") or row.primary_intent,
        "intents": "|".join(canonical.get("intents") or []),
        "confidence": canonical.get("confidence") if canonical else row.confidence,
        "warnings": "|".join(warnings),
        "diagnostics_summary": _diagnostics_summary(diagnostics),
        "error_status": row.error_status,
        "error_message": row.error_message,
    }


def _diagnostics_summary(diagnostics: dict[str, Any]) -> str | None:
    if not diagnostics:
        return None
    try:
        text = json.dumps(diagnostics, ensure_ascii=False)
        return text[:300] if len(text) > 300 else text
    except Exception:
        return str(diagnostics)[:300]


def _signal_fields(signal: dict[str, Any]) -> dict[str, Any]:
    entries = signal.get("entries") or []
    tps = signal.get("take_profits") or []
    risk_hint = signal.get("risk_hint") or {}
    stop_loss = signal.get("stop_loss") or {}
    return {
        "symbol": signal.get("symbol"),
        "side": signal.get("side"),
        "entry_structure": signal.get("entry_structure"),
        "entries_count": len(entries),
        "entries_summary": "|".join(
            f"{e.get('sequence')}:{e.get('entry_type')}:{e.get('role', '')}@{(e.get('price') or {}).get('value', '')}"
            for e in entries
        ),
        "stop_loss_price": (stop_loss.get("price") or {}).get("value"),
        "take_profit_count": len(tps),
        "take_profit_prices": "|".join(
            str((tp.get("price") or {}).get("value", "")) for tp in tps
        ),
        "risk_hint_raw": risk_hint.get("raw"),
        "risk_hint_value": risk_hint.get("value"),
        "risk_hint_min_value": risk_hint.get("min_value"),
        "risk_hint_max_value": risk_hint.get("max_value"),
        "leverage_hint": signal.get("leverage_hint"),
        "missing_fields": "|".join(signal.get("missing_fields") or []),
        "completeness": signal.get("completeness"),
    }


def _update_fields(
    update: dict[str, Any],
    targeted_actions: list[dict[str, Any]],
    target_hints: dict[str, Any],
) -> dict[str, Any]:
    operations: list[dict[str, Any]] = update.get("operations") or []
    first_set_stop = next((o.get("set_stop") or {} for o in operations if o.get("set_stop")), {})
    first_close = next((o.get("close") or {} for o in operations if o.get("close")), {})
    first_cancel = next((o.get("cancel_pending") or {} for o in operations if o.get("cancel_pending")), {})
    first_mod_entries = next((o.get("modify_entries") or {} for o in operations if o.get("modify_entries")), {})
    first_mod_targets = next((o.get("modify_targets") or {} for o in operations if o.get("modify_targets")), {})
    first_invalidate = next((o.get("invalidate_setup") or {} for o in operations if o.get("invalidate_setup")), {})
    mod_entries_entries = first_mod_entries.get("entries") or []
    mod_targets_tps = first_mod_targets.get("take_profits") or []

    return {
        "operations_count": len(operations),
        "operations_summary": "|".join(f"{o.get('op_type')}({o.get('source_intent')})" for o in operations),
        "operation_types": "|".join(o.get("op_type", "") for o in operations),
        "source_intents": "|".join(o.get("source_intent", "") for o in operations),
        "operation_confidences": "|".join(str(o.get("confidence", "")) for o in operations),
        "operation_raw_fragments": "|".join(o.get("raw_fragment", "") or "" for o in operations),
        "target_scope_hint": target_hints.get("scope_hint"),
        "target_reply_to_message_id": target_hints.get("reply_to_message_id"),
        "target_telegram_message_ids": "|".join(str(v) for v in (target_hints.get("telegram_message_ids") or [])),
        "target_telegram_links": "|".join(target_hints.get("telegram_links") or []),
        "target_explicit_ids": "|".join(target_hints.get("explicit_ids") or []),
        "target_symbols": "|".join(target_hints.get("symbols") or []),
        "set_stop_target_type": first_set_stop.get("target_type"),
        "set_stop_price": (first_set_stop.get("price") or {}).get("value"),
        "set_stop_tp_level": first_set_stop.get("tp_level"),
        "close_scope": first_close.get("close_scope"),
        "close_fraction": first_close.get("fraction"),
        "close_price": (first_close.get("close_price") or {}).get("value"),
        "cancel_scope_hint": first_cancel.get("cancel_scope_hint"),
        "modify_entries_kind": first_mod_entries.get("kind"),
        "modify_entries_count": len(mod_entries_entries),
        "modify_entries_summary": "|".join(
            f"{e.get('sequence')}:{e.get('entry_type')}@{(e.get('price') or {}).get('value', '')}"
            for e in mod_entries_entries
        ),
        "modify_entries_entry_structure": first_mod_entries.get("entry_structure"),
        "modify_targets_mode": first_mod_targets.get("mode"),
        "modify_targets_count": len(mod_targets_tps),
        "modify_targets_prices": "|".join(
            str((tp.get("price") or {}).get("value", "")) for tp in mod_targets_tps
        ),
        "modify_targets_target_tp_level": first_mod_targets.get("target_tp_level"),
        "invalidate_reason_text": first_invalidate.get("reason_text"),
        "targeted_actions_count": len(targeted_actions),
        "targeted_actions_summary": "|".join(
            f"{a.get('action_type')}({a.get('source_intent')})" for a in targeted_actions
        ),
    }


def _report_fields(report: dict[str, Any]) -> dict[str, Any]:
    events: list[dict[str, Any]] = report.get("events") or []
    result = report.get("result") or {}

    hit_target = None
    hit_price = None
    for ev in events:
        et = ev.get("event_type")
        level = ev.get("level")
        price_val = (ev.get("price") or {}).get("value")
        if et == "TP_HIT":
            hit_target = f"TP{level}" if level else "TP"
        elif et == "SL_HIT":
            hit_target = "SL"
        elif et == "EXIT_BE":
            hit_target = "BE"
        elif et == "ENTRY_FILLED":
            hit_target = f"ENTRY{level}" if level else "ENTRY"
        if hit_price is None and price_val is not None:
            hit_price = price_val

    return {
        "report_events_count": len(events),
        "report_events_summary": "|".join(
            f"{e.get('event_type')}(lvl={e.get('level')})" for e in events
        ),
        "report_event_types": "|".join(e.get("event_type", "") for e in events),
        "report_event_levels": "|".join(str(e.get("level", "")) for e in events),
        "report_event_prices": "|".join(str((e.get("price") or {}).get("value", "")) for e in events),
        "report_event_source_intents": "|".join(e.get("source_intent", "") for e in events),
        "report_event_raw_fragments": "|".join(e.get("raw_fragment", "") or "" for e in events),
        "report_result_raw_fragment": result.get("raw_fragment"),
        "hit_target": hit_target,
        "hit_price": hit_price,
    }


__all__ = ["ReportRow", "flatten_for_scope"]
```

- [ ] **Step 5: Verifica test pass**

```bash
pytest parser_test/reporting/tests/test_flatteners_v2.py -v
```

Expected: tutti PASSED.

- [ ] **Step 6: Commit**

```bash
git add parser_test/reporting/report_schema_v2.py parser_test/reporting/flatteners_v2.py parser_test/reporting/tests/
git commit -m "feat(parser-test): aggiungi report_schema_v2 e flatteners_v2"
```

---

## Task 8: `parser_test/reporting/report_export_v2.py`

**Files:**
- Create: `parser_test/reporting/report_export_v2.py`

Nessun test automatizzato — il comportamento CSV è verificato eseguendo il comando generate_reports su dati reali (Task 10). L'integrazione è coperta dal test in Task 10.

- [ ] **Step 1: Implementa `report_export_v2.py`**

`parser_test/reporting/report_export_v2.py`:

```python
"""Esporta parser_results_v2 in CSV per scope."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any

from parser_test.reporting.flatteners_v2 import ReportRow, flatten_for_scope
from parser_test.reporting.report_schema_v2 import SCOPE_COLUMNS

_SCOPE_FILTERS: dict[str, str] = {
    "ALL":             "r.error_status = 'OK'",
    "NEW_SIGNAL":      "r.error_status = 'OK' AND r.primary_class = 'SIGNAL' AND r.parse_status = 'PARSED'",
    "SETUP_INCOMPLETE":"r.error_status = 'OK' AND r.primary_class = 'SIGNAL' AND r.parse_status = 'PARTIAL'",
    "UPDATE":          "r.error_status = 'OK' AND r.primary_class = 'UPDATE'",
    "REPORT":          "r.error_status = 'OK' AND r.primary_class = 'REPORT'",
    "INFO_ONLY":       "r.error_status = 'OK' AND r.primary_class = 'INFO'",
    "UNCLASSIFIED":    "r.error_status = 'OK' AND r.parse_status = 'UNCLASSIFIED'",
    "ERRORS":          "(r.error_status != 'OK' OR r.parse_status = 'ERROR')",
}

_SCOPE_FILENAMES: dict[str, str] = {
    "ALL":             "all_messages",
    "NEW_SIGNAL":      "new_signal",
    "SETUP_INCOMPLETE":"setup_incomplete",
    "UPDATE":          "update",
    "REPORT":          "report",
    "INFO_ONLY":       "info_only",
    "UNCLASSIFIED":    "unclassified",
    "ERRORS":          "errors",
}

_SELECT_JOIN = """
    SELECT
        r.run_id, r.raw_message_id, r.trader_id, r.parser_profile,
        r.primary_class, r.parse_status, r.primary_intent, r.confidence,
        r.canonical_json, r.warnings_json, r.diagnostics_json,
        r.error_status, r.error_message,
        m.telegram_message_id, m.source_chat_id, m.source_topic_id,
        m.reply_to_message_id, m.message_ts, m.raw_text
    FROM parser_results_v2 r
    JOIN raw_messages m ON r.raw_message_id = m.raw_message_id
    WHERE r.run_id = ? AND {filter}
    ORDER BY m.message_ts ASC
"""


def export_all(
    conn: sqlite3.Connection,
    run_id: int,
    trader: str | None,
    reports_dir: Path,
) -> list[Path]:
    run_dir = reports_dir / f"run_{run_id}"
    trader_name = trader or "all_traders"
    csv_dir = run_dir / f"{trader_name}_message_types_csv"
    csv_dir.mkdir(parents=True, exist_ok=True)

    generated: list[Path] = []
    for scope, where_filter in _SCOPE_FILTERS.items():
        query = _SELECT_JOIN.format(filter=where_filter)
        params: list[Any] = [run_id]
        if trader is not None:
            query = query.rstrip() + " AND r.trader_id = ?"
            params.append(trader)
        rows = [_build_report_row(r) for r in conn.execute(query, params).fetchall()]
        filename = f"{trader_name}_{_SCOPE_FILENAMES[scope]}.csv"
        out_path = csv_dir / filename
        _write_csv(out_path, scope, rows)
        generated.append(out_path)
        print(f"  {filename}: {len(rows)} righe")
    return generated


def _build_report_row(r: tuple) -> ReportRow:
    return ReportRow(
        run_id=r[0],
        raw_message_id=r[1],
        trader_id=r[2],
        parser_profile=r[3],
        primary_class=r[4],
        parse_status=r[5],
        primary_intent=r[6],
        confidence=r[7],
        canonical_json=r[8],
        warnings_json=r[9],
        diagnostics_json=r[10],
        error_status=r[11],
        error_message=r[12],
        telegram_message_id=r[13],
        source_chat_id=r[14],
        source_topic_id=r[15],
        reply_to_message_id=r[16],
        message_ts=r[17],
        raw_text=r[18],
    )


def _write_csv(path: Path, scope: str, rows: list[ReportRow]) -> None:
    columns = SCOPE_COLUMNS[scope]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(flatten_for_scope(scope, row))


__all__ = ["export_all"]
```

- [ ] **Step 2: Commit**

```bash
git add parser_test/reporting/report_export_v2.py
git commit -m "feat(parser-test): aggiungi report_export_v2"
```

---

## Task 9: `parser_test/scripts/generate_parser_reports_v2.py`

**Files:**
- Create: `parser_test/scripts/generate_parser_reports_v2.py`

- [ ] **Step 1: Implementa lo script**

`parser_test/scripts/generate_parser_reports_v2.py`:

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
from parser_test.scripts.replay_parser_v2 import run_replay
from src.storage.parser_runs import ParserRunStore

_DEFAULT_REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports_v2"


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay parser_v2 + genera CSV")
    parser.add_argument("--db-path")
    parser.add_argument("--db-name")
    parser.add_argument("--db-per-chat", action="store_true")
    parser.add_argument("--chat-id")
    parser.add_argument("--trader")
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force-reparse", action="store_true")
    parser.add_argument("--skip-replay", action="store_true")
    parser.add_argument("--run", default="latest", help="'latest' oppure run_id numerico")
    parser.add_argument("--reports-dir", default=str(_DEFAULT_REPORTS_DIR))
    args = parser.parse_args()

    parser_test_dir = Path(__file__).resolve().parents[1]
    db_path = resolve_parser_test_db_path(
        project_root=PROJECT_ROOT,
        parser_test_dir=parser_test_dir,
        explicit_db_path=args.db_path,
        db_name=args.db_name,
        db_per_chat=args.db_per_chat,
        chat_ref=args.chat_id,
    )
    conn = sqlite3.connect(db_path)
    apply_parser_test_schema(conn)

    try:
        if not args.skip_replay:
            run_id = run_replay(
                conn,
                trader=args.trader,
                chat_id=args.chat_id,
                from_date=args.from_date,
                to_date=args.to_date,
                limit=args.limit,
                force_reparse=args.force_reparse,
            )
        else:
            if args.run == "latest":
                record = ParserRunStore(conn).get_latest_run(trader_filter=args.trader)
                if record is None:
                    print("[generate] Nessun run trovato. Esegui prima senza --skip-replay.")
                    sys.exit(1)
                run_id = record.run_id
            else:
                run_id = int(args.run)
            print(f"[generate] Uso run_id={run_id}")

        reports_dir = Path(args.reports_dir)
        print(f"\n[generate] Produco CSV in {reports_dir}/run_{run_id}/")
        generated = export_all(conn, run_id, args.trader, reports_dir)
        print(f"\n[generate] {len(generated)} file generati.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verifica smoke test**

Assicurati di avere almeno un DB nella cartella `parser_test/db/`. Se non ne hai, importa prima dei dati (vedi README che scriveremo). Se hai un DB, esegui:

```bash
python parser_test/scripts/generate_parser_reports_v2.py \
  --db-name trader_a_topic \
  --trader trader_a \
  --force-reparse
```

Expected: nessun traceback, output con conteggi messaggi, CSV generati in `parser_test/reports_v2/run_1/`.

Se non hai dati ora, salta questo step e procedi al commit.

- [ ] **Step 3: Commit**

```bash
git add parser_test/scripts/generate_parser_reports_v2.py
git commit -m "feat(parser-test): aggiungi generate_parser_reports_v2"
```

---

## Task 10: Aggiorna `watch_parser.py`

**Files:**
- Modify: `parser_test/scripts/watch_parser.py`

- [ ] **Step 1: Sostituisci il contenuto**

`parser_test/scripts/watch_parser.py`:

```python
"""Guarda i file del profilo parser_v2 e rilancia replay + CSV al cambio.

Uso:
    python parser_test/scripts/watch_parser.py --trader trader_a
    python parser_test/scripts/watch_parser.py --trader trader_a --dry-run
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False

_DEBOUNCE_SECONDS: float = 2.0
_WATCHED_FILENAMES: tuple[str, ...] = (
    "semantic_markers.json",
    "semantic_markers_1.json",
    "rules.json",
    "profile.py",
    "signal_extractor.py",
    "intent_entity_extractor.py",
)


def _monitored_files(trader: str) -> list[Path]:
    profile_dir = PROJECT_ROOT / "src" / "parser_v2" / "profiles" / trader
    return [profile_dir / name for name in _WATCHED_FILENAMES]


def _run_pipeline(trader: str, db_name: str | None, dry_run: bool) -> None:
    print(f"\n[watch_parser] cambio rilevato — avvio pipeline per {trader}", flush=True)
    report_script = PROJECT_ROOT / "parser_test" / "scripts" / "generate_parser_reports_v2.py"
    cmd = [sys.executable, str(report_script), "--trader", trader, "--force-reparse"]
    if db_name:
        cmd += ["--db-name", db_name]
    print(f"[watch_parser] {' '.join(cmd)}", flush=True)
    if not dry_run:
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
        if result.returncode != 0:
            print(f"[watch_parser] WARNING: exit code {result.returncode}", flush=True)
    print(f"[watch_parser] pipeline completata per {trader}", flush=True)


if _WATCHDOG_AVAILABLE:

    class _DebounceHandler(FileSystemEventHandler):
        def __init__(self, trader: str, db_name: str | None, dry_run: bool, watched_paths: set[Path]) -> None:
            self._trader = trader
            self._db_name = db_name
            self._dry_run = dry_run
            self._watched = {str(p.resolve()) for p in watched_paths}
            self._last_trigger: float = 0.0

        def on_modified(self, event: FileSystemEvent) -> None:
            if str(Path(event.src_path).resolve()) not in self._watched:
                return
            now = time.monotonic()
            if now - self._last_trigger < _DEBOUNCE_SECONDS:
                return
            self._last_trigger = now
            _run_pipeline(self._trader, self._db_name, self._dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch parser_v2 profile files and re-run pipeline")
    parser.add_argument("--trader", required=True)
    parser.add_argument("--db-name")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    watched = set(_monitored_files(args.trader))
    existing = [p for p in watched if p.exists()]

    if not existing:
        print(f"[watch_parser] ERRORE: nessun file trovato per trader={args.trader!r}")
        print(f"  cercato in: {PROJECT_ROOT / 'src' / 'parser_v2' / 'profiles' / args.trader}")
        sys.exit(1)

    if not _WATCHDOG_AVAILABLE:
        print("[watch_parser] watchdog non installato. Installa con: pip install watchdog")
        sys.exit(1)

    print(f"[watch_parser] monitoraggio trader={args.trader!r}")
    for p in sorted(existing):
        print(f"  {p.relative_to(PROJECT_ROOT)}")
    print("[watch_parser] Ctrl+C per fermare\n")

    handler = _DebounceHandler(args.trader, args.db_name, args.dry_run, watched)
    observer = Observer()
    for p in existing:
        observer.schedule(handler, str(p.parent), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add parser_test/scripts/watch_parser.py
git commit -m "feat(parser-test): aggiorna watch_parser per parser_v2"
```

---

## Task 11: Riscrivi `parser_test/README.md`

**Files:**
- Modify: `parser_test/README.md`

- [ ] **Step 1: Riscrivi il README**

`parser_test/README.md`:

```markdown
# parser_test — Ambiente di test e sviluppo per parser_v2

Ambiente autonomo per importare messaggi reali da Telegram, eseguire `src/parser_v2`
e produrre CSV leggibili per sviluppo e valutazione del parser.

---

## Setup

### 1. Dipendenze

```bash
pip install -r requirements.txt
```

### 2. File `.env`

Copia `.env.example` (se presente) oppure crea `parser_test/.env`:

```env
TELEGRAM_API_ID=<il tuo api_id>
TELEGRAM_API_HASH=<il tuo api_hash>
TELEGRAM_SESSION_NAME=parser_test
```

Le credenziali Telegram si ottengono da https://my.telegram.org.

---

## Flusso operativo

```
import_history.py   →   raw_messages nel DB
                              ↓
generate_parser_reports_v2.py (--force-reparse)
                              ↓
  replay_parser_v2   →   parser_results_v2
  report_export_v2   →   CSV in reports_v2/run_<id>/
```

---

## Comandi

### Import da Telegram

Scarica i messaggi di un canale/topic nel DB locale.

```bash
python parser_test/scripts/import_history.py ^
  --chat-id <CHAT_ID> ^
  --topic-id <TOPIC_ID> ^
  --db-name trader_a_topic ^
  --from-date 2026-04-01 ^
  --to-date 2026-05-01
```

| Argomento | Descrizione |
|-----------|-------------|
| `--chat-id` | ID numerico del canale Telegram |
| `--topic-id` | ID del topic (se il canale usa topics) |
| `--db-name` | Nome del DB locale (file in `db/parser_test__<name>.sqlite3`) |
| `--from-date` | Data inizio (`YYYY-MM-DD`) |
| `--to-date` | Data fine (`YYYY-MM-DD`) |
| `--limit` | Numero massimo messaggi |
| `--only-new` | Importa solo messaggi non ancora nel DB |
| `--download-media` | Scarica anche media (immagini, documenti) |

### Replay parser v2

Riesegue `src/parser_v2` su tutti i messaggi raw salvati.

```bash
python parser_test/scripts/replay_parser_v2.py ^
  --db-name trader_a_topic ^
  --trader trader_a ^
  --force-reparse
```

| Argomento | Descrizione |
|-----------|-------------|
| `--trader` | Profilo da usare (`trader_a`, `ta`, `a`) |
| `--from-date / --to-date` | Filtra per data messaggio |
| `--limit` | Processa solo N messaggi |
| `--only-unparsed` | Salta messaggi già parsati con successo |
| `--force-reparse` | Riprocessa anche se già presenti risultati |
| `--show-samples N` | Stampa N esempi di output a schermo |

### Genera CSV (da run esistente)

```bash
python parser_test/scripts/generate_parser_reports_v2.py ^
  --db-name trader_a_topic ^
  --run latest ^
  --trader trader_a ^
  --skip-replay
```

### Replay + CSV in un comando

```bash
python parser_test/scripts/generate_parser_reports_v2.py ^
  --db-name trader_a_topic ^
  --trader trader_a ^
  --force-reparse
```

### Watch mode (sviluppo attivo)

Monitora i file del profilo e rilancia automaticamente replay + CSV ad ogni modifica.

```bash
python parser_test/scripts/watch_parser.py ^
  --trader trader_a ^
  --db-name trader_a_topic
```

File monitorati: `semantic_markers.json`, `rules.json`, `profile.py`, `signal_extractor.py`,
`intent_entity_extractor.py` in `src/parser_v2/profiles/trader_a/`.

---

## Output CSV

I CSV vengono generati in:

```
parser_test/reports_v2/run_<run_id>/<trader>_message_types_csv/
  <trader>_all_messages.csv
  <trader>_new_signal.csv
  <trader>_update.csv
  <trader>_report.csv
  <trader>_info_only.csv
  <trader>_setup_incomplete.csv
  <trader>_unclassified.csv
  <trader>_errors.csv
```

### Scope CSV

| File | Contenuto |
|------|-----------|
| `all_messages` | Tutti i messaggi parsati con successo |
| `new_signal` | `primary_class=SIGNAL`, `parse_status=PARSED` |
| `setup_incomplete` | `primary_class=SIGNAL`, `parse_status=PARTIAL` |
| `update` | `primary_class=UPDATE` |
| `report` | `primary_class=REPORT` |
| `info_only` | `primary_class=INFO` |
| `unclassified` | `parse_status=UNCLASSIFIED` |
| `errors` | `error_status != OK` oppure `parse_status=ERROR` |

I valori lista nelle celle CSV usano `|` come separatore.
Encoding: UTF-8-sig (compatibile LibreOffice).

---

## Database

Il DB è un file SQLite in `parser_test/db/`. Ogni `--db-name` crea un file separato.

Tabelle principali:
- `raw_messages` — messaggi Telegram importati
- `parser_runs` — metadati di ogni run di replay
- `parser_results_v2` — risultati `CanonicalMessage` per ogni messaggio
```

- [ ] **Step 2: Commit**

```bash
git add parser_test/README.md
git commit -m "docs(parser-test): riscrivi README per parser_v2"
```

---

## Self-review

**Spec coverage:**
- ✅ Fase 1 Registry: Task 2
- ✅ Fase 2 DB: Task 3
- ✅ Fase 3 Storage: Task 4 + 5
- ✅ Fase 4 Replay: Task 6
- ✅ Fase 5 CSV: Task 7 + 8
- ✅ Fase 6 Comando unico: Task 9
- ✅ watch_parser: Task 10
- ✅ README: Task 11
- ✅ Eliminazione v1: Task 1

**Type consistency:**
- `ParserRunStore(conn)` — Task 4, usato uguale in Task 6 e 9 ✅
- `ParserResultV2Store(conn)` — Task 5, usato uguale in Task 6 ✅
- `apply_parser_test_schema(conn)` — Task 3, chiamato identico in Task 6 e 9 ✅
- `run_replay(conn, ...)` — definito in Task 6, importato in Task 9 ✅
- `ReportRow` — definito in Task 7 `flatteners_v2.py`, importato in Task 8 `report_export_v2.py` ✅
- `flatten_for_scope(scope, row)` — definito Task 7, usato Task 8 ✅
- `export_all(conn, run_id, trader, reports_dir)` — definito Task 8, chiamato Task 9 ✅

**Placeholder scan:** nessun TBD, nessun "implement later" ✅
```

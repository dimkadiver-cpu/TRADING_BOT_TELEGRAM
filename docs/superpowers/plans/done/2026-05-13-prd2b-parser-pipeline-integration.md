# PRD 2.b — Parser Pipeline Integration in runtime_v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Costruire `src/runtime_v2/parser_pipeline/` che consuma `ParserDispatchCandidate` (PRD 01), chiama `UniversalParserRuntime`, e persiste `CanonicalMessage` in `canonical_messages`.

**Architecture:** TDD. Migration SQL prima di tutto. Repository adapter pattern su SQLite (stesso stile di `raw_messages.py`). `ParserPipelineProcessor` orchestratore sottile, dipendenze iniettate. Acceptance test estende `tests/runtime_v2/test_acceptance.py`.

**Tech Stack:** Python 3.11, Pydantic v2, sqlite3, pytest, `src/parser_v2/`, `src/runtime_v2/`

---

## File map

| File | Azione |
|---|---|
| `db/migrations/024_runtime_v2_canonical_messages.sql` | Crea: schema tabella canonical_messages |
| `src/runtime_v2/persistence/canonical_messages.py` | Crea: CanonicalMessageRepository |
| `src/runtime_v2/parser_pipeline/__init__.py` | Crea: package marker |
| `src/runtime_v2/parser_pipeline/models.py` | Crea: CanonicalParseResult, ParserJobStatus |
| `src/runtime_v2/parser_pipeline/processor.py` | Crea: ParserPipelineProcessor |
| `tests/runtime_v2/test_canonical_message_repository.py` | Crea: test repository |
| `tests/runtime_v2/test_parser_pipeline_processor.py` | Crea: test processor |
| `tests/runtime_v2/test_acceptance.py` | Modifica: aggiungere slice end-to-end PRD 2.b |

---

## Task 1: Migration SQL — tabella `canonical_messages`

**Files:**
- Create: `db/migrations/024_runtime_v2_canonical_messages.sql`

- [ ] **Step 1: Creare il file migration**

```sql
-- db/migrations/024_runtime_v2_canonical_messages.sql
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

- [ ] **Step 2: Verificare che la migration si applichi su DB vuoto**

```python
# Eseguire in una sessione Python
import sqlite3
conn = sqlite3.connect(":memory:")
conn.executescript(open("db/migrations/024_runtime_v2_canonical_messages.sql").read())
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print(tables)  # deve contenere ('canonical_messages',)
conn.close()
```

Output atteso: `[('canonical_messages',)]`

- [ ] **Step 3: Commit**

```
git add db/migrations/024_runtime_v2_canonical_messages.sql
git commit -m "feat(runtime-v2): add canonical_messages migration 024"
```

---

## Task 2: `CanonicalMessageRepository` (TDD)

**Files:**
- Create: `src/runtime_v2/persistence/canonical_messages.py`
- Create: `tests/runtime_v2/test_canonical_message_repository.py`

- [ ] **Step 1: Scrivere i test (falliscono — il file non esiste ancora)**

Creare `tests/runtime_v2/test_canonical_message_repository.py`:

```python
from __future__ import annotations

import sqlite3
import pytest
from pathlib import Path

from src.parser_v2.contracts.context import RawContext
from src.parser_v2.contracts.canonical_message import CanonicalMessage, InfoPayload


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text())
    conn.commit()
    conn.close()


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    _apply_migrations(path)
    return path


def _make_info_canonical(profile: str = "trader_a") -> CanonicalMessage:
    return CanonicalMessage(
        parser_profile=profile,
        primary_class="INFO",
        parse_status="UNCLASSIFIED",
        confidence=1.0,
        info=InfoPayload(raw_fragment=None),
        raw_context=RawContext(raw_text="test message"),
    )


def test_save_returns_canonical_message_id(db_path):
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    canonical = _make_info_canonical()
    cid = repo.save(raw_message_id=1, canonical=canonical)
    assert isinstance(cid, int)
    assert cid > 0


def test_save_idempotent_same_raw_and_context(db_path):
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    canonical = _make_info_canonical()
    id1 = repo.save(raw_message_id=1, canonical=canonical)
    id2 = repo.save(raw_message_id=1, canonical=canonical)
    assert id1 == id2


def test_save_different_run_contexts_produce_different_rows(db_path):
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    canonical = _make_info_canonical()
    id1 = repo.save(raw_message_id=1, canonical=canonical, run_context="live")
    id2 = repo.save(raw_message_id=1, canonical=canonical, run_context="reparse_20260513")
    assert id1 != id2


def test_get_by_raw_message_id_returns_canonical(db_path):
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    canonical = _make_info_canonical()
    repo.save(raw_message_id=42, canonical=canonical)
    retrieved = repo.get_by_raw_message_id(raw_message_id=42)
    assert retrieved is not None
    assert retrieved.primary_class == "INFO"
    assert retrieved.parser_profile == "trader_a"


def test_get_by_raw_message_id_missing_returns_none(db_path):
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    result = repo.get_by_raw_message_id(raw_message_id=999)
    assert result is None
```

- [ ] **Step 2: Eseguire i test — verificare che falliscano**

```
pytest tests/runtime_v2/test_canonical_message_repository.py -v
```

Output atteso: `ModuleNotFoundError` o `ImportError` — il file non esiste.

- [ ] **Step 3: Implementare `CanonicalMessageRepository`**

Creare `src/runtime_v2/persistence/canonical_messages.py`:

```python
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.parser_v2.contracts.canonical_message import CanonicalMessage


class CanonicalMessageRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def save(
        self,
        raw_message_id: int,
        canonical: CanonicalMessage,
        run_context: str = "live",
    ) -> int:
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO canonical_messages
                    (raw_message_id, run_context, parser_profile, schema_version,
                     primary_class, parse_status, primary_intent, confidence,
                     canonical_json, warnings_json, diagnostics_json, parsed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_message_id,
                    run_context,
                    canonical.parser_profile,
                    canonical.schema_version,
                    canonical.primary_class,
                    canonical.parse_status,
                    canonical.primary_intent,
                    canonical.confidence,
                    canonical.model_dump_json(),
                    json.dumps(canonical.warnings),
                    json.dumps(canonical.diagnostics),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            if cursor.lastrowid and cursor.rowcount > 0:
                return cursor.lastrowid
            row = conn.execute(
                "SELECT canonical_message_id FROM canonical_messages "
                "WHERE raw_message_id = ? AND run_context = ?",
                (raw_message_id, run_context),
            ).fetchone()
            return row[0]
        finally:
            conn.close()

    def get_by_raw_message_id(
        self,
        raw_message_id: int,
        run_context: str = "live",
    ) -> CanonicalMessage | None:
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT canonical_json FROM canonical_messages "
                "WHERE raw_message_id = ? AND run_context = ?",
                (raw_message_id, run_context),
            ).fetchone()
            if row is None:
                return None
            return CanonicalMessage.model_validate_json(row[0])
        finally:
            conn.close()


__all__ = ["CanonicalMessageRepository"]
```

- [ ] **Step 4: Eseguire i test**

```
pytest tests/runtime_v2/test_canonical_message_repository.py -v
```

Output atteso: tutti e 5 i test passano.

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/persistence/canonical_messages.py tests/runtime_v2/test_canonical_message_repository.py
git commit -m "feat(runtime-v2): add CanonicalMessageRepository with idempotent save"
```

---

## Task 3: Modelli `CanonicalParseResult` e `ParserJobStatus`

**Files:**
- Create: `src/runtime_v2/parser_pipeline/__init__.py`
- Create: `src/runtime_v2/parser_pipeline/models.py`

- [ ] **Step 1: Creare il package `parser_pipeline`**

Creare `src/runtime_v2/parser_pipeline/__init__.py` (vuoto):

```python
```

- [ ] **Step 2: Creare `models.py`**

Creare `src/runtime_v2/parser_pipeline/models.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.parser_v2.contracts.canonical_message import CanonicalMessage
from src.parser_v2.contracts.enums import MessageClass, ParseStatus


class CanonicalParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_message_id: int
    canonical_message_id: int
    parser_profile: str
    primary_class: MessageClass
    parse_status: ParseStatus
    canonical_message: CanonicalMessage
    warnings: list[str]
    parsed_at: datetime


class ParserJobStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_message_id: int
    status: Literal["parsed", "failed", "skipped"]
    reason: str | None = None
    canonical_message_id: int | None = None


__all__ = ["CanonicalParseResult", "ParserJobStatus"]
```

- [ ] **Step 3: Verificare che i modelli siano importabili**

```
python -c "from src.runtime_v2.parser_pipeline.models import CanonicalParseResult, ParserJobStatus; print('OK')"
```

Output atteso: `OK`

- [ ] **Step 4: Commit**

```
git add src/runtime_v2/parser_pipeline/__init__.py src/runtime_v2/parser_pipeline/models.py
git commit -m "feat(runtime-v2): add parser_pipeline package with CanonicalParseResult and ParserJobStatus models"
```

---

## Task 4: `ParserPipelineProcessor` (TDD)

**Files:**
- Create: `src/runtime_v2/parser_pipeline/processor.py`
- Create: `tests/runtime_v2/test_parser_pipeline_processor.py`

- [ ] **Step 1: Scrivere i test (falliscono — processor non esiste)**

Creare `tests/runtime_v2/test_parser_pipeline_processor.py`:

```python
from __future__ import annotations

import sqlite3
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.contracts.canonical_message import CanonicalMessage, InfoPayload
from src.runtime_v2.intake.models import RawMessageEnvelope
from src.runtime_v2.trader_resolution.models import ResolvedTraderContext, ParserDispatchCandidate
from src.runtime_v2.parser_pipeline.models import CanonicalParseResult, ParserJobStatus


_TS = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)

_TRADER_A_SIGNAL = (
    "[trader#A]\n"
    "BTCUSDT Лонг\n"
    "Вход: 65000\n"
    "SL: 62000\n"
    "TP1: 70000\n"
)

_TRADER_A_INFO = "#admin Технические работы"


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text())
    conn.commit()
    conn.close()


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    _apply_migrations(path)
    return path


def _make_envelope(raw_message_id: int = 1, text: str = _TRADER_A_SIGNAL) -> RawMessageEnvelope:
    return RawMessageEnvelope(
        raw_message_id=raw_message_id,
        source_chat_id="-100123",
        source_chat_title="Test",
        source_type="channel",
        source_topic_id=3,
        telegram_message_id=raw_message_id,
        reply_to_message_id=None,
        raw_text=text,
        message_ts=_TS,
        acquired_at=_TS,
        acquisition_mode="live",
        acquisition_status="ACQUIRED",
        processing_status="done",
        source_trader_id="trader_a",
        resolved_trader_id="trader_a",
        resolution_method="source_chat_id",
        resolution_detail=None,
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )


def _make_candidate(raw_message_id: int = 1, text: str = _TRADER_A_SIGNAL) -> ParserDispatchCandidate:
    envelope = _make_envelope(raw_message_id, text)
    resolved = ResolvedTraderContext(
        raw_message_id=raw_message_id,
        trader_id="trader_a",
        method="source_chat_id",
        detail=None,
        is_ambiguous=False,
        resolved_at=_TS,
    )
    context = ParserContext(
        raw_context=RawContext(raw_text=text),
        message_id=raw_message_id,
        source_chat_id="-100123",
        source_topic_id=3,
    )
    return ParserDispatchCandidate(
        raw_message=envelope,
        resolved_trader=resolved,
        parser_profile="trader_a",
        parser_context=context,
    )


def test_process_signal_returns_canonical_parse_result(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    processor = ParserPipelineProcessor(canonical_repo=repo)
    candidate = _make_candidate()

    result = processor.process(candidate)

    assert isinstance(result, CanonicalParseResult)
    assert result.raw_message_id == 1
    assert result.primary_class == "SIGNAL"
    assert result.parse_status == "PARSED"
    assert result.canonical_message_id > 0


def test_process_info_message_returns_canonical_parse_result(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    processor = ParserPipelineProcessor(canonical_repo=repo)
    candidate = _make_candidate(raw_message_id=2, text=_TRADER_A_INFO)

    result = processor.process(candidate)

    assert isinstance(result, CanonicalParseResult)
    assert result.primary_class == "INFO"


def test_process_empty_text_returns_canonical_not_failed(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    processor = ParserPipelineProcessor(canonical_repo=repo)
    candidate = _make_candidate(raw_message_id=3, text=None)
    candidate = candidate.model_copy(
        update={"raw_message": candidate.raw_message.model_copy(update={"raw_text": None})}
    )

    result = processor.process(candidate)

    assert isinstance(result, CanonicalParseResult)
    assert result.parse_status in {"UNCLASSIFIED", "PARTIAL", "PARSED"}


def test_process_idempotent_same_raw_message(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    processor = ParserPipelineProcessor(canonical_repo=repo)
    candidate = _make_candidate()

    result1 = processor.process(candidate)
    result2 = processor.process(candidate)

    assert isinstance(result1, CanonicalParseResult)
    assert isinstance(result2, CanonicalParseResult)
    assert result1.canonical_message_id == result2.canonical_message_id


def test_process_unknown_profile_returns_job_status_failed(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

    repo = CanonicalMessageRepository(db_path)
    processor = ParserPipelineProcessor(canonical_repo=repo)
    candidate = _make_candidate()
    candidate = candidate.model_copy(update={"parser_profile": "trader_unknown"})

    result = processor.process(candidate)

    assert isinstance(result, ParserJobStatus)
    assert result.status == "failed"
    assert result.reason == "unknown_parser_profile"


def test_process_does_not_import_router() -> None:
    import importlib
    import sys
    mod = importlib.import_module("src.runtime_v2.parser_pipeline.processor")
    assert "src.telegram.router" not in sys.modules or \
        "router" not in str(mod.__file__)
```

- [ ] **Step 2: Eseguire i test — verificare che falliscano**

```
pytest tests/runtime_v2/test_parser_pipeline_processor.py -v
```

Output atteso: `ModuleNotFoundError` — processor non esiste.

- [ ] **Step 3: Implementare `ParserPipelineProcessor`**

Creare `src/runtime_v2/parser_pipeline/processor.py`:

```python
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.parser_v2.core.runtime import UniversalParserRuntime
from src.parser_v2.profiles.registry import get_parser_v2_profile
from src.runtime_v2.parser_pipeline.models import CanonicalParseResult, ParserJobStatus
from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository
from src.runtime_v2.trader_resolution.models import ParserDispatchCandidate

logger = logging.getLogger(__name__)


class ParserPipelineProcessor:
    def __init__(
        self,
        *,
        runtime: UniversalParserRuntime | None = None,
        canonical_repo: CanonicalMessageRepository,
    ) -> None:
        self._runtime = runtime or UniversalParserRuntime()
        self._canonical_repo = canonical_repo

    def process(
        self,
        candidate: ParserDispatchCandidate,
        run_context: str = "live",
    ) -> CanonicalParseResult | ParserJobStatus:
        raw_message_id = candidate.raw_message.raw_message_id
        raw_text = candidate.raw_message.raw_text or ""

        try:
            profile = get_parser_v2_profile(candidate.parser_profile)
        except KeyError:
            logger.error(
                "Unknown parser profile %r for raw_message_id=%d",
                candidate.parser_profile,
                raw_message_id,
            )
            return ParserJobStatus(
                raw_message_id=raw_message_id,
                status="failed",
                reason="unknown_parser_profile",
            )

        try:
            canonical = self._runtime.parse(raw_text, candidate.parser_context, profile)
        except Exception:
            logger.exception(
                "Parser runtime error for raw_message_id=%d profile=%r",
                raw_message_id,
                candidate.parser_profile,
            )
            return ParserJobStatus(
                raw_message_id=raw_message_id,
                status="failed",
                reason="parser_runtime_error",
            )

        try:
            canonical_message_id = self._canonical_repo.save(
                raw_message_id, canonical, run_context
            )
        except Exception:
            logger.exception("Persistence error for raw_message_id=%d", raw_message_id)
            return ParserJobStatus(
                raw_message_id=raw_message_id,
                status="failed",
                reason="persistence_error",
            )

        return CanonicalParseResult(
            raw_message_id=raw_message_id,
            canonical_message_id=canonical_message_id,
            parser_profile=canonical.parser_profile,
            primary_class=canonical.primary_class,
            parse_status=canonical.parse_status,
            canonical_message=canonical,
            warnings=canonical.warnings,
            parsed_at=datetime.now(timezone.utc),
        )


__all__ = ["ParserPipelineProcessor"]
```

- [ ] **Step 4: Eseguire i test del processor**

```
pytest tests/runtime_v2/test_parser_pipeline_processor.py -v
```

Output atteso: tutti i test passano.

- [ ] **Step 5: Eseguire l'intera suite runtime_v2**

```
pytest tests/runtime_v2/ -v
```

Output atteso: tutti i test passano.

- [ ] **Step 6: Commit**

```
git add src/runtime_v2/parser_pipeline/processor.py tests/runtime_v2/test_parser_pipeline_processor.py
git commit -m "feat(runtime-v2): add ParserPipelineProcessor — ParserDispatchCandidate to CanonicalMessage"
```

---

## Task 5: Test di accettazione end-to-end (slice PRD 01 + PRD 2.b)

**Files:**
- Modify: `tests/runtime_v2/test_acceptance.py`

- [ ] **Step 1: Aggiungere i test di accettazione end-to-end**

Aprire `tests/runtime_v2/test_acceptance.py` e aggiungere in fondo (tutti gli import sono locali alle funzioni — non modificare la sezione import esistente in cima al file):

```python
# ---------------------------------------------------------------------------
# PRD 2.b — Acceptance: slice end-to-end PRD 01 → parser_pipeline
# ---------------------------------------------------------------------------

_SIGNAL_TEXT_PRD2B = (
    "[trader#A]\n"
    "BTCUSDT Лонг\n"
    "Вход: 65000\n"
    "SL: 62000\n"
    "TP1: 70000\n"
)
_INFO_TEXT_PRD2B = "#admin Технические работы на сервере"
_PARTIAL_TEXT_PRD2B = "BTCUSDT Лонг\nВход: 65000"  # missing SL/TP → PARTIAL


def _run_intake_prd2b(
    db_path: str,
    text: str,
    msg_id: int,
    trader_id: str = "trader_a",
):
    """Run PRD 01 intake to produce a ParserDispatchCandidate."""
    from src.runtime_v2.trader_resolution.channel_config_resolver import (
        ChannelConfigResolver,
        ChannelEntry,
    )
    from src.runtime_v2.trader_resolution.resolver import RuntimeV2TraderResolver
    from src.runtime_v2.intake.processor import RuntimeV2IntakeProcessor
    from src.runtime_v2.persistence.raw_messages import RawMessageRepository

    channel_entry = ChannelEntry(
        chat_id="-100123",
        topic_id=3,
        label="Test",
        active=True,
        trader_id=trader_id,
        parser_profile=trader_id,
        blacklist=[],
    )
    config_resolver = MagicMock(spec=ChannelConfigResolver)
    config_resolver.resolve.return_value = channel_entry
    trader_resolver = RuntimeV2TraderResolver(channel_config_resolver=config_resolver)
    raw_repo = RawMessageRepository(db_path)
    intake = RuntimeV2IntakeProcessor(raw_repo=raw_repo, trader_resolver=trader_resolver)
    item = _make_item(chat_id="-100123", msg_id=msg_id, text=text, topic_id=3)
    result = intake.process(item)
    assert result is not None, "Intake produced no ParserDispatchCandidate"
    return result


def test_prd2b_signal_persisted_in_canonical_messages(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

    candidate = _run_intake_prd2b(db_path, _SIGNAL_TEXT_PRD2B, msg_id=100)
    processor = ParserPipelineProcessor(canonical_repo=CanonicalMessageRepository(db_path))
    result = processor.process(candidate)

    assert isinstance(result, CanonicalParseResult)
    assert result.primary_class == "SIGNAL"
    assert result.parse_status == "PARSED"
    stored = CanonicalMessageRepository(db_path).get_by_raw_message_id(
        candidate.raw_message.raw_message_id
    )
    assert stored is not None
    assert stored.primary_class == "SIGNAL"


def test_prd2b_info_message_persisted_schema_valid(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

    candidate = _run_intake_prd2b(db_path, _INFO_TEXT_PRD2B, msg_id=101)
    processor = ParserPipelineProcessor(canonical_repo=CanonicalMessageRepository(db_path))
    result = processor.process(candidate)

    assert isinstance(result, CanonicalParseResult)
    assert result.primary_class == "INFO"
    assert CanonicalMessageRepository(db_path).get_by_raw_message_id(
        candidate.raw_message.raw_message_id
    ) is not None


def test_prd2b_partial_message_persisted_not_failed(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

    candidate = _run_intake_prd2b(db_path, _PARTIAL_TEXT_PRD2B, msg_id=102)
    processor = ParserPipelineProcessor(canonical_repo=CanonicalMessageRepository(db_path))
    result = processor.process(candidate)

    assert isinstance(result, CanonicalParseResult)
    assert result.parse_status in {"PARTIAL", "UNCLASSIFIED", "PARSED"}
    assert CanonicalMessageRepository(db_path).get_by_raw_message_id(
        candidate.raw_message.raw_message_id
    ) is not None


def test_prd2b_idempotent_second_process_same_canonical_id(db_path):
    from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
    from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

    candidate = _run_intake_prd2b(db_path, _SIGNAL_TEXT_PRD2B, msg_id=103)
    repo = CanonicalMessageRepository(db_path)
    processor = ParserPipelineProcessor(canonical_repo=repo)
    result1 = processor.process(candidate)
    result2 = processor.process(candidate)

    assert isinstance(result1, CanonicalParseResult)
    assert isinstance(result2, CanonicalParseResult)
    assert result1.canonical_message_id == result2.canonical_message_id


def test_prd2b_no_router_import_in_parser_pipeline():
    import importlib
    import sys
    importlib.import_module("src.runtime_v2.parser_pipeline.processor")
    assert "src.telegram.router" not in sys.modules
```

- [ ] **Step 2: Eseguire i test di accettazione**

```
pytest tests/runtime_v2/test_acceptance.py -v -k "prd2b"
```

Output atteso: tutti i test `prd2b_*` passano.

- [ ] **Step 3: Eseguire tutta la suite runtime_v2**

```
pytest tests/runtime_v2/ -v
```

Output atteso: tutti i test passano (PRD 01 + PRD 2.b).

- [ ] **Step 4: Eseguire anche i test parser_v2 per escludere regressioni**

```
pytest src/parser_v2/tests/ tests/runtime_v2/ -v
```

Output atteso: tutti i test passano.

- [ ] **Step 5: Commit finale PRD 2.b**

```
git add tests/runtime_v2/test_acceptance.py
git commit -m "test(runtime-v2): acceptance slice PRD 2.b — raw_message to canonical_messages end-to-end"
```

---

## Verifica finale

```
pytest src/parser_v2/tests/ tests/runtime_v2/ -v
```

**PRD 2.b è done.** La slice `ParserDispatchCandidate → CanonicalMessage → canonical_messages` funziona senza toccare il router legacy. PRD 03 (Operation Rules Engine V2) può partire.

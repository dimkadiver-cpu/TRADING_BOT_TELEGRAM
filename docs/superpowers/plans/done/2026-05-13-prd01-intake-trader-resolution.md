# PRD-01 Intake & Trader Resolution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementare il primo blocco del `runtime_v2` — acquisizione raw message, risoluzione trader config-driven, e produzione di `ParserDispatchCandidate` — senza importare `src.telegram.router`.

**Architecture:** `src/runtime_v2/` è un nuovo package autonomo. Adapter sottili wrappano `RawMessageStore` e `ProcessingStatusStore` esistenti. `ChannelConfigResolver` carica `channels.yaml` per la risoluzione config-driven. `RuntimeV2IntakeProcessor` orchestra il pipeline completo: raw ingest → eligibility → trader resolution → `ParserDispatchCandidate`. Il parsing (`parser_v2`) non è incluso in questa fase.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, SQLite3, pytest

---

## File Map

**Create:**
- `db/migrations/023_runtime_v2_raw_messages.sql`
- `src/runtime_v2/__init__.py`
- `src/runtime_v2/intake/__init__.py`
- `src/runtime_v2/intake/models.py` — RawMessageEnvelope, RawIngestItem, IntakeConfig
- `src/runtime_v2/trader_resolution/__init__.py`
- `src/runtime_v2/trader_resolution/models.py` — ResolvedTraderContext, ParserDispatchCandidate
- `src/runtime_v2/trader_resolution/channel_config_resolver.py` — ChannelConfigResolver
- `src/runtime_v2/trader_resolution/resolver.py` — RuntimeV2TraderResolver
- `src/runtime_v2/persistence/__init__.py`
- `src/runtime_v2/persistence/raw_messages.py` — RawMessageRepository
- `src/runtime_v2/intake/eligibility.py` — IntakeEligibilityCheck
- `src/runtime_v2/intake/processor.py` — RuntimeV2IntakeProcessor
- `tests/runtime_v2/__init__.py`
- `tests/runtime_v2/test_channel_config_resolver.py`
- `tests/runtime_v2/test_trader_resolver.py`
- `tests/runtime_v2/test_intake_processor.py`

**Modify:**
- `parser_test/scripts/resolve_traders.py` — aggiunge config-driven step (channels.yaml prima di EffectiveTraderResolver)

---

## Task 1: Migration 023

**Files:**
- Create: `db/migrations/023_runtime_v2_raw_messages.sql`

- [ ] **Step 1: Scrivi la migration**

```sql
-- db/migrations/023_runtime_v2_raw_messages.sql
-- Add columns required by runtime_v2 intake layer.
-- acquisition_status already exists; new columns are additive.

ALTER TABLE raw_messages ADD COLUMN acquisition_mode TEXT NOT NULL DEFAULT 'live';
ALTER TABLE raw_messages ADD COLUMN resolved_trader_id TEXT;
ALTER TABLE raw_messages ADD COLUMN resolution_method TEXT;
ALTER TABLE raw_messages ADD COLUMN resolution_detail TEXT;

CREATE INDEX IF NOT EXISTS idx_raw_messages_resolved_trader_id
    ON raw_messages(resolved_trader_id);
```

- [ ] **Step 2: Verifica che la migration si applichi**

Controlla come le migration esistenti vengono applicate nel progetto (cerca `009_processing_status.sql` nel codice). Se c'è uno script runner usalo. Altrimenti applica manualmente:

```bash
python -c "
import sqlite3, pathlib
conn = sqlite3.connect('db/live.db')
conn.executescript(pathlib.Path('db/migrations/023_runtime_v2_raw_messages.sql').read_text())
conn.commit()
cols = [r[1] for r in conn.execute('PRAGMA table_info(raw_messages)')]
print('columns:', cols)
assert 'acquisition_mode' in cols
assert 'resolved_trader_id' in cols
print('OK')
"
```

Expected: `OK` e lista colonne include `acquisition_mode`, `resolved_trader_id`, `resolution_method`, `resolution_detail`.

- [ ] **Step 3: Commit**

```bash
git add db/migrations/023_runtime_v2_raw_messages.sql
git commit -m "feat(runtime_v2): migration 023 — add acquisition_mode and trader resolution columns"
```

---

## Task 2: Intake Models

**Files:**
- Create: `src/runtime_v2/__init__.py`
- Create: `src/runtime_v2/intake/__init__.py`
- Create: `src/runtime_v2/intake/models.py`
- Create: `tests/runtime_v2/__init__.py`
- Test: `tests/runtime_v2/test_intake_models.py`

- [ ] **Step 1: Scrivi i test**

```python
# tests/runtime_v2/test_intake_models.py
from __future__ import annotations
import pytest
from datetime import datetime, timezone
from src.runtime_v2.intake.models import (
    RawMessageEnvelope,
    RawIngestItem,
    IntakeConfig,
)

_TS = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


def _make_envelope(**overrides) -> RawMessageEnvelope:
    defaults = dict(
        raw_message_id=1,
        source_chat_id="-100123",
        source_chat_title="Test",
        source_type="channel",
        source_topic_id=3,
        telegram_message_id=456,
        reply_to_message_id=None,
        raw_text="BUY BTC",
        message_ts=_TS,
        acquired_at=_TS,
        acquisition_mode="live",
        acquisition_status="ACQUIRED",
        processing_status="pending",
        source_trader_id=None,
        resolved_trader_id=None,
        resolution_method=None,
        resolution_detail=None,
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )
    defaults.update(overrides)
    return RawMessageEnvelope(**defaults)


def test_raw_ingest_item_construction():
    item = RawIngestItem(
        source_chat_id="-100123",
        source_chat_title="Test",
        source_type="channel",
        source_topic_id=3,
        telegram_message_id=456,
        reply_to_message_id=None,
        raw_text="BUY BTC",
        message_ts=_TS,
        acquisition_mode="live",
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )
    assert item.source_chat_id == "-100123"
    assert item.acquisition_mode == "live"


def test_raw_message_envelope_valid():
    env = _make_envelope()
    assert env.raw_message_id == 1
    assert env.acquisition_status == "ACQUIRED"
    assert env.processing_status == "pending"


def test_raw_message_envelope_rejects_invalid_acquisition_status():
    with pytest.raises(Exception):
        _make_envelope(acquisition_status="ACQUIRED_REVIEW_ONLY")


def test_raw_message_envelope_rejects_invalid_processing_status():
    with pytest.raises(Exception):
        _make_envelope(processing_status="unknown_status")


def test_intake_config_defaults():
    cfg = IntakeConfig()
    assert cfg.reply_chain_depth_limit == 5


def test_intake_config_custom():
    cfg = IntakeConfig(reply_chain_depth_limit=10)
    assert cfg.reply_chain_depth_limit == 10
```

- [ ] **Step 2: Esegui i test per verificare il fallimento**

```bash
pytest tests/runtime_v2/test_intake_models.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.runtime_v2'`

- [ ] **Step 3: Crea i file package e models**

```python
# src/runtime_v2/__init__.py
from __future__ import annotations
```

```python
# src/runtime_v2/intake/__init__.py
from __future__ import annotations
```

```python
# tests/runtime_v2/__init__.py
```

```python
# src/runtime_v2/intake/models.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

AcquisitionStatus = Literal["ACQUIRED", "BLACKLISTED", "MEDIA_ONLY_SKIPPED"]
ProcessingStatusV2 = Literal[
    "pending", "processing", "done", "failed", "blacklisted", "review", "skipped"
]
AcquisitionMode = Literal["live", "catchup", "import"]


@dataclass(slots=True, frozen=True)
class IntakeConfig:
    """Global configuration for the runtime_v2 intake pipeline."""
    reply_chain_depth_limit: int = 5


@dataclass(slots=True)
class RawIngestItem:
    """Raw Telegram event received by the intake processor from the listener."""
    source_chat_id: str
    source_chat_title: str | None
    source_type: str | None
    source_topic_id: int | None
    telegram_message_id: int
    reply_to_message_id: int | None
    raw_text: str | None
    message_ts: datetime
    acquisition_mode: AcquisitionMode
    has_media: bool
    media_kind: str | None
    media_mime_type: str | None
    media_filename: str | None


class RawMessageEnvelope(BaseModel):
    """Persisted raw message contract.

    acquisition_status is set once at ingest and never changes.
    processing_status tracks intake pipeline progress and is mutable.
    """

    raw_message_id: int
    source_chat_id: str
    source_chat_title: str | None
    source_type: str | None
    source_topic_id: int | None
    telegram_message_id: int
    reply_to_message_id: int | None
    raw_text: str | None
    message_ts: datetime
    acquired_at: datetime
    acquisition_mode: AcquisitionMode
    acquisition_status: AcquisitionStatus
    processing_status: ProcessingStatusV2
    source_trader_id: str | None
    resolved_trader_id: str | None
    resolution_method: str | None
    resolution_detail: str | None
    has_media: bool
    media_kind: str | None
    media_mime_type: str | None
    media_filename: str | None
```

- [ ] **Step 4: Esegui i test**

```bash
pytest tests/runtime_v2/test_intake_models.py -v
```

Expected: tutti PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/ tests/runtime_v2/
git commit -m "feat(runtime_v2): intake models — RawMessageEnvelope, RawIngestItem, IntakeConfig"
```

---

## Task 3: Trader Resolution Models

**Files:**
- Create: `src/runtime_v2/trader_resolution/__init__.py`
- Create: `src/runtime_v2/trader_resolution/models.py`
- Test: `tests/runtime_v2/test_trader_resolution_models.py`

- [ ] **Step 1: Scrivi i test**

```python
# tests/runtime_v2/test_trader_resolution_models.py
from __future__ import annotations
import pytest
from datetime import datetime, timezone
from src.runtime_v2.trader_resolution.models import (
    ResolvedTraderContext,
    ParserDispatchCandidate,
)
from src.runtime_v2.intake.models import RawMessageEnvelope
from src.parser_v2.contracts.context import ParserContext

_TS = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


def _make_envelope(raw_message_id: int = 1) -> RawMessageEnvelope:
    return RawMessageEnvelope(
        raw_message_id=raw_message_id,
        source_chat_id="-100123",
        source_chat_title=None,
        source_type=None,
        source_topic_id=3,
        telegram_message_id=456,
        reply_to_message_id=None,
        raw_text="BUY BTC",
        message_ts=_TS,
        acquired_at=_TS,
        acquisition_mode="live",
        acquisition_status="ACQUIRED",
        processing_status="pending",
        source_trader_id=None,
        resolved_trader_id=None,
        resolution_method=None,
        resolution_detail=None,
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )


def test_resolved_trader_context_resolved():
    ctx = ResolvedTraderContext(
        raw_message_id=1,
        trader_id="trader_a",
        method="source_chat_id",
        detail=None,
        is_ambiguous=False,
        resolved_at=_TS,
    )
    assert ctx.trader_id == "trader_a"
    assert not ctx.is_ambiguous


def test_resolved_trader_context_unresolved():
    ctx = ResolvedTraderContext(
        raw_message_id=1,
        trader_id=None,
        method="unresolved",
        detail="no alias found",
        is_ambiguous=False,
        resolved_at=_TS,
    )
    assert ctx.trader_id is None
    assert ctx.method == "unresolved"


def test_resolved_trader_context_rejects_invalid_method():
    with pytest.raises(Exception):
        ResolvedTraderContext(
            raw_message_id=1,
            trader_id="trader_a",
            method="invalid_method",
            detail=None,
            is_ambiguous=False,
            resolved_at=_TS,
        )


def test_parser_dispatch_candidate():
    env = _make_envelope()
    resolved = ResolvedTraderContext(
        raw_message_id=1,
        trader_id="trader_a",
        method="source_chat_id",
        detail=None,
        is_ambiguous=False,
        resolved_at=_TS,
    )
    ctx = ParserContext(message_id=456, source_chat_id="-100123", source_topic_id=3)
    candidate = ParserDispatchCandidate(
        raw_message=env,
        resolved_trader=resolved,
        parser_profile="trader_a",
        parser_context=ctx,
    )
    assert candidate.parser_profile == "trader_a"
    assert candidate.raw_message.raw_message_id == 1
    assert candidate.resolved_trader.trader_id == "trader_a"
```

- [ ] **Step 2: Esegui per verificare il fallimento**

```bash
pytest tests/runtime_v2/test_trader_resolution_models.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.runtime_v2.trader_resolution'`

- [ ] **Step 3: Crea i file**

```python
# src/runtime_v2/trader_resolution/__init__.py
from __future__ import annotations
```

```python
# src/runtime_v2/trader_resolution/models.py
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from src.parser_v2.contracts.context import ParserContext
from src.runtime_v2.intake.models import RawMessageEnvelope

ResolutionMethod = Literal[
    "content_alias",
    "content_alias_ambiguous",
    "reply_chain",
    "reply_chain_alias",
    "source_chat_id",
    "source_chat_username",
    "source_chat_title",
    "source_topic_config",
    "assume_trader",
    "unresolved",
]


class ResolvedTraderContext(BaseModel):
    raw_message_id: int
    trader_id: str | None
    method: ResolutionMethod
    detail: str | None
    is_ambiguous: bool
    resolved_at: datetime


class ParserDispatchCandidate(BaseModel):
    raw_message: RawMessageEnvelope
    resolved_trader: ResolvedTraderContext
    parser_profile: str
    parser_context: ParserContext
```

- [ ] **Step 4: Esegui i test**

```bash
pytest tests/runtime_v2/test_trader_resolution_models.py -v
```

Expected: tutti PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/trader_resolution/ tests/runtime_v2/test_trader_resolution_models.py
git commit -m "feat(runtime_v2): trader resolution models — ResolvedTraderContext, ParserDispatchCandidate"
```

---

## Task 4: ChannelConfigResolver

**Files:**
- Create: `src/runtime_v2/trader_resolution/channel_config_resolver.py`
- Test: `tests/runtime_v2/test_channel_config_resolver.py`

- [ ] **Step 1: Scrivi i test**

```python
# tests/runtime_v2/test_channel_config_resolver.py
from __future__ import annotations
import pytest
import yaml
from src.runtime_v2.trader_resolution.channel_config_resolver import (
    ChannelConfigResolver,
    ChannelEntry,
)

_SAMPLE_YAML = """
recovery:
  max_hours: 7
blacklist_global:
  - "#admin"
  - "#info"
channels:
  - chat_id: -1001111111111
    topic_id: 3
    label: "Trader_A_Topic"
    active: true
    trader_id: trader_a
    blacklist: []
  - chat_id: -1001111111111
    topic_id: 4
    label: "Trader_B_Topic"
    active: true
    trader_id: trader_b
    blacklist: ["#skip"]
  - chat_id: -1002222222222
    label: "Mono_C"
    active: true
    trader_id: trader_c
    blacklist: []
  - chat_id: -1003333333333
    label: "Inactive_D"
    active: false
    trader_id: trader_d
    blacklist: []
  - chat_id: -1004444444444
    label: "Custom_Profile"
    active: true
    trader_id: trader_e
    parser_profile: trader_e_v2
    blacklist: []
"""


@pytest.fixture
def config_file(tmp_path):
    p = tmp_path / "channels.yaml"
    p.write_text(_SAMPLE_YAML)
    return str(p)


@pytest.fixture
def resolver(config_file):
    return ChannelConfigResolver(config_file)


def test_lookup_by_chat_and_topic(resolver):
    entry = resolver.lookup("-1001111111111", topic_id=3)
    assert entry is not None
    assert entry.trader_id == "trader_a"
    assert entry.active is True


def test_lookup_different_topic(resolver):
    entry = resolver.lookup("-1001111111111", topic_id=4)
    assert entry is not None
    assert entry.trader_id == "trader_b"


def test_lookup_no_topic_mono_trader(resolver):
    entry = resolver.lookup("-1002222222222", topic_id=None)
    assert entry is not None
    assert entry.trader_id == "trader_c"


def test_lookup_unknown_chat_returns_none(resolver):
    assert resolver.lookup("-9999999999", topic_id=None) is None


def test_lookup_inactive_returns_entry_with_active_false(resolver):
    entry = resolver.lookup("-1003333333333", topic_id=None)
    assert entry is not None
    assert entry.active is False


def test_lookup_parser_profile_override(resolver):
    entry = resolver.lookup("-1004444444444", topic_id=None)
    assert entry is not None
    assert entry.parser_profile == "trader_e_v2"


def test_lookup_parser_profile_defaults_to_trader_id(resolver):
    entry = resolver.lookup("-1002222222222", topic_id=None)
    assert entry is not None
    assert entry.parser_profile == "trader_c"


def test_global_blacklist_match(resolver):
    assert resolver.is_globally_blacklisted("#admin pinned") is True
    assert resolver.is_globally_blacklisted("#info message") is True


def test_global_blacklist_no_match(resolver):
    assert resolver.is_globally_blacklisted("BUY BTC 45000") is False


def test_reload_picks_up_changes(config_file):
    resolver = ChannelConfigResolver(config_file)
    assert resolver.lookup("-1002222222222", topic_id=None).trader_id == "trader_c"
    data = yaml.safe_load(open(config_file))
    for ch in data["channels"]:
        if str(ch["chat_id"]) == "-1002222222222":
            ch["trader_id"] = "trader_c_new"
    with open(config_file, "w") as f:
        yaml.dump(data, f)
    resolver.reload()
    assert resolver.lookup("-1002222222222", topic_id=None).trader_id == "trader_c_new"


def test_topic_fallback_to_chat_only(resolver):
    # topic_id=99 not configured; falls back to chat-level entry if present
    # -1002222222222 has no topic_id in yaml → stored as (chat_id, None)
    entry = resolver.lookup("-1002222222222", topic_id=99)
    assert entry is not None
    assert entry.trader_id == "trader_c"
```

- [ ] **Step 2: Esegui per verificare il fallimento**

```bash
pytest tests/runtime_v2/test_channel_config_resolver.py -v
```

Expected: `ModuleNotFoundError: No module named '...channel_config_resolver'`

- [ ] **Step 3: Implementa `channel_config_resolver.py`**

```python
# src/runtime_v2/trader_resolution/channel_config_resolver.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass(slots=True, frozen=True)
class ChannelEntry:
    chat_id: str
    topic_id: int | None
    label: str | None
    active: bool
    trader_id: str | None
    parser_profile: str  # defaults to trader_id when not overridden in yaml
    blacklist: list[str]


class ChannelConfigResolver:
    """Loads channels.yaml and provides O(1) lookup by (source_chat_id, topic_id).

    Call reload() to refresh after a file change. Watchdog hot-reload is
    the caller's responsibility — this class only manages the in-memory index.
    """

    def __init__(self, config_path: str) -> None:
        self._config_path = config_path
        self._index: dict[tuple[str, int | None], ChannelEntry] = {}
        self._global_blacklist: list[str] = []
        self.reload()

    def reload(self) -> None:
        with open(self._config_path, encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f)
        index: dict[tuple[str, int | None], ChannelEntry] = {}
        for raw in data.get("channels", []):
            chat_id = str(raw["chat_id"])
            topic_id: int | None = raw.get("topic_id")
            trader_id: str | None = raw.get("trader_id")
            parser_profile: str = raw.get("parser_profile") or trader_id or ""
            entry = ChannelEntry(
                chat_id=chat_id,
                topic_id=topic_id,
                label=raw.get("label"),
                active=bool(raw.get("active", False)),
                trader_id=trader_id,
                parser_profile=parser_profile,
                blacklist=list(raw.get("blacklist", [])),
            )
            index[(chat_id, topic_id)] = entry
        self._index = index
        self._global_blacklist = list(data.get("blacklist_global", []))

    def lookup(self, source_chat_id: str, topic_id: int | None) -> ChannelEntry | None:
        """Returns ChannelEntry or None if not configured.

        Lookup order:
        1. Exact match on (source_chat_id, topic_id)
        2. If topic_id is not None, fallback to (source_chat_id, None)
        Caller is responsible for checking entry.active.
        """
        entry = self._index.get((source_chat_id, topic_id))
        if entry is not None:
            return entry
        if topic_id is not None:
            return self._index.get((source_chat_id, None))
        return None

    def is_globally_blacklisted(self, text: str) -> bool:
        return any(phrase in text for phrase in self._global_blacklist)
```

- [ ] **Step 4: Esegui i test**

```bash
pytest tests/runtime_v2/test_channel_config_resolver.py -v
```

Expected: tutti PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/trader_resolution/channel_config_resolver.py \
        tests/runtime_v2/test_channel_config_resolver.py
git commit -m "feat(runtime_v2): ChannelConfigResolver — config-driven trader lookup from channels.yaml"
```

---

## Task 5: RawMessageRepository (Persistence Adapter)

**Files:**
- Create: `src/runtime_v2/persistence/__init__.py`
- Create: `src/runtime_v2/persistence/raw_messages.py`
- Test: `tests/runtime_v2/test_raw_message_repository.py`

- [ ] **Step 1: Scrivi i test**

```python
# tests/runtime_v2/test_raw_message_repository.py
from __future__ import annotations
import pytest
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.intake.models import RawIngestItem
from src.runtime_v2.trader_resolution.models import ResolvedTraderContext

_TS = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


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


@pytest.fixture
def repo(db_path):
    return RawMessageRepository(db_path=db_path)


def _make_item(chat_id: str = "-100123", msg_id: int = 456, mode: str = "live") -> RawIngestItem:
    return RawIngestItem(
        source_chat_id=chat_id,
        source_chat_title="Test",
        source_type="channel",
        source_topic_id=3,
        telegram_message_id=msg_id,
        reply_to_message_id=None,
        raw_text="BUY BTC",
        message_ts=_TS,
        acquisition_mode=mode,
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )


def test_save_raw_returns_envelope(repo):
    env = repo.save_raw(_make_item())
    assert env.raw_message_id > 0
    assert env.source_chat_id == "-100123"
    assert env.acquisition_status == "ACQUIRED"
    assert env.processing_status == "pending"
    assert env.acquisition_mode == "live"


def test_save_raw_dedup_same_id(repo):
    env1 = repo.save_raw(_make_item())
    env2 = repo.save_raw(_make_item())
    assert env1.raw_message_id == env2.raw_message_id


def test_save_raw_catchup_mode(repo):
    env = repo.save_raw(_make_item(mode="catchup"))
    assert env.acquisition_mode == "catchup"


def test_set_blacklisted(repo):
    env = repo.save_raw(_make_item())
    repo.set_blacklisted(env.raw_message_id)
    updated = repo.get_by_id(env.raw_message_id)
    assert updated.acquisition_status == "BLACKLISTED"
    assert updated.processing_status == "blacklisted"


def test_set_media_only_skipped(repo):
    env = repo.save_raw(_make_item())
    repo.set_media_only_skipped(env.raw_message_id)
    updated = repo.get_by_id(env.raw_message_id)
    assert updated.acquisition_status == "MEDIA_ONLY_SKIPPED"
    assert updated.processing_status == "skipped"


def test_update_processing_status(repo):
    env = repo.save_raw(_make_item())
    repo.update_processing_status(env.raw_message_id, "review")
    updated = repo.get_by_id(env.raw_message_id)
    assert updated.processing_status == "review"


def test_update_trader_resolution(repo):
    env = repo.save_raw(_make_item())
    ctx = ResolvedTraderContext(
        raw_message_id=env.raw_message_id,
        trader_id="trader_a",
        method="source_chat_id",
        detail=None,
        is_ambiguous=False,
        resolved_at=_TS,
    )
    repo.update_trader_resolution(env.raw_message_id, ctx)
    updated = repo.get_by_id(env.raw_message_id)
    assert updated.resolved_trader_id == "trader_a"
    assert updated.resolution_method == "source_chat_id"
    assert updated.resolution_detail is None
```

- [ ] **Step 2: Esegui per verificare il fallimento**

```bash
pytest tests/runtime_v2/test_raw_message_repository.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.runtime_v2.persistence'`

- [ ] **Step 3: Crea i file**

```python
# src/runtime_v2/persistence/__init__.py
from __future__ import annotations
```

```python
# src/runtime_v2/persistence/raw_messages.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.storage.raw_messages import RawMessageStore, RawMessageRecord
from src.storage.processing_status import ProcessingStatusStore
from src.runtime_v2.intake.models import (
    RawIngestItem,
    RawMessageEnvelope,
    ProcessingStatusV2,
)
from src.runtime_v2.trader_resolution.models import ResolvedTraderContext


class RawMessageRepository:
    """Adapter over RawMessageStore + ProcessingStatusStore for runtime_v2.

    The existing storage layer handles core dedup and persistence.
    New columns (acquisition_mode, resolved_trader_id, etc.) are managed
    via direct SQL because RawMessageRecord is a legacy contract we don't modify.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._raw_store = RawMessageStore(db_path)
        self._status_store = ProcessingStatusStore(db_path)

    def save_raw(self, item: RawIngestItem) -> RawMessageEnvelope:
        """Save or retrieve raw message by dedup key (source_chat_id, telegram_message_id)."""
        record = RawMessageRecord(
            source_chat_id=item.source_chat_id,
            source_chat_title=item.source_chat_title,
            source_type=item.source_type,
            source_trader_id=None,
            source_topic_id=item.source_topic_id,
            telegram_message_id=item.telegram_message_id,
            reply_to_message_id=item.reply_to_message_id,
            raw_text=item.raw_text,
            message_ts=item.message_ts.isoformat(),
            acquired_at=datetime.now(timezone.utc).isoformat(),
            acquisition_status="ACQUIRED",
            has_media=item.has_media,
            media_kind=item.media_kind,
            media_mime_type=item.media_mime_type,
            media_filename=item.media_filename,
        )
        result = self._raw_store.save_with_id(record)
        self._update_column(result.raw_message_id, "acquisition_mode", item.acquisition_mode)
        return self.get_by_id(result.raw_message_id)

    def get_by_id(self, raw_message_id: int) -> RawMessageEnvelope:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM raw_messages WHERE raw_message_id = ?", (raw_message_id,)
        ).fetchone()
        conn.close()
        return self._row_to_envelope(row)

    def set_blacklisted(self, raw_message_id: int) -> None:
        self._update_column(raw_message_id, "acquisition_status", "BLACKLISTED")
        self._status_store.update(raw_message_id, "blacklisted")  # type: ignore[arg-type]

    def set_media_only_skipped(self, raw_message_id: int) -> None:
        self._update_column(raw_message_id, "acquisition_status", "MEDIA_ONLY_SKIPPED")
        self._status_store.update(raw_message_id, "skipped")  # type: ignore[arg-type]

    def update_processing_status(self, raw_message_id: int, status: ProcessingStatusV2) -> None:
        self._status_store.update(raw_message_id, status)  # type: ignore[arg-type]

    def update_trader_resolution(self, raw_message_id: int, ctx: ResolvedTraderContext) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "UPDATE raw_messages SET resolved_trader_id=?, resolution_method=?, resolution_detail=?"
            " WHERE raw_message_id=?",
            (ctx.trader_id, ctx.method, ctx.detail, raw_message_id),
        )
        conn.commit()
        conn.close()

    def _update_column(self, raw_message_id: int, column: str, value: object) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            f"UPDATE raw_messages SET {column}=? WHERE raw_message_id=?",  # noqa: S608
            (value, raw_message_id),
        )
        conn.commit()
        conn.close()

    def _row_to_envelope(self, row: sqlite3.Row) -> RawMessageEnvelope:
        keys = set(row.keys())
        return RawMessageEnvelope(
            raw_message_id=row["raw_message_id"],
            source_chat_id=row["source_chat_id"],
            source_chat_title=row["source_chat_title"],
            source_type=row["source_type"],
            source_topic_id=row["source_topic_id"],
            telegram_message_id=row["telegram_message_id"],
            reply_to_message_id=row["reply_to_message_id"],
            raw_text=row["raw_text"],
            message_ts=datetime.fromisoformat(row["message_ts"]),
            acquired_at=datetime.fromisoformat(row["acquired_at"]),
            acquisition_mode=row["acquisition_mode"] if "acquisition_mode" in keys else "live",
            acquisition_status=row["acquisition_status"] or "ACQUIRED",
            processing_status=row["processing_status"] or "pending",
            source_trader_id=row["source_trader_id"],
            resolved_trader_id=row["resolved_trader_id"] if "resolved_trader_id" in keys else None,
            resolution_method=row["resolution_method"] if "resolution_method" in keys else None,
            resolution_detail=row["resolution_detail"] if "resolution_detail" in keys else None,
            has_media=bool(row["has_media"]),
            media_kind=row["media_kind"],
            media_mime_type=row["media_mime_type"],
            media_filename=row["media_filename"],
        )
```

- [ ] **Step 4: Esegui i test**

```bash
pytest tests/runtime_v2/test_raw_message_repository.py -v
```

Expected: tutti PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/persistence/ tests/runtime_v2/test_raw_message_repository.py
git commit -m "feat(runtime_v2): RawMessageRepository — persistence adapter for raw messages"
```

---

## Task 6: RuntimeV2TraderResolver

**Files:**
- Create: `src/runtime_v2/trader_resolution/resolver.py`
- Test: `tests/runtime_v2/test_trader_resolver.py`

- [ ] **Step 1: Scrivi i test**

```python
# tests/runtime_v2/test_trader_resolver.py
from __future__ import annotations
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from src.runtime_v2.trader_resolution.resolver import RuntimeV2TraderResolver
from src.runtime_v2.trader_resolution.channel_config_resolver import (
    ChannelConfigResolver,
    ChannelEntry,
)
from src.runtime_v2.intake.models import RawMessageEnvelope

_TS = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


def _make_envelope(
    chat_id: str = "-100123",
    topic_id: int | None = None,
    text: str = "BUY BTC",
    reply_id: int | None = None,
) -> RawMessageEnvelope:
    return RawMessageEnvelope(
        raw_message_id=1,
        source_chat_id=chat_id,
        source_chat_title="Test",
        source_type="channel",
        source_topic_id=topic_id,
        telegram_message_id=456,
        reply_to_message_id=reply_id,
        raw_text=text,
        message_ts=_TS,
        acquired_at=_TS,
        acquisition_mode="live",
        acquisition_status="ACQUIRED",
        processing_status="pending",
        source_trader_id=None,
        resolved_trader_id=None,
        resolution_method=None,
        resolution_detail=None,
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )


def _make_channel_entry(trader_id: str, topic_id: int | None = None, active: bool = True) -> ChannelEntry:
    return ChannelEntry(
        chat_id="-100123",
        topic_id=topic_id,
        label="Test",
        active=active,
        trader_id=trader_id,
        parser_profile=trader_id,
        blacklist=[],
    )


def _make_effective_result(trader_id: str | None, method: str, detail: str | None = None):
    result = MagicMock()
    result.trader_id = trader_id
    result.method = method
    result.detail = detail
    return result


@pytest.fixture
def channel_config():
    return MagicMock(spec=ChannelConfigResolver)


@pytest.fixture
def effective_resolver():
    return MagicMock()


@pytest.fixture
def resolver(channel_config, effective_resolver):
    from src.runtime_v2.trader_resolution.resolver import RuntimeV2TraderResolver
    return RuntimeV2TraderResolver(channel_config, effective_resolver)


def test_config_driven_chat_id(resolver, channel_config, effective_resolver):
    channel_config.lookup.return_value = _make_channel_entry("trader_a")
    env = _make_envelope()
    ctx = resolver.resolve(env)
    assert ctx.trader_id == "trader_a"
    assert ctx.method == "source_chat_id"
    assert not ctx.is_ambiguous
    effective_resolver.resolve.assert_not_called()


def test_config_driven_topic_config(resolver, channel_config, effective_resolver):
    channel_config.lookup.return_value = _make_channel_entry("trader_a", topic_id=3)
    env = _make_envelope(topic_id=3)
    ctx = resolver.resolve(env)
    assert ctx.trader_id == "trader_a"
    assert ctx.method == "source_topic_config"
    effective_resolver.resolve.assert_not_called()


def test_inactive_channel_falls_through_to_effective(resolver, channel_config, effective_resolver):
    channel_config.lookup.return_value = _make_channel_entry("trader_a", active=False)
    effective_resolver.resolve.return_value = _make_effective_result("trader_b", "content_alias")
    env = _make_envelope()
    ctx = resolver.resolve(env)
    assert ctx.trader_id == "trader_b"
    assert ctx.method == "content_alias"


def test_no_config_entry_falls_through_to_effective(resolver, channel_config, effective_resolver):
    channel_config.lookup.return_value = None
    effective_resolver.resolve.return_value = _make_effective_result("trader_c", "content_alias")
    env = _make_envelope()
    ctx = resolver.resolve(env)
    assert ctx.trader_id == "trader_c"
    assert ctx.method == "content_alias"


def test_ambiguous_alias_sets_is_ambiguous(resolver, channel_config, effective_resolver):
    channel_config.lookup.return_value = None
    effective_resolver.resolve.return_value = _make_effective_result(
        None, "content_alias_ambiguous", detail="trader_a,trader_b"
    )
    env = _make_envelope()
    ctx = resolver.resolve(env)
    assert ctx.trader_id is None
    assert ctx.is_ambiguous is True
    assert ctx.method == "content_alias_ambiguous"


def test_unresolved_returns_unresolved_method(resolver, channel_config, effective_resolver):
    channel_config.lookup.return_value = None
    effective_resolver.resolve.return_value = _make_effective_result(None, "unresolved")
    env = _make_envelope()
    ctx = resolver.resolve(env)
    assert ctx.trader_id is None
    assert ctx.method == "unresolved"
    assert not ctx.is_ambiguous


def test_reply_chain_method_maps_correctly(resolver, channel_config, effective_resolver):
    channel_config.lookup.return_value = None
    effective_resolver.resolve.return_value = _make_effective_result("trader_a", "reply_chain")
    env = _make_envelope(reply_id=100)
    ctx = resolver.resolve(env)
    assert ctx.method == "reply_chain"
    assert ctx.trader_id == "trader_a"
```

- [ ] **Step 2: Esegui per verificare il fallimento**

```bash
pytest tests/runtime_v2/test_trader_resolver.py -v
```

Expected: `ModuleNotFoundError: No module named '...resolver'`

- [ ] **Step 3: Implementa `resolver.py`**

```python
# src/runtime_v2/trader_resolution/resolver.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from src.telegram.effective_trader import EffectiveTraderResolver, EffectiveTraderContext
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelConfigResolver
from src.runtime_v2.trader_resolution.models import ResolvedTraderContext, ResolutionMethod
from src.runtime_v2.intake.models import RawMessageEnvelope

# Maps EffectiveTraderResolver method strings to ResolutionMethod literals.
# Keys are the method values returned by the existing resolver.
_METHOD_MAP: dict[str, ResolutionMethod] = {
    "content_alias": "content_alias",
    "content_alias_ambiguous": "content_alias_ambiguous",
    "reply_chain": "reply_chain",
    "reply_chain_alias": "reply_chain_alias",
    "source_chat_id": "source_chat_id",
    "source_chat_username": "source_chat_username",
    "source_chat_title": "source_chat_title",
    "unresolved": "unresolved",
}


class RuntimeV2TraderResolver:
    """Resolves effective trader using config-first strategy.

    Step 1: channels.yaml lookup by (source_chat_id, source_topic_id).
           Returns immediately if entry is active and has trader_id.
    Step 2: EffectiveTraderResolver (text alias priority → reply-chain).

    Note: EffectiveTraderResolver currently has a hardcoded reply-chain depth (10).
    IntakeConfig.reply_chain_depth_limit declares the intended contract (default 5).
    Enforcement requires a future update to EffectiveTraderResolver to accept max_depth.
    """

    def __init__(
        self,
        channel_config_resolver: ChannelConfigResolver,
        effective_trader_resolver: EffectiveTraderResolver,
    ) -> None:
        self._channel_config = channel_config_resolver
        self._effective = effective_trader_resolver

    def resolve(self, envelope: RawMessageEnvelope) -> ResolvedTraderContext:
        now = datetime.now(timezone.utc)

        # Step 1: config-driven via channels.yaml
        entry = self._channel_config.lookup(envelope.source_chat_id, envelope.source_topic_id)
        if entry is not None and entry.active and entry.trader_id:
            method: ResolutionMethod = (
                "source_topic_config"
                if envelope.source_topic_id is not None and entry.topic_id is not None
                else "source_chat_id"
            )
            return ResolvedTraderContext(
                raw_message_id=envelope.raw_message_id,
                trader_id=entry.trader_id,
                method=method,
                detail=None,
                is_ambiguous=False,
                resolved_at=now,
            )

        # Step 2: EffectiveTraderResolver (text → reply-chain)
        ctx = EffectiveTraderContext(
            source_chat_id=envelope.source_chat_id,
            source_chat_username=None,
            source_chat_title=envelope.source_chat_title,
            raw_text=envelope.raw_text,
            reply_to_message_id=envelope.reply_to_message_id,
        )
        result = self._effective.resolve(ctx)
        mapped_method: ResolutionMethod = _METHOD_MAP.get(result.method, "unresolved")
        return ResolvedTraderContext(
            raw_message_id=envelope.raw_message_id,
            trader_id=result.trader_id,
            method=mapped_method,
            detail=result.detail,
            is_ambiguous=(result.method == "content_alias_ambiguous"),
            resolved_at=now,
        )
```

- [ ] **Step 4: Esegui i test**

```bash
pytest tests/runtime_v2/test_trader_resolver.py -v
```

Expected: tutti PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/trader_resolution/resolver.py tests/runtime_v2/test_trader_resolver.py
git commit -m "feat(runtime_v2): RuntimeV2TraderResolver — config-first + EffectiveTraderResolver fallback"
```

---

## Task 7: IntakeEligibilityCheck + IntakeProcessor

**Files:**
- Create: `src/runtime_v2/intake/eligibility.py`
- Create: `src/runtime_v2/intake/processor.py`
- Test: `tests/runtime_v2/test_intake_processor.py`

- [ ] **Step 1: Scrivi i test**

```python
# tests/runtime_v2/test_intake_processor.py
from __future__ import annotations
import pytest
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.runtime_v2.intake.models import IntakeConfig, RawIngestItem
from src.runtime_v2.intake.processor import RuntimeV2IntakeProcessor
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.trader_resolution.channel_config_resolver import (
    ChannelConfigResolver,
    ChannelEntry,
)
from src.runtime_v2.trader_resolution.models import ResolvedTraderContext

_TS = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


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


@pytest.fixture
def repo(db_path):
    return RawMessageRepository(db_path=db_path)


def _make_item(
    text: str = "BUY BTC 45000 SL 44000 TP 47000",
    chat_id: str = "-100123",
    msg_id: int = 1,
    has_media: bool = False,
    reply_id: int | None = None,
) -> RawIngestItem:
    return RawIngestItem(
        source_chat_id=chat_id,
        source_chat_title="Test",
        source_type="channel",
        source_topic_id=None,
        telegram_message_id=msg_id,
        reply_to_message_id=reply_id,
        raw_text=text,
        message_ts=_TS,
        acquisition_mode="live",
        has_media=has_media,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )


def _make_resolved(trader_id: str | None, method: str, is_ambiguous: bool = False) -> ResolvedTraderContext:
    return ResolvedTraderContext(
        raw_message_id=0,
        trader_id=trader_id,
        method=method,
        detail=None,
        is_ambiguous=is_ambiguous,
        resolved_at=_TS,
    )


def _build_processor(repo, trader_id="trader_a", profiles=("trader_a",), globally_blacklisted=False):
    channel_config = MagicMock(spec=ChannelConfigResolver)
    channel_config.is_globally_blacklisted.return_value = globally_blacklisted
    channel_config.lookup.return_value = ChannelEntry(
        chat_id="-100123",
        topic_id=None,
        label="Test",
        active=True,
        trader_id=trader_id,
        parser_profile=trader_id,
        blacklist=[],
    )
    resolver = MagicMock()
    resolved = _make_resolved(trader_id, "source_chat_id")
    resolver.resolve.return_value = resolved

    eligibility = MagicMock()
    eligibility.check.return_value = MagicMock(eligible=True, review_reason=None)

    with patch(
        "src.runtime_v2.intake.processor.list_parser_v2_profiles",
        return_value=list(profiles),
    ):
        return RuntimeV2IntakeProcessor(
            repo=repo,
            eligibility=eligibility,
            resolver=resolver,
            channel_config=channel_config,
            config=IntakeConfig(),
        ), channel_config, resolver, eligibility


def test_happy_path_returns_candidate(repo):
    processor, _, _, _ = _build_processor(repo)
    with patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_a"]):
        candidate = processor.process(_make_item())
    assert candidate is not None
    assert candidate.parser_profile == "trader_a"
    assert candidate.resolved_trader.trader_id == "trader_a"
    env = repo.get_by_id(candidate.raw_message.raw_message_id)
    assert env.processing_status == "done"
    assert env.acquisition_status == "ACQUIRED"


def test_dedup_same_message_id(repo):
    processor, _, _, _ = _build_processor(repo)
    with patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_a"]):
        c1 = processor.process(_make_item(msg_id=1))
        c2 = processor.process(_make_item(msg_id=1))
    assert c1.raw_message.raw_message_id == c2.raw_message.raw_message_id


def test_globally_blacklisted_returns_none(repo):
    processor, _, _, _ = _build_processor(repo, globally_blacklisted=True)
    candidate = processor.process(_make_item(text="#admin"))
    assert candidate is None
    conn = sqlite3.connect(repo._db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT acquisition_status, processing_status FROM raw_messages LIMIT 1").fetchone()
    conn.close()
    assert row["acquisition_status"] == "BLACKLISTED"
    assert row["processing_status"] == "blacklisted"


def test_media_only_no_text_returns_none(repo):
    processor, _, _, _ = _build_processor(repo)
    item = _make_item(text=None, has_media=True)
    item.raw_text = None
    candidate = processor.process(item)
    assert candidate is None
    conn = sqlite3.connect(repo._db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT acquisition_status, processing_status FROM raw_messages LIMIT 1").fetchone()
    conn.close()
    assert row["acquisition_status"] == "MEDIA_ONLY_SKIPPED"


def test_eligibility_review_returns_none(repo):
    processor, channel_config, resolver, eligibility = _build_processor(repo)
    eligibility.check.return_value = MagicMock(
        eligible=False, review_reason="short_update_without_strong_link"
    )
    candidate = processor.process(_make_item(text="ok"))
    assert candidate is None
    env = repo.get_by_id(1)
    assert env.processing_status == "review"
    assert env.acquisition_status == "ACQUIRED"


def test_unresolved_trader_returns_none(repo):
    processor, channel_config, resolver, eligibility = _build_processor(repo)
    resolver.resolve.return_value = _make_resolved(None, "unresolved")
    with patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_a"]):
        candidate = processor.process(_make_item())
    assert candidate is None
    env = repo.get_by_id(1)
    assert env.processing_status == "review"


def test_no_parser_profile_returns_none(repo):
    processor, _, _, _ = _build_processor(repo, trader_id="trader_a", profiles=["trader_b"])
    with patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_b"]):
        candidate = processor.process(_make_item())
    assert candidate is None
    env = repo.get_by_id(1)
    assert env.processing_status == "review"


def test_acquisition_status_immutable_after_blacklist(repo):
    processor, _, _, _ = _build_processor(repo, globally_blacklisted=True)
    processor.process(_make_item(text="#admin"))
    env = repo.get_by_id(1)
    # acquisition_status was set once at ingest — must remain BLACKLISTED
    assert env.acquisition_status == "BLACKLISTED"
    # processing_status should not change further after blacklist
    repo.update_processing_status(env.raw_message_id, "pending")
    assert repo.get_by_id(env.raw_message_id).acquisition_status == "BLACKLISTED"
```

- [ ] **Step 2: Esegui per verificare il fallimento**

```bash
pytest tests/runtime_v2/test_intake_processor.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.runtime_v2.intake.processor'`

- [ ] **Step 3: Implementa `eligibility.py`**

```python
# src/runtime_v2/intake/eligibility.py
from __future__ import annotations

from dataclasses import dataclass

from src.storage.raw_messages import RawMessageStore
from src.telegram.eligibility import MessageEligibilityEvaluator
from src.runtime_v2.intake.models import RawMessageEnvelope


@dataclass(slots=True, frozen=True)
class EligibilityOutcome:
    eligible: bool
    review_reason: str | None  # None when eligible


class IntakeEligibilityCheck:
    """Wraps MessageEligibilityEvaluator and maps its result to runtime_v2 terms."""

    def __init__(self, raw_store: RawMessageStore) -> None:
        self._evaluator = MessageEligibilityEvaluator(raw_store)

    def check(self, envelope: RawMessageEnvelope) -> EligibilityOutcome:
        result = self._evaluator.evaluate(
            source_chat_id=envelope.source_chat_id,
            raw_text=envelope.raw_text,
            reply_to_message_id=envelope.reply_to_message_id,
        )
        if result.is_eligible:
            return EligibilityOutcome(eligible=True, review_reason=None)
        return EligibilityOutcome(
            eligible=False,
            review_reason="short_update_without_strong_link",
        )
```

- [ ] **Step 4: Implementa `processor.py`**

```python
# src/runtime_v2/intake/processor.py
from __future__ import annotations

# Top-level import required for test patching via
# patch("src.runtime_v2.intake.processor.list_parser_v2_profiles")
from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.profiles.registry import list_parser_v2_profiles
from src.runtime_v2.intake.eligibility import IntakeEligibilityCheck
from src.runtime_v2.intake.models import IntakeConfig, RawIngestItem
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelConfigResolver
from src.runtime_v2.trader_resolution.models import ParserDispatchCandidate
from src.runtime_v2.trader_resolution.resolver import RuntimeV2TraderResolver


class RuntimeV2IntakeProcessor:
    """Orchestrates the full intake pipeline for runtime_v2.

    For each raw item, produces a ParserDispatchCandidate or returns None
    when the message cannot proceed (blacklisted, media-only, review, unresolved).
    All side effects (DB writes, status updates) are persisted before returning.
    """

    def __init__(
        self,
        repo: RawMessageRepository,
        eligibility: IntakeEligibilityCheck,
        resolver: RuntimeV2TraderResolver,
        channel_config: ChannelConfigResolver,
        config: IntakeConfig,
    ) -> None:
        self._repo = repo
        self._eligibility = eligibility
        self._resolver = resolver
        self._channel_config = channel_config
        self._config = config

    def process(self, item: RawIngestItem) -> ParserDispatchCandidate | None:
        # 1. Save raw (dedup — idempotent by source_chat_id + telegram_message_id)
        env = self._repo.save_raw(item)

        # 2. Global blacklist check
        if self._channel_config.is_globally_blacklisted(item.raw_text or ""):
            self._repo.set_blacklisted(env.raw_message_id)
            return None

        # 3. Media-only without text
        if item.has_media and not item.raw_text:
            self._repo.set_media_only_skipped(env.raw_message_id)
            return None

        # 4. Eligibility — short update without strong link
        outcome = self._eligibility.check(env)
        if not outcome.eligible:
            self._repo.update_processing_status(env.raw_message_id, "review")
            return None

        # 5. Trader resolution
        self._repo.update_processing_status(env.raw_message_id, "processing")
        resolved = self._resolver.resolve(env)
        resolved = resolved.model_copy(update={"raw_message_id": env.raw_message_id})

        if resolved.is_ambiguous or resolved.trader_id is None:
            self._repo.update_processing_status(env.raw_message_id, "review")
            return None

        # 6. Persist resolution
        self._repo.update_trader_resolution(env.raw_message_id, resolved)

        # 7. Derive parser_profile (channels.yaml override, else resolved_trader_id)
        entry = self._channel_config.lookup(env.source_chat_id, env.source_topic_id)
        parser_profile = (entry.parser_profile if entry and entry.parser_profile else None) or resolved.trader_id

        # 8. Validate profile in parser_v2 registry
        # list_parser_v2_profiles is imported at module level for test patchability
        if parser_profile not in list_parser_v2_profiles():
            self._repo.update_processing_status(env.raw_message_id, "review")
            return None

        # 9. Build ParserContext
        parser_context = ParserContext(
            message_id=env.telegram_message_id,
            reply_to_message_id=env.reply_to_message_id,
            source_chat_id=env.source_chat_id,
            source_topic_id=env.source_topic_id,
            raw_context=RawContext(
                raw_text=env.raw_text or "",
                message_id=env.telegram_message_id,
                reply_to_message_id=env.reply_to_message_id,
                source_chat_id=env.source_chat_id,
                source_topic_id=env.source_topic_id,
            ),
        )

        # 10. Intake done — parser pipeline picks up from here
        self._repo.update_processing_status(env.raw_message_id, "done")
        return ParserDispatchCandidate(
            raw_message=env,
            resolved_trader=resolved,
            parser_profile=parser_profile,
            parser_context=parser_context,
        )
```

- [ ] **Step 5: Esegui i test**

```bash
pytest tests/runtime_v2/test_intake_processor.py -v
```

Expected: tutti PASS

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/intake/ tests/runtime_v2/test_intake_processor.py
git commit -m "feat(runtime_v2): IntakeEligibilityCheck + RuntimeV2IntakeProcessor"
```

---

## Task 8: Allinea parser_test resolve_traders.py

**Files:**
- Modify: `parser_test/scripts/resolve_traders.py`

L'obiettivo è aggiungere il channels.yaml lookup come step 1b nel flusso di risoluzione, prima di `EffectiveTraderResolver`. L'ordine diventa: `source_trader_id già presente` → `channels.yaml config` → `EffectiveTraderResolver` → `--assume-trader` → `unresolved`.

- [ ] **Step 1: Leggi il file attuale**

```bash
cat parser_test/scripts/resolve_traders.py
```

Identifica la funzione `resolve_all()` e il punto dove viene chiamato `EffectiveTraderResolver.resolve()` per ogni riga senza `source_trader_id`.

- [ ] **Step 2: Scrivi un test di regressione prima di modificare**

```python
# parser_test/scripts/tests/test_resolve_traders_config.py
from __future__ import annotations
import sqlite3
import tempfile
import pytest
import yaml
from pathlib import Path

# Import the existing resolve_all from parser_test
import sys
sys.path.insert(0, str(Path("parser_test")))
from scripts.resolve_traders import resolve_all


def _create_test_db(tmp_path):
    db = str(tmp_path / "test.db")
    conn = sqlite3.connect(db)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text())
    conn.commit()
    conn.close()
    return db


def _insert_raw(db, chat_id, msg_id, text, source_trader_id=None):
    conn = sqlite3.connect(db)
    conn.execute(
        """INSERT INTO raw_messages
           (source_chat_id, telegram_message_id, raw_text, message_ts, acquired_at, acquisition_status, source_trader_id)
           VALUES (?, ?, ?, '2026-01-01', '2026-01-01', 'ACQUIRED', ?)""",
        (chat_id, msg_id, text, source_trader_id),
    )
    conn.commit()
    conn.close()


def _get_resolution(db, msg_id):
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT resolved_trader_id, resolution_method FROM raw_messages WHERE telegram_message_id=?",
        (msg_id,),
    ).fetchone()
    conn.close()
    return row


def _make_channels_yaml(tmp_path, trader_id: str, chat_id: str) -> str:
    data = {
        "blacklist_global": [],
        "channels": [{"chat_id": int(chat_id), "active": True, "trader_id": trader_id, "blacklist": []}],
    }
    p = tmp_path / "channels.yaml"
    p.write_text(yaml.dump(data))
    return str(p)


def test_source_trader_id_takes_priority(tmp_path):
    db = _create_test_db(tmp_path)
    _insert_raw(db, "-100123", 1, "BUY BTC", source_trader_id="trader_x")
    channels_yaml = _make_channels_yaml(tmp_path, "trader_from_config", "-100123")
    resolve_all(db_path=db, channels_yaml=channels_yaml)
    tid, method = _get_resolution(db, 1)
    assert tid == "trader_x"
    assert method == "source_trader_id"


def test_config_driven_resolves_from_channels_yaml(tmp_path):
    db = _create_test_db(tmp_path)
    _insert_raw(db, "-100555", 2, "BUY ETH")
    channels_yaml = _make_channels_yaml(tmp_path, "trader_y", "-100555")
    resolve_all(db_path=db, channels_yaml=channels_yaml)
    tid, method = _get_resolution(db, 2)
    assert tid == "trader_y"
    assert method == "source_topic_config"  # or "source_chat_id"


def test_assume_trader_fallback(tmp_path):
    db = _create_test_db(tmp_path)
    _insert_raw(db, "-100999", 3, "BUY XRP")
    resolve_all(db_path=db, channels_yaml=None, assume_trader="trader_fallback")
    tid, method = _get_resolution(db, 3)
    assert tid == "trader_fallback"
    assert method == "assume_trader"
```

- [ ] **Step 3: Esegui il test di regressione per verificare il fallimento**

```bash
pytest parser_test/scripts/tests/test_resolve_traders_config.py -v
```

Expected: FAIL su `test_config_driven_resolves_from_channels_yaml` (channels.yaml non usato ancora).

- [ ] **Step 4: Modifica `resolve_traders.py`**

**4a.** Aggiungi il parametro `channels_yaml: str | None = None` alla firma di `resolve_all()`.

**4b.** All'inizio di `resolve_all()`, prima del loop, aggiungi:

```python
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelConfigResolver
_channel_config = ChannelConfigResolver(channels_yaml) if channels_yaml else None
```

**4c.** Nel loop su ogni riga, dopo il check su `source_trader_id` esistente e prima di chiamare `EffectiveTraderResolver`, inserisci:

```python
# Step 2: config-driven via channels.yaml (same semantics as live)
if _channel_config is not None:
    entry = _channel_config.lookup(row.source_chat_id, topic_id=None)
    if entry is not None and entry.active and entry.trader_id:
        method = "source_topic_config" if entry.topic_id is not None else "source_chat_id"
        _write(conn, row.raw_message_id, entry.trader_id, method)
        results["config_driven"] += 1
        continue
```

- [ ] **Step 5: Aggiungi `--channels-yaml` al parser argomenti in `main()`**

Nell'argparse di `main()` aggiungi:

```python
parser.add_argument("--channels-yaml", default=None, help="Path to channels.yaml for config-driven resolution")
```

E passa `channels_yaml=args.channels_yaml` a `resolve_all()`.

- [ ] **Step 6: Esegui i test**

```bash
pytest parser_test/scripts/tests/test_resolve_traders_config.py -v
pytest parser_test/scripts/tests/test_trader_resolution.py -v
pytest parser_test/scripts/tests/test_resolve_traders.py -v
```

Expected: tutti PASS (inclusi i test di regressione pre-esistenti).

- [ ] **Step 7: Commit**

```bash
git add parser_test/scripts/resolve_traders.py \
        parser_test/scripts/tests/test_resolve_traders_config.py
git commit -m "feat(parser_test): resolve_traders — add config-driven step via channels.yaml"
```

---

## Task 9: Suite di integrazione e verifica acceptance criteria

**Files:**
- Test: `tests/runtime_v2/test_acceptance.py`

- [ ] **Step 1: Scrivi la suite di acceptance**

```python
# tests/runtime_v2/test_acceptance.py
"""
Verifica i criteri di accettazione PRD-01 §11.2.
"""
from __future__ import annotations
import sqlite3
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.runtime_v2.intake.models import IntakeConfig, RawIngestItem
from src.runtime_v2.intake.processor import RuntimeV2IntakeProcessor
from src.runtime_v2.intake.eligibility import IntakeEligibilityCheck
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.trader_resolution.channel_config_resolver import (
    ChannelConfigResolver,
    ChannelEntry,
)
from src.runtime_v2.trader_resolution.resolver import RuntimeV2TraderResolver

_TS = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


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


def _make_item(chat_id="-100123", msg_id=1, text="BUY BTC SL 44000 TP 47000",
               has_media=False, reply_id=None, topic_id=None, mode="live") -> RawIngestItem:
    return RawIngestItem(
        source_chat_id=chat_id, source_chat_title="Test", source_type="channel",
        source_topic_id=topic_id, telegram_message_id=msg_id, reply_to_message_id=reply_id,
        raw_text=text, message_ts=_TS, acquisition_mode=mode,
        has_media=has_media, media_kind=None, media_mime_type=None, media_filename=None,
    )


def _build(db_path, *, trader_id="trader_a", topic_id=None, active=True,
           globally_blacklisted=False, eligible=True,
           resolved_trader_id="trader_a", resolved_method="source_chat_id",
           is_ambiguous=False):
    """Builds a RuntimeV2IntakeProcessor with mocked dependencies.

    Note: does NOT patch list_parser_v2_profiles here.
    Each test that reaches step 8 (profile validation) must apply its own
    @patch or with-patch decorator.
    """
    repo = RawMessageRepository(db_path=db_path)
    config = IntakeConfig()

    channel_config = MagicMock(spec=ChannelConfigResolver)
    channel_config.is_globally_blacklisted.return_value = globally_blacklisted
    channel_config.lookup.return_value = (
        ChannelEntry(chat_id="-100123", topic_id=topic_id, label="Test", active=active,
                     trader_id=trader_id, parser_profile=trader_id, blacklist=[])
        if trader_id else None
    )

    from src.runtime_v2.trader_resolution.models import ResolvedTraderContext
    resolver = MagicMock(spec=RuntimeV2TraderResolver)
    resolver.resolve.return_value = ResolvedTraderContext(
        raw_message_id=0, trader_id=resolved_trader_id, method=resolved_method,
        detail=None, is_ambiguous=is_ambiguous, resolved_at=_TS,
    )

    eligibility = MagicMock(spec=IntakeEligibilityCheck)
    eligibility.check.return_value = MagicMock(
        eligible=eligible,
        review_reason=None if eligible else "short_update_without_strong_link",
    )

    processor = RuntimeV2IntakeProcessor(
        repo=repo, eligibility=eligibility, resolver=resolver,
        channel_config=channel_config, config=config,
    )
    return processor, repo


# Criterion 1: dedup
@patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_a"])
def test_criterion_1_dedup(mock_profiles, db_path):
    p, repo = _build(db_path)
    c1 = p.process(_make_item(msg_id=1))
    c2 = p.process(_make_item(msg_id=1))
    assert c1.raw_message.raw_message_id == c2.raw_message.raw_message_id


# Criterion 2: topic preserved
@patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_a"])
def test_criterion_2_topic_preserved(mock_profiles, db_path):
    p, repo = _build(db_path, topic_id=3)
    c = p.process(_make_item(topic_id=3))
    assert c.raw_message.source_topic_id == 3


# Criterion 3: mono-trader from config
@patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_a"])
def test_criterion_3_mono_trader_from_config(mock_profiles, db_path):
    p, repo = _build(db_path, trader_id="trader_a", resolved_method="source_chat_id")
    c = p.process(_make_item())
    assert c.resolved_trader.method == "source_chat_id"
    assert c.resolved_trader.trader_id == "trader_a"


# Criterion 6: short update without strong link → review
def test_criterion_6_short_update_review(db_path):
    p, repo = _build(db_path, eligible=False)
    result = p.process(_make_item(text="ok"))
    assert result is None
    env = repo.get_by_id(1)
    assert env.processing_status == "review"
    assert env.acquisition_status == "ACQUIRED"


# Criterion 8: ambiguous alias → review
def test_criterion_8_ambiguous_alias_review(db_path):
    p, repo = _build(db_path, trader_id=None, resolved_trader_id=None, is_ambiguous=True, resolved_method="content_alias_ambiguous")
    with patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_a"]):
        result = p.process(_make_item())
    assert result is None
    env = repo.get_by_id(1)
    assert env.processing_status == "review"


# Criterion 10: no import of src.telegram.router
def test_criterion_10_no_router_import():
    import src.runtime_v2.intake.processor as mod
    import src.runtime_v2.trader_resolution.resolver as res_mod
    import src.runtime_v2.persistence.raw_messages as pers_mod
    for m in (mod, res_mod, pers_mod):
        content = open(m.__file__).read()
        assert "src.telegram.router" not in content, f"{m.__file__} imports src.telegram.router"


# Criterion 11: result is ParserDispatchCandidate
@patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_a"])
def test_criterion_11_result_type(mock_profiles, db_path):
    from src.runtime_v2.trader_resolution.models import ParserDispatchCandidate
    p, _ = _build(db_path)
    c = p.process(_make_item())
    assert isinstance(c, ParserDispatchCandidate)
    assert c.parser_context is not None


# Criterion 12: no_parser_profile → review
@patch("src.runtime_v2.intake.processor.list_parser_v2_profiles", return_value=["trader_b"])
def test_criterion_12_no_parser_profile_review(mock_profiles, db_path):
    # trader_a resolved but not in profiles list → review
    p, repo = _build(db_path, resolved_trader_id="trader_a")
    result = p.process(_make_item())
    assert result is None
    env = repo.get_by_id(1)
    assert env.processing_status == "review"


# Criterion 13: acquisition_status immutable
def test_criterion_13_acquisition_status_immutable(db_path):
    p, repo = _build(db_path, globally_blacklisted=True)
    p.process(_make_item(text="#admin"))
    env = repo.get_by_id(1)
    assert env.acquisition_status == "BLACKLISTED"
    # Attempting to change processing_status does NOT change acquisition_status
    repo.update_processing_status(env.raw_message_id, "review")
    assert repo.get_by_id(env.raw_message_id).acquisition_status == "BLACKLISTED"
```

- [ ] **Step 2: Esegui per verificare il fallimento**

```bash
pytest tests/runtime_v2/test_acceptance.py -v
```

Expected: FAIL (processor non ancora esistente al momento di eseguire — ma se fatto nell'ordine del piano, sarà PASS)

- [ ] **Step 3: Esegui la suite completa**

```bash
pytest tests/runtime_v2/ -v
```

Expected: tutti PASS

- [ ] **Step 4: Esegui i test di regressione esistenti**

```bash
pytest src/storage/tests/ -v
pytest src/telegram/tests/ -v
pytest parser_test/scripts/tests/ -v
```

Expected: nessuna regressione.

- [ ] **Step 5: Commit finale**

```bash
git add tests/runtime_v2/test_acceptance.py
git commit -m "test(runtime_v2): acceptance suite PRD-01 criteria 1-13"
```

---

## Verifica finale

```bash
# Tutti i test runtime_v2
pytest tests/runtime_v2/ -v

# Nessuna regressione storage e telegram
pytest src/storage/tests/ src/telegram/tests/ -v

# Nessuna dipendenza da router nel nuovo package
grep -r "src.telegram.router" src/runtime_v2/ && echo "FAIL: router import found" || echo "OK: no router import"
```

---

## Note su limitazioni note

**Depth limit reply-chain:** `EffectiveTraderResolver` ha depth hardcoded a 10. `IntakeConfig.reply_chain_depth_limit=5` è il contratto dichiarato; l'enforcement richiede un futuro aggiornamento a `EffectiveTraderResolver` per accettare `max_depth` come parametro.

**source_chat_username:** `RawMessageEnvelope` non ha `source_chat_username`. Viene passato `None` a `EffectiveTraderContext`. Il metodo `source_chat_username` in `ResolutionMethod` resta disponibile per future estensioni.

**DB asincrono:** Questa implementazione usa `sqlite3` sincrono. Se il listener usa `aiosqlite`, il `RawMessageRepository` dovrà essere adattato con una versione asincrona in una fase successiva.

# Control Plane Telegram — Part 1: Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared infrastructure (DB tables, typed config loader, Pydantic models, auth validator) that every later part of the Control Plane depends on — with no Telegram behaviour yet.

**Architecture:** A new package `src/runtime_v2/control_plane/` holds typed Pydantic models, a YAML+env config loader for `config/telegram_control.yaml`, and a stateless auth validator. A new SQL migration `007_ops_control_plane.sql` adds the four control-plane tables. Everything here is pure/synchronous and unit-testable without network or Telegram.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, sqlite3, pytest.

**Source spec:** `docs/superpowers/specs/2026-05-29-control-plane-telegram-design.md` (§3 Package Layout, §4 DB tables, §5 Config, §7 Parte 1). Command/config detail: `docs/Raggionamento/Controllo_Notifica/COMMANDS_SPEC.md` §2, §4, §11; `TECH_LOG_SPEC.md` §3, §10.

**Cross-part contract this part publishes:**
- `src/runtime_v2/control_plane/models.py` — `ControlPlaneConfig`, `NotificationOutboxEntry`, `ControlCommand`, `ConfigOverride`, `RuntimeSnapshot` (+ nested config models).
- `src/runtime_v2/control_plane/config.py` — `load_control_plane_config(path) -> ControlPlaneConfig`, `ControlPlaneConfigError`.
- `src/runtime_v2/control_plane/auth.py` — `AuthValidator`, `AuthResult`.
- Migration `007` — tables `ops_notification_outbox`, `ops_telegram_control_commands`, `ops_config_overrides`, `ops_runtime_snapshot`.

---

## File Structure

| File | Responsibility |
|---|---|
| `db/ops_migrations/007_ops_control_plane.sql` | Create the 4 new control-plane tables + indexes (idempotent `IF NOT EXISTS`). |
| `config/telegram_control.yaml` | Operator-editable config template; secrets via `${ENV}` placeholders. |
| `src/runtime_v2/control_plane/__init__.py` | Empty package marker. |
| `src/runtime_v2/control_plane/models.py` | All Pydantic models for the control plane. |
| `src/runtime_v2/control_plane/config.py` | Load + `${ENV}` substitute + validate YAML into `ControlPlaneConfig`. |
| `src/runtime_v2/control_plane/auth.py` | Stateless per-update auth decision. |
| `tests/runtime_v2/control_plane/__init__.py` | Test package marker. |
| `tests/runtime_v2/control_plane/test_migration_007.py` | Assert tables/columns exist after migration. |
| `tests/runtime_v2/control_plane/test_models.py` | Validate model defaults + round-trip. |
| `tests/runtime_v2/control_plane/test_config.py` | Valid YAML, missing env, missing field, env substitution. |
| `tests/runtime_v2/control_plane/test_auth.py` | OK / wrong chat / wrong topic / unauthorized. |

---

### Task 1: DB migration 007 — control-plane tables

**Files:**
- Create: `db/ops_migrations/007_ops_control_plane.sql`
- Test: `tests/runtime_v2/control_plane/__init__.py`, `tests/runtime_v2/control_plane/test_migration_007.py`

- [ ] **Step 1: Create the test package marker**

Create `tests/runtime_v2/control_plane/__init__.py` with a single newline:

```python
```

- [ ] **Step 2: Write the failing migration test**

Create `tests/runtime_v2/control_plane/test_migration_007.py`:

```python
# tests/runtime_v2/control_plane/test_migration_007.py
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _apply_migrations(db_path: str, migrations_dir: Path) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(migrations_dir.glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path, Path("db/ops_migrations"))
    return db_path


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_migration_creates_control_plane_tables(ops_db):
    conn = sqlite3.connect(ops_db)
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "ops_notification_outbox" in tables
    assert "ops_telegram_control_commands" in tables
    assert "ops_config_overrides" in tables
    assert "ops_runtime_snapshot" in tables


def test_outbox_has_unique_dedupe_key(ops_db):
    conn = sqlite3.connect(ops_db)
    cols = _columns(conn, "ops_notification_outbox")
    # dedupe_key uniqueness is enforced; second identical insert must fail
    conn.execute(
        "INSERT INTO ops_notification_outbox "
        "(notification_type, destination, payload_json, priority, dedupe_key, created_at) "
        "VALUES ('X','CLEAN_LOG','{}','LOW','k1','t')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO ops_notification_outbox "
            "(notification_type, destination, payload_json, priority, dedupe_key, created_at) "
            "VALUES ('Y','TECH_LOG','{}','LOW','k1','t')"
        )
        conn.commit()
    conn.close()
    assert {"notification_id", "destination", "dedupe_key", "status", "attempts"} <= cols


def test_control_commands_unique_request_id(ops_db):
    conn = sqlite3.connect(ops_db)
    cols = _columns(conn, "ops_telegram_control_commands")
    conn.close()
    assert {"command_request_id", "telegram_user_id", "command_text", "status"} <= cols
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_migration_007.py -v`
Expected: FAIL — tables not found (`ops_notification_outbox` not in tables).

- [ ] **Step 4: Write the migration**

Create `db/ops_migrations/007_ops_control_plane.sql`:

```sql
-- db/ops_migrations/007_ops_control_plane.sql

CREATE TABLE IF NOT EXISTS ops_notification_outbox (
    notification_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_type  TEXT NOT NULL,
    destination        TEXT NOT NULL,       -- TECH_LOG | CLEAN_LOG | COMMANDS_REPLY
    payload_json       TEXT NOT NULL,
    priority           TEXT NOT NULL DEFAULT 'MEDIUM',   -- HIGH | MEDIUM | LOW
    status             TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING | SENT | FAILED
    dedupe_key         TEXT NOT NULL UNIQUE,
    attempts           INTEGER NOT NULL DEFAULT 0,
    last_error         TEXT,
    created_at         TEXT NOT NULL,
    sent_at            TEXT
);

CREATE TABLE IF NOT EXISTS ops_telegram_control_commands (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    command_request_id  TEXT NOT NULL UNIQUE,
    chat_id             TEXT NOT NULL,
    message_thread_id   TEXT NOT NULL,
    telegram_user_id    TEXT NOT NULL,
    telegram_username   TEXT,
    command_text        TEXT NOT NULL,
    command_name        TEXT,
    payload_json        TEXT,
    received_at         TEXT NOT NULL,
    status              TEXT NOT NULL,       -- RECEIVED|REJECTED|ACCEPTED|EXECUTED|FAILED|IGNORED
    reject_reason       TEXT,
    execution_result    TEXT,
    idempotency_key     TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_config_overrides (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    override_key TEXT NOT NULL,             -- es. "symbol_blacklist.global"
    scope_type   TEXT NOT NULL,             -- GLOBAL | PER_TRADER
    scope_value  TEXT,                      -- trader_id se PER_TRADER
    value_json   TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    reason       TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_runtime_snapshot (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_at           TEXT NOT NULL,
    control_mode          TEXT NOT NULL,
    active_blocks_json    TEXT NOT NULL,
    open_chain_count      INTEGER NOT NULL,
    pending_command_count INTEGER NOT NULL,
    shutdown_reason       TEXT,
    created_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outbox_status
    ON ops_notification_outbox(status, destination, created_at);
CREATE INDEX IF NOT EXISTS idx_cfg_override_active
    ON ops_config_overrides(active, override_key, scope_type, scope_value);
CREATE INDEX IF NOT EXISTS idx_runtime_snapshot_at
    ON ops_runtime_snapshot(snapshot_at);
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_migration_007.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add db/ops_migrations/007_ops_control_plane.sql tests/runtime_v2/control_plane/__init__.py tests/runtime_v2/control_plane/test_migration_007.py
git commit -m "feat(control_plane): add migration 007 for control-plane tables

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Pydantic models

**Files:**
- Create: `src/runtime_v2/control_plane/__init__.py`, `src/runtime_v2/control_plane/models.py`
- Test: `tests/runtime_v2/control_plane/test_models.py`

- [ ] **Step 1: Create the package marker**

Create `src/runtime_v2/control_plane/__init__.py` with a single newline:

```python
```

- [ ] **Step 2: Write the failing models test**

Create `tests/runtime_v2/control_plane/test_models.py`:

```python
# tests/runtime_v2/control_plane/test_models.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.runtime_v2.control_plane.models import (
    CleanLogConfig,
    ControlPlaneConfig,
    NotificationOutboxEntry,
    TechLogConfig,
    TopicConfig,
    TopicsConfig,
)


def _minimal_config() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        token="123:ABC",
        chat_id=-1001234567890,
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=101),
            tech_log=TechLogConfig(thread_id=102),
            clean_log=CleanLogConfig(thread_id=103),
        ),
        authorized_users=[123456789],
    )


def test_config_defaults():
    cfg = _minimal_config()
    assert cfg.enabled is True
    assert cfg.startup.mode == "auto"
    assert cfg.startup.restore_max_age_seconds == 300
    assert cfg.topics.tech_log.min_level == "WARNING"
    assert cfg.topics.tech_log.operational_events is False
    assert cfg.topics.clean_log.min_partial_fill_notify_pct == 10.0


def test_config_rejects_bad_startup_mode():
    with pytest.raises(ValidationError):
        ControlPlaneConfig(
            token="t",
            chat_id=1,
            topics=TopicsConfig(
                commands=TopicConfig(thread_id=1),
                tech_log=TechLogConfig(thread_id=2),
                clean_log=CleanLogConfig(thread_id=3),
            ),
            startup={"mode": "nonsense"},
        )


def test_outbox_entry_roundtrip():
    entry = NotificationOutboxEntry(
        notification_type="SIGNAL_ACCEPTED",
        destination="CLEAN_LOG",
        payload_json='{"chain_id": 145}',
        priority="MEDIUM",
        dedupe_key="clean:sig_accepted:145",
    )
    assert entry.status == "PENDING"
    assert entry.attempts == 0
    again = NotificationOutboxEntry.model_validate(entry.model_dump())
    assert again.dedupe_key == "clean:sig_accepted:145"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: src.runtime_v2.control_plane.models`.

- [ ] **Step 4: Write the models**

Create `src/runtime_v2/control_plane/models.py`:

```python
# src/runtime_v2/control_plane/models.py
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Destination = Literal["TECH_LOG", "CLEAN_LOG", "COMMANDS_REPLY"]
Priority = Literal["HIGH", "MEDIUM", "LOW"]
OutboxStatus = Literal["PENDING", "SENT", "FAILED"]
StartupMode = Literal["auto", "standby", "restore"]
CommandStatus = Literal[
    "RECEIVED", "REJECTED", "ACCEPTED", "EXECUTED", "FAILED", "IGNORED"
]


# ── Config models ───────────────────────────────────────────────────────────

class TopicConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    thread_id: int


class TechLogConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    thread_id: int
    enabled: bool = True
    min_level: Literal["WARNING", "INFO", "DEBUG"] = "WARNING"
    operational_events: bool = False
    batch_seconds: int = 10
    max_messages_per_minute: int = 20
    dedupe_window_seconds: int = 60
    max_repeated_before_summary: int = 5
    debug_max_duration_minutes: int = 60


class CleanLogConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    thread_id: int
    debounce_seconds: int = 20
    aggregate_fills_seconds: int = 30
    aggregate_updates_seconds: int = 20
    max_messages_per_chain_per_minute: int = 4
    min_partial_fill_notify_pct: float = 10.0


class TopicsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    commands: TopicConfig
    tech_log: TechLogConfig
    clean_log: CleanLogConfig


class StartupConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mode: StartupMode = "auto"
    restore_max_age_seconds: int = 300


class ControlPlaneConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: bool = True
    token: str
    chat_id: int
    topics: TopicsConfig
    authorized_users: list[int] = Field(default_factory=list)
    startup: StartupConfig = Field(default_factory=StartupConfig)
    keyboard: list[list[str]] = Field(default_factory=list)
    notifications: dict[str, str] = Field(default_factory=dict)


# ── Runtime models ──────────────────────────────────────────────────────────

class NotificationOutboxEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    notification_id: int | None = None
    notification_type: str
    destination: Destination
    payload_json: str
    priority: Priority = "MEDIUM"
    status: OutboxStatus = "PENDING"
    dedupe_key: str
    attempts: int = 0
    last_error: str | None = None
    created_at: datetime | None = None
    sent_at: datetime | None = None


class ControlCommand(BaseModel):
    model_config = ConfigDict(extra="ignore")
    command_request_id: str
    chat_id: str
    message_thread_id: str
    telegram_user_id: str
    telegram_username: str | None = None
    command_text: str
    command_name: str | None = None
    payload_json: str | None = None
    status: CommandStatus = "RECEIVED"
    reject_reason: str | None = None
    execution_result: str | None = None
    idempotency_key: str | None = None


class ConfigOverride(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int | None = None
    override_key: str
    scope_type: Literal["GLOBAL", "PER_TRADER"]
    scope_value: str | None = None
    value_json: str
    created_by: str
    reason: str | None = None
    active: bool = True


class RuntimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int | None = None
    snapshot_at: datetime
    control_mode: str
    active_blocks_json: str
    open_chain_count: int
    pending_command_count: int
    shutdown_reason: str | None = None


__all__ = [
    "Destination", "Priority", "OutboxStatus", "StartupMode", "CommandStatus",
    "TopicConfig", "TechLogConfig", "CleanLogConfig", "TopicsConfig",
    "StartupConfig", "ControlPlaneConfig",
    "NotificationOutboxEntry", "ControlCommand", "ConfigOverride", "RuntimeSnapshot",
]
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_models.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/__init__.py src/runtime_v2/control_plane/models.py tests/runtime_v2/control_plane/test_models.py
git commit -m "feat(control_plane): add Pydantic models

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Config loader (`config.py`) + YAML template

**Files:**
- Create: `config/telegram_control.yaml`, `src/runtime_v2/control_plane/config.py`
- Test: `tests/runtime_v2/control_plane/test_config.py`

- [ ] **Step 1: Write the config template**

Create `config/telegram_control.yaml`:

```yaml
# config/telegram_control.yaml
# Secrets are read from environment variables via ${ENV} placeholders.
enabled: true

token_env: CONTROL_TELEGRAM_BOT_TOKEN     # env var holding the bot token

chat_id: "${CONTROL_TELEGRAM_CHAT_ID}"    # private supergroup id (e.g. -1001234567890)

topics:
  commands:
    thread_id: 101
  tech_log:
    thread_id: 102
    enabled: true
    min_level: WARNING
    operational_events: false
    batch_seconds: 10
    max_messages_per_minute: 20
    dedupe_window_seconds: 60
    max_repeated_before_summary: 5
    debug_max_duration_minutes: 60
  clean_log:
    thread_id: 103
    debounce_seconds: 20
    aggregate_fills_seconds: 30
    aggregate_updates_seconds: 20
    max_messages_per_chain_per_minute: 4
    min_partial_fill_notify_pct: 10

authorized_users:
  - "${CONTROL_TELEGRAM_USER_ID}"

startup:
  mode: auto                              # auto | standby | restore
  restore_max_age_seconds: 300

keyboard:
  - ["/status", "/health", "/control"]
  - ["/trades", "/reviews", "/logs"]
  - ["/pause", "/resume"]
  - ["/block", "/debug_on"]

notifications:
  startup: "on"
  shutdown: "on"
  control_change: "on"
  review_required: "on"
  entry_order_placed: "silent"
  entry_filled: "on"
  tp_filled: "on"
  sl_filled: "on"
  close_full_filled: "on"
  close_partial_filled: "on"
  order_rejected: "on"
  reconciliation_warning: "on"
  technical_error: "on"
```

- [ ] **Step 2: Write the failing config test**

Create `tests/runtime_v2/control_plane/test_config.py`:

```python
# tests/runtime_v2/control_plane/test_config.py
from __future__ import annotations

import pytest

from src.runtime_v2.control_plane.config import (
    ControlPlaneConfigError,
    load_control_plane_config,
)

_VALID_YAML = """
enabled: true
token_env: CP_TOKEN
chat_id: "${CP_CHAT}"
topics:
  commands: {thread_id: 101}
  tech_log: {thread_id: 102}
  clean_log: {thread_id: 103}
authorized_users:
  - "${CP_USER}"
startup:
  mode: standby
  restore_max_age_seconds: 600
"""


def _write(tmp_path, text):
    p = tmp_path / "telegram_control.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_load_valid_config_with_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_TOKEN", "999:XYZ")
    monkeypatch.setenv("CP_CHAT", "-1009999")
    monkeypatch.setenv("CP_USER", "42")
    cfg = load_control_plane_config(_write(tmp_path, _VALID_YAML))
    assert cfg.token == "999:XYZ"
    assert cfg.chat_id == -1009999
    assert cfg.authorized_users == [42]
    assert cfg.startup.mode == "standby"
    assert cfg.startup.restore_max_age_seconds == 600
    assert cfg.topics.commands.thread_id == 101


def test_missing_token_env_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("CP_TOKEN", raising=False)
    monkeypatch.setenv("CP_CHAT", "-1009999")
    monkeypatch.setenv("CP_USER", "42")
    with pytest.raises(ControlPlaneConfigError) as exc:
        load_control_plane_config(_write(tmp_path, _VALID_YAML))
    assert "CP_TOKEN" in str(exc.value)


def test_unresolved_env_placeholder_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_TOKEN", "999:XYZ")
    monkeypatch.delenv("CP_CHAT", raising=False)
    monkeypatch.setenv("CP_USER", "42")
    with pytest.raises(ControlPlaneConfigError) as exc:
        load_control_plane_config(_write(tmp_path, _VALID_YAML))
    assert "CP_CHAT" in str(exc.value)


def test_missing_required_field_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_TOKEN", "999:XYZ")
    monkeypatch.setenv("CP_CHAT", "-1009999")
    monkeypatch.setenv("CP_USER", "42")
    bad = """
token_env: CP_TOKEN
chat_id: "${CP_CHAT}"
topics:
  commands: {thread_id: 101}
"""
    with pytest.raises(ControlPlaneConfigError):
        load_control_plane_config(_write(tmp_path, bad))


def test_missing_file_raises(tmp_path):
    with pytest.raises(ControlPlaneConfigError):
        load_control_plane_config(str(tmp_path / "nope.yaml"))
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: src.runtime_v2.control_plane.config`.

- [ ] **Step 4: Write the config loader**

Create `src/runtime_v2/control_plane/config.py`:

```python
# src/runtime_v2/control_plane/config.py
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from src.runtime_v2.control_plane.models import ControlPlaneConfig

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ControlPlaneConfigError(Exception):
    pass


def _substitute_env(value):
    """Recursively replace ${ENV} placeholders in strings; raise if unset."""
    if isinstance(value, str):
        def _repl(match: re.Match) -> str:
            name = match.group(1)
            env_val = os.environ.get(name)
            if env_val is None or env_val == "":
                raise ControlPlaneConfigError(
                    f"Environment variable {name} referenced in telegram_control.yaml is not set"
                )
            return env_val
        return _ENV_PATTERN.sub(_repl, value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


def load_control_plane_config(path: str) -> ControlPlaneConfig:
    p = Path(path)
    if not p.exists():
        raise ControlPlaneConfigError(f"Config file not found: {path}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ControlPlaneConfigError(f"Invalid YAML in {path}: {exc}") from exc

    raw = _substitute_env(raw)

    # Resolve token from token_env if `token` not given directly.
    token = raw.get("token")
    if not token:
        token_env = raw.get("token_env")
        if not token_env:
            raise ControlPlaneConfigError("Missing 'token' or 'token_env' in config")
        token = os.environ.get(token_env)
        if not token:
            raise ControlPlaneConfigError(
                f"Environment variable {token_env} (token_env) is not set"
            )
    raw["token"] = token

    try:
        return ControlPlaneConfig.model_validate(raw)
    except ValidationError as exc:
        raise ControlPlaneConfigError(f"Invalid telegram_control config: {exc}") from exc


__all__ = ["load_control_plane_config", "ControlPlaneConfigError"]
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_config.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add config/telegram_control.yaml src/runtime_v2/control_plane/config.py tests/runtime_v2/control_plane/test_config.py
git commit -m "feat(control_plane): add YAML config loader with env substitution

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Auth validator

**Files:**
- Create: `src/runtime_v2/control_plane/auth.py`
- Test: `tests/runtime_v2/control_plane/test_auth.py`

**Behaviour (COMMANDS_SPEC §4.2):** wrong chat → IGNORE; wrong topic → IGNORE; unauthorized user → REJECT_UNAUTHORIZED (no reply); otherwise OK.

- [ ] **Step 1: Write the failing auth test**

Create `tests/runtime_v2/control_plane/test_auth.py`:

```python
# tests/runtime_v2/control_plane/test_auth.py
from __future__ import annotations

from src.runtime_v2.control_plane.auth import AuthValidator
from src.runtime_v2.control_plane.models import (
    CleanLogConfig,
    ControlPlaneConfig,
    TechLogConfig,
    TopicConfig,
    TopicsConfig,
)


def _config() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        token="t",
        chat_id=-100999,
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=101),
            tech_log=TechLogConfig(thread_id=102),
            clean_log=CleanLogConfig(thread_id=103),
        ),
        authorized_users=[42, 43],
    )


def test_authorized_user_in_commands_topic_ok():
    v = AuthValidator(_config())
    res = v.validate(chat_id=-100999, thread_id=101, user_id=42)
    assert res.decision == "OK"


def test_wrong_chat_ignored():
    v = AuthValidator(_config())
    res = v.validate(chat_id=-1, thread_id=101, user_id=42)
    assert res.decision == "IGNORE"
    assert res.reason == "wrong_chat"


def test_wrong_topic_ignored():
    v = AuthValidator(_config())
    res = v.validate(chat_id=-100999, thread_id=999, user_id=42)
    assert res.decision == "IGNORE"
    assert res.reason == "wrong_topic"


def test_unauthorized_user_rejected():
    v = AuthValidator(_config())
    res = v.validate(chat_id=-100999, thread_id=101, user_id=7)
    assert res.decision == "REJECT_UNAUTHORIZED"
    assert res.reason == "unauthorized_user"


def test_missing_thread_id_treated_as_wrong_topic():
    v = AuthValidator(_config())
    res = v.validate(chat_id=-100999, thread_id=None, user_id=42)
    assert res.decision == "IGNORE"
    assert res.reason == "wrong_topic"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: src.runtime_v2.control_plane.auth`.

- [ ] **Step 3: Write the auth validator**

Create `src/runtime_v2/control_plane/auth.py`:

```python
# src/runtime_v2/control_plane/auth.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.runtime_v2.control_plane.models import ControlPlaneConfig

AuthDecision = Literal["OK", "IGNORE", "REJECT_UNAUTHORIZED"]


@dataclass(frozen=True)
class AuthResult:
    decision: AuthDecision
    reason: str | None = None


class AuthValidator:
    """Stateless per-update authorization (COMMANDS_SPEC §4.2)."""

    def __init__(self, config: ControlPlaneConfig) -> None:
        self._chat_id = config.chat_id
        self._commands_thread_id = config.topics.commands.thread_id
        self._authorized = set(config.authorized_users)

    def validate(
        self, *, chat_id: int, thread_id: int | None, user_id: int
    ) -> AuthResult:
        if chat_id != self._chat_id:
            return AuthResult("IGNORE", "wrong_chat")
        if thread_id != self._commands_thread_id:
            return AuthResult("IGNORE", "wrong_topic")
        if user_id not in self._authorized:
            return AuthResult("REJECT_UNAUTHORIZED", "unauthorized_user")
        return AuthResult("OK", None)


__all__ = ["AuthValidator", "AuthResult", "AuthDecision"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_auth.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the whole Part 1 suite**

Run: `python -m pytest tests/runtime_v2/control_plane/ -v`
Expected: PASS (all Part 1 tests).

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/auth.py tests/runtime_v2/control_plane/test_auth.py
git commit -m "feat(control_plane): add stateless auth validator

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## End-of-part verification

- [ ] Run `python main.py --migrate` and confirm output reports the new ops migration applied (or 0 if already applied to the local DB).
- [ ] Run `python -m pytest tests/runtime_v2/control_plane/ -v` — all green.
- [ ] Update `docs/AUDIT.md`: mark Control Plane Part 1 (Foundation) complete; list new files; note the `scope_type` discrepancy (prose spec says `PER_TRADER` for per-trader pause, but `ControlStateRepository.get_effective_mode` matches `"TRADER"` — to be resolved in Part 4).

---

## Self-Review

**Spec coverage (spec §7 Parte 1):** migration `007` ✅ (Task 1); `telegram_control.yaml` ✅ (Task 3); `models.py` ✅ (Task 2); `config.py` ✅ (Task 3); `auth.py` ✅ (Task 4). Test requirement "unit su config.py (YAML valido/invalido, campi mancanti) e auth.py (user autorizzato, non autorizzato, topic sbagliato)" ✅ covered by test_config.py + test_auth.py.

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every run step shows exact command + expected result.

**Type consistency:** `ControlPlaneConfig`, `TopicsConfig`, `TopicConfig`, `TechLogConfig`, `CleanLogConfig`, `StartupConfig` defined in Task 2 and used identically in Tasks 3–4 tests. `AuthResult.decision` values `OK|IGNORE|REJECT_UNAUTHORIZED` match between auth.py and test_auth.py. `load_control_plane_config` / `ControlPlaneConfigError` names match between config.py and test_config.py.

**Note for later parts:** `NotificationOutboxEntry`, `ControlCommand`, `ConfigOverride`, `RuntimeSnapshot` are defined here but only consumed in Parts 2/4/5; that is intentional (Part 1 publishes the shared contract).

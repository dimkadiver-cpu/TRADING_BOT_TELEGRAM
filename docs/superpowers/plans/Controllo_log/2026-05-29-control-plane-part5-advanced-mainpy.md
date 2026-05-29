# Control Plane Telegram — Part 5: Advanced Commands + main.py Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the Control Plane: add `/pnl`, `/logs`, `/debug_on [durata]`, `/debug_off`; add runtime snapshots; implement the `auto | standby | restore` startup modes; and wire the bot + dispatcher into `main.py` with coordinated startup and graceful SIGTERM shutdown (snapshot + TECH_LOG notification).

**Architecture:** `SnapshotStore` persists/reads `ops_runtime_snapshot`. A pure `resolve_startup()` decides whether to start blocked (standby) or replay a fresh snapshot (restore, fallback auto). `DebugModeController` holds debug expiry in memory. `RuntimeControlService` gains `get_pnl`, `get_logs`, debug toggles. The dispatcher learns to render TECH_LOG. A new `build_control_plane()` bootstrap wires everything; `main.py` creates two asyncio tasks (bot, dispatcher), writes startup notifications, and on SIGTERM saves a snapshot, flushes a TECH_LOG shutdown message, and cancels the tasks.

**Tech Stack:** Python 3.12, asyncio, sqlite3, python-telegram-bot ≥21, pytest.

**Depends on:** Parts 1–4 merged.

**Cross-part contract this part publishes:**
- `snapshot_store.py` — `SnapshotStore.save/get_latest/is_stale`.
- `startup.py` — `resolve_startup()`, `StartupPlan`.
- `debug_controller.py` — `DebugModeController`.
- `service.py` (extended) — `get_pnl`, `get_logs`, `enable_debug`, `disable_debug`, `debug_status`.
- `formatters/{pnl,debug,tech_log}.py`.
- `bootstrap.py` — `build_control_plane()` (used by `main.py`).
- `main.py` (modified) — tasks + SIGTERM + startup modes.

**Scope notes (honest):**
- `/pnl` renders from data the schema actually holds — open-trade count and the latest `ops_account_snapshots` equity/available if present. Per-trade unrealized PnL, fees, and funding are **not** persisted yet, so they are shown as `n/a`. Flagged in `docs/AUDIT.md`.
- TECH_LOG **dedup/batch/rate-limit** (TECH_LOG_SPEC §6–§7) and full Level-3 **debug emission** of internal decisions are **not** implemented here. This part delivers: the TECH_LOG formatter, debug-mode state with auto-expiry, the `/debug_*` replies and announce, dedupe via the outbox's UNIQUE `dedupe_key`, and the startup/shutdown TECH_LOG notifications. The verbose per-decision instrumentation is flagged as a follow-up.
- Excluded commands (`/forceclose`, `/closepartial`, `/cancelpending`, `/movetobe`, `/halt`, `/panicclose`) are **not** added to the allowed set (acceptance criterion 6 of the design spec).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/runtime_v2/control_plane/snapshot_store.py` | `ops_runtime_snapshot` persistence + staleness check. |
| `src/runtime_v2/control_plane/startup.py` | Pure `resolve_startup()` decision. |
| `src/runtime_v2/control_plane/debug_controller.py` | In-memory debug-mode state with expiry. |
| `src/runtime_v2/control_plane/service.py` (modify) | `get_pnl`, `get_logs`, debug toggles. |
| `src/runtime_v2/control_plane/formatters/pnl.py` | `/pnl`. |
| `src/runtime_v2/control_plane/formatters/debug.py` | `/debug_on` `/debug_off`. |
| `src/runtime_v2/control_plane/formatters/tech_log.py` | TECH_LOG message rendering. |
| `src/runtime_v2/control_plane/notification_dispatcher.py` (modify) | Render TECH_LOG via formatter. |
| `src/runtime_v2/control_plane/telegram_bot.py` (modify) | Allow + dispatch `/pnl`,`/logs`,`/debug_on`,`/debug_off`. |
| `src/runtime_v2/control_plane/bootstrap.py` | `build_control_plane()` wiring helper. |
| `main.py` (modify) | Create tasks, startup modes, SIGTERM shutdown. |
| `tests/runtime_v2/control_plane/test_snapshot_store.py` | save/get/stale. |
| `tests/runtime_v2/control_plane/test_startup.py` | auto/standby/restore/fallback. |
| `tests/runtime_v2/control_plane/test_debug_controller.py` | enable/expire/cap/disable. |
| `tests/runtime_v2/control_plane/test_advanced_formatters.py` | pnl/debug/tech_log text. |
| `tests/runtime_v2/control_plane/test_command_router_advanced.py` | router dispatches advanced cmds. |
| `tests/runtime_v2/control_plane/test_bootstrap.py` | builder degrades gracefully + wires when configured. |

---

### Task 1: SnapshotStore

**Files:**
- Create: `src/runtime_v2/control_plane/snapshot_store.py`
- Test: `tests/runtime_v2/control_plane/test_snapshot_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/control_plane/test_snapshot_store.py`:

```python
# tests/runtime_v2/control_plane/test_snapshot_store.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.snapshot_store import SnapshotStore


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


def test_save_and_get_latest(ops_db):
    store = SnapshotStore(ops_db)
    store.save(control_mode="NONE", active_blocks=["GLOBAL:BLOCK_NEW_ENTRIES"],
               open_chain_count=3, pending_command_count=1, shutdown_reason="SIGTERM")
    snap = store.get_latest()
    assert snap is not None
    assert snap.control_mode == "NONE"
    assert snap.open_chain_count == 3
    assert snap.pending_command_count == 1
    assert snap.shutdown_reason == "SIGTERM"


def test_get_latest_returns_most_recent(ops_db):
    store = SnapshotStore(ops_db)
    store.save(control_mode="NONE", active_blocks=[], open_chain_count=1,
               pending_command_count=0)
    store.save(control_mode="BLOCK_NEW_ENTRIES", active_blocks=["GLOBAL"],
               open_chain_count=5, pending_command_count=2)
    snap = store.get_latest()
    assert snap.control_mode == "BLOCK_NEW_ENTRIES"
    assert snap.open_chain_count == 5


def test_get_latest_empty(ops_db):
    store = SnapshotStore(ops_db)
    assert store.get_latest() is None


def test_is_stale():
    store = SnapshotStore(":memory:")
    fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
    old = datetime.now(timezone.utc) - timedelta(seconds=500)
    assert store.is_stale(fresh, max_age_seconds=300) is False
    assert store.is_stale(old, max_age_seconds=300) is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_snapshot_store.py -v`
Expected: FAIL — `ModuleNotFoundError: ...snapshot_store`.

- [ ] **Step 3: Write the snapshot store**

Create `src/runtime_v2/control_plane/snapshot_store.py`:

```python
# src/runtime_v2/control_plane/snapshot_store.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.runtime_v2.control_plane.models import RuntimeSnapshot


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SnapshotStore:
    """Persistence for ops_runtime_snapshot (COMMANDS_SPEC §3.5)."""

    def __init__(self, ops_db_path: str) -> None:
        self._db = ops_db_path

    def save(
        self,
        *,
        control_mode: str,
        active_blocks: list[str],
        open_chain_count: int,
        pending_command_count: int,
        shutdown_reason: str | None = None,
    ) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "INSERT INTO ops_runtime_snapshot "
                "(snapshot_at, control_mode, active_blocks_json, open_chain_count, "
                " pending_command_count, shutdown_reason, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (now, control_mode, json.dumps(active_blocks), open_chain_count,
                 pending_command_count, shutdown_reason, now),
            )
            conn.commit()
        finally:
            conn.close()

    def get_latest(self) -> RuntimeSnapshot | None:
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT id, snapshot_at, control_mode, active_blocks_json, "
                "open_chain_count, pending_command_count, shutdown_reason "
                "FROM ops_runtime_snapshot ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return RuntimeSnapshot(
            id=row[0],
            snapshot_at=datetime.fromisoformat(row[1]),
            control_mode=row[2],
            active_blocks_json=row[3],
            open_chain_count=row[4],
            pending_command_count=row[5],
            shutdown_reason=row[6],
        )

    def is_stale(self, snapshot_at: datetime, *, max_age_seconds: int) -> bool:
        if snapshot_at.tzinfo is None:
            snapshot_at = snapshot_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - snapshot_at).total_seconds()
        return age > max_age_seconds


__all__ = ["SnapshotStore"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_snapshot_store.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/snapshot_store.py tests/runtime_v2/control_plane/test_snapshot_store.py
git commit -m "feat(control_plane): add runtime snapshot store

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Startup resolver

**Files:**
- Create: `src/runtime_v2/control_plane/startup.py`
- Test: `tests/runtime_v2/control_plane/test_startup.py`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/control_plane/test_startup.py`:

```python
# tests/runtime_v2/control_plane/test_startup.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.runtime_v2.control_plane.models import RuntimeSnapshot
from src.runtime_v2.control_plane.startup import resolve_startup


def _snapshot(control_mode, age_seconds):
    return RuntimeSnapshot(
        snapshot_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
        control_mode=control_mode,
        active_blocks_json="[]",
        open_chain_count=0,
        pending_command_count=0,
    )


def test_auto_does_not_block():
    plan = resolve_startup(mode="auto", restore_max_age_seconds=300, latest_snapshot=None)
    assert plan.mode == "auto"
    assert plan.apply_global_block is False


def test_standby_blocks():
    plan = resolve_startup(mode="standby", restore_max_age_seconds=300, latest_snapshot=None)
    assert plan.mode == "standby"
    assert plan.apply_global_block is True


def test_restore_fresh_blocked_snapshot_blocks():
    snap = _snapshot("BLOCK_NEW_ENTRIES", age_seconds=42)
    plan = resolve_startup(mode="restore", restore_max_age_seconds=300, latest_snapshot=snap)
    assert plan.mode == "restore"
    assert plan.apply_global_block is True


def test_restore_fresh_unblocked_snapshot_does_not_block():
    snap = _snapshot("NONE", age_seconds=42)
    plan = resolve_startup(mode="restore", restore_max_age_seconds=300, latest_snapshot=snap)
    assert plan.mode == "restore"
    assert plan.apply_global_block is False


def test_restore_stale_snapshot_falls_back_to_auto():
    snap = _snapshot("BLOCK_NEW_ENTRIES", age_seconds=480)
    plan = resolve_startup(mode="restore", restore_max_age_seconds=300, latest_snapshot=snap)
    assert plan.mode == "auto"            # fallback
    assert plan.apply_global_block is False
    assert plan.fell_back is True


def test_restore_missing_snapshot_falls_back_to_auto():
    plan = resolve_startup(mode="restore", restore_max_age_seconds=300, latest_snapshot=None)
    assert plan.mode == "auto"
    assert plan.fell_back is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_startup.py -v`
Expected: FAIL — `ModuleNotFoundError: ...startup`.

- [ ] **Step 3: Write the resolver**

Create `src/runtime_v2/control_plane/startup.py`:

```python
# src/runtime_v2/control_plane/startup.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.runtime_v2.control_plane.models import RuntimeSnapshot


@dataclass
class StartupPlan:
    mode: str                  # effective mode after fallback (auto|standby|restore)
    apply_global_block: bool
    fell_back: bool = False
    message: str = ""


def _age_seconds(snapshot_at: datetime) -> float:
    if snapshot_at.tzinfo is None:
        snapshot_at = snapshot_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - snapshot_at).total_seconds()


def resolve_startup(
    *,
    mode: str,
    restore_max_age_seconds: int,
    latest_snapshot: RuntimeSnapshot | None,
) -> StartupPlan:
    if mode == "standby":
        return StartupPlan(mode="standby", apply_global_block=True,
                           message="Bot avviato in modalità standby. Nuove entry bloccate.")
    if mode == "restore":
        if latest_snapshot is None:
            return StartupPlan(mode="auto", apply_global_block=False, fell_back=True,
                               message="Snapshot assente. Fallback a modalità auto.")
        age = _age_seconds(latest_snapshot.snapshot_at)
        if age > restore_max_age_seconds:
            return StartupPlan(
                mode="auto", apply_global_block=False, fell_back=True,
                message=(f"Snapshot DB troppo vecchio ({int(age)}s > "
                         f"max {restore_max_age_seconds}s). Fallback a modalità auto."),
            )
        blocked = latest_snapshot.control_mode in ("BLOCK_NEW_ENTRIES", "FULL_STOP")
        return StartupPlan(
            mode="restore", apply_global_block=blocked,
            message=f"Stato ripristinato da snapshot DB. Snapshot age: {int(age)}s",
        )
    # auto (default)
    return StartupPlan(mode="auto", apply_global_block=False,
                       message="Modalità: auto")


__all__ = ["resolve_startup", "StartupPlan"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_startup.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/startup.py tests/runtime_v2/control_plane/test_startup.py
git commit -m "feat(control_plane): add startup mode resolver

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: DebugModeController

**Files:**
- Create: `src/runtime_v2/control_plane/debug_controller.py`
- Test: `tests/runtime_v2/control_plane/test_debug_controller.py`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/control_plane/test_debug_controller.py`:

```python
# tests/runtime_v2/control_plane/test_debug_controller.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.runtime_v2.control_plane.debug_controller import DebugModeController, parse_duration


def test_parse_duration_defaults():
    assert parse_duration(None) == 600       # 10 minutes default
    assert parse_duration("") == 600


def test_parse_duration_units():
    assert parse_duration("5m") == 300
    assert parse_duration("30m") == 1800
    assert parse_duration("1h") == 3600


def test_parse_duration_caps_at_max():
    assert parse_duration("10h", max_seconds=3600) == 3600


def test_enable_and_active():
    ctrl = DebugModeController(max_seconds=3600)
    now = datetime(2026, 5, 29, 14, 0, 0, tzinfo=timezone.utc)
    expiry = ctrl.enable(duration_seconds=600, now=now)
    assert expiry == now + timedelta(seconds=600)
    assert ctrl.is_active(now=now) is True
    assert ctrl.is_active(now=now + timedelta(seconds=601)) is False   # expired


def test_disable():
    ctrl = DebugModeController(max_seconds=3600)
    now = datetime(2026, 5, 29, 14, 0, 0, tzinfo=timezone.utc)
    ctrl.enable(duration_seconds=600, now=now)
    ctrl.disable()
    assert ctrl.is_active(now=now) is False


def test_enable_respects_cap():
    ctrl = DebugModeController(max_seconds=300)
    now = datetime(2026, 5, 29, 14, 0, 0, tzinfo=timezone.utc)
    expiry = ctrl.enable(duration_seconds=999999, now=now)
    assert expiry == now + timedelta(seconds=300)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_debug_controller.py -v`
Expected: FAIL — `ModuleNotFoundError: ...debug_controller`.

- [ ] **Step 3: Write the controller**

Create `src/runtime_v2/control_plane/debug_controller.py`:

```python
# src/runtime_v2/control_plane/debug_controller.py
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([mh])\s*$", re.IGNORECASE)
_DEFAULT_SECONDS = 600   # 10 minutes


def parse_duration(text: str | None, *, max_seconds: int = 3600) -> int:
    if not text:
        return min(_DEFAULT_SECONDS, max_seconds)
    m = _DURATION_RE.match(text)
    if not m:
        return min(_DEFAULT_SECONDS, max_seconds)
    value, unit = int(m.group(1)), m.group(2).lower()
    seconds = value * 60 if unit == "m" else value * 3600
    return min(seconds, max_seconds)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DebugModeController:
    """In-memory debug-mode state with automatic expiry (TECH_LOG_SPEC §8)."""

    def __init__(self, *, max_seconds: int = 3600) -> None:
        self._max_seconds = max_seconds
        self._expires_at: datetime | None = None

    def enable(self, *, duration_seconds: int, now: datetime | None = None) -> datetime:
        now = now or _now()
        capped = min(duration_seconds, self._max_seconds)
        self._expires_at = now + timedelta(seconds=capped)
        return self._expires_at

    def disable(self) -> None:
        self._expires_at = None

    def is_active(self, *, now: datetime | None = None) -> bool:
        if self._expires_at is None:
            return False
        return (now or _now()) <= self._expires_at

    @property
    def expires_at(self) -> datetime | None:
        return self._expires_at


__all__ = ["DebugModeController", "parse_duration"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_debug_controller.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/debug_controller.py tests/runtime_v2/control_plane/test_debug_controller.py
git commit -m "feat(control_plane): add debug-mode controller

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Advanced formatters (pnl / debug / tech_log)

**Files:**
- Create: `src/runtime_v2/control_plane/formatters/pnl.py`, `debug.py`, `tech_log.py`
- Test: `tests/runtime_v2/control_plane/test_advanced_formatters.py`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/control_plane/test_advanced_formatters.py`:

```python
# tests/runtime_v2/control_plane/test_advanced_formatters.py
from __future__ import annotations

from datetime import datetime, timezone

from src.runtime_v2.control_plane.formatters.debug import (
    format_debug_off, format_debug_on,
)
from src.runtime_v2.control_plane.formatters.pnl import PnlView, format_pnl
from src.runtime_v2.control_plane.formatters.tech_log import format_tech_log


def test_format_pnl_with_equity():
    view = PnlView(account_id="main", open_trades=5, equity_usdt=10000.0,
                   available_usdt=8000.0, updated_at="14:32:10")
    text = format_pnl(view)
    assert "PnL" in text
    assert "main" in text
    assert "Open trades: 5" in text


def test_format_pnl_without_equity():
    view = PnlView(account_id="main", open_trades=0, equity_usdt=None,
                   available_usdt=None, updated_at="14:32:10")
    text = format_pnl(view)
    assert "n/a" in text


def test_format_debug_on():
    expiry = datetime(2026, 5, 29, 14, 42, 10, tzinfo=timezone.utc)
    text = format_debug_on(duration_seconds=600, expires_at=expiry)
    assert "DEBUG MODE ATTIVATO" in text
    assert "10" in text       # 10 minutes
    assert "/debug_off" in text


def test_format_debug_off():
    text = format_debug_off()
    assert "DISATTIVATO" in text.upper()


def test_format_tech_log():
    text = format_tech_log({
        "level": "ERROR", "category": "Exchange",
        "description": "API error retCode 10001",
        "context": {"Symbol": "BTCUSDT", "Chain": "#145"},
        "action": "retry scheduled (1/3)",
        "source": "ccxt_bybit_adapter",
    })
    assert "[ERROR]" in text
    assert "Exchange" in text
    assert "Symbol: BTCUSDT" in text
    assert "Source: ccxt_bybit_adapter" in text


def test_format_tech_log_minimal():
    text = format_tech_log({"level": "WARN", "description": "something"})
    assert "[WARN]" in text
    assert "something" in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_advanced_formatters.py -v`
Expected: FAIL — formatter modules not found.

- [ ] **Step 3: Write `formatters/pnl.py`**

Create `src/runtime_v2/control_plane/formatters/pnl.py`:

```python
# src/runtime_v2/control_plane/formatters/pnl.py
from __future__ import annotations

from dataclasses import dataclass

_SEP = "────────────────"


@dataclass
class PnlView:
    account_id: str
    open_trades: int
    equity_usdt: float | None
    available_usdt: float | None
    updated_at: str


def format_pnl(view: PnlView) -> str:
    equity = f"{view.equity_usdt:,.2f} USDT" if view.equity_usdt is not None else "n/a"
    avail = f"{view.available_usdt:,.2f} USDT" if view.available_usdt is not None else "n/a"
    lines = [
        f"💰 PnL — account: {view.account_id}",
        _SEP,
        f"Updated: {view.updated_at}",
        "",
        f"Open trades: {view.open_trades}",
        f"Equity: {equity}",
        f"Available: {avail}",
        "",
        "Note: per-trade unrealized PnL/fees/funding not yet available.",
        "",
        "Use:",
        "/trades for per-trade details",
        "/status for system health",
    ]
    return "\n".join(lines)


__all__ = ["PnlView", "format_pnl"]
```

- [ ] **Step 4: Write `formatters/debug.py`**

Create `src/runtime_v2/control_plane/formatters/debug.py`:

```python
# src/runtime_v2/control_plane/formatters/debug.py
from __future__ import annotations

from datetime import datetime

_SEP = "────────────────"


def format_debug_on(*, duration_seconds: int, expires_at: datetime) -> str:
    minutes = duration_seconds // 60
    return "\n".join([
        "🔍 DEBUG MODE ATTIVATO",
        _SEP,
        f"Durata: {minutes} minuti",
        f"Scadenza: {expires_at.strftime('%H:%M:%S')}",
        "",
        "TECH_LOG mostrerà più dettagli diagnostici.",
        "",
        "Usa /debug_off per disattivare prima.",
        "",
        "Commands:",
        "/debug_off",
    ])


def format_debug_off() -> str:
    return "\n".join([
        "✅ DEBUG MODE DISATTIVATO",
        _SEP,
        "Ritorno a min_level: WARNING.",
    ])


__all__ = ["format_debug_on", "format_debug_off"]
```

- [ ] **Step 5: Write `formatters/tech_log.py`**

Create `src/runtime_v2/control_plane/formatters/tech_log.py`:

```python
# src/runtime_v2/control_plane/formatters/tech_log.py
from __future__ import annotations

_SEP = "────────────────"


def format_tech_log(payload: dict) -> str:
    level = str(payload.get("level", "INFO")).upper()
    category = payload.get("category")
    description = payload.get("description", "")
    header = f"[{level}] {category}" if category else f"[{level}]"
    lines = [header, _SEP, description]

    context = payload.get("context") or {}
    if context:
        lines.append("")
        lines.append("Context:")
        for key, value in context.items():
            lines.append(f"{key}: {value}")

    action = payload.get("action")
    if action:
        lines.append("")
        lines.append(f"Action: {action}")

    lines.append(_SEP)
    lines.append(f"Source: {payload.get('source', 'runtime')}")
    return "\n".join(lines)


__all__ = ["format_tech_log"]
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_advanced_formatters.py -v`
Expected: PASS (6 tests).

- [ ] **Step 7: Wire the TECH_LOG formatter into the dispatcher**

In `src/runtime_v2/control_plane/notification_dispatcher.py`, add the import near the top:

```python
from src.runtime_v2.control_plane.formatters.tech_log import format_tech_log
```

Then replace the `_render` method body with:

```python
    def _render(self, destination: str, notification_type: str, payload: dict) -> str:
        if destination == "CLEAN_LOG":
            return format_clean_log(notification_type, payload)
        if destination == "TECH_LOG":
            return format_tech_log(payload)
        return payload.get("text") or f"{notification_type}"
```

- [ ] **Step 8: Run dispatcher tests (no regression)**

Run: `python -m pytest tests/runtime_v2/control_plane/test_dispatcher.py -v`
Expected: PASS (CLEAN_LOG path unchanged).

- [ ] **Step 9: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/pnl.py src/runtime_v2/control_plane/formatters/debug.py src/runtime_v2/control_plane/formatters/tech_log.py src/runtime_v2/control_plane/notification_dispatcher.py tests/runtime_v2/control_plane/test_advanced_formatters.py
git commit -m "feat(control_plane): add pnl/debug/tech_log formatters + dispatcher TECH_LOG render

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Service advanced methods + router wiring

**Files:**
- Modify: `src/runtime_v2/control_plane/service.py`, `src/runtime_v2/control_plane/telegram_bot.py`
- Test: `tests/runtime_v2/control_plane/test_command_router_advanced.py`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/control_plane/test_command_router_advanced.py`:

```python
# tests/runtime_v2/control_plane/test_command_router_advanced.py
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.audit_store import CommandAuditStore
from src.runtime_v2.control_plane.auth import AuthValidator
from src.runtime_v2.control_plane.models import (
    CleanLogConfig, ControlPlaneConfig, TechLogConfig, TopicConfig, TopicsConfig,
)
from src.runtime_v2.control_plane.service import RuntimeControlService
from src.runtime_v2.control_plane.telegram_bot import CommandRouter


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


def _router(ops_db, log_path=None):
    cfg = ControlPlaneConfig(
        token="t", chat_id=-100999,
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=101),
            tech_log=TechLogConfig(thread_id=102),
            clean_log=CleanLogConfig(thread_id=103),
        ),
        authorized_users=[42],
    )
    return CommandRouter(
        config=cfg, auth=AuthValidator(cfg),
        audit=CommandAuditStore(ops_db),
        service=RuntimeControlService(ops_db_path=ops_db, log_path=log_path),
    )


def _route(router, text, mid):
    return router.route(command_text=text, message_id=mid,
                        chat_id=-100999, thread_id=101, user_id=42, username="op")


def test_pnl(ops_db):
    router = _router(ops_db)
    res = _route(router, "/pnl", 1)
    assert "PnL" in res.reply_text


def test_debug_on_then_off(ops_db):
    router = _router(ops_db)
    on = _route(router, "/debug_on 5m", 2)
    assert "DEBUG MODE ATTIVATO" in on.reply_text
    assert "5 minuti" in on.reply_text
    off = _route(router, "/debug_off", 3)
    assert "DISATTIVATO" in off.reply_text.upper()


def test_logs_reads_file(ops_db, tmp_path):
    log_file = tmp_path / "bot.log"
    log_file.write_text("\n".join(f"line {i}" for i in range(50)), encoding="utf-8")
    router = _router(ops_db, log_path=str(log_file))
    res = _route(router, "/logs 5", 4)
    assert "line 49" in res.reply_text


def test_excluded_command_rejected(ops_db):
    router = _router(ops_db)
    res = _route(router, "/forceclose 145", 5)
    assert "riconosciuto" in res.reply_text.lower()   # not in allowed set
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_command_router_advanced.py -v`
Expected: FAIL — `RuntimeControlService` has no `log_path` kwarg / `/pnl` unknown.

- [ ] **Step 3: Extend `service.py`**

In `src/runtime_v2/control_plane/service.py`:

(a) Add imports near the top:

```python
from src.runtime_v2.control_plane.debug_controller import DebugModeController
from src.runtime_v2.control_plane.formatters.pnl import PnlView
```

(b) Change `__init__` to accept `log_path` and a debug controller. Replace the existing `def __init__(self, *, ops_db_path: str) -> None:` block with:

```python
    def __init__(
        self,
        *,
        ops_db_path: str,
        log_path: str | None = None,
        debug_controller: DebugModeController | None = None,
    ) -> None:
        self._ops_db = ops_db_path
        self._log_path = log_path
        self._queries = StatusQueries(ops_db_path)
        self._overrides = OverrideStore(ops_db_path)
        self._debug = debug_controller or DebugModeController()
```

(c) Add these read/debug methods to the class (after `unblock_symbol`):

```python
    # ── advanced reads ──────────────────────────────────────────────────────
    def get_pnl(self) -> PnlView:
        conn = sqlite3.connect(self._ops_db)
        try:
            open_trades = conn.execute(
                "SELECT COUNT(*) FROM ops_trade_chains "
                "WHERE lifecycle_state IN ('OPEN','PARTIALLY_CLOSED')"
            ).fetchone()[0]
            row = conn.execute(
                "SELECT account_id, equity_usdt, available_balance_usdt "
                "FROM ops_account_snapshots ORDER BY snapshot_id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        account_id = row[0] if row else "main"
        equity = row[1] if row else None
        available = row[2] if row else None
        return PnlView(
            account_id=account_id, open_trades=open_trades,
            equity_usdt=equity, available_usdt=available,
            updated_at=_now()[11:19],
        )

    def get_logs(self, n: int = 20) -> list[str]:
        if not self._log_path:
            return []
        try:
            with open(self._log_path, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return []
        return lines[-n:]

    # ── debug ───────────────────────────────────────────────────────────────
    def enable_debug(self, *, duration_seconds: int):
        return self._debug.enable(duration_seconds=duration_seconds)

    def disable_debug(self) -> None:
        self._debug.disable()

    def debug_status(self) -> bool:
        return self._debug.is_active()
```

(d) Extend `__all__`: add `"PnlView"` if not present (re-exported for convenience):

```python
__all__ = [
    "RuntimeControlService", "VersionInfo",
    "PauseResult", "ResumeResult", "BlockResult", "UnblockResult",
]
```
(no change needed if already present — keep as-is.)

- [ ] **Step 4: Extend the router in `telegram_bot.py`**

In `src/runtime_v2/control_plane/telegram_bot.py`:

(a) Add imports:

```python
from src.runtime_v2.control_plane.debug_controller import parse_duration
from src.runtime_v2.control_plane.formatters.debug import format_debug_off, format_debug_on
from src.runtime_v2.control_plane.formatters.pnl import format_pnl
```

(b) Extend the allowed command sets. Replace the `_CONTROL_COMMANDS`/`_ALLOWED_COMMANDS` definitions with:

```python
_CONTROL_COMMANDS = frozenset({"pause", "resume", "start", "block", "unblock"})
_ADVANCED_COMMANDS = frozenset({"pnl", "logs", "debug_on", "debug_off"})
_ALLOWED_COMMANDS = _READONLY_COMMANDS | _CONTROL_COMMANDS | _ADVANCED_COMMANDS
```

(c) Store the debug-max from config on the router. In `CommandRouter.__init__`, add at the end:

```python
        self._debug_max_seconds = config.topics.tech_log.debug_max_duration_minutes * 60
```

(d) In `_dispatch`, before the final `return "Comando non riconosciuto."`, add:

```python
        if command_name == "pnl":
            return format_pnl(self._service.get_pnl())
        if command_name == "logs":
            n = int(args[0]) if args and args[0].isdigit() else 20
            lines = self._service.get_logs(n)
            if not lines:
                return "📋 LOGS\n────────────────\n(log non disponibile)"
            return "📋 LOGS — last {}\n────────────────\n{}".format(len(lines), "\n".join(lines))
        if command_name == "debug_on":
            seconds = parse_duration(args[0] if args else None,
                                     max_seconds=self._debug_max_seconds)
            expiry = self._service.enable_debug(duration_seconds=seconds)
            return format_debug_on(duration_seconds=seconds, expires_at=expiry)
        if command_name == "debug_off":
            self._service.disable_debug()
            return format_debug_off()
```

- [ ] **Step 5: Run the advanced router test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_command_router_advanced.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Run the full control-plane suite**

Run: `python -m pytest tests/runtime_v2/control_plane/ -v`
Expected: PASS (all Parts 1–5 control-plane tests).

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/control_plane/service.py src/runtime_v2/control_plane/telegram_bot.py tests/runtime_v2/control_plane/test_command_router_advanced.py
git commit -m "feat(control_plane): add /pnl /logs /debug_on /debug_off

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Bootstrap builder

**Files:**
- Create: `src/runtime_v2/control_plane/bootstrap.py`
- Test: `tests/runtime_v2/control_plane/test_bootstrap.py`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/control_plane/test_bootstrap.py`:

```python
# tests/runtime_v2/control_plane/test_bootstrap.py
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.bootstrap import build_control_plane


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


_YAML = """
enabled: {enabled}
token_env: CP_TOKEN
chat_id: "${{CP_CHAT}}"
topics:
  commands: {{thread_id: 101}}
  tech_log: {{thread_id: 102}}
  clean_log: {{thread_id: 103}}
authorized_users:
  - "${{CP_USER}}"
startup:
  mode: standby
"""


def _write_config(tmp_path, enabled="true"):
    p = tmp_path / "telegram_control.yaml"
    p.write_text(_YAML.format(enabled=enabled), encoding="utf-8")
    return str(p)


def test_missing_config_returns_none(ops_db, tmp_path):
    cp = build_control_plane(config_path=str(tmp_path / "nope.yaml"),
                             ops_db_path=ops_db, log_path=None)
    assert cp is None


def test_disabled_returns_none(ops_db, tmp_path, monkeypatch):
    monkeypatch.setenv("CP_TOKEN", "x")
    monkeypatch.setenv("CP_CHAT", "-100999")
    monkeypatch.setenv("CP_USER", "42")
    cp = build_control_plane(config_path=_write_config(tmp_path, "false"),
                             ops_db_path=ops_db, log_path=None)
    assert cp is None


def test_enabled_wires_components(ops_db, tmp_path, monkeypatch):
    monkeypatch.setenv("CP_TOKEN", "123:ABC")
    monkeypatch.setenv("CP_CHAT", "-100999")
    monkeypatch.setenv("CP_USER", "42")
    cp = build_control_plane(config_path=_write_config(tmp_path, "true"),
                             ops_db_path=ops_db, log_path=None)
    assert cp is not None
    assert cp.bot is not None
    assert cp.dispatcher is not None
    assert cp.service is not None
    assert cp.snapshot_store is not None
    # startup plan reflects standby
    assert cp.startup_plan.apply_global_block is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_bootstrap.py -v`
Expected: FAIL — `ModuleNotFoundError: ...bootstrap`.

- [ ] **Step 3: Write the bootstrap**

Create `src/runtime_v2/control_plane/bootstrap.py`:

```python
# src/runtime_v2/control_plane/bootstrap.py
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.runtime_v2.control_plane.audit_store import CommandAuditStore
from src.runtime_v2.control_plane.auth import AuthValidator
from src.runtime_v2.control_plane.config import (
    ControlPlaneConfigError, load_control_plane_config,
)
from src.runtime_v2.control_plane.debug_controller import DebugModeController
from src.runtime_v2.control_plane.models import ControlPlaneConfig
from src.runtime_v2.control_plane.notification_dispatcher import (
    TelegramBotSender, TelegramNotificationDispatcher,
)
from src.runtime_v2.control_plane.service import RuntimeControlService
from src.runtime_v2.control_plane.snapshot_store import SnapshotStore
from src.runtime_v2.control_plane.startup import StartupPlan, resolve_startup
from src.runtime_v2.control_plane.telegram_bot import CommandRouter, TelegramControlBot
from src.runtime_v2.control_plane.topic_router import TopicRouter

logger = logging.getLogger(__name__)


@dataclass
class ControlPlane:
    config: ControlPlaneConfig
    service: RuntimeControlService
    bot: TelegramControlBot
    dispatcher: TelegramNotificationDispatcher
    snapshot_store: SnapshotStore
    startup_plan: StartupPlan


def build_control_plane(
    *,
    config_path: str,
    ops_db_path: str,
    log_path: str | None,
) -> ControlPlane | None:
    """Wire the control plane. Returns None if disabled or misconfigured (runtime continues)."""
    try:
        config = load_control_plane_config(config_path)
    except ControlPlaneConfigError as exc:
        logger.warning("control plane disabled — config error: %s", exc)
        return None

    if not config.enabled:
        logger.info("control plane disabled via config")
        return None

    try:
        from telegram import Bot
        bot_api = Bot(config.token)
    except Exception:
        logger.exception("control plane disabled — telegram Bot init failed")
        return None

    debug_controller = DebugModeController(
        max_seconds=config.topics.tech_log.debug_max_duration_minutes * 60
    )
    service = RuntimeControlService(
        ops_db_path=ops_db_path, log_path=log_path, debug_controller=debug_controller,
    )
    router = CommandRouter(
        config=config,
        auth=AuthValidator(config),
        audit=CommandAuditStore(ops_db_path),
        service=service,
    )
    bot = TelegramControlBot(config=config, router=router)
    dispatcher = TelegramNotificationDispatcher(
        config=config,
        ops_db_path=ops_db_path,
        topic_router=TopicRouter(config),
        sender=TelegramBotSender(bot_api),
    )
    snapshot_store = SnapshotStore(ops_db_path)
    startup_plan = resolve_startup(
        mode=config.startup.mode,
        restore_max_age_seconds=config.startup.restore_max_age_seconds,
        latest_snapshot=snapshot_store.get_latest(),
    )

    return ControlPlane(
        config=config, service=service, bot=bot, dispatcher=dispatcher,
        snapshot_store=snapshot_store, startup_plan=startup_plan,
    )


__all__ = ["build_control_plane", "ControlPlane"]
```

- [ ] **Step 4: Run the bootstrap test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_bootstrap.py -v`
Expected: PASS (3 tests).

> If `telegram.Bot("123:ABC")` raises on a malformed token in the installed PTB version, change the test token to a structurally valid dummy like `"123456:AAH-dummytokendummytokendummytoken"`; `Bot(...)` construction does not contact the network.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/bootstrap.py tests/runtime_v2/control_plane/test_bootstrap.py
git commit -m "feat(control_plane): add bootstrap builder

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Integrate into main.py

**Files:**
- Modify: `main.py`

This task wires the control plane into the asyncio runtime and adds graceful shutdown. It is verified by the bootstrap/startup unit tests above plus a manual smoke run (the live Telegram polling path cannot be unit-tested without a token).

- [ ] **Step 1: Add imports**

In `main.py`, add near the other `src.runtime_v2` imports:

```python
import signal
from src.runtime_v2.control_plane.bootstrap import build_control_plane
```

- [ ] **Step 2: Build the control plane and apply startup mode**

In `_async_main`, after the execution-runtime block (after the `try/except` that builds `execution_runtime`) and before `watcher = ChannelConfigWatcher(...)`, add:

```python
    # Control Plane (Telegram bot + notification dispatcher)
    telegram_control_yaml = str(root_dir / "config" / "telegram_control.yaml")
    control_plane = build_control_plane(
        config_path=telegram_control_yaml,
        ops_db_path=ops_db_path,
        log_path=log_path,
    )
    if control_plane is not None:
        plan = control_plane.startup_plan
        logger.info("control plane startup: mode=%s block=%s",
                    plan.mode, plan.apply_global_block)
        if plan.apply_global_block:
            try:
                control_plane.service.pause(scope_value=None, created_by="startup")
            except Exception:
                logger.exception("failed to apply startup global block")
```

- [ ] **Step 3: Create the control-plane asyncio tasks**

In `_async_main`, inside the `try:` block after `worker_task = asyncio.create_task(listener.run_worker())`, add:

```python
        control_bot_task = None
        control_dispatcher_task = None
        if control_plane is not None:
            control_bot_task = asyncio.create_task(control_plane.bot.run())
            control_dispatcher_task = asyncio.create_task(control_plane.dispatcher.run())
```

- [ ] **Step 4: Register a cross-platform shutdown trigger**

In `_async_main`, right after `_main_loop = asyncio.get_running_loop()` (near the top, where the event loop is captured), add:

```python
    shutdown_event = asyncio.Event()

    def _request_shutdown(*_args) -> None:
        _main_loop.call_soon_threadsafe(shutdown_event.set)

    for _sig_name in ("SIGTERM", "SIGINT"):
        _sig = getattr(signal, _sig_name, None)
        if _sig is None:
            continue
        try:
            signal.signal(_sig, _request_shutdown)   # works on Windows for SIGTERM/SIGINT
        except (ValueError, OSError, RuntimeError):
            pass
```

- [ ] **Step 5: Race the run loop against the shutdown event**

In `_async_main`, replace:

```python
        try:
            await client.run_until_disconnected()
        finally:
```

with:

```python
        async def _disconnect_on_shutdown() -> None:
            await shutdown_event.wait()
            await client.disconnect()

        shutdown_watch_task = asyncio.create_task(_disconnect_on_shutdown())
        try:
            await client.run_until_disconnected()
        finally:
            shutdown_watch_task.cancel()
```

- [ ] **Step 6: Graceful control-plane shutdown in the existing `finally`**

In the same `finally:` block (where `worker_task.cancel()` etc. are called), add the control-plane shutdown **before** the existing cancellations:

```python
            if control_plane is not None:
                try:
                    status = control_plane.service.get_status()
                    control_plane.snapshot_store.save(
                        control_mode=status.control_mode,
                        active_blocks=[
                            f"{b.scope_type}:{b.scope_value or 'GLOBAL'}"
                            for b in control_plane.service.get_control().active_blocks
                        ],
                        open_chain_count=status.open_count + status.partial_count,
                        pending_command_count=status.pending_commands,
                        shutdown_reason="SIGTERM",
                    )
                    # Best-effort TECH_LOG shutdown notice via the outbox + one drain.
                    import sqlite3 as _sqlite3
                    from src.runtime_v2.control_plane.outbox_writer import write_tech_log_event
                    _conn = _sqlite3.connect(ops_db_path)
                    try:
                        with _conn:
                            write_tech_log_event(
                                _conn,
                                notification_type="RUNTIME_SHUTDOWN",
                                payload={
                                    "level": "INFO",
                                    "category": "Runtime",
                                    "description": "Runtime shutdown (SIGTERM). Snapshot saved.",
                                    "source": "runtime_main",
                                },
                                dedupe_key=f"tech:shutdown:{status.updated_at}",
                            )
                    finally:
                        _conn.close()
                    await control_plane.dispatcher.drain_once()
                except Exception:
                    logger.exception("control plane shutdown snapshot/notify failed")
                if control_bot_task is not None:
                    control_bot_task.cancel()
                if control_dispatcher_task is not None:
                    control_dispatcher_task.cancel()
                try:
                    await control_plane.bot.shutdown()
                    await control_plane.dispatcher.shutdown()
                except Exception:
                    logger.exception("control plane task shutdown failed")
```

- [ ] **Step 7: Verify main.py imports and the bootstrap bootstrap path**

Run: `python -c "import main; print('import ok')"`
Expected: prints `import ok` (no import-time errors).

- [ ] **Step 8: Run the existing main bootstrap test (no regression)**

Run: `python -m pytest tests/runtime_v2/test_main_runtime_bootstrap.py -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add main.py
git commit -m "feat(control_plane): integrate bot + dispatcher into main.py with SIGTERM shutdown

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## End-of-part verification

- [ ] `python -m pytest tests/runtime_v2/control_plane/ -v` — all Parts 1–5 green.
- [ ] `python -m pytest tests/runtime_v2 -q` — no regressions across runtime_v2.
- [ ] `python -c "import main"` — clean import.
- [ ] Manual smoke (needs token + chat + 3 topics):
  - Start with `startup.mode: standby` → confirm runtime starts with a global block (verify `/control` shows BLOCKED) and `/start` clears it.
  - Send `/pnl`, `/logs 10`, `/debug_on 5m`, `/debug_off` → confirm replies in COMMANDS.
  - Send SIGTERM (`taskkill` / Ctrl-C) → confirm a row appears in `ops_runtime_snapshot` and a shutdown message lands in TECH_LOG.
- [ ] Update `docs/AUDIT.md`: mark Part 5 + the whole Control Plane complete; record deferred items (TECH_LOG batch/rate-limit, full Level-3 debug emission, per-trade PnL, blacklist enforcement at signal time, CLEAN_LOG aggregation/debounce).
- [ ] Run the handoff skill (per CLAUDE.md end-of-session instruction).

---

## Self-Review

**Spec coverage (spec §7 Parte 5):** `/pnl`,`/logs`,`/debug_on [durata]`,`/debug_off` ✅ (Task 5). Files: `snapshot_store.py` ✅ (Task 1), `formatters/pnl.py`+`debug.py`+`tech_log.py` ✅ (Task 4), `service.py` (get_pnl, debug) ✅ (Task 5), `notification_dispatcher.py` TECH_LOG render ✅ (Task 4 Step 7), `main.py` tasks/SIGTERM/startup ✅ (Task 7). Startup modes auto/standby/restore + stale fallback ✅ (Task 2, `test_startup.py`). SIGTERM → snapshot + TECH_LOG ✅ (Task 7 Step 6). Excluded commands not added ✅ (`test_command_router_advanced.py::test_excluded_command_rejected`). Acceptance #17 (debug), #18 (startup modes), #19 (graceful shutdown snapshot+TECH_LOG) addressed.

**Placeholder scan:** No TBD/TODO. Every modification gives exact insertion text and a precise location relative to existing `main.py`/`service.py`/`telegram_bot.py` structures. The one conditional guidance note (PTB token validity in `test_bootstrap.py`) provides a concrete alternative value.

**Type consistency:** `PnlView` defined in `formatters/pnl.py`, imported by `service.py` and tests. `StartupPlan` fields (`mode`, `apply_global_block`, `fell_back`, `message`) consistent between `startup.py`, `bootstrap.py`, and `test_startup.py`. `DebugModeController.enable(*, duration_seconds, now=None)` / `is_active(*, now=None)` / `disable()` match controller, service, and tests. `parse_duration(text, *, max_seconds)` matches usage in `telegram_bot.py` and tests. `RuntimeControlService.__init__(*, ops_db_path, log_path=None, debug_controller=None)` is backward compatible with Parts 3–4 call sites (`RuntimeControlService(ops_db_path=...)`). `SnapshotStore.save(*, control_mode, active_blocks, open_chain_count, pending_command_count, shutdown_reason=None)` matches the `main.py` shutdown call and tests. `write_tech_log_event(conn, *, notification_type, payload, dedupe_key, priority)` (from Part 2) matches the `main.py` shutdown usage.

**Risk note:** `build_control_plane` returns `None` on any config/Bot-init failure so a misconfigured control plane never blocks the trading runtime from starting (design-spec acceptance "Il runtime non si ferma per fallimenti di notifica"). All `main.py` control-plane calls are guarded by `if control_plane is not None`.

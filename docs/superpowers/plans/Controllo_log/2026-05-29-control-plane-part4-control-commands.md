# Control Plane Telegram — Part 4: Control Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator change runtime state from Telegram: block/unblock new entries (`/pause`, `/resume`, `/start`) and manage the symbol blacklist (`/block`, `/unblock`) — globally or per trader — with idempotent, audited writes that the existing `LifecycleGateWorker` already honours.

**Architecture:** `OverrideStore` persists the symbol blacklist in `ops_config_overrides`. `RuntimeControlService` gains write methods that write `ops_control_state` (pause/resume/start) and call `OverrideStore` (block/unblock). The `CommandRouter` from Part 3 is extended to allow and dispatch the new commands, threading the operator's `user_id` as `created_by`. Two new formatters render the replies.

**Tech Stack:** Python 3.12, sqlite3, pytest.

**Depends on:** Parts 1–3 merged.

**Critical integration decision — `scope_type` for per-trader pause:** the existing `ControlStateRepository.get_effective_mode` (`src/runtime_v2/lifecycle/repositories.py:319-329`) matches `scope_type == "TRADER"`, **not** `"PER_TRADER"` as the prose COMMANDS_SPEC §7.2 states. To make `/pause <trader>` actually gate signals (acceptance #9), this part writes `scope_type = "TRADER"` for per-trader control rows. The `"PER_TRADER"` value is used only for `ops_config_overrides` (blacklist scope), which is a separate concern and is read back by `StatusQueries.get_control()` (Part 3). This divergence is recorded in `docs/AUDIT.md`.

**Cross-part contract this part publishes:**
- `override_store.py` — `OverrideStore.add_symbol/remove_symbol/get_blacklist`.
- `service.py` (extended) — `pause/resume/start/block_symbol/unblock_symbol` + result dataclasses.
- `formatters/pause.py`, `formatters/block.py` — reply renderers.
- `telegram_bot.py` (extended) — new commands allowed + dispatched.

**Scope note (honest):** blacklist **enforcement** at signal time lives in the enrichment/gate read path, which currently reads the YAML blacklist via `OperationConfigLoader` (not `ops_config_overrides`). Wiring DB-override enforcement into enrichment is **out of scope** here (CLAUDE.md forbids casually touching enrichment, and it requires a merged-read change). This part delivers exactly what the design-spec acceptance requires: `/block` **persists** to `ops_config_overrides` and `/control` **displays** it (acceptance #12–#15). The enforcement wiring is flagged as a follow-up in `docs/AUDIT.md`.

Per acceptance #15 of CLEAN_LOG_SPEC, `/pause` and `/resume` replies go **only** to the COMMANDS topic — no CLEAN_LOG message is emitted.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/runtime_v2/control_plane/override_store.py` | CRUD on `ops_config_overrides` symbol blacklist. |
| `src/runtime_v2/control_plane/service.py` (modify) | Add `pause/resume/start/block_symbol/unblock_symbol` + result dataclasses. |
| `src/runtime_v2/control_plane/formatters/pause.py` | `/pause` `/resume` `/start` replies. |
| `src/runtime_v2/control_plane/formatters/block.py` | `/block` `/unblock` replies. |
| `src/runtime_v2/control_plane/telegram_bot.py` (modify) | Allow + dispatch the new commands; pass `created_by`. |
| `tests/runtime_v2/control_plane/test_override_store.py` | Add/remove/list blacklist. |
| `tests/runtime_v2/control_plane/test_service_writes.py` | pause/resume integration with `get_effective_mode`; idempotency; block/unblock. |
| `tests/runtime_v2/control_plane/test_control_formatters.py` | pause/resume/start/block/unblock text. |
| `tests/runtime_v2/control_plane/test_command_router_writes.py` | Router dispatches the new commands and audits. |

---

### Task 1: OverrideStore

**Files:**
- Create: `src/runtime_v2/control_plane/override_store.py`
- Test: `tests/runtime_v2/control_plane/test_override_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/control_plane/test_override_store.py`:

```python
# tests/runtime_v2/control_plane/test_override_store.py
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.override_store import OverrideStore


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


def test_add_global_symbol(ops_db):
    store = OverrideStore(ops_db)
    result = store.add_symbol(scope_type="GLOBAL", scope_value=None,
                              symbol="BTCUSDT", created_by="42")
    assert result == ["BTCUSDT"]
    assert store.get_blacklist("GLOBAL", None) == ["BTCUSDT"]


def test_add_is_idempotent(ops_db):
    store = OverrideStore(ops_db)
    store.add_symbol(scope_type="GLOBAL", scope_value=None, symbol="BTCUSDT", created_by="42")
    result = store.add_symbol(scope_type="GLOBAL", scope_value=None, symbol="BTCUSDT", created_by="42")
    assert result == ["BTCUSDT"]   # no duplicate


def test_add_multiple_and_per_trader(ops_db):
    store = OverrideStore(ops_db)
    store.add_symbol(scope_type="GLOBAL", scope_value=None, symbol="BTCUSDT", created_by="42")
    store.add_symbol(scope_type="GLOBAL", scope_value=None, symbol="ETHUSDT", created_by="42")
    store.add_symbol(scope_type="PER_TRADER", scope_value="trader_a", symbol="SOLUSDT", created_by="42")
    assert set(store.get_blacklist("GLOBAL", None)) == {"BTCUSDT", "ETHUSDT"}
    assert store.get_blacklist("PER_TRADER", "trader_a") == ["SOLUSDT"]


def test_remove_symbol(ops_db):
    store = OverrideStore(ops_db)
    store.add_symbol(scope_type="GLOBAL", scope_value=None, symbol="BTCUSDT", created_by="42")
    store.add_symbol(scope_type="GLOBAL", scope_value=None, symbol="ETHUSDT", created_by="42")
    result = store.remove_symbol(scope_type="GLOBAL", scope_value=None, symbol="BTCUSDT")
    assert result == ["ETHUSDT"]


def test_remove_missing_symbol_is_noop(ops_db):
    store = OverrideStore(ops_db)
    result = store.remove_symbol(scope_type="GLOBAL", scope_value=None, symbol="NOPEUSDT")
    assert result == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_override_store.py -v`
Expected: FAIL — `ModuleNotFoundError: ...override_store`.

- [ ] **Step 3: Write the override store**

Create `src/runtime_v2/control_plane/override_store.py`:

```python
# src/runtime_v2/control_plane/override_store.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _override_key(scope_type: str) -> str:
    return "symbol_blacklist.global" if scope_type == "GLOBAL" else "symbol_blacklist.trader"


class OverrideStore:
    """Symbol blacklist persistence in ops_config_overrides (COMMANDS_SPEC §5, §8)."""

    def __init__(self, ops_db_path: str) -> None:
        self._db = ops_db_path

    def _fetch_row(self, conn, scope_type, scope_value):
        if scope_value is None:
            return conn.execute(
                "SELECT id, value_json FROM ops_config_overrides "
                "WHERE override_key=? AND scope_type=? AND scope_value IS NULL AND active=1",
                (_override_key(scope_type), scope_type),
            ).fetchone()
        return conn.execute(
            "SELECT id, value_json FROM ops_config_overrides "
            "WHERE override_key=? AND scope_type=? AND scope_value=? AND active=1",
            (_override_key(scope_type), scope_type, scope_value),
        ).fetchone()

    def get_blacklist(self, scope_type: str, scope_value: str | None) -> list[str]:
        conn = sqlite3.connect(self._db)
        try:
            row = self._fetch_row(conn, scope_type, scope_value)
        finally:
            conn.close()
        if not row:
            return []
        try:
            return list(json.loads(row[1] or "[]"))
        except Exception:
            return []

    def _upsert(self, scope_type, scope_value, symbols, created_by) -> None:
        now = _now()
        value_json = json.dumps(symbols)
        conn = sqlite3.connect(self._db)
        try:
            with conn:
                row = self._fetch_row(conn, scope_type, scope_value)
                if row:
                    conn.execute(
                        "UPDATE ops_config_overrides SET value_json=?, updated_at=? WHERE id=?",
                        (value_json, now, row[0]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO ops_config_overrides "
                        "(override_key, scope_type, scope_value, value_json, created_by, "
                        " active, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,1,?,?)",
                        (_override_key(scope_type), scope_type, scope_value, value_json,
                         created_by, now, now),
                    )
        finally:
            conn.close()

    def add_symbol(
        self, *, scope_type: str, scope_value: str | None, symbol: str, created_by: str
    ) -> list[str]:
        symbol = symbol.upper()
        current = self.get_blacklist(scope_type, scope_value)
        if symbol not in current:
            current = current + [symbol]
            self._upsert(scope_type, scope_value, current, created_by)
        return current

    def remove_symbol(
        self, *, scope_type: str, scope_value: str | None, symbol: str
    ) -> list[str]:
        symbol = symbol.upper()
        current = self.get_blacklist(scope_type, scope_value)
        if symbol in current:
            current = [s for s in current if s != symbol]
            self._upsert(scope_type, scope_value, current, created_by="system")
        return current


__all__ = ["OverrideStore"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_override_store.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/override_store.py tests/runtime_v2/control_plane/test_override_store.py
git commit -m "feat(control_plane): add symbol-blacklist override store

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Service write methods

**Files:**
- Modify: `src/runtime_v2/control_plane/service.py`
- Test: `tests/runtime_v2/control_plane/test_service_writes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/control_plane/test_service_writes.py`:

```python
# tests/runtime_v2/control_plane/test_service_writes.py
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.service import RuntimeControlService
from src.runtime_v2.lifecycle.repositories import ControlStateRepository


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


def _active_block_count(ops_db) -> int:
    conn = sqlite3.connect(ops_db)
    n = conn.execute(
        "SELECT COUNT(*) FROM ops_control_state "
        "WHERE active=1 AND execution_pause_mode='BLOCK_NEW_ENTRIES'"
    ).fetchone()[0]
    conn.close()
    return n


def test_pause_global_blocks_gate(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    svc.pause(scope_value=None, created_by="42")
    mode = ControlStateRepository(ops_db).get_effective_mode("main", "trader_a", "BTC/USDT", "LONG")
    assert mode == "BLOCK_NEW_ENTRIES"


def test_pause_per_trader_uses_TRADER_scope(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    svc.pause(scope_value="trader_a", created_by="42")
    repo = ControlStateRepository(ops_db)
    assert repo.get_effective_mode("main", "trader_a", "BTC/USDT", "LONG") == "BLOCK_NEW_ENTRIES"
    assert repo.get_effective_mode("main", "trader_b", "BTC/USDT", "LONG") == "NONE"


def test_pause_is_idempotent(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    r1 = svc.pause(scope_value=None, created_by="42")
    r2 = svc.pause(scope_value=None, created_by="42")
    assert r1.already_active is False
    assert r2.already_active is True
    assert _active_block_count(ops_db) == 1


def test_resume_global(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    svc.pause(scope_value=None, created_by="42")
    result = svc.resume(scope_value=None)
    assert result.had_block is True
    mode = ControlStateRepository(ops_db).get_effective_mode("main", "trader_a", "BTC/USDT", "LONG")
    assert mode == "NONE"


def test_resume_per_trader_only_that_trader(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    svc.pause(scope_value="trader_a", created_by="42")
    svc.pause(scope_value="trader_b", created_by="42")
    svc.resume(scope_value="trader_a")
    repo = ControlStateRepository(ops_db)
    assert repo.get_effective_mode("main", "trader_a", "BTC/USDT", "LONG") == "NONE"
    assert repo.get_effective_mode("main", "trader_b", "BTC/USDT", "LONG") == "BLOCK_NEW_ENTRIES"


def test_resume_when_no_block(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    result = svc.resume(scope_value=None)
    assert result.had_block is False


def test_start_clears_global_block(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    svc.pause(scope_value=None, created_by="42")
    svc.start()
    assert _active_block_count(ops_db) == 0


def test_block_and_unblock_symbol_visible_in_control(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    res = svc.block_symbol(scope_value=None, symbol="btcusdt", created_by="42")
    assert "BTCUSDT" in res.blacklist
    assert "BTCUSDT" in svc.get_control().blacklist_global
    res2 = svc.unblock_symbol(scope_value=None, symbol="BTCUSDT")
    assert "BTCUSDT" not in res2.blacklist
    assert "BTCUSDT" not in svc.get_control().blacklist_global


def test_block_symbol_per_trader(ops_db):
    svc = RuntimeControlService(ops_db_path=ops_db)
    svc.block_symbol(scope_value="trader_a", symbol="SOLUSDT", created_by="42")
    assert svc.get_control().blacklist_per_trader.get("trader_a") == ["SOLUSDT"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_service_writes.py -v`
Expected: FAIL — `RuntimeControlService` has no `pause` attribute.

- [ ] **Step 3: Extend `service.py`**

In `src/runtime_v2/control_plane/service.py`, add these imports at the top of the file (after the existing imports):

```python
import sqlite3
from datetime import datetime, timezone

from src.runtime_v2.control_plane.override_store import OverrideStore
```

Add these result dataclasses just after the existing `VersionInfo` dataclass:

```python
@dataclass
class PauseResult:
    scope_type: str
    scope_value: str | None
    mode: str
    already_active: bool


@dataclass
class ResumeResult:
    scope_type: str
    scope_value: str | None
    had_block: bool


@dataclass
class BlockResult:
    scope_type: str
    scope_value: str | None
    symbol: str
    blacklist: list[str]


@dataclass
class UnblockResult:
    scope_type: str
    scope_value: str | None
    symbol: str
    blacklist: list[str]
```

Add a module-level helper near `_git`:

```python
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
```

In `RuntimeControlService.__init__`, add the override store:

```python
        self._overrides = OverrideStore(ops_db_path)
```

(Place it right after `self._queries = StatusQueries(ops_db_path)`.)

Then add the write methods to the `RuntimeControlService` class (after `get_version`):

```python
    # ── writes: pause / resume / start ──────────────────────────────────────
    def pause(self, *, scope_value: str | None, created_by: str) -> "PauseResult":
        # NOTE: per-trader pause uses scope_type "TRADER" so that the existing
        # ControlStateRepository.get_effective_mode() (which matches "TRADER")
        # actually gates the trader. (See plan header — divergence from prose spec.)
        scope_type = "GLOBAL" if scope_value is None else "TRADER"
        now = _now()
        conn = sqlite3.connect(self._ops_db)
        try:
            with conn:
                if scope_value is None:
                    existing = conn.execute(
                        "SELECT 1 FROM ops_control_state WHERE active=1 "
                        "AND scope_type='GLOBAL' AND scope_value IS NULL "
                        "AND execution_pause_mode='BLOCK_NEW_ENTRIES'"
                    ).fetchone()
                else:
                    existing = conn.execute(
                        "SELECT 1 FROM ops_control_state WHERE active=1 "
                        "AND scope_type='TRADER' AND scope_value=? "
                        "AND execution_pause_mode='BLOCK_NEW_ENTRIES'",
                        (scope_value,),
                    ).fetchone()
                already_active = existing is not None
                if not already_active:
                    conn.execute(
                        "INSERT INTO ops_control_state "
                        "(scope_type, scope_value, execution_pause_mode, reason, "
                        " created_by, active, created_at, updated_at) "
                        "VALUES (?,?, 'BLOCK_NEW_ENTRIES', 'telegram:/pause', ?, 1, ?, ?)",
                        (scope_type, scope_value, created_by, now, now),
                    )
        finally:
            conn.close()
        return PauseResult(scope_type, scope_value, "BLOCK_NEW_ENTRIES", already_active)

    def resume(self, *, scope_value: str | None) -> "ResumeResult":
        scope_type = "GLOBAL" if scope_value is None else "TRADER"
        now = _now()
        conn = sqlite3.connect(self._ops_db)
        try:
            with conn:
                if scope_value is None:
                    cur = conn.execute(
                        "UPDATE ops_control_state SET active=0, updated_at=? "
                        "WHERE active=1 AND scope_type='GLOBAL' AND scope_value IS NULL "
                        "AND execution_pause_mode IN ('BLOCK_NEW_ENTRIES','FULL_STOP')",
                        (now,),
                    )
                else:
                    cur = conn.execute(
                        "UPDATE ops_control_state SET active=0, updated_at=? "
                        "WHERE active=1 AND scope_type='TRADER' AND scope_value=? "
                        "AND execution_pause_mode IN ('BLOCK_NEW_ENTRIES','FULL_STOP')",
                        (now, scope_value),
                    )
                had_block = cur.rowcount > 0
        finally:
            conn.close()
        return ResumeResult(scope_type, scope_value, had_block)

    def start(self) -> "ResumeResult":
        # Activation from standby == clear the global block.
        return self.resume(scope_value=None)

    # ── writes: symbol blacklist ────────────────────────────────────────────
    def block_symbol(
        self, *, scope_value: str | None, symbol: str, created_by: str
    ) -> "BlockResult":
        scope_type = "GLOBAL" if scope_value is None else "PER_TRADER"
        blacklist = self._overrides.add_symbol(
            scope_type=scope_type, scope_value=scope_value,
            symbol=symbol, created_by=created_by,
        )
        return BlockResult(scope_type, scope_value, symbol.upper(), blacklist)

    def unblock_symbol(
        self, *, scope_value: str | None, symbol: str
    ) -> "UnblockResult":
        scope_type = "GLOBAL" if scope_value is None else "PER_TRADER"
        blacklist = self._overrides.remove_symbol(
            scope_type=scope_type, scope_value=scope_value, symbol=symbol,
        )
        return UnblockResult(scope_type, scope_value, symbol.upper(), blacklist)
```

Finally, extend the module `__all__` to include the new result types:

```python
__all__ = [
    "RuntimeControlService", "VersionInfo",
    "PauseResult", "ResumeResult", "BlockResult", "UnblockResult",
]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_service_writes.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Confirm Part 3 read tests still pass**

Run: `python -m pytest tests/runtime_v2/control_plane/test_command_router.py tests/runtime_v2/control_plane/test_status_queries.py -q`
Expected: PASS (no regression from service changes).

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/service.py tests/runtime_v2/control_plane/test_service_writes.py
git commit -m "feat(control_plane): add pause/resume/start/block/unblock service writes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: pause / block formatters

**Files:**
- Create: `src/runtime_v2/control_plane/formatters/pause.py`, `src/runtime_v2/control_plane/formatters/block.py`
- Test: `tests/runtime_v2/control_plane/test_control_formatters.py`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/control_plane/test_control_formatters.py`:

```python
# tests/runtime_v2/control_plane/test_control_formatters.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters.block import format_block, format_unblock
from src.runtime_v2.control_plane.formatters.pause import (
    format_pause, format_resume, format_start,
)
from src.runtime_v2.control_plane.service import (
    BlockResult, PauseResult, ResumeResult, UnblockResult,
)


def test_pause_global():
    text = format_pause(PauseResult("GLOBAL", None, "BLOCK_NEW_ENTRIES", False))
    assert "BLOCCATE" in text
    assert "GLOBAL" in text
    assert "/resume" in text


def test_pause_per_trader():
    text = format_pause(PauseResult("TRADER", "trader_a", "BLOCK_NEW_ENTRIES", False))
    assert "trader_a" in text
    assert "/resume trader_a" in text


def test_pause_already_active_mentions_existing():
    text = format_pause(PauseResult("GLOBAL", None, "BLOCK_NEW_ENTRIES", True))
    assert "già" in text.lower() or "already" in text.lower()


def test_resume_with_block():
    text = format_resume(ResumeResult("GLOBAL", None, True))
    assert "RIABILITATE" in text


def test_resume_no_block():
    text = format_resume(ResumeResult("GLOBAL", None, False))
    assert "NESSUN BLOCCO" in text.upper()


def test_resume_per_trader():
    text = format_resume(ResumeResult("TRADER", "trader_a", True))
    assert "trader_a" in text


def test_start():
    text = format_start(ResumeResult("GLOBAL", None, True))
    assert "ATTIVATO" in text.upper()


def test_block_global():
    text = format_block(BlockResult("GLOBAL", None, "BTCUSDT", ["BTCUSDT", "ETHUSDT"]))
    assert "BTCUSDT" in text
    assert "GLOBAL" in text
    assert "ETHUSDT" in text   # full blacklist shown


def test_block_per_trader():
    text = format_block(BlockResult("PER_TRADER", "trader_a", "SOLUSDT", ["SOLUSDT"]))
    assert "trader_a" in text
    assert "SOLUSDT" in text


def test_unblock_global():
    text = format_unblock(UnblockResult("GLOBAL", None, "BTCUSDT", ["ETHUSDT"]))
    assert "SBLOCCATO" in text.upper()
    assert "ETHUSDT" in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_control_formatters.py -v`
Expected: FAIL — formatter modules not found.

- [ ] **Step 3: Write `formatters/pause.py`**

Create `src/runtime_v2/control_plane/formatters/pause.py`:

```python
# src/runtime_v2/control_plane/formatters/pause.py
from __future__ import annotations

from src.runtime_v2.control_plane.service import PauseResult, ResumeResult

_SEP = "────────────────"


def format_pause(result: PauseResult) -> str:
    if result.scope_value is None:
        lines = [
            "⏸️ NUOVE ENTRY BLOCCATE",
            _SEP,
            "Scope: GLOBAL",
            f"Mode: {result.mode}",
            "Set by: operator",
        ]
        if result.already_active:
            lines.append("(blocco già attivo — mantenuto)")
        lines += ["", "Effect:", "Nuovi segnali vanno in REVIEW_REQUIRED.",
                  "", "Commands:", "/resume", "/control"]
    else:
        lines = [
            f"⏸️ {result.scope_value} — NUOVE ENTRY BLOCCATE",
            _SEP,
            f"Scope: {result.scope_value}",
            f"Mode: {result.mode}",
            "Set by: operator",
        ]
        if result.already_active:
            lines.append("(blocco già attivo — mantenuto)")
        lines += ["", "Effect:", f"Nuovi segnali di {result.scope_value} vanno in REVIEW_REQUIRED.",
                  "", "Commands:", f"/resume {result.scope_value}", "/control"]
    return "\n".join(lines)


def format_resume(result: ResumeResult) -> str:
    if not result.had_block:
        return "\n".join([
            "ℹ️ NESSUN BLOCCO ATTIVO",
            _SEP,
            "Non esiste un blocco sulle nuove entry per questo scope.",
            "", "Commands:", "/control",
        ])
    if result.scope_value is None:
        return "\n".join([
            "▶️ NUOVE ENTRY RIABILITATE",
            _SEP,
            "Blocco globale rimosso.",
            "", "Effect:", "Il runtime può nuovamente accettare nuovi SIGNAL.",
            "", "Commands:", "/control", "/status",
        ])
    return "\n".join([
        f"▶️ {result.scope_value} — NUOVE ENTRY RIABILITATE",
        _SEP,
        f"Blocco su {result.scope_value} rimosso.",
        "", "Commands:", "/control",
    ])


def format_start(result: ResumeResult) -> str:
    return "\n".join([
        "▶️ RUNTIME ATTIVATO",
        _SEP,
        "Il runtime è ora operativo.",
        "Nuove entry abilitate.",
        "", "Commands:", "/status", "/control",
    ])


__all__ = ["format_pause", "format_resume", "format_start"]
```

- [ ] **Step 4: Write `formatters/block.py`**

Create `src/runtime_v2/control_plane/formatters/block.py`:

```python
# src/runtime_v2/control_plane/formatters/block.py
from __future__ import annotations

from src.runtime_v2.control_plane.service import BlockResult, UnblockResult

_SEP = "────────────────"


def _scope_label(scope_type: str, scope_value: str | None) -> str:
    return "GLOBAL" if scope_value is None else scope_value


def format_block(result: BlockResult) -> str:
    scope = _scope_label(result.scope_type, result.scope_value)
    title = (
        f"🚫 {result.symbol} BLOCCATO" if result.scope_value is None
        else f"🚫 {result.scope_value} / {result.symbol} BLOCCATO"
    )
    lines = [
        title,
        _SEP,
        f"Scope: {scope}",
        f"Effetto: segnali su {result.symbol} vanno in REVIEW_REQUIRED.",
        "",
        "Blacklist attuale:",
        ", ".join(result.blacklist) if result.blacklist else "none",
        "",
        "Commands:",
        (f"/unblock {result.symbol}" if result.scope_value is None
         else f"/unblock {result.scope_value} {result.symbol}"),
        "/control",
    ]
    return "\n".join(lines)


def format_unblock(result: UnblockResult) -> str:
    scope = _scope_label(result.scope_type, result.scope_value)
    title = (
        f"✅ {result.symbol} SBLOCCATO" if result.scope_value is None
        else f"✅ {result.scope_value} / {result.symbol} SBLOCCATO"
    )
    lines = [
        title,
        _SEP,
        f"Scope: {scope}",
        f"{result.symbol} rimosso dalla blacklist.",
        "",
        "Blacklist attuale:",
        ", ".join(result.blacklist) if result.blacklist else "none",
        "",
        "Commands:",
        "/control",
    ]
    return "\n".join(lines)


__all__ = ["format_block", "format_unblock"]
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_control_formatters.py -v`
Expected: PASS (10 tests).

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/pause.py src/runtime_v2/control_plane/formatters/block.py tests/runtime_v2/control_plane/test_control_formatters.py
git commit -m "feat(control_plane): add pause/block command formatters

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Wire control commands into the router

**Files:**
- Modify: `src/runtime_v2/control_plane/telegram_bot.py`
- Test: `tests/runtime_v2/control_plane/test_command_router_writes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/control_plane/test_command_router_writes.py`:

```python
# tests/runtime_v2/control_plane/test_command_router_writes.py
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
from src.runtime_v2.lifecycle.repositories import ControlStateRepository


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


def _router(ops_db):
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
        service=RuntimeControlService(ops_db_path=ops_db),
    )


def _route(router, text, mid):
    return router.route(command_text=text, message_id=mid,
                        chat_id=-100999, thread_id=101, user_id=42, username="op")


def test_pause_command_blocks(ops_db):
    router = _router(ops_db)
    res = _route(router, "/pause", 1)
    assert "BLOCCATE" in res.reply_text
    assert ControlStateRepository(ops_db).get_effective_mode(
        "main", "trader_a", "BTC/USDT", "LONG") == "BLOCK_NEW_ENTRIES"


def test_pause_trader_then_resume(ops_db):
    router = _router(ops_db)
    _route(router, "/pause trader_a", 2)
    repo = ControlStateRepository(ops_db)
    assert repo.get_effective_mode("main", "trader_a", "X", "LONG") == "BLOCK_NEW_ENTRIES"
    res = _route(router, "/resume trader_a", 3)
    assert "RIABILITATE" in res.reply_text
    assert repo.get_effective_mode("main", "trader_a", "X", "LONG") == "NONE"


def test_block_then_control_shows_it(ops_db):
    router = _router(ops_db)
    _route(router, "/block BTCUSDT", 4)
    ctrl = _route(router, "/control", 5)
    assert "BTCUSDT" in ctrl.reply_text


def test_block_per_trader(ops_db):
    router = _router(ops_db)
    res = _route(router, "/block trader_a SOLUSDT", 6)
    assert "SOLUSDT" in res.reply_text
    assert "trader_a" in res.reply_text


def test_pause_is_audited_executed(ops_db):
    router = _router(ops_db)
    _route(router, "/pause", 7)
    conn = sqlite3.connect(ops_db)
    status = conn.execute(
        "SELECT status FROM ops_telegram_control_commands WHERE command_request_id='-100999:7'"
    ).fetchone()[0]
    conn.close()
    assert status == "EXECUTED"


def test_block_missing_arg_usage(ops_db):
    router = _router(ops_db)
    res = _route(router, "/block", 8)
    assert "usage" in res.reply_text.lower() or "Usage" in res.reply_text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_command_router_writes.py -v`
Expected: FAIL — `/pause` is rejected as `unknown_command` (not yet allowed).

- [ ] **Step 3: Extend the router in `telegram_bot.py`**

In `src/runtime_v2/control_plane/telegram_bot.py`:

(a) Add formatter imports near the existing formatter imports:

```python
from src.runtime_v2.control_plane.formatters.block import format_block, format_unblock
from src.runtime_v2.control_plane.formatters.pause import (
    format_pause, format_resume, format_start,
)
```

(b) Replace the `_READONLY_COMMANDS` frozenset with a superset that includes the control commands:

```python
_READONLY_COMMANDS = frozenset({
    "help", "status", "trades", "trade", "health", "control", "reviews", "version",
})
_CONTROL_COMMANDS = frozenset({"pause", "resume", "start", "block", "unblock"})
_ALLOWED_COMMANDS = _READONLY_COMMANDS | _CONTROL_COMMANDS
```

(c) Change `_allowed_commands` to return the superset:

```python
    def _allowed_commands(self) -> frozenset[str]:
        return _ALLOWED_COMMANDS
```

(d) In `route()`, thread the operator id into dispatch. Change the dispatch call:

```python
            reply = self._dispatch(command_name, args)
```
to:
```python
            reply = self._dispatch(command_name, args, created_by=str(user_id))
```

(e) Change the `_dispatch` signature and add the control branches. Replace the `def _dispatch(self, command_name: str, args: list[str]) -> str:` signature with:

```python
    def _dispatch(self, command_name: str, args: list[str], *, created_by: str = "") -> str:
```

Then, inside `_dispatch`, immediately before the final `return "Comando non riconosciuto."`, add:

```python
        if command_name == "pause":
            scope = args[0] if args else None
            return format_pause(self._service.pause(scope_value=scope, created_by=created_by))
        if command_name == "resume":
            scope = args[0] if args else None
            return format_resume(self._service.resume(scope_value=scope))
        if command_name == "start":
            return format_start(self._service.start())
        if command_name == "block":
            scope, symbol = _parse_scope_symbol(args)
            if symbol is None:
                return "Usage: /block <symbol>  oppure  /block <trader> <symbol>"
            return format_block(self._service.block_symbol(
                scope_value=scope, symbol=symbol, created_by=created_by))
        if command_name == "unblock":
            scope, symbol = _parse_scope_symbol(args)
            if symbol is None:
                return "Usage: /unblock <symbol>  oppure  /unblock <trader> <symbol>"
            return format_unblock(self._service.unblock_symbol(
                scope_value=scope, symbol=symbol))
```

(f) Add the `_parse_scope_symbol` helper at module level (next to `_parse`):

```python
def _parse_scope_symbol(args: list[str]) -> tuple[str | None, str | None]:
    """One arg -> (None, symbol). Two args -> (trader, symbol). Else -> (None, None)."""
    if len(args) == 1:
        return None, args[0]
    if len(args) == 2:
        return args[0], args[1]
    return None, None
```

- [ ] **Step 4: Run the write-command router test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_command_router_writes.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the full control-plane suite**

Run: `python -m pytest tests/runtime_v2/control_plane/ -v`
Expected: PASS (all Parts 1–4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/telegram_bot.py tests/runtime_v2/control_plane/test_command_router_writes.py
git commit -m "feat(control_plane): wire pause/resume/start/block/unblock into router

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## End-of-part verification

- [ ] `python -m pytest tests/runtime_v2/control_plane/ -v` — all green.
- [ ] `python -m pytest tests/runtime_v2/lifecycle -q` — gate still reads control state correctly (no regression).
- [ ] Manual integration (optional): `/pause trader_a` then push a `trader_a` signal through enrichment → confirm the gate routes it to `REVIEW_REQUIRED` (control_mode=BLOCK_NEW_ENTRIES); `/resume trader_a` → next signal accepted.
- [ ] Update `docs/AUDIT.md`: mark Part 4 complete; record (1) the `TRADER` scope decision for per-trader pause, (2) blacklist **enforcement** at signal time deferred (only persistence + display delivered).

---

## Self-Review

**Spec coverage (spec §7 Parte 4):** `/pause [trader]`, `/resume [trader]`, `/block <symbol>`, `/block <trader> <symbol>`, `/unblock …`, `/start` ✅ (Task 4). Files: `override_store.py` ✅ (Task 1), `formatters/pause.py` + `formatters/block.py` ✅ (Task 3), `service.py` write methods ✅ (Task 2), `telegram_bot.py` new handlers ✅ (Task 4). Test requirement "integration con DB in-memory — /pause → record in ops_control_state → /control mostra blocco → /resume → record disattivato; idempotenza su doppio /pause" ✅ (`test_service_writes.py` + `test_command_router_writes.py`). Acceptance #8–#14 covered.

**Placeholder scan:** No TBD/TODO. Every modification specifies exact insertion text and location relative to existing Part 3 structures (`_READONLY_COMMANDS`, `_dispatch`, `route()`'s dispatch call).

**Type consistency:** `PauseResult/ResumeResult/BlockResult/UnblockResult` defined in `service.py` (Task 2) and imported identically by `formatters/pause.py`, `formatters/block.py`, and `test_control_formatters.py`. Service write signatures (`pause(*, scope_value, created_by)`, `resume(*, scope_value)`, `start()`, `block_symbol(*, scope_value, symbol, created_by)`, `unblock_symbol(*, scope_value, symbol)`) match all call sites in tests and the router. `OverrideStore.add_symbol/remove_symbol/get_blacklist` keyword signatures match `test_override_store.py` and `service.py`. `_dispatch(..., *, created_by)` matches the updated `route()` call.

**Integration correctness:** `pause(scope_value="trader_a")` writes `scope_type="TRADER"`, which `ControlStateRepository.get_effective_mode` matches — verified directly in `test_service_writes.py::test_pause_per_trader_uses_TRADER_scope` and `test_command_router_writes.py::test_pause_trader_then_resume`, closing the prose-spec/code gap flagged in Part 1.

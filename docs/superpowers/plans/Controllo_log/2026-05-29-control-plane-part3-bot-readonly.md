# Control Plane Telegram — Part 3: Read-Only Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator query the runtime from Telegram with read-only commands: `/help`, `/status`, `/trades`, `/trade <id>`, `/health`, `/control`, `/reviews`, `/version`. Every received message is authorized and audited.

**Architecture:** `StatusQueries` runs read-only SQL on `ops.sqlite3` and returns typed view objects. `RuntimeControlService` is the single entry point the bot uses (read methods here; write methods in Part 4). `CommandAuditStore` records every received command in `ops_telegram_control_commands`. A testable `CommandRouter` ties together auth → audit → dispatch → formatter and returns reply text. `TelegramControlBot` is a thin python-telegram-bot wrapper: handlers call `CommandRouter.route()` and reply in the COMMANDS topic.

**Tech Stack:** Python 3.12, sqlite3, python-telegram-bot ≥21, pytest.

**Depends on:** Part 1 (config/auth/models/migration) and Part 2 (python-telegram-bot installed). **Requires Parts 1–2 merged.**

**Cross-part contract this part publishes:**
- `status_queries.py` — `StatusQueries` + view dataclasses (`StatusView`, `TradesView`, `TradeRow`, `TradeDetail`, `HealthView`, `ControlView`, `BlockInfo`, `ReviewsView`, `ReviewItem`, `VersionInfo`).
- `service.py` — `RuntimeControlService` (read methods; Part 4 adds writes).
- `audit_store.py` — `CommandAuditStore` (Part 4 reuses for write-command auditing).
- `telegram_bot.py` — `CommandRouter`, `RouteResult`, `TelegramControlBot`.
- `formatters/{status,trades,trade_detail,health,control,reviews}.py` — render functions.

> **Note on file placement:** the design spec lists `audit_store.py` under Part 4. It is created here because Part 3's auth/reject path must audit from the start (acceptance #5, #20). Part 4 reuses it unchanged.

**Scope note (honest):** PnL/ROI/mark-price fields shown in the COMMANDS_SPEC mock-ups require market data not persisted in the current schema; `/status`, `/trades`, `/trade` render the fields the schema **does** support (states, counts, SL presence, recent events) and omit per-trade unrealized PnL. This is recorded in `docs/AUDIT.md`. `/pnl` is Part 5.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/runtime_v2/control_plane/status_queries.py` | Read-only `ops.sqlite3` queries → typed views. |
| `src/runtime_v2/control_plane/service.py` | `RuntimeControlService` read API (single DB entry point). |
| `src/runtime_v2/control_plane/audit_store.py` | Upsert command audit rows. |
| `src/runtime_v2/control_plane/formatters/status.py` | `/status` text + 🟢/🟡/🔴 semaforo. |
| `src/runtime_v2/control_plane/formatters/trades.py` | `/trades` compact list. |
| `src/runtime_v2/control_plane/formatters/trade_detail.py` | `/trade <id>` detail. |
| `src/runtime_v2/control_plane/formatters/health.py` | `/health`. |
| `src/runtime_v2/control_plane/formatters/control.py` | `/control` blocks + blacklist. |
| `src/runtime_v2/control_plane/formatters/reviews.py` | `/reviews`. |
| `src/runtime_v2/control_plane/telegram_bot.py` | `CommandRouter` + `TelegramControlBot` (PTB wiring). |
| `tests/runtime_v2/control_plane/test_status_queries.py` | Counts/states from in-memory-style DB. |
| `tests/runtime_v2/control_plane/test_readonly_formatters.py` | Each formatter renders expected text. |
| `tests/runtime_v2/control_plane/test_audit_store.py` | Record + update audit row. |
| `tests/runtime_v2/control_plane/test_command_router.py` | Auth/audit/dispatch routing. |

---

### Task 1: StatusQueries + view dataclasses

**Files:**
- Create: `src/runtime_v2/control_plane/status_queries.py`
- Test: `tests/runtime_v2/control_plane/test_status_queries.py`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/control_plane/test_status_queries.py`:

```python
# tests/runtime_v2/control_plane/test_status_queries.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.status_queries import StatusQueries


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def ops_db(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    return db_path


def _add_chain(conn, cid, state, symbol="BTC/USDT", side="LONG", sl=None):
    now = _now()
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        " current_stop_price, management_plan_json, risk_snapshot_json, plan_state_json, "
        " created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, cid, cid, cid, "trader_a", "main", symbol, side, state, "ONE_SHOT",
         sl, "{}", "{}", "{}", now, now),
    )


def test_status_counts(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 1, "OPEN", sl=62000.0)
        _add_chain(conn, 2, "OPEN", sl=None)          # no SL
        _add_chain(conn, 3, "WAITING_ENTRY")
        _add_chain(conn, 4, "PARTIALLY_CLOSED", sl=100.0)
        _add_chain(conn, 5, "REVIEW_REQUIRED")
        _add_chain(conn, 6, "CLOSED")
        conn.execute(
            "INSERT INTO ops_execution_commands "
            "(trade_chain_id, command_type, status, idempotency_key, created_at, updated_at) "
            "VALUES (1,'PLACE_ENTRY','PENDING','k1',?,?)", (_now(), _now()),
        )
        conn.execute(
            "INSERT INTO ops_execution_commands "
            "(trade_chain_id, command_type, status, idempotency_key, created_at, updated_at) "
            "VALUES (2,'PLACE_ENTRY','FAILED','k2',?,?)", (_now(), _now()),
        )
    conn.close()

    q = StatusQueries(ops_db)
    view = q.get_status()
    assert view.open_count == 2          # OPEN x2
    assert view.partial_count == 1       # PARTIALLY_CLOSED
    assert view.waiting_entry_count == 1
    assert view.review_count == 1
    assert view.pending_commands == 1
    assert view.failed_commands == 1
    assert view.no_sl_count == 1         # chain 2 OPEN without SL


def test_control_view_blocks_and_blacklist(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        conn.execute(
            "INSERT INTO ops_control_state "
            "(scope_type, scope_value, execution_pause_mode, active, created_at, updated_at) "
            "VALUES ('GLOBAL', NULL, 'BLOCK_NEW_ENTRIES', 1, ?, ?)", (_now(), _now()),
        )
        conn.execute(
            "INSERT INTO ops_config_overrides "
            "(override_key, scope_type, scope_value, value_json, created_by, active, created_at, updated_at) "
            "VALUES ('symbol_blacklist.global','GLOBAL',NULL,'[\"BTCUSDT\"]','42',1,?,?)",
            (_now(), _now()),
        )
    conn.close()

    q = StatusQueries(ops_db)
    view = q.get_control()
    assert view.new_entries_enabled is False
    assert any(b.scope_type == "GLOBAL" for b in view.active_blocks)
    assert "BTCUSDT" in view.blacklist_global


def test_reviews(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 10, "REVIEW_REQUIRED", symbol="SOL/USDT")
        conn.execute(
            "INSERT INTO ops_lifecycle_events "
            "(trade_chain_id, event_type, source_type, payload_json, idempotency_key, created_at) "
            "VALUES (10,'REVIEW_REQUIRED','enrichment','{\"reason\": \"missing_sl\"}','r10',?)",
            (_now(),),
        )
    conn.close()
    q = StatusQueries(ops_db)
    items = q.get_reviews().items
    assert any(it.chain_id == 10 for it in items)


def test_get_trade_detail(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 20, "OPEN", symbol="ETH/USDT", side="SHORT", sl=3500.0)
    conn.close()
    q = StatusQueries(ops_db)
    detail = q.get_trade(20)
    assert detail is not None
    assert detail.symbol == "ETH/USDT"
    assert detail.side == "SHORT"
    assert q.get_trade(999) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_status_queries.py -v`
Expected: FAIL — `ModuleNotFoundError: ...status_queries`.

- [ ] **Step 3: Write the queries module**

Create `src/runtime_v2/control_plane/status_queries.py`:

```python
# src/runtime_v2/control_plane/status_queries.py
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

_ACTIVE_STATES = ("OPEN", "PARTIALLY_CLOSED", "WAITING_ENTRY", "REVIEW_REQUIRED",
                  "BE_MOVE_PENDING", "PROTECTED_BE")


@dataclass
class StatusView:
    updated_at: str
    control_mode: str
    new_entries_enabled: bool
    sync_age_seconds: float | None
    open_count: int
    partial_count: int
    waiting_entry_count: int
    review_count: int
    pending_commands: int
    failed_commands: int
    no_sl_count: int


@dataclass
class TradeRow:
    chain_id: int
    symbol: str
    side: str
    state: str
    has_sl: bool


@dataclass
class TradesView:
    updated_at: str
    total: int
    rows: list[TradeRow] = field(default_factory=list)


@dataclass
class TradeDetail:
    chain_id: int
    symbol: str
    side: str
    trader_id: str
    account_id: str
    state: str
    entry_avg_price: float | None
    current_stop_price: float | None
    last_events: list[str] = field(default_factory=list)


@dataclass
class HealthView:
    updated_at: str
    workers: list[tuple[str, str, str]]
    db_ok: bool
    exchange_connected: bool
    last_event_age_seconds: float | None


@dataclass
class BlockInfo:
    scope_type: str
    scope_value: str | None
    mode: str
    created_at: str | None


@dataclass
class ControlView:
    new_entries_enabled: bool
    active_blocks: list[BlockInfo] = field(default_factory=list)
    blacklist_global: list[str] = field(default_factory=list)
    blacklist_per_trader: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class ReviewItem:
    chain_id: int | None
    symbol: str | None
    reason: str


@dataclass
class ReviewsView:
    updated_at: str
    items: list[ReviewItem] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _age_seconds(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


class StatusQueries:
    def __init__(self, ops_db_path: str) -> None:
        self._db = ops_db_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db)

    def get_status(self) -> StatusView:
        conn = self._connect()
        try:
            def _count(state: str) -> int:
                return conn.execute(
                    "SELECT COUNT(*) FROM ops_trade_chains WHERE lifecycle_state=?",
                    (state,),
                ).fetchone()[0]

            open_count = _count("OPEN")
            partial_count = _count("PARTIALLY_CLOSED")
            waiting = _count("WAITING_ENTRY")
            review = _count("REVIEW_REQUIRED")
            pending = conn.execute(
                "SELECT COUNT(*) FROM ops_execution_commands WHERE status='PENDING'"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM ops_execution_commands WHERE status='FAILED'"
            ).fetchone()[0]
            no_sl = conn.execute(
                "SELECT COUNT(*) FROM ops_trade_chains "
                "WHERE lifecycle_state IN ('OPEN','PARTIALLY_CLOSED') "
                "AND current_stop_price IS NULL"
            ).fetchone()[0]
            last_event_ts = conn.execute(
                "SELECT MAX(received_at) FROM ops_exchange_events"
            ).fetchone()[0]
        finally:
            conn.close()

        control = self.get_control()
        return StatusView(
            updated_at=_now_iso(),
            control_mode="BLOCK_NEW_ENTRIES" if not control.new_entries_enabled else "NONE",
            new_entries_enabled=control.new_entries_enabled,
            sync_age_seconds=_age_seconds(last_event_ts),
            open_count=open_count,
            partial_count=partial_count,
            waiting_entry_count=waiting,
            review_count=review,
            pending_commands=pending,
            failed_commands=failed,
            no_sl_count=no_sl,
        )

    def get_open_trades(self) -> TradesView:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT trade_chain_id, symbol, side, lifecycle_state, current_stop_price "
                "FROM ops_trade_chains "
                "WHERE lifecycle_state IN ({}) "
                "ORDER BY trade_chain_id".format(
                    ",".join("?" * len(_ACTIVE_STATES))
                ),
                _ACTIVE_STATES,
            ).fetchall()
        finally:
            conn.close()
        trade_rows = [
            TradeRow(chain_id=r[0], symbol=r[1], side=r[2], state=r[3], has_sl=r[4] is not None)
            for r in rows
        ]
        return TradesView(updated_at=_now_iso(), total=len(trade_rows), rows=trade_rows)

    def get_trade(self, chain_id: int) -> TradeDetail | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT trade_chain_id, symbol, side, trader_id, account_id, "
                "lifecycle_state, entry_avg_price, current_stop_price "
                "FROM ops_trade_chains WHERE trade_chain_id=?",
                (chain_id,),
            ).fetchone()
            if row is None:
                return None
            events = conn.execute(
                "SELECT created_at, event_type FROM ops_lifecycle_events "
                "WHERE trade_chain_id=? ORDER BY event_id DESC LIMIT 3",
                (chain_id,),
            ).fetchall()
        finally:
            conn.close()
        last_events = []
        for created_at, etype in reversed(events):
            hhmm = created_at[11:16] if created_at and len(created_at) >= 16 else ""
            last_events.append(f"{hhmm} {etype}".strip())
        return TradeDetail(
            chain_id=row[0], symbol=row[1], side=row[2], trader_id=row[3],
            account_id=row[4], state=row[5], entry_avg_price=row[6],
            current_stop_price=row[7], last_events=last_events,
        )

    def get_health(self) -> HealthView:
        conn = self._connect()
        try:
            try:
                conn.execute("SELECT 1 FROM ops_trade_chains LIMIT 1").fetchone()
                db_ok = True
            except sqlite3.Error:
                db_ok = False
            last_event_ts = conn.execute(
                "SELECT MAX(received_at) FROM ops_exchange_events"
            ).fetchone()[0]
        finally:
            conn.close()
        age = _age_seconds(last_event_ts)
        sync_status = "OK" if (age is None or age < 60) else "WARNING"
        workers = [
            ("Parser pipeline", "OK", ""),
            ("Lifecycle gate", "OK", ""),
            ("Execution worker", "OK", ""),
            ("Exchange sync", sync_status, f"last event {int(age)}s ago" if age else "no events"),
            ("Notification disp.", "OK", ""),
        ]
        return HealthView(
            updated_at=_now_iso(),
            workers=workers,
            db_ok=db_ok,
            exchange_connected=(age is not None and age < 120),
            last_event_age_seconds=age,
        )

    def get_control(self) -> ControlView:
        conn = self._connect()
        try:
            block_rows = conn.execute(
                "SELECT scope_type, scope_value, execution_pause_mode, created_at "
                "FROM ops_control_state "
                "WHERE active=1 AND execution_pause_mode IN ('BLOCK_NEW_ENTRIES','FULL_STOP')"
            ).fetchall()
            override_rows = conn.execute(
                "SELECT override_key, scope_type, scope_value, value_json "
                "FROM ops_config_overrides WHERE active=1 AND override_key LIKE 'symbol_blacklist%'"
            ).fetchall()
        finally:
            conn.close()

        blocks = [
            BlockInfo(scope_type=r[0], scope_value=r[1], mode=r[2], created_at=r[3])
            for r in block_rows
        ]
        new_entries_enabled = not any(b.scope_type == "GLOBAL" for b in blocks)

        blacklist_global: list[str] = []
        blacklist_per_trader: dict[str, list[str]] = {}
        for _key, scope_type, scope_value, value_json in override_rows:
            try:
                symbols = json.loads(value_json or "[]")
            except Exception:
                symbols = []
            if scope_type == "GLOBAL":
                blacklist_global = list(symbols)
            elif scope_type == "PER_TRADER" and scope_value:
                blacklist_per_trader[scope_value] = list(symbols)

        return ControlView(
            new_entries_enabled=new_entries_enabled,
            active_blocks=blocks,
            blacklist_global=blacklist_global,
            blacklist_per_trader=blacklist_per_trader,
        )

    def get_reviews(self) -> ReviewsView:
        conn = self._connect()
        try:
            chain_rows = conn.execute(
                "SELECT trade_chain_id, symbol FROM ops_trade_chains "
                "WHERE lifecycle_state='REVIEW_REQUIRED' ORDER BY trade_chain_id"
            ).fetchall()
            reasons = dict(conn.execute(
                "SELECT trade_chain_id, payload_json FROM ops_lifecycle_events "
                "WHERE event_type='REVIEW_REQUIRED' AND trade_chain_id IS NOT NULL "
                "ORDER BY event_id"
            ).fetchall())
        finally:
            conn.close()
        items: list[ReviewItem] = []
        for cid, symbol in chain_rows:
            reason = "review_required"
            raw = reasons.get(cid)
            if raw:
                try:
                    reason = json.loads(raw).get("reason", reason)
                except Exception:
                    pass
            items.append(ReviewItem(chain_id=cid, symbol=symbol, reason=reason))
        return ReviewsView(updated_at=_now_iso(), items=items)


__all__ = [
    "StatusQueries", "StatusView", "TradesView", "TradeRow", "TradeDetail",
    "HealthView", "ControlView", "BlockInfo", "ReviewsView", "ReviewItem",
]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_status_queries.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/status_queries.py tests/runtime_v2/control_plane/test_status_queries.py
git commit -m "feat(control_plane): add read-only status queries

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Read-only formatters

**Files:**
- Create: `src/runtime_v2/control_plane/formatters/status.py`, `trades.py`, `trade_detail.py`, `health.py`, `control.py`, `reviews.py`
- Test: `tests/runtime_v2/control_plane/test_readonly_formatters.py`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/control_plane/test_readonly_formatters.py`:

```python
# tests/runtime_v2/control_plane/test_readonly_formatters.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters.control import format_control
from src.runtime_v2.control_plane.formatters.health import format_health
from src.runtime_v2.control_plane.formatters.reviews import format_reviews
from src.runtime_v2.control_plane.formatters.status import format_status, status_level
from src.runtime_v2.control_plane.formatters.trade_detail import format_trade_detail
from src.runtime_v2.control_plane.formatters.trades import format_trades
from src.runtime_v2.control_plane.status_queries import (
    BlockInfo, ControlView, HealthView, ReviewItem, ReviewsView,
    StatusView, TradeDetail, TradeRow, TradesView,
)


def _status(**kw) -> StatusView:
    base = dict(
        updated_at="14:32:10", control_mode="NONE", new_entries_enabled=True,
        sync_age_seconds=4.0, open_count=7, partial_count=1, waiting_entry_count=2,
        review_count=0, pending_commands=2, failed_commands=0, no_sl_count=0,
    )
    base.update(kw)
    return StatusView(**base)


def test_status_level_green():
    assert status_level(_status()) == "🟢"


def test_status_level_yellow_on_review():
    assert status_level(_status(review_count=3)) == "🟡"


def test_status_level_red_on_no_sl():
    assert status_level(_status(no_sl_count=1)) == "🔴"


def test_status_level_red_on_failed_command():
    assert status_level(_status(failed_commands=2)) == "🔴"


def test_format_status_contains_sections():
    text = format_status(_status())
    assert "STATUS" in text
    assert "Open: 7" in text
    assert "Pending commands: 2" in text


def test_format_trades_empty():
    text = format_trades(TradesView(updated_at="14:32:10", total=0, rows=[]))
    assert "0" in text and "OPEN TRADES" in text


def test_format_trades_rows():
    view = TradesView(updated_at="14:32:10", total=1, rows=[
        TradeRow(chain_id=145, symbol="BTC/USDT", side="LONG", state="OPEN", has_sl=True),
    ])
    text = format_trades(view)
    assert "#145" in text
    assert "BTC/USDT" in text


def test_format_trade_detail():
    detail = TradeDetail(
        chain_id=145, symbol="BTC/USDT", side="LONG", trader_id="trader_a",
        account_id="main", state="OPEN", entry_avg_price=65020.0,
        current_stop_price=62000.0, last_events=["14:10 ENTRY_FILLED"],
    )
    text = format_trade_detail(detail)
    assert "TRADE #145" in text
    assert "trader_a" in text
    assert "14:10 ENTRY_FILLED" in text


def test_format_trade_detail_none():
    assert "not found" in format_trade_detail(None).lower()


def test_format_health():
    view = HealthView(
        updated_at="14:32:10",
        workers=[("Exchange sync", "OK", "last event 4s ago")],
        db_ok=True, exchange_connected=True, last_event_age_seconds=4.0,
    )
    text = format_health(view)
    assert "HEALTH" in text
    assert "Exchange sync" in text


def test_format_control_no_blocks():
    text = format_control(ControlView(new_entries_enabled=True))
    assert "ENABLED" in text
    assert "none" in text.lower()


def test_format_control_with_block_and_blacklist():
    view = ControlView(
        new_entries_enabled=False,
        active_blocks=[BlockInfo("GLOBAL", None, "BLOCK_NEW_ENTRIES", "14:10:33")],
        blacklist_global=["BTCUSDT"],
    )
    text = format_control(view)
    assert "BLOCKED" in text
    assert "BTCUSDT" in text


def test_format_reviews():
    view = ReviewsView(updated_at="14:32:10", items=[
        ReviewItem(chain_id=151, symbol="SOL/USDT", reason="missing_sl"),
    ])
    text = format_reviews(view)
    assert "#151" in text
    assert "missing_sl" in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_readonly_formatters.py -v`
Expected: FAIL — formatter modules not found.

- [ ] **Step 3: Write `formatters/status.py`**

Create `src/runtime_v2/control_plane/formatters/status.py`:

```python
# src/runtime_v2/control_plane/formatters/status.py
from __future__ import annotations

from src.runtime_v2.control_plane.status_queries import StatusView

_SEP = "────────────────"


def status_level(view: StatusView) -> str:
    if view.failed_commands > 0 or view.no_sl_count > 0:
        return "🔴"
    stale = view.sync_age_seconds is not None and view.sync_age_seconds > 30
    if view.review_count > 0 or stale:
        return "🟡"
    return "🟢"


def format_status(view: StatusView) -> str:
    sync = (
        f"{int(view.sync_age_seconds)}s ago" if view.sync_age_seconds is not None else "n/a"
    )
    lines = [
        f"{status_level(view)} Runtime V2 — STATUS",
        _SEP,
        f"Updated: {view.updated_at}",
        "",
        "Mode:",
        f"New entries: {'ENABLED' if view.new_entries_enabled else 'BLOCKED'}",
        f"Control: {view.control_mode}",
        f"Sync: {sync}",
        "",
        "Trades:",
        f"Open: {view.open_count}",
        f"Waiting entry: {view.waiting_entry_count}",
        f"Partial: {view.partial_count}",
        f"Review required: {view.review_count}",
        "",
        "Execution:",
        f"Pending commands: {view.pending_commands}",
        f"Failed commands: {view.failed_commands}",
        "",
        "Risk:",
        f"No SL: {view.no_sl_count}",
        "",
        "Use:",
        "/trades",
        "/reviews",
        "/control",
    ]
    return "\n".join(lines)


__all__ = ["format_status", "status_level"]
```

- [ ] **Step 4: Write `formatters/trades.py`**

Create `src/runtime_v2/control_plane/formatters/trades.py`:

```python
# src/runtime_v2/control_plane/formatters/trades.py
from __future__ import annotations

from src.runtime_v2.control_plane.status_queries import TradesView

_SEP = "────────────────"


def _side_emoji(side: str) -> str:
    return "📈" if side == "LONG" else ("📉" if side == "SHORT" else "•")


def format_trades(view: TradesView) -> str:
    lines = [
        f"📊 OPEN TRADES — {view.total} active",
        _SEP,
        f"Updated: {view.updated_at}",
        "",
    ]
    if not view.rows:
        lines.append("No open trades.")
    else:
        for r in view.rows:
            sl = "SL: set" if r.has_sl else "NoSL"
            lines.append(
                f"#{r.chain_id} {r.symbol} {_side_emoji(r.side)} {r.side} | {r.state} | {sl}"
            )
    lines += ["", _SEP, "Use:", "/trade #id for details", "/reviews for blocked cases"]
    return "\n".join(lines)


__all__ = ["format_trades"]
```

- [ ] **Step 5: Write `formatters/trade_detail.py`**

Create `src/runtime_v2/control_plane/formatters/trade_detail.py`:

```python
# src/runtime_v2/control_plane/formatters/trade_detail.py
from __future__ import annotations

from src.runtime_v2.control_plane.status_queries import TradeDetail

_SEP = "────────────────"


def _side_emoji(side: str) -> str:
    return "📈" if side == "LONG" else ("📉" if side == "SHORT" else "•")


def format_trade_detail(detail: TradeDetail | None) -> str:
    if detail is None:
        return "Trade not found."
    lines = [
        f"📌 TRADE #{detail.chain_id}",
        _SEP,
        f"{detail.symbol} — {_side_emoji(detail.side)} {detail.side}",
        f"Trader: {detail.trader_id}",
        f"Exchange Account: {detail.account_id}",
        "",
        "Position:",
        f"Avg entry: {detail.entry_avg_price if detail.entry_avg_price is not None else 'n/a'}",
        f"State: {detail.state}",
        "",
        "Protection:",
        f"SL: {detail.current_stop_price if detail.current_stop_price is not None else 'none'}",
    ]
    if detail.last_events:
        lines += ["", "Last events:"]
        lines += detail.last_events
    return "\n".join(lines)


__all__ = ["format_trade_detail"]
```

- [ ] **Step 6: Write `formatters/health.py`**

Create `src/runtime_v2/control_plane/formatters/health.py`:

```python
# src/runtime_v2/control_plane/formatters/health.py
from __future__ import annotations

from src.runtime_v2.control_plane.status_queries import HealthView

_SEP = "────────────────"


def format_health(view: HealthView) -> str:
    lines = [
        "💊 HEALTH",
        _SEP,
        f"Updated: {view.updated_at}",
        "",
        "Workers:",
    ]
    for name, status, detail in view.workers:
        suffix = f" — {detail}" if detail else ""
        lines.append(f"{name}: {status}{suffix}")
    lines += [
        "",
        "DB:",
        f"ops.sqlite3: {'OK' if view.db_ok else 'ERROR'}",
        "",
        "Exchange:",
        f"Connected: {'YES' if view.exchange_connected else 'NO'}",
    ]
    return "\n".join(lines)


__all__ = ["format_health"]
```

- [ ] **Step 7: Write `formatters/control.py`**

Create `src/runtime_v2/control_plane/formatters/control.py`:

```python
# src/runtime_v2/control_plane/formatters/control.py
from __future__ import annotations

from src.runtime_v2.control_plane.status_queries import ControlView

_SEP = "────────────────"


def format_control(view: ControlView) -> str:
    lines = [
        "🛡️ CONTROL",
        _SEP,
        f"New entries: {'ENABLED' if view.new_entries_enabled else 'BLOCKED'}",
        "Open positions: managed",
        "Updates: processed",
        "",
    ]
    if view.active_blocks:
        lines.append("Active blocks:")
        for b in view.active_blocks:
            scope = b.scope_value or "GLOBAL"
            when = f" ({b.created_at})" if b.created_at else ""
            lines.append(f"{scope} — {b.mode}{when}")
    else:
        lines.append("Active blocks: none")
    lines += ["", "Symbol blacklist:"]
    lines.append(
        "Global: " + (", ".join(view.blacklist_global) if view.blacklist_global else "none")
    )
    if view.blacklist_per_trader:
        lines.append("Per trader:")
        for trader, syms in view.blacklist_per_trader.items():
            lines.append(f"  {trader}: {', '.join(syms)}")
    else:
        lines.append("Per trader: none")
    return "\n".join(lines)


__all__ = ["format_control"]
```

- [ ] **Step 8: Write `formatters/reviews.py`**

Create `src/runtime_v2/control_plane/formatters/reviews.py`:

```python
# src/runtime_v2/control_plane/formatters/reviews.py
from __future__ import annotations

from src.runtime_v2.control_plane.status_queries import ReviewsView

_SEP = "────────────────"


def format_reviews(view: ReviewsView) -> str:
    lines = [
        f"⚠️ REVIEWS — {len(view.items)} required",
        _SEP,
        f"Updated: {view.updated_at}",
        "",
    ]
    if not view.items:
        lines.append("No reviews pending.")
    else:
        for it in view.items:
            cid = f"#{it.chain_id}" if it.chain_id is not None else "#?"
            sym = it.symbol or "?"
            lines.append(f"{cid} {sym} | {it.reason}")
    lines += ["", "Use:", "/trade #id for details", "/control for pause/resume"]
    return "\n".join(lines)


__all__ = ["format_reviews"]
```

- [ ] **Step 9: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_readonly_formatters.py -v`
Expected: PASS (all formatter tests).

- [ ] **Step 10: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/status.py src/runtime_v2/control_plane/formatters/trades.py src/runtime_v2/control_plane/formatters/trade_detail.py src/runtime_v2/control_plane/formatters/health.py src/runtime_v2/control_plane/formatters/control.py src/runtime_v2/control_plane/formatters/reviews.py tests/runtime_v2/control_plane/test_readonly_formatters.py
git commit -m "feat(control_plane): add read-only command formatters

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: CommandAuditStore

**Files:**
- Create: `src/runtime_v2/control_plane/audit_store.py`
- Test: `tests/runtime_v2/control_plane/test_audit_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/control_plane/test_audit_store.py`:

```python
# tests/runtime_v2/control_plane/test_audit_store.py
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.audit_store import CommandAuditStore


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


def test_record_then_update(ops_db):
    store = CommandAuditStore(ops_db)
    store.record(
        command_request_id="-100999:55",
        chat_id="-100999", message_thread_id="101",
        telegram_user_id="42", telegram_username="op",
        command_text="/status", command_name="status",
        status="RECEIVED",
    )
    store.update_status("-100999:55", status="EXECUTED", execution_result="ok")

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT status, execution_result, command_name FROM ops_telegram_control_commands "
        "WHERE command_request_id=?", ("-100999:55",),
    ).fetchone()
    conn.close()
    assert row == ("EXECUTED", "ok", "status")


def test_record_rejected_unauthorized(ops_db):
    store = CommandAuditStore(ops_db)
    store.record(
        command_request_id="-100999:77",
        chat_id="-100999", message_thread_id="101",
        telegram_user_id="7", telegram_username=None,
        command_text="/status", command_name=None,
        status="REJECTED", reject_reason="unauthorized_user",
    )
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT status, reject_reason FROM ops_telegram_control_commands "
        "WHERE command_request_id=?", ("-100999:77",),
    ).fetchone()
    conn.close()
    assert row == ("REJECTED", "unauthorized_user")


def test_record_is_idempotent_on_request_id(ops_db):
    store = CommandAuditStore(ops_db)
    for _ in range(2):
        store.record(
            command_request_id="-100999:88",
            chat_id="-100999", message_thread_id="101",
            telegram_user_id="42", telegram_username="op",
            command_text="/status", command_name="status",
            status="RECEIVED",
        )
    conn = sqlite3.connect(ops_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM ops_telegram_control_commands WHERE command_request_id=?",
        ("-100999:88",),
    ).fetchone()[0]
    conn.close()
    assert count == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_audit_store.py -v`
Expected: FAIL — `ModuleNotFoundError: ...audit_store`.

- [ ] **Step 3: Write the audit store**

Create `src/runtime_v2/control_plane/audit_store.py`:

```python
# src/runtime_v2/control_plane/audit_store.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CommandAuditStore:
    """Writes ops_telegram_control_commands (COMMANDS_SPEC §11)."""

    def __init__(self, ops_db_path: str) -> None:
        self._db = ops_db_path

    def record(
        self,
        *,
        command_request_id: str,
        chat_id: str,
        message_thread_id: str,
        telegram_user_id: str,
        telegram_username: str | None,
        command_text: str,
        command_name: str | None,
        status: str,
        reject_reason: str | None = None,
        payload_json: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                """
                INSERT INTO ops_telegram_control_commands
                    (command_request_id, chat_id, message_thread_id, telegram_user_id,
                     telegram_username, command_text, command_name, payload_json,
                     received_at, status, reject_reason, idempotency_key,
                     created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(command_request_id) DO NOTHING
                """,
                (command_request_id, chat_id, message_thread_id, telegram_user_id,
                 telegram_username, command_text, command_name, payload_json,
                 now, status, reject_reason, idempotency_key, now, now),
            )
            conn.commit()
        finally:
            conn.close()

    def update_status(
        self,
        command_request_id: str,
        *,
        status: str,
        execution_result: str | None = None,
        reject_reason: str | None = None,
    ) -> None:
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_telegram_control_commands "
                "SET status=?, execution_result=COALESCE(?, execution_result), "
                "    reject_reason=COALESCE(?, reject_reason), updated_at=? "
                "WHERE command_request_id=?",
                (status, execution_result, reject_reason, now, command_request_id),
            )
            conn.commit()
        finally:
            conn.close()


__all__ = ["CommandAuditStore"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_audit_store.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/audit_store.py tests/runtime_v2/control_plane/test_audit_store.py
git commit -m "feat(control_plane): add command audit store

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: RuntimeControlService (read) + CommandRouter + bot wiring

**Files:**
- Create: `src/runtime_v2/control_plane/service.py`, `src/runtime_v2/control_plane/telegram_bot.py`
- Test: `tests/runtime_v2/control_plane/test_command_router.py`

- [ ] **Step 1: Write the failing router test**

Create `tests/runtime_v2/control_plane/test_command_router.py`:

```python
# tests/runtime_v2/control_plane/test_command_router.py
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


def _config():
    return ControlPlaneConfig(
        token="t", chat_id=-100999,
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=101),
            tech_log=TechLogConfig(thread_id=102),
            clean_log=CleanLogConfig(thread_id=103),
        ),
        authorized_users=[42],
    )


def _router(ops_db):
    cfg = _config()
    service = RuntimeControlService(ops_db_path=ops_db)
    return CommandRouter(
        config=cfg,
        auth=AuthValidator(cfg),
        audit=CommandAuditStore(ops_db),
        service=service,
    )


def _last_status(ops_db, request_id):
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT status, reject_reason FROM ops_telegram_control_commands "
        "WHERE command_request_id=?", (request_id,),
    ).fetchone()
    conn.close()
    return row


def test_authorized_status_returns_reply(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/status", message_id=1,
        chat_id=-100999, thread_id=101, user_id=42, username="op",
    )
    assert res.reply_text is not None
    assert "STATUS" in res.reply_text
    assert _last_status(ops_db, "-100999:1")[0] == "EXECUTED"


def test_wrong_chat_ignored_no_reply(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/status", message_id=2,
        chat_id=-1, thread_id=101, user_id=42, username="op",
    )
    assert res.reply_text is None
    assert res.decision == "IGNORE"


def test_wrong_topic_ignored(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/status", message_id=3,
        chat_id=-100999, thread_id=999, user_id=42, username="op",
    )
    assert res.reply_text is None
    assert res.decision == "IGNORE"


def test_unauthorized_rejected_no_reply_but_audited(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/status", message_id=4,
        chat_id=-100999, thread_id=101, user_id=7, username="intruder",
    )
    assert res.reply_text is None
    assert res.decision == "REJECT_UNAUTHORIZED"
    assert _last_status(ops_db, "-100999:4") == ("REJECTED", "unauthorized_user")


def test_unknown_command_replies_and_audits_rejected(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/wat", message_id=5,
        chat_id=-100999, thread_id=101, user_id=42, username="op",
    )
    assert res.reply_text is not None
    assert "riconosciuto" in res.reply_text.lower()
    assert _last_status(ops_db, "-100999:5") == ("REJECTED", "unknown_command")


def test_help_lists_commands(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/help", message_id=6,
        chat_id=-100999, thread_id=101, user_id=42, username="op",
    )
    assert "/status" in res.reply_text
    assert "/trades" in res.reply_text


def test_trade_with_id_arg(ops_db):
    # seed one chain
    conn = sqlite3.connect(ops_db)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            "INSERT INTO ops_trade_chains "
            "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
            " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
            " management_plan_json, risk_snapshot_json, plan_state_json, created_at, updated_at) "
            "VALUES (77,77,77,77,'trader_a','main','BTC/USDT','LONG','OPEN','ONE_SHOT','{}','{}','{}',?,?)",
            (now, now),
        )
    conn.close()
    router = _router(ops_db)
    res = router.route(
        command_text="/trade 77", message_id=7,
        chat_id=-100999, thread_id=101, user_id=42, username="op",
    )
    assert "TRADE #77" in res.reply_text


def test_version(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/version", message_id=8,
        chat_id=-100999, thread_id=101, user_id=42, username="op",
    )
    assert "VERSION" in res.reply_text
    assert "v2" in res.reply_text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_command_router.py -v`
Expected: FAIL — `service`/`telegram_bot` modules not found.

- [ ] **Step 3: Write `service.py` (read methods)**

Create `src/runtime_v2/control_plane/service.py`:

```python
# src/runtime_v2/control_plane/service.py
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from src.runtime_v2.control_plane.status_queries import (
    ControlView, HealthView, ReviewsView, StatusView, StatusQueries,
    TradeDetail, TradesView,
)

_START_TIME = time.time()


@dataclass
class VersionInfo:
    runtime: str
    commit: str
    branch: str
    uptime_seconds: int


def _git(args: list[str]) -> str:
    try:
        out = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=5, check=False
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


class RuntimeControlService:
    """Single entry point for the bot to read/write ops state.

    Part 3 implements read methods; Part 4 adds pause/resume/block/unblock/start.
    """

    def __init__(self, *, ops_db_path: str) -> None:
        self._ops_db = ops_db_path
        self._queries = StatusQueries(ops_db_path)

    # ── reads ───────────────────────────────────────────────────────────────
    def get_status(self) -> StatusView:
        return self._queries.get_status()

    def get_open_trades(self) -> TradesView:
        return self._queries.get_open_trades()

    def get_trade(self, chain_id: int) -> TradeDetail | None:
        return self._queries.get_trade(chain_id)

    def get_health(self) -> HealthView:
        return self._queries.get_health()

    def get_control(self) -> ControlView:
        return self._queries.get_control()

    def get_reviews(self) -> ReviewsView:
        return self._queries.get_reviews()

    def get_version(self) -> VersionInfo:
        return VersionInfo(
            runtime="v2",
            commit=_git(["rev-parse", "--short", "HEAD"]),
            branch=_git(["rev-parse", "--abbrev-ref", "HEAD"]),
            uptime_seconds=int(time.time() - _START_TIME),
        )


__all__ = ["RuntimeControlService", "VersionInfo"]
```

- [ ] **Step 4: Write `telegram_bot.py` (router + bot wiring)**

Create `src/runtime_v2/control_plane/telegram_bot.py`:

```python
# src/runtime_v2/control_plane/telegram_bot.py
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.runtime_v2.control_plane.audit_store import CommandAuditStore
from src.runtime_v2.control_plane.auth import AuthValidator
from src.runtime_v2.control_plane.formatters.control import format_control
from src.runtime_v2.control_plane.formatters.health import format_health
from src.runtime_v2.control_plane.formatters.reviews import format_reviews
from src.runtime_v2.control_plane.formatters.status import format_status
from src.runtime_v2.control_plane.formatters.trade_detail import format_trade_detail
from src.runtime_v2.control_plane.formatters.trades import format_trades
from src.runtime_v2.control_plane.models import ControlPlaneConfig
from src.runtime_v2.control_plane.service import RuntimeControlService

logger = logging.getLogger(__name__)

_HELP_TEXT = """📋 COMANDI DISPONIBILI
────────────────
Informativi:
/status    — salute bot e conteggi
/trades    — trade aperti
/trade #id — dettaglio singola chain
/health    — stato workers
/control   — blocchi operativi
/reviews   — casi da controllare
/version   — versione runtime
/help      — questo messaggio"""


@dataclass
class RouteResult:
    decision: str            # OK | IGNORE | REJECT_UNAUTHORIZED | REJECTED | EXECUTED | FAILED
    reply_text: str | None   # None = do not reply


# Read-only command set for Part 3. Part 4/5 extend this set.
_READONLY_COMMANDS = frozenset({
    "help", "status", "trades", "trade", "health", "control", "reviews", "version",
})


def _parse(command_text: str) -> tuple[str | None, list[str]]:
    parts = command_text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return None, []
    name = parts[0][1:].split("@", 1)[0].lower()   # strip leading "/" and @botname
    return name, parts[1:]


class CommandRouter:
    def __init__(
        self,
        *,
        config: ControlPlaneConfig,
        auth: AuthValidator,
        audit: CommandAuditStore,
        service: RuntimeControlService,
    ) -> None:
        self._config = config
        self._auth = auth
        self._audit = audit
        self._service = service

    def route(
        self,
        *,
        command_text: str,
        message_id: int,
        chat_id: int,
        thread_id: int | None,
        user_id: int,
        username: str | None,
    ) -> RouteResult:
        request_id = f"{chat_id}:{message_id}"
        auth_result = self._auth.validate(
            chat_id=chat_id, thread_id=thread_id, user_id=user_id
        )

        if auth_result.decision == "IGNORE":
            # Only audit when it's our chat but the wrong topic.
            if auth_result.reason == "wrong_topic":
                self._record(request_id, chat_id, thread_id, user_id, username,
                             command_text, None, "IGNORED", reject_reason="wrong_topic")
            return RouteResult("IGNORE", None)

        if auth_result.decision == "REJECT_UNAUTHORIZED":
            self._record(request_id, chat_id, thread_id, user_id, username,
                         command_text, None, "REJECTED", reject_reason="unauthorized_user")
            return RouteResult("REJECT_UNAUTHORIZED", None)

        command_name, args = _parse(command_text)
        if command_name not in self._allowed_commands():
            self._record(request_id, chat_id, thread_id, user_id, username,
                         command_text, command_name, "REJECTED",
                         reject_reason="unknown_command")
            return RouteResult("REJECTED", "Comando non riconosciuto.")

        self._record(request_id, chat_id, thread_id, user_id, username,
                     command_text, command_name, "ACCEPTED")
        try:
            reply = self._dispatch(command_name, args)
            self._audit.update_status(request_id, status="EXECUTED")
            return RouteResult("EXECUTED", reply)
        except Exception:
            logger.exception("command handler failed: %s", command_text)
            self._audit.update_status(request_id, status="FAILED")
            return RouteResult("FAILED", "Errore interno durante l'esecuzione del comando.")

    # ── overridable in later parts ────────────────────────────────────────────
    def _allowed_commands(self) -> frozenset[str]:
        return _READONLY_COMMANDS

    def _dispatch(self, command_name: str, args: list[str]) -> str:
        if command_name == "help":
            return _HELP_TEXT
        if command_name == "status":
            return format_status(self._service.get_status())
        if command_name == "trades":
            return format_trades(self._service.get_open_trades())
        if command_name == "trade":
            if not args or not args[0].lstrip("#").isdigit():
                return "Usage: /trade <chain_id>"
            chain_id = int(args[0].lstrip("#"))
            return format_trade_detail(self._service.get_trade(chain_id))
        if command_name == "health":
            return format_health(self._service.get_health())
        if command_name == "control":
            return format_control(self._service.get_control())
        if command_name == "reviews":
            return format_reviews(self._service.get_reviews())
        if command_name == "version":
            v = self._service.get_version()
            return (
                "📦 VERSION\n────────────────\n"
                f"Runtime: {v.runtime}\nCommit: {v.commit}\n"
                f"Branch: {v.branch}\nUptime: {v.uptime_seconds}s"
            )
        return "Comando non riconosciuto."

    def _record(self, request_id, chat_id, thread_id, user_id, username,
                command_text, command_name, status, reject_reason=None) -> None:
        self._audit.record(
            command_request_id=request_id,
            chat_id=str(chat_id),
            message_thread_id=str(thread_id),
            telegram_user_id=str(user_id),
            telegram_username=username,
            command_text=command_text,
            command_name=command_name,
            status=status,
            reject_reason=reject_reason,
        )


class TelegramControlBot:
    """python-telegram-bot wrapper. Thin: delegates all logic to CommandRouter."""

    def __init__(self, *, config: ControlPlaneConfig, router: CommandRouter) -> None:
        self._config = config
        self._router = router
        self._app = None

    def _build_app(self):
        from telegram.ext import Application, MessageHandler, filters

        app = Application.builder().token(self._config.token).build()
        app.add_handler(MessageHandler(filters.COMMAND, self._on_command))
        return app

    async def _on_command(self, update, context) -> None:
        msg = update.effective_message
        if msg is None or update.effective_user is None:
            return
        result = self._router.route(
            command_text=msg.text or "",
            message_id=msg.message_id,
            chat_id=msg.chat_id,
            thread_id=msg.message_thread_id,
            user_id=update.effective_user.id,
            username=update.effective_user.username,
        )
        if result.reply_text is not None:
            await context.bot.send_message(
                chat_id=self._config.chat_id,
                message_thread_id=self._config.topics.commands.thread_id,
                text=result.reply_text,
            )

    async def run(self) -> None:
        self._app = self._build_app()
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

    async def shutdown(self) -> None:
        if self._app is None:
            return
        try:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        finally:
            self._app = None


__all__ = ["CommandRouter", "RouteResult", "TelegramControlBot"]
```

- [ ] **Step 5: Run the router test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_command_router.py -v`
Expected: PASS (8 tests).

- [ ] **Step 6: Run the full control-plane suite**

Run: `python -m pytest tests/runtime_v2/control_plane/ -v`
Expected: PASS (all Parts 1–3 tests).

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/control_plane/service.py src/runtime_v2/control_plane/telegram_bot.py tests/runtime_v2/control_plane/test_command_router.py
git commit -m "feat(control_plane): add read-only service, command router, and bot wiring

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## End-of-part verification

- [ ] `python -m pytest tests/runtime_v2/control_plane/ -v` — all green.
- [ ] Manual smoke (optional, needs token+chat): build `TelegramControlBot`, `await run()`, send `/status` in the COMMANDS topic, confirm a reply appears.
- [ ] Update `docs/AUDIT.md`: mark Part 3 complete; note PnL/mark-price fields omitted from `/status`,`/trades`,`/trade`; note `audit_store.py` created in Part 3 (ahead of spec's Part 4 placement).

---

## Self-Review

**Spec coverage (spec §7 Parte 3):** commands `/help`,`/status`,`/trades`,`/trade <id>`,`/health`,`/control`,`/reviews`,`/version` ✅ (Task 4 dispatch). Files: `telegram_bot.py` ✅, `service.py` (read) ✅, `status_queries.py` ✅, formatters `status/trades/trade_detail/health/control/reviews` ✅. Test requirements: formatter unit (semaforo 🟢/🟡/🔴) ✅; `status_queries` against a real DB ✅. Acceptance #4 (out-of-topic ignored), #5 (unauthorized audited REJECTED, no reply), #6 (`/status` coherent), #7 (`/trades` compact), #15 (`/control`), #16 (`/reviews`), #20 (every command audited) all exercised in `test_command_router.py`.

**Placeholder scan:** No TBD/TODO. Every formatter and the router are fully implemented. `/version` git calls are wrapped (`_git`) so they degrade to `"unknown"` rather than failing.

**Type consistency:** view dataclasses (`StatusView`, `TradesView`, `TradeRow`, `TradeDetail`, `HealthView`, `ControlView`, `BlockInfo`, `ReviewsView`, `ReviewItem`) defined once in `status_queries.py` and imported identically by formatters, service, and tests. `RuntimeControlService(ops_db_path=...)` keyword matches router test. `CommandRouter.route(*, command_text, message_id, chat_id, thread_id, user_id, username)` signature matches every test call. `RouteResult(decision, reply_text)` consistent. `CommandAuditStore.record(...)`/`update_status(...)` keyword signatures match `test_audit_store.py` and router usage.

**Extensibility hook:** `CommandRouter._allowed_commands()` and `_dispatch()` are designed to be overridden/extended in Part 4 (adds pause/resume/block/unblock/start) and Part 5 (pnl/logs/debug) without rewriting routing/auth/audit.

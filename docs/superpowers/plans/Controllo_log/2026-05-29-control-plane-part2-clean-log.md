# Control Plane Telegram — Part 2: CLEAN_LOG Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver operator-facing CLEAN_LOG notifications to Telegram: lifecycle/exchange events are projected into an outbox table by the existing workers, and an async dispatcher drains the outbox, formats each entry, and sends it to the CLEAN_LOG topic.

**Architecture:** Outbox pattern. Workers never call Telegram. Inside their existing `with conn:` transactions they call `project_clean_log_for_chain(conn, chain_id)`, which reads the lifecycle events just written and inserts CLEAN_LOG rows into `ops_notification_outbox` (idempotent via UNIQUE `dedupe_key`). A `TelegramNotificationDispatcher` polls the outbox every ~2s, routes each entry to a topic via `TopicRouter`, renders text via a formatter, and sends through a `NotificationSender` (real impl wraps `telegram.Bot`; tests use a fake). Failures retry with attempt counting; after 3 attempts the entry is marked `FAILED` and the runtime continues.

**Tech Stack:** Python 3.12, Pydantic v2, sqlite3, asyncio, python-telegram-bot ≥21.

**Depends on:** Part 1 (migration 007, `models.py`, `config.py`). **This part requires Part 1 merged.**

**Cross-part contract this part publishes:**
- `outbox_writer.py` — `write_clean_log_event(conn, …)`, `write_tech_log_event(conn, …)`, `project_clean_log_for_chain(conn, chain_id)`.
- `topic_router.py` — `TopicRouter.resolve(destination) -> (chat_id, thread_id)`.
- `notification_dispatcher.py` — `TelegramNotificationDispatcher` (+ `NotificationSender` protocol, `TelegramBotSender`, used by Part 5 for TECH_LOG too).
- `formatters/clean_log.py` — `format_clean_log(notification_type, payload) -> str`.

**Scope note (honest simplification):** CLEAN_LOG aggregation/debounce (CLEAN_LOG_SPEC §6–§8, §15) is **not** fully enforced in this part. Each significant lifecycle event yields one CLEAN_LOG message; the debounce/aggregate config fields are loaded but not yet applied. `ENTRY_FILLED` maps to `ENTRY_OPENED` (the `ENTRY_UPDATED` distinction and TP batching are deferred). This is recorded in `docs/AUDIT.md` as a follow-up. Multi-chain summaries and reconciliation messages are out of scope here.

---

## File Structure

| File | Responsibility |
|---|---|
| `requirements.txt` (modify) | Add `python-telegram-bot>=21.0`. |
| `src/runtime_v2/control_plane/outbox_writer.py` | Worker-side API: write outbox rows + project lifecycle events → CLEAN_LOG. |
| `src/runtime_v2/control_plane/topic_router.py` | Map destination → `(chat_id, thread_id)`. |
| `src/runtime_v2/control_plane/notification_dispatcher.py` | Async drain loop + `NotificationSender` protocol + `TelegramBotSender`. |
| `src/runtime_v2/control_plane/formatters/__init__.py` | Package marker. |
| `src/runtime_v2/control_plane/formatters/clean_log.py` | Render CLEAN_LOG message text. |
| `src/runtime_v2/lifecycle/entry_gate.py` (modify) | Call projection after persisting signal events. |
| `src/runtime_v2/lifecycle/workers.py` (modify) | Call projection after persisting fill events. |
| `tests/runtime_v2/control_plane/conftest.py` | Async test hook (`pytest_pyfunc_call`). |
| `tests/runtime_v2/control_plane/test_outbox_writer.py` | Outbox insert + dedupe + projection mapping. |
| `tests/runtime_v2/control_plane/test_topic_router.py` | Destination routing. |
| `tests/runtime_v2/control_plane/test_clean_log_formatter.py` | Per-event text rendering. |
| `tests/runtime_v2/control_plane/test_dispatcher.py` | Drain → send, retry, FAILED, dedupe. |
| `tests/runtime_v2/control_plane/test_worker_clean_log_integration.py` | Worker persist → outbox row. |

---

### Task 1: Add python-telegram-bot dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add the dependency line**

Edit `requirements.txt` — append after the `ccxt>=4.4.0` line:

```
python-telegram-bot>=21.0
```

- [ ] **Step 2: Install it**

Run: `python -m pip install "python-telegram-bot>=21.0"`
Expected: installs `python-telegram-bot` and its deps; no errors.

- [ ] **Step 3: Verify import**

Run: `python -c "import telegram; from telegram.ext import Application; print(telegram.__version__)"`
Expected: prints a version `21.x` (or newer).

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "build: add python-telegram-bot dependency

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Async test hook conftest

**Files:**
- Create: `tests/runtime_v2/control_plane/conftest.py`

This mirrors the project's existing async pattern (`src/telegram/tests/conftest.py`) so `async def test_*` functions run under `asyncio.run`.

- [ ] **Step 1: Create the conftest**

Create `tests/runtime_v2/control_plane/conftest.py`:

```python
# tests/runtime_v2/control_plane/conftest.py
from __future__ import annotations

import asyncio

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    test_func = pyfuncitem.obj
    if not asyncio.iscoroutinefunction(test_func):
        return None
    asyncio.run(test_func(**pyfuncitem.funcargs))
    return True
```

- [ ] **Step 2: Commit**

```bash
git add tests/runtime_v2/control_plane/conftest.py
git commit -m "test(control_plane): add async test hook conftest

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: outbox_writer — write + project

**Files:**
- Create: `src/runtime_v2/control_plane/outbox_writer.py`
- Test: `tests/runtime_v2/control_plane/test_outbox_writer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/control_plane/test_outbox_writer.py`:

```python
# tests/runtime_v2/control_plane/test_outbox_writer.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.outbox_writer import (
    project_clean_log_for_chain,
    write_clean_log_event,
)


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_chain(conn, chain_id, symbol="BTC/USDT", side="LONG"):
    now = _now()
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        " management_plan_json, risk_snapshot_json, plan_state_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (chain_id, chain_id, chain_id, chain_id, "trader_a", "main", symbol, side,
         "WAITING_ENTRY", "ONE_SHOT", "{}", "{}", "{}", now, now),
    )


def _seed_event(conn, chain_id, event_type, idem, payload=None):
    conn.execute(
        "INSERT OR IGNORE INTO ops_lifecycle_events "
        "(trade_chain_id, event_type, source_type, payload_json, idempotency_key, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (chain_id, event_type, "test", json.dumps(payload or {}), idem, _now()),
    )


def test_write_clean_log_event_inserts_row(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        write_clean_log_event(
            conn,
            notification_type="SIGNAL_ACCEPTED",
            chain_id=145,
            payload={"symbol": "BTC/USDT", "side": "LONG"},
        )
    row = conn.execute(
        "SELECT destination, notification_type, status FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert row == ("CLEAN_LOG", "SIGNAL_ACCEPTED", "PENDING")


def test_write_clean_log_event_dedupes(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        write_clean_log_event(conn, notification_type="SIGNAL_ACCEPTED",
                              chain_id=145, payload={}, dedupe_key="k")
        write_clean_log_event(conn, notification_type="SIGNAL_ACCEPTED",
                              chain_id=145, payload={}, dedupe_key="k")
    count = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()[0]
    conn.close()
    assert count == 1


def test_projection_maps_signal_accepted(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 145)
        _seed_event(conn, 145, "SIGNAL_ACCEPTED", "sig_accepted:145")
        _seed_event(conn, 145, "TRADE_CHAIN_CREATED", "chain_created:145")
        project_clean_log_for_chain(conn, 145)
    rows = conn.execute(
        "SELECT notification_type FROM ops_notification_outbox ORDER BY notification_id"
    ).fetchall()
    conn.close()
    # SIGNAL_ACCEPTED projected; TRADE_CHAIN_CREATED is policy=off
    assert [r[0] for r in rows] == ["SIGNAL_ACCEPTED"]


def test_projection_maps_fills(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 200)
        _seed_event(conn, 200, "ENTRY_FILLED", "entry_filled:200:1",
                    {"fill_price": 65020.0, "filled_qty": 0.004})
        _seed_event(conn, 200, "TP_FILLED", "tp_filled:200:2",
                    {"tp_level": 1, "is_final": False})
        _seed_event(conn, 200, "SL_FILLED", "sl_filled:200:3", {})
        project_clean_log_for_chain(conn, 200)
    types = {r[0] for r in conn.execute(
        "SELECT notification_type FROM ops_notification_outbox"
    ).fetchall()}
    conn.close()
    assert types == {"ENTRY_OPENED", "TP_FILLED", "SL_FILLED"}


def test_projection_is_idempotent(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 300)
        _seed_event(conn, 300, "SIGNAL_ACCEPTED", "sig_accepted:300")
        project_clean_log_for_chain(conn, 300)
        project_clean_log_for_chain(conn, 300)
    count = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()[0]
    conn.close()
    assert count == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_outbox_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: src.runtime_v2.control_plane.outbox_writer`.

- [ ] **Step 3: Write the outbox writer**

Create `src/runtime_v2/control_plane/outbox_writer.py`:

```python
# src/runtime_v2/control_plane/outbox_writer.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

# Map internal lifecycle event_type -> CLEAN_LOG notification_type.
# Events absent from this map have policy "off" (CLEAN_LOG_SPEC §2).
_CLEAN_LOG_EVENT_MAP: dict[str, str] = {
    "SIGNAL_ACCEPTED": "SIGNAL_ACCEPTED",
    "REVIEW_REQUIRED": "REVIEW_REQUIRED",
    "ENTRY_FILLED": "ENTRY_OPENED",
    "TP_FILLED": "TP_FILLED",
    "SL_FILLED": "SL_FILLED",
    "CLOSE_FULL_FILLED": "POSITION_CLOSED",
}

_PRIORITY_BY_TYPE: dict[str, str] = {
    "SL_FILLED": "HIGH",
    "POSITION_CLOSED": "HIGH",
    "REVIEW_REQUIRED": "HIGH",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(
    conn: sqlite3.Connection,
    *,
    notification_type: str,
    destination: str,
    payload: dict,
    priority: str,
    dedupe_key: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO ops_notification_outbox
            (notification_type, destination, payload_json, priority, status,
             dedupe_key, attempts, created_at)
        VALUES (?,?,?,?, 'PENDING', ?, 0, ?)
        """,
        (notification_type, destination, json.dumps(payload), priority,
         dedupe_key, _now()),
    )


def write_clean_log_event(
    conn: sqlite3.Connection,
    *,
    notification_type: str,
    chain_id: int | None,
    payload: dict,
    priority: str | None = None,
    dedupe_key: str | None = None,
) -> None:
    """Insert a CLEAN_LOG outbox row inside the caller's transaction."""
    key = dedupe_key or f"clean:{notification_type}:{chain_id}"
    pri = priority or _PRIORITY_BY_TYPE.get(notification_type, "MEDIUM")
    _record(conn, notification_type=notification_type, destination="CLEAN_LOG",
            payload=payload, priority=pri, dedupe_key=key)


def write_tech_log_event(
    conn: sqlite3.Connection,
    *,
    notification_type: str,
    payload: dict,
    dedupe_key: str,
    priority: str = "MEDIUM",
) -> None:
    """Insert a TECH_LOG outbox row inside the caller's transaction."""
    _record(conn, notification_type=notification_type, destination="TECH_LOG",
            payload=payload, priority=priority, dedupe_key=dedupe_key)


def project_clean_log_for_chain(conn: sqlite3.Connection, chain_id: int) -> int:
    """Read lifecycle events for `chain_id` and project CLEAN_LOG outbox rows.

    Idempotent: dedupe_key = "clean:<idempotency_key>" + UNIQUE constraint.
    Returns the number of rows attempted (including dedupe no-ops).
    """
    chain_row = conn.execute(
        "SELECT symbol, side, entry_mode FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()
    symbol = chain_row[0] if chain_row else None
    side = chain_row[1] if chain_row else None
    entry_mode = chain_row[2] if chain_row else None

    events = conn.execute(
        """
        SELECT event_type, payload_json, idempotency_key
        FROM ops_lifecycle_events
        WHERE trade_chain_id=?
        ORDER BY event_id
        """,
        (chain_id,),
    ).fetchall()

    written = 0
    for event_type, payload_json, idem in events:
        notification_type = _CLEAN_LOG_EVENT_MAP.get(event_type)
        if notification_type is None:
            continue
        try:
            ev_payload = json.loads(payload_json or "{}")
        except Exception:
            ev_payload = {}

        # Promote terminal TP to TP_FILLED_FINAL.
        if notification_type == "TP_FILLED" and ev_payload.get("is_final"):
            notification_type = "TP_FILLED_FINAL"

        payload = {
            "chain_id": chain_id,
            "symbol": symbol,
            "side": side,
            "entry_mode": entry_mode,
            **ev_payload,
        }
        write_clean_log_event(
            conn,
            notification_type=notification_type,
            chain_id=chain_id,
            payload=payload,
            dedupe_key=f"clean:{idem}",
        )
        written += 1
    return written


__all__ = [
    "write_clean_log_event",
    "write_tech_log_event",
    "project_clean_log_for_chain",
]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_outbox_writer.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/outbox_writer.py tests/runtime_v2/control_plane/test_outbox_writer.py
git commit -m "feat(control_plane): add outbox writer + lifecycle projection

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: TopicRouter

**Files:**
- Create: `src/runtime_v2/control_plane/topic_router.py`
- Test: `tests/runtime_v2/control_plane/test_topic_router.py`

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/control_plane/test_topic_router.py`:

```python
# tests/runtime_v2/control_plane/test_topic_router.py
from __future__ import annotations

import pytest

from src.runtime_v2.control_plane.models import (
    CleanLogConfig, ControlPlaneConfig, TechLogConfig, TopicConfig, TopicsConfig,
)
from src.runtime_v2.control_plane.topic_router import TopicRouter


def _config():
    return ControlPlaneConfig(
        token="t", chat_id=-100999,
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=101),
            tech_log=TechLogConfig(thread_id=102),
            clean_log=CleanLogConfig(thread_id=103),
        ),
    )


def test_resolve_clean_log():
    r = TopicRouter(_config())
    assert r.resolve("CLEAN_LOG") == (-100999, 103)


def test_resolve_tech_log():
    r = TopicRouter(_config())
    assert r.resolve("TECH_LOG") == (-100999, 102)


def test_resolve_commands_reply():
    r = TopicRouter(_config())
    assert r.resolve("COMMANDS_REPLY") == (-100999, 101)


def test_resolve_unknown_raises():
    r = TopicRouter(_config())
    with pytest.raises(ValueError):
        r.resolve("NOPE")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_topic_router.py -v`
Expected: FAIL — `ModuleNotFoundError: src.runtime_v2.control_plane.topic_router`.

- [ ] **Step 3: Write the router**

Create `src/runtime_v2/control_plane/topic_router.py`:

```python
# src/runtime_v2/control_plane/topic_router.py
from __future__ import annotations

from src.runtime_v2.control_plane.models import ControlPlaneConfig


class TopicRouter:
    """Centralizes destination -> (chat_id, thread_id) mapping (CLEAN_LOG_SPEC §17.4)."""

    def __init__(self, config: ControlPlaneConfig) -> None:
        self._chat_id = config.chat_id
        self._threads = {
            "CLEAN_LOG": config.topics.clean_log.thread_id,
            "TECH_LOG": config.topics.tech_log.thread_id,
            "COMMANDS_REPLY": config.topics.commands.thread_id,
        }

    def resolve(self, destination: str) -> tuple[int, int]:
        thread_id = self._threads.get(destination)
        if thread_id is None:
            raise ValueError(f"Unknown notification destination: {destination}")
        return self._chat_id, thread_id


__all__ = ["TopicRouter"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_topic_router.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/topic_router.py tests/runtime_v2/control_plane/test_topic_router.py
git commit -m "feat(control_plane): add topic router

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: CLEAN_LOG formatter

**Files:**
- Create: `src/runtime_v2/control_plane/formatters/__init__.py`, `src/runtime_v2/control_plane/formatters/clean_log.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`

- [ ] **Step 1: Create the formatters package marker**

Create `src/runtime_v2/control_plane/formatters/__init__.py` with a single newline:

```python
```

- [ ] **Step 2: Write the failing formatter test**

Create `tests/runtime_v2/control_plane/test_clean_log_formatter.py`:

```python
# tests/runtime_v2/control_plane/test_clean_log_formatter.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters.clean_log import format_clean_log


def test_signal_accepted():
    text = format_clean_log("SIGNAL_ACCEPTED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
        "trader_id": "trader_a",
    })
    assert "#145" in text
    assert "SIGNAL ACCEPTED" in text
    assert "BTC/USDT" in text
    assert "📈" in text          # LONG side emoji
    assert "Source:" in text


def test_review_required():
    text = format_clean_log("REVIEW_REQUIRED", {
        "chain_id": 147, "symbol": "ETH/USDT", "side": "SHORT",
        "reason": "ambiguous_entry_zone",
    })
    assert "REVIEW REQUIRED" in text
    assert "📉" in text          # SHORT side emoji
    assert "ambiguous_entry_zone" in text


def test_entry_opened():
    text = format_clean_log("ENTRY_OPENED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
        "fill_price": 65020.0, "filled_qty": 0.004,
    })
    assert "ENTRY OPENED" in text
    assert "65,020" in text or "65020" in text


def test_tp_filled():
    text = format_clean_log("TP_FILLED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG", "tp_level": 1,
    })
    assert "TP" in text and "FILLED" in text


def test_sl_filled_marks_closed():
    text = format_clean_log("SL_FILLED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
    })
    assert "SL FILLED" in text
    assert "POSITION CLOSED" in text
    assert "🛑" in text


def test_position_closed():
    text = format_clean_log("POSITION_CLOSED", {
        "chain_id": 145, "symbol": "BTC/USDT", "side": "LONG",
    })
    assert "POSITION CLOSED" in text


def test_unknown_type_has_safe_fallback():
    text = format_clean_log("WAT", {"chain_id": 1, "symbol": "X/Y", "side": "LONG"})
    assert "#1" in text
    assert "WAT" in text
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py -v`
Expected: FAIL — `ModuleNotFoundError: ...formatters.clean_log`.

- [ ] **Step 4: Write the formatter**

Create `src/runtime_v2/control_plane/formatters/clean_log.py`:

```python
# src/runtime_v2/control_plane/formatters/clean_log.py
from __future__ import annotations

_SEP = "────────────────"


def _side_emoji(side: str | None) -> str:
    if side == "LONG":
        return "📈"
    if side == "SHORT":
        return "📉"
    return "•"


def _num(value) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return f"{int(f):,}"
    return f"{f:,.2f}"


def _header(emoji: str, chain_id, event_label: str, symbol, side) -> list[str]:
    return [
        f"{emoji} #{chain_id} — {event_label}",
        _SEP,
        f"{symbol} — {_side_emoji(side)} {side}",
        "",
    ]


def _footer(source: str, link: str | None = None) -> list[str]:
    lines = [_SEP, f"Source: {source}"]
    if link:
        lines.append(link)
    return lines


def _signal_accepted(p: dict) -> str:
    lines = _header("✅", p.get("chain_id"), "SIGNAL ACCEPTED", p.get("symbol"), p.get("side"))
    if p.get("trader_id"):
        lines.append(f"Trader: {p['trader_id']}")
    lines.append("")
    lines += _footer(p.get("source", "original_message"), p.get("link"))
    return "\n".join(lines)


def _review_required(p: dict) -> str:
    lines = _header("⚠️", p.get("chain_id"), "REVIEW REQUIRED", p.get("symbol"), p.get("side"))
    lines.append(f"Reason: {p.get('reason', 'unknown')}")
    lines.append("Action: no automatic execution")
    lines.append("")
    lines += _footer(p.get("source", "runtime"), p.get("link"))
    return "\n".join(lines)


def _entry_opened(p: dict) -> str:
    lines = _header("📊", p.get("chain_id"), "ENTRY OPENED", p.get("symbol"), p.get("side"))
    if p.get("fill_price") is not None:
        lines.append("Filled:")
        lines.append(f"Entry: {_num(p['fill_price'])}")
        if p.get("filled_qty") is not None:
            lines.append(f"Qty: {p['filled_qty']}")
        lines.append("")
    lines += _footer(p.get("source", "exchange"))
    return "\n".join(lines)


def _tp_filled(p: dict, final: bool) -> str:
    level = p.get("tp_level")
    label = f"TP{level} FILLED" if level is not None else "TP FILLED"
    if final:
        label += " — POSITION CLOSED"
    lines = _header("📊", p.get("chain_id"), label, p.get("symbol"), p.get("side"))
    if p.get("tp_price") is not None:
        lines.append(f"TP_{level}: {_num(p['tp_price'])}")
    if p.get("pnl") is not None:
        lines.append(f"PnL: {p['pnl']} USDT")
    lines.append("")
    lines += _footer(p.get("source", "exchange"))
    return "\n".join(lines)


def _sl_filled(p: dict) -> str:
    lines = _header("🛑", p.get("chain_id"), "SL FILLED — POSITION CLOSED",
                    p.get("symbol"), p.get("side"))
    if p.get("pnl") is not None:
        lines.append(f"PnL: {p['pnl']} USDT")
    lines.append("Close reason: STOP_LOSS")
    lines.append("")
    lines += _footer(p.get("source", "exchange"))
    return "\n".join(lines)


def _position_closed(p: dict) -> str:
    lines = _header("📊", p.get("chain_id"), "POSITION CLOSED", p.get("symbol"), p.get("side"))
    if p.get("pnl") is not None:
        lines.append(f"PnL: {p['pnl']} USDT")
    lines.append("Close reason: MANUAL_CLOSE")
    lines.append("")
    lines += _footer(p.get("source", "exchange"))
    return "\n".join(lines)


def _fallback(notification_type: str, p: dict) -> str:
    lines = _header("📊", p.get("chain_id"), notification_type, p.get("symbol"), p.get("side"))
    lines += _footer(p.get("source", "runtime"))
    return "\n".join(lines)


def format_clean_log(notification_type: str, payload: dict) -> str:
    if notification_type == "SIGNAL_ACCEPTED":
        return _signal_accepted(payload)
    if notification_type == "REVIEW_REQUIRED":
        return _review_required(payload)
    if notification_type == "ENTRY_OPENED":
        return _entry_opened(payload)
    if notification_type == "TP_FILLED":
        return _tp_filled(payload, final=False)
    if notification_type == "TP_FILLED_FINAL":
        return _tp_filled(payload, final=True)
    if notification_type == "SL_FILLED":
        return _sl_filled(payload)
    if notification_type == "POSITION_CLOSED":
        return _position_closed(payload)
    return _fallback(notification_type, payload)


__all__ = ["format_clean_log"]
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/__init__.py src/runtime_v2/control_plane/formatters/clean_log.py tests/runtime_v2/control_plane/test_clean_log_formatter.py
git commit -m "feat(control_plane): add CLEAN_LOG formatter

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Notification dispatcher

**Files:**
- Create: `src/runtime_v2/control_plane/notification_dispatcher.py`
- Test: `tests/runtime_v2/control_plane/test_dispatcher.py`

**Design:** `NotificationSender` is an async protocol. `TelegramBotSender` wraps `telegram.Bot`. The dispatcher's `drain_once()` claims `PENDING` rows (oldest-first, `BEGIN IMMEDIATE` to avoid double-dispatch), routes + formats + sends each, then marks `SENT`; on send error it bumps `attempts` and `last_error`, marking `FAILED` once `attempts >= max_attempts`. `run()` loops `drain_once` every `poll_interval` seconds until cancelled.

- [ ] **Step 1: Write the failing test**

Create `tests/runtime_v2/control_plane/test_dispatcher.py`:

```python
# tests/runtime_v2/control_plane/test_dispatcher.py
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.control_plane.models import (
    CleanLogConfig, ControlPlaneConfig, TechLogConfig, TopicConfig, TopicsConfig,
)
from src.runtime_v2.control_plane.notification_dispatcher import (
    TelegramNotificationDispatcher,
)
from src.runtime_v2.control_plane.outbox_writer import write_clean_log_event
from src.runtime_v2.control_plane.topic_router import TopicRouter


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
    )


class FakeSender:
    def __init__(self, fail_times: int = 0):
        self.sent: list[dict] = []
        self._fail_times = fail_times
        self.calls = 0

    async def send(self, *, chat_id, thread_id, text, silent=False):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("telegram down")
        self.sent.append({"chat_id": chat_id, "thread_id": thread_id, "text": text})


def _dispatcher(ops_db, sender):
    cfg = _config()
    return TelegramNotificationDispatcher(
        config=cfg,
        ops_db_path=ops_db,
        topic_router=TopicRouter(cfg),
        sender=sender,
    )


def _seed(ops_db, dedupe_key="clean:k1"):
    conn = sqlite3.connect(ops_db)
    with conn:
        write_clean_log_event(conn, notification_type="SIGNAL_ACCEPTED",
                              chain_id=145,
                              payload={"symbol": "BTC/USDT", "side": "LONG"},
                              dedupe_key=dedupe_key)
    conn.close()


async def test_drain_sends_and_marks_sent(ops_db):
    _seed(ops_db)
    sender = FakeSender()
    disp = _dispatcher(ops_db, sender)
    n = await disp.drain_once()
    assert n == 1
    assert len(sender.sent) == 1
    assert sender.sent[0]["thread_id"] == 103
    conn = sqlite3.connect(ops_db)
    status = conn.execute("SELECT status FROM ops_notification_outbox").fetchone()[0]
    conn.close()
    assert status == "SENT"


async def test_drain_retries_then_fails(ops_db):
    _seed(ops_db)
    sender = FakeSender(fail_times=99)
    disp = _dispatcher(ops_db, sender)
    # 3 drain passes -> attempts reaches max -> FAILED
    await disp.drain_once()
    await disp.drain_once()
    await disp.drain_once()
    conn = sqlite3.connect(ops_db)
    status, attempts = conn.execute(
        "SELECT status, attempts FROM ops_notification_outbox"
    ).fetchone()
    conn.close()
    assert status == "FAILED"
    assert attempts == 3


async def test_failed_entry_not_resent(ops_db):
    _seed(ops_db)
    sender = FakeSender(fail_times=99)
    disp = _dispatcher(ops_db, sender)
    for _ in range(5):
        await disp.drain_once()
    assert sender.calls == 3  # stops attempting after FAILED


async def test_recovers_after_transient_failure(ops_db):
    _seed(ops_db)
    sender = FakeSender(fail_times=1)
    disp = _dispatcher(ops_db, sender)
    await disp.drain_once()   # fails once
    await disp.drain_once()   # succeeds
    assert len(sender.sent) == 1
    conn = sqlite3.connect(ops_db)
    status = conn.execute("SELECT status FROM ops_notification_outbox").fetchone()[0]
    conn.close()
    assert status == "SENT"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_dispatcher.py -v`
Expected: FAIL — `ModuleNotFoundError: ...notification_dispatcher`.

- [ ] **Step 3: Write the dispatcher**

Create `src/runtime_v2/control_plane/notification_dispatcher.py`:

```python
# src/runtime_v2/control_plane/notification_dispatcher.py
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Protocol

from src.runtime_v2.control_plane.formatters.clean_log import format_clean_log
from src.runtime_v2.control_plane.models import ControlPlaneConfig
from src.runtime_v2.control_plane.topic_router import TopicRouter

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3


class NotificationSender(Protocol):
    async def send(
        self, *, chat_id: int, thread_id: int, text: str, silent: bool = False
    ) -> None: ...


class TelegramBotSender:
    """Real sender backed by python-telegram-bot's Bot."""

    def __init__(self, bot) -> None:
        self._bot = bot

    async def send(self, *, chat_id, thread_id, text, silent=False):
        await self._bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=text,
            disable_notification=silent,
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TelegramNotificationDispatcher:
    def __init__(
        self,
        *,
        config: ControlPlaneConfig,
        ops_db_path: str,
        topic_router: TopicRouter,
        sender: NotificationSender,
        poll_interval_seconds: float = 2.0,
        batch_size: int = 50,
    ) -> None:
        self._config = config
        self._ops_db = ops_db_path
        self._router = topic_router
        self._sender = sender
        self._poll = poll_interval_seconds
        self._batch = batch_size

    def _claim_pending(self) -> list[tuple]:
        conn = sqlite3.connect(self._ops_db, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT notification_id, notification_type, destination, payload_json, attempts
                FROM ops_notification_outbox
                WHERE status='PENDING'
                ORDER BY CASE priority WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
                         created_at, notification_id
                LIMIT ?
                """,
                (self._batch,),
            ).fetchall()
            conn.execute("COMMIT")
            return rows
        finally:
            conn.close()

    def _mark_sent(self, notification_id: int) -> None:
        conn = sqlite3.connect(self._ops_db)
        try:
            conn.execute(
                "UPDATE ops_notification_outbox SET status='SENT', sent_at=? WHERE notification_id=?",
                (_now(), notification_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _mark_failure(self, notification_id: int, attempts: int, error: str) -> None:
        new_attempts = attempts + 1
        status = "FAILED" if new_attempts >= _MAX_ATTEMPTS else "PENDING"
        conn = sqlite3.connect(self._ops_db)
        try:
            conn.execute(
                "UPDATE ops_notification_outbox "
                "SET attempts=?, last_error=?, status=? WHERE notification_id=?",
                (new_attempts, error[:500], status, notification_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _render(self, destination: str, notification_type: str, payload: dict) -> str:
        if destination == "CLEAN_LOG":
            return format_clean_log(notification_type, payload)
        # TECH_LOG / COMMANDS_REPLY formatters arrive in later parts; safe fallback.
        return payload.get("text") or f"{notification_type}"

    async def drain_once(self) -> int:
        import json

        rows = self._claim_pending()
        sent = 0
        for notification_id, notification_type, destination, payload_json, attempts in rows:
            try:
                payload = json.loads(payload_json or "{}")
            except Exception:
                payload = {}
            try:
                chat_id, thread_id = self._router.resolve(destination)
                text = self._render(destination, notification_type, payload)
                silent = self._is_silent(notification_type)
                await self._sender.send(
                    chat_id=chat_id, thread_id=thread_id, text=text, silent=silent
                )
                self._mark_sent(notification_id)
                sent += 1
            except Exception as exc:  # noqa: BLE001 — runtime must not stop on notify failure
                logger.warning("notification %s send failed: %s", notification_id, exc)
                self._mark_failure(notification_id, attempts, str(exc))
        return sent

    def _is_silent(self, notification_type: str) -> bool:
        key_map = {
            "ENTRY_OPENED": "entry_filled",
            "TP_FILLED": "tp_filled",
            "TP_FILLED_FINAL": "tp_filled",
            "SL_FILLED": "sl_filled",
            "POSITION_CLOSED": "close_full_filled",
        }
        pref = self._config.notifications.get(key_map.get(notification_type, ""), "on")
        return pref == "silent"

    async def run(self) -> None:
        while True:
            try:
                await self.drain_once()
            except Exception:
                logger.exception("dispatcher drain error")
            await asyncio.sleep(self._poll)

    async def shutdown(self) -> None:
        return None


__all__ = [
    "TelegramNotificationDispatcher",
    "NotificationSender",
    "TelegramBotSender",
]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_dispatcher.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/notification_dispatcher.py tests/runtime_v2/control_plane/test_dispatcher.py
git commit -m "feat(control_plane): add async notification dispatcher with retry

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Wire projection into the workers

**Files:**
- Modify: `src/runtime_v2/lifecycle/workers.py` (inside `LifecycleEventWorker._persist_result`, after the `for cmd ...` loop, still inside `with conn:`)
- Modify: `src/runtime_v2/lifecycle/entry_gate.py` (inside `LifecycleGateWorker._persist_signal`, after persisting events, still inside `with conn:`; and inside `_persist_update` for chain-bound events)
- Test: `tests/runtime_v2/control_plane/test_worker_clean_log_integration.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/runtime_v2/control_plane/test_worker_clean_log_integration.py`:

```python
# tests/runtime_v2/control_plane/test_worker_clean_log_integration.py
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.runtime_v2.lifecycle.event_processor import EventProcessorResult
from src.runtime_v2.lifecycle.models import LifecycleEvent, TradeChain
from src.runtime_v2.lifecycle.repositories import (
    ExchangeEventRepository, ExecutionCommandRepository,
    LifecycleEventRepository, TradeChainRepository,
)
from src.runtime_v2.lifecycle.workers import LifecycleEventWorker
from src.runtime_v2.lifecycle.event_processor import LifecycleEventProcessor


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


def test_fill_event_projects_clean_log_outbox(ops_db):
    chain_repo = TradeChainRepository(ops_db)
    chain = chain_repo.save(TradeChain(
        source_enrichment_id=1, canonical_message_id=1, raw_message_id=1,
        trader_id="trader_a", account_id="main", symbol="BTC/USDT", side="LONG",
        lifecycle_state="OPEN", entry_mode="ONE_SHOT", management_plan_json="{}",
    ))
    worker = LifecycleEventWorker(
        ops_db_path=ops_db,
        processor=LifecycleEventProcessor(),
        chain_repo=chain_repo,
        event_repo=LifecycleEventRepository(ops_db),
        command_repo=ExecutionCommandRepository(ops_db),
        exchange_event_repo=ExchangeEventRepository(ops_db),
    )
    result = EventProcessorResult(
        new_lifecycle_state="CLOSED",
        new_be_protection_status=None,
        entry_avg_price=None,
        current_stop_price=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain.trade_chain_id,
            event_type="SL_FILLED",
            source_type="exchange_event",
            payload_json="{}",
            idempotency_key=f"sl_filled:{chain.trade_chain_id}:1",
        )],
        execution_commands=[],
    )
    # Access the persistence method directly (the integration seam).
    worker._persist_result(chain.trade_chain_id, result)

    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT notification_type, destination FROM ops_notification_outbox"
    ).fetchall()
    conn.close()
    assert ("SL_FILLED", "CLEAN_LOG") in rows
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/runtime_v2/control_plane/test_worker_clean_log_integration.py -v`
Expected: FAIL — no outbox row (projection not wired yet).

- [ ] **Step 3: Wire projection into `LifecycleEventWorker._persist_result`**

In `src/runtime_v2/lifecycle/workers.py`, add the import near the top (after the existing `from src.runtime_v2.lifecycle.repositories import (...)` block):

```python
from src.runtime_v2.control_plane.outbox_writer import project_clean_log_for_chain
```

Then, in `_persist_result`, locate the end of the `for cmd in result.execution_commands:` loop. Immediately after that loop and still **inside** the `with conn:` block, add:

```python
                try:
                    project_clean_log_for_chain(conn, chain_id)
                except Exception:
                    logger.exception("clean_log projection failed for chain %s", chain_id)
```

(The `try/except` ensures a projection bug never breaks trade persistence.)

- [ ] **Step 4: Wire projection into `LifecycleGateWorker._persist_signal` and `_persist_update`**

In `src/runtime_v2/lifecycle/entry_gate.py`, add the import near the other top-level imports (after `from src.runtime_v2.signal_enrichment.models import (...)`):

```python
from src.runtime_v2.control_plane.outbox_writer import project_clean_log_for_chain
```

In `_persist_signal`, at the end of the `with conn:` block (after the market-snapshot insert, before `finally:`), add:

```python
                if chain_id is not None:
                    try:
                        project_clean_log_for_chain(conn, chain_id)
                    except Exception:
                        logger.exception("clean_log projection failed for chain %s", chain_id)
```

In `_persist_update`, at the end of the `with conn:` block (after the `for event in result.review_events:` loop), add:

```python
                for cr in result.chain_results:
                    if cr.trade_chain_id:
                        try:
                            project_clean_log_for_chain(conn, cr.trade_chain_id)
                        except Exception:
                            logger.exception(
                                "clean_log projection failed for chain %s", cr.trade_chain_id
                            )
```

- [ ] **Step 5: Run the integration test to verify it passes**

Run: `python -m pytest tests/runtime_v2/control_plane/test_worker_clean_log_integration.py -v`
Expected: PASS.

- [ ] **Step 6: Run the existing lifecycle suites to confirm no regression**

Run: `python -m pytest tests/runtime_v2/lifecycle/ -q`
Expected: PASS (no regressions from the added projection calls).

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/lifecycle/workers.py src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/control_plane/test_worker_clean_log_integration.py
git commit -m "feat(control_plane): project CLEAN_LOG outbox rows from lifecycle workers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## End-of-part verification

- [ ] Run the full control-plane suite: `python -m pytest tests/runtime_v2/control_plane/ -v` — all green.
- [ ] Run lifecycle + execution suites: `python -m pytest tests/runtime_v2/lifecycle tests/runtime_v2/execution_gateway -q` — no regressions.
- [ ] Manual smoke (optional, needs real bot token + chat): instantiate `TelegramBotSender(telegram.Bot(token))`, seed one outbox row, call `await dispatcher.drain_once()`, confirm message lands in the CLEAN_LOG topic.
- [ ] Update `docs/AUDIT.md`: mark Part 2 complete; explicitly record the deferred items — aggregation/debounce not enforced, `ENTRY_UPDATED`/TP-batching/multi-chain summaries/reconciliation messages not yet implemented.

---

## Self-Review

**Spec coverage (spec §7 Parte 2):** `outbox_writer.py` ✅ (Task 3), `topic_router.py` ✅ (Task 4), `notification_dispatcher.py` ✅ (Task 6), `formatters/clean_log.py` ✅ (Task 5). Worker integration ("LifecycleEventWorker e ExchangeEventSyncWorker chiamano outbox_writer dopo ogni transizione") ✅ (Task 7) — implemented via `project_clean_log_for_chain` called from `LifecycleEventWorker._persist_result` and `LifecycleGateWorker._persist_signal/_persist_update`. Test requirements: formatter unit ✅, dispatcher integration (send/retry/dedupe) ✅. The full set of CLEAN_LOG events (SIGNAL_ACCEPTED, ENTRY_OPENED, TP_FILLED, SL_FILLED, POSITION_CLOSED, UPDATE_*) — SIGNAL_ACCEPTED/REVIEW/ENTRY_OPENED/TP_FILLED/SL_FILLED/POSITION_CLOSED covered; UPDATE_DONE/PARTIAL/REJECTED rich formatting deferred (flagged).

**Placeholder scan:** No TBD/TODO. Every code step shows full content. The two worker edits reference exact insertion points relative to existing structures (`with conn:` blocks confirmed present at `workers.py:206` and `entry_gate.py:1434/1554`).

**Type consistency:** `format_clean_log(notification_type, payload)` signature matches dispatcher call and tests. `TopicRouter.resolve` returns `(chat_id, thread_id)` consistently. `write_clean_log_event(conn, *, notification_type, chain_id, payload, priority, dedupe_key)` keyword signature is identical across outbox_writer.py, its tests, and dispatcher seed helper. `NotificationSender.send(*, chat_id, thread_id, text, silent)` matches `FakeSender`, `TelegramBotSender`, and dispatcher call site.

**Risk note:** Both worker edits are guarded by `try/except` so a CLEAN_LOG projection error can never break trade-state persistence — consistent with spec acceptance criterion "Il runtime non si ferma per fallimenti di notifica".

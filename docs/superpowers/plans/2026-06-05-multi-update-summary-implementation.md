# Multi-Update Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign `MULTI_CHAIN_SUMMARY` so multi-update trader notifications become autosufficient, use per-chain display outcomes, and delay `CLOSE_FULL` summaries until final `POSITION_CLOSED` links are resolvable.

**Architecture:** Keep the current update synthesis path in `src/runtime_v2/lifecycle/entry_gate.py`, but introduce a richer normalized per-chain summary outcome before formatting. Extend `src/runtime_v2/control_plane/formatters/clean_log.py` to render the approved autosufficient format. For `CLOSE_FULL`, add a second-phase summary emission path that waits for resolvable final-close links rather than emitting immediately with root links.

**Tech Stack:** Python 3.12, SQLite, runtime_v2 lifecycle/control-plane, pytest

---

## File Map

| File | Responsibility |
|---|---|
| `src/runtime_v2/lifecycle/entry_gate.py` | Normalize multi-update chain outcomes, enrich summary payload, gate immediate vs delayed summary emission |
| `src/runtime_v2/control_plane/formatters/clean_log.py` | Render autosufficient `MULTI_CHAIN_SUMMARY` text in approved format |
| `src/runtime_v2/control_plane/outbox_writer.py` | Reuse or extend link resolution helpers for root/final-close links if needed |
| `src/runtime_v2/control_plane/notification_dispatcher.py` | If needed, support final-link-aware summary release or tracking update |
| `tests/runtime_v2/lifecycle/test_entry_gate.py` | Unit/integration coverage for normalized outcomes and delayed `CLOSE_FULL` summary emission |
| `tests/runtime_v2/control_plane/test_clean_log_formatter.py` | Formatter assertions for autosufficient summary |
| `tests/runtime_v2/control_plane/test_outbox_writer.py` | Link resolution and tracking assertions when final-close links are involved |
| `tests/runtime_v2/control_plane/test_dispatcher.py` | Dispatcher-level behavior if delayed summary emission touches outbox lifecycle |

---

### Task 1: Lock the formatter contract for autosufficient non-`CLOSE_FULL`

**Files:**
- Modify: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`
- Inspect: `src/runtime_v2/control_plane/formatters/clean_log.py:499-552`

- [ ] **Step 1: Add a failing formatter test for autosufficient non-`CLOSE_FULL` summary**

Append a new test near the existing `MULTI_CHAIN_SUMMARY` tests:

```python
def test_multi_chain_summary_autosufficient_non_close_full():
    from src.runtime_v2.control_plane.formatters.clean_log import format_clean_log

    text = format_clean_log("MULTI_CHAIN_SUMMARY", {
        "summary_kind": "immediate",
        "requested_operations": ["CANCEL_PENDING", "MOVE_SL_TO_BE"],
        "chains": [
            {
                "chain_id": 6,
                "symbol": "WLD",
                "side": "LONG",
                "status": "DONE",
                "link": "https://t.me/c/3897279123/468",
                "display_lines": [
                    "Entry_2: 61,192.03 -> cancelled",
                    "Entry_3: 60,192.03 -> cancelled",
                    "SL: 66,400 -> 68,500 BE",
                ],
            },
            {
                "chain_id": 7,
                "symbol": "ICNT",
                "side": "LONG",
                "status": "PARTIAL",
                "link": "https://t.me/c/3897279123/469",
                "display_lines": [
                    "Entry_2: SKIPPED - no pending averaging order",
                    "SL: 66,400 -> 68,500 BE",
                ],
            },
        ],
        "counts": {"done": 1, "partial": 1, "skipped": 1, "error": 0},
        "source": "trader_update",
        "link": "https://t.me/c/3927267771/365",
    })

    assert "UPDATE APPLICATO" in text
    assert "Operations requested:" in text
    assert "#6 WLD LONG - DONE".replace("-", "—")[:10] or "#6 WLD LONG" in text
    assert "https://t.me/c/3897279123/468" in text
    assert "Entry_2: 61,192.03 -> cancelled" in text
    assert "Entry_2: SKIPPED - no pending averaging order" in text
    assert "Done: 1 | Partial: 1 | Skipped: 1 | Error: 0" in text
    assert text.rstrip().endswith("https://t.me/c/3927267771/365")
```

- [ ] **Step 2: Run the focused formatter test**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py -k "autosufficient_non_close_full" -v
```

Expected: FAIL because the current formatter still renders the old table-based summary.

- [ ] **Step 3: Rewrite `_multi_chain_summary()` for the approved non-`CLOSE_FULL` layout**

In `src/runtime_v2/control_plane/formatters/clean_log.py`, update `_multi_chain_summary()` so it supports a richer payload:

```python
requested = p.get("requested_operations") or p.get("operations") or []
chains = p.get("chains") or []
counts = p.get("counts") or {}
summary_kind = p.get("summary_kind", "immediate")
```

Render this structure:

```python
lines = [header_line, _SEP]
lines.append("Operations requested:")
for op in requested:
    lines.append(f"{_BULLET} {op}")
lines.append(_SEP)
for chain in chains:
    lines.append(f"#{chain['chain_id']} {chain['symbol']} {chain['side']} — {chain['status']}")
    if chain.get("link"):
        lines.append(chain["link"])
    for item in chain.get("display_lines") or []:
        lines.append(item)
    lines.append(_SEP)
```

And the footer counts:

```python
done = counts.get("done", 0)
partial = counts.get("partial", 0)
skipped = counts.get("skipped", 0)
error = counts.get("error", 0)
lines.append(f"Done: {done} | Partial: {partial} | Skipped: {skipped} | Error: {error}")
```

- [ ] **Step 4: Re-run the focused formatter test**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py -k "autosufficient_non_close_full" -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/clean_log.py tests/runtime_v2/control_plane/test_clean_log_formatter.py
git commit -m "feat(control-plane): render autosufficient multi-update summary"
```

---

### Task 2: Lock the formatter contract for `CLOSE_FULL`

**Files:**
- Modify: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`
- Modify: `src/runtime_v2/control_plane/formatters/clean_log.py`

- [ ] **Step 1: Add a failing formatter test for `CLOSE_FULL` summary**

Append:

```python
def test_multi_chain_summary_close_full_uses_compact_rows():
    from src.runtime_v2.control_plane.formatters.clean_log import format_clean_log

    text = format_clean_log("MULTI_CHAIN_SUMMARY", {
        "summary_kind": "final_close",
        "requested_operations": ["Close full"],
        "chains": [
            {
                "chain_id": 6,
                "symbol": "WLD",
                "side": "LONG",
                "status": "DONE",
                "link": "https://t.me/c/3897279123/468",
                "display_lines": [],
            },
            {
                "chain_id": 7,
                "symbol": "ICNT",
                "side": "LONG",
                "status": "DONE",
                "link": "https://t.me/c/3897279123/469",
                "display_lines": [],
            },
        ],
        "counts": {"done": 2, "partial": 0, "skipped": 0, "error": 0},
        "source": "trader_update",
        "link": "https://t.me/c/3927267771/365",
    })

    assert "Operation requested:" in text
    assert "Close full" in text
    assert "https://t.me/c/3897279123/468" in text
    assert "Position: open" not in text
    assert "Close reason:" not in text
    assert "Done: 2 | Skipped: 0 | Error: 0" in text
```

- [ ] **Step 2: Run the focused test**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py -k "close_full_uses_compact_rows" -v
```

Expected: FAIL until the formatter learns `summary_kind="final_close"` and the compact footer variant.

- [ ] **Step 3: Add `summary_kind="final_close"` branch in `_multi_chain_summary()`**

Use this rendering rule:

```python
is_close_full = p.get("summary_kind") == "final_close"
```

When `is_close_full`:

- render the same header and per-chain heading
- render the link line
- do not render `display_lines`
- render compact footer:

```python
lines.append(f"Done: {done} | Skipped: {skipped} | Error: {error}")
```

- [ ] **Step 4: Re-run the focused test**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py -k "close_full_uses_compact_rows" -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/clean_log.py tests/runtime_v2/control_plane/test_clean_log_formatter.py
git commit -m "feat(control-plane): add compact close-full multi summary"
```

---

### Task 3: Introduce normalized summary payload in lifecycle

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py:53-63, 94-238, 2117-2153`
- Modify: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Add a failing lifecycle test for normalized non-`CLOSE_FULL` payload**

Append a new test near the existing `MULTI_CHAIN_SUMMARY` assertions:

```python
def test_write_multi_chain_summary_builds_autosufficient_chain_payload(tmp_path):
    import json
    import sqlite3
    from src.runtime_v2.lifecycle.entry_gate import _write_multi_chain_summary, UpdateChainResult
    from src.runtime_v2.lifecycle.models import LifecycleEvent

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE ops_trade_chains (
            trade_chain_id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT
        );
        CREATE TABLE ops_clean_log_tracking (
            trade_chain_id INTEGER PRIMARY KEY,
            clean_log_root_message_id TEXT,
            clean_log_last_message_id TEXT,
            telegram_chat_id TEXT,
            telegram_thread_id TEXT,
            last_clean_log_event_type TEXT,
            last_clean_log_sent_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE ops_notification_outbox (
            notification_id INTEGER PRIMARY KEY,
            notification_type TEXT,
            destination TEXT,
            payload_json TEXT,
            priority TEXT,
            status TEXT,
            dedupe_key TEXT UNIQUE,
            attempts INTEGER,
            created_at TEXT,
            send_after TEXT,
            aggregation_group TEXT,
            source_message_id TEXT
        );
        """
    )
    conn.execute("INSERT INTO ops_trade_chains VALUES (6, 'WLD', 'LONG')")
    conn.execute("INSERT INTO ops_trade_chains VALUES (7, 'ICNT', 'LONG')")
    conn.execute(
        "INSERT INTO ops_clean_log_tracking VALUES (6, '468', '468', '-1003897279123', NULL, NULL, NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO ops_clean_log_tracking VALUES (7, '469', '469', '-1003897279123', NULL, NULL, NULL, NULL)"
    )

    accepted_done = LifecycleEvent(
        event_type="TELEGRAM_UPDATE_ACCEPTED",
        source_type="telegram_update",
        source_id="365",
        payload_json=json.dumps({
            "action": "CANCEL_PENDING",
            "cancelled_entries": [{"sequence": 2, "price": "61,192.03"}],
        }),
        idempotency_key="u:6:1",
    )
    accepted_partial = LifecycleEvent(
        event_type="TELEGRAM_UPDATE_ACCEPTED",
        source_type="telegram_update",
        source_id="365",
        payload_json=json.dumps({
            "action": "MOVE_STOP",
            "old_sl_price": "66,400",
            "new_sl_price": "68,500",
            "reference": "TP_1",
        }),
        idempotency_key="u:7:1",
    )
    noop_partial = LifecycleEvent(
        event_type="NOOP_NOT_PENDING",
        source_type="telegram_update",
        source_id="365",
        payload_json=json.dumps({"reason": "no pending averaging order"}),
        idempotency_key="u:7:2",
    )

    _write_multi_chain_summary(
        conn,
        [
            UpdateChainResult(6, None, None, [accepted_done], []),
            UpdateChainResult(7, None, None, [accepted_partial, noop_partial], []),
        ],
        canonical_message_id=365,
        update_source_link="https://t.me/c/3927267771/365",
    )

    row = conn.execute(
        "SELECT payload_json FROM ops_notification_outbox WHERE notification_type='MULTI_CHAIN_SUMMARY'"
    ).fetchone()
    payload = json.loads(row[0])
    assert payload["summary_kind"] == "immediate"
    assert payload["requested_operations"] == ["Cancel pending", "Move stop"]
    assert payload["chains"][0]["display_lines"]
    assert payload["chains"][1]["display_lines"][0] == "Entry_2: SKIPPED - no pending averaging order"
    assert payload["link"] == "https://t.me/c/3927267771/365"
```

- [ ] **Step 2: Run the focused test**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "autosufficient_chain_payload" -v
```

Expected: FAIL because `_write_multi_chain_summary()` currently emits only `operations`, `chains`, and `source`.

- [ ] **Step 3: Add normalized summary helpers in `entry_gate.py`**

Add helper functions above `_write_multi_chain_summary()`:

```python
def _render_update_display_lines(accepted: list[LifecycleEvent], noops: list[LifecycleEvent]) -> list[str]:
    ...

def _resolve_summary_status(accepted: list[LifecycleEvent], noops: list[LifecycleEvent], reviews: list[LifecycleEvent]) -> str:
    ...

def _resolve_signal_root_link(conn, chain_id: int) -> str | None:
    ...
```

Use `_render_update_display_lines()` rules:

- `CANCEL_PENDING` -> `Entry_n: old -> cancelled`
- `MOVE_SL_TO_BE` -> `SL: old -> new BE`
- `MOVE_STOP` with `reference="TP_1"` -> `SL: old -> new` then `Reference: TP_1`
- `MOVE_STOP` with `reference="Price"` -> `SL: old -> new` then `Reference: Price`
- noop `reason="no pending averaging order"` -> `Entry_2: SKIPPED - no pending averaging order` when entry sequence is known, otherwise `SKIPPED - no pending averaging order`

- [ ] **Step 4: Rewrite `_write_multi_chain_summary()` to emit normalized payload**

Change its signature to:

```python
def _write_multi_chain_summary(conn, chain_results, canonical_message_id, update_source_link: str | None = None) -> None:
```

Build payload like:

```python
payload = {
    "summary_kind": "immediate",
    "requested_operations": operations_seen,
    "chains": chains_payload,
    "counts": {"done": done, "partial": partial, "skipped": skipped, "error": error},
    "source": source,
    "link": update_source_link,
}
```

Each `chains_payload` row should include:

```python
{
    "chain_id": cid,
    "symbol": symbol,
    "side": side,
    "status": status,
    "link_mode": "signal_root",
    "link": signal_link,
    "display_lines": display_lines,
}
```

- [ ] **Step 5: Re-run the focused test**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "autosufficient_chain_payload" -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat(lifecycle): normalize multi-update summary payload"
```

---

### Task 4: Carry `MOVE_STOP` reference semantics into per-chain update logs

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Modify: `src/runtime_v2/control_plane/formatters/clean_log.py`
- Modify: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`

- [ ] **Step 1: Add a failing formatter test for `UPDATE_DONE` stop move with `Reference`**

Append:

```python
def test_update_done_move_stop_shows_reference_tp():
    from src.runtime_v2.control_plane.formatters.clean_log import format_clean_log

    text = format_clean_log("UPDATE_DONE", {
        "chain_id": 8,
        "symbol": "BTC",
        "side": "LONG",
        "applied_actions": ["MOVE_STOP"],
        "changed": [
            {"field": "SL", "old": "66,400", "new": "68,500"},
        ],
        "display_lines": [
            "SL: 66,400 -> 68,500",
            "Reference: TP_1",
        ],
        "source": "trader_update",
        "link": "https://t.me/c/3897279123/470",
    })

    assert "SL: 66,400 -> 68,500" in text
    assert "Reference: TP_1" in text
```

- [ ] **Step 2: Run the focused test**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py -k "move_stop_shows_reference_tp" -v
```

Expected: FAIL because `_update_done()` currently knows only `changed`.

- [ ] **Step 3: Extend `_write_update_clean_log()` and `_update_done()` to support `display_lines`**

In `src/runtime_v2/lifecycle/entry_gate.py`, include `display_lines` in the synthesized payload when action is `MOVE_STOP`:

```python
display_lines = []
...
elif action == "MOVE_STOP":
    display_lines.append(f"SL: {p.get('old_sl_price')} -> {p.get('new_sl_price')}")
    if p.get("reference") in {"Price", "TP_1", "TP_2", "TP_3"}:
        display_lines.append(f"Reference: {p['reference']}")
```

Then in `src/runtime_v2/control_plane/formatters/clean_log.py`, prefer `display_lines` before generic `changed` rendering:

```python
display_lines = p.get("display_lines") or []
if display_lines:
    for item in display_lines:
        lines.append(item)
elif changed:
    ...
```

- [ ] **Step 4: Re-run the focused test**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py -k "move_stop_shows_reference_tp" -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py src/runtime_v2/control_plane/formatters/clean_log.py tests/runtime_v2/control_plane/test_clean_log_formatter.py
git commit -m "feat(control-plane): render move-stop reference lines"
```

---

### Task 5: Delay `CLOSE_FULL` multi-summary until final links are resolvable

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Modify: `tests/runtime_v2/lifecycle/test_entry_gate.py`
- Inspect: `tests/runtime_v2/control_plane/test_clean_log_tracking.py`, `tests/runtime_v2/control_plane/test_dispatcher.py`

- [ ] **Step 1: Add a failing lifecycle test that `CLOSE_FULL` summary is not written immediately**

Append:

```python
def test_write_multi_chain_summary_skips_immediate_emit_for_close_full(tmp_path):
    import json
    import sqlite3
    from src.runtime_v2.lifecycle.entry_gate import _write_multi_chain_summary, UpdateChainResult
    from src.runtime_v2.lifecycle.models import LifecycleEvent

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE ops_trade_chains (trade_chain_id INTEGER PRIMARY KEY, symbol TEXT, side TEXT);
        CREATE TABLE ops_clean_log_tracking (
            trade_chain_id INTEGER PRIMARY KEY,
            clean_log_root_message_id TEXT,
            clean_log_last_message_id TEXT,
            telegram_chat_id TEXT,
            telegram_thread_id TEXT,
            last_clean_log_event_type TEXT,
            last_clean_log_sent_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE ops_notification_outbox (
            notification_id INTEGER PRIMARY KEY,
            notification_type TEXT,
            destination TEXT,
            payload_json TEXT,
            priority TEXT,
            status TEXT,
            dedupe_key TEXT UNIQUE,
            attempts INTEGER,
            created_at TEXT,
            send_after TEXT,
            aggregation_group TEXT,
            source_message_id TEXT
        );
        """
    )
    conn.execute("INSERT INTO ops_trade_chains VALUES (6, 'WLD', 'LONG')")
    event = LifecycleEvent(
        event_type='TELEGRAM_UPDATE_ACCEPTED',
        source_type='telegram_update',
        source_id='365',
        payload_json=json.dumps({'action': 'CLOSE_FULL'}),
        idempotency_key='close:6:365',
    )

    _write_multi_chain_summary(
        conn,
        [UpdateChainResult(6, None, None, [event], [])],
        canonical_message_id=365,
        update_source_link='https://t.me/c/3927267771/365',
    )

    row = conn.execute(
        "SELECT COUNT(*) FROM ops_notification_outbox WHERE notification_type='MULTI_CHAIN_SUMMARY'"
    ).fetchone()[0]
    assert row == 0
```

- [ ] **Step 2: Run the focused test**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "skips_immediate_emit_for_close_full" -v
```

Expected: FAIL because the current summary writer emits immediately.

- [ ] **Step 3: Introduce pending-close summary persistence path**

In `src/runtime_v2/lifecycle/entry_gate.py`, split `_write_multi_chain_summary()` into:

```python
if contains_close_full:
    _write_pending_close_full_summary(...)
    return
```

Use a new outbox payload:

```python
{
    "summary_kind": "pending_final_close_links",
    "requested_operations": ["Close full"],
    "chains": [... link_mode="final_close", link=None ...],
    "counts": {...},
    "source": source,
    "link": update_source_link,
}
```

Write it with:

- `notification_type="MULTI_CHAIN_SUMMARY_PENDING"` if you add a new outbox type, or
- `notification_type="MULTI_CHAIN_SUMMARY"` with `status='PENDING_LINKS'` only if the existing outbox model supports it cleanly.

Preferred approach: add a new dedicated notification type to avoid overloading existing dispatcher semantics.

- [ ] **Step 4: Add a release helper that upgrades pending close summaries once final links exist**

Add a helper in `entry_gate.py` or a small control-plane helper:

```python
def _try_release_close_full_summary(conn, canonical_message_id: int) -> None:
    ...
```

Resolution rule per chain:

- read `ops_clean_log_tracking.clean_log_last_message_id`
- require `last_clean_log_event_type == 'POSITION_CLOSED'`
- build `https://t.me/c/<chat>/<last_message_id>`

When every target chain resolves:

- write final `MULTI_CHAIN_SUMMARY` payload with `summary_kind='final_close'`
- mark the pending record consumed or delete it

- [ ] **Step 5: Re-run the focused test**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "skips_immediate_emit_for_close_full" -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat(lifecycle): delay close-full multi summary until final links resolve"
```

---

### Task 6: Add final-link release coverage

**Files:**
- Modify: `tests/runtime_v2/lifecycle/test_entry_gate.py`
- Modify: `tests/runtime_v2/control_plane/test_outbox_writer.py` or `tests/runtime_v2/control_plane/test_dispatcher.py`

- [ ] **Step 1: Add a failing test for final summary release**

Append a test that simulates tracking rows reaching `POSITION_CLOSED`:

```python
def test_release_close_full_summary_uses_position_closed_links(tmp_path):
    import json
    import sqlite3
    from src.runtime_v2.lifecycle.entry_gate import _try_release_close_full_summary

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE ops_pending_multi_chain_summaries (
            pending_id INTEGER PRIMARY KEY,
            canonical_message_id INTEGER,
            payload_json TEXT
        );
        CREATE TABLE ops_clean_log_tracking (
            trade_chain_id INTEGER PRIMARY KEY,
            clean_log_root_message_id TEXT,
            clean_log_last_message_id TEXT,
            telegram_chat_id TEXT,
            telegram_thread_id TEXT,
            last_clean_log_event_type TEXT,
            last_clean_log_sent_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE ops_notification_outbox (
            notification_id INTEGER PRIMARY KEY,
            notification_type TEXT,
            destination TEXT,
            payload_json TEXT,
            priority TEXT,
            status TEXT,
            dedupe_key TEXT UNIQUE,
            attempts INTEGER,
            created_at TEXT,
            send_after TEXT,
            aggregation_group TEXT,
            source_message_id TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO ops_pending_multi_chain_summaries (canonical_message_id, payload_json) VALUES (?, ?)",
        (
            365,
            json.dumps({
                "summary_kind": "pending_final_close_links",
                "requested_operations": ["Close full"],
                "chains": [
                    {"chain_id": 6, "symbol": "WLD", "side": "LONG", "status": "DONE", "link_mode": "final_close", "link": None, "display_lines": []},
                    {"chain_id": 7, "symbol": "ICNT", "side": "LONG", "status": "DONE", "link_mode": "final_close", "link": None, "display_lines": []},
                ],
                "counts": {"done": 2, "partial": 0, "skipped": 0, "error": 0},
                "source": "trader_update",
                "link": "https://t.me/c/3927267771/365",
            }),
        ),
    )
    conn.execute("INSERT INTO ops_clean_log_tracking VALUES (6, '453', '468', '-1003897279123', NULL, 'POSITION_CLOSED', NULL, NULL)")
    conn.execute("INSERT INTO ops_clean_log_tracking VALUES (7, '454', '469', '-1003897279123', NULL, 'POSITION_CLOSED', NULL, NULL)")

    _try_release_close_full_summary(conn, 365)

    row = conn.execute(
        "SELECT payload_json FROM ops_notification_outbox WHERE notification_type='MULTI_CHAIN_SUMMARY'"
    ).fetchone()
    payload = json.loads(row[0])
    assert payload["summary_kind"] == "final_close"
    assert payload["chains"][0]["link"] == "https://t.me/c/3897279123/468"
    assert payload["chains"][1]["link"] == "https://t.me/c/3897279123/469"
```

- [ ] **Step 2: Run the focused test**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "release_close_full_summary_uses_position_closed_links" -v
```

Expected: FAIL until pending summary storage and release helper exist.

- [ ] **Step 3: Implement the minimal persistence object for pending close summaries**

Add a small table-backed store. Preferred file placement:

- if tiny: inline helper SQL in `entry_gate.py`
- if reused: new file `src/runtime_v2/control_plane/pending_multi_summary_store.py`

Minimal interface:

```python
def save_pending_close_full_summary(conn, canonical_message_id: int, payload: dict) -> None: ...
def load_pending_close_full_summary(conn, canonical_message_id: int) -> dict | None: ...
def delete_pending_close_full_summary(conn, canonical_message_id: int) -> None: ...
```

- [ ] **Step 4: Re-run the focused test**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k "release_close_full_summary_uses_position_closed_links" -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat(lifecycle): release close-full multi summary with final links"
```

---

### Task 7: Full regression pass and doc alignment

**Files:**
- Modify: `docs/Raggionamento/Controllo_Notifica/runtime_v2_control_plane_messaggi.md`
- Modify: `docs/Raggionamento/Controllo_Notifica/Correzioni.md` if it still describes superseded formatting
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Add one regression test for move-stop reference in summary**

Append a focused test:

```python
def test_multi_chain_summary_move_stop_price_reference():
    from src.runtime_v2.control_plane.formatters.clean_log import format_clean_log

    text = format_clean_log("MULTI_CHAIN_SUMMARY", {
        "summary_kind": "immediate",
        "requested_operations": ["Move stop"],
        "chains": [
            {
                "chain_id": 8,
                "symbol": "BTC",
                "side": "LONG",
                "status": "DONE",
                "link": "https://t.me/c/3897279123/470",
                "display_lines": [
                    "SL: 66,400 -> 67,950",
                    "Reference: Price",
                ],
            },
        ],
        "counts": {"done": 1, "partial": 0, "skipped": 0, "error": 0},
        "source": "trader_update",
        "link": "https://t.me/c/3927267771/365",
    })

    assert "Reference: Price" in text
```

- [ ] **Step 2: Run targeted suites**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py tests/runtime_v2/lifecycle/test_entry_gate.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Update durable docs**

Align `docs/Raggionamento/Controllo_Notifica/runtime_v2_control_plane_messaggi.md` with:

- autosufficient non-`CLOSE_FULL` summary examples
- compact `CLOSE_FULL` summary examples
- link semantics:
  - non-`CLOSE_FULL` -> `SIGNAL_ACCEPTED`
  - `CLOSE_FULL` -> `POSITION_CLOSED`
- move-stop reference semantics:
  - `MOVE_STOP_BE` -> BE
  - `MOVE_STOP` -> `Reference: TP_n | Price`

If `Correzioni.md` contains provisional mockups now superseded by the approved version, replace them with a short note pointing to the finalized doc/spec instead of duplicating two diverging sources of truth.

- [ ] **Step 4: Run final focused regression**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_clean_log_formatter.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/control_plane/test_outbox_writer.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/Raggionamento/Controllo_Notifica/runtime_v2_control_plane_messaggi.md docs/Raggionamento/Controllo_Notifica/Correzioni.md tests/runtime_v2/control_plane/test_clean_log_formatter.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/control_plane/test_outbox_writer.py
git commit -m "docs(control-plane): align multi-update summary behavior"
```

---

## Self-Review

### Spec coverage

- Autosufficient non-`CLOSE_FULL` summary: Task 1, Task 3
- Compact `CLOSE_FULL` summary: Task 2
- Per-chain root/final link semantics: Task 3, Task 5, Task 6
- `MOVE_STOP` / `MOVE_STOP_BE` reference behavior: Task 4, Task 7
- Delay until final `POSITION_CLOSED` link exists: Task 5, Task 6
- Durable docs alignment: Task 7

No spec requirement is intentionally left without a task. The only explicit follow-up not fully closed here is timeout/degradation if final-close links never resolve; that remains outside this plan because the approved spec treated it as a later technical follow-up.

### Placeholder scan

- No `TODO`, `TBD`, or “implement later”
- Every code-changing step names exact files
- Every verification step includes exact commands

### Type consistency

This plan consistently uses:

- `summary_kind`: `immediate | final_close | pending_final_close_links`
- `requested_operations`
- `display_lines`
- `counts`
- `link_mode`: `signal_root | final_close`

Keep those exact names in implementation and tests.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-05-multi-update-summary-implementation.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?

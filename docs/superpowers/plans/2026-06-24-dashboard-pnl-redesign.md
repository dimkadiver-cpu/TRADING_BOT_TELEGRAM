# Dashboard PNL Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `equity_usdt` with a futures-wallet view, add partial PnL, per-trader breakdown, per-account Avail/Margin, and auto-refresh on account snapshots.

**Architecture:** Three layers touched in order — data layer (`status_queries.py` extends `PnlView` and `get_pnl()`), payload/template layer (`formatters/dashboard.py` + `formatters/templates/dashboard.py`), event-plumbing layer (`account_snapshot_worker.py` → `dashboard_manager.py` → `bootstrap.py`). Each task is independently testable and committed before the next one starts.

**Tech Stack:** Python 3.11+, SQLite via `sqlite3`, asyncio, pytest + pytest-asyncio, Telegram Bot API (mocked in tests).

## Global Constraints

- No new production dependencies — only stdlib and packages already in the repo.
- All SQL is inline string formatting using the repo's existing `scope_frag + scope_params` pattern; no ORM.
- Tests use real SQLite (no mocks for DB) — apply migrations via `_apply_migrations(db_path)` calling `db/ops_migrations/*.sql`.
- `partial_row`, `closed_row` column indices 0 and 1 only (SUM gross, SUM fees+funding); compute net in Python.
- `SNAPSHOT_STALE_SECONDS = 180` — do not change this constant.
- Template functions return plain strings; no f-string alignment beyond what the reference shows.
- `by_trader` activated only when 2+ distinct trader_ids in non-global scope.
- Callback `on_snapshot_saved` is fire-and-forget: errors logged, never re-raised.

---

### Task 1: PnlView partial fields + get_pnl() partial query

**Files:**
- Modify: `src/runtime_v2/control_plane/status_queries.py` (PnlView dataclass ~line 200; get_pnl body ~lines 1213-1416)
- Test: `tests/runtime_v2/control_plane/test_status_queries.py`

**Interfaces:**
- Produces: `PnlView.partial_pnl: float | None`, `PnlView.partial_fees: float | None`, `PnlView.partial_pnl_net: float | None`
- Consumed by Task 4 (payload builder reads `view.partial_pnl`, `view.partial_fees`, `view.partial_pnl_net`).

- [ ] **Step 1: Add helper to test file for chains with PnL data**

Append after existing `_add_chain` in `tests/runtime_v2/control_plane/test_status_queries.py`:

```python
def _add_chain_pnl(
    conn,
    cid: int,
    state: str,
    *,
    account_id: str = "main",
    trader_id: str = "trader_a",
    gross_pnl: float = 0.0,
    fees: float = 0.0,
    funding: float = 0.0,
    risk_snapshot_json: str = "{}",
) -> None:
    now = _now()
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        " current_stop_price, management_plan_json, risk_snapshot_json, plan_state_json, "
        " cumulative_gross_pnl, cumulative_fees, cumulative_funding, "
        " created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, cid, cid, cid, trader_id, account_id, "BTC/USDT", "LONG", state, "ONE_SHOT",
         None, "{}", risk_snapshot_json, "{}",
         gross_pnl, fees, funding, now, now),
    )
```

- [ ] **Step 2: Write failing tests**

Append to `tests/runtime_v2/control_plane/test_status_queries.py`:

```python
# ---------------------------------------------------------------------------
# Task 1: partial PnL (PARTIALLY_CLOSED trades)
# ---------------------------------------------------------------------------

def test_get_pnl_partial_pnl_populated_from_partially_closed(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain_pnl(conn, 1, "PARTIALLY_CLOSED", gross_pnl=100.0, fees=5.0, funding=2.0)
    conn.close()

    view = StatusQueries(ops_db).get_pnl(scope=QueryScope(account_id="main", trader_ids=None))
    assert view.partial_pnl == pytest.approx(100.0)
    assert view.partial_fees == pytest.approx(7.0)   # fees + funding
    assert view.partial_pnl_net == pytest.approx(93.0)


def test_get_pnl_partial_pnl_none_when_no_partially_closed(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain_pnl(conn, 1, "CLOSED", gross_pnl=50.0, fees=1.0, funding=0.5)
    conn.close()

    view = StatusQueries(ops_db).get_pnl(scope=QueryScope(account_id="main", trader_ids=None))
    assert view.partial_pnl is None
    assert view.partial_pnl_net is None


def test_get_pnl_partial_pnl_global_scope(ops_db):
    """Global scope (scope=None legacy path) also computes partial_pnl."""
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain_pnl(conn, 1, "PARTIALLY_CLOSED", account_id="acc_a", gross_pnl=200.0, fees=10.0)
        _add_chain_pnl(conn, 2, "PARTIALLY_CLOSED", account_id="acc_b", gross_pnl=50.0, fees=3.0)
    conn.close()

    view = StatusQueries(ops_db).get_pnl()
    assert view.partial_pnl == pytest.approx(250.0)
    assert view.partial_pnl_net == pytest.approx(237.0)
```

- [ ] **Step 3: Run tests — expect FAIL**

```
pytest tests/runtime_v2/control_plane/test_status_queries.py -k "partial_pnl" -v
```
Expected: `AttributeError: 'PnlView' has no attribute 'partial_pnl'` or similar.

- [ ] **Step 4: Add fields to PnlView dataclass**

In `src/runtime_v2/control_plane/status_queries.py`, find the `PnlView` dataclass (around line 200). Add after `pnl_net`:

```python
    partial_pnl: float | None = None
    partial_fees: float | None = None
    partial_pnl_net: float | None = None
```

- [ ] **Step 5: Add partial query inside `if scope is not None:` block**

Find the `closed_row` query inside `if scope is not None:` (around line 1213). Immediately after the `closed_row = conn.execute(...).fetchone()` line, add:

```python
            partial_row = conn.execute(
                f"SELECT "
                f"SUM(cumulative_gross_pnl), "
                f"SUM(cumulative_fees + cumulative_funding) "
                f"FROM ops_trade_chains "
                f"WHERE lifecycle_state='PARTIALLY_CLOSED' AND {scope_frag}",
                scope_params,
            ).fetchone()
```

- [ ] **Step 6: Add partial query inside `else:` block (scope is None)**

Find the `else:` branch that follows `if scope is not None:` (around line 1224). Immediately after `closed_row = conn.execute(...).fetchone()` in that else block, add:

```python
            partial_row = conn.execute(
                "SELECT "
                "SUM(cumulative_gross_pnl), "
                "SUM(cumulative_fees + cumulative_funding) "
                "FROM ops_trade_chains "
                "WHERE lifecycle_state='PARTIALLY_CLOSED'"
            ).fetchone()
```

- [ ] **Step 7: Compute partial variables after the try/finally block**

After the existing block that computes `gross_pnl`, `pnl_net`, etc. (around line 1246), add:

```python
        partial_pnl: float | None = None
        partial_fees: float | None = None
        partial_pnl_net: float | None = None
        if partial_row and partial_row[0] is not None:
            partial_pnl = float(partial_row[0])
            partial_fees = float(partial_row[1]) if partial_row[1] is not None else 0.0
            partial_pnl_net = partial_pnl - (partial_fees or 0.0)
```

- [ ] **Step 8: Add fields to PnlView constructor call**

At the `return PnlView(...)` call (around line 1392), add:

```python
            partial_pnl=partial_pnl,
            partial_fees=partial_fees,
            partial_pnl_net=partial_pnl_net,
```

- [ ] **Step 9: Run tests — expect PASS**

```
pytest tests/runtime_v2/control_plane/test_status_queries.py -k "partial_pnl" -v
```
Expected: all 3 PASS.

- [ ] **Step 10: Run full test suite to check for regressions**

```
pytest tests/runtime_v2/control_plane/test_status_queries.py -v
```
Expected: all previously-passing tests still PASS.

- [ ] **Step 11: Commit**

```bash
git add src/runtime_v2/control_plane/status_queries.py tests/runtime_v2/control_plane/test_status_queries.py
git commit -m "feat(pnl): add partial_pnl fields to PnlView + PARTIALLY_CLOSED query in get_pnl()"
```

---

### Task 2: PnlView by_trader breakdown

**Files:**
- Modify: `src/runtime_v2/control_plane/status_queries.py`
- Test: `tests/runtime_v2/control_plane/test_status_queries.py`

**Interfaces:**
- Consumes: `_scope_where(scope)` already imported; `json` already imported.
- Produces: `PnlView.by_trader: list[dict] | None` — each dict has keys `trader_id: str`, `open_count: int`, `risk_usdt: float | None`, `closed_pnl: float`, `partial_pnl: float`. Set to `None` when 0 or 1 distinct trader in scope; set to list when 2+.
- Consumed by Task 4 (payload builder reads `view.by_trader`).

- [ ] **Step 1: Write failing tests**

Append to `tests/runtime_v2/control_plane/test_status_queries.py`:

```python
# ---------------------------------------------------------------------------
# Task 2: by_trader breakdown
# ---------------------------------------------------------------------------

def test_get_pnl_by_trader_none_for_single_trader(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain_pnl(conn, 1, "CLOSED", account_id="demo_1", trader_id="trader_a", gross_pnl=100.0)
        _add_chain_pnl(conn, 2, "OPEN",   account_id="demo_1", trader_id="trader_a")
    conn.close()

    view = StatusQueries(ops_db).get_pnl(scope=QueryScope(account_id="demo_1", trader_ids=None))
    assert view.by_trader is None


def test_get_pnl_by_trader_included_for_two_traders(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain_pnl(conn, 1, "CLOSED",           account_id="demo_1", trader_id="trader_a", gross_pnl=100.0, fees=5.0)
        _add_chain_pnl(conn, 2, "OPEN",             account_id="demo_1", trader_id="trader_a")
        _add_chain_pnl(conn, 3, "CLOSED",           account_id="demo_1", trader_id="trader_b", gross_pnl=200.0, fees=8.0)
        _add_chain_pnl(conn, 4, "PARTIALLY_CLOSED", account_id="demo_1", trader_id="trader_b", gross_pnl=30.0,  fees=1.0)
    conn.close()

    view = StatusQueries(ops_db).get_pnl(scope=QueryScope(account_id="demo_1", trader_ids=None))
    assert view.by_trader is not None
    assert len(view.by_trader) == 2

    by_id = {t["trader_id"]: t for t in view.by_trader}
    ta = by_id["trader_a"]
    assert ta["open_count"] == 1
    assert ta["closed_pnl"] == pytest.approx(95.0)   # 100 - 5
    assert ta["partial_pnl"] == pytest.approx(0.0)

    tb = by_id["trader_b"]
    assert tb["open_count"] == 1
    assert tb["closed_pnl"] == pytest.approx(192.0)  # 200 - 8
    assert tb["partial_pnl"] == pytest.approx(29.0)  # 30 - 1


def test_get_pnl_by_trader_risk_from_risk_snapshot_json(ops_db):
    risk_json_a = '{"risk_amount": 120.0}'
    risk_json_b = '{"risk_amount": 80.0}'
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain_pnl(conn, 1, "OPEN", account_id="demo_1", trader_id="trader_a", risk_snapshot_json=risk_json_a)
        _add_chain_pnl(conn, 2, "OPEN", account_id="demo_1", trader_id="trader_b", risk_snapshot_json=risk_json_b)
    conn.close()

    view = StatusQueries(ops_db).get_pnl(scope=QueryScope(account_id="demo_1", trader_ids=None))
    assert view.by_trader is not None
    by_id = {t["trader_id"]: t for t in view.by_trader}
    assert by_id["trader_a"]["risk_usdt"] == pytest.approx(120.0)
    assert by_id["trader_b"]["risk_usdt"] == pytest.approx(80.0)


def test_get_pnl_by_trader_risk_none_when_no_risk_json(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain_pnl(conn, 1, "OPEN", account_id="demo_1", trader_id="trader_a")
        _add_chain_pnl(conn, 2, "OPEN", account_id="demo_1", trader_id="trader_b")
    conn.close()

    view = StatusQueries(ops_db).get_pnl(scope=QueryScope(account_id="demo_1", trader_ids=None))
    assert view.by_trader is not None
    for t in view.by_trader:
        assert t["risk_usdt"] is None
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/runtime_v2/control_plane/test_status_queries.py -k "by_trader" -v
```
Expected: `AttributeError: 'PnlView' has no attribute 'by_trader'` or similar.

- [ ] **Step 3: Add by_trader field to PnlView**

In `PnlView` dataclass, add after `partial_pnl_net`:

```python
    by_trader: list[dict] | None = None
```

- [ ] **Step 4: Add by_trader computation in get_pnl()**

Inside the `if scope is not None:` block (after the partial_row query from Task 1, still inside the `conn` context), add:

```python
            # by_trader: per-trader breakdown, only for single-account non-global scope
            _by_trader: list[dict] | None = None
            if scope.account_id is not None:
                distinct_traders = conn.execute(
                    f"SELECT DISTINCT trader_id FROM ops_trade_chains WHERE {scope_frag}",
                    scope_params,
                ).fetchall()
                trader_ids_in_scope = [r[0] for r in distinct_traders if r[0] is not None]
                if len(trader_ids_in_scope) >= 2:
                    by_trader_list: list[dict] = []
                    for tid in trader_ids_in_scope:
                        oc = conn.execute(
                            "SELECT COUNT(*) FROM ops_trade_chains "
                            "WHERE lifecycle_state IN ('OPEN','PARTIALLY_CLOSED') "
                            "AND account_id=? AND trader_id=?",
                            (scope.account_id, tid),
                        ).fetchone()[0]
                        cp = conn.execute(
                            "SELECT SUM(cumulative_gross_pnl - cumulative_fees - cumulative_funding) "
                            "FROM ops_trade_chains "
                            "WHERE lifecycle_state='CLOSED' AND account_id=? AND trader_id=?",
                            (scope.account_id, tid),
                        ).fetchone()
                        pp = conn.execute(
                            "SELECT SUM(cumulative_gross_pnl - cumulative_fees - cumulative_funding) "
                            "FROM ops_trade_chains "
                            "WHERE lifecycle_state='PARTIALLY_CLOSED' AND account_id=? AND trader_id=?",
                            (scope.account_id, tid),
                        ).fetchone()
                        risk_rows = conn.execute(
                            "SELECT risk_snapshot_json FROM ops_trade_chains "
                            "WHERE lifecycle_state IN ('OPEN','PARTIALLY_CLOSED','WAITING_ENTRY') "
                            "AND account_id=? AND trader_id=? AND risk_snapshot_json IS NOT NULL",
                            (scope.account_id, tid),
                        ).fetchall()
                        risk_total: float | None = None
                        for rrow in risk_rows:
                            try:
                                v = json.loads(rrow[0]).get("risk_amount")
                                if v is not None:
                                    risk_total = (risk_total or 0.0) + float(v)
                            except Exception:
                                pass
                        by_trader_list.append({
                            "trader_id": tid,
                            "open_count": oc,
                            "risk_usdt": risk_total,
                            "closed_pnl": float(cp[0]) if cp and cp[0] is not None else 0.0,
                            "partial_pnl": float(pp[0]) if pp and pp[0] is not None else 0.0,
                        })
                    _by_trader = by_trader_list
```

In the `else:` block (scope is None), add:
```python
            _by_trader = None
```

- [ ] **Step 5: Add to PnlView constructor call**

```python
            by_trader=_by_trader,
```

- [ ] **Step 6: Run tests — expect PASS**

```
pytest tests/runtime_v2/control_plane/test_status_queries.py -k "by_trader" -v
```
Expected: all 4 PASS.

- [ ] **Step 7: Full test suite regression check**

```
pytest tests/runtime_v2/control_plane/test_status_queries.py -v
```
Expected: all previously-passing tests still PASS.

- [ ] **Step 8: Commit**

```bash
git add src/runtime_v2/control_plane/status_queries.py tests/runtime_v2/control_plane/test_status_queries.py
git commit -m "feat(pnl): add by_trader per-trader breakdown to PnlView + get_pnl()"
```

---

### Task 3: by_account extend with Avail/Margin

**Files:**
- Modify: `src/runtime_v2/control_plane/status_queries.py`
- Test: `tests/runtime_v2/control_plane/test_status_queries.py`

**Interfaces:**
- Produces: each dict in `PnlView.by_account` gains `"available_usdt": float | None` and `"margin_usdt": float | None`.
- Consumed by Task 4 (template `_pnl_by_account_lines` reads these keys).

- [ ] **Step 1: Write failing test**

Append to `tests/runtime_v2/control_plane/test_status_queries.py`:

```python
# ---------------------------------------------------------------------------
# Task 3: by_account Avail/Margin
# ---------------------------------------------------------------------------

def test_get_pnl_by_account_includes_avail_and_margin(ops_db):
    from datetime import timedelta
    fresh_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    conn = sqlite3.connect(ops_db)
    with conn:
        # _add_snapshot: available = equity*0.9, margin = 10.0 (hardcoded)
        _add_snapshot(conn, "acc_a", 1000.0, fresh_time)
        _add_chain_pnl(conn, 1, "OPEN", account_id="acc_a")
    conn.close()

    view = StatusQueries(ops_db).get_pnl()
    assert view.by_account is not None
    acc_a = next(r for r in view.by_account if r["account_id"] == "acc_a")
    assert acc_a["available_usdt"] == pytest.approx(900.0)   # equity * 0.9
    assert acc_a["margin_usdt"] == pytest.approx(10.0)
```

- [ ] **Step 2: Run test — expect FAIL**

```
pytest tests/runtime_v2/control_plane/test_status_queries.py::test_get_pnl_by_account_includes_avail_and_margin -v
```
Expected: `KeyError: 'available_usdt'` or `AssertionError`.

- [ ] **Step 3: Extend by_account dict construction**

Find the `by_account.append({...})` call in the global scope CTE loop (around line 1349). Change it to:

```python
                    by_account.append({
                        "account_id": acc_id,
                        "net_pnl": net_pnl_acc,
                        "open_count": open_c,
                        "available_usdt": snap_r[2] if snap_r else None,
                        "margin_usdt": snap_r[4] if snap_r else None,
                        "age_seconds": age,
                        "stale": is_stale_acc,
                    })
```

> Note: Remove `"equity_usdt": snap_r[1] if snap_r else None` — it is no longer needed. The snap_r indices are: [0]=account_id, [1]=equity_usdt, [2]=available_balance_usdt, [3]=total_open_risk_usdt, [4]=total_margin_used_usdt, [5]=account_unrealized_pnl_usdt, [6]=source, [7]=captured_at.

- [ ] **Step 4: Run test — expect PASS**

```
pytest tests/runtime_v2/control_plane/test_status_queries.py::test_get_pnl_by_account_includes_avail_and_margin -v
```
Expected: PASS.

- [ ] **Step 5: Full test suite regression check**

```
pytest tests/runtime_v2/control_plane/test_status_queries.py -v
```
Expected: all previously-passing tests still PASS (any test that asserted `equity_usdt` in by_account dicts will need updating — fix those assertions by removing the `equity_usdt` check).

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/status_queries.py tests/runtime_v2/control_plane/test_status_queries.py
git commit -m "feat(pnl): extend by_account with available_usdt and margin_usdt, remove equity_usdt"
```

---

### Task 4: Payload builder + full template rewrite

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/dashboard.py` (`_build_pnl_payload`)
- Modify: `src/runtime_v2/control_plane/formatters/templates/dashboard.py` (all PNL functions + `_PNL_BLOCKS`)
- Test: `tests/runtime_v2/control_plane/test_dashboard_formatter.py`

**Interfaces:**
- Consumes (from Task 1–3): `view.partial_pnl`, `view.partial_pnl_net`, `view.by_trader`, `view.available_balance_usdt`, `view.total_margin_used_usdt`, `view.by_account[*]["available_usdt"]`, `view.by_account[*]["margin_usdt"]`.
- Consumes existing: `view.pnl_net`, `view.total_open_risk_usdt`, `view.accounts_fresh`, `view.accounts_stale`, `view.accounts_in_scope`, `view.account_unrealized_pnl_usdt`.

- [ ] **Step 1: Write failing tests for template functions**

Append to `tests/runtime_v2/control_plane/test_dashboard_formatter.py`:

```python
# ---------------------------------------------------------------------------
# Task 4: PNL template rewrite
# ---------------------------------------------------------------------------

from src.runtime_v2.control_plane.formatters.templates.dashboard import (
    _pnl_account_lines,
    _pnl_realized_lines,
    _pnl_realized_label,
    _pnl_by_trader_lines,
    _pnl_by_account_lines,
)


def test_pnl_account_lines_shows_futures_wallet():
    p = {
        "available_balance_usdt": 1234.0,
        "total_margin_used_usdt": 456.0,
        "futures_wallet_usdt": 1690.0,
        "account_unrealized_pnl_usdt": 12.34,
        "total_open_risk_usdt": 123.0,
        "captured_at": "2026-06-24T14:32:05+00:00",
        "snapshot_age_seconds": 45.0,
        "snapshot_stale": False,
        "account_id": "demo_1",
    }
    text = _pnl_account_lines(p)
    assert "Available:" in text
    assert "Margin in use:" in text
    assert "Futures wallet:" in text
    assert "1,690.00" in text
    assert "Open risk*:" in text
    assert "Snapshot:" in text
    # equity must NOT appear
    assert "Equity" not in text


def test_pnl_account_lines_stale_marker():
    p = {
        "available_balance_usdt": 100.0,
        "total_margin_used_usdt": 50.0,
        "futures_wallet_usdt": 150.0,
        "account_unrealized_pnl_usdt": None,
        "total_open_risk_usdt": None,
        "captured_at": "2026-06-24T14:28:41+00:00",
        "snapshot_age_seconds": 213.0,
        "snapshot_stale": True,
        "account_id": "demo_1",
    }
    text = _pnl_account_lines(p)
    assert "STALE" in text


def test_pnl_account_lines_no_snapshot():
    p = {
        "available_balance_usdt": None,
        "total_margin_used_usdt": None,
        "futures_wallet_usdt": None,
        "account_unrealized_pnl_usdt": None,
        "total_open_risk_usdt": None,
        "captured_at": None,
        "snapshot_age_seconds": None,
        "snapshot_stale": False,
        "account_id": "demo_1",
    }
    text = _pnl_account_lines(p)
    assert "nessun snapshot" in text


def test_pnl_realized_lines_shows_closed_and_partial():
    p = {"pnl_net": 890.20, "partial_pnl_net": 45.80}
    text = _pnl_realized_lines(p)
    assert "Closed:" in text
    assert "Partial open:" in text
    assert "Totale:" in text
    assert "936.00" in text


def test_pnl_realized_lines_omits_partial_when_zero():
    p = {"pnl_net": 100.0, "partial_pnl_net": None}
    text = _pnl_realized_lines(p)
    assert "Closed:" in text
    assert "Partial open:" not in text
    assert "Totale:" in text


def test_pnl_realized_lines_no_trades():
    p = {"pnl_net": None, "partial_pnl_net": None}
    text = _pnl_realized_lines(p)
    assert "Nessun trade chiuso" in text


def test_pnl_realized_label_global():
    assert _pnl_realized_label({"is_global": True}) == "Realized — All accounts:"


def test_pnl_realized_label_single_trader():
    assert _pnl_realized_label({"is_global": False, "trader_id": "trader_a", "by_trader": None}) == "Realized — trader_a:"


def test_pnl_realized_label_multi_trader():
    p = {
        "is_global": False,
        "trader_id": None,
        "by_trader": [
            {"trader_id": "trader_a", "open_count": 0, "risk_usdt": None, "closed_pnl": 0.0, "partial_pnl": 0.0},
            {"trader_id": "trader_b", "open_count": 0, "risk_usdt": None, "closed_pnl": 0.0, "partial_pnl": 0.0},
        ],
        "account_id": "demo_1",
    }
    label = _pnl_realized_label(p)
    assert label == "Realized — trader_a, trader_b:"


def test_pnl_realized_label_account_wide():
    p = {"is_global": False, "trader_id": None, "by_trader": None, "account_id": "demo_1"}
    assert _pnl_realized_label(p) == "Realized — demo_1:"


def test_pnl_by_trader_lines_format():
    p = {
        "by_trader": [
            {"trader_id": "trader_a", "open_count": 3, "risk_usdt": 120.0, "closed_pnl": 890.20, "partial_pnl": 45.80},
            {"trader_id": "trader_b", "open_count": 2, "risk_usdt": None,  "closed_pnl": 234.50, "partial_pnl": 0.0},
        ]
    }
    text = _pnl_by_trader_lines(p)
    assert "trader_a" in text
    assert "Open: 3" in text
    assert "Risk: 120.00" in text
    assert "Closed: +890.20" in text
    assert "Partial: +45.80" in text
    assert "trader_b" in text
    # trader_b has no risk_usdt — must not appear
    assert "Risk:" not in text.split("trader_b")[1].split("\n")[0]
    # trader_b partial is 0 — must not appear
    assert "Partial:" not in text.split("trader_b")[1].split("\n")[0]


def test_pnl_by_account_lines_format():
    p = {
        "by_account": [
            {"account_id": "demo_1", "net_pnl": 890.20, "available_usdt": 1450.0, "margin_usdt": 890.0, "age_seconds": 32.0, "stale": False},
            {"account_id": "demo_2", "net_pnl": 344.30, "available_usdt": None,   "margin_usdt": None,  "age_seconds": 18.0, "stale": False},
            {"account_id": "demo_3", "net_pnl": 156.80, "available_usdt": None,   "margin_usdt": None,  "age_seconds": 240.0, "stale": True},
        ]
    }
    text = _pnl_by_account_lines(p)
    assert "demo_1" in text
    assert "Avail: 1450" in text
    assert "Margin: 890" in text
    assert "Net: +890.20" in text
    assert "demo_3" in text
    assert "STALE" in text
    assert "4m ago" in text   # 240s → 4m


def test_pnl_payload_includes_futures_wallet_and_excludes_equity(tmp_path):
    """Integration: format_dashboard_view builds PNL payload with futures_wallet and no equity."""
    _apply_migrations(str(tmp_path / "ops.sqlite3"))
    db_path = str(tmp_path / "ops.sqlite3")
    from datetime import timedelta
    fresh_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO ops_account_snapshots "
            "(account_id, equity_usdt, available_balance_usdt, total_open_risk_usdt, "
            "total_margin_used_usdt, account_unrealized_pnl_usdt, source, captured_at, "
            "payload_json, snapshot_status, error_code) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("demo_1", 2000.0, 1500.0, 50.0, 500.0, 20.0, "ccxt_bybit:demo", fresh_time, "{}", "OK", None)
        )
    conn.close()

    queries = StatusQueries(db_path)
    scope = QueryScope(account_id="demo_1", trader_ids=None)
    text, _ = format_dashboard_view("pnl", scope, queries)
    assert "Futures wallet" in text
    assert "1,500" in text  # available
    assert "500" in text    # margin
    assert "Equity" not in text
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/runtime_v2/control_plane/test_dashboard_formatter.py -k "pnl_account_lines or pnl_realized or pnl_by_trader or pnl_by_account or pnl_payload" -v
```
Expected: `ImportError` for `_pnl_by_trader_lines`, plus assertion failures for other tests.

- [ ] **Step 3: Update `_build_pnl_payload()` in formatters/dashboard.py**

Find `_build_pnl_payload` (around line 410). Replace the existing `payload = {...}` block with:

```python
    payload = {
        **_build_scope_meta(scope),
        "account_id": scope.account_id or "All accounts",
        "updated_at": view.updated_at,
        "total": accounts_in_scope if is_global else 1,
        "page_display": "1/1",
        "filters_str": _build_filters_str(is_global, filters),
        "order_str": "Net desc" if is_global else None,
        "is_global": is_global,
        # equity_usdt removed — use futures_wallet_usdt instead
        "available_balance_usdt": view.available_balance_usdt,
        "total_margin_used_usdt": view.total_margin_used_usdt,
        "futures_wallet_usdt": (
            (view.available_balance_usdt or 0.0) + (view.total_margin_used_usdt or 0.0)
            if (view.available_balance_usdt is not None or view.total_margin_used_usdt is not None)
            else None
        ),
        "gross_pnl": view.gross_pnl,
        "total_fees": view.total_fees,
        "pnl_net": view.pnl_net,
        "partial_pnl": view.partial_pnl,
        "partial_fees": view.partial_fees,
        "partial_pnl_net": view.partial_pnl_net,
        "by_trader": view.by_trader,
        "open_count": view.open_count,
        "waiting_entry_count": view.waiting_entry_count,
        "accounts_in_scope": view.accounts_in_scope,
        "by_account": view.by_account,
        "captured_at": view.captured_at,
        "source": view.source,
        "account_unrealized_pnl_usdt": view.account_unrealized_pnl_usdt,
        "snapshot_age_seconds": view.snapshot_age_seconds,
        "snapshot_stale": view.snapshot_stale,
        "total_open_risk_usdt": view.total_open_risk_usdt,
        "accounts_fresh": view.accounts_fresh,
        "accounts_stale": view.accounts_stale,
    }
```

- [ ] **Step 4: Rewrite `_pnl_account_lines()` in templates/dashboard.py**

Replace the existing `_pnl_account_lines` function (around line 286):

```python
def _pnl_account_lines(p: dict) -> str:
    from datetime import datetime
    available = p.get("available_balance_usdt")
    margin = p.get("total_margin_used_usdt")
    futures_wallet = p.get("futures_wallet_usdt")
    upnl = p.get("account_unrealized_pnl_usdt")
    risk = p.get("total_open_risk_usdt")
    captured_at = p.get("captured_at")
    age = p.get("snapshot_age_seconds")
    stale = p.get("snapshot_stale", False)

    if available is None and margin is None and captured_at is None:
        return "  n/a — nessun snapshot disponibile"

    parts = []
    if available is not None:
        parts.append(f"  Available:      {available:>12,.2f} USDT")
    if margin is not None:
        parts.append(f"  Margin in use:  {margin:>12,.2f} USDT")
    if available is not None or margin is not None:
        parts.append(f"  {'─' * 29}")
    if futures_wallet is not None:
        parts.append(f"  Futures wallet: {futures_wallet:>12,.2f} USDT")
    if upnl is not None:
        sign = "+" if upnl >= 0 else ""
        parts.append(f"  uPnL live:      {sign}{upnl:>11.2f} USDT")
    if risk is not None:
        parts.append(f"  Open risk*:     {risk:>12.2f} USDT")
    if captured_at:
        try:
            dt = datetime.fromisoformat(captured_at)
            time_str = dt.strftime("%H:%M:%S") + " UTC"
        except ValueError:
            time_str = captured_at
        age_str = f"age {int(age)}s" if age is not None else "age ?"
        stale_str = " · STALE" if stale else ""
        parts.append(f"  Snapshot: {time_str} · {age_str}{stale_str}")

    return "\n".join(parts) if parts else "  n/a — nessun snapshot disponibile"
```

- [ ] **Step 5: Rewrite `_pnl_realized_label()` in templates/dashboard.py**

Replace the existing `_pnl_realized_label` function (around line 319):

```python
def _pnl_realized_label(p: dict) -> str:
    if p.get("is_global"):
        return "Realized — All accounts:"
    by_trader = p.get("by_trader")
    if by_trader and len(by_trader) >= 2:
        names = ", ".join(t["trader_id"] for t in by_trader)
        return f"Realized — {names}:"
    trader_id = p.get("trader_id")
    if trader_id:
        return f"Realized — {trader_id}:"
    account_id = p.get("account_id") or ""
    return f"Realized — {account_id}:"
```

- [ ] **Step 6: Rewrite `_pnl_realized_lines()` in templates/dashboard.py**

Replace the existing `_pnl_realized_lines` function (around line 328):

```python
def _pnl_realized_lines(p: dict) -> str:
    pnl_net = p.get("pnl_net")
    partial_pnl_net = p.get("partial_pnl_net")

    has_closed = pnl_net is not None
    has_partial = partial_pnl_net is not None and partial_pnl_net != 0.0

    if not has_closed and not has_partial:
        return "  Nessun trade chiuso."

    parts = []
    if has_closed:
        sign = "+" if pnl_net >= 0 else ""
        parts.append(f"  Closed:        {sign}{pnl_net:.2f} USDT")
    if has_partial:
        sign = "+" if partial_pnl_net >= 0 else ""
        parts.append(f"  Partial open:   {sign}{partial_pnl_net:.2f} USDT")
    totale = (pnl_net or 0.0) + (partial_pnl_net or 0.0)
    parts.append(f"  {'─' * 29}")
    sign = "+" if totale >= 0 else ""
    parts.append(f"  Totale:        {sign}{totale:.2f} USDT")
    return "\n".join(parts)
```

- [ ] **Step 7: Add `_pnl_by_trader_lines()` function in templates/dashboard.py**

Add after `_pnl_realized_lines`:

```python
def _pnl_by_trader_lines(p: dict) -> str:
    rows = p.get("by_trader") or []
    lines = []
    for t in rows:
        parts = [t["trader_id"], f"Open: {t['open_count']}"]
        if t.get("risk_usdt") is not None:
            parts.append(f"Risk: {t['risk_usdt']:.2f}")
        sign = "+" if t["closed_pnl"] >= 0 else ""
        parts.append(f"Closed: {sign}{t['closed_pnl']:.2f}")
        if t["partial_pnl"] != 0.0:
            sign = "+" if t["partial_pnl"] >= 0 else ""
            parts.append(f"Partial: {sign}{t['partial_pnl']:.2f}")
        lines.append("  " + " · ".join(parts))
    return "\n".join(lines)
```

- [ ] **Step 8: Rewrite `_pnl_by_account_lines()` in templates/dashboard.py**

Replace the existing `_pnl_by_account_lines` function:

```python
def _pnl_by_account_lines(p: dict) -> str:
    rows = p.get("by_account") or []
    lines = []
    for r in rows:
        acc_id = r.get("account_id", "?")
        net = r.get("net_pnl", 0.0)
        sign = "+" if net >= 0 else ""
        age = r.get("age_seconds")
        stale = r.get("stale", False)
        available = r.get("available_usdt")
        margin = r.get("margin_usdt")

        if stale:
            if age is not None and age >= 60:
                age_human = f"{int(age // 60)}m ago"
            elif age is not None:
                age_human = f"{int(age)}s ago"
            else:
                age_human = "?"
            lines.append(f"{acc_id} · STALE · last {age_human} · Net: {sign}{net:.2f}")
        else:
            parts = [acc_id]
            if available is not None:
                parts.append(f"Avail: {available:.0f}")
            if margin is not None:
                parts.append(f"Margin: {margin:.0f}")
            parts.append(f"Net: {sign}{net:.2f}")
            if age is not None:
                parts.append(f"age {int(age)}s")
            lines.append(" · ".join(parts))
    return "\n".join(lines) if lines else "n/a"
```

- [ ] **Step 9: Rewrite `_PNL_BLOCKS` in templates/dashboard.py**

Replace the entire `_PNL_BLOCKS` list (from `_PNL_BLOCKS: list = [` to `TEMPLATE_DASHBOARD_PNL = ...`):

```python
_PNL_BLOCKS: list = [
    *_dash_header_full("💰", "PnL"),
    # Non-global: show account snapshot with dynamic header
    ConditionalBlock(
        condition=lambda p: not p.get("is_global"),
        blocks=[
            DerivedBlock(text_fn=lambda p: f"Account snapshot ({p.get('account_id')}):"),
            DerivedBlock(text_fn=_pnl_account_lines),
            SeparatorBlock(),
        ],
    ),
    # Global: summary line + financial aggregates
    ConditionalBlock(
        condition=lambda p: bool(p.get("is_global")),
        blocks=[
            DerivedBlock(text_fn=lambda p: (
                f"Accounts: {p.get('accounts_in_scope', 0)} · "
                f"Snapshots: {p.get('accounts_fresh', 0)} fresh · {p.get('accounts_stale', 0)} stale"
            )),
            ConditionalBlock(
                condition=lambda p: p.get("futures_wallet_usdt") is not None,
                blocks=[
                    DerivedBlock(text_fn=lambda p: f"Futures wallet: {p['futures_wallet_usdt']:,.2f} USDT   (fresh only)"),
                ],
            ),
            ConditionalBlock(
                condition=lambda p: p.get("available_balance_usdt") is not None,
                blocks=[
                    DerivedBlock(text_fn=lambda p: f"Available:      {p['available_balance_usdt']:,.2f} USDT"),
                ],
            ),
            ConditionalBlock(
                condition=lambda p: p.get("total_margin_used_usdt") is not None,
                blocks=[
                    DerivedBlock(text_fn=lambda p: f"Margin in use:  {p['total_margin_used_usdt']:,.2f} USDT"),
                ],
            ),
            ConditionalBlock(
                condition=lambda p: p.get("account_unrealized_pnl_usdt") is not None,
                blocks=[
                    DerivedBlock(text_fn=lambda p: (
                        f"uPnL aggregate: +{p['account_unrealized_pnl_usdt']:.2f} USDT"
                        if p["account_unrealized_pnl_usdt"] >= 0
                        else f"uPnL aggregate: {p['account_unrealized_pnl_usdt']:.2f} USDT"
                    )),
                ],
            ),
            SeparatorBlock(),
        ],
    ),
    DerivedBlock(text_fn=_pnl_realized_label),
    DerivedBlock(text_fn=_pnl_realized_lines),
    # By trader: only when 2+ traders in scope
    ConditionalBlock(
        condition=lambda p: len(p.get("by_trader") or []) >= 2,
        blocks=[
            StaticBlock(""),
            StaticBlock("By trader:"),
            DerivedBlock(text_fn=_pnl_by_trader_lines),
        ],
    ),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: (
        f"Open: {p.get('open_count', 0)} · Waiting entry: {p.get('waiting_entry_count', 0)}"
    )),
    # Global: by_account breakdown
    ConditionalBlock(
        condition=lambda p: bool(p.get("by_account")),
        blocks=[
            SeparatorBlock(),
            StaticBlock("By account:"),
            DerivedBlock(text_fn=_pnl_by_account_lines),
        ],
    ),
]

TEMPLATE_DASHBOARD_PNL = TemplateConfig(_PNL_BLOCKS, payload_transform=None)
```

- [ ] **Step 10: Run template tests — expect PASS**

```
pytest tests/runtime_v2/control_plane/test_dashboard_formatter.py -k "pnl_account_lines or pnl_realized or pnl_by_trader or pnl_by_account or pnl_payload" -v
```
Expected: all PASS.

- [ ] **Step 11: Run full test suite**

```
pytest tests/runtime_v2/control_plane/ -v
```
Expected: all PASS. Fix any test that asserted the old "Equity:" line or old `_pnl_realized_lines` format (replace `"Gross:"` / `"Fees:"` / `"Net:"` expectations with new `"Closed:"` / `"Totale:"`).

- [ ] **Step 12: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/dashboard.py src/runtime_v2/control_plane/formatters/templates/dashboard.py tests/runtime_v2/control_plane/test_dashboard_formatter.py
git commit -m "feat(pnl): rewrite PNL payload + template — futures wallet, partial PnL, by trader, global header"
```

---

### Task 5: AccountSnapshotWorker — on_snapshot_saved callback

**Files:**
- Modify: `src/runtime_v2/lifecycle/account_snapshot_worker.py`
- Test: `tests/runtime_v2/lifecycle/test_account_snapshot_worker.py`

**Interfaces:**
- Produces: `AccountSnapshotWorker.__init__` gains `on_snapshot_saved: Callable[[str], None] | None = None`.
- The callback is called synchronously from within `_fetch_one` after a successful OK save; errors are caught and logged.
- Consumed by Task 6 (bootstrap wires a lambda that dispatches `on_snapshot_event`).

- [ ] **Step 1: Write failing tests**

Append to `tests/runtime_v2/lifecycle/test_account_snapshot_worker.py`:

```python
# ---------------------------------------------------------------------------
# Task 5: on_snapshot_saved callback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_callback_called_on_ok_snapshot():
    port = _make_port(account_id="demo_1")   # returns snapshot with status="OK"
    repo = _make_repo()
    called_with: list[str] = []
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["demo_1"],
        interval_seconds=999,
        on_snapshot_saved=lambda acc_id: called_with.append(acc_id),
    )
    await worker._fetch_one("demo_1")
    assert called_with == ["demo_1"]


@pytest.mark.asyncio
async def test_callback_not_called_on_failed_snapshot():
    port = _make_port(account_id="demo_1")
    port.get_account_state.return_value = _make_snapshot("demo_1", status="FAILED")
    repo = _make_repo()
    called_with: list[str] = []
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["demo_1"],
        interval_seconds=999,
        on_snapshot_saved=lambda acc_id: called_with.append(acc_id),
    )
    await worker._fetch_one("demo_1")
    assert called_with == []


@pytest.mark.asyncio
async def test_callback_not_called_on_port_exception():
    port = _make_port(raise_exc=RuntimeError("network error"))
    repo = _make_repo()
    called_with: list[str] = []
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["demo_1"],
        interval_seconds=999,
        on_snapshot_saved=lambda acc_id: called_with.append(acc_id),
    )
    await worker._fetch_one("demo_1")
    assert called_with == []


@pytest.mark.asyncio
async def test_callback_error_does_not_crash_worker():
    port = _make_port(account_id="demo_1")
    repo = _make_repo()
    def _bad_callback(acc_id: str) -> None:
        raise ValueError("oops")
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["demo_1"],
        interval_seconds=999,
        on_snapshot_saved=_bad_callback,
    )
    # Must not raise
    await worker._fetch_one("demo_1")
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/runtime_v2/lifecycle/test_account_snapshot_worker.py -k "callback" -v
```
Expected: `TypeError: __init__() got an unexpected keyword argument 'on_snapshot_saved'`.

- [ ] **Step 3: Implement the callback in AccountSnapshotWorker**

Replace `src/runtime_v2/lifecycle/account_snapshot_worker.py` with:

```python
# src/runtime_v2/lifecycle/account_snapshot_worker.py
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 60
_DEFAULT_STALE_AFTER = 180


class AccountSnapshotWorker:
    def __init__(
        self,
        *,
        port,
        repository,
        account_ids: list[str],
        interval_seconds: int = _DEFAULT_INTERVAL,
        stale_after_seconds: int = _DEFAULT_STALE_AFTER,
        on_snapshot_saved: Callable[[str], None] | None = None,
    ) -> None:
        self._port = port
        self._repository = repository
        self._account_ids = list(account_ids)
        self._interval = interval_seconds
        self._stale_after = stale_after_seconds
        self._pending_refresh: set[str] = set()
        self._on_snapshot_saved = on_snapshot_saved

    async def run(self) -> None:
        await self._fetch_all()
        while True:
            await asyncio.sleep(self._interval)
            pending = list(self._pending_refresh)
            self._pending_refresh.clear()
            for account_id in pending:
                await self._fetch_one(account_id)
            just_fetched = set(pending)
            await self._fetch_all(skip=just_fetched)

    def trigger(self, account_id: str) -> None:
        self._pending_refresh.add(account_id)

    async def _fetch_all(self, skip: set[str] | None = None) -> None:
        for account_id in self._account_ids:
            if skip and account_id in skip:
                continue
            await self._fetch_one(account_id)

    async def _fetch_one(self, account_id: str) -> None:
        try:
            snap = await asyncio.get_running_loop().run_in_executor(
                None, self._port.get_account_state, account_id
            )
            self._repository.save_account(snap, account_id)
            if snap.snapshot_status == "OK" and self._on_snapshot_saved is not None:
                try:
                    self._on_snapshot_saved(account_id)
                except Exception as exc:
                    logger.warning(
                        "AccountSnapshotWorker: on_snapshot_saved callback error for %s: %s",
                        account_id, exc,
                    )
        except Exception as exc:
            logger.warning("AccountSnapshotWorker: failed for %s: %s", account_id, exc)
            from src.runtime_v2.lifecycle.ports import AccountStateSnapshot
            failed_snap = AccountStateSnapshot(
                account_id=account_id,
                captured_at=datetime.now(timezone.utc),
                source="unknown",
                snapshot_status="FAILED",
                error_code=type(exc).__name__,
            )
            try:
                self._repository.save_account(failed_snap, account_id)
            except Exception:
                pass


__all__ = ["AccountSnapshotWorker"]
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/runtime_v2/lifecycle/test_account_snapshot_worker.py -v
```
Expected: all PASS (both new and existing tests).

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/account_snapshot_worker.py tests/runtime_v2/lifecycle/test_account_snapshot_worker.py
git commit -m "feat(snapshot-worker): add on_snapshot_saved callback, fired only on OK snapshots"
```

---

### Task 6: DashboardManager.on_snapshot_event() + bootstrap wiring

**Files:**
- Modify: `src/runtime_v2/control_plane/dashboard_manager.py`
- Modify: `src/runtime_v2/control_plane/bootstrap.py`
- Test: `tests/runtime_v2/control_plane/test_dashboard_manager.py`

**Interfaces:**
- Consumes: `_parse_view`, `_THROTTLE_SECONDS`, `self._last_edit`, `self._pending_tasks`, `self._get_all_dashboards()`, `self._do_refresh()`, `self._deferred_refresh()` — all exist in `dashboard_manager.py`.
- Consumes: `AccountSnapshotWorker(on_snapshot_saved=...)` from Task 5.
- Produces: `DashboardManager.on_snapshot_event(account_id: str) -> None` (async).

- [ ] **Step 1: Write failing tests**

Append to `tests/runtime_v2/control_plane/test_dashboard_manager.py`:

```python
# ---------------------------------------------------------------------------
# Task 6: on_snapshot_event
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_snapshot_event_refreshes_pnl_dashboard_in_scope(tmp_path):
    bot = _make_mock_bot(message_id=77)
    manager = _make_manager(tmp_path, bot=bot)
    _patch_render_view(manager)

    scope = _make_scope("acc1")
    await manager.create(scope=scope, chat_id=500, thread_id=0)

    # Switch to pnl view manually
    conn = sqlite3.connect(manager._ops_db_path)
    conn.execute("UPDATE ops_dashboard_messages SET current_view='pnl:0' WHERE chat_id=500")
    conn.commit()
    conn.close()

    bot.edit_message_text.reset_mock()
    manager._last_edit.clear()

    await manager.on_snapshot_event(account_id="acc1")
    assert bot.edit_message_text.call_count == 1


@pytest.mark.asyncio
async def test_on_snapshot_event_skips_non_pnl_views(tmp_path):
    bot = _make_mock_bot(message_id=78)
    manager = _make_manager(tmp_path, bot=bot)
    _patch_render_view(manager)

    scope = _make_scope("acc1")
    await manager.create(scope=scope, chat_id=501, thread_id=0)
    # Dashboard is in default 'attivi:0' view — not pnl

    bot.edit_message_text.reset_mock()
    manager._last_edit.clear()

    await manager.on_snapshot_event(account_id="acc1")
    assert bot.edit_message_text.call_count == 0, "attivi view must not be refreshed on snapshot event"


@pytest.mark.asyncio
async def test_on_snapshot_event_skips_different_account(tmp_path):
    bot = _make_mock_bot(message_id=79)
    manager = _make_manager(tmp_path, bot=bot)
    _patch_render_view(manager)

    scope = _make_scope("acc1")
    await manager.create(scope=scope, chat_id=502, thread_id=0)

    conn = sqlite3.connect(manager._ops_db_path)
    conn.execute("UPDATE ops_dashboard_messages SET current_view='pnl:0' WHERE chat_id=502")
    conn.commit()
    conn.close()

    bot.edit_message_text.reset_mock()
    manager._last_edit.clear()

    # Snapshot event for a different account
    await manager.on_snapshot_event(account_id="acc_other")
    assert bot.edit_message_text.call_count == 0


@pytest.mark.asyncio
async def test_on_snapshot_event_refreshes_global_scope_pnl_dashboard(tmp_path):
    bot = _make_mock_bot(message_id=80)
    # Global scope: account_id=None
    manager = _make_manager(tmp_path, bot=bot, scope=_make_global_scope())
    _patch_render_view(manager)

    global_scope = _make_global_scope()
    await manager.create(scope=global_scope, chat_id=503, thread_id=0)

    conn = sqlite3.connect(manager._ops_db_path)
    conn.execute("UPDATE ops_dashboard_messages SET current_view='pnl:0' WHERE chat_id=503")
    conn.commit()
    conn.close()

    bot.edit_message_text.reset_mock()
    manager._last_edit.clear()

    # Global dashboard has scope_account_id=NULL → refreshed for any account
    await manager.on_snapshot_event(account_id="any_account")
    assert bot.edit_message_text.call_count == 1
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest tests/runtime_v2/control_plane/test_dashboard_manager.py -k "snapshot_event" -v
```
Expected: `AttributeError: 'DashboardManager' object has no attribute 'on_snapshot_event'`.

- [ ] **Step 3: Implement on_snapshot_event() in dashboard_manager.py**

Find `on_trade_event` in `src/runtime_v2/control_plane/dashboard_manager.py` (around line 583). Add the following method immediately after it (before `_deferred_refresh`):

```python
    async def on_snapshot_event(self, account_id: str) -> None:
        """Update PNL dashboards in scope for account_id after a new OK snapshot.
        Only dashboards with current_view == 'pnl' are refreshed.
        """
        rows = self._get_all_dashboards()
        for chat_id, thread_id, message_id, scope_account_id, scope_trader_id, current_view_str in rows:
            view_name, _ = _parse_view(current_view_str)
            if view_name != "pnl":
                continue
            if scope_account_id is not None and scope_account_id != account_id:
                continue

            key = (chat_id, thread_id)
            now = time.monotonic()
            last = self._last_edit.get(key, 0.0)
            elapsed = now - last

            if elapsed >= _THROTTLE_SECONDS:
                await self._do_refresh(
                    chat_id=chat_id,
                    thread_id=thread_id,
                    message_id=message_id,
                    scope_account_id=scope_account_id,
                    scope_trader_id=scope_trader_id,
                    current_view_str=current_view_str,
                )
            else:
                if key not in self._pending_tasks or self._pending_tasks[key].done():
                    delay = _THROTTLE_SECONDS - elapsed
                    task = asyncio.get_running_loop().create_task(
                        self._deferred_refresh(
                            delay=delay,
                            chat_id=chat_id,
                            thread_id=thread_id,
                            message_id=message_id,
                            scope_account_id=scope_account_id,
                            scope_trader_id=scope_trader_id,
                            current_view_str=current_view_str,
                        )
                    )
                    self._pending_tasks[key] = task
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/runtime_v2/control_plane/test_dashboard_manager.py -k "snapshot_event" -v
```
Expected: all 4 PASS.

- [ ] **Step 5: Wire callback in bootstrap.py**

In `src/runtime_v2/control_plane/bootstrap.py`, find where `AccountSnapshotWorker` might be constructed. This is NOT in `build_control_plane` currently — the worker is constructed in the main entry point. Check by searching:

```
grep -rn "AccountSnapshotWorker" src/
```

If the worker is constructed in `bootstrap.py`, add wiring there. If it's in `main.py` or another entry point, add it there. The pattern is:

```python
import asyncio

def _on_snap(account_id: str) -> None:
    asyncio.create_task(dashboard_manager.on_snapshot_event(account_id))

worker = AccountSnapshotWorker(
    port=port,
    repository=repository,
    account_ids=account_ids,
    on_snapshot_saved=_on_snap,
)
```

> `asyncio.create_task` is safe here because `_on_snap` is always called from within `_fetch_one`, which is an async context running in the event loop. Do NOT use `asyncio.get_event_loop().create_task()` (deprecated).

- [ ] **Step 6: Run full test suite**

```
pytest tests/ -v --tb=short
```
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/control_plane/dashboard_manager.py src/runtime_v2/control_plane/bootstrap.py tests/runtime_v2/control_plane/test_dashboard_manager.py
git commit -m "feat(dashboard): add on_snapshot_event() + wire AccountSnapshotWorker callback for PNL auto-refresh"
```

---

## Self-Review

### Spec coverage check

| Spec section | Covered by task |
|---|---|
| D1: Remove equity_usdt, show available/margin/futures_wallet | Task 3 (by_account), Task 4 (payload + template) |
| D2: Realized split Closed / Partial open | Task 1 (query), Task 4 (template) |
| D3: Auto-refresh on snapshot | Task 5 (worker callback), Task 6 (on_snapshot_event + wiring) |
| D4: Account snapshot label with account_id | Task 4 (_PNL_BLOCKS DerivedBlock header) |
| D5: By trader breakdown (2+ traders) | Task 2 (query), Task 4 (template) |
| D6: by_account Avail/Margin | Task 3 (query), Task 4 (template) |
| Spec 6b edge case "Nessun trade chiuso" | Task 4 (_pnl_realized_lines) |
| Spec 6b multi-trader label | Task 4 (_pnl_realized_label) |
| Spec 6d global header consolidation | Task 4 (_PNL_BLOCKS) |
| Spec 6e STALE age in minutes | Task 4 (_pnl_by_account_lines) |
| Spec 11: callback fire-and-forget | Task 5 (try/except in _fetch_one) |

### Placeholder scan

No "TBD", "TODO", or empty steps — each step has concrete code.

### Type consistency

- `partial_pnl`, `partial_fees`, `partial_pnl_net` — defined in Task 1, read in Task 4 payload builder.
- `by_trader` — defined in Task 2 as `list[dict] | None`, dict keys `trader_id`, `open_count`, `risk_usdt`, `closed_pnl`, `partial_pnl` — all used consistently in Task 4 template.
- `available_usdt`, `margin_usdt` — defined in Task 3 in by_account dicts, read in Task 4 `_pnl_by_account_lines`.
- `futures_wallet_usdt` — computed in Task 4 payload builder, consumed in same task's template.
- `on_snapshot_saved` — defined in Task 5, wired in Task 6.
- `on_snapshot_event` — defined in Task 6, matches `Callable[[str], None]` signature from Task 5.

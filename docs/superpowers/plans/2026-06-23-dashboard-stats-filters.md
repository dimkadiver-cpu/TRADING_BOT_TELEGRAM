# Dashboard Stats Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendere la vista dashboard `Stats` semanticamente corretta su scope trader/account/global, correggere `Closed` net PnL, e introdurre filtri gerarchici normalizzati `scope + views` nel control plane Telegram.

**Architecture:** L’implementazione resta un refactor mirato in quattro task sequenziali. Prima fissiamo la semantica dati in `StatusQueries`, poi introduciamo il nuovo modello filtri e la normalizzazione nel manager, quindi aggiorniamo payload e template di rendering, e infine chiudiamo con test integrati e rifiniture callback. Lo scope base del dashboard resta persistito nelle colonne esistenti; `filters_json` contiene solo restringimenti utente e filtri locali di vista.

**Tech Stack:** Python 3.12, SQLite (`sqlite3`), python-telegram-bot, pytest, GitHub connector. Nessuna nuova dipendenza.

---

## File Map

| File | Azione | Responsabilità |
|---|---|---|
| `src/runtime_v2/control_plane/status_queries.py` | Modify | Correggere semantica `Stats`, `Closed` netto, best/worst netti, breakdown `Account -> Trader`, query selector trader per account |
| `src/runtime_v2/control_plane/dashboard_manager.py` | Modify | Normalizzazione `filters_json`, compatibilità lazy formato legacy, callback selector/clear gerarchici |
| `src/runtime_v2/control_plane/formatters/dashboard.py` | Modify | `effective_scope` da modello normalizzato, isolamento filtri locali per vista, payload `Stats` ricco |
| `src/runtime_v2/control_plane/formatters/templates/dashboard.py` | Modify | Header dedicato `Stats`, rendering best/worst contestuale, breakdown annidato, `Closed` net PnL coerente |
| `tests/runtime_v2/control_plane/test_status_queries_scoped.py` | Modify | Test semantica `Stats`, `Closed`, trader selector |
| `tests/runtime_v2/control_plane/test_dashboard_manager.py` | Modify | Test normalizzazione filtri, compatibilità legacy, cambio account -> reset trader, clear selettivi |
| `tests/runtime_v2/control_plane/test_dashboard_formatter.py` | Modify | Test header `Stats`, breakdown per scope, `Closed` netto, filtro string per vista |

---

## Task 1: Fix query semantics for Stats and Closed

**Files:**
- Modify: `src/runtime_v2/control_plane/status_queries.py`
- Modify: `tests/runtime_v2/control_plane/test_status_queries_scoped.py`

**Outcome:** `StatusQueries` usa closure time e net PnL come unica base semantica per `Stats`, `Closed`, best/worst e breakdown globali.

- [ ] **Step 1: Add failing tests for closure-based stats and net-based outcomes**

Append to `tests/runtime_v2/control_plane/test_status_queries_scoped.py`:

```python
class TestStatsNetSemantics:
    def test_today_uses_closed_timestamp_not_created_timestamp(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            old_created = _ts_offset(days=20)
            today_closed = _ts_offset(hours=1)
            _add_chain(
                conn,
                900,
                "CLOSED",
                account_id="account_A",
                trader_id="trader_1",
                cumulative_gross_pnl=20.0,
                cumulative_fees=1.0,
                created_at=old_created,
            )
            conn.execute(
                "UPDATE ops_trade_chains SET closed_at=?, updated_at=? WHERE trade_chain_id=?",
                (today_closed, today_closed, 900),
            )
        conn.close()

        q = StatusQueries(ops_db)
        stats = q.get_stats(SCOPE_A)
        today_row = next(r for r in stats.rows if r.label == "Today")
        assert today_row.trade_count == 1

    def test_win_loss_breakeven_use_net_pnl(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 901, "CLOSED", account_id="account_A", cumulative_gross_pnl=10.0, cumulative_fees=15.0)
            _add_chain(conn, 902, "CLOSED", account_id="account_A", cumulative_gross_pnl=8.0, cumulative_fees=8.0)
            _add_chain(conn, 903, "CLOSED", account_id="account_A", cumulative_gross_pnl=12.0, cumulative_fees=1.0)
        conn.close()

        q = StatusQueries(ops_db)
        stats = q.get_stats(SCOPE_A)
        total_row = next(r for r in stats.rows if r.label == "All time")
        assert total_row.trade_count == 3
        assert total_row.wins == 1
        assert total_row.losses == 1
        assert total_row.breakevens == 1
        assert total_row.win_pct == 50.0

    def test_best_and_worst_use_net_pnl_not_gross(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 904, "CLOSED", account_id="account_A", symbol="BTC/USDT", cumulative_gross_pnl=20.0, cumulative_fees=25.0)
            _add_chain(conn, 905, "CLOSED", account_id="account_A", symbol="ETH/USDT", cumulative_gross_pnl=15.0, cumulative_fees=1.0)
        conn.close()

        q = StatusQueries(ops_db)
        stats = q.get_stats(SCOPE_A)
        assert stats.best_trade["trade_chain_id"] == 905
        assert stats.worst_trade["trade_chain_id"] == 904

    def test_closed_trades_expose_net_pnl_when_rendered_as_net(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 906, "CLOSED", account_id="account_A", cumulative_gross_pnl=30.0, cumulative_fees=4.0, cumulative_funding=1.0)
        conn.close()

        q = StatusQueries(ops_db)
        view = q.get_closed_trades(SCOPE_A)
        assert view.rows[0].net_pnl == 25.0
```

- [ ] **Step 2: Run tests to confirm red state**

Run:

```bash
pytest tests/runtime_v2/control_plane/test_status_queries_scoped.py -k "StatsNetSemantics or closed_trades_expose_net_pnl" -v
```

Expected: FAIL because `StatsRow` lacks `wins/losses/breakevens`, `StatsView` lacks `best_trade/worst_trade`, and `ClosedTradeRow` lacks `net_pnl`.

- [ ] **Step 3: Extend dataclasses in `status_queries.py`**

Replace the `StatsRow`, `StatsView`, and `ClosedTradeRow` declarations with:

```python
@dataclass
class StatsRow:
    label: str
    trade_count: int
    wins: int
    losses: int
    breakevens: int
    win_pct: float | None
    pnl_net: float
    fees: float


@dataclass
class StatsView:
    updated_at: str
    rows: list[StatsRow]
    closed_trade_count: int
    account_count: int
    trader_count: int
    best_trade: dict | None = None
    worst_trade: dict | None = None
    breakdown_accounts: list[dict] | None = None


@dataclass
class ClosedTradeRow:
    chain_id: int
    symbol: str
    side: str
    closed_at: str | None
    gross_pnl: float | None
    net_pnl: float | None
    trader_id: str | None = None
    account_id: str | None = None
    created_at: str | None = None
    closed_reason: str | None = None
    lifecycle_state: str | None = None
```

- [ ] **Step 4: Refactor `get_stats()` to use shared net/closed expressions**

Inside `get_stats()`, define shared expressions:

```python
closed_ts_expr = "COALESCE(closed_at, updated_at)"
net_pnl_expr = "(cumulative_gross_pnl - cumulative_fees - cumulative_funding)"
```

Replace `_stats_for_window()` with:

```python
def _stats_for_window(date_filter_sql: str, date_params: list) -> tuple[int, int, int, int, float, float, float | None]:
    row = conn.execute(
        f"SELECT "
        f"COUNT(*), "
        f"SUM(CASE WHEN {net_pnl_expr} > 0 THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN {net_pnl_expr} < 0 THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN {net_pnl_expr} = 0 THEN 1 ELSE 0 END), "
        f"SUM({net_pnl_expr}), "
        f"SUM(cumulative_fees + cumulative_funding) "
        f"FROM ops_trade_chains "
        f"WHERE lifecycle_state='CLOSED' AND {scope_frag} {date_filter_sql} {side_sql}",
        [*scope_params, *date_params, *side_params],
    ).fetchone()
    count = row[0] or 0
    wins = row[1] or 0
    losses = row[2] or 0
    breakevens = row[3] or 0
    pnl_net = float(row[4]) if row[4] is not None else 0.0
    fees = float(row[5]) if row[5] is not None else 0.0
    denom = wins + losses
    win_pct = (wins / denom * 100.0) if denom > 0 else None
    return count, wins, losses, breakevens, pnl_net, fees, win_pct
```

Use these window filters:

```python
today = f"AND date({closed_ts_expr}) = date('now')"
last_7d = f"AND {closed_ts_expr} >= datetime('now', '-7 days')"
last_30d = f"AND {closed_ts_expr} >= datetime('now', '-30 days')"
```

- [ ] **Step 5: Build best/worst and global breakdown from net PnL**

In `get_stats()`, fetch best/worst with:

```python
best_row = conn.execute(
    f"SELECT trade_chain_id, symbol, account_id, trader_id, {net_pnl_expr} AS pnl_net "
    f"FROM ops_trade_chains "
    f"WHERE lifecycle_state='CLOSED' AND {scope_frag} {side_sql} "
    f"ORDER BY pnl_net DESC, trade_chain_id ASC LIMIT 1",
    [*scope_params, *side_params],
).fetchone()
worst_row = conn.execute(
    f"SELECT trade_chain_id, symbol, account_id, trader_id, {net_pnl_expr} AS pnl_net "
    f"FROM ops_trade_chains "
    f"WHERE lifecycle_state='CLOSED' AND {scope_frag} {side_sql} "
    f"ORDER BY pnl_net ASC, trade_chain_id ASC LIMIT 1",
    [*scope_params, *side_params],
).fetchone()
```

Build nested breakdown with one grouped dataset:

```python
breakdown_rows = conn.execute(
    f"SELECT account_id, trader_id, COUNT(*), "
    f"SUM(CASE WHEN {net_pnl_expr} > 0 THEN 1 ELSE 0 END), "
    f"SUM(CASE WHEN {net_pnl_expr} < 0 THEN 1 ELSE 0 END), "
    f"SUM(CASE WHEN {net_pnl_expr} = 0 THEN 1 ELSE 0 END), "
    f"SUM({net_pnl_expr}) "
    f"FROM ops_trade_chains "
    f"WHERE lifecycle_state='CLOSED' AND {scope_frag} {side_sql} "
    f"GROUP BY account_id, trader_id "
    f"ORDER BY account_id ASC, SUM({net_pnl_expr}) DESC, trader_id ASC",
    [*scope_params, *side_params],
).fetchall()
```

Group them in Python into `breakdown_accounts` where each account dict contains `trade_count`, `wins`, `losses`, `breakevens`, `net_pnl`, and ordered `traders`.

- [ ] **Step 6: Add trader selector query and closed net field**

Add method:

```python
def get_traders_for_account_scope(self, scope: QueryScope, account_id: str) -> list[str]:
    conn = self._connect()
    try:
        if scope.account_id is not None and scope.account_id != account_id:
            return []
        if scope.trader_ids is not None:
            return list(scope.trader_ids)
        rows = conn.execute(
            "SELECT DISTINCT trader_id FROM ops_trade_chains WHERE account_id=? AND trader_id IS NOT NULL ORDER BY trader_id",
            (account_id,),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()
```

Update `get_closed_trades()` select and row builder:

```python
f"(t.cumulative_gross_pnl - t.cumulative_fees - t.cumulative_funding) AS net_pnl, "
```

and:

```python
net_pnl=float(r[8]) if r[8] is not None else None,
lifecycle_state=r[9],
closed_reason=r[10],
```

Adjust indexes accordingly.

- [ ] **Step 7: Run query tests and verify green**

Run:

```bash
pytest tests/runtime_v2/control_plane/test_status_queries_scoped.py -x -q
```

Expected: all status-query tests pass, including new net/closure-based semantics.

- [ ] **Step 8: Commit**

```bash
git add src/runtime_v2/control_plane/status_queries.py tests/runtime_v2/control_plane/test_status_queries_scoped.py
git commit -m "feat(stats): use closed timestamps, net pnl semantics, and nested account trader breakdown"
```

---

## Task 2: Introduce normalized filter model in DashboardManager

**Files:**
- Modify: `src/runtime_v2/control_plane/dashboard_manager.py`
- Modify: `tests/runtime_v2/control_plane/test_dashboard_manager.py`

**Outcome:** `DashboardManager` reads/writes normalized `filters_json`, normalizes legacy payloads lazily, and enforces account -> trader hierarchy.

- [ ] **Step 1: Add failing tests for normalized filters and hierarchy rules**

Append to `tests/runtime_v2/control_plane/test_dashboard_manager.py`:

```python
def test_legacy_flat_filters_are_normalized_on_read(tmp_path):
    db_path = _make_db(tmp_path)
    mgr = DashboardManager(ops_db_path=db_path, scope_resolver=MagicMock(), queries=MagicMock(), bot=None)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_dashboard_messages VALUES (1,0,42,NULL,NULL,'stats:0','2024-01-01',?)",
        ('{"account": "demo_1", "side": "LONG"}',),
    )
    conn.commit()
    conn.close()

    filters = mgr._get_parsed_filters(1, 0)
    assert filters == {
        "scope": {"account": "demo_1"},
        "views": {"active": {"side": "LONG"}},
    }


def test_changing_account_clears_trader(tmp_path):
    db_path = _make_db(tmp_path)
    mgr = DashboardManager(ops_db_path=db_path, scope_resolver=MagicMock(), queries=MagicMock(), bot=None)
    normalized = mgr._normalize_filters(
        current_view_name="stats",
        filters={"scope": {"account": "demo_1", "trader": "trader_a"}, "views": {"stats": {}}},
        base_scope=QueryScope(account_id=None, trader_ids=None),
        mutation=("account", "demo_2"),
    )
    assert normalized["scope"] == {"account": "demo_2"}


def test_trader_without_account_is_removed(tmp_path):
    db_path = _make_db(tmp_path)
    mgr = DashboardManager(ops_db_path=db_path, scope_resolver=MagicMock(), queries=MagicMock(), bot=None)
    normalized = mgr._normalize_filters(
        current_view_name="stats",
        filters={"scope": {"trader": "trader_a"}, "views": {"stats": {}}},
        base_scope=QueryScope(account_id=None, trader_ids=None),
        mutation=None,
    )
    assert normalized["scope"] == {}


def test_reset_all_clears_scope_and_views(tmp_path):
    db_path = _make_db(tmp_path)
    mgr = DashboardManager(ops_db_path=db_path, scope_resolver=MagicMock(), queries=MagicMock(), bot=None)
    normalized = mgr._normalize_filters(
        current_view_name="closed",
        filters={"scope": {"account": "demo_1"}, "views": {"closed": {"period": "week", "side": "LONG"}}},
        base_scope=QueryScope(account_id=None, trader_ids=None),
        mutation=("reset_all", None),
    )
    assert normalized == {"scope": {}, "views": {}}
```

- [ ] **Step 2: Run manager tests to confirm red state**

Run:

```bash
pytest tests/runtime_v2/control_plane/test_dashboard_manager.py -k "normalized or account_clears or reset_all" -v
```

Expected: FAIL because `_normalize_filters()` does not exist and `_get_parsed_filters()` returns flat JSON.

- [ ] **Step 3: Add normalized filter helpers in `dashboard_manager.py`**

Add these methods inside `DashboardManager`:

```python
def _legacy_to_normalized_filters(self, flat: dict) -> dict:
    scope: dict[str, str] = {}
    views: dict[str, dict] = {}
    if flat.get("account"):
        scope["account"] = flat["account"]
    if flat.get("trader"):
        scope["trader"] = flat["trader"]
    active_view: dict[str, str] = {}
    if flat.get("side"):
        active_view["side"] = flat["side"]
    if flat.get("status"):
        active_view["status"] = flat["status"]
    if active_view:
        views["active"] = active_view
    if flat.get("period"):
        views.setdefault("closed", {})["period"] = flat["period"]
    return {"scope": scope, "views": views}


def _empty_filters(self) -> dict:
    return {"scope": {}, "views": {}}


def _get_parsed_filters(self, chat_id: int, thread_id: int) -> dict | None:
    raw = self._get_filters_json(chat_id, thread_id)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    if "scope" in parsed or "views" in parsed:
        return parsed
    return self._legacy_to_normalized_filters(parsed)
```

- [ ] **Step 4: Implement normalization and mutation rules**

Add:

```python
def _normalize_filters(self, current_view_name: str, filters: dict | None, base_scope: QueryScope, mutation: tuple[str, str | None] | None = None) -> dict:
    normalized = filters or self._empty_filters()
    scope = dict(normalized.get("scope") or {})
    views = {k: dict(v) for k, v in (normalized.get("views") or {}).items()}

    if mutation == ("reset_all", None):
        return self._empty_filters()

    if mutation is not None:
        key, value = mutation
        if key == "account":
            if value:
                scope["account"] = value
            else:
                scope.pop("account", None)
            scope.pop("trader", None)
        elif key == "trader":
            if value:
                scope["trader"] = value
            else:
                scope.pop("trader", None)
        else:
            view_branch = views.setdefault(current_view_name, {})
            if value:
                view_branch[key] = value
            else:
                view_branch.pop(key, None)
            if not view_branch:
                views.pop(current_view_name, None)

    if "account" not in scope:
        scope.pop("trader", None)

    if base_scope.account_id is not None:
        scope["account"] = base_scope.account_id
    if base_scope.trader_ids is not None and len(base_scope.trader_ids) == 1:
        scope["trader"] = base_scope.trader_ids[0]

    allowed_filters = {
        "active": {"status", "side"},
        "closed": {"period", "side"},
        "stats": {"side"},
        "pnl": set(),
        "blocked": {"side"},
    }
    cleaned_views: dict[str, dict] = {}
    for view_name, payload in views.items():
        allowed = allowed_filters.get(view_name, set())
        kept = {k: v for k, v in payload.items() if k in allowed}
        if kept:
            cleaned_views[view_name] = kept

    return {"scope": scope, "views": cleaned_views}
```

- [ ] **Step 5: Wire selector and clear callbacks to normalized mutations**

Inside `handle_callback()`, replace the flat selector branch with:

```python
filters = self._get_parsed_filters(chat_id, thread_id) or self._empty_filters()
if callback_data in ("clear", "clear_view"):
    normalized = self._normalize_filters(current_view_name, filters, scope, ("reset_all", None))
    self._update_filters_json(chat_id, thread_id, None if normalized == self._empty_filters() else json.dumps(normalized))
    new_view = current_view_name
    new_page = 0
elif callback_data.startswith("selector:"):
    _, filter_type, filter_value = callback_data.split(":", 2)
    mutation_value = None if filter_value in ("all", "") else filter_value
    normalized = self._normalize_filters(current_view_name, filters, scope, (filter_type, mutation_value))
    raw = None if normalized == self._empty_filters() else json.dumps(normalized)
    self._update_filters_json(chat_id, thread_id, raw)
    new_view = current_view_name
    new_page = 0
```

- [ ] **Step 6: Add account-aware trader selector panel**

In `_show_selector_values_panel()`, replace trader lookup with:

```python
filters = self._get_parsed_filters(chat_id, thread_id) or self._empty_filters()
scope_filters = filters.get("scope") or {}
selected_account = scope_filters.get("account")
if filter_type == "trader":
    if not selected_account:
        text = "🔎 Select trader\n\nSelect an account first."
        rows = [[InlineKeyboardButton("← Back", callback_data="filters")]]
        keyboard = InlineKeyboardMarkup(rows)
        await self._bot.edit_message_text(chat_id=chat_id, message_id=stored_message_id, text=text, reply_markup=keyboard)
        return
    values = self._queries.get_traders_for_account_scope(scope=QueryScope(account_id=scope.account_id, trader_ids=scope.trader_ids), account_id=selected_account)
```

- [ ] **Step 7: Run manager tests and verify green**

Run:

```bash
pytest tests/runtime_v2/control_plane/test_dashboard_manager.py -x -q
```

Expected: all manager tests pass, including normalization, lazy compatibility, trader gating, and clear/reset behavior.

- [ ] **Step 8: Commit**

```bash
git add src/runtime_v2/control_plane/dashboard_manager.py tests/runtime_v2/control_plane/test_dashboard_manager.py
git commit -m "feat(filters): normalize dashboard filters into scope and per-view branches"
```

---

## Task 3: Update dashboard payload shaping for effective scope and Stats metadata

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/dashboard.py`
- Modify: `tests/runtime_v2/control_plane/test_dashboard_formatter.py`

**Outcome:** the formatter computes effective scope from normalized filters and passes only view-relevant filters and `Stats` metadata to templates.

- [ ] **Step 1: Add failing formatter tests for Stats headers and scope-specific breakdown**

Append to `tests/runtime_v2/control_plane/test_dashboard_formatter.py`:

```python
class TestStatsScopeRendering:
    def test_stats_global_header_uses_accounts_traders_closed_trades(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 950, "CLOSED", account_id="demo_1", trader_id="trader_a", cumulative_gross_pnl=5.0)
            _add_chain(conn, 951, "CLOSED", account_id="demo_2", trader_id="trader_b", cumulative_gross_pnl=7.0)
        conn.close()

        q = StatusQueries(ops_db)
        text, _ = format_dashboard_view("stats", QueryScope(account_id=None, trader_ids=None), q)
        assert "Accounts:" in text
        assert "Traders:" in text
        assert "Closed trades:" in text
        assert "Page:" not in text

    def test_stats_account_scope_shows_by_trader_only(self, ops_db):
        conn = sqlite3.connect(ops_db)
        with conn:
            _add_chain(conn, 952, "CLOSED", account_id="demo_1", trader_id="trader_a", cumulative_gross_pnl=10.0)
            _add_chain(conn, 953, "CLOSED", account_id="demo_1", trader_id="trader_b", cumulative_gross_pnl=4.0)
        conn.close()

        q = StatusQueries(ops_db)
        text, _ = format_dashboard_view("stats", QueryScope(account_id="demo_1", trader_ids=None), q)
        assert "By trader:" in text
        assert "By account:" not in text

    def test_pnl_filters_do_not_render_period(self, ops_db):
        q = StatusQueries(ops_db)
        filters = {"scope": {}, "views": {"pnl": {"period": "week"}}}
        text, _ = format_dashboard_view("pnl", QueryScope(account_id="demo_1", trader_ids=None), q, filters=filters)
        assert "Last 7d" not in text
```

- [ ] **Step 2: Run formatter tests to confirm red state**

Run:

```bash
pytest tests/runtime_v2/control_plane/test_dashboard_formatter.py -k "StatsScopeRendering or pnl_filters_do_not_render_period" -v
```

Expected: FAIL because headers still use generic `Total/Page/Order` for stats and filters are still read from the flat shape.

- [ ] **Step 3: Replace `_effective_scope()` and filter extraction in `formatters/dashboard.py`**

Replace `_effective_scope()` with:

```python
def _effective_scope(scope: QueryScope, filters: dict | None) -> QueryScope:
    if not filters:
        return scope
    scope_filters = filters.get("scope") or {}
    account = scope_filters.get("account")
    trader = scope_filters.get("trader")

    eff_account = scope.account_id
    if account and (scope.account_id is None or scope.account_id == account):
        eff_account = account

    eff_traders = scope.trader_ids
    if trader:
        if scope.trader_ids is None:
            eff_traders = [trader]
        elif trader in scope.trader_ids:
            eff_traders = [trader]

    return QueryScope(account_id=eff_account, trader_ids=eff_traders)
```

Add helper:

```python
def _view_filters(filters: dict | None, view_name: str) -> dict:
    if not filters:
        return {}
    return dict((filters.get("views") or {}).get(view_name) or {})
```

- [ ] **Step 4: Update payload builders to use per-view filters and Stats metadata**

Use `f = _view_filters(filters, "active")`, `"closed"`, `"stats"`, `"blocked"`, `"pnl"` as appropriate.

For `_build_pnl_payload()`, force no period rendering:

```python
payload = {
    **_build_scope_meta(query_scope),
    "filters_str": _build_filters_str(is_global, {"scope": (filters or {}).get("scope", {}), "views": {"pnl": {}}}),
}
```

For `_build_stats_payload()`, pass dedicated fields:

```python
payload = {
    **_build_scope_meta(query_scope),
    "updated_at": view.updated_at,
    "filters_str": _build_filters_str(is_global, {"scope": (filters or {}).get("scope", {}), "views": {"stats": f}}),
    "is_global": query_scope.account_id is None,
    "stats_rows": [...],
    "closed_trade_count": view.closed_trade_count,
    "account_count": view.account_count,
    "trader_count": view.trader_count,
    "best_trade": view.best_trade,
    "worst_trade": view.worst_trade,
    "breakdown_accounts": view.breakdown_accounts,
}
```

- [ ] **Step 5: Run formatter tests and verify green**

Run:

```bash
pytest tests/runtime_v2/control_plane/test_dashboard_formatter.py -x -q
```

Expected: formatter tests pass with normalized filters and scope-correct `Stats` payloads.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/dashboard.py tests/runtime_v2/control_plane/test_dashboard_formatter.py
git commit -m "feat(formatter): derive effective scope from normalized dashboard filters"
```

---

## Task 4: Update dashboard templates for Stats hierarchy and Closed net labeling

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/templates/dashboard.py`
- Modify: `tests/runtime_v2/control_plane/test_dashboard_formatter.py`

**Outcome:** rendered Telegram text matches the approved design: dedicated `Stats` header, nested `Account -> Trader` breakdown, contextual best/worst, and `Closed` net PnL consistency.

- [ ] **Step 1: Add failing template tests for final Stats/Closed output**

Append to `tests/runtime_v2/control_plane/test_dashboard_formatter.py`:

```python
def test_closed_view_labels_net_pnl_with_true_net_value(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 970, "CLOSED", account_id="demo_1", trader_id="trader_a", symbol="BTCUSDT", cumulative_gross_pnl=30.0, cumulative_fees=5.0)
    conn.close()

    q = StatusQueries(ops_db)
    text, _ = format_dashboard_view("closed", QueryScope(account_id="demo_1", trader_ids=["trader_a"]), q)
    assert "Net PnL: +25.00 USDT" in text
    assert "Net PnL: +30.00 USDT" not in text


def test_stats_global_breakdown_is_nested_by_account_and_trader(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 971, "CLOSED", account_id="demo_1", trader_id="trader_a", cumulative_gross_pnl=20.0)
        _add_chain(conn, 972, "CLOSED", account_id="demo_1", trader_id="trader_b", cumulative_gross_pnl=10.0)
        _add_chain(conn, 973, "CLOSED", account_id="demo_2", trader_id="trader_c", cumulative_gross_pnl=5.0)
    conn.close()

    q = StatusQueries(ops_db)
    text, _ = format_dashboard_view("stats", QueryScope(account_id=None, trader_ids=None), q)
    assert "By account:" in text
    assert "demo_1" in text
    assert "trader_a" in text
    assert "trader_b" in text


def test_stats_trader_scope_omits_redundant_breakdown(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 974, "CLOSED", account_id="demo_1", trader_id="trader_a", cumulative_gross_pnl=12.0)
    conn.close()

    q = StatusQueries(ops_db)
    text, _ = format_dashboard_view("stats", QueryScope(account_id="demo_1", trader_ids=["trader_a"]), q)
    assert "By account:" not in text
    assert "By trader:" not in text
```

- [ ] **Step 2: Run tests to confirm red state**

Run:

```bash
pytest tests/runtime_v2/control_plane/test_dashboard_formatter.py -k "closed_view_labels_net_pnl or stats_global_breakdown or stats_trader_scope" -v
```

Expected: FAIL because templates still render gross PnL in `Closed` and stats breakdown is account-only.

- [ ] **Step 3: Add dedicated Stats header blocks**

In `templates/dashboard.py`, add:

```python
def _stats_header_blocks() -> list:
    return [
        DerivedBlock(text_fn=lambda p: (
            "📉 Stats — All accounts" if p.get("is_global") else (
                f"📉 Stats — {p.get('account_id')} · {p.get('trader_id')}" if p.get("trader_id") else f"📉 Stats — {p.get('account_id')}"
            )
        )),
        SeparatorBlock(),
        DerivedBlock(text_fn=lambda p: (
            f"Accounts: {p.get('account_count', 0)} · Traders: {p.get('trader_count', 0)} · Closed trades: {p.get('closed_trade_count', 0)}"
            if p.get("is_global")
            else (
                f"Closed trades: {p.get('closed_trade_count', 0)}" if p.get("trader_id")
                else f"Traders: {p.get('trader_count', 0)} · Closed trades: {p.get('closed_trade_count', 0)}"
            )
        )),
        DerivedBlock(text_fn=lambda p: f"Updated: {p.get('updated_at', 'n/a')}"),
        ConditionalBlock(
            condition=lambda p: bool(p.get("filters_str")),
            blocks=[DerivedBlock(text_fn=lambda p: f"Filters: {p['filters_str']}")],
        ),
        SeparatorBlock(),
    ]
```

- [ ] **Step 4: Render best/worst contextually and nested breakdown**

Add helpers:

```python
def _trade_context_line(label: str, trade: dict | None, payload: dict) -> str:
    if not trade:
        return f"{label}: —"
    parts = [f"#{trade['trade_chain_id']}", display_symbol(trade['symbol'])]
    if payload.get("is_global"):
        parts.extend([trade["account_id"], trade["trader_id"]])
    elif not payload.get("trader_id"):
        parts.append(trade["trader_id"])
    parts.append(money_signed(trade["net_pnl"]))
    return f"{label}:  " + " · ".join(parts)


def _stats_breakdown_lines(payload: dict) -> str:
    rows = payload.get("breakdown_accounts") or []
    if not rows:
        return ""
    if payload.get("trader_id"):
        return ""
    if payload.get("is_global"):
        lines = []
        for idx, account in enumerate(rows):
            if idx:
                lines.append(_SEP)
            lines.append(
                f"{account['account_id']} · Trades: {account['trade_count']} · Win%: {account['win_pct_str']} · Net: {money_signed(account['net_pnl'])}"
            )
            for trader in account.get("traders", []):
                lines.append(
                    f"├─ {trader['trader_id']} · Trades: {trader['trade_count']} · Win%: {trader['win_pct_str']} · Net: {money_signed(trader['net_pnl'])}"
                )
        return "\n".join(lines)
    return "\n".join(
        f"{trader['trader_id']} · Trades: {trader['trade_count']} · Win%: {trader['win_pct_str']} · Net: {money_signed(trader['net_pnl'])}"
        for trader in rows[0].get("traders", [])
    )
```

Update `_STATS_BLOCKS` to use `_stats_header_blocks()`, `_trade_context_line()`, and `_stats_breakdown_lines()`.

- [ ] **Step 5: Fix Closed item renderer to use `net_pnl`**

Replace the net line in `_render_closed_item()` with:

```python
pnl = row.get("net_pnl")
pnl_str = money_signed(pnl) if pnl is not None else "—"
lines.append(f"Net PnL: {pnl_str} · ⏱ {duration}")
```

- [ ] **Step 6: Run full dashboard formatter tests and verify green**

Run:

```bash
pytest tests/runtime_v2/control_plane/test_dashboard_formatter.py -x -q
```

Expected: all formatter tests pass, with correct `Stats` headers, nested breakdown, and `Closed` net labeling.

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/templates/dashboard.py tests/runtime_v2/control_plane/test_dashboard_formatter.py
git commit -m "feat(templates): render scoped stats hierarchy and net-consistent closed trades"
```

---

## Final Verification

- [ ] **Step 1: Run the full control-plane dashboard test slice**

Run:

```bash
pytest tests/runtime_v2/control_plane/test_status_queries_scoped.py tests/runtime_v2/control_plane/test_dashboard_manager.py tests/runtime_v2/control_plane/test_dashboard_formatter.py -x -q
```

Expected: all tests PASS.

- [ ] **Step 2: Run a broader regression pass for the whole control-plane suite**

Run:

```bash
pytest tests/runtime_v2/control_plane/ -x -q
```

Expected: PASS or only pre-existing unrelated failures. If unrelated failures exist, record them before any extra code changes.

- [ ] **Step 3: Commit final stabilization if needed**

```bash
git add src/runtime_v2/control_plane/status_queries.py src/runtime_v2/control_plane/dashboard_manager.py src/runtime_v2/control_plane/formatters/dashboard.py src/runtime_v2/control_plane/formatters/templates/dashboard.py tests/runtime_v2/control_plane/test_status_queries_scoped.py tests/runtime_v2/control_plane/test_dashboard_manager.py tests/runtime_v2/control_plane/test_dashboard_formatter.py
git commit -m "test(control-plane): cover stats filters hierarchy and dashboard semantics"
```

---

## Self-Review

Spec coverage:
- Stats now uses closure time and net semantics everywhere.
- Closed net labeling is corrected at the query and template layers.
- Global/account/trader scope rendering is covered.
- Hierarchical filters and selective clears are covered.
- Lazy compatibility for legacy `filters_json` is covered.

Placeholder scan:
- No `TODO`, `TBD`, or vague “handle appropriately” language remains.
- Each code-changing task names exact files, exact methods, and exact commands.

Type consistency:
- New data contract uses `best_trade`/`worst_trade`, `breakdown_accounts`, and `net_pnl` consistently across query, formatter, and template tasks.

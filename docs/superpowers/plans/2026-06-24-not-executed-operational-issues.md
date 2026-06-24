# Not Executed And Operational Issues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the semantic meaning of dashboard `Blocked` with `Not executed`, add `Operational issues`, and centralize the classification logic in control-plane query code without introducing a new persistence table.

**Architecture:** Keep V1 on top of existing `ops_trade_chains`, `ops_lifecycle_events`, and `ops_execution_commands`, but stop deriving the dashboard meaning inside formatters. Add explicit query/view models for `Not executed` and `Operational issues`, route dashboard manager and formatter layers to those views, and verify the business rules with targeted tests before changing rendering.

**Tech Stack:** Python 3.12, SQLite, pytest, runtime_v2 control-plane query/formatter stack

---

## File map

- Modify: `src/runtime_v2/control_plane/status_queries.py`
  Add explicit row/view dataclasses, evidence helpers, and query methods for `not_executed` and `operational_issues`.
- Modify: `src/runtime_v2/control_plane/service.py`
  Expose read methods for the new views.
- Modify: `src/runtime_v2/control_plane/formatters/dashboard.py`
  Replace `blocked` payload construction with `not_executed` payload logic and add `operational_issues` payload logic.
- Modify: `src/runtime_v2/control_plane/formatters/templates/dashboard.py`
  Replace blocked template rendering, add `Operational issues` template, update empty states and labels.
- Modify: `src/runtime_v2/control_plane/dashboard_manager.py`
  Add tab labels, alias routing, and filter panel/static filter values for the new views.
- Modify: `tests/runtime_v2/control_plane/test_status_queries.py`
  Add focused query coverage for business classification.
- Modify: `tests/runtime_v2/control_plane/test_dashboard_formatter.py`
  Add rendering coverage for `Not executed` and `Operational issues`.
- Modify: `tests/runtime_v2/control_plane/test_dashboard_manager.py`
  Add tab routing and compatibility coverage for legacy `blocked` callbacks.

### Task 1: Add failing query tests for classification

**Files:**
- Modify: `tests/runtime_v2/control_plane/test_status_queries.py`
- Test: `tests/runtime_v2/control_plane/test_status_queries.py`

- [ ] **Step 1: Add helper fixtures for signal-only and post-entry cases**

```python
def _add_signal_only_event(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    event_type: str,
    reason: str,
    account_id: str | None = None,
    trader_id: str | None = None,
    symbol: str | None = None,
    side: str | None = None,
) -> None:
    import json

    payload = {
        "reason": reason,
        "account_id": account_id,
        "trader_id": trader_id,
        "symbol": symbol,
        "side": side,
    }
    conn.execute(
        "INSERT INTO ops_lifecycle_events "
        "(trade_chain_id, event_type, source_type, source_id, payload_json, idempotency_key, created_at) "
        "VALUES (NULL,?,?,?,?,?,?)",
        (
            event_type,
            "test",
            source_id,
            json.dumps(payload),
            f"signal_only_{source_id}_{event_type}",
            _now(),
        ),
    )


def _add_command(
    conn: sqlite3.Connection,
    *,
    chain_id: int,
    command_type: str,
    status: str,
    payload: str = "{}",
) -> None:
    conn.execute(
        "INSERT INTO ops_execution_commands "
        "(trade_chain_id, command_type, status, idempotency_key, payload_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            chain_id,
            command_type,
            status,
            f'{chain_id}:{command_type}:{status}:{_now()}',
            payload,
            _now(),
            _now(),
        ),
    )
```

- [ ] **Step 2: Add failing test for signal-only `SIGNAL_REJECTED` in `Not executed`**

```python
def test_get_not_executed_includes_signal_rejected_without_chain(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_signal_only_event(
            conn,
            source_id=104,
            event_type="SIGNAL_REJECTED",
            reason="risk_limit_exceeded",
            account_id="demo_1",
            trader_id="trader_a",
            symbol="ETH/USDT",
            side="LONG",
        )
    conn.close()

    rows = StatusQueries(ops_db).get_not_executed_trades(QueryScope(account_id=None, trader_ids=None)).rows

    assert len(rows) == 1
    assert rows[0].reference == "#S-104"
    assert rows[0].outcome == "REJECTED"
    assert rows[0].phase == "Risk"
```

- [ ] **Step 3: Add failing test for final entry failure with no ACK/fill**

```python
def test_get_not_executed_includes_failed_entry_without_ack_or_fill(ops_db):
    import json

    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 22, "CANCELLED", symbol="SOL/USDT", side="SHORT", account_id="demo_2")
        _add_command(
            conn,
            chain_id=22,
            command_type="PLACE_ENTRY",
            status="FAILED",
            payload=json.dumps({"reason": "insufficient_margin"}),
        )
    conn.close()

    rows = StatusQueries(ops_db).get_not_executed_trades(QueryScope(account_id=None, trader_ids=None)).rows

    assert any(r.trade_chain_id == 22 and r.outcome == "NOT_EXECUTED" for r in rows)
```

- [ ] **Step 4: Add failing test for ACKed waiting entry exclusion**

```python
def test_get_not_executed_excludes_acknowledged_waiting_entry(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 30, "WAITING_ENTRY")
        _add_command(conn, chain_id=30, command_type="PLACE_ENTRY", status="ACK")
    conn.close()

    rows = StatusQueries(ops_db).get_not_executed_trades(QueryScope(account_id=None, trader_ids=None)).rows

    assert all(r.trade_chain_id != 30 for r in rows)
```

- [ ] **Step 5: Add failing test for post-entry issue routing**

```python
def test_get_operational_issues_includes_post_entry_move_stop_failure(ops_db):
    import json

    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 44, "OPEN", symbol="BTC/USDT", side="LONG")
        conn.execute(
            "UPDATE ops_trade_chains SET filled_entry_qty=1.0, open_position_qty=1.0 WHERE trade_chain_id=44"
        )
        _add_command(conn, chain_id=44, command_type="PLACE_ENTRY", status="ACK")
        _add_command(
            conn,
            chain_id=44,
            command_type="MOVE_STOP",
            status="FAILED",
            payload=json.dumps({"reason": "exchange_rejected"}),
        )
    conn.close()

    rows = StatusQueries(ops_db).get_operational_issues(QueryScope(account_id=None, trader_ids=None)).rows

    assert any(r.trade_chain_id == 44 and r.command_type == "MOVE_STOP" for r in rows)
```

- [ ] **Step 6: Add failing test for pre-entry review vs post-entry review split**

```python
def test_review_required_is_split_between_views_by_entry_evidence(ops_db):
    import json

    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 50, "REVIEW_REQUIRED")
        conn.execute(
            "INSERT INTO ops_lifecycle_events "
            "(trade_chain_id, event_type, source_type, payload_json, idempotency_key, created_at) "
            "VALUES (50,'REVIEW_REQUIRED','test',?, 'rr_pre', ?)",
            (json.dumps({"reason": "manual_review_required"}), _now()),
        )
        _add_chain(conn, 51, "OPEN")
        conn.execute(
            "UPDATE ops_trade_chains SET filled_entry_qty=1.0, open_position_qty=1.0 WHERE trade_chain_id=51"
        )
        _add_command(conn, chain_id=51, command_type="PLACE_ENTRY", status="ACK")
        conn.execute(
            "INSERT INTO ops_lifecycle_events "
            "(trade_chain_id, event_type, source_type, payload_json, idempotency_key, created_at) "
            "VALUES (51,'REVIEW_REQUIRED','test',?, 'rr_post', ?)",
            (json.dumps({"reason": "missing_protective_stop"}), _now()),
        )
    conn.close()

    q = StatusQueries(ops_db)
    not_executed = q.get_not_executed_trades(QueryScope(account_id=None, trader_ids=None)).rows
    operational = q.get_operational_issues(QueryScope(account_id=None, trader_ids=None)).rows

    assert any(r.trade_chain_id == 50 for r in not_executed)
    assert all(r.trade_chain_id != 51 for r in not_executed)
    assert any(r.trade_chain_id == 51 for r in operational)
```

- [ ] **Step 7: Run the focused query tests to verify they fail**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_status_queries.py -q
```

Expected: FAIL with missing methods like `get_not_executed_trades`, missing row types, or old blocked behavior still active.

- [ ] **Step 8: Commit**

```bash
git add tests/runtime_v2/control_plane/test_status_queries.py
git commit -m "test: define not-executed and operational-issues query behavior"
```

### Task 2: Implement query models and classification logic

**Files:**
- Modify: `src/runtime_v2/control_plane/status_queries.py`
- Modify: `src/runtime_v2/control_plane/service.py`
- Test: `tests/runtime_v2/control_plane/test_status_queries.py`

- [ ] **Step 1: Replace blocked view dataclasses with explicit row/view models**

```python
@dataclass
class NotExecutedRow:
    reference: str
    trade_chain_id: int | None
    signal_reference: int | None
    account_id: str | None
    trader_id: str | None
    symbol: str | None
    side: str | None
    outcome: str
    phase: str
    reason: str | None
    command_type: str | None
    occurred_at: str
    details_command: str


@dataclass
class NotExecutedView:
    updated_at: str
    rows: list[NotExecutedRow]


@dataclass
class OperationalIssueRow:
    trade_chain_id: int
    account_id: str | None
    trader_id: str | None
    symbol: str | None
    side: str | None
    issue_type: str
    phase: str
    reason: str | None
    command_type: str | None
    occurred_at: str
    details_command: str


@dataclass
class OperationalIssuesView:
    updated_at: str
    rows: list[OperationalIssueRow]
```

- [ ] **Step 2: Add small evidence helpers near the query layer**

```python
_ENTRY_COMMAND_TYPES = {"PLACE_ENTRY", "PLACE_ENTRY_WITH_ATTACHED_TPSL"}
_ENTRY_ACK_STATUSES = {"ACK", "WAITING_POSITION", "DONE"}


def _normalize_reason(value: str | None) -> str:
    return value or "unavailable"


def _extract_reason(blob: str | None) -> str | None:
    if not blob:
        return None
    try:
        return json.loads(blob).get("reason") or json.loads(blob).get("error")
    except Exception:
        return None
```

- [ ] **Step 3: Implement `get_not_executed_trades(...)` with signal-only union and entry-failure suppression**

```python
def get_not_executed_trades(
    self,
    scope: QueryScope,
    *,
    side: str | None = None,
    outcome: str | None = None,
    phase: str | None = None,
) -> NotExecutedView:
    # 1. collect signal-only SIGNAL_REJECTED and pre-entry REVIEW_REQUIRED rows
    # 2. collect chain-backed entry failure rows from ops_execution_commands
    # 3. suppress chains with acknowledged entry evidence, positive fill, or open position
    # 4. dedupe by signal_reference or trade_chain_id
    # 5. apply scope/filter narrowing
    # 6. sort by occurred_at DESC
    ...
```

- [ ] **Step 4: Implement `get_operational_issues(...)` with post-entry evidence gating**

```python
def get_operational_issues(
    self,
    scope: QueryScope,
    *,
    side: str | None = None,
    issue_type: str | None = None,
    phase: str | None = None,
) -> OperationalIssuesView:
    # only chain-backed rows
    # require entry ACK/fill/open-position evidence
    # include failed/review-required post-entry operations
    # dedupe by chain_id to latest relevant issue
    ...
```

- [ ] **Step 5: Expose the new methods through `RuntimeControlService`**

```python
from src.runtime_v2.control_plane.status_queries import (
    ClosedTradesView,
    ControlView,
    HealthView,
    NotExecutedView,
    OperationalIssuesView,
    PnlView,
    ReviewsView,
    StatsView,
    StatusQueries,
    StatusView,
    TradeDetail,
    TradesView,
)


def get_not_executed_trades(self, scope: QueryScope) -> NotExecutedView:
    return self._queries.get_not_executed_trades(scope)


def get_operational_issues(self, scope: QueryScope) -> OperationalIssuesView:
    return self._queries.get_operational_issues(scope)
```

- [ ] **Step 6: Run the focused query tests to verify they pass**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_status_queries.py -q
```

Expected: PASS for the new not-executed and operational-issues cases, with no regression in unrelated status query tests.

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/control_plane/status_queries.py src/runtime_v2/control_plane/service.py tests/runtime_v2/control_plane/test_status_queries.py
git commit -m "feat: classify not-executed and operational issues in queries"
```

### Task 3: Add failing formatter tests for the new dashboard views

**Files:**
- Modify: `tests/runtime_v2/control_plane/test_dashboard_formatter.py`
- Test: `tests/runtime_v2/control_plane/test_dashboard_formatter.py`

- [ ] **Step 1: Add failing formatter test for `Not executed` signal-only rendering**

```python
def test_not_executed_renders_signal_only_reference_and_reason(ops_db):
    import json

    conn = sqlite3.connect(ops_db)
    with conn:
        conn.execute(
            "INSERT INTO ops_lifecycle_events "
            "(trade_chain_id, event_type, source_type, source_id, payload_json, idempotency_key, created_at) "
            "VALUES (NULL,'SIGNAL_REJECTED','test',104,?, 'signal_only_fmt', ?)",
            (
                json.dumps({
                    "reason": "risk_limit_exceeded",
                    "account_id": "demo_1",
                    "trader_id": "trader_a",
                    "symbol": "ETH/USDT",
                    "side": "LONG",
                }),
                _now(),
            ),
        )
    conn.close()

    text, total = format_dashboard_view("not_executed", QueryScope(account_id=None, trader_ids=None), StatusQueries(ops_db))

    assert "#S-104" in text
    assert "REJECTED" in text
    assert "Reason: risk_limit_exceeded" in text
    assert "At:" in text
    assert total == 1
```

- [ ] **Step 2: Add failing formatter test for `Operational issues`**

```python
def test_operational_issues_renders_trade_reference_and_command(ops_db):
    import json

    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 22, "OPEN", symbol="SOLUSDT", side="SHORT")
        conn.execute(
            "UPDATE ops_trade_chains SET filled_entry_qty=1.0, open_position_qty=1.0 WHERE trade_chain_id=22"
        )
        conn.execute(
            "INSERT INTO ops_execution_commands "
            "(trade_chain_id, command_type, status, idempotency_key, payload_json, created_at, updated_at) "
            "VALUES (22,'MOVE_STOP','FAILED','fmt_move_stop',?,?,?)",
            (json.dumps({"reason": "exchange_rejected"}), _now(), _now()),
        )
    conn.close()

    text, total = format_dashboard_view("operational_issues", SCOPE_ACCOUNT, StatusQueries(ops_db))

    assert "#22" in text
    assert "MOVE_STOP" in text
    assert "Operational issues" in text
    assert total == 1
```

- [ ] **Step 3: Add failing compatibility test for legacy `blocked` alias**

```python
def test_blocked_alias_routes_to_not_executed_view(ops_db):
    import json

    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 33, "CANCELLED")
        conn.execute(
            "INSERT INTO ops_execution_commands "
            "(trade_chain_id, command_type, status, idempotency_key, payload_json, created_at, updated_at) "
            "VALUES (33,'PLACE_ENTRY','FAILED','alias_blocked',?,?,?)",
            (json.dumps({"reason": "insufficient_margin"}), _now(), _now()),
        )
    conn.close()

    text_blocked, _ = format_dashboard_view("blocked", SCOPE_ACCOUNT, StatusQueries(ops_db))
    text_not_executed, _ = format_dashboard_view("not_executed", SCOPE_ACCOUNT, StatusQueries(ops_db))

    assert text_blocked == text_not_executed
```

- [ ] **Step 4: Run formatter tests to verify they fail**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_dashboard_formatter.py -q
```

Expected: FAIL because the formatter does not yet know `not_executed` or `operational_issues`, and still renders `Blocked`.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime_v2/control_plane/test_dashboard_formatter.py
git commit -m "test: define dashboard rendering for new failure views"
```

### Task 4: Implement dashboard payloads and templates

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/dashboard.py`
- Modify: `src/runtime_v2/control_plane/formatters/templates/dashboard.py`
- Test: `tests/runtime_v2/control_plane/test_dashboard_formatter.py`

- [ ] **Step 1: Add payload builders for `not_executed` and `operational_issues`**

```python
def _build_not_executed_payload(
    scope: QueryScope,
    queries: StatusQueries,
    page: int = 0,
    page_size: int = 5,
    filters: dict | None = None,
) -> tuple[dict, int]:
    ...


def _build_operational_issues_payload(
    scope: QueryScope,
    queries: StatusQueries,
    page: int = 0,
    page_size: int = 5,
    filters: dict | None = None,
) -> tuple[dict, int]:
    ...
```

- [ ] **Step 2: Update view normalization and dispatch**

```python
_name_map = {
    "attivi": "active",
    "chiusi": "closed",
    "bloccati": "not_executed",
    "blocked": "not_executed",
}

...
elif normalized == "not_executed":
    payload, total = _build_not_executed_payload(scope, queries, page, page_size, filters)
elif normalized == "operational_issues":
    payload, total = _build_operational_issues_payload(scope, queries, page, page_size, filters)
```

- [ ] **Step 3: Replace blocked keyboard tab and add operational tab**

```python
row1 = [
    _tab("⚡ Active", "active"),
    _tab("✅ Closed", "closed"),
    _tab("🚫 Not executed", "not_executed"),
]
row2 = [
    _tab("⚠️ Issues", "operational_issues"),
    _tab("📊 PnL", "pnl"),
    _tab("📈 Stats", "stats"),
]
row3 = [
    InlineKeyboardButton("🔄 Refresh", callback_data="refresh"),
    InlineKeyboardButton("🔎 Filters", callback_data="filters"),
    InlineKeyboardButton("🧹 Clear", callback_data="clear"),
]
```

- [ ] **Step 4: Add template renderers for both views**

```python
def _render_not_executed_item(row: dict, i: int, p: dict) -> list[str]:
    ...


def _render_operational_issue_item(row: dict, i: int, p: dict) -> list[str]:
    ...


TEMPLATE_DASHBOARD_NOT_EXECUTED = TemplateConfig(_NOT_EXECUTED_BLOCKS, payload_transform=None)
TEMPLATE_DASHBOARD_OPERATIONAL_ISSUES = TemplateConfig(_OPERATIONAL_ISSUES_BLOCKS, payload_transform=None)
```

- [ ] **Step 5: Register the new templates and remove blocked-specific wording**

```python
TEMPLATE_REGISTRY = {
    ...
    "dashboard_not_executed": TEMPLATE_DASHBOARD_NOT_EXECUTED,
    "dashboard_operational_issues": TEMPLATE_DASHBOARD_OPERATIONAL_ISSUES,
}
```

- [ ] **Step 6: Run formatter tests to verify they pass**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_dashboard_formatter.py -q
```

Expected: PASS with `Not executed` / `Operational issues` headers, `At:` labels, and alias compatibility.

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/dashboard.py src/runtime_v2/control_plane/formatters/templates/dashboard.py tests/runtime_v2/control_plane/test_dashboard_formatter.py
git commit -m "feat: render not-executed and operational-issues dashboard views"
```

### Task 5: Add failing dashboard manager tests for view routing and filters

**Files:**
- Modify: `tests/runtime_v2/control_plane/test_dashboard_manager.py`
- Test: `tests/runtime_v2/control_plane/test_dashboard_manager.py`

- [ ] **Step 1: Update the queries mock to include the new view methods**

```python
from src.runtime_v2.control_plane.status_queries import (
    ClosedTradesView,
    NotExecutedView,
    OperationalIssuesView,
    TradesView,
)

queries.get_not_executed_trades.return_value = NotExecutedView(updated_at="12:00:00", rows=[])
queries.get_operational_issues.return_value = OperationalIssuesView(updated_at="12:00:00", rows=[])
```

- [ ] **Step 2: Add failing test for legacy callback alias to `not_executed`**

```python
async def test_handle_blocked_alias_uses_not_executed_view(tmp_path):
    bot = _make_mock_bot()
    queries = _make_queries_mock()
    manager = _make_manager(tmp_path, bot=bot, queries=queries)

    rendered: list[str] = []

    def patched(scope, view, page, filters=None):
        rendered.append(view)
        return ("[ok]", MagicMock())

    manager._render_view = patched  # type: ignore[method-assign]

    query = MagicMock()
    query.data = "view:blocked"
    query.answer = AsyncMock()

    await manager.handle_callback(callback_query=query, chat_id=1, thread_id=0)

    assert rendered[-1] == "not_executed"
```

- [ ] **Step 3: Add failing test for `Operational issues` filter panel**

```python
async def test_filters_panel_supports_operational_issues(tmp_path):
    bot = _make_mock_bot()
    manager = _make_manager(tmp_path, bot=bot)

    query = MagicMock()
    query.answer = AsyncMock()

    await manager._show_filters_panel(
        callback_query=query,
        chat_id=1,
        thread_id=0,
        stored_message_id=42,
        current_view_name="operational_issues",
        scope=_make_scope(),
    )

    assert bot.edit_message_text.call_count == 1
```

- [ ] **Step 4: Run dashboard manager tests to verify they fail**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_dashboard_manager.py -q
```

Expected: FAIL because `blocked` is still canonical and the new view/filter path does not exist yet.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime_v2/control_plane/test_dashboard_manager.py
git commit -m "test: define dashboard-manager routing for new failure tabs"
```

### Task 6: Implement dashboard manager routing and filter behavior

**Files:**
- Modify: `src/runtime_v2/control_plane/dashboard_manager.py`
- Test: `tests/runtime_v2/control_plane/test_dashboard_manager.py`

- [ ] **Step 1: Rename view labels and accepted filter-bearing views**

```python
view_labels = {
    "active": "Active",
    "closed": "Closed",
    "not_executed": "Not executed",
    "operational_issues": "Operational issues",
    "pnl": "PnL",
    "stats": "Stats",
}

if current_view_name in ("active", "closed", "not_executed", "operational_issues", "pnl", "stats"):
    ...
```

- [ ] **Step 2: Add selector panel values for the new view-local filters**

```python
static_values: dict[str, list[str]] = {
    "status": ["OPEN", "PARTIALLY_CLOSED", "WAITING_ENTRY", "REVIEW_REQUIRED"],
    "side": ["LONG", "SHORT"],
    "period": ["today", "week", "month"],
    "not_executed_outcome": ["Rejected", "Entry not executed"],
    "not_executed_phase": ["Validation", "Policy", "Risk", "Manual review", "Entry submission"],
    "issue_type": ["Review required", "Command failed"],
    "issue_phase": ["Protection", "Breakeven", "Take profit", "Close", "Sync", "Entry cancel"],
}
```

- [ ] **Step 3: Normalize legacy blocked callbacks to `not_executed` before render/save**

```python
def _normalize_dashboard_view(view: str) -> str:
    return {
        "blocked": "not_executed",
        "bloccati": "not_executed",
    }.get(view, view)
```

- [ ] **Step 4: Apply normalization where current view is parsed from callback data or DB state**

```python
view = _normalize_dashboard_view(raw_view)
```

- [ ] **Step 5: Run dashboard manager tests to verify they pass**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_dashboard_manager.py -q
```

Expected: PASS for legacy alias routing, new view labels, and filter panel support.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/dashboard_manager.py tests/runtime_v2/control_plane/test_dashboard_manager.py
git commit -m "feat: route dashboard manager to not-executed and issues views"
```

### Task 7: Run focused regression suite and document follow-up

**Files:**
- Modify: `docs/AUDIT.md` if the team keeps implementation notes there during execution
- Test: `tests/runtime_v2/control_plane/test_status_queries.py`
- Test: `tests/runtime_v2/control_plane/test_dashboard_formatter.py`
- Test: `tests/runtime_v2/control_plane/test_dashboard_manager.py`

- [ ] **Step 1: Run the focused control-plane regression suite**

Run:

```bash
.venv\Scripts\python.exe -m pytest \
  tests/runtime_v2/control_plane/test_status_queries.py \
  tests/runtime_v2/control_plane/test_dashboard_formatter.py \
  tests/runtime_v2/control_plane/test_dashboard_manager.py \
  -q
```

Expected: PASS across query classification, formatter rendering, and manager routing/filter tests.

- [ ] **Step 2: Spot-check for stale `Blocked` references that should remain only as aliases**

Run:

```bash
rg -n "Blocked|blocked|bloccati" src/runtime_v2/control_plane tests/runtime_v2/control_plane
```

Expected: only compatibility aliases, test names, or deliberate comments remain; no canonical blocked business view remains.

- [ ] **Step 3: Update durable notes only if execution reveals important runtime caveats**

```markdown
- `Not executed` in V1 is query-derived from lifecycle + execution tables.
- Follow-up candidate: persist a dedicated outcome projection if historical edge cases remain hard to classify safely.
```

- [ ] **Step 4: Commit**

```bash
git add docs/AUDIT.md src/runtime_v2/control_plane tests/runtime_v2/control_plane
git commit -m "test: verify dashboard failure-view migration end to end"
```

## Self-review

### Spec coverage

- `Not executed` semantics: covered by Tasks 1-4.
- `Operational issues` semantics: covered by Tasks 1-4 and Task 6 for UI routing.
- Legacy `blocked` alias migration: covered by Tasks 3 and 6.
- Filter behavior: covered by Tasks 5 and 6.
- Anti-duplication and post-entry suppression: covered by Task 1 query tests and Task 2 implementation.
- Rendering labels and empty states: covered by Tasks 3 and 4.

### Placeholder scan

- No `TODO`, `TBD`, or “similar to previous task” placeholders remain.
- Each test task includes concrete code and exact commands.
- Each implementation task names exact files and target methods.

### Type consistency

- Canonical view names used throughout the plan: `not_executed`, `operational_issues`.
- Query/service/formatter naming is aligned to `get_not_executed_trades` and `get_operational_issues`.
- Row/view types are consistent across query, service, and test tasks.

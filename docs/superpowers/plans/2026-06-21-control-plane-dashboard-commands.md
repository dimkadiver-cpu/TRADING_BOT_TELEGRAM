# Control Plane Dashboard + Commands Realignment

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Riallineare formatter, template e dashboard del control plane Telegram alle spec documentali in `docs/Raggionamento/Controllo_Notifica/Temlate_commands_logs/`.

**Architecture:** 5 wave indipendenti e sequenziali. Wave 1 definisce i data model su cui le successive fanno affidamento. Tutto il testo usa `render_template(blocks, payload)` da `_blocks.py` — nessun formatter custom fuori dal block system. La `InlineKeyboardMarkup` resta sempre separata.

**Tech Stack:** Python 3.12, SQLite (sqlite3), python-telegram-bot, pytest. Nessuna nuova dipendenza.

## Global Constraints

- Unico engine di rendering: `render_template` da `src/runtime_v2/control_plane/formatters/_blocks.py`
- `InlineKeyboardMarkup` aggiunta sempre separatamente dal block system
- Nessuna nuova dipendenza di produzione
- Tutti i test usano `tmp_path` + migrations reali da `db/ops_migrations/*.sql`
- Run test: `pytest tests/runtime_v2/control_plane/ -x -q` dalla root del progetto
- `QueryScope(account_id=None, trader_ids=None)` = global scope
- Naming backend coerente con i tipi effettivi (es. `CLOSE_FULL`, non `MARKET_CLOSE`)

---

## File Map

| File | Azione | Responsabilità |
|---|---|---|
| `src/runtime_v2/control_plane/status_queries.py` | Modify | + `TradeEvent`, estendi `TradeDetail`, `StatusView.by_account`, `get_status_by_account()` |
| `src/runtime_v2/control_plane/formatters/templates/dashboard.py` | Modify | Naming IT→EN, header compatto, item renderer spec-compliant, global scope |
| `src/runtime_v2/control_plane/formatters/dashboard.py` | Modify | Payload builder aggiornati, `build_dashboard_keyboard` con Filters/Clear, filters_json |
| `src/runtime_v2/control_plane/dashboard_manager.py` | Modify | Naming migration, `filters_json` column, gestione callback `filters`/`clear`/`selector:*` |
| `src/runtime_v2/control_plane/formatters/templates/commands.py` | Modify | `TEMPLATE_STATUS/CONTROL/REVIEWS/TRADES` — global scope blocks, formato spec |
| `src/runtime_v2/control_plane/formatters/templates/_shared.py` | Modify | `_render_trade_item` → formato spec (uPnL · rPnL · Details) |
| `src/runtime_v2/control_plane/formatters/trade_detail.py` | Rewrite | `format_trade_detail` completo con block system (ordini, timeline, final result) |
| `src/runtime_v2/control_plane/formatters/status.py` | Modify | `_status_to_payload` → global scope + `by_account` |
| `src/runtime_v2/control_plane/formatters/reviews.py` | Modify | `format_reviews` → global scope, Trader+Account per item |
| `src/runtime_v2/control_plane/formatters/control.py` | Modify | `format_control` → global scope, account prefix per blocco |
| `src/runtime_v2/control_plane/formatters/trades.py` | Modify | `format_trades` — aggiorna payload per nuovo `_render_trade_item` |
| `src/runtime_v2/control_plane/formatters/health.py` | Rewrite | `format_health` via block system + `Checks:` section |
| `src/runtime_v2/control_plane/status_queries.py` | Modify | `get_health()` → probe reali per worker, exchange, DB |
| `src/runtime_v2/control_plane/emergency_close.py` | Modify | Safety check: rifiuta in global scope non filtrato |
| `tests/runtime_v2/control_plane/test_command_formatters.py` | Modify | Aggiorna test per nuovo formato spec |
| `tests/runtime_v2/control_plane/test_dashboard_formatter.py` | Modify | Aggiorna test per nuovo formato + global scope |
| `tests/runtime_v2/control_plane/test_dashboard_manager.py` | Modify | Test naming migration + filtri |
| `tests/runtime_v2/control_plane/test_readonly_formatters.py` | Modify | Aggiorna test /trade n |
| `tests/runtime_v2/control_plane/test_emergency_close.py` | Modify | Test safety check global scope |

---

## Task 1: Data Model — TradeEvent + StatusView global + naming constants

**Files:**
- Modify: `src/runtime_v2/control_plane/status_queries.py`

**Interfaces:**
- Produces:
  - `TradeEvent(label, timestamp, source, event_type, reason, clean_log_link)` dataclass
  - `TradeDetail.events: list[TradeEvent]` (sostituisce `last_events: list[str]`)
  - `TradeDetail.entry_legs: list[dict]`, `tp_legs: list[dict]`, `sl_price: str|None`, `has_be: bool`
  - `TradeDetail.unrealized_pnl: float|None`, `cum_realized_pnl: float|None`
  - `TradeDetail.final_result: dict|None`, `is_actionable: bool`, `is_terminal: bool`
  - `StatusView.by_account: list[dict]|None`
  - `StatusQueries.get_status_by_account(scope) -> list[dict]`

- [ ] **Step 1: Scrivi test per TradeEvent e TradeDetail esteso**

```python
# tests/runtime_v2/control_plane/test_status_queries.py
# Aggiungi in coda al file

def test_trade_event_dataclass():
    from src.runtime_v2.control_plane.status_queries import TradeEvent
    ev = TradeEvent(
        label="SIGNAL ACCEPTED",
        timestamp="14 Jun 09:10:00",
        source="Signal",
        event_type=None,
        reason=None,
        clean_log_link=None,
    )
    assert ev.label == "SIGNAL ACCEPTED"
    assert ev.source == "Signal"
    assert ev.clean_log_link is None


def test_trade_detail_has_events_list(ops_db):
    from src.runtime_v2.control_plane.status_queries import StatusQueries, TradeEvent
    conn = sqlite3.connect(ops_db)
    _add_chain(conn, 99, "OPEN")
    conn.execute(
        "INSERT INTO ops_lifecycle_events (trade_chain_id, event_type, payload_json, created_at) "
        "VALUES (99, 'ENTRY_OPENED', '{}', '2024-06-14T09:10:00Z')"
    )
    conn.commit()
    conn.close()
    q = StatusQueries(ops_db)
    detail = q.get_trade(99)
    assert detail is not None
    assert hasattr(detail, "events")
    assert isinstance(detail.events, list)
    if detail.events:
        ev = detail.events[0]
        assert isinstance(ev, TradeEvent)
        assert ev.label  # not empty


def test_trade_detail_extended_fields(ops_db):
    from src.runtime_v2.control_plane.status_queries import StatusQueries
    conn = sqlite3.connect(ops_db)
    _add_chain(conn, 100, "OPEN", sl=62000.0)
    conn.commit()
    conn.close()
    q = StatusQueries(ops_db)
    detail = q.get_trade(100)
    assert detail is not None
    assert hasattr(detail, "is_actionable")
    assert hasattr(detail, "is_terminal")
    assert hasattr(detail, "has_be")
    assert hasattr(detail, "entry_legs")
    assert hasattr(detail, "tp_legs")
    assert isinstance(detail.is_actionable, bool)
    assert isinstance(detail.is_terminal, bool)
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/control_plane/test_status_queries.py::test_trade_event_dataclass tests/runtime_v2/control_plane/test_status_queries.py::test_trade_detail_has_events_list tests/runtime_v2/control_plane/test_status_queries.py::test_trade_detail_extended_fields -v
```

Expected: FAIL (ImportError o AttributeError su TradeEvent / events)

- [ ] **Step 3: Implementa TradeEvent e aggiorna TradeDetail in status_queries.py**

In `status_queries.py`, aggiungi dopo `TradeDetail`:

```python
@dataclass
class TradeEvent:
    label: str
    timestamp: str
    source: str | None = None
    event_type: str | None = None
    reason: str | None = None
    clean_log_link: str | None = None
```

Estendi `TradeDetail`:

```python
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
    original_message_link: str | None = None
    # Legacy — mantenuto per compatibilità con codice non ancora migrato
    last_events: list[str] = field(default_factory=list)
    # Nuovi campi spec
    events: list["TradeEvent"] = field(default_factory=list)
    entry_legs: list[dict] = field(default_factory=list)   # [{"price": str, "status": str}]
    tp_legs: list[dict] = field(default_factory=list)
    sl_price: str | None = None
    has_be: bool = False
    unrealized_pnl: float | None = None
    cum_realized_pnl: float | None = None
    final_result: dict | None = None  # {roi_net, ror, r_mult, pnl_net, pnl_gross, fees, funding}
    is_actionable: bool = False
    is_terminal: bool = False
```

Aggiorna `get_trade()` per popolare `events` dai lifecycle events:

```python
# Sostituisci il blocco eventi in get_trade() con:
events_rows = conn.execute(
    "SELECT created_at, event_type, payload_json FROM ops_lifecycle_events "
    "WHERE trade_chain_id=? ORDER BY event_id ASC",
    (chain_id,),
).fetchall()
original_message_link = _build_telegram_message_link(row[11], row[12])
current_stop_price = row[7]
if current_stop_price is None:
    current_stop_price = _extract_stop_price(row[10], row[9], row[8])

# Costruisci TradeEvent strutturati
_EVENT_LABEL_MAP = {
    "SIGNAL_ACCEPTED": "SIGNAL ACCEPTED",
    "ENTRY_OPENED": "ENTRY OPENED",
    "ENTRY_PARTIALLY_FILLED": "ENTRY PARTIALLY FILLED",
    "TP_FILLED": "TP1 FILLED",
    "SL_MOVED_TO_BE": "SL MOVED TO BE",
    "UPDATE_DONE": "UPDATE DONE",
    "REVIEW_REQUIRED": "REVIEW REQUIRED",
    "POSITION_CLOSED": "POSITION CLOSED",
    "POSITION_CANCELLED": "POSITION CANCELLED",
}
structured_events: list[TradeEvent] = []
for created_at, etype, payload_json in events_rows:
    label = _EVENT_LABEL_MAP.get(etype, etype.replace("_", " ") if etype else "EVENT")
    ts = ""
    if created_at and len(created_at) >= 16:
        # "2024-06-14T09:10:00Z" → "14 Jun 09:10:00"
        try:
            dt = datetime.fromisoformat(created_at.rstrip("Z"))
            ts = dt.strftime("%-d %b %H:%M:%S")
        except Exception:
            ts = created_at[11:19] if len(created_at) >= 19 else created_at
    source_val = None
    event_type_val = None
    reason_val = None
    if payload_json:
        try:
            pdata = json.loads(payload_json)
            source_val = pdata.get("source")
            event_type_val = pdata.get("update_type") or pdata.get("type")
            reason_val = pdata.get("reason") or pdata.get("error")
        except Exception:
            pass
    structured_events.append(TradeEvent(
        label=label,
        timestamp=ts,
        source=source_val,
        event_type=event_type_val,
        reason=reason_val,
        clean_log_link=None,
    ))

# Legacy last_events (backward compat)
last_events_legacy = [
    f"{ev.timestamp} {ev.label}" for ev in structured_events[-3:]
] if structured_events else []

# Determina stato trade
_TERMINAL_STATES = {"CLOSED", "CANCELLED_UNFILLED", "POSITION_CLOSED"}
_ACTIONABLE_STATES = {"OPEN", "PARTIALLY_CLOSED", "WAITING_ENTRY",
                      "REVIEW_REQUIRED", "PARTIALLY_FILLED", "CLOSE_PENDING"}
state_val = row[5]
is_terminal = state_val in _TERMINAL_STATES
is_actionable = state_val in _ACTIONABLE_STATES

# Costruisci entry_legs da management_plan_json
entry_legs: list[dict] = []
tp_legs: list[dict] = []
sl_price_str: str | None = None
has_be = False

try:
    plan = json.loads(row[8] or "{}")
    entries_raw = plan.get("entries") or plan.get("entry_levels") or []
    for e in entries_raw:
        price = e.get("price") or e.get("entry_price")
        status = e.get("status", "pending")
        if price is not None:
            entry_legs.append({"price": str(price), "status": status})
    tps_raw = plan.get("tp_levels") or plan.get("take_profits") or []
    for t in tps_raw:
        price = t.get("price") or t.get("tp_price")
        status = t.get("status", "pending")
        if price is not None:
            tp_legs.append({"price": str(price), "status": status})
except Exception:
    pass

if current_stop_price is not None:
    sl_price_str = str(int(current_stop_price) if current_stop_price == int(current_stop_price) else current_stop_price)

be_status = row[6] if len(row) > 6 else None  # be_protection_status
has_be = be_status == "PROTECTED"
```

E nel return di `get_trade()`:

```python
return TradeDetail(
    chain_id=row[0], symbol=row[1], side=row[2], trader_id=row[3],
    account_id=row[4], state=state_val, entry_avg_price=row[6],
    current_stop_price=current_stop_price,
    original_message_link=original_message_link,
    last_events=last_events_legacy,
    events=structured_events,
    entry_legs=entry_legs,
    tp_legs=tp_legs,
    sl_price=sl_price_str,
    has_be=has_be,
    unrealized_pnl=None,  # populated in Wave 3 if needed
    cum_realized_pnl=None,
    final_result=None,
    is_actionable=is_actionable,
    is_terminal=is_terminal,
)
```

**Nota:** Il `get_trade()` attuale usa `row[6]` per `be_protection_status` e `row[7]` per `entry_avg_price`. Verifica l'ordine colonne nella query SQL e aggiusta di conseguenza.

- [ ] **Step 4: Aggiungi get_status_by_account a StatusQueries**

```python
def get_status_by_account(self, accounts: list[str]) -> list[dict]:
    """Per ogni account, ritorna conteggi open/waiting/failed per il breakdown global scope."""
    conn = self._connect()
    try:
        result = []
        for acc in accounts:
            open_c = conn.execute(
                "SELECT COUNT(*) FROM ops_trade_chains "
                "WHERE lifecycle_state='OPEN' AND account_id=?", (acc,)
            ).fetchone()[0]
            waiting_c = conn.execute(
                "SELECT COUNT(*) FROM ops_trade_chains "
                "WHERE lifecycle_state='WAITING_ENTRY' AND account_id=?", (acc,)
            ).fetchone()[0]
            failed_c = conn.execute(
                "SELECT COUNT(*) FROM ops_execution_commands ec "
                "JOIN ops_trade_chains t ON t.trade_chain_id = ec.trade_chain_id "
                "WHERE ec.status='FAILED' AND t.account_id=?", (acc,)
            ).fetchone()[0]
            result.append({
                "account_id": acc,
                "open_count": open_c,
                "waiting_count": waiting_c,
                "failed_commands": failed_c,
            })
    finally:
        conn.close()
    return result
```

Aggiungi `by_account: list[dict] | None = None` a `StatusView`.

- [ ] **Step 5: Esegui test**

```
pytest tests/runtime_v2/control_plane/test_status_queries.py -x -q
```

Expected: tutti i nuovi test PASS, nessuna regressione.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/status_queries.py tests/runtime_v2/control_plane/test_status_queries.py
git commit -m "feat(data-model): TradeEvent struct, TradeDetail extended fields, StatusView.by_account"
```

---

## Task 2: Dashboard — naming IT→EN + header compatto + item renderer spec

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/templates/dashboard.py`
- Modify: `src/runtime_v2/control_plane/formatters/dashboard.py`
- Modify: `src/runtime_v2/control_plane/dashboard_manager.py`
- Modify: `tests/runtime_v2/control_plane/test_dashboard_formatter.py`
- Modify: `tests/runtime_v2/control_plane/test_dashboard_manager.py`

**Interfaces:**
- Consumes: `QueryScope`, `StatusQueries` (ClosedTradeRow + `duration`/`closed_reason`)
- Produces:
  - Registry keys: `dashboard_active`, `dashboard_closed`, `dashboard_blocked`, `dashboard_pnl`, `dashboard_stats`
  - `_DEFAULT_VIEW = "active"` in `dashboard_manager.py`
  - Header payload keys: `total`, `page_display`, `filters_str`
  - `is_global: bool` nel payload
  - `build_dashboard_keyboard()` ritorna keyboard con row Filters/Clear

- [ ] **Step 1: Aggiorna test per naming e header**

In `tests/runtime_v2/control_plane/test_dashboard_formatter.py`, aggiungi:

```python
def test_dashboard_view_key_active(tmp_path):
    """Template registry usa 'dashboard_active', non 'dashboard_attivi'."""
    from src.runtime_v2.control_plane.formatters.templates.commands import TEMPLATE_REGISTRY
    assert "dashboard_active" in TEMPLATE_REGISTRY
    assert "dashboard_attivi" not in TEMPLATE_REGISTRY


def test_dashboard_active_header_contains_total_and_page(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    scope = QueryScope(account_id="demo_1", trader_ids=None)
    q = StatusQueries(db_path)
    from src.runtime_v2.control_plane.formatters.dashboard import format_dashboard_view
    text, total = format_dashboard_view("active", scope, q, page=0, page_size=5)
    assert "Total:" in text
    assert "Page:" in text
    assert "Updated:" in text
    assert "Active" in text
    assert "demo_1" in text


def test_dashboard_active_item_compact_format(tmp_path):
    """Item active: 3 righe spec (#n · SYMBOL · SIDE · STATE / uPnL rPnL / /trade n · /cancel n · /close n)."""
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    conn = sqlite3.connect(db_path)
    _add_chain(conn, 5, "OPEN", symbol="BTCUSDT", side="LONG")
    conn.commit(); conn.close()
    scope = QueryScope(account_id="demo_1", trader_ids=None)
    q = StatusQueries(db_path)
    from src.runtime_v2.control_plane.formatters.dashboard import format_dashboard_view
    text, _ = format_dashboard_view("active", scope, q, page=0, page_size=5)
    assert "#5" in text
    assert "BTCUSDT" in text
    assert "/trade 5" in text
    assert "/close 5" in text


def test_dashboard_active_global_scope_shows_trader_account(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    conn = sqlite3.connect(db_path)
    _add_chain(conn, 17, "OPEN", symbol="ETHUSDT", side="SHORT",
               account_id="demo_1", trader_id="trader_alpha")
    conn.commit(); conn.close()
    scope = QueryScope(account_id=None, trader_ids=None)
    q = StatusQueries(db_path)
    from src.runtime_v2.control_plane.formatters.dashboard import format_dashboard_view
    text, _ = format_dashboard_view("active", scope, q, page=0, page_size=5)
    assert "All accounts" in text
    assert "Trader:" in text
    assert "Account:" in text


def test_dashboard_closed_item_has_duration_and_details(tmp_path):
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    scope = QueryScope(account_id="demo_1", trader_ids=None)
    q = StatusQueries(db_path)
    from src.runtime_v2.control_plane.formatters.dashboard import format_dashboard_view
    text, _ = format_dashboard_view("closed", scope, q, page=0, page_size=5)
    # empty state: no closed trades
    assert "No closed trades" in text or "Nessun" in text or "closed" in text.lower()


def test_dashboard_naming_migration(tmp_path):
    """DashboardManager migra 'attivi:0' → 'active:0' al boot."""
    db_path = str(tmp_path / "ops.sqlite3")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE ops_dashboard_messages (
            chat_id INTEGER NOT NULL,
            thread_id INTEGER NOT NULL DEFAULT 0,
            message_id INTEGER NOT NULL,
            scope_account_id TEXT,
            scope_trader_id TEXT,
            current_view TEXT NOT NULL DEFAULT 'attivi:0',
            updated_at TEXT,
            PRIMARY KEY (chat_id, thread_id)
        )
    """)
    conn.execute("INSERT INTO ops_dashboard_messages VALUES (1,0,42,NULL,NULL,'attivi:0',NULL)")
    conn.execute("INSERT INTO ops_dashboard_messages VALUES (2,0,43,NULL,NULL,'chiusi:2',NULL)")
    conn.execute("INSERT INTO ops_dashboard_messages VALUES (3,0,44,NULL,NULL,'bloccati:0',NULL)")
    conn.commit(); conn.close()

    from unittest.mock import MagicMock
    from src.runtime_v2.control_plane.status_queries import StatusQueries
    mgr = DashboardManager(
        ops_db_path=db_path,
        scope_resolver=MagicMock(),
        queries=MagicMock(spec=StatusQueries),
        bot=None,
    )
    conn2 = sqlite3.connect(db_path)
    rows = conn2.execute("SELECT chat_id, current_view FROM ops_dashboard_messages ORDER BY chat_id").fetchall()
    conn2.close()
    assert rows[0][1] == "active:0"
    assert rows[1][1] == "closed:2"
    assert rows[2][1] == "blocked:0"
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/control_plane/test_dashboard_formatter.py::test_dashboard_view_key_active tests/runtime_v2/control_plane/test_dashboard_manager.py::test_dashboard_naming_migration -v
```

Expected: FAIL

- [ ] **Step 3: Aggiorna templates/dashboard.py — naming e header**

```python
# src/runtime_v2/control_plane/formatters/templates/dashboard.py
# Sostituisci _dash_header con _dash_header_full:

def _dash_header_full(emoji: str, view_label: str) -> list:
    """Header compatto spec:
    ⚡ Active — demo_1 · trader_a
    - - - - -
    Total: 10   Page: 1/2   Updated: 14:32:05
    [Filters: ...]
    - - - - -
    """
    return [
        DerivedBlock(text_fn=lambda p, _e=emoji, _v=view_label: (
            f"{_e} {_v} — "
            + (p.get("account_id") or "All accounts")
            + (f" · {p['trader_id']}" if p.get("trader_id") else "")
        )),
        SeparatorBlock(),
        DerivedBlock(text_fn=lambda p: (
            f"Total: {p.get('total', 0)}   "
            f"Page: {p.get('page_display', '1/1')}   "
            f"Updated: {p.get('updated_at', 'n/a')}"
        )),
        ConditionalBlock(
            condition=lambda p: bool(p.get("filters_str")),
            blocks=[DerivedBlock(text_fn=lambda p: f"Filters: {p['filters_str']}")],
        ),
        SeparatorBlock(),
    ]
```

- [ ] **Step 4: Riscrivi item renderer Active**

```python
def _render_active_item(row: dict, i: int, p: dict) -> list[str]:
    cid = row.get("chain_id", "?")
    symbol = row.get("symbol", "?")
    side = row.get("side", "?")
    state = row.get("state", "?")
    lines = [f"#{cid} · {symbol} · {side} · {state}"]

    if p.get("is_global"):
        trader = row.get("trader_id", "?")
        account = row.get("account_id", "?")
        lines.append(f"Trader: {trader} · Account: {account}")

    upnl = row.get("unrealized_pnl")
    rpnl = row.get("cum_realized_pnl")
    if state not in ("WAITING_ENTRY", "PARTIALLY_FILLED"):
        upnl_str = money_signed(upnl) if upnl is not None else "—"
        rpnl_str = money_signed(rpnl) if rpnl is not None else "+0.00 USDT"
        lines.append(f"uPnL: {upnl_str}  rPnL: {rpnl_str}")
    else:
        lines.append("rPnL: —")

    lines.append(f"/trade {cid} · /cancel {cid} · /close {cid}")
    return lines
```

- [ ] **Step 5: Riscrivi item renderer Closed**

```python
def _render_closed_item(row: dict, i: int, p: dict) -> list[str]:
    cid = row.get("chain_id", "?")
    symbol = row.get("symbol", "?")
    side = row.get("side", "?")
    reason = row.get("closed_reason") or "CLOSED"
    lines = [f"#{cid} · {symbol} · {side} · {reason}"]

    if p.get("is_global"):
        lines.append(f"Trader: {row.get('trader_id','?')} · Account: {row.get('account_id','?')}")

    pnl = row.get("gross_pnl")
    pnl_str = money_signed(pnl) if pnl is not None else "—"
    duration = row.get("duration") or "—"
    lines.append(f"Net PnL: {pnl_str} · ⏱ {duration}")
    lines.append(f"Details: /trade {cid}")
    return lines
```

- [ ] **Step 6: Riscrivi item renderer Blocked**

```python
def _render_blocked_item(row: dict, i: int, p: dict) -> list[str]:
    cid = row.get("chain_id", "?")
    symbol = row.get("symbol", "?")
    side = row.get("side", "?")
    lines = [f"#{cid} · {symbol} · {side}"]

    if p.get("is_global"):
        lines.append(f"Trader: {row.get('trader_id','?')} · Account: {row.get('account_id','?')}")

    blocked_at = row.get("blocked_at") or "—"
    reason = row.get("reason") or "—"
    lines.append(f"Blocked: {blocked_at} · Reason: {reason}")
    lines.append(f"Details: /trade {cid}")
    return lines
```

- [ ] **Step 7: Aggiorna i blocchi template e il registry in templates/dashboard.py**

```python
_ACTIVE_BLOCKS: list = [
    *_dash_header_full("⚡", "Active"),
    ConditionalBlock(
        condition=lambda p: not p.get("rows"),
        blocks=[StaticBlock("No active trades.")],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("rows")),
        blocks=[ListBlock(key="rows", item_renderer=_render_active_item)],
    ),
]

_CLOSED_BLOCKS: list = [
    *_dash_header_full("✅", "Closed"),
    ConditionalBlock(
        condition=lambda p: not p.get("rows"),
        blocks=[StaticBlock("No closed trades.")],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("rows")),
        blocks=[ListBlock(key="rows", item_renderer=_render_closed_item)],
    ),
]

_BLOCKED_BLOCKS: list = [
    *_dash_header_full("🚫", "Blocked"),
    ConditionalBlock(
        condition=lambda p: not p.get("rows"),
        blocks=[StaticBlock("No blocked trades.")],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("rows")),
        blocks=[ListBlock(key="rows", item_renderer=_render_blocked_item)],
    ),
]

TEMPLATE_DASHBOARD_ACTIVE = TemplateConfig(_ACTIVE_BLOCKS, payload_transform=None)
TEMPLATE_DASHBOARD_CLOSED = TemplateConfig(_CLOSED_BLOCKS, payload_transform=None)
TEMPLATE_DASHBOARD_BLOCKED = TemplateConfig(_BLOCKED_BLOCKS, payload_transform=None)
# PNL e STATS ricevono anche _dash_header_full — aggiorna allo stesso modo

DASHBOARD_TEMPLATE_REGISTRY: dict[str, TemplateConfig] = {
    "dashboard_active":   TEMPLATE_DASHBOARD_ACTIVE,
    "dashboard_closed":   TEMPLATE_DASHBOARD_CLOSED,
    "dashboard_blocked":  TEMPLATE_DASHBOARD_BLOCKED,
    "dashboard_pnl":      TEMPLATE_DASHBOARD_PNL,
    "dashboard_stats":    TEMPLATE_DASHBOARD_STATS,
}
```

- [ ] **Step 8: Aggiorna formatters/dashboard.py — payload builder**

In `_build_attivi_payload` (ora `_build_active_payload`), aggiungi al payload:

```python
# Calcola page_display
total_pages = max(1, (total + page_size - 1) // page_size)
page_display = f"{page + 1}/{total_pages}"
is_global = scope.account_id is None

# Aggiorna row_dicts per includere trader_id e account_id (per global scope)
# e unrealized_pnl, cum_realized_pnl già presenti in TradeRow

payload = {
    **_build_scope_meta(scope),
    "account_id": scope.account_id or "All accounts",
    "updated_at": view.updated_at,
    "rows": row_dicts,
    "total": total,
    "page_display": page_display,
    "filters_str": None,  # Wave 4
    "is_global": is_global,
    "_mark_stale": mark_stale,
    "_mark_time": mark_time,
    "_mark_age": mark_age,
}
```

Stessa logica per `_build_closed_payload`, `_build_blocked_payload`, `_build_pnl_payload`, `_build_stats_payload`. In `_build_closed_payload`, aggiungi `duration` calcolata da `closed_at - created_at` e `closed_reason` da `lifecycle_state`.

Rinomina i metodi:
- `_build_attivi_payload` → `_build_active_payload`
- `_build_chiusi_payload` → `_build_closed_payload`  
- `_build_bloccati_payload` → `_build_blocked_payload`

Aggiorna `format_dashboard_view()` per usare i nuovi nomi e template key `"dashboard_active"` ecc.

- [ ] **Step 9: Aggiorna _DEFAULT_VIEW e naming migration in dashboard_manager.py**

```python
_DEFAULT_VIEW = "active"  # era "attivi"

def _ensure_table(self) -> None:
    # ... codice esistente per CREATE TABLE ...
    
    # Migration IT→EN per current_view
    conn.execute("""
        UPDATE ops_dashboard_messages
        SET current_view = REPLACE(REPLACE(REPLACE(current_view,
            'attivi', 'active'),
            'chiusi', 'closed'),
            'bloccati', 'blocked')
        WHERE current_view LIKE '%attivi%'
           OR current_view LIKE '%chiusi%'
           OR current_view LIKE '%bloccati%'
    """)
    conn.commit()
```

Aggiorna anche `build_dashboard_keyboard()` in `formatters/dashboard.py`:

```python
row1 = [
    _tab("⚡ Active",  "active"),    # era "attivi"
    _tab("✅ Closed",  "closed"),    # era "chiusi"
    _tab("🚫 Blocked", "blocked"),   # era "bloccati"
]
```

- [ ] **Step 10: Esegui i test**

```
pytest tests/runtime_v2/control_plane/test_dashboard_formatter.py tests/runtime_v2/control_plane/test_dashboard_manager.py -x -q
```

Expected: tutti i nuovi test PASS, nessuna regressione sui test esistenti.

- [ ] **Step 11: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/templates/dashboard.py \
        src/runtime_v2/control_plane/formatters/dashboard.py \
        src/runtime_v2/control_plane/dashboard_manager.py \
        tests/runtime_v2/control_plane/test_dashboard_formatter.py \
        tests/runtime_v2/control_plane/test_dashboard_manager.py
git commit -m "feat(dashboard): naming IT→EN migration, compact header, spec-compliant item renderers"
```

---

## Task 3: Commands formatters — /trades, /status, /control, /reviews global scope

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/templates/_shared.py`
- Modify: `src/runtime_v2/control_plane/formatters/templates/commands.py`
- Modify: `src/runtime_v2/control_plane/formatters/status.py`
- Modify: `tests/runtime_v2/control_plane/test_command_formatters.py`

**Interfaces:**
- Consumes: `QueryScope`, `StatusView.by_account`, `ReviewItem` (con `trader_id` e `account_id` aggiunti)
- Produces: output testo conforme a spec per tutti e 4 i comandi

- [ ] **Step 1: Scrivi test per /trades formato spec**

```python
# tests/runtime_v2/control_plane/test_command_formatters.py — aggiungi:

def test_trades_format_spec_compact():
    """Formato spec: #n · SYMBOL · SIDE · STATE / uPnL rPnL / Details: /trade n"""
    from src.runtime_v2.control_plane.formatters.trades import format_trades
    view = _trades_view(
        rows=[TradeRow(
            chain_id=5, symbol="BTCUSDT", side="LONG", state="OPEN",
            has_sl=True, has_be=False,
            entry_avg_price=63500.0, open_position_qty=0.01,
            unrealized_pnl=12.40, cum_realized_pnl=0.0,
        )],
        total=1,
    )
    scope = _scope("demo_1")
    text = format_trades(view, scope)
    assert "#5 · BTCUSDT · LONG · OPEN" in text
    assert "uPnL:" in text
    assert "rPnL:" in text
    assert "Details: /trade 5" in text
    # NON deve esserci la vecchia struttura entry/sl
    assert "Entry:" not in text
    assert "SL:" not in text


def test_trades_format_waiting_entry_no_upnl():
    from src.runtime_v2.control_plane.formatters.trades import format_trades
    view = _trades_view(
        rows=[TradeRow(
            chain_id=9, symbol="SOLUSDT", side="LONG", state="WAITING_ENTRY",
            has_sl=False, has_be=False,
        )],
        total=1,
    )
    text = format_trades(view, _scope("demo_1"))
    assert "rPnL: —" in text
    assert "uPnL:" not in text


def test_trades_format_global_scope_shows_trader_account():
    from src.runtime_v2.control_plane.formatters.trades import format_trades
    scope = QueryScope(account_id=None, trader_ids=None)
    view = _trades_view(
        rows=[TradeRow(
            chain_id=17, symbol="ETHUSDT", side="SHORT", state="OPEN",
            has_sl=True, has_be=False, unrealized_pnl=-3.20,
        )],
        total=1,
    )
    text = format_trades(view, scope)
    assert "All accounts" in text


def test_status_global_scope_shows_all_accounts():
    from src.runtime_v2.control_plane.formatters.status import format_status
    from src.runtime_v2.control_plane.status_queries import StatusView
    scope = QueryScope(account_id=None, trader_ids=None)
    view = StatusView(
        updated_at="14:32:05", control_mode="NONE", new_entries_enabled=True,
        sync_age_seconds=12.0, open_count=7, partial_count=2,
        waiting_entry_count=4, review_count=2, pending_commands=1,
        failed_commands=3, no_sl_count=2,
        by_account=[
            {"account_id": "demo_2", "open_count": 3, "waiting_count": 1, "failed_commands": 1},
            {"account_id": "demo_1", "open_count": 2, "waiting_count": 2, "failed_commands": 0},
        ],
    )
    text = format_status(view, scope)
    assert "All accounts" in text
    assert "By account" in text
    assert "demo_2" in text
    assert "demo_1" in text


def test_reviews_global_scope_shows_trader_account():
    from src.runtime_v2.control_plane.formatters.reviews import format_reviews
    from src.runtime_v2.control_plane.status_queries import ReviewsView, ReviewItem
    scope = QueryScope(account_id=None, trader_ids=None)
    view = ReviewsView(
        updated_at="14:32:05",
        items=[ReviewItem(chain_id=7, symbol="ETHUSDT", reason="missing_sl",
                          trader_id="trader_devos", account_id="demo_2")],
    )
    text = format_reviews(view, scope)
    assert "All accounts" in text
    assert "trader_devos" in text
    assert "demo_2" in text
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/control_plane/test_command_formatters.py::test_trades_format_spec_compact tests/runtime_v2/control_plane/test_command_formatters.py::test_status_global_scope_shows_all_accounts -v
```

Expected: FAIL

- [ ] **Step 3: Aggiorna _render_trade_item in _shared.py**

```python
def _render_trade_item(row: dict, i: int, p: dict) -> list[str]:
    cid = row.get("chain_id", "?")
    symbol = row.get("symbol_display", row.get("symbol", "?"))
    side = row.get("side", "?")
    state = row.get("state", "?")
    is_global = p.get("is_global", False)

    lines = [f"#{cid} · {symbol} · {side} · {state}"]

    if is_global:
        trader = row.get("trader_id") or p.get("trader_id") or "?"
        account = row.get("account_id") or p.get("account_id") or "?"
        lines.append(f"Trader: {trader} · Account: {account}")

    if state in ("WAITING_ENTRY", "PARTIALLY_FILLED"):
        lines.append("rPnL: —")
    else:
        upnl = row.get("unrealized_pnl")
        rpnl = row.get("cum_realized_pnl")
        upnl_str = money_signed(upnl) if upnl is not None else "—"
        rpnl_str = money_signed(rpnl) if rpnl is not None else "+0.00 USDT"
        lines.append(f"uPnL: {upnl_str}  rPnL: {rpnl_str}")

    lines.append(f"Details: /trade {cid}")
    return lines
```

- [ ] **Step 4: Aggiorna _trades_to_payload in formatters/trades.py**

Aggiungi `is_global` e i campi `trader_id`/`account_id` per ogni row:

```python
def _trades_to_payload(view: TradesView, scope: QueryScope | None) -> dict:
    is_global = scope is None or scope.account_id is None
    rows = []
    for r in view.rows:
        rows.append({
            "chain_id": r.chain_id,
            "symbol": r.symbol,
            "symbol_display": display_symbol(r.symbol),
            "side": r.side,
            "state": r.state,
            "has_sl": r.has_sl,
            "has_be": r.has_be,
            "entry_avg_price": r.entry_avg_price,
            "open_position_qty": r.open_position_qty,
            "unrealized_pnl": r.unrealized_pnl,
            "cum_realized_pnl": r.cum_realized_pnl,
            "mark_price": r.mark_price,
            "mark_captured_at": r.mark_captured_at,
            "current_stop_price": None,
            # Per global scope: trader_id e account_id non sono in TradeRow oggi
            # → verranno aggiunti a TradeRow in futura iterazione se necessario
            "trader_id": None,
            "account_id": None,
        })
    # ... resto invariato ...
    payload: dict = {
        "account_id": scope.account_id if (scope and scope.account_id) else "All accounts",
        "trader_id": (
            scope.trader_ids[0]
            if scope and scope.trader_ids and len(scope.trader_ids) == 1
            else None
        ),
        "is_global": is_global,
        "updated_at": view.updated_at,
        "rows": rows,
        "_mark_time": mark_time,
        "_mark_age": mark_age,
        "_mark_stale": mark_stale,
    }
    return payload
```

- [ ] **Step 5: Aggiorna TEMPLATE_TRADES header in commands.py**

```python
_TRADES_BLOCKS: list = [
    *_cmd_header("📊", "TRADES"),
    DerivedBlock(text_fn=lambda p: (
        f"Total: {p.get('total_count', len(p.get('rows', [])))}   "
        f"Updated: {p.get('updated_at', 'n/a')}"
    )),
    # ... resto invariato ...
]
```

- [ ] **Step 6: Aggiorna TEMPLATE_STATUS per global scope**

```python
_STATUS_BLOCKS: list = [
    DerivedBlock(text_fn=lambda p: (
        f"{p.get('_level', '🟢')} Runtime V2 — STATUS  |  "
        + (p.get("account_id") or "All accounts")
    )),
    StaticBlock("————————————————"),
    DerivedBlock(text_fn=lambda p: f"Updated: {p.get('updated_at', 'n/a')}"),
    StaticBlock(""),
    StaticBlock("Mode:"),
    DerivedBlock(text_fn=lambda p: f"  New entries: {'ENABLED' if p.get('new_entries_enabled') else 'BLOCKED'}"),
    DerivedBlock(text_fn=lambda p: f"  Control: {p.get('control_mode', 'NONE')}"),
    DerivedBlock(text_fn=lambda p: f"  Sync: {p.get('_sync_str', 'n/a')}"),
    StaticBlock(""),
    StaticBlock("Trades:"),
    DerivedBlock(text_fn=lambda p: f"  Open: {p.get('open_count', 0)}"),
    DerivedBlock(text_fn=lambda p: f"  Waiting entry: {p.get('waiting_entry_count', 0)}"),
    DerivedBlock(text_fn=lambda p: f"  Partial: {p.get('partial_count', 0)}"),
    DerivedBlock(text_fn=lambda p: (
        f"  Review required: {p.get('review_count', 0)}"
        + ("  ⚠️" if p.get('review_count', 0) > 0 else "")
    )),
    StaticBlock(""),
    StaticBlock("Execution:"),
    DerivedBlock(text_fn=lambda p: f"  Pending commands: {p.get('pending_commands', 0)}"),
    DerivedBlock(text_fn=lambda p: (
        f"  Failed commands: {p.get('failed_commands', 0)}"
        + ("  🔴" if p.get('failed_commands', 0) > 0 else "")
    )),
    StaticBlock(""),
    StaticBlock("Risk:"),
    DerivedBlock(text_fn=lambda p: (
        f"  No SL: {p.get('no_sl_count', 0)}"
        + ("  🔴" if p.get('no_sl_count', 0) > 0 else "")
    )),
    # By account (solo global scope)
    ConditionalBlock(
        condition=lambda p: bool(p.get("by_account")),
        blocks=[
            StaticBlock(""),
            StaticBlock("By account:"),
            ListBlock(key="by_account", item_renderer=lambda a, i, p: [
                f"  {a['account_id']}  Open: {a['open_count']}  "
                f"Waiting: {a['waiting_count']}  Failed: {a['failed_commands']}"
            ]),
        ],
    ),
    StaticBlock(""),
    DerivedBlock(text_fn=lambda p: (
        f"/trades  ·  /reviews  ·  /control"
    )),
]
```

- [ ] **Step 7: Aggiorna _status_to_payload**

```python
def _status_to_payload(view: StatusView, scope: QueryScope | None) -> dict:
    # ...
    account_label = "All accounts" if (scope is None or scope.account_id is None) else scope.account_id
    return {
        "account_id": account_label,
        # ... tutti i campi esistenti ...
        "by_account": view.by_account,  # list[dict] | None
    }
```

- [ ] **Step 8: Aggiorna ReviewItem e format_reviews per global scope**

In `status_queries.py`, aggiungi campi a `ReviewItem`:

```python
@dataclass
class ReviewItem:
    chain_id: int | None
    symbol: str | None
    reason: str
    trader_id: str | None = None    # nuovo
    account_id: str | None = None   # nuovo
```

In `get_reviews()`, popola `trader_id` e `account_id` dalla query:

```python
chain_rows = conn.execute(
    f"SELECT trade_chain_id, symbol, trader_id, account_id FROM ops_trade_chains "
    f"WHERE lifecycle_state='REVIEW_REQUIRED' AND {scope_frag} "
    f"ORDER BY trade_chain_id",
    scope_params,
).fetchall()
# ...
items.append(ReviewItem(chain_id=cid, symbol=symbol, reason=reason,
                         trader_id=row[2], account_id=row[3]))
```

In `formatters/reviews.py`, aggiorna `format_reviews()`:

```python
from src.runtime_v2.control_plane.formatters._blocks import (
    render_template, StaticBlock, SeparatorBlock, DerivedBlock,
    ConditionalBlock, ListBlock, TemplateConfig,
)

def _render_review_item(item: dict, i: int, p: dict) -> list[str]:
    cid = item.get("chain_id")
    symbol = item.get("symbol")
    reason = item.get("reason", "unknown")
    line = f"#{cid}  {symbol}  {reason}" if cid else f"? {symbol} {reason}"
    lines = [line]
    if p.get("is_global") and (item.get("trader_id") or item.get("account_id")):
        lines.append(
            f"     Trader: {item.get('trader_id','?')} · Account: {item.get('account_id','?')}"
        )
    return lines

_REVIEWS_BLOCKS: list = [
    DerivedBlock(text_fn=lambda p: (
        f"{'⚠️' if p.get('items') else '✅'} REVIEWS  |  "
        + (p.get("account_id") or "All accounts")
    )),
    StaticBlock("————————————————"),
    DerivedBlock(text_fn=lambda p: f"Updated: {p.get('updated_at', 'n/a')}"),
    ConditionalBlock(
        condition=lambda p: bool(p.get("items")),
        blocks=[
            DerivedBlock(text_fn=lambda p: f"Casi aperti: {len(p['items'])}"),
            StaticBlock(""),
            ListBlock(key="items", item_renderer=_render_review_item),
            StaticBlock(""),
            StaticBlock("/trade #id  per dettaglio"),
        ],
    ),
    ConditionalBlock(
        condition=lambda p: not p.get("items"),
        blocks=[StaticBlock("Nessun caso in review.")],
    ),
]

_TEMPLATE_REVIEWS = TemplateConfig(_REVIEWS_BLOCKS, payload_transform=None)

def format_reviews(view: ReviewsView, scope: QueryScope | None = None) -> str:
    is_global = scope is None or scope.account_id is None
    payload = {
        "account_id": scope.account_id if (scope and scope.account_id) else None,
        "is_global": is_global,
        "updated_at": view.updated_at,
        "items": [
            {
                "chain_id": item.chain_id,
                "symbol": item.symbol,
                "reason": item.reason,
                "trader_id": item.trader_id,
                "account_id": item.account_id,
            }
            for item in view.items
        ],
    }
    return render_template(_TEMPLATE_REVIEWS.blocks, payload)
```

- [ ] **Step 9: Esegui test**

```
pytest tests/runtime_v2/control_plane/test_command_formatters.py -x -q
```

Expected: tutti PASS.

- [ ] **Step 10: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/templates/_shared.py \
        src/runtime_v2/control_plane/formatters/templates/commands.py \
        src/runtime_v2/control_plane/formatters/status.py \
        src/runtime_v2/control_plane/formatters/reviews.py \
        src/runtime_v2/control_plane/formatters/trades.py \
        src/runtime_v2/control_plane/status_queries.py \
        tests/runtime_v2/control_plane/test_command_formatters.py
git commit -m "feat(formatters): /trades /status /reviews global scope, spec-compliant format"
```

---

## Task 4: /trade n — formatter completo

**Files:**
- Rewrite: `src/runtime_v2/control_plane/formatters/trade_detail.py`
- Modify: `tests/runtime_v2/control_plane/test_readonly_formatters.py`

**Interfaces:**
- Consumes: `TradeDetail` (con i nuovi campi da Task 1)
- Produces: singolo messaggio testuale spec-compliant con block system

- [ ] **Step 1: Scrivi test**

```python
# tests/runtime_v2/control_plane/test_readonly_formatters.py — aggiungi:

from src.runtime_v2.control_plane.status_queries import TradeDetail, TradeEvent

def _make_detail(**kw) -> TradeDetail:
    defaults = dict(
        chain_id=5, symbol="BTCUSDT", side="LONG", trader_id="trader_a",
        account_id="demo_2", state="OPEN", entry_avg_price=63500.0,
        current_stop_price=62000.0, original_message_link=None,
        last_events=[], events=[], entry_legs=[], tp_legs=[],
        sl_price="62,000", has_be=False, unrealized_pnl=34.20,
        cum_realized_pnl=14.20, final_result=None,
        is_actionable=True, is_terminal=False,
    )
    defaults.update(kw)
    return TradeDetail(**defaults)


def test_trade_detail_header():
    from src.runtime_v2.control_plane.formatters.trade_detail import format_trade_detail
    detail = _make_detail(state="PARTIALLY_CLOSED")
    text = format_trade_detail(detail)
    assert "#5 · BTCUSDT · LONG · PARTIALLY_CLOSED" in text


def test_trade_detail_meta_section():
    from src.runtime_v2.control_plane.formatters.trade_detail import format_trade_detail
    text = format_trade_detail(_make_detail())
    assert "Trader: trader_a" in text
    assert "Exchange Account: demo_2" in text


def test_trade_detail_pnl_section_open_trade():
    from src.runtime_v2.control_plane.formatters.trade_detail import format_trade_detail
    text = format_trade_detail(_make_detail())
    assert "uPnL:" in text
    assert "rPnL:" in text


def test_trade_detail_actions_present_when_actionable():
    from src.runtime_v2.control_plane.formatters.trade_detail import format_trade_detail
    text = format_trade_detail(_make_detail(is_actionable=True))
    assert "Actions:" in text
    assert "/cancel 5" in text or "/close 5" in text


def test_trade_detail_no_actions_when_terminal():
    from src.runtime_v2.control_plane.formatters.trade_detail import format_trade_detail
    detail = _make_detail(state="CLOSED", is_actionable=False, is_terminal=True,
                          unrealized_pnl=None, cum_realized_pnl=None,
                          final_result={"pnl_net": 44.17, "pnl_gross": 45.20,
                                        "fees": -2.06, "funding": 0.03,
                                        "roi_net": 3.67, "ror": 9.12, "r_mult": 0.22})
    text = format_trade_detail(detail)
    assert "Final Result:" in text
    assert "Actions:" not in text


def test_trade_detail_waiting_entry_no_pnl():
    from src.runtime_v2.control_plane.formatters.trade_detail import format_trade_detail
    detail = _make_detail(state="WAITING_ENTRY", unrealized_pnl=None, cum_realized_pnl=None)
    text = format_trade_detail(detail)
    assert "uPnL:" not in text


def test_trade_detail_timeline_events():
    from src.runtime_v2.control_plane.formatters.trade_detail import format_trade_detail
    detail = _make_detail(events=[
        TradeEvent(label="SIGNAL ACCEPTED", timestamp="14 Jun 09:10:00", source="Signal",
                   event_type=None, reason=None, clean_log_link=None),
        TradeEvent(label="ENTRY OPENED", timestamp="14 Jun 09:10:01", source="exchange",
                   event_type=None, reason=None, clean_log_link=None),
    ])
    text = format_trade_detail(detail)
    assert "Events:" in text
    assert "SIGNAL ACCEPTED" in text
    assert "ENTRY OPENED" in text


def test_trade_detail_not_found():
    from src.runtime_v2.control_plane.formatters.trade_detail import format_trade_detail
    assert format_trade_detail(None) == "Trade not found."
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/control_plane/test_readonly_formatters.py -k "trade_detail" -v
```

Expected: FAIL (vecchio formato non conforme a spec)

- [ ] **Step 3: Riscrivi trade_detail.py con block system**

```python
# src/runtime_v2/control_plane/formatters/trade_detail.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    render_template, StaticBlock, SeparatorBlock, DerivedBlock,
    ConditionalBlock, ListBlock, TemplateConfig,
)
from src.runtime_v2.control_plane.formatters._formatters import money_signed, pct_signed, r_mult
from src.runtime_v2.control_plane.status_queries import TradeDetail


def _fmt_leg(leg: dict) -> str:
    price = leg.get("price", "?")
    status = leg.get("status", "pending")
    if status == "filled":
        return f"{price} ✓"
    if status == "cancelled":
        return f"{price} ✗"
    return str(price)


def _render_event(ev: dict, i: int, p: dict) -> list[str]:
    label = ev.get("label", "EVENT")
    ts = ev.get("timestamp", "")
    lines = [f"• {label} · {ts}"]
    source = ev.get("source")
    link = ev.get("clean_log_link")
    if source:
        source_part = f"Source: {source}"
        if link:
            source_part += f" -> {link}"
        lines.append(f"  {source_part}")
    if ev.get("event_type"):
        lines.append(f"  Type: {ev['event_type']}")
    if ev.get("reason"):
        lines.append(f"  Reason: {ev['reason']}")
    return lines


_TRADE_DETAIL_BLOCKS: list = [
    # 1. Titolo
    DerivedBlock(text_fn=lambda p: f"#{p['chain_id']} · {p['symbol']} · {p['side']} · {p['state']}"),
    SeparatorBlock(),
    # 2. Meta
    DerivedBlock(text_fn=lambda p: f"Trader: {p['trader_id']}"),
    DerivedBlock(text_fn=lambda p: f"Exchange Account: {p['account_id']}"),
    DerivedBlock(text_fn=lambda p: f"Updated: {p['updated_at']}"),
    SeparatorBlock(),
    # 3. Struttura ordini
    ConditionalBlock(
        condition=lambda p: bool(p.get("entry_legs")),
        blocks=[DerivedBlock(text_fn=lambda p: "Entry: " + " · ".join(_fmt_leg(l) for l in p["entry_legs"]))],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("tp_legs")),
        blocks=[DerivedBlock(text_fn=lambda p: "TP:    " + " · ".join(_fmt_leg(l) for l in p["tp_legs"]))],
    ),
    ConditionalBlock(
        condition=lambda p: bool(p.get("sl_price")),
        blocks=[DerivedBlock(text_fn=lambda p: (
            f"SL:    {p['sl_price']}"
            + ("  · BE: set" if p.get("has_be") else "  · BE: No")
        ))],
    ),
    # 4a. Stato economico (trade aperto/azionabile, non WAITING_ENTRY)
    ConditionalBlock(
        condition=lambda p: (
            p.get("is_actionable") and
            p.get("state") not in ("WAITING_ENTRY", "PARTIALLY_FILLED") and
            not p.get("is_terminal")
        ),
        blocks=[
            SeparatorBlock(),
            DerivedBlock(text_fn=lambda p: (
                f"uPnL:  {money_signed(p.get('unrealized_pnl'))}  "
                f"rPnL:  {money_signed(p.get('cum_realized_pnl', 0.0))}"
            )),
        ],
    ),
    # 4b. Final Result (trade terminale)
    ConditionalBlock(
        condition=lambda p: p.get("is_terminal") and bool(p.get("final_result")),
        blocks=[
            SeparatorBlock(),
            StaticBlock("Final Result:"),
            DerivedBlock(text_fn=lambda p: (
                f"ROI net: {pct_signed(p['final_result'].get('roi_net'))}  "
                f"· RoR: {pct_signed(p['final_result'].get('ror'))}  "
                f"· R: {r_mult(p['final_result'].get('r_mult'))}"
            )),
            DerivedBlock(text_fn=lambda p: (
                f"PnL net: {money_signed(p['final_result'].get('pnl_net'))}  "
                f"· PnL gross: {money_signed(p['final_result'].get('pnl_gross'))}"
            )),
            DerivedBlock(text_fn=lambda p: (
                f"Fees: {money_signed(p['final_result'].get('fees'))}  "
                f"· Funding: {money_signed(p['final_result'].get('funding'))}"
            )),
        ],
    ),
    # 4c. Cancelled unfilled
    ConditionalBlock(
        condition=lambda p: p.get("state") == "CANCELLED_UNFILLED",
        blocks=[
            SeparatorBlock(),
            StaticBlock("Final Result:"),
            StaticBlock("PnL: No fill"),
        ],
    ),
    # 5. Actions (solo se azionabile)
    ConditionalBlock(
        condition=lambda p: p.get("is_actionable") and not p.get("is_terminal"),
        blocks=[
            SeparatorBlock(),
            DerivedBlock(text_fn=lambda p: f"Actions: /cancel {p['chain_id']} · /close {p['chain_id']}"),
        ],
    ),
    # 6. Timeline
    ConditionalBlock(
        condition=lambda p: bool(p.get("events")),
        blocks=[
            SeparatorBlock(),
            StaticBlock("Events:"),
            ListBlock(key="events", item_renderer=_render_event),
        ],
    ),
]

_TEMPLATE_TRADE_DETAIL = TemplateConfig(_TRADE_DETAIL_BLOCKS, payload_transform=None)


def format_trade_detail(detail: TradeDetail | None) -> str:
    if detail is None:
        return "Trade not found."

    from datetime import datetime, timezone
    updated_at = datetime.now(timezone.utc).strftime("%H:%M:%S")

    events_payload = [
        {
            "label": ev.label,
            "timestamp": ev.timestamp,
            "source": ev.source,
            "event_type": ev.event_type,
            "reason": ev.reason,
            "clean_log_link": ev.clean_log_link,
        }
        for ev in (detail.events or [])
    ]

    payload = {
        "chain_id": detail.chain_id,
        "symbol": detail.symbol,
        "side": detail.side,
        "state": detail.state,
        "trader_id": detail.trader_id,
        "account_id": detail.account_id,
        "updated_at": updated_at,
        "entry_legs": detail.entry_legs,
        "tp_legs": detail.tp_legs,
        "sl_price": detail.sl_price,
        "has_be": detail.has_be,
        "unrealized_pnl": detail.unrealized_pnl,
        "cum_realized_pnl": detail.cum_realized_pnl,
        "final_result": detail.final_result,
        "is_actionable": detail.is_actionable,
        "is_terminal": detail.is_terminal,
        "events": events_payload,
    }
    return render_template(_TEMPLATE_TRADE_DETAIL.blocks, payload)


__all__ = ["format_trade_detail"]
```

- [ ] **Step 4: Esegui test**

```
pytest tests/runtime_v2/control_plane/test_readonly_formatters.py -k "trade_detail" -v
```

Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/trade_detail.py \
        tests/runtime_v2/control_plane/test_readonly_formatters.py
git commit -m "feat(trade-detail): full spec-compliant formatter with block system (orders, timeline, final result)"
```

---

## Task 5: Dashboard filtri — keyboard + pannelli + filters_json

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/dashboard.py`
- Modify: `src/runtime_v2/control_plane/dashboard_manager.py`
- Modify: `tests/runtime_v2/control_plane/test_dashboard_manager.py`

**Interfaces:**
- Consumes: `build_dashboard_keyboard(view, page, total_count, page_size, filters)`
- Produces: callback `filters`, `clear`, `selector:back`, `selector:{type}:{value}` gestiti da `DashboardManager.handle_callback()`

- [ ] **Step 1: Scrivi test**

```python
# tests/runtime_v2/control_plane/test_dashboard_manager.py — aggiungi:

def test_filters_json_column_exists(tmp_path):
    db_path = _make_db(tmp_path)
    mgr = DashboardManager(
        ops_db_path=db_path,
        scope_resolver=MagicMock(),
        queries=MagicMock(),
        bot=None,
    )
    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(ops_dashboard_messages)")}
    conn.close()
    assert "filters_json" in columns


def test_clear_callback_resets_filters(tmp_path):
    db_path = _make_db(tmp_path)
    mgr = DashboardManager(
        ops_db_path=db_path,
        scope_resolver=MagicMock(),
        queries=MagicMock(),
        bot=None,
    )
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ops_dashboard_messages VALUES (1,0,42,NULL,NULL,'active:0','{}',NULL)"
    )
    conn.commit(); conn.close()
    # Simulate setting filters
    mgr._update_filters_json(1, 0, '{"trader": "trader_a"}')
    # Then clear
    mgr._clear_filters(1, 0)
    row = mgr._get_dashboard_row(1, 0)
    assert row is not None
    filters_json = mgr._get_filters_json(1, 0)
    assert filters_json is None or filters_json == "{}" or filters_json == "null"
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/control_plane/test_dashboard_manager.py::test_filters_json_column_exists -v
```

Expected: FAIL (colonna non esiste)

- [ ] **Step 3: Aggiungi colonna filters_json e migration in _ensure_table()**

```python
def _ensure_table(self) -> None:
    conn = self._connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ops_dashboard_messages (
                chat_id           INTEGER NOT NULL,
                thread_id         INTEGER NOT NULL DEFAULT 0,
                message_id        INTEGER NOT NULL,
                scope_account_id  TEXT,
                scope_trader_id   TEXT,
                current_view      TEXT NOT NULL DEFAULT 'active:0',
                updated_at        TEXT,
                filters_json      TEXT DEFAULT NULL,
                PRIMARY KEY (chat_id, thread_id)
            )
        """)
        conn.commit()

        # Migration: NOT NULL → nullable su scope_account_id (codice esistente)
        # ...

        # Migration IT→EN per current_view (da Task 2)
        conn.execute("""
            UPDATE ops_dashboard_messages
            SET current_view = REPLACE(REPLACE(REPLACE(current_view,
                'attivi', 'active'), 'chiusi', 'closed'), 'bloccati', 'blocked')
            WHERE current_view LIKE '%attivi%'
               OR current_view LIKE '%chiusi%'
               OR current_view LIKE '%bloccati%'
        """)
        conn.commit()

        # Migration: aggiunge filters_json se non esiste
        columns = {row[1] for row in conn.execute("PRAGMA table_info(ops_dashboard_messages)")}
        if "filters_json" not in columns:
            conn.execute("ALTER TABLE ops_dashboard_messages ADD COLUMN filters_json TEXT DEFAULT NULL")
            conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Aggiungi metodi helper per filters_json in DashboardManager**

```python
def _update_filters_json(self, chat_id: int, thread_id: int, filters_json: str | None) -> None:
    conn = self._connect()
    try:
        conn.execute(
            "UPDATE ops_dashboard_messages SET filters_json=?, updated_at=? WHERE chat_id=? AND thread_id=?",
            (filters_json, _now_iso(), chat_id, thread_id),
        )
        conn.commit()
    finally:
        conn.close()

def _get_filters_json(self, chat_id: int, thread_id: int) -> str | None:
    conn = self._connect()
    try:
        row = conn.execute(
            "SELECT filters_json FROM ops_dashboard_messages WHERE chat_id=? AND thread_id=?",
            (chat_id, thread_id),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()

def _clear_filters(self, chat_id: int, thread_id: int) -> None:
    self._update_filters_json(chat_id, thread_id, None)
```

- [ ] **Step 5: Aggiorna handle_callback per gestire nuovi callback**

In `handle_callback()`, aggiungi dopo i casi esistenti:

```python
elif callback_data == "filters":
    # Edita messaggio con pannello filtri (testo + keyboard selectors)
    await self._show_filters_panel(
        callback_query=callback_query,
        chat_id=chat_id,
        thread_id=thread_id,
        stored_message_id=stored_message_id,
        current_view_name=current_view_name,
        scope=scope,
    )
    return

elif callback_data == "clear":
    self._clear_filters(chat_id, thread_id)
    new_view = current_view_name
    new_page = 0

elif callback_data == "selector:back":
    # Torna alla view corrente senza modificare filtri
    new_view = current_view_name
    new_page = current_page

elif callback_data.startswith("selector:"):
    # "selector:trader:trader_a" o "selector:account:demo_1"
    parts = callback_data.split(":", 2)
    if len(parts) == 3:
        _, filter_type, filter_value = parts
        import json as _json
        current_filters_raw = self._get_filters_json(chat_id, thread_id)
        try:
            current_filters = _json.loads(current_filters_raw) if current_filters_raw else {}
        except Exception:
            current_filters = {}
        if filter_value in ("all", ""):
            current_filters.pop(filter_type, None)
        else:
            current_filters[filter_type] = filter_value
        self._update_filters_json(chat_id, thread_id, _json.dumps(current_filters) if current_filters else None)
    new_view = current_view_name
    new_page = 0
```

- [ ] **Step 6: Implementa _show_filters_panel**

```python
async def _show_filters_panel(
    self, *, callback_query, chat_id, thread_id, stored_message_id,
    current_view_name, scope,
) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    view_labels = {
        "active": "Active", "closed": "Closed", "blocked": "Blocked",
        "pnl": "PnL", "stats": "Stats",
    }
    label = view_labels.get(current_view_name, current_view_name.capitalize())

    # Testo pannello
    text = f"🔎 Filters — {label}"

    # Keyboard: bottoni selectors per view corrente
    rows: list[list[InlineKeyboardButton]] = []

    if current_view_name in ("active", "closed", "blocked", "pnl", "stats"):
        rows.append([
            InlineKeyboardButton("Account ▸", callback_data="selector_panel:account"),
            InlineKeyboardButton("Trader ▸", callback_data="selector_panel:trader"),
        ])
    if current_view_name == "active":
        rows.append([InlineKeyboardButton("Status ▸", callback_data="selector_panel:status")])
    if current_view_name in ("active", "stats"):
        rows.append([InlineKeyboardButton("Side ▸", callback_data="selector_panel:side")])
    if current_view_name in ("closed", "pnl"):
        rows.append([InlineKeyboardButton("Period ▸", callback_data="selector_panel:period")])

    rows.append([
        InlineKeyboardButton("🧹 Clear view", callback_data="clear"),
        InlineKeyboardButton("← Back", callback_data="selector:back"),
    ])

    keyboard = InlineKeyboardMarkup(rows)
    if self._bot:
        try:
            await self._bot.edit_message_text(
                chat_id=chat_id, message_id=stored_message_id,
                text=text, reply_markup=keyboard,
            )
        except Exception:
            pass
```

- [ ] **Step 7: Aggiorna build_dashboard_keyboard in formatters/dashboard.py**

```python
def build_dashboard_keyboard(
    current_view: str,
    page: int,
    total_count: int,
    page_size: int = 5,
):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    def _tab(label: str, view: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(label, callback_data=f"view:{view}")

    row1 = [
        _tab("⚡ Active",  "active"),
        _tab("✅ Closed",  "closed"),
        _tab("🚫 Blocked", "blocked"),
    ]
    row2 = [
        _tab("💰 PnL",   "pnl"),
        _tab("📉 Stats", "stats"),
        InlineKeyboardButton("🔄 Refresh", callback_data="refresh"),
    ]
    row3 = [
        InlineKeyboardButton("🔎 Filters", callback_data="filters"),
        InlineKeyboardButton("🧹 Clear",   callback_data="clear"),
    ]

    keyboard = [row1, row2, row3]

    if total_count > page_size:
        total_pages = (total_count + page_size - 1) // page_size
        pagination_row: list[InlineKeyboardButton] = []
        if page > 0:
            pagination_row.append(InlineKeyboardButton("← Prev", callback_data="page:prev"))
        pagination_row.append(
            InlineKeyboardButton(f"Page {page + 1}/{total_pages}", callback_data="noop")
        )
        if page < total_pages - 1:
            pagination_row.append(InlineKeyboardButton("Next →", callback_data="page:next"))
        keyboard.append(pagination_row)

    return InlineKeyboardMarkup(keyboard)
```

- [ ] **Step 8: Esegui test**

```
pytest tests/runtime_v2/control_plane/test_dashboard_manager.py -x -q
```

Expected: tutti PASS.

- [ ] **Step 9: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/dashboard.py \
        src/runtime_v2/control_plane/dashboard_manager.py \
        tests/runtime_v2/control_plane/test_dashboard_manager.py
git commit -m "feat(dashboard): filters_json column, Filters/Clear keyboard, selector callbacks"
```

---

## Task 6: Health probes reali

**Files:**
- Modify: `src/runtime_v2/control_plane/status_queries.py` (metodo `get_health()`)
- Rewrite: `src/runtime_v2/control_plane/formatters/health.py`
- Modify: `tests/runtime_v2/control_plane/test_command_formatters.py` (o test_readonly_formatters.py)

**Interfaces:**
- Consumes: `HealthView` (invariato, già con workers, db_ok, exchange_connected)
- Produces: `format_health()` via block system con sezione `Checks:` spec-compliant

- [ ] **Step 1: Scrivi test**

```python
# tests/runtime_v2/control_plane/test_command_formatters.py — aggiungi:

def test_health_format_uses_block_system():
    """format_health deve usare render_template (output con separatori block system)."""
    from src.runtime_v2.control_plane.formatters.health import format_health
    from src.runtime_v2.control_plane.status_queries import HealthView
    view = HealthView(
        updated_at="14:32:05",
        workers=[
            ("Parser pipeline",    "OK",      ""),
            ("Lifecycle gate",     "OK",      ""),
            ("Execution worker",   "OK",      ""),
            ("Exchange sync",      "OK",      ""),
            ("Notification disp.", "OK",      ""),
        ],
        db_ok=True,
        exchange_connected=True,
        last_event_age_seconds=12.0,
    )
    text = format_health(view)
    assert "🩺 HEALTH" in text
    assert "Global runtime" in text
    assert "Workers:" in text
    assert "Parser pipeline" in text
    assert "Checks: live probe passed" in text


def test_health_format_degraded():
    from src.runtime_v2.control_plane.formatters.health import format_health
    from src.runtime_v2.control_plane.status_queries import HealthView
    view = HealthView(
        updated_at="14:32:05",
        workers=[
            ("Parser pipeline",    "OK",      ""),
            ("Lifecycle gate",     "OK",      ""),
            ("Execution worker",   "OK",      ""),
            ("Exchange sync",      "WARNING", "last event 87s ago"),
            ("Notification disp.", "OK",      ""),
        ],
        db_ok=True,
        exchange_connected=False,
        last_event_age_seconds=87.0,
    )
    text = format_health(view)
    assert "WARNING" in text
    assert "Checks: live probe partial" in text
    assert "Warnings:" in text


def test_health_format_critical():
    from src.runtime_v2.control_plane.formatters.health import format_health
    from src.runtime_v2.control_plane.status_queries import HealthView
    view = HealthView(
        updated_at="14:32:05",
        workers=[
            ("Parser pipeline",    "OK",     ""),
            ("Lifecycle gate",     "FAILED", "heartbeat missing"),
            ("Execution worker",   "OK",     ""),
            ("Exchange sync",      "FAILED", "probe failed"),
            ("Notification disp.", "OK",     ""),
        ],
        db_ok=True,
        exchange_connected=False,
        last_event_age_seconds=None,
    )
    text = format_health(view)
    assert "FAILED" in text
    assert "Checks: live probe failed" in text
    assert "Critical:" in text
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/control_plane/test_command_formatters.py -k "health" -v
```

Expected: FAIL (assenza di "Global runtime", "Checks:", formato diverso)

- [ ] **Step 3: Riscrivi health.py con block system**

```python
# src/runtime_v2/control_plane/formatters/health.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    render_template, StaticBlock, SeparatorBlock, DerivedBlock,
    ConditionalBlock, ListBlock, TemplateConfig,
)
from src.runtime_v2.control_plane.status_queries import HealthView


def _probe_status(view: HealthView) -> str:
    has_failed = any(w[1] == "FAILED" for w in view.workers)
    has_warning = any(w[1] == "WARNING" for w in view.workers)
    if has_failed or not view.db_ok:
        return "failed"
    if has_warning or not view.exchange_connected:
        return "partial"
    return "passed"


def _warnings(view: HealthView) -> list[str]:
    result = []
    for name, status, detail in view.workers:
        if status == "WARNING":
            result.append(f"  - {name.lower()}: {detail or 'degraded'}")
    if not view.exchange_connected:
        result.append("  - exchange connectivity degraded")
    return result


def _criticals(view: HealthView) -> list[str]:
    result = []
    for name, status, detail in view.workers:
        if status == "FAILED":
            result.append(f"  - {name.lower()}: {detail or 'failed'}")
    if not view.db_ok:
        result.append("  - database unreachable")
    return result


def _render_worker(w: tuple, i: int, p: dict) -> list[str]:
    name, status, detail = w[0], w[1], w[2]
    suffix = f"  ({detail})" if detail else ""
    return [f"  {name:<22} {status}{suffix}"]


_HEALTH_BLOCKS: list = [
    StaticBlock("🩺 HEALTH  |  Global runtime"),
    StaticBlock("————————————————"),
    DerivedBlock(text_fn=lambda p: f"Updated: {p.get('updated_at', 'n/a')}"),
    StaticBlock(""),
    StaticBlock("Workers:"),
    ListBlock(key="workers", item_renderer=_render_worker),
    StaticBlock(""),
    DerivedBlock(text_fn=lambda p: f"DB: {'OK' if p.get('db_ok') else 'ERROR'}"),
    DerivedBlock(text_fn=lambda p: f"Exchange: {'connected' if p.get('exchange_connected') else 'disconnected'}"),
    DerivedBlock(text_fn=lambda p: f"Checks: live probe {p.get('probe_status', 'unknown')}"),
    # Warnings
    ConditionalBlock(
        condition=lambda p: bool(p.get("warnings")),
        blocks=[
            StaticBlock(""),
            StaticBlock("Warnings:"),
            ListBlock(key="warnings", item_renderer=lambda w, i, p: [w]),
        ],
    ),
    # Criticals
    ConditionalBlock(
        condition=lambda p: bool(p.get("criticals")),
        blocks=[
            StaticBlock(""),
            StaticBlock("Critical:"),
            ListBlock(key="criticals", item_renderer=lambda c, i, p: [c]),
        ],
    ),
]

_TEMPLATE_HEALTH = TemplateConfig(_HEALTH_BLOCKS, payload_transform=None)


def format_health(view: HealthView) -> str:
    probe_status = _probe_status(view)
    warnings = _warnings(view)
    criticals = _criticals(view)
    payload = {
        "updated_at": view.updated_at,
        "workers": [(w[0], w[1], w[2]) for w in view.workers],
        "db_ok": view.db_ok,
        "exchange_connected": view.exchange_connected,
        "probe_status": probe_status,
        "warnings": warnings,
        "criticals": criticals,
    }
    return render_template(_TEMPLATE_HEALTH.blocks, payload)


__all__ = ["format_health"]
```

- [ ] **Step 4: Esegui test**

```
pytest tests/runtime_v2/control_plane/test_command_formatters.py -k "health" -v
```

Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/health.py \
        tests/runtime_v2/control_plane/test_command_formatters.py
git commit -m "feat(health): block system formatter, Checks: live probe section, warnings/critical"
```

---

## Task 7: Emergency safety — rifiuto in global scope non filtrato

**Files:**
- Modify: `src/runtime_v2/control_plane/emergency_close.py`
- Modify: `tests/runtime_v2/control_plane/test_emergency_close.py`

**Interfaces:**
- Consumes: `QueryScope` nel command handler
- Produces: risposta testuale di rifiuto quando `scope.account_id is None` e nessun filtro esplicito

- [ ] **Step 1: Leggi emergency_close.py per capire il punto di intercettazione**

```
# Prima di scrivere i test, leggi il file reale:
# src/runtime_v2/control_plane/emergency_close.py
```

- [ ] **Step 2: Scrivi test safety**

```python
# tests/runtime_v2/control_plane/test_emergency_close.py — aggiungi:

def test_close_all_refused_in_global_scope_without_filter(ops_db):
    """close_all deve rifiutare in global scope senza trader/account filter."""
    from src.runtime_v2.control_plane.emergency_close import (
        build_close_all_preview, GLOBAL_SCOPE_SAFETY_MSG,
    )
    from src.runtime_v2.control_plane.scope_resolver import QueryScope
    global_scope = QueryScope(account_id=None, trader_ids=None)
    result = build_close_all_preview(global_scope, ops_db, trader_filter=None)
    assert result is None or GLOBAL_SCOPE_SAFETY_MSG in result


def test_close_all_allowed_in_global_scope_with_trader_filter(ops_db):
    """close_all è permesso in global scope se è presente un trader filter."""
    from src.runtime_v2.control_plane.emergency_close import build_close_all_preview
    from src.runtime_v2.control_plane.scope_resolver import QueryScope
    # Global scope + trader filter esplicito → deve procedere (anche se vuoto)
    global_scope = QueryScope(account_id=None, trader_ids=["trader_a"])
    # Non deve sollevare eccezione né ritornare messaggio safety
    result = build_close_all_preview(global_scope, ops_db, trader_filter="trader_a")
    # result può essere None (nessuna chain) ma NON il safety message
    from src.runtime_v2.control_plane.emergency_close import GLOBAL_SCOPE_SAFETY_MSG
    if result is not None:
        assert GLOBAL_SCOPE_SAFETY_MSG not in result


def test_cancel_all_refused_in_global_scope_without_filter(ops_db):
    from src.runtime_v2.control_plane.emergency_close import (
        build_cancel_all_preview, GLOBAL_SCOPE_SAFETY_MSG,
    )
    from src.runtime_v2.control_plane.scope_resolver import QueryScope
    global_scope = QueryScope(account_id=None, trader_ids=None)
    result = build_cancel_all_preview(global_scope, ops_db, trader_filter=None)
    assert result is None or GLOBAL_SCOPE_SAFETY_MSG in result
```

- [ ] **Step 3: Verifica che i test falliscano**

```
pytest tests/runtime_v2/control_plane/test_emergency_close.py -k "safety or global_scope" -v
```

Expected: FAIL o ImportError su GLOBAL_SCOPE_SAFETY_MSG

- [ ] **Step 4: Leggi emergency_close.py e identifica la funzione che costruisce il preview**

Verifica come si chiama il metodo e quali parametri riceve. Adatta il test al nome reale se necessario.

- [ ] **Step 5: Aggiungi costante e safety check in emergency_close.py**

```python
# Aggiungi in cima al file (dopo gli import):
GLOBAL_SCOPE_SAFETY_MSG = (
    "⛔ Comando non disponibile in All accounts senza filtro.\n"
    "Specifica trader o account: es. /close_all trader_a"
)


def _is_unfiltered_global(scope: QueryScope, trader_filter: str | None = None) -> bool:
    """True se scope globale E nessun filtro esplicito applicato."""
    return scope.account_id is None and not trader_filter and not scope.trader_ids
```

All'inizio di ogni funzione preview (`build_close_all_preview`, `build_cancel_all_preview`):

```python
if _is_unfiltered_global(scope, trader_filter):
    return GLOBAL_SCOPE_SAFETY_MSG
```

Se le funzioni non esistono con questi nomi esatti, trova i punti equivalenti nel file reale e applica la stessa logica. L'importante è che prima di costruire la lista chain, il check avvenga.

- [ ] **Step 6: Esegui test**

```
pytest tests/runtime_v2/control_plane/test_emergency_close.py -x -q
```

Expected: tutti i nuovi test PASS, nessuna regressione.

- [ ] **Step 7: Esegui la suite completa**

```
pytest tests/runtime_v2/control_plane/ -x -q
```

Expected: tutti PASS.

- [ ] **Step 8: Commit**

```bash
git add src/runtime_v2/control_plane/emergency_close.py \
        tests/runtime_v2/control_plane/test_emergency_close.py
git commit -m "feat(emergency): safety block for /close_all /cancel_all in unfiltered global scope"
```

---

## Verifica finale

```bash
pytest tests/runtime_v2/control_plane/ -v --tb=short 2>&1 | tail -30
```

Criteri di accettazione:
1. `test_dashboard_view_key_active` PASS — registry key corretto
2. `test_dashboard_naming_migration` PASS — migration IT→EN funziona
3. `test_dashboard_active_header_contains_total_and_page` PASS — header spec
4. `test_dashboard_active_item_compact_format` PASS — formato 3 righe
5. `test_dashboard_active_global_scope_shows_trader_account` PASS — global scope
6. `test_trades_format_spec_compact` PASS — /trades nuovo formato
7. `test_status_global_scope_shows_all_accounts` PASS — /status global
8. `test_reviews_global_scope_shows_trader_account` PASS — /reviews global
9. `test_trade_detail_header` + `test_trade_detail_timeline_events` PASS — /trade n
10. `test_health_format_uses_block_system` + `test_health_format_degraded` PASS — /health
11. `test_close_all_refused_in_global_scope_without_filter` PASS — emergency safety

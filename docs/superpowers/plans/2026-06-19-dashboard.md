# Piano 3 — Dashboard (`/dashboard`, 5 viste, paginazione, auto-refresh)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementare il dashboard inline pinnabile: creato da `/dashboard`, aggiornato in-place su ogni cambio stato trade nel scope, con 5 viste navigabili (Attivi, Chiusi, Bloccati, PnL, Stats) e paginazione a 5 trade per pagina.

**Architecture:** `DashboardManager` centralizza creazione, aggiornamento, e auto-refresh. Legge `ops_dashboard_messages` per sapere quali messaggi aggiornare. Viene invocato: (a) da `CommandRouter` su `/dashboard`; (b) da `TelegramNotificationDispatcher` dopo ogni CLEAN_LOG inviato (tramite callback opzionale); (c) da `TelegramControlBot` su click keyboard. Throttle 5s per messaggio. I template vivono in `DASHBOARD_REGISTRY` separato.

**Tech Stack:** Python 3.11+, SQLite, python-telegram-bot (esistenti).

---

## ⚠️ Correzioni post-revisione (2026-06-19)

Verifica contro schema reale e `status_queries.py`/`notification_dispatcher.py`:

1. **Vista Bloccati — `review_reason` NON esiste** come colonna. La query in Task 2
   (`get_trades_bloccati`) che fa `SELECT ..., review_reason, ...` crasha in produzione.
   Il motivo va letto da `ops_lifecycle_events.payload_json` (come `get_reviews()`). Vedi
   Task 2 Step 5 corretto.
2. **`EXEC_FAILED` non è un `lifecycle_state`** valido (stati reali: OPEN, PARTIALLY_CLOSED,
   WAITING_ENTRY, REVIEW_REQUIRED, BE_MOVE_PENDING, PROTECTED_BE, CLOSED). Filtrare
   `ops_trade_chains` per `EXEC_FAILED` non restituisce nulla. La spec vuole i **comandi
   falliti** da `ops_execution_commands WHERE status='FAILED'` (con `result_payload_json`
   come motivo) — è una query separata, non un `lifecycle_state`. Vedi Task 2 Step 5.
3. **`closed_at` non esiste** → fallback PRAGMA a `updated_at` (già previsto, mantenere).
4. **Fixture di test dallo schema reale** (`db/ops_migrations/*.sql`), non inventate.
5. **PnL non-realizzato** nella vista Attivi è fissato a `None` ("non disponibile"),
   ma spec/header promettono "Snapshot: Ns fa" + PnL. O si legge da
   `ops_position_snapshots(payload_json, captured_at)`, o si declassa a gap esplicito
   nella spec. Decidere prima di implementare.

---

## Dipendenze da Piani precedenti

- Piano 1 Task 1: `TableBlock`, `SectionBlock` callable
- Piano 1 Task 2: `QueryScope`, `ScopeResolver`
- Piano 1 Task 4: `get_stats()`, `get_pnl()` con scope
- Piano 1 Task 8: `/dashboard` placeholder (da sostituire)
- Piano 2: `CallbackQueryHandler` già registrato in PTB app (da estendere per prefisso `"dash:"`)

## Global Constraints

- Un solo messaggio dashboard per `(chat_id, thread_id)` — upsert su ricreazione
- `current_view` formato: `"vista:pagina"` — es. `"attivi:0"`, `"chiusi:2"`
- Paginazione: 5 trade per pagina, terza riga keyboard solo se totale > 5
- Reset pagina a 0 su cambio vista
- Throttle edit: min 5s tra edit successivi sullo stesso `(chat_id, thread_id)` — edit schedulata, non scartata
- `MessageNotModified` da Telegram gestita silenziosamente (contenuto invariato)
- `/dashboard` da tech_log topic → risposta "comando non disponibile"
- Auto-refresh scope: se il trade aggiornato è nel scope di un dashboard → refresh quel dashboard
- `thread_id=0` = `nessun thread` nel DB (private_bot mode)
- `display_symbol()` per tutti i simboli
- Separatore `- - - - -` = `SeparatorBlock()` già esistente

---

## File Structure

**Nuovi file:**
- `src/runtime_v2/control_plane/dashboard_manager.py` — `DashboardManager`
- `src/runtime_v2/control_plane/formatters/dashboard.py` — `format_dashboard_view()`
- `src/runtime_v2/control_plane/formatters/templates/dashboard.py` — `DASHBOARD_REGISTRY`
- `tests/runtime_v2/control_plane/test_dashboard_manager.py`
- `tests/runtime_v2/control_plane/test_dashboard_templates.py`

**File modificati:**
- `src/runtime_v2/control_plane/status_queries.py` — nuove query: `get_trades_attivi`, `get_trades_chiusi_paginated`, `get_trades_bloccati`, `DashboardPnlView`, `DashboardTradeRow`
- `src/runtime_v2/control_plane/service.py` — delegate nuove query
- `src/runtime_v2/control_plane/notification_dispatcher.py` — `lifecycle_callback` opzionale
- `src/runtime_v2/control_plane/telegram_bot.py` — `/dashboard` reale, routing callback `"dash:"`
- `src/runtime_v2/control_plane/bootstrap.py` — `DashboardManager` costruito e iniettato
- DB migration: `ops_dashboard_messages` table

---

## Task 0: Pre-flight — verifica colonne DB per Chiusi e Bloccati

**Files:** nessuno (solo verifica)

La struttura JSON dei campi di piano è già nota dal codice (`ExecutionPlanBuilder.build()` in `src/runtime_v2/lifecycle/execution_plan.py`):

- **`plan_state_json`** — struttura runtime del piano:
  ```json
  {
    "legs": [{"price": 63500.0, "status": "PENDING|FILLED|CANCELLED", "sequence": 1, ...}],
    "stop_loss": 62000.0,
    "final_tp": 65000.0,
    "intermediate_tps": [64000.0, 64500.0]
  }
  ```
  Entry legs → `plan["legs"]`, ognuna con `price` e `status`.
  TP → `plan["intermediate_tps"]` + `plan["final_tp"]` come prezzi flat **senza status individuale**.

- **`management_plan_json`** — serializzato da `ManagementPlanConfig`: solo `be_trigger` e `be_fee_correction_enabled`. **Non contiene entry né TP.** Non usato in questo piano.

Task 0 verifica solo le colonne DB opzionali per Chiusi e Bloccati.

- [ ] **Step 1: Verificare colonne `ops_trade_chains`**

```bash
python -c "
import sqlite3
conn = sqlite3.connect('path/to/ops.db')
cols = [r[1] for r in conn.execute('PRAGMA table_info(ops_trade_chains)').fetchall()]
print(cols)
"
```

Cercare: `closed_at`, `cumulative_gross_pnl`, `cumulative_fees`, `cumulative_funding`, `review_reason`.

Se `closed_at` è assente, la query Chiusi usa `updated_at` come fallback (già gestito in Task 2 con PRAGMA runtime). Se `cumulative_gross_pnl` è assente, il PnL chiusi mostra `NULL` (già gestito).

- [ ] **Step 2: (corretto) `EXEC_FAILED` NON è un lifecycle_state**

`EXEC_FAILED` non esiste come stato di `ops_trade_chains`. La vista Bloccati unisce due
fonti (vedi Task 2 Step 5 corretto):
- `ops_trade_chains WHERE lifecycle_state='REVIEW_REQUIRED'` (motivo da `ops_lifecycle_events`);
- `ops_execution_commands WHERE status='FAILED'` (motivo da `command_type` + `result_payload_json`).

Verificare gli stati realmente presenti solo per conferma:

```bash
python -c "
import sqlite3
conn = sqlite3.connect('path/to/ops.db')
print(conn.execute('SELECT DISTINCT lifecycle_state FROM ops_trade_chains').fetchall())
"
```

---

## Task 1: DB migration — `ops_dashboard_messages`

**Files:**
- Create: `src/runtime_v2/control_plane/migrations/add_ops_dashboard_messages.sql`

- [ ] **Step 1: Scrivere la migration SQL**

```sql
-- src/runtime_v2/control_plane/migrations/add_ops_dashboard_messages.sql
CREATE TABLE IF NOT EXISTS ops_dashboard_messages (
    chat_id      INTEGER NOT NULL,
    thread_id    INTEGER NOT NULL DEFAULT 0,
    message_id   INTEGER NOT NULL,
    account_id   TEXT NOT NULL,
    trader_id    TEXT,              -- NULL = tutti i trader dell'account
    current_view TEXT NOT NULL DEFAULT 'attivi:0',
    updated_at   TEXT,
    PRIMARY KEY (chat_id, thread_id)
);
```

- [ ] **Step 2: Verificare come vengono applicate le migration nel progetto**

Run: `rg "CREATE TABLE" src/runtime_v2/ --type py -l`
Run: `rg "migration|migrate|schema" src/runtime_v2/ --type py -l`

Se il progetto usa uno script di init schema (es. `ops_db_init.py` o simile), aggiungere il CREATE TABLE lì. Se usa file `.sql` standalone, applicare manualmente su dev con:

```bash
sqlite3 path/to/ops.db < src/runtime_v2/control_plane/migrations/add_ops_dashboard_messages.sql
```

- [ ] **Step 3: Commit**

```bash
git add src/runtime_v2/control_plane/migrations/add_ops_dashboard_messages.sql
git commit -m "feat: add ops_dashboard_messages migration for dashboard message tracking"
```

---

## Task 2: Nuove query `status_queries.py` — viste dashboard

**Files:**
- Modify: `src/runtime_v2/control_plane/status_queries.py`

**Interfaces:**
- Produces:
  - `DashboardTradeRow` — trade con entry/TP legs, PnL, link
  - `ClosedTradeRow` — trade chiuso con timestamps, PnL, links, durata
  - `BlockedTradeRow` — trade bloccato con motivo, timestamp, link
  - `StatusQueries.get_trades_attivi(scope, page, per_page) -> tuple[list[DashboardTradeRow], int]`
  - `StatusQueries.get_trades_chiusi(scope, page, per_page) -> tuple[list[ClosedTradeRow], int]`
  - `StatusQueries.get_trades_bloccati(scope) -> list[BlockedTradeRow]`
  - `StatusQueries.get_dashboard_pnl(scope) -> DashboardPnlView`

Nota: `get_stats()` e `get_pnl()` dalla Piano 1 sono riusati direttamente per le viste PnL e Stats del dashboard.

- [ ] **Step 1: Aggiungere dataclass**

```python
@dataclass
class EntryLeg:
    price: str
    status: str  # "filled" | "cancelled" | "pending"
    # Mappa da plan_state_json["legs"][i]["status"]: FILLED→filled, CANCELLED→cancelled, PENDING→pending

@dataclass
class DashboardTradeRow:
    chain_id: int
    symbol: str
    side: str
    state: str
    trader_id: str
    account_id: str
    entry_legs: list[EntryLeg]
    tp_prices: list[str]   # prezzi flat da intermediate_tps + final_tp; nessun status per TP
    sl_price: str | None
    has_be: bool
    pnl: float | None
    signal_link: str | None  # link segnale originale

@dataclass
class ClosedTradeRow:
    chain_id: int
    symbol: str
    side: str
    trader_id: str
    account_id: str
    opened_at: str | None
    closed_at: str | None
    duration_minutes: int | None
    net_pnl: float | None
    opened_link: str | None  # link segnale originale
    closed_link: str | None  # link messaggio chiusura

@dataclass
class BlockedTradeRow:
    chain_id: int
    symbol: str
    side: str
    state: str  # REVIEW_REQUIRED | EXEC_FAILED
    trader_id: str
    account_id: str
    motivo: str | None
    blocked_at: str | None
    link: str | None  # segnale originale (REVIEW_REQUIRED) o tech_log errore (EXEC_FAILED)

@dataclass
class DashboardPnlView:
    updated_at: str
    equity_usdt: float | None
    available_balance_usdt: float | None
    total_margin_used_usdt: float | None
    gross_pnl: float | None
    fees: float | None
    net_pnl: float | None
    open_count: int
    waiting_count: int
    snapshot_age_seconds: float | None  # None = no snapshot, >120 = stale
```

- [ ] **Step 2: Aggiungere helper `_parse_entry_legs` e `_parse_tp_prices`**

```python
def _fmt_price(raw) -> str:
    """Converti prezzo float in stringa senza decimali inutili (63500.0 → '63500')."""
    if raw is None:
        return ""
    try:
        f = float(raw)
        return str(int(f)) if f == int(f) else str(f)
    except Exception:
        return str(raw)


def _parse_entry_legs(plan_state_json: str | None) -> list[EntryLeg]:
    """Estrae entry legs da plan_state_json["legs"].

    Struttura da ExecutionPlanBuilder (src/runtime_v2/lifecycle/execution_plan.py):
      {"legs": [{"price": 63500.0, "status": "PENDING"|"FILLED"|"CANCELLED", ...}]}
    management_plan_json non viene usato — contiene solo config BE.
    """
    if not plan_state_json:
        return []
    try:
        plan = json.loads(plan_state_json)
        legs = []
        for leg in sorted(plan.get("legs") or [], key=lambda l: l.get("sequence", 0)):
            price = _fmt_price(leg.get("price"))
            raw_status = (leg.get("status") or "PENDING").upper()
            status = "filled" if raw_status == "FILLED" else "cancelled" if raw_status == "CANCELLED" else "pending"
            legs.append(EntryLeg(price=price, status=status))
        return legs
    except Exception:
        return []


def _parse_tp_prices(plan_state_json: str | None) -> list[str]:
    """Estrae prezzi TP da plan_state_json come lista flat (nessun status per singolo TP).

    Ritorna intermediate_tps (ordinati) + final_tp in coda.
    """
    if not plan_state_json:
        return []
    try:
        plan = json.loads(plan_state_json)
        prices = [_fmt_price(p) for p in (plan.get("intermediate_tps") or []) if p is not None]
        final = plan.get("final_tp")
        if final is not None:
            prices.append(_fmt_price(final))
        return prices
    except Exception:
        return []
```

- [ ] **Step 3: Implementare `get_trades_attivi()`**

```python
def get_trades_attivi(
    self, scope: "QueryScope", page: int = 0, per_page: int = 5
) -> tuple[list[DashboardTradeRow], int]:
    """Ritorna (rows per la pagina, totale). page è 0-based."""
    where, params = _scope_where(scope)
    conn = self._connect()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM ops_trade_chains "
            f"WHERE lifecycle_state IN ({','.join('?' * len(_ACTIVE_STATES))}) AND {where}",
            (*_ACTIVE_STATES, *params),
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT trade_chain_id, symbol, side, lifecycle_state, trader_id, account_id, "
            f"management_plan_json, plan_state_json, "
            f"COALESCE(current_stop_price, expected_stop_price), "
            f"be_protection_status, "
            f"source_chat_id, telegram_message_id "
            f"FROM ops_trade_chains "
            f"WHERE lifecycle_state IN ({','.join('?' * len(_ACTIVE_STATES))}) AND {where} "
            f"ORDER BY trade_chain_id "
            f"LIMIT ? OFFSET ?",
            (*_ACTIVE_STATES, *params, per_page, page * per_page),
        ).fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        entry_legs = _parse_entry_legs(r[7])       # solo plan_state_json
        tp_prices = _parse_tp_prices(r[7])          # flat prices, nessun status per TP
        signal_link = _build_telegram_message_link(r[10], r[11])
        result.append(DashboardTradeRow(
            chain_id=r[0], symbol=r[1], side=r[2], state=r[3],
            trader_id=r[4] or "", account_id=r[5] or "",
            entry_legs=entry_legs, tp_prices=tp_prices,
            sl_price=str(r[8]) if r[8] is not None else None,
            has_be=r[9] == "PROTECTED",
            pnl=None,  # PnL unrealizzato non disponibile in questo schema — None
            signal_link=signal_link,
        ))
    return result, total
```

- [ ] **Step 4: Implementare `get_trades_chiusi()`**

```python
def get_trades_chiusi(
    self, scope: "QueryScope", page: int = 0, per_page: int = 5
) -> tuple[list[ClosedTradeRow], int]:
    where, params = _scope_where(scope)
    conn = self._connect()
    try:
        # Determina colonna timestamp (Task 0)
        chain_cols = {r[1] for r in conn.execute("PRAGMA table_info(ops_trade_chains)").fetchall()}
        ts_col = "closed_at" if "closed_at" in chain_cols else "updated_at"
        pnl_expr = (
            "COALESCE(cumulative_gross_pnl,0) - COALESCE(cumulative_fees,0) + COALESCE(cumulative_funding,0)"
            if "cumulative_gross_pnl" in chain_cols else "NULL"
        )
        total = conn.execute(
            f"SELECT COUNT(*) FROM ops_trade_chains WHERE lifecycle_state='CLOSED' AND {where}",
            params,
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT trade_chain_id, symbol, side, trader_id, account_id, "
            f"created_at, {ts_col}, {pnl_expr}, "
            f"source_chat_id, telegram_message_id "
            f"FROM ops_trade_chains "
            f"WHERE lifecycle_state='CLOSED' AND {where} "
            f"ORDER BY {ts_col} DESC "
            f"LIMIT ? OFFSET ?",
            (*params, per_page, page * per_page),
        ).fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        opened_link = _build_telegram_message_link(r[8], r[9])
        # closed_link: da ops_clean_log_tracking (last message) — None se non disponibile
        duration = None
        if r[5] and r[6]:
            try:
                from datetime import datetime, timezone
                opened_dt = datetime.fromisoformat(r[5])
                closed_dt = datetime.fromisoformat(r[6])
                duration = int((closed_dt - opened_dt).total_seconds() / 60)
            except Exception:
                pass
        result.append(ClosedTradeRow(
            chain_id=r[0], symbol=r[1], side=r[2],
            trader_id=r[3] or "", account_id=r[4] or "",
            opened_at=r[5], closed_at=r[6],
            duration_minutes=duration,
            net_pnl=float(r[7]) if r[7] is not None else None,
            opened_link=opened_link,
            closed_link=None,  # TODO: fetch from ops_clean_log_tracking se utile
        ))
    return result, total
```

- [ ] **Step 5: Implementare `get_trades_bloccati()`**

`review_reason` **non esiste** come colonna: il motivo dei REVIEW_REQUIRED si legge da
`ops_lifecycle_events` (come `get_reviews()`). `EXEC_FAILED` **non è un lifecycle_state**:
i comandi falliti sono righe di `ops_execution_commands WHERE status='FAILED'`. Bloccati =
unione dei due insiemi.

```python
def get_trades_bloccati(self, scope: "QueryScope") -> list[BlockedTradeRow]:
    where, params = _scope_where(scope)
    conn = self._connect()
    try:
        # (a) REVIEW_REQUIRED da ops_trade_chains
        review_rows = conn.execute(
            f"SELECT trade_chain_id, symbol, side, updated_at, "
            f"source_chat_id, telegram_message_id, trader_id, account_id "
            f"FROM ops_trade_chains "
            f"WHERE lifecycle_state='REVIEW_REQUIRED' AND {where} "
            f"ORDER BY trade_chain_id",
            params,
        ).fetchall()
        # motivo da ops_lifecycle_events (ultimo REVIEW_REQUIRED per chain)
        reasons = dict(conn.execute(
            "SELECT trade_chain_id, payload_json FROM ops_lifecycle_events "
            "WHERE event_type='REVIEW_REQUIRED' AND trade_chain_id IS NOT NULL "
            "ORDER BY event_id DESC"
        ).fetchall())
        # (b) comandi falliti da ops_execution_commands (join su chain per scope/simbolo)
        failed_rows = conn.execute(
            f"SELECT c.trade_chain_id, t.symbol, t.side, c.updated_at, "
            f"t.source_chat_id, t.telegram_message_id, t.trader_id, t.account_id, "
            f"c.command_type, c.result_payload_json "
            f"FROM ops_execution_commands c "
            f"JOIN ops_trade_chains t ON t.trade_chain_id = c.trade_chain_id "
            f"WHERE c.status='FAILED' AND {where} "
            f"ORDER BY c.command_id DESC",
            params,
        ).fetchall()
    finally:
        conn.close()

    result: list[BlockedTradeRow] = []
    for r in review_rows:
        motivo = "unknown"
        blob = reasons.get(r[0])
        if blob:
            try:
                motivo = json.loads(blob).get("reason", "unknown")
            except Exception:
                pass
        result.append(BlockedTradeRow(
            chain_id=r[0], symbol=r[1], side=r[2], state="REVIEW_REQUIRED",
            trader_id=r[6] or "", account_id=r[7] or "",
            motivo=motivo, blocked_at=r[3],
            link=_build_telegram_message_link(r[4], r[5]),
        ))
    for r in failed_rows:
        motivo = r[8]  # command_type fallito
        try:
            err = json.loads(r[9] or "{}").get("error") or json.loads(r[9] or "{}").get("reason")
            if err:
                motivo = f"{r[8]}: {err}"
        except Exception:
            pass
        result.append(BlockedTradeRow(
            chain_id=r[0], symbol=r[1], side=r[2], state="EXEC_FAILED",
            trader_id=r[6] or "", account_id=r[7] or "",
            motivo=motivo, blocked_at=r[3],
            link=_build_telegram_message_link(r[4], r[5]),
        ))
    return result
```

Nota: `_scope_where(scope)` produce `account_id=? [AND trader_id IN (...)]`. Nella query (b)
le colonne `account_id`/`trader_id` provengono dal JOIN su `t.` — assicurarsi che
`_scope_where` qualifichi le colonne (`t.account_id`, `t.trader_id`) o costruire il
filtro con prefisso `t.` in questa query.

- [ ] **Step 6: Implementare `get_dashboard_pnl()`**

```python
def get_dashboard_pnl(self, scope: "QueryScope") -> DashboardPnlView:
    conn = self._connect()
    try:
        snap = None
        snap_ts = None
        if _table_exists(conn, "ops_account_snapshots"):
            snap = conn.execute(
                "SELECT captured_at, equity_usdt, available_balance_usdt, total_margin_used_usdt "
                "FROM ops_account_snapshots WHERE account_id=? ORDER BY captured_at DESC LIMIT 1",
                (scope.account_id,),
            ).fetchone()
            if snap:
                snap_ts = snap[0]

        where, params = _scope_where(scope)
        chain_cols = {r[1] for r in conn.execute("PRAGMA table_info(ops_trade_chains)").fetchall()}
        open_count = conn.execute(
            f"SELECT COUNT(*) FROM ops_trade_chains WHERE lifecycle_state='OPEN' AND {where}", params
        ).fetchone()[0]
        waiting_count = conn.execute(
            f"SELECT COUNT(*) FROM ops_trade_chains WHERE lifecycle_state='WAITING_ENTRY' AND {where}", params
        ).fetchone()[0]

        gross_pnl = fees = net_pnl = None
        if "cumulative_gross_pnl" in chain_cols:
            row = conn.execute(
                f"SELECT SUM(cumulative_gross_pnl), SUM(cumulative_fees), SUM(cumulative_funding) "
                f"FROM ops_trade_chains WHERE lifecycle_state='CLOSED' AND {where}", params
            ).fetchone()
            if row and row[0] is not None:
                gross_pnl = float(row[0])
                fees = float(row[1] or 0)
                net_pnl = gross_pnl - fees + float(row[2] or 0)
    finally:
        conn.close()

    return DashboardPnlView(
        updated_at=_now_iso(),
        equity_usdt=snap[1] if snap else None,
        available_balance_usdt=snap[2] if snap else None,
        total_margin_used_usdt=snap[3] if snap else None,
        gross_pnl=gross_pnl, fees=fees, net_pnl=net_pnl,
        open_count=open_count, waiting_count=waiting_count,
        snapshot_age_seconds=_age_seconds(snap_ts),
    )
```

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/control_plane/status_queries.py
git commit -m "feat: add dashboard-specific trade queries (attivi, chiusi, bloccati, pnl)"
```

---

## Task 3: `templates/dashboard.py` — DASHBOARD_REGISTRY

**Files:**
- Create: `src/runtime_v2/control_plane/formatters/templates/dashboard.py`
- Test: `tests/runtime_v2/control_plane/test_dashboard_templates.py`

**Interfaces:**
- Produces: `DASHBOARD_REGISTRY: dict[str, TemplateConfig]` con chiavi:
  - `"attivi"`, `"chiusi"`, `"bloccati"`, `"pnl"`, `"stats"`

Il payload di ogni template è già costruito dal formatter (Task 4). Tutti `payload_transform=None`.

- [ ] **Step 1: Creare `templates/dashboard.py`**

```python
# src/runtime_v2/control_plane/formatters/templates/dashboard.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    BranchBlock, ConditionalBlock, DerivedBlock, ListBlock,
    SectionBlock, SeparatorBlock, StaticBlock, TableBlock, TemplateConfig,
)


# ── header comune ─────────────────────────────────────────────────────────────

def _header_blocks(view_emoji: str) -> list:
    return [
        DerivedBlock(text_fn=lambda p, e=view_emoji: f"{e} DASHBOARD — {p['scope_label']}"),
        SeparatorBlock(),
        DerivedBlock(text_fn=lambda p: (
            f"{p['updated_at']}  |  Snapshot: {p['snapshot_age']}  {p.get('stale_warn', '')}"
            if p.get("snapshot_age") else p["updated_at"]
        )),
    ]


# ── /attivi ───────────────────────────────────────────────────────────────────

def _fmt_price_leg(price: str, status: str) -> str:
    if status == "filled":
        return f"{price}✓"
    if status == "cancelled":
        return f"{price}✗"
    return str(price)


def _attivi_trade_renderer(trade: dict, idx: int, payload: dict) -> list[str]:
    symbol = trade["symbol"]
    side = trade["side"]
    state = trade["state"]
    trader_tag = f"  [{trade['trader_id']}]" if payload.get("show_trader") and trade.get("trader_id") else ""
    lines = [f"#{trade['chain_id']}  {symbol}  {side}  {state}{trader_tag}"]

    # Entry legs (con status ✓/✗ — dati da plan_state_json["legs"])
    if trade.get("entry_legs"):
        entry_parts = [_fmt_price_leg(l["price"], l["status"]) for l in trade["entry_legs"]]
        if trade["state"] == "WAITING_ENTRY" and all(l["status"] == "pending" for l in trade["entry_legs"]):
            lines.append("    In attesa di riempimento")
        else:
            lines.append(f"    Entry: {' · '.join(entry_parts)}")
    # TP prices (flat — plan_state_json non ha status per singolo TP)
    if trade.get("tp_prices"):
        lines.append(f"    TP: {' · '.join(trade['tp_prices'])}")
    # SL e BE
    sl_str = f"SL: {trade['sl_price']}" if trade.get("sl_price") else ""
    be_str = "BE: ✓" if trade.get("has_be") else ""
    sl_be = "  ".join(filter(None, [sl_str, be_str]))
    if sl_be:
        lines.append(f"    {sl_be}")
    # PnL
    if trade.get("pnl") is not None:
        sign = "+" if trade["pnl"] > 0 else ""
        lines.append(f"    PnL: {sign}{trade['pnl']:.2f} USDT")
    # Link
    if trade.get("signal_link"):
        lines.append(f"    {trade['signal_link']}")

    return lines


_ATTIVI = TemplateConfig(blocks=[
    *_header_blocks("⚡"),
    BranchBlock(
        condition=lambda p: p["total"] == 0,
        then_blocks=[SeparatorBlock(), StaticBlock("Nessun trade attivo.")],
        else_blocks=[
            SeparatorBlock(),
            ListBlock(key="trades", item_renderer=_attivi_trade_renderer),
        ],
    ),
])


# ── /chiusi ───────────────────────────────────────────────────────────────────

def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return "n/d"
    # "2026-06-14 11:52:00" → "14 Jun 11:52"
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%-d %b %H:%M")
    except Exception:
        return ts[:16] if ts else "n/d"


def _fmt_duration(minutes: int | None) -> str:
    if minutes is None:
        return ""
    h, m = divmod(minutes, 60)
    return f"⏱ {h}h {m}m" if h > 0 else f"⏱ {m}m"


def _chiusi_trade_renderer(trade: dict, idx: int, payload: dict) -> list[str]:
    symbol = trade["symbol"]
    side = trade["side"]
    trader_tag = f"  [{trade['trader_id']}]" if payload.get("show_trader") and trade.get("trader_id") else ""
    lines = [f"#{trade['chain_id']}  {symbol}  {side}  CLOSED{trader_tag}"]
    lines.append(f"     Opened: {_fmt_ts(trade.get('opened_at'))}")
    if trade.get("opened_link"):
        lines.append(f"     {trade['opened_link']}")
    lines.append("__SEP__")  # SeparatorBlock sentinel
    lines.append(f"     Closed: {_fmt_ts(trade.get('closed_at'))}")
    if trade.get("closed_link"):
        lines.append(f"     {trade['closed_link']}")
    lines.append("__SEP__")
    pnl_str = ""
    if trade.get("net_pnl") is not None:
        sign = "+" if trade["net_pnl"] > 0 else ""
        pnl_str = f"PnL: {sign}{trade['net_pnl']:.2f} USDT"
    dur_str = _fmt_duration(trade.get("duration_minutes"))
    lines.append(f"     {pnl_str}   {dur_str}".rstrip())
    return lines
```

Nota: il renderer usa `"__SEP__"` direttamente come sentinel per i separatori interni. La funzione `_finalize()` li converte già. Questo è il pattern corretto per separatori dentro `ListBlock`.

```python
_CHIUSI = TemplateConfig(blocks=[
    *_header_blocks("✅"),
    BranchBlock(
        condition=lambda p: p["total"] == 0,
        then_blocks=[SeparatorBlock(), StaticBlock("Nessun trade chiuso.")],
        else_blocks=[
            SeparatorBlock(),
            ListBlock(key="trades", item_renderer=_chiusi_trade_renderer),
        ],
    ),
])


# ── /bloccati ─────────────────────────────────────────────────────────────────

def _fmt_blocked_ts(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%-d %b %H:%M")
    except Exception:
        return ts[:16] if ts else ""


def _bloccati_trade_renderer(trade: dict, idx: int, payload: dict) -> list[str]:
    symbol = trade["symbol"]
    side = trade["side"]
    state = trade["state"]
    lines = [f"#{trade['chain_id']}  {symbol}  {side}  {state}"]
    if trade.get("motivo"):
        lines.append(f"     Motivo: {trade['motivo']}")
    if trade.get("blocked_at"):
        lines.append(f"     {_fmt_blocked_ts(trade['blocked_at'])}")
    if trade.get("link"):
        lines.append(f"     {trade['link']}")
    return lines


_BLOCCATI = TemplateConfig(blocks=[
    *_header_blocks("🚫"),
    BranchBlock(
        condition=lambda p: p["total"] == 0,
        then_blocks=[SeparatorBlock(), StaticBlock("Nessun trade bloccato.")],
        else_blocks=[
            SeparatorBlock(),
            ListBlock(key="trades", item_renderer=_bloccati_trade_renderer),
        ],
    ),
])


# ── /pnl ─────────────────────────────────────────────────────────────────────

def _fmt_m(v: float | None) -> str:
    if v is None:
        return "n/a"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:>10,.2f} USDT"


_PNL = TemplateConfig(blocks=[
    *_header_blocks("💰"),
    SeparatorBlock(),
    SectionBlock(label="Account:", blocks=[
        DerivedBlock(text_fn=lambda p: f"  Equity:    {_fmt_m(p.get('equity_usdt'))}"),
        DerivedBlock(text_fn=lambda p: f"  Balance:   {_fmt_m(p.get('balance_usdt'))}"),
        DerivedBlock(text_fn=lambda p: f"  Margin:    {_fmt_m(p.get('margin_usdt'))}"),
    ]),
    SeparatorBlock(),
    SectionBlock(
        label=lambda p: f"Realizzato ({p['trader_label']}):",
        blocks=[
            DerivedBlock(text_fn=lambda p: f"  Gross:   {_fmt_m(p.get('gross_pnl'))}"),
            DerivedBlock(text_fn=lambda p: f"  Fees:    {_fmt_m(p.get('fees'))}"),
            DerivedBlock(text_fn=lambda p: f"  Netto:   {_fmt_m(p.get('net_pnl'))}"),
        ],
    ),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"Open: {p['open_count']}  |  Waiting: {p['waiting_count']}"),
])


# ── /stats ────────────────────────────────────────────────────────────────────

def _stats_rows(p: dict) -> list[list[str]]:
    def _pct(w, t): return f"{int(w/t*100)}%" if t else "—"
    def _pnl(v): return f"{v:+.2f}" if v is not None else "n/a"
    return [
        ["Oggi:", str(p["today_trades"]), _pct(p["today_wins"], p["today_trades"]), _pnl(p["today_pnl"])],
        ["7g:", str(p["week_trades"]), _pct(p["week_wins"], p["week_trades"]), _pnl(p["week_pnl"])],
        ["30g:", str(p["month_trades"]), _pct(p["month_wins"], p["month_trades"]), _pnl(p["month_pnl"])],
        ["Tot:", str(p["total_trades"]), _pct(p["total_wins"], p["total_trades"]), _pnl(p["total_pnl"])],
    ]


_STATS = TemplateConfig(blocks=[
    *_header_blocks("📉"),
    SeparatorBlock(),
    TableBlock(
        headers=["", "Trades", "Win%", "Netto"],
        rows_fn=_stats_rows,
        alignments=["left", "right", "right", "right"],
    ),
    ConditionalBlock(
        condition=lambda p: p.get("best_symbol") is not None,
        blocks=[
            SeparatorBlock(),
            DerivedBlock(text_fn=lambda p: f"Best:  #{p['best_id']}  {p['best_symbol']}  {p['best_pnl']:+.2f}"),
            DerivedBlock(text_fn=lambda p: f"Worst: #{p['worst_id']}  {p['worst_symbol']}  {p['worst_pnl']:+.2f}"),
        ],
    ),
])


# ── Keyboard helpers ──────────────────────────────────────────────────────────

def build_dashboard_keyboard(
    current_view: str,
    total_items: int,
    current_page: int,
    per_page: int = 5,
) -> object:
    """Costruisce InlineKeyboardMarkup per il dashboard.
    
    Riga 1: [⚡ Attivi] [✅ Chiusi] [🚫 Bloccati]
    Riga 2: [💰 PnL] [📉 Stats] [🔄 Refresh]
    Riga 3 (condizionale): [← Prec] [Pagina N/M] [Succ →]
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    view_name = current_view.split(":")[0] if ":" in current_view else current_view
    total_pages = max(1, (total_items + per_page - 1) // per_page)

    def _view_btn(label: str, view: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(label, callback_data=f"dash:view:{view}")

    row1 = [
        _view_btn("⚡ Attivi", "attivi"),
        _view_btn("✅ Chiusi", "chiusi"),
        _view_btn("🚫 Bloccati", "bloccati"),
    ]
    row2 = [
        _view_btn("💰 PnL", "pnl"),
        _view_btn("📉 Stats", "stats"),
        InlineKeyboardButton("🔄 Refresh", callback_data="dash:refresh"),
    ]
    rows = [row1, row2]

    # Riga paginazione: solo per Attivi/Chiusi/Bloccati con >5 trade
    if view_name in ("attivi", "chiusi", "bloccati") and total_items > per_page:
        page_row = []
        if current_page > 0:
            page_row.append(InlineKeyboardButton("← Prec", callback_data=f"dash:page:{current_page - 1}"))
        page_row.append(InlineKeyboardButton(f"Pagina {current_page + 1}/{total_pages}", callback_data="noop"))
        if current_page < total_pages - 1:
            page_row.append(InlineKeyboardButton("Succ →", callback_data=f"dash:page:{current_page + 1}"))
        rows.append(page_row)

    return InlineKeyboardMarkup(rows)


DASHBOARD_REGISTRY: dict[str, TemplateConfig] = {
    "attivi": _ATTIVI,
    "chiusi": _CHIUSI,
    "bloccati": _BLOCCATI,
    "pnl": _PNL,
    "stats": _STATS,
}

__all__ = ["DASHBOARD_REGISTRY", "build_dashboard_keyboard"]
```

- [ ] **Step 2: Scrivere smoke test**

Creare `tests/runtime_v2/control_plane/test_dashboard_templates.py`:

```python
from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.templates.dashboard import (
    DASHBOARD_REGISTRY, build_dashboard_keyboard,
)


def _base_payload(view: str) -> dict:
    return {
        "scope_label": "demo_1 · trader_a",
        "updated_at": "14:32:05",
        "snapshot_age": "18s fa",
        "stale_warn": "",
        "total": 0,
        "trades": [],
        "show_trader": False,
    }


def test_attivi_empty():
    cfg = DASHBOARD_REGISTRY["attivi"]
    result = render_template(cfg.blocks, _base_payload("attivi"), transform=cfg.payload_transform)
    assert "DASHBOARD — demo_1 · trader_a" in result
    assert "Nessun trade attivo." in result


def test_attivi_with_trade():
    cfg = DASHBOARD_REGISTRY["attivi"]
    payload = {**_base_payload("attivi"), "total": 1, "trades": [{
        "chain_id": 5, "symbol": "BTC/USDT", "side": "LONG", "state": "OPEN",
        "trader_id": "trader_a",
        "entry_legs": [{"price": "63500", "status": "filled"}, {"price": "63200", "status": "cancelled"}],
        "tp_prices": ["64000", "65200", "66500"],   # flat — nessun status
        "sl_price": "62000", "has_be": True, "pnl": 34.20,
        "signal_link": "https://t.me/c/123/987",
    }]}
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "#5" in result
    assert "63500✓" in result
    assert "63200✗" in result
    assert "64000 · 65200 · 66500" in result   # prezzi flat, nessun simbolo TP
    assert "BE: ✓" in result
    assert "+34.20" in result


def test_attivi_waiting_entry():
    cfg = DASHBOARD_REGISTRY["attivi"]
    payload = {**_base_payload("attivi"), "total": 1, "trades": [{
        "chain_id": 9, "symbol": "SOL/USDT", "side": "LONG", "state": "WAITING_ENTRY",
        "trader_id": "trader_a",
        "entry_legs": [{"price": "148", "status": "pending"}, {"price": "147", "status": "pending"}],
        "tp_prices": ["155", "160"], "sl_price": "143", "has_be": False, "pnl": None,
        "signal_link": None,
    }]}
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "In attesa di riempimento" in result


def test_chiusi_empty():
    cfg = DASHBOARD_REGISTRY["chiusi"]
    result = render_template(cfg.blocks, _base_payload("chiusi"), transform=cfg.payload_transform)
    assert "Nessun trade chiuso." in result


def test_pnl_with_realized():
    cfg = DASHBOARD_REGISTRY["pnl"]
    payload = {
        "scope_label": "demo_1 · trader_a", "updated_at": "14:32:05",
        "snapshot_age": None, "stale_warn": "", "trader_label": "trader_a",
        "equity_usdt": 10432.50, "balance_usdt": 9100.0, "margin_usdt": 820.0,
        "gross_pnl": 142.60, "fees": -11.20, "net_pnl": 130.00,
        "open_count": 1, "waiting_count": 1,
    }
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "Realizzato (trader_a):" in result
    assert "130.00" in result


def test_keyboard_no_pagination():
    kb = build_dashboard_keyboard("attivi:0", total_items=3, current_page=0)
    # 3 trade, nessuna terza riga
    assert len(kb.inline_keyboard) == 2


def test_keyboard_with_pagination():
    kb = build_dashboard_keyboard("attivi:1", total_items=12, current_page=1)
    assert len(kb.inline_keyboard) == 3
    # pagina 2/3 (0-based 1) → Prec e Succ presenti
    page_row = kb.inline_keyboard[2]
    labels = [btn.text for btn in page_row]
    assert "← Prec" in labels
    assert "Succ →" in labels


def test_keyboard_first_page_no_prev():
    kb = build_dashboard_keyboard("attivi:0", total_items=12, current_page=0)
    page_row = kb.inline_keyboard[2]
    labels = [btn.text for btn in page_row]
    assert "← Prec" not in labels


def test_keyboard_last_page_no_next():
    kb = build_dashboard_keyboard("chiusi:2", total_items=12, current_page=2)
    page_row = kb.inline_keyboard[2]
    labels = [btn.text for btn in page_row]
    assert "Succ →" not in labels


def test_keyboard_pnl_no_pagination():
    # PnL vista: mai terza riga
    kb = build_dashboard_keyboard("pnl:0", total_items=100, current_page=0)
    assert len(kb.inline_keyboard) == 2
```

Run: `pytest tests/runtime_v2/control_plane/test_dashboard_templates.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/templates/dashboard.py \
        tests/runtime_v2/control_plane/test_dashboard_templates.py
git commit -m "feat: add DASHBOARD_REGISTRY with 5 view templates and keyboard builder"
```

---

## Task 4: `formatters/dashboard.py` — `format_dashboard_view()`

**Files:**
- Create: `src/runtime_v2/control_plane/formatters/dashboard.py`

**Interfaces:**
- Consumes: tutti i view dataclass da `status_queries.py` (Task 2)
- Produces:
  - `format_dashboard_view(view_name, scope, service, scope_label, page) -> tuple[str, int, int]`
    — ritorna `(testo, totale_items, pagina_attuale)` per costruire keyboard
  - `format_dashboard_creation(scope_label) -> str` — messaggio iniziale

```python
# src/runtime_v2/control_plane/formatters/dashboard.py
from __future__ import annotations

from datetime import datetime, timezone

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.display import display_symbol
from src.runtime_v2.control_plane.formatters.templates.dashboard import DASHBOARD_REGISTRY
from src.runtime_v2.control_plane.scope_resolver import QueryScope

_PER_PAGE = 5
_STALE_WARN_SECONDS = 120


def _now_hms() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _snapshot_age_str(age_seconds: float | None) -> tuple[str | None, str]:
    if age_seconds is None:
        return None, ""
    label = f"{int(age_seconds)}s fa"
    warn = "  ⚠️" if age_seconds > _STALE_WARN_SECONDS else ""
    return label, warn


def _trader_label(scope: QueryScope) -> str:
    if scope.trader_ids and len(scope.trader_ids) == 1:
        return scope.trader_ids[0]
    return "tutti i trader"


def format_dashboard_creation(scope_label: str) -> str:
    return (
        f"📊 DASHBOARD — {scope_label}\n"
        "────────────────\n"
        f"Aggiornato: {_now_hms()}\n\n"
        "[seleziona una vista o pinna questo messaggio]"
    )


def format_dashboard_view(
    view_name: str,
    scope: QueryScope,
    service,  # RuntimeControlService — type hint evitato per evitare circular import
    scope_label: str,
    page: int = 0,
) -> tuple[str, int, int]:
    """Ritorna (testo_renderizzato, totale_items, pagina)."""
    cfg = DASHBOARD_REGISTRY[view_name]
    now = _now_hms()

    if view_name == "attivi":
        trades, total = service.get_trades_attivi(scope, page=page, per_page=_PER_PAGE)
        payload = {
            "scope_label": scope_label, "updated_at": now,
            "snapshot_age": None, "stale_warn": "", "total": total,
            "show_trader": scope.trader_ids is None,
            "trades": [
                {
                    "chain_id": t.chain_id,
                    "symbol": display_symbol(t.symbol),
                    "side": t.side, "state": t.state,
                    "trader_id": t.trader_id,
                    "entry_legs": [{"price": l.price, "status": l.status} for l in t.entry_legs],
                    "tp_prices": t.tp_prices,
                    "sl_price": t.sl_price, "has_be": t.has_be,
                    "pnl": t.pnl, "signal_link": t.signal_link,
                }
                for t in trades
            ],
        }
        return render_template(cfg.blocks, payload, transform=cfg.payload_transform), total, page

    elif view_name == "chiusi":
        trades, total = service.get_trades_chiusi(scope, page=page, per_page=_PER_PAGE)
        payload = {
            "scope_label": scope_label, "updated_at": now,
            "snapshot_age": None, "stale_warn": "", "total": total,
            "show_trader": scope.trader_ids is None,
            "trades": [
                {
                    "chain_id": t.chain_id,
                    "symbol": display_symbol(t.symbol),
                    "side": t.side,
                    "trader_id": t.trader_id,
                    "opened_at": t.opened_at, "closed_at": t.closed_at,
                    "duration_minutes": t.duration_minutes,
                    "net_pnl": t.net_pnl,
                    "opened_link": t.opened_link, "closed_link": t.closed_link,
                }
                for t in trades
            ],
        }
        return render_template(cfg.blocks, payload, transform=cfg.payload_transform), total, page

    elif view_name == "bloccati":
        trades = service.get_trades_bloccati(scope)
        total = len(trades)
        payload = {
            "scope_label": scope_label, "updated_at": now,
            "snapshot_age": None, "stale_warn": "", "total": total,
            "show_trader": scope.trader_ids is None,
            "trades": [
                {
                    "chain_id": t.chain_id,
                    "symbol": display_symbol(t.symbol),
                    "side": t.side, "state": t.state,
                    "trader_id": t.trader_id,
                    "motivo": t.motivo, "blocked_at": t.blocked_at, "link": t.link,
                }
                for t in trades
            ],
        }
        return render_template(cfg.blocks, payload, transform=cfg.payload_transform), total, page

    elif view_name == "pnl":
        pnl = service.get_dashboard_pnl(scope)
        snap_age, stale_warn = _snapshot_age_str(pnl.snapshot_age_seconds)
        payload = {
            "scope_label": scope_label, "updated_at": now,
            "snapshot_age": snap_age, "stale_warn": stale_warn,
            "trader_label": _trader_label(scope),
            "equity_usdt": pnl.equity_usdt,
            "balance_usdt": pnl.available_balance_usdt,
            "margin_usdt": pnl.total_margin_used_usdt,
            "gross_pnl": pnl.gross_pnl, "fees": pnl.fees, "net_pnl": pnl.net_pnl,
            "open_count": pnl.open_count, "waiting_count": pnl.waiting_count,
        }
        return render_template(cfg.blocks, payload, transform=cfg.payload_transform), 0, 0

    elif view_name == "stats":
        stats = service.get_stats(scope)
        best = stats.best_trade
        worst = stats.worst_trade
        payload = {
            "scope_label": scope_label, "updated_at": now,
            "snapshot_age": None, "stale_warn": "",
            "today_trades": stats.today.trades, "today_wins": stats.today.wins, "today_pnl": stats.today.net_pnl,
            "week_trades": stats.week.trades, "week_wins": stats.week.wins, "week_pnl": stats.week.net_pnl,
            "month_trades": stats.month.trades, "month_wins": stats.month.wins, "month_pnl": stats.month.net_pnl,
            "total_trades": stats.total.trades, "total_wins": stats.total.wins, "total_pnl": stats.total.net_pnl,
            "best_id": best[0] if best else None,
            "best_symbol": display_symbol(best[1]) if best else None,
            "best_pnl": best[2] if best else None,
            "worst_id": worst[0] if worst else None,
            "worst_symbol": display_symbol(worst[1]) if worst else None,
            "worst_pnl": worst[2] if worst else None,
        }
        return render_template(cfg.blocks, payload, transform=cfg.payload_transform), 0, 0

    raise ValueError(f"Unknown dashboard view: {view_name}")


__all__ = ["format_dashboard_view", "format_dashboard_creation"]
```

- [ ] **Step 2: Aggiungere metodi delegate a `service.py`**

```python
from src.runtime_v2.control_plane.status_queries import (
    ..., DashboardPnlView, DashboardTradeRow, ClosedTradeRow, BlockedTradeRow,
)

def get_trades_attivi(self, scope: QueryScope, page: int = 0, per_page: int = 5):
    return self._queries.get_trades_attivi(scope, page, per_page)

def get_trades_chiusi(self, scope: QueryScope, page: int = 0, per_page: int = 5):
    return self._queries.get_trades_chiusi(scope, page, per_page)

def get_trades_bloccati(self, scope: QueryScope):
    return self._queries.get_trades_bloccati(scope)

def get_dashboard_pnl(self, scope: QueryScope) -> DashboardPnlView:
    return self._queries.get_dashboard_pnl(scope)
```

- [ ] **Step 3: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/dashboard.py \
        src/runtime_v2/control_plane/service.py
git commit -m "feat: add format_dashboard_view() formatter and service delegates"
```

---

## Task 5: `DashboardManager`

**Files:**
- Create: `src/runtime_v2/control_plane/dashboard_manager.py`
- Test: `tests/runtime_v2/control_plane/test_dashboard_manager.py`

**Interfaces:**
- Produces:
  - `DashboardManager(ops_db_path, bot, service, scope_resolver)`
  - `.create_or_update(chat_id, thread_id, scope, scope_label) -> int` — ritorna message_id
  - `.navigate(chat_id, thread_id, action, value) -> None` — cambio vista o pagina
  - `.refresh(chat_id, thread_id) -> None` — re-render vista attuale
  - `.on_trade_event(account_id, trader_id, chain_id) -> None` — auto-refresh, chiamato da dispatcher

- [ ] **Step 1: Creare `dashboard_manager.py`**

```python
# src/runtime_v2/control_plane/dashboard_manager.py
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from datetime import datetime, timezone

from src.runtime_v2.control_plane.formatters.dashboard import (
    format_dashboard_creation, format_dashboard_view,
)
from src.runtime_v2.control_plane.formatters.templates.dashboard import build_dashboard_keyboard
from src.runtime_v2.control_plane.scope_resolver import QueryScope, ScopeResolver

logger = logging.getLogger(__name__)

_THROTTLE_SECONDS = 5.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DashboardManager:
    """Gestisce creazione, aggiornamento e auto-refresh dei messaggi dashboard.

    Throttle: minimo 5s tra edit successivi sullo stesso (chat_id, thread_id).
    Se un'edit arriva durante il cooldown, viene schedulata per dopo — non scartata.
    """

    def __init__(
        self,
        *,
        ops_db_path: str,
        bot,          # telegram.Bot — usato per send_message / edit_message_text
        service,      # RuntimeControlService
        scope_resolver: ScopeResolver,
    ) -> None:
        self._db = ops_db_path
        self._bot = bot
        self._service = service
        self._scope_resolver = scope_resolver
        self._throttle: dict[tuple[int, int], float] = {}  # (chat_id, thread_id) -> last_edit_ts
        self._pending_refresh: dict[tuple[int, int], asyncio.Task] = {}

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _upsert(
        self, chat_id: int, thread_id: int, message_id: int,
        account_id: str, trader_id: str | None, current_view: str = "attivi:0",
    ) -> None:
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                """INSERT INTO ops_dashboard_messages
                   (chat_id, thread_id, message_id, account_id, trader_id, current_view, updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(chat_id, thread_id) DO UPDATE SET
                   message_id=excluded.message_id,
                   account_id=excluded.account_id,
                   trader_id=excluded.trader_id,
                   current_view=excluded.current_view,
                   updated_at=excluded.updated_at""",
                (chat_id, thread_id, message_id, account_id, trader_id, current_view, _now()),
            )
            conn.commit()
        finally:
            conn.close()

    def _get_record(self, chat_id: int, thread_id: int) -> dict | None:
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT message_id, account_id, trader_id, current_view "
                "FROM ops_dashboard_messages WHERE chat_id=? AND thread_id=?",
                (chat_id, thread_id),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        return {"message_id": row[0], "account_id": row[1], "trader_id": row[2], "current_view": row[3]}

    def _update_view(self, chat_id: int, thread_id: int, current_view: str) -> None:
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_dashboard_messages SET current_view=?, updated_at=? "
                "WHERE chat_id=? AND thread_id=?",
                (current_view, _now(), chat_id, thread_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _all_dashboards_for_scope(self, account_id: str, trader_id: str | None) -> list[dict]:
        """Trova i dashboard da aggiornare per un evento su (account_id, trader_id)."""
        conn = sqlite3.connect(self._db)
        try:
            rows = conn.execute(
                "SELECT chat_id, thread_id, message_id, account_id, trader_id, current_view "
                "FROM ops_dashboard_messages WHERE account_id=?",
                (account_id,),
            ).fetchall()
        finally:
            conn.close()
        result = []
        for r in rows:
            dash_trader_id = r[4]
            # Includi se: scope è account-wide (trader_id=None) OPPURE trader_id coincide
            if dash_trader_id is None or dash_trader_id == trader_id:
                result.append({
                    "chat_id": r[0], "thread_id": r[1], "message_id": r[2],
                    "account_id": r[3], "trader_id": r[4], "current_view": r[5],
                })
        return result

    # ── scope helpers ─────────────────────────────────────────────────────────

    def _scope_from_record(self, record: dict) -> QueryScope:
        trader_id = record["trader_id"]
        return QueryScope(
            account_id=record["account_id"],
            trader_ids=[trader_id] if trader_id else None,
        )

    def _scope_label_from_record(self, record: dict) -> str:
        tid = record["trader_id"]
        return f"{record['account_id']} · {tid}" if tid else record["account_id"]

    # ── rendering ─────────────────────────────────────────────────────────────

    def _render_current(
        self, record: dict
    ) -> tuple[str, object]:
        """Renderizza la vista corrente. Ritorna (testo, keyboard)."""
        current_view = record["current_view"]
        parts = current_view.split(":", 1)
        view_name = parts[0]
        page = int(parts[1]) if len(parts) > 1 else 0

        scope = self._scope_from_record(record)
        scope_label = self._scope_label_from_record(record)

        text, total, current_page = format_dashboard_view(
            view_name, scope, self._service, scope_label, page=page
        )
        keyboard = build_dashboard_keyboard(current_view, total, current_page)
        return text, keyboard

    # ── throttle ──────────────────────────────────────────────────────────────

    def _can_edit_now(self, chat_id: int, thread_id: int) -> bool:
        key = (chat_id, thread_id)
        last = self._throttle.get(key, 0.0)
        return (time.time() - last) >= _THROTTLE_SECONDS

    def _mark_edited(self, chat_id: int, thread_id: int) -> None:
        self._throttle[(chat_id, thread_id)] = time.time()

    # ── public API ────────────────────────────────────────────────────────────

    async def create_or_update(
        self,
        *,
        chat_id: int,
        thread_id: int,
        scope: QueryScope,
        scope_label: str,
    ) -> int:
        """Crea nuovo messaggio dashboard (o sovrascrive) e persiste in DB. Ritorna message_id."""
        text = format_dashboard_creation(scope_label)
        send_kwargs: dict = {"chat_id": chat_id, "text": text}
        if thread_id != 0:
            send_kwargs["message_thread_id"] = thread_id

        from telegram import InlineKeyboardMarkup
        keyboard = InlineKeyboardMarkup([[]])  # keyboard vuota al momento della creazione
        # Costruisci keyboard base (solo riga 1 e 2, nessuna paginazione)
        from src.runtime_v2.control_plane.formatters.templates.dashboard import build_dashboard_keyboard
        keyboard = build_dashboard_keyboard("attivi:0", total_items=0, current_page=0)
        send_kwargs["reply_markup"] = keyboard

        msg = await self._bot.send_message(**send_kwargs)
        message_id = msg.message_id

        trader_id = scope.trader_ids[0] if scope.trader_ids and len(scope.trader_ids) == 1 else None
        self._upsert(chat_id, thread_id, message_id, scope.account_id, trader_id, "attivi:0")
        return message_id

    async def navigate(
        self,
        *,
        chat_id: int,
        thread_id: int,
        action: str,  # "view" | "page" | "refresh"
        value: str,   # view name o numero pagina o ""
    ) -> None:
        """Gestisce click su keyboard (cambio vista, paginazione, refresh)."""
        record = self._get_record(chat_id, thread_id)
        if record is None:
            return

        current = record["current_view"]
        parts = current.split(":", 1)
        view_name = parts[0]
        page = int(parts[1]) if len(parts) > 1 else 0

        if action == "view":
            new_view = f"{value}:0"  # reset pagina su cambio vista
        elif action == "page":
            new_view = f"{view_name}:{value}"
        else:  # refresh
            new_view = current

        self._update_view(chat_id, thread_id, new_view)
        record["current_view"] = new_view

        await self._edit_message(record)

    async def _edit_message(self, record: dict) -> None:
        chat_id = record["chat_id"]
        thread_id = record["thread_id"]
        message_id = record["message_id"]

        if not self._can_edit_now(chat_id, thread_id):
            # Schedula edit per dopo (non scartare)
            key = (chat_id, thread_id)
            existing = self._pending_refresh.get(key)
            if existing and not existing.done():
                existing.cancel()
            self._pending_refresh[key] = asyncio.create_task(
                self._delayed_edit(record, delay=_THROTTLE_SECONDS)
            )
            return

        await self._do_edit(record, chat_id, thread_id, message_id)

    async def _delayed_edit(self, record: dict, delay: float) -> None:
        await asyncio.sleep(delay)
        # Rileggi record fresco dal DB (la vista potrebbe essere cambiata durante il delay)
        fresh = self._get_record(record["chat_id"], record["thread_id"])
        if fresh:
            await self._do_edit(fresh, fresh["chat_id"], fresh["thread_id"], fresh["message_id"])

    async def _do_edit(self, record: dict, chat_id: int, thread_id: int, message_id: int) -> None:
        try:
            text, keyboard = self._render_current(record)
            await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=keyboard,
            )
            self._mark_edited(chat_id, thread_id)
        except Exception as exc:
            err_str = str(exc)
            if "Message is not modified" in err_str:
                pass  # contenuto invariato, OK
            else:
                logger.warning("dashboard edit failed chat=%s msg=%s: %s", chat_id, message_id, exc)

    async def on_trade_event(
        self,
        *,
        account_id: str,
        trader_id: str | None,
        chain_id: int | None,
    ) -> None:
        """Chiamato da TelegramNotificationDispatcher dopo ogni CLEAN_LOG inviato."""
        dashboards = self._all_dashboards_for_scope(account_id, trader_id)
        for dash in dashboards:
            await self._edit_message(dash)


__all__ = ["DashboardManager"]
```

- [ ] **Step 2: Scrivere i test**

Creare `tests/runtime_v2/control_plane/test_dashboard_manager.py`:

```python
from __future__ import annotations
import asyncio, os, sqlite3, tempfile
from unittest.mock import AsyncMock, MagicMock, patch
from src.runtime_v2.control_plane.dashboard_manager import DashboardManager
from src.runtime_v2.control_plane.scope_resolver import QueryScope, ScopeResolver


def _make_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE ops_dashboard_messages (
            chat_id INTEGER NOT NULL,
            thread_id INTEGER NOT NULL DEFAULT 0,
            message_id INTEGER NOT NULL,
            account_id TEXT NOT NULL,
            trader_id TEXT,
            current_view TEXT NOT NULL DEFAULT 'attivi:0',
            updated_at TEXT,
            PRIMARY KEY (chat_id, thread_id)
        );
    """)
    conn.commit()
    conn.close()
    return path


def _make_manager(db: str) -> DashboardManager:
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
    bot.edit_message_text = AsyncMock()

    service = MagicMock()
    service.get_trades_attivi = MagicMock(return_value=([], 0))
    service.get_trades_chiusi = MagicMock(return_value=([], 0))
    service.get_trades_bloccati = MagicMock(return_value=[])
    service.get_dashboard_pnl = MagicMock(return_value=MagicMock(
        snapshot_age_seconds=None, equity_usdt=None, available_balance_usdt=None,
        total_margin_used_usdt=None, gross_pnl=None, fees=None, net_pnl=None,
        open_count=0, waiting_count=0,
    ))

    scope_resolver = MagicMock()
    return DashboardManager(
        ops_db_path=db, bot=bot, service=service, scope_resolver=scope_resolver
    )


def test_create_inserts_record():
    db = _make_db()
    mgr = _make_manager(db)
    scope = QueryScope(account_id="demo_1", trader_ids=["trader_a"])
    asyncio.run(mgr.create_or_update(chat_id=-100111, thread_id=316, scope=scope, scope_label="demo_1 · trader_a"))
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT account_id, trader_id, current_view FROM ops_dashboard_messages").fetchone()
    conn.close()
    assert row == ("demo_1", "trader_a", "attivi:0")
    os.unlink(db)


def test_navigate_view_resets_page():
    db = _make_db()
    mgr = _make_manager(db)
    scope = QueryScope(account_id="demo_1", trader_ids=None)
    asyncio.run(mgr.create_or_update(chat_id=-100111, thread_id=4, scope=scope, scope_label="demo_1"))
    asyncio.run(mgr.navigate(chat_id=-100111, thread_id=4, action="view", value="chiusi"))
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT current_view FROM ops_dashboard_messages").fetchone()
    conn.close()
    assert row[0] == "chiusi:0"
    os.unlink(db)


def test_navigate_page():
    db = _make_db()
    mgr = _make_manager(db)
    scope = QueryScope(account_id="demo_1", trader_ids=None)
    asyncio.run(mgr.create_or_update(chat_id=-100111, thread_id=4, scope=scope, scope_label="demo_1"))
    asyncio.run(mgr.navigate(chat_id=-100111, thread_id=4, action="page", value="2"))
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT current_view FROM ops_dashboard_messages").fetchone()
    conn.close()
    assert row[0] == "attivi:2"
    os.unlink(db)


def test_on_trade_event_triggers_edit():
    db = _make_db()
    mgr = _make_manager(db)
    # Inserisci record manuale
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO ops_dashboard_messages VALUES (-100111, 4, 999, 'demo_1', 'trader_a', 'attivi:0', NULL)")
    conn.commit()
    conn.close()
    asyncio.run(mgr.on_trade_event(account_id="demo_1", trader_id="trader_a", chain_id=5))
    mgr._bot.edit_message_text.assert_awaited_once()
    os.unlink(db)


def test_on_trade_event_skips_wrong_trader():
    db = _make_db()
    mgr = _make_manager(db)
    # Dashboard per trader_a, evento da trader_b
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO ops_dashboard_messages VALUES (-100111, 316, 999, 'demo_1', 'trader_a', 'attivi:0', NULL)")
    conn.commit()
    conn.close()
    asyncio.run(mgr.on_trade_event(account_id="demo_1", trader_id="trader_b", chain_id=7))
    mgr._bot.edit_message_text.assert_not_awaited()
    os.unlink(db)
```

Run: `pytest tests/runtime_v2/control_plane/test_dashboard_manager.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/runtime_v2/control_plane/dashboard_manager.py \
        tests/runtime_v2/control_plane/test_dashboard_manager.py
git commit -m "feat: add DashboardManager with create, navigate, auto-refresh and throttle"
```

---

## Task 6: Wiring — dispatcher hook + telegram_bot + bootstrap

**Files:**
- Modify: `src/runtime_v2/control_plane/notification_dispatcher.py`
- Modify: `src/runtime_v2/control_plane/telegram_bot.py`
- Modify: `src/runtime_v2/control_plane/bootstrap.py`

**Interfaces:**
- `TelegramNotificationDispatcher.__init__` aggiunge `lifecycle_callback: Callable | None = None`
- `CommandRouter` aggiunge `dashboard_manager: DashboardManager | None = None`
- `bootstrap.build_control_plane()` costruisce e inietta `DashboardManager`

- [ ] **Step 1: Aggiungere `lifecycle_callback` al dispatcher**

In `TelegramNotificationDispatcher.__init__`:
```python
from collections.abc import Callable, Awaitable
self._lifecycle_callback: Callable | None = lifecycle_callback
```

In `drain_once()`, dopo `_update_clean_log_tracking(...)`:
```python
if destination == "CLEAN_LOG" and self._lifecycle_callback is not None:
    try:
        await self._lifecycle_callback(
            account_id=account_id,
            trader_id=payload.get("trader_id"),
            chain_id=payload.get("chain_id"),
        )
    except Exception:
        logger.exception("dashboard lifecycle_callback failed")
```

Aggiungere parametro alla firma `__init__`:
```python
def __init__(
    self,
    *,
    config: ControlPlaneConfig,
    ops_db_path: str,
    topic_router: TopicRouter,
    sender: NotificationSender,
    poll_interval_seconds: float = 2.0,
    batch_size: int = 50,
    debug_status: Callable[[], bool] | None = None,
    lifecycle_callback: Callable | None = None,
) -> None:
```

- [ ] **Step 2: Aggiornare `RouteResult` e `/dashboard` in `telegram_bot.py`**

`route()` e `_dispatch()` restano **sincroni** — nessuna modifica alle loro firme.

**2a. Estendere `RouteResult`** con due campi opzionali per passare il scope a `_on_command()`:

```python
@dataclass
class RouteResult:
    decision: str
    reply_text: str | None
    dashboard_scope: "QueryScope | None" = None       # populate solo se decision=="DASHBOARD"
    dashboard_scope_label: str | None = None
```

**2b. `_dispatch()` per `/dashboard`** — ritorna decision `"DASHBOARD"` senza await:

```python
if command_name == "dashboard":
    return _DispatchResult(reply_text=None, decision="DASHBOARD")
```

**2c. `route()`** — propaga scope nel `RouteResult` quando decision è `"DASHBOARD"`:

Il scope è già calcolato in `route()` (da `ScopeResolver`) prima di chiamare `_dispatch()`. Usarlo direttamente:

```python
# In route(), dopo dispatch_result = self._dispatch(...)
if dispatch_result.decision == "DASHBOARD":
    return RouteResult(
        "DASHBOARD", None,
        dashboard_scope=scope,
        dashboard_scope_label=_scope_label(scope),
    )
return RouteResult(dispatch_result.decision, dispatch_result.reply_text)
```

**2d. `_on_command()`** — gestisce l'await qui, dopo la chiamata sincrona a `route()`:

```python
async def _on_command(self, update, context) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    result = self._router.route(       # sync — invariato
        command_text=message.text or "",
        message_id=message.message_id,
        chat_id=message.chat_id,
        thread_id=message.message_thread_id,
        user_id=user.id,
        username=user.username,
    )

    if result.decision == "DASHBOARD":
        if self._dashboard_manager is not None:
            await self._dashboard_manager.create_or_update(
                chat_id=message.chat_id,
                thread_id=message.message_thread_id or 0,
                scope=result.dashboard_scope,
                scope_label=result.dashboard_scope_label,
            )
        return

    if result.reply_text is None:
        return

    # ... resto invariato (start keyboard, send_message)
```

`_dashboard_manager` viene aggiunto a `TelegramControlBot.__init__`:

```python
class TelegramControlBot:
    def __init__(self, *, config, router, dashboard_manager=None) -> None:
        self._config = config
        self._router = router
        self._dashboard_manager = dashboard_manager
        self._app = None
        self._keyboard_users: set[int] = set()
```

- [ ] **Step 3: Aggiungere routing callback `"dash:"` in `_on_callback_query()`**

In `TelegramControlBot._on_callback_query()`, distinguere callback emergency vs dashboard:

```python
async def _on_callback_query(self, update, context) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return
    await query.answer()

    data = query.data or ""
    chat_id = query.message.chat_id
    thread_id = getattr(query.message, "message_thread_id", None) or 0
    message_id = query.message.message_id

    if data == "noop":
        return

    if data.startswith("dash:"):
        # Dashboard navigation
        parts = data.split(":", 2)
        action = parts[1] if len(parts) > 1 else ""
        value = parts[2] if len(parts) > 2 else ""
        if self._router._dashboard_manager:
            await self._router._dashboard_manager.navigate(
                chat_id=chat_id, thread_id=thread_id,
                action=action, value=value,
            )
        return

    # Emergency close callback (Piano 2)
    result = await self._router.handle_callback(
        callback_data=data,
        user_id=user.id,
        chat_id=chat_id,
        message_id=message_id,
        thread_id=thread_id,
        created_by=str(user.id),
    )
    if result.answer_text:
        try:
            await query.answer(result.answer_text)
        except Exception:
            pass
    if result.delete_message:
        try:
            await query.message.delete()
        except Exception:
            pass
        return
    if result.reply_text:
        try:
            await query.message.edit_text(result.reply_text)
        except Exception:
            pass
```

- [ ] **Step 4: Aggiungere `dashboard_manager` a `CommandRouter`**

```python
class CommandRouter:
    def __init__(
        self, *, config, auth, audit, service, scope_resolver,
        dashboard_manager=None,
    ) -> None:
        ...
        self._dashboard_manager = dashboard_manager
```

- [ ] **Step 5: Aggiornare `bootstrap.py`**

`Bot` è uno stateless HTTP client. Una singola istanza condivisa tra dispatcher e `DashboardManager` è sufficiente — nessuna necessità di istanze multiple.

```python
from telegram import Bot
from src.runtime_v2.control_plane.dashboard_manager import DashboardManager
from src.runtime_v2.control_plane.scope_resolver import ScopeResolver

def build_control_plane(...) -> ControlPlane | None:
    ...
    scope_resolver = ScopeResolver(config)

    # Bot condiviso: usato da dispatcher (send notifiche) e DashboardManager (edit dashboard).
    # Sostituisce _create_sender() che creava un'istanza separata.
    shared_bot = Bot(token=config.token)
    sender = TelegramBotSender(shared_bot)

    dashboard_manager = DashboardManager(
        ops_db_path=ops_db_path,
        bot=shared_bot,   # stessa istanza del dispatcher
        service=service,
        scope_resolver=scope_resolver,
    )

    router = CommandRouter(
        config=config, auth=auth, audit=audit, service=service,
        scope_resolver=scope_resolver,
        dashboard_manager=dashboard_manager,
    )
    bot = TelegramControlBot(config=config, router=router, dashboard_manager=dashboard_manager)

    dispatcher = TelegramNotificationDispatcher(
        config=config,
        ops_db_path=ops_db_path,
        topic_router=topic_router,
        sender=sender,
        debug_status=service.debug_status,
        lifecycle_callback=dashboard_manager.on_trade_event,
    )
    ...
```

Rimuovere la funzione helper `_create_sender()` da `bootstrap.py` (non più necessaria).

- [ ] **Step 6: Eseguire la suite completa**

Run: `pytest tests/runtime_v2/control_plane/ -v --tb=short`
Expected: tutti PASS

- [ ] **Step 7: Commit finale Piano 3**

```bash
git add src/runtime_v2/control_plane/notification_dispatcher.py \
        src/runtime_v2/control_plane/telegram_bot.py \
        src/runtime_v2/control_plane/bootstrap.py
git commit -m "feat: wire DashboardManager into dispatcher lifecycle hook and CommandRouter"
```

---

## Self-Review Piano 3

| Requisito spec | Task | Note |
|---|---|---|
| `ops_dashboard_messages` con PK `(chat_id, thread_id)` e `DEFAULT 0` | Task 1 | |
| 5 viste: Attivi/Chiusi/Bloccati/PnL/Stats | Task 3 | |
| Entry legs con `✓`/`✗` status | Task 3 (`_attivi_trade_renderer`) | Dati da `plan_state_json["legs"]` |
| TP come prezzi flat (nessun status per TP) | Task 3 (`_attivi_trade_renderer`) | `plan_state_json` non traccia status per singolo TP |
| `In attesa di riempimento` per WAITING_ENTRY | Task 3 | |
| Separatori `- - -` in vista Chiusi | Task 3 (sentinel `__SEP__`) | |
| Chiusi: opened/closed timestamp + links + PnL + ⏱ | Task 3 + Task 2 | |
| Bloccati: motivo + timestamp + link | Task 3 + Task 2 | |
| Keyboard 2 righe + 1 condizionale (>5 trade) | Task 3 (`build_dashboard_keyboard`) | |
| `← Prec` assente a pagina 0 | Task 3 | |
| `Succ →` assente all'ultima pagina | Task 3 | |
| `[Pagina N/M]` = noop | Task 3 | |
| Reset pagina 0 su cambio vista | Task 5 (`navigate`) | |
| Throttle 5s tra edit | Task 5 (`_THROTTLE_SECONDS`) | |
| Edit schedulata durante cooldown, non scartata | Task 5 (`_delayed_edit`) | |
| `MessageNotModified` gestita silenziosamente | Task 5 (`_do_edit`) | |
| Auto-refresh su CLEAN_LOG sent | Task 6 (dispatcher hook) | |
| Auto-refresh scope corretto (trader_id filter) | Task 5 (`_all_dashboards_for_scope`) | |
| `/dashboard` da tech_log → IGNORE | Piano 1 Task 3 (auth) | |
| Ricreazione sovrascrive: `ON CONFLICT DO UPDATE` | Task 5 (`_upsert`) | |
| `thread_id=0` per private_bot | Task 1 (schema) + Task 5 (`create_or_update`) | |
| `route()` / `_dispatch()` restano sincroni | Task 6 Step 2 | `await` gestito in `_on_command()` |
| Bot condiviso tra dispatcher e DashboardManager | Task 6 Step 5 | Rimosso `_create_sender()` |

## Gap noti (accettati)

| Gap | Motivazione |
|---|---|
| Link segnale senza `thread_id` nel path | Telegram risolve correttamente senza di esso; `source_thread_id` non è in schema |
| Nessun simbolo `✓`/`✗` per i TP | `plan_state_json` non traccia fill per singolo TP — dati non disponibili |

# Control Plane: Dashboard + Commands Realignment

**Date:** 2026-06-21  
**Scope:** `src/runtime_v2/control_plane/` — formatter, template, dashboard, trade detail, health, emergency  
**Approach:** Ondate indipendenti B (5 wave)

---

## Contesto

Il sistema Telegram control plane ha formatter e template che divergono dalle spec documentali in `docs/Raggionamento/Controllo_Notifica/Temlate_commands_logs/`. I gap principali:
- Dashboard items usano formato entry/sl/be/qty (vecchio) invece del formato compatto spec (uPnL · rPnL · /trade n)
- Nomi view interni in italiano (`attivi/chiusi/bloccati`) vs output spec in inglese (`Active/Closed/Blocked`)
- Nessun header compatto `Total / Page / Updated` nel dashboard
- Global scope (`All accounts`) non gestito in `/status`, `/control`, `/reviews`, `/trades`, dashboard
- `/trade n` primitivo (nessuna struttura ordini, timeline grezza)
- `/health` mostra stato cached, non probe reali
- Emergency commands non rifiutano in `All accounts` non filtrato

---

## Vincoli architetturali

- **Unico engine**: tutto il testo usa `render_template(blocks, payload)` da `_blocks.py`. Nessun formatter custom fuori dal block system.
- `InlineKeyboardMarkup` sempre aggiunta separatamente dal block system.
- Nessuna nuova dipendenza di produzione.
- Naming del backend reale deve restare coerente (non inventare nomi).

---

## Wave 1 — Data model

### 1a. Naming migration IT→EN

**File:** `dashboard_manager.py`, `formatters/dashboard.py`, `formatters/templates/dashboard.py`

Chiavi registry:
```
dashboard_attivi   → dashboard_active
dashboard_chiusi   → dashboard_closed
dashboard_bloccati → dashboard_blocked
```

Costante `_DEFAULT_VIEW = "active"`.

Migration inline in `_ensure_table()`:
```sql
UPDATE ops_dashboard_messages
SET current_view = REPLACE(REPLACE(REPLACE(current_view,
    'attivi', 'active'), 'chiusi', 'closed'), 'bloccati', 'blocked')
WHERE current_view LIKE '%attivi%'
   OR current_view LIKE '%chiusi%'
   OR current_view LIKE '%bloccati%';
```

Callback `view:attivi` → `view:active`, ecc.

### 1b. TradeEvent dataclass

**File:** `status_queries.py`

```python
@dataclass
class TradeEvent:
    label: str               # "SIGNAL ACCEPTED", "ENTRY OPENED", ecc.
    timestamp: str           # "14 Jun 09:10:00"
    source: str | None       # "Signal", "exchange", "operation_rules", "system"
    event_type: str | None   # per UPDATE DONE: "CANCEL_PENDING", ecc.
    reason: str | None       # "missing_sl", "FINAL TP FILLED", ecc.
    clean_log_link: str | None
```

`TradeDetail.last_events: list[str]` → `TradeDetail.events: list[TradeEvent]`

Nuovi campi `TradeDetail`:
```python
entry_legs: list[dict]       # [{"price": "63,500", "status": "filled|cancelled|pending"}]
tp_legs: list[dict]
sl_price: str | None
has_be: bool
unrealized_pnl: float | None
cum_realized_pnl: float | None
final_result: dict | None    # {roi_net, ror, r_mult, pnl_net, pnl_gross, fees, funding}
is_actionable: bool
is_terminal: bool
```

### 1c. StatusView — global scope support

`StatusView` aggiunge `by_account: list[dict] | None` (lista di `{account_id, open_count, waiting_count, failed_commands}`).

`StatusQueries.get_status(scope)` popola `by_account` quando `scope.account_id is None`.

---

## Wave 2 — Templates / Formatters

### Dashboard header compatto

Nuovo helper `_dash_header_full(emoji, view_label)` in `templates/dashboard.py`:
```python
# Output:
# ⚡ Active — demo_1 · trader_a
# - - - - - - - - - - - - - - - - - - - -
# Total: 10   Page: 1/2   Updated: 14:32:05
# Filters: trader_a · Last 7d            ← solo se p.get("filters_str")
# - - - - - - - - - - - - - - - - - - - -
```

Payload keys aggiunte: `total`, `page_display` (es. "1/2"), `filters_str`.

### Dashboard items — Active

`_render_active_item(row, i, p)` — 3 righe (4 in global scope):
```
#5 · BTC/USDT · LONG · PARTIALLY_CLOSED
[Trader: trader_a · Account: demo_2]    ← solo se p["is_global"]
uPnL: +34.20 USDT  rPnL: +14.20 USDT  ← omesso per WAITING_ENTRY
/trade 5 · /cancel 5 · /close 5
```

Payload row keys: `chain_id`, `symbol`, `side`, `state`, `unrealized_pnl`, `cum_realized_pnl`, `trader_id`, `account_id`.

### Dashboard items — Closed

`_render_closed_item(row, i, p)` — 3 righe:
```
#22 · BTC/USDT · LONG · STOP_LOSS
[Trader: ... · Account: ...]    ← solo se is_global
Net PnL: -3.20 USDT · ⏱ 2h 34m
Details: /trade 22
```

Payload row keys aggiuntivi: `closed_reason`, `duration`.

### Dashboard items — Blocked

`_render_blocked_item(row, i, p)` — 3 righe:
```
#7 · ETH/USDT · LONG
[Trader: ... · Account: ...]    ← solo se is_global
Blocked: 14 Jun 11:52 · Reason: missing_sl
Details: /trade 7
```

### Dashboard views — PnL e Stats global scope

**PnL global:**
```
💰 PnL — All accounts
...
Accounts in scope: 3
Realized — All accounts · Last 7d:
...
By account:
demo_2 · Net: +210.40 USDT · Open: 3
```

**Stats global:**
```
📉 Stats — All accounts
...
[tabella aggregata]
By account:
demo_2 · Trades: 8 · Win%: 75% · Net: +210.40
```

### `/trades` — formato spec

`_render_trade_item` in `_shared.py` riscritto:
```
#5 · BTC/USDT · LONG · OPEN
[Trader: ... · Account: ...]    ← solo se is_global
uPnL: +12.40 USDT  rPnL: +0.00 USDT
Details: /trade 5
```
`WAITING_ENTRY`: solo `rPnL: —`, niente uPnL.

Header `/trades` aggiornato: `Total: N   Updated: HH:MM:SS` senza snapshot mark info.

### `/status` global scope

Template block aggiunge `ConditionalBlock(condition=lambda p: p.get("is_global"))` per:
- Header: `All accounts` invece di account specifico
- Sezione `By account:` con breakdown

`_status_to_payload` aggiunge `is_global`, `by_account` dal `StatusView`.

### `/control` global scope

`_render_block_item` aggiunge `account_id` prefix quando `is_global`.  
Blacklist formattata con account prefix.

### `/reviews` global scope

`_render_review_item` aggiunge riga `Trader: ... · Account: ...` quando `is_global`.

---

## Wave 3 — `/trade n`

`format_trade_detail` riscritto con block system.

Template order (spec):
1. `#5 · BTC/USDT · LONG · PARTIALLY_CLOSED`
2. Meta: Trader, Exchange Account, Updated
3. Ordini: Entry legs (✓/✗/pending), TP, SL, BE
4. Stato economico (se azionabile) o Final Result (se terminale)
5. Actions (solo se `is_actionable`)
6. Timeline eventi (via `ListBlock`)

`TradeEvent` renderer:
```
• SIGNAL ACCEPTED · 14 Jun 09:10:00
  Source: Signal -> clean_log_link
  [Type: CANCEL_PENDING]
  [Reason: missing_sl]
```

`StatusQueries.get_trade_detail(chain_id)` esteso per popolare i nuovi campi da `management_plan_json`, `plan_state_json`, `risk_snapshot_json`. Events costruiti da tabella audit/eventi se disponibile, altrimenti fallback su last_events come singolo TradeEvent con label grezzo.

---

## Wave 4 — Dashboard filtri

### Colonna `filters_json`

Migration `ops_dashboard_messages`: `ALTER TABLE ops_dashboard_messages ADD COLUMN filters_json TEXT DEFAULT NULL`.

`DashboardManager._save_dashboard` e `_update_current_view` aggiornano `filters_json`.

### Keyboard aggiornata

`build_dashboard_keyboard(current_view, page, total_count, page_size, filters)`:
```
Row 1: [⚡ Active]  [✅ Closed]  [🚫 Blocked]
Row 2: [💰 PnL]    [📉 Stats]   [🔄 Refresh]
Row 3: [🔎 Filters] [🧹 Clear]
Row 4: [← Prev]   [Page N/M]   [Next →]    ← solo se paginato
```

### Pannelli filtri per-view

Nuovo callback `filters` → edita messaggio con testo filtri + keyboard selectors.

**Active filters panel:**
```
🔎 Filters — Active
[Account ▸]  [Trader ▸]  [Status ▸]  [Side ▸]
[🧹 Clear view]  [← Back]
```

**Selectors** (callback `selector:account:all`, `selector:account:demo_1`, ecc.) costruiti dinamicamente da `ScopeResolver`.

`DashboardManager.handle_callback` aggiunge gestione dei nuovi callback:
- `filters` → mostra pannello filtri
- `selector:back` → torna alla view corrente
- `selector:{type}:{value}` → aggiorna `filters_json`, reset page, re-render
- `clear` → svuota `filters_json`, re-render
- `clear_view` → svuota solo filtri della view corrente

---

## Wave 5 — Health probes + Emergency safety

### Health probes reali

Il command handler `/health` (in `telegram_bot.py` o `service.py`) esegue prima del render:

```python
workers = [
    ("Parser pipeline",    _probe_worker("parser",       db, threshold=120)),
    ("Lifecycle gate",     _probe_worker("lifecycle",    db, threshold=60)),
    ("Execution worker",   _probe_worker("execution",    db, threshold=60)),
    ("Exchange sync",      _probe_exchange_sync(db,      threshold=90)),
    ("Notification disp.", _probe_worker("notification", db, threshold=120)),
]
db_ok = _probe_db(db)
exchange = _probe_exchange_connectivity(db)
```

`_probe_worker(name, db, threshold)` → query `MAX(updated_at)` da tabella eventi filtrata per source/worker. Se age > threshold → `("WARNING", f"last event {age}s ago")`, se non trovato → `("unknown", "")`.

Output template `TEMPLATE_HEALTH` aggiornato:
```
🩺 HEALTH  |  Global runtime
...
Checks: live probe passed/partial/failed
[Warnings: ...]
[Critical: ...]
```

### Emergency safety

In `emergency_close.py` (o command handler), prima di costruire il preview:

```python
if scope.account_id is None and not has_explicit_trader_filter(args):
    return "⛔ Comando non disponibile in All accounts senza filtro.\nSpecifica trader: /close_all trader_a"
```

Applicato a `/close_all`, `/cancel_all`, `/close` quando scope globale.

---

## Test strategy

Per ogni wave, test mirati prima dell'implementazione:

| Wave | Test file | Casi principali |
|---|---|---|
| 1 | `test_dashboard_manager.py` | naming migration, TradeEvent serialization |
| 2 | `test_dashboard_formatter.py`, `test_command_formatters.py` | header format, item 3-line, global scope |
| 3 | `test_readonly_formatters.py` (nuovo: trade detail) | struttura ordini, timeline, final result |
| 4 | `test_dashboard_manager.py` | filtri callback, filters_json persistence |
| 5 | `test_emergency_close.py`, `test_command_formatters.py` | safety block, health probe output |

---

## Acceptance contract

1. Formatter/template output coerente con spec markdown.
2. `global scope / account scope / trader scope` gestiti esplicitamente in tutti i comandi.
3. `/dashboard` ha header `Total/Page/Updated` + tab view + filtri funzionanti.
4. `/trades` usa layout sintetico 3-righe (4 in global scope).
5. `/trade n` mostra struttura ordini + timeline strutturata + final result.
6. `/status`, `/control`, `/reviews` hanno caso `All accounts` con breakdown.
7. `/health` esegue probe reali con esito `Checks: live probe passed/failed`.
8. Emergency commands rifiutano in `All accounts` non filtrato.
9. Test coprono scope, rendering e safety per tutti i comandi.

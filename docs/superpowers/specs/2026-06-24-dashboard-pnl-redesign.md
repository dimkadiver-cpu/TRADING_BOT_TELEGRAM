# Spec — Dashboard Tab PNL Redesign

**Date:** 2026-06-24
**Status:** Approved
**Scope:** `src/runtime_v2/control_plane/` — status_queries, formatters, dashboard_manager, account_snapshot_worker

---

## 1. Contesto

Il tab PNL del dashboard Telegram mostra attualmente `equity_usdt` (Bybit `totalEquity`) che per gli account Unified Trading Account (UTA) include spot assets, non solo il capitale futures. Questo causa confusione perché il bot gestisce esclusivamente futures lineari.

Problemi identificati:
- `equity_usdt` = wallet completo Bybit (spot + futures + uPnL), non il futures wallet
- PNL realizzato esclude i trade `PARTIALLY_CLOSED` con TP parziali già incassati
- Il refresh del tab PNL avviene solo su eventi trade, non su aggiornamenti snapshot account
- Il breakdown per-trader manca di `open_count` e `risk` per trader
- In scope trader-specifico non è chiaro che lo snapshot è account-wide

---

## 2. Decisioni di design

| # | Decisione | Scelta |
|---|-----------|--------|
| D1 | Metrica capitale | Rimuovere `equity_usdt`; mostrare `available`, `margin_used`, `futures_wallet = available + margin_used` |
| D2 | PNL realizzato | Split: `Closed` (CLOSED) + `Partial open` (PARTIALLY_CLOSED con fills) |
| D3 | Refresh | Auto su nuovo snapshot account (throttle 5s esistente) + manuale 🔄 |
| D4 | Scope snapshot con trader filter | Mostrare snapshot account-wide con label `Account snapshot (account_id):` |
| D5 | Breakdown multi-trader | `By trader:` con Open / Risk / Closed / Partial — solo se 2+ trader nello scope |
| D6 | Scope globale by_account | Aggiungere `Avail` e `Margin` per account nel breakdown |

---

## 3. PnlView — nuovi campi

Aggiungere a `PnlView` (`status_queries.py`):

```python
partial_pnl: float | None = None          # realized da PARTIALLY_CLOSED
partial_fees: float | None = None         # fees dei PARTIALLY_CLOSED
partial_pnl_net: float | None = None      # partial_pnl - partial_fees
by_trader: list[dict] | None = None       # breakdown per trader (2+ trader nello scope)
```

> **Nota:** `total_open_risk_usdt` è già presente in `PnlView` (letto da `ops_account_snapshots`) —
> non richiede modifiche. Viene propagato invariato nel payload.

Struttura di ogni elemento `by_trader`:
```python
{
    "trader_id": str,
    "open_count": int,           # OPEN + PARTIALLY_CLOSED
    "risk_usdt": float | None,   # SUM(risk_amount) da risk_snapshot_json open trades
    "closed_pnl": float,         # SUM(gross - fees - funding) WHERE CLOSED
    "partial_pnl": float,        # SUM(gross - fees - funding) WHERE PARTIALLY_CLOSED
}
```

---

## 4. get_pnl() — modifiche query

### 4a. Partial open PNL

Aggiungere query parallela a `closed_row`:

```sql
SELECT
    SUM(cumulative_gross_pnl),
    SUM(cumulative_fees + cumulative_funding),
    SUM(cumulative_fees),
    SUM(cumulative_funding)
FROM ops_trade_chains
WHERE lifecycle_state = 'PARTIALLY_CLOSED'
  AND {scope_frag}
```

### 4b. By trader breakdown

Attivo quando `scope.account_id is not None` (scope non globale).

Per ogni `trader_id` distinto in scope:
```sql
-- open_count
SELECT COUNT(*) FROM ops_trade_chains
WHERE lifecycle_state IN ('OPEN','PARTIALLY_CLOSED')
  AND account_id = ? AND trader_id = ?

-- closed_pnl
SELECT SUM(cumulative_gross_pnl - cumulative_fees - cumulative_funding)
FROM ops_trade_chains
WHERE lifecycle_state = 'CLOSED'
  AND account_id = ? AND trader_id = ?

-- partial_pnl
SELECT SUM(cumulative_gross_pnl - cumulative_fees - cumulative_funding)
FROM ops_trade_chains
WHERE lifecycle_state = 'PARTIALLY_CLOSED'
  AND account_id = ? AND trader_id = ?
```

Risk per trader — SUM di `risk_amount` estratto da `risk_snapshot_json`:
```sql
SELECT risk_snapshot_json FROM ops_trade_chains
WHERE lifecycle_state IN ('OPEN','PARTIALLY_CLOSED','WAITING_ENTRY')
  AND account_id = ? AND trader_id = ?
  AND risk_snapshot_json IS NOT NULL
```
Per ogni riga: `json.loads(risk_snapshot_json).get("risk_amount")` → somma in Python.

`by_trader` mostrato solo se il numero di trader distinti in scope >= 2.

### 4c. Global scope by_account — aggiungere available e margin

Estendere ogni dict in `by_account` con:
```python
{
    ...existing...,
    "available_usdt": snap_r[2] if snap_r else None,    # available_balance_usdt
    "margin_usdt": snap_r[4] if snap_r else None,       # total_margin_used_usdt
}
```

---

## 5. _build_pnl_payload() — modifiche

File: `formatters/dashboard.py`

Aggiungere al payload:
```python
"partial_pnl":       view.partial_pnl,
"partial_fees":      view.partial_fees,
"partial_pnl_net":   view.partial_pnl_net,
"by_trader":         view.by_trader,
"futures_wallet_usdt": (
    (view.available_balance_usdt or 0.0) + (view.total_margin_used_usdt or 0.0)
    if (view.available_balance_usdt is not None or view.total_margin_used_usdt is not None)
    else None
),
```

Rimuovere `"equity_usdt"` dal payload (non più usato nel template).

---

## 6. Template — modifiche display

File: `formatters/templates/dashboard.py`

### 6a. `_pnl_account_lines()` — scope non globale

Riscrivere completamente la funzione. Nuovo ordine e formato:

```
  Available:      X,XXX.XX USDT
  Margin in use:    XXX.XX USDT
  ─────────────────────────────
  Futures wallet: X,XXX.XX USDT      ← available + margin_used
  uPnL live:       +XX.XX USDT       ← se account_unrealized_pnl_usdt disponibile
  Open risk*:       XXX.XX USDT      ← se total_open_risk_usdt disponibile
  Snapshot: HH:MM:SS UTC · age Xs    [· STALE]
```

Il blocco statico `StaticBlock("Account snapshot:")` in `_PNL_BLOCKS` diventa dinamico:
```python
DerivedBlock(text_fn=lambda p: f"Account snapshot ({p.get('account_id_inner') or p.get('account_id')}):")
```

Se nessun snapshot disponibile (tutti i valori None e `captured_at` assente):
`n/a — nessun snapshot disponibile`

### 6b. `_pnl_realized_lines()` — split Closed / Partial open

Riscrivere con il nuovo formato:

```
  Closed:        +XXX.XX USDT
  Partial open:   +XX.XX USDT       ← omessa se partial_pnl_net is None or == 0
  ─────────────────────────────
  Totale:        +XXX.XX USDT
```

`Totale` = `pnl_net + (partial_pnl_net or 0.0)`.

**Edge case — nessun trade chiuso:**
Se `pnl_net is None` e `partial_pnl_net` è None o 0:
```
  Nessun trade chiuso.
```

**`_pnl_realized_label()` — label scope-aware:**

| Scope | Label |
|-------|-------|
| Globale | `Realized — All accounts:` |
| Account + nessun trader filter | `Realized — {account_id}:` |
| Trader singolo | `Realized — {trader_id}:` |
| Multi-trader (2+) | `Realized — trader_a, trader_b:` (lista ordinata da `by_trader`) |

La lista dei trader per la label si ricava da `[t["trader_id"] for t in p.get("by_trader") or []]`; se `by_trader` è None o vuoto e c'è un `trader_id` singolo nel payload, usare quello.

### 6c. Nuovo blocco `By trader:`

Condizione: `len(p.get("by_trader") or []) >= 2`

```
By trader:
  trader_a · Open: 3 · Risk: 120.00 · Closed: +890.20 · Partial: +45.80
  trader_b · Open: 2 · Risk: 100.00 · Closed: +234.50 · Partial: +67.10
```

`Risk` omesso per riga se `risk_usdt is None`.
`Partial` omessa per riga se `partial_pnl == 0`.

Il blocco `By trader:` **non ha separatore prima** — segue direttamente il blocco Realized.
La riga `─────` che separa da `Open:` è il `SeparatorBlock()` esistente che precede la riga Open.

Layout in `_PNL_BLOCKS`:
```python
DerivedBlock(text_fn=_pnl_realized_label),
DerivedBlock(text_fn=_pnl_realized_lines),
ConditionalBlock(
    condition=lambda p: len(p.get("by_trader") or []) >= 2,
    blocks=[
        StaticBlock(""),
        StaticBlock("By trader:"),
        DerivedBlock(text_fn=_pnl_by_trader_lines),
    ],
),
SeparatorBlock(),
DerivedBlock(text_fn=lambda p: f"Open: {p.get('open_count', 0)} · Waiting entry: {p.get('waiting_entry_count', 0)}"),
```

### 6d. Scope globale — header

Riscrivere il blocco `ConditionalBlock(condition=lambda p: bool(p.get("is_global")), ...)`.

Nuovo formato display:
```
Accounts: 3 · Snapshots: 2 fresh · 1 stale
Futures wallet: 4,230.00 USDT   (fresh only)
Available:      2,890.00 USDT
Margin in use:  1,340.00 USDT
uPnL aggregate:   +87.40 USDT
```

Regole:
- `Accounts: X · Snapshots: Y fresh · Z stale` — riga unica combinata; usare `accounts_in_scope`, `accounts_fresh`, `accounts_stale`
- `Futures wallet (fresh only)` = `available_balance_usdt + total_margin_used_usdt` calcolato su somme fresh-only (già in `global_available_balance_usdt` + `global_total_margin_used_usdt`); omessa se entrambe None
- `Available` e `Margin in use` dalla stessa fonte fresh-only; omesse se None
- `uPnL aggregate` = `account_unrealized_pnl_usdt` aggregato (già presente nel payload); omessa se None
- Rimuovere: riga `"Snapshot mode: per-account latest"` e warning `"⚠️ STALE: ..."` — STALE è già visibile nel breakdown per-account

### 6e. Scope globale — `_pnl_by_account_lines()`

Nuovo formato per ogni riga:

```
demo_1 · Avail: 1,450 · Margin: 890 · Net: +890.20 · age 32s
demo_2 · Avail: 1,440 · Margin: 450 · Net: +344.30 · age 18s
demo_3 · STALE · last 4m ago · Net: +156.80
```

Regole:
- `Avail` e `Margin`: formato intero senza decimali e senza `USDT` (short format); omesse se None
- `Net`: mantiene `USDT` e segno
- Per account STALE: `{acc_id} · STALE · last {age_human} · Net: {sign}{net:.2f}`
  - `age_human`: se age >= 60s → `{int(age/60)}m ago`; altrimenti → `{int(age)}s ago`
- Per account fresh: `{acc_id} · Avail: {avail:.0f} · Margin: {margin:.0f} · Net: {sign}{net:.2f} · age {age}s`
- Rimuovere `Open: X` dalla riga per-account (rimane solo nel totale globale)

---

## 7. Refresh automatico su snapshot

### 7a. AccountSnapshotWorker — callback

Aggiungere parametro opzionale `on_snapshot_saved: Callable[[str], None] | None = None`.

Dopo `self._repository.save_account(snap, account_id)` (successo, status OK):
```python
if self._on_snapshot_saved:
    self._on_snapshot_saved(account_id)
```

Non chiamare callback se `snapshot_status != 'OK'` (FAILED/FALLBACK non triggerano refresh).

### 7b. DashboardManager — on_snapshot_event()

Nuovo metodo, analogo a `on_trade_event()`:

```python
async def on_snapshot_event(self, account_id: str) -> None:
    """Aggiorna i dashboard PNL in scope per account_id.
    Chiamato da AccountSnapshotWorker dopo ogni snapshot OK.
    Solo i dashboard con current_view == 'pnl' vengono aggiornati.
    """
    rows = self._get_all_dashboards()
    for chat_id, thread_id, message_id, scope_account_id, scope_trader_id, current_view_str in rows:
        view_name, _ = _parse_view(current_view_str)
        if view_name != "pnl":
            continue
        if scope_account_id is not None and scope_account_id != account_id:
            continue
        # throttle + deferred identici a on_trade_event
        ...
```

Differenza chiave rispetto a `on_trade_event`: filtra per `view_name == "pnl"` — non ha senso refreshare tab Active/Closed per un evento snapshot.

### 7c. Bootstrap — wiring

In `bootstrap.py` (o dove viene costruito `AccountSnapshotWorker`).

Il callback è **sincrono** (`Callable[[str], None]`) — viene chiamato dall'interno di `_fetch_one` (contesto async), quindi `asyncio.create_task` è sicuro:

```python
def _on_snap(account_id: str) -> None:
    asyncio.create_task(dashboard_manager.on_snapshot_event(account_id))

worker = AccountSnapshotWorker(
    ...,
    on_snapshot_saved=_on_snap,
)
```

Non usare `asyncio.get_event_loop().create_task()` — deprecato in contesto async; `asyncio.create_task()` è sufficiente perché il callback è chiamato dall'interno di un coroutine già in esecuzione.

---

## 8. File modificati

| File | Tipo modifica |
|------|--------------|
| `status_queries.py` | `PnlView` + nuovi campi; `get_pnl()` + partial_pnl, by_trader, by_account extended |
| `formatters/dashboard.py` | `_build_pnl_payload()` + nuovi campi, rimozione equity_usdt |
| `formatters/templates/dashboard.py` | `_pnl_account_lines`, `_pnl_realized_lines`, `_pnl_realized_label`, nuovo `_pnl_by_trader_lines`, `_pnl_by_account_lines`, `_PNL_BLOCKS` global header |
| `lifecycle/account_snapshot_worker.py` | Parametro `on_snapshot_saved` callback |
| `control_plane/dashboard_manager.py` | Nuovo `on_snapshot_event()` |
| `control_plane/bootstrap.py` | Wiring callback snapshot → dashboard_manager |

---

## 9. Test da aggiornare / aggiungere

| Test file | Cosa coprire |
|-----------|-------------|
| `tests/runtime_v2/control_plane/test_status_queries.py` | `get_pnl()`: partial_pnl, by_trader con risk, by_account con avail/margin |
| `tests/runtime_v2/control_plane/` (nuovo) | `on_snapshot_event()`: solo tab pnl refreshato, throttle rispettato |
| `tests/runtime_v2/lifecycle/` (nuovo) | `AccountSnapshotWorker`: callback chiamato solo su OK, non su FAILED |

---

## 10. Layout di riferimento completo

Vedi: `docs/Raggionamento/Controllo_Notifica/Temlate_commands_logs/Dashboard_tab_pnl.md`

---

## 11. Note e vincoli

- `futures_wallet_usdt` è calcolato lato display (`available + margin_used`), non salvato in DB.
- Per UTA Bybit: `available_balance_usdt` = `totalAvailableBalance` (già futures-aware, include cross-margin).
- `risk_amount` estratto da `risk_snapshot_json` in Python con `json.loads().get("risk_amount")` — errori silenziosi (skip se null/malformato).
- `by_trader` attivo solo per scope non-globale con 2+ trader. Per trader singolo il totale è già filtrato — breakdown ridondante.
- Il callback `on_snapshot_saved` è fire-and-forget: errori loggati ma non propagati al worker.
- `SNAPSHOT_STALE_SECONDS = 180` invariato.
- `total_open_risk_usdt` già presente in `PnlView` da sessione precedente — non richiede nuovi campi.
- `Open risk*` nel blocco account snapshot (scope non-globale) usa `total_open_risk_usdt` esistente.
- Nel scope globale `total_open_risk_usdt` è `None` (non aggregato) — `Open risk*` non mostrato.
- Age STALE in `by_account`: convertire in minuti se >= 60s (`4m ago`, non `240s ago`).

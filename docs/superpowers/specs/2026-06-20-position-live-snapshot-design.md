# Design: Position Live Snapshot — Dashboard Mark Price + UPL + Realized PnL
**Data:** 2026-06-20
**Stato:** approvato — pronto per piano di implementazione

---

## Contesto

Il dashboard "Attivi" mostra mark price e UPL calcolati da `ops_market_snapshots`, tabella
scritta solo durante la valutazione di nuovi segnali (entry gate). Le posizioni aperte da ore
mostrano prezzi stale — il formatter già segnala `_mark_stale = True` oltre 120s, ma nessun
loop aggiorna quei dati.

Parallelamente, `run_position_reconciliation()` fa N chiamate REST separate (una per chain
aperta) ogni 600s per rilevare posizioni chiuse esternamente, scrivendo in
`ops_position_snapshots` solo la qty per audit.

Obiettivo: un singolo `fetch_positions()` bulk per account ogni 60s che produce mark price
fresco, UPL calcolato da Bybit, e PnL realizzato cumulativo — eliminando le N chiamate
individuali e alimentando il dashboard con dati live.

---

## Scope

**In scope:**
- Nuovo metodo adapter `fetch_all_positions()` + dataclass `RawPositionLive`
- Migrazione `ops_position_snapshots` a schema unificato con upsert
- `run_bulk_position_sync()` sostituisce `run_position_reconciliation()`
- Nuovo campo config `position_live_snapshot_interval_seconds`
- `TradeRow.cum_realized_pnl` + `get_open_trades()` legge da `ops_position_snapshots`
- Payload dashboard aggiornato con `cum_realized_pnl`

**Fuori scope:**
- Riorganizzazione visiva del template dashboard (task separato)
- Modifica a `ops_market_snapshots` (resta per entry gate, non usata da dashboard)
- Modifica a `ops_trade_chains.cumulative_gross_pnl` (invariato)

---

## Design

### 1. Tabella unificata `ops_position_snapshots`

Migrazione da log append (con `snapshot_id` seriale) a **upsert live** con PK
`(account_id, symbol, side)`.

```sql
CREATE TABLE ops_position_snapshots (
    account_id        TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    side              TEXT NOT NULL,   -- 'LONG' | 'SHORT'
    qty               REAL,
    mark_price        REAL,
    unrealized_pnl    REAL,            -- UPL calcolato da Bybit
    cum_realized_pnl  REAL,            -- cumRealisedPnl Bybit (dall'apertura posizione)
    source            TEXT,            -- 'bulk_position_sync'
    captured_at       TEXT NOT NULL,
    PRIMARY KEY (account_id, symbol, side)
)
```

**Migrazione:** rename-and-copy pattern (identico a `ops_dashboard_messages`).
La tabella esistente ha `snapshot_id` come PK seriale e payload minimal in `payload_json`.
I dati storici vengono scartati — non sono usati da nessun consumer funzionale.

**Consumer:**
- Riconciliazione → legge `qty` per detect zero-qty closes
- Dashboard → legge `mark_price`, `unrealized_pnl`, `cum_realized_pnl`
- Debug → `captured_at` indica la freschezza del dato

`ops_market_snapshots` non viene modificata — scritta dall'entry gate, non più letta dal
dashboard per le posizioni attive.

---

### 2. Adapter layer

#### `RawPositionLive` in `adapters/base.py`

```python
@dataclass
class RawPositionLive:
    symbol: str
    side: str              # 'LONG' | 'SHORT'
    qty: float
    mark_price: float | None
    unrealized_pnl: float | None
    cum_realized_pnl: float | None
```

#### `fetch_all_positions()` in `base.py` (default) + `CcxtBybitAdapter`

Dichiarato in `base.py` con default `return None` — adapter che non lo implementano
vengono ignorati gracefully (stesso pattern di `fetch_market_snapshot`).

Implementazione in `CcxtBybitAdapter`:

```python
def fetch_all_positions(self, execution_account_id: str) -> list[RawPositionLive] | None:
    try:
        positions = self._exchange.fetch_positions()   # senza filtro simbolo
        result = []
        for pos in positions:
            side_raw = str(pos.get("side") or "").upper()
            if side_raw not in ("LONG", "SHORT"):
                continue
            info = pos.get("info") or {}
            result.append(RawPositionLive(
                symbol=pos.get("symbol") or "",
                side=side_raw,
                qty=float(pos.get("contracts") or 0.0),
                mark_price=_safe_float(pos.get("markPrice")),
                unrealized_pnl=_safe_float(pos.get("unrealizedPnl")),
                cum_realized_pnl=_safe_float(info.get("cumRealisedPnl")),
            ))
        return result
    except Exception as exc:
        logger.warning("fetch_all_positions failed: %s", exc)
        return None
```

`FakeAdapter` implementa `fetch_all_positions()` con lista iniettabile via
`set_position_live(positions: list[RawPositionLive])` per i test.

---

### 3. `ExchangeEventSyncWorker.run_bulk_position_sync()`

Sostituisce `run_position_reconciliation()`. Una sola chiamata REST per account.

```python
def run_bulk_position_sync(self) -> int:
    if not hasattr(self._adapter, "fetch_all_positions"):
        return 0

    positions = self._adapter.fetch_all_positions(self._execution_account_id)
    if positions is None:
        return 0   # errore adapter → skip tick, log già fatto nell'adapter

    now = datetime.now(timezone.utc).isoformat()
    for pos in positions:
        self._repo.upsert_position_snapshot(
            account_id=self._execution_account_id,
            symbol=pos.symbol,
            side=pos.side,
            qty=pos.qty,
            mark_price=pos.mark_price,
            unrealized_pnl=pos.unrealized_pnl,
            cum_realized_pnl=pos.cum_realized_pnl,
            source="bulk_position_sync",
            captured_at=now,
        )

    # Detect posizioni chiuse esternamente — stessa logica di run_position_reconciliation()
    live_index = {(p.symbol, p.side): p.qty for p in positions}
    open_chains = self._get_open_chains()
    processed = 0
    for chain_id, symbol, side, open_qty in open_chains:
        qty = live_index.get((symbol, side.upper()), 0.0)
        if qty > 0.0:
            self._position_zero_count.pop(chain_id, None)
            continue
        # qty == 0 → _position_zero_count + synthetic close (invariato)
        ...
    return processed
```

`run_position_reconciliation()` viene rimosso da `ExchangeEventSyncWorker`.
`run_trade_based_reconciliation()` e `run_protective_orders_reconciliation()` restano
invariati e chiamati nello stesso timer.

---

### 4. Wiring `main.py` / `main_linux_server.py`

`_make_pos_recon()` aggiornata:

```python
def _make_pos_recon(ws=workers):
    def _pos_recon():
        for w in ws:
            w.run_bulk_position_sync()          # ← sostituisce run_position_reconciliation()
            w.run_trade_based_reconciliation()
            w.run_protective_orders_reconciliation()
    return _pos_recon
```

`AdapterExecutionContext` riceve il nuovo intervallo:

```python
ctx = AdapterExecutionContext(
    adp_name,
    reconciliation_fn=_make_recon(),
    position_reconciliation_fn=_make_pos_recon(),
    poll_fallback_period_seconds=float(adp_cfg.websocket.poll_fallback_period_seconds),
    position_reconciliation_interval_seconds=float(
        adp_cfg.websocket.position_live_snapshot_interval_seconds
    ),
)
```

Stessa modifica speculare in `main_linux_server.py`.

#### Config `execution.yaml`

```yaml
websocket:
  poll_fallback_enabled: true
  poll_fallback_period_seconds: 60        # Timer A — ordini (run_reconciliation)
  position_live_snapshot_interval_seconds: 60   # Timer B — posizioni (run_bulk_position_sync)
```

`position_reconciliation_interval_seconds` rimosso dal config e dal modello.

#### `ExecutionAdapterWebsocketConfig` in `models.py`

```python
position_live_snapshot_interval_seconds: int = 60   # nuovo, sostituisce position_reconciliation_interval_seconds
```

---

### 5. `StatusQueries` + `TradeRow`

#### `TradeRow` — nuovo campo

```python
@dataclass
class TradeRow:
    ...
    cum_realized_pnl: float | None = None   # nuovo — da ops_position_snapshots
```

#### `get_open_trades()` — sorgente dati

La query sulle chains resta invariata. Cambia la sorgente del market data:
da `ops_market_snapshots` → `ops_position_snapshots`.

Index in memoria: `{(symbol, side): (mark_price, unrealized_pnl, cum_realized_pnl, captured_at)}`

**UPL**: usa `unrealized_pnl` da Bybit direttamente. Il calcolo interno
`(mark - entry) * qty * direction` rimane come fallback se il dato live non è disponibile
(adapter non supporta bulk fetch o snapshot non ancora presente).

---

### 6. Dashboard formatter

`_build_attivi_payload()` in `formatters/dashboard.py` aggiunge `cum_realized_pnl` al dict
di ogni riga:

```python
row_dicts = [
    {
        ...
        "unrealized_pnl": r.unrealized_pnl,
        "cum_realized_pnl": r.cum_realized_pnl,
        "mark_price": r.mark_price,
        "mark_captured_at": r.mark_captured_at,
    }
    for r in page_rows
]
```

Il template mostra `cum_realized_pnl` come campo opzionale accanto a UPL.
Mostrato solo se `!= 0` e non `None` — le posizioni OPEN senza partial close non aggiungono
rumore. La riorganizzazione visiva completa del template è un task separato.

---

## File toccati

| File | Modifica |
|------|---------|
| `execution_gateway/adapters/base.py` | `RawPositionLive` dataclass + `fetch_all_positions()` default |
| `execution_gateway/adapters/ccxt_bybit/adapter.py` | `fetch_all_positions()` implementazione |
| `execution_gateway/adapters/fake.py` | `fetch_all_positions()` + `set_position_live()` |
| `execution_gateway/event_sync.py` | `run_bulk_position_sync()` sostituisce `run_position_reconciliation()` |
| `execution_gateway/repositories.py` | `upsert_position_snapshot()` + migrazione tabella |
| `execution_gateway/models.py` | `position_live_snapshot_interval_seconds`, rimuovi `position_reconciliation_interval_seconds` |
| `main.py` | `_make_pos_recon()` aggiornato + nuovo campo config |
| `main_linux_server.py` | speculare a `main.py` |
| `config/execution.yaml` | nuovo campo `position_live_snapshot_interval_seconds` |
| `control_plane/status_queries.py` | `TradeRow.cum_realized_pnl` + `get_open_trades()` legge `ops_position_snapshots` |
| `control_plane/formatters/dashboard.py` | `cum_realized_pnl` nel payload attivi |

**Non toccati:** template, `ops_market_snapshots`, `ops_trade_chains`, `dashboard_manager.py`,
`scope_resolver.py`, `telegram_bot.py`.

---

## Rischi e note

- **Bybit `cumRealisedPnl`**: campo raw nell'`info` dict, non normalizzato da ccxt. Verificare
  che sia presente nella risposta live prima del deploy.
- **Posizioni con qty=0 nel bulk**: Bybit può restituire posizioni chiuse con qty=0 nella
  lista. La logica zero-count esistente le gestisce correttamente.
- **Migrazione `ops_position_snapshots`**: i dati storici vengono persi. Nessun consumer
  funzionale li usa — accettabile.
- **Template**: `cum_realized_pnl` visibile nel dashboard in forma minimale fino alla
  riorganizzazione visiva del template (task separato).

---

## Follow-up

- Riorganizzazione visiva template dashboard "Attivi" (separato)
- Valutare se `ops_market_snapshots` può essere deprecata completamente una volta che
  il live snapshot è stabile in produzione

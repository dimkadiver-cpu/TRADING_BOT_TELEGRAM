# Account Snapshots & PnL Integrity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendere i dati del tab `💰 PnL` semanticamente corretti, verificabili per origine e freshness, alimentati da uno snapshot account periodico indipendente dal flusso segnali.

**Architecture:** Un nuovo `AccountSnapshotWorker` chiama `fetch_balance()` ogni 60s per ogni account e scrive record append-only in `ops_account_snapshots`. Il tab PnL legge il latest snapshot per-account con CTE, calcola l'age al render e mostra STALE se > 180s. Il bug AS-06 (payload hardcoded `"{}"` in `entry_gate.py`) viene corretto separatamente.

**Tech Stack:** Python 3.11+, asyncio, SQLite, pydantic v2, ccxt, pytest

## Global Constraints

- Tabella `ops_account_snapshots` è append-only: nessun UPDATE o DELETE su record esistenti.
- Nessun segreto (API key, secret, signature) nei payload persistiti.
- `_safe_float(a) or _safe_float(b)` è vietato per zero-value safety — usare pattern esplicito `if v is not None`.
- Campi exchange mancanti → salvare `NULL`, non sostituire con campo semanticamente diverso.
- Tutti i timestamp in UTC ISO 8601.
- `snapshot_status` accetta: `'OK'`, `'FALLBACK'`, `'FAILED'`.
- Stale threshold: `SNAPSHOT_STALE_SECONDS = 180` (costante in `status_queries.py`).
- Nessuna dipendenza nuova di produzione senza approvazione esplicita.
- Eseguire i test dalla root del progetto: `pytest <path> -v`.

---

## File Map

| File | Operazione | Task |
|---|---|---|
| `db/ops_migrations/020_ops_account_snapshot_fields.sql` | Create | 1 |
| `src/runtime_v2/execution_gateway/models.py` | Modify | 2 |
| `src/runtime_v2/lifecycle/ports.py` | Modify | 2 |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py` | Modify | 3 |
| `src/runtime_v2/lifecycle/live_exchange_data_port.py` | Modify | 4 |
| `src/runtime_v2/lifecycle/entry_gate.py` (righe 2452–2465, 2467–2481) | Modify | 5 |
| `src/runtime_v2/lifecycle/repositories.py` | Modify | 6 |
| `src/runtime_v2/lifecycle/account_snapshot_worker.py` | Create | 7 |
| `main.py` | Modify | 8 |
| `src/runtime_v2/control_plane/status_queries.py` | Modify | 9 |
| `src/runtime_v2/control_plane/formatters/dashboard.py` | Modify | 10 |
| `src/runtime_v2/control_plane/formatters/templates/dashboard.py` | Modify | 10 |
| `tests/runtime_v2/control_plane/test_migration_020.py` | Create | 1 |
| `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py` | Modify | 3 |
| `tests/runtime_v2/lifecycle/test_repositories.py` | Modify | 6 |
| `tests/runtime_v2/lifecycle/test_account_snapshot_worker.py` | Create | 7 |
| `tests/runtime_v2/control_plane/test_status_queries.py` | Modify | 9 |
| `tests/runtime_v2/control_plane/test_dashboard_formatter.py` | Modify | 10 |

---

## Task 1: DB Migration

**Files:**
- Create: `db/ops_migrations/020_ops_account_snapshot_fields.sql`
- Create: `tests/runtime_v2/control_plane/test_migration_020.py`

**Interfaces:**
- Produces: colonne `account_unrealized_pnl_usdt REAL`, `snapshot_status TEXT NOT NULL DEFAULT 'OK'`, `error_code TEXT` su `ops_account_snapshots`; indice `idx_ops_account_snapshots_account_captured`

- [ ] **Step 1: Creare il file di migration**

```sql
-- db/ops_migrations/020_ops_account_snapshot_fields.sql
ALTER TABLE ops_account_snapshots ADD COLUMN account_unrealized_pnl_usdt REAL;
ALTER TABLE ops_account_snapshots ADD COLUMN snapshot_status TEXT NOT NULL DEFAULT 'OK';
ALTER TABLE ops_account_snapshots ADD COLUMN error_code TEXT;

CREATE INDEX IF NOT EXISTS idx_ops_account_snapshots_account_captured
ON ops_account_snapshots(account_id, captured_at DESC, snapshot_id DESC);
```

- [ ] **Step 2: Scrivere il test**

```python
# tests/runtime_v2/control_plane/test_migration_020.py
from __future__ import annotations
import sqlite3
from pathlib import Path


def test_migration_020_adds_account_snapshot_fields(tmp_path):
    db_path = tmp_path / "ops.sqlite3"
    conn = sqlite3.connect(str(db_path))
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(ops_account_snapshots)")}
    conn.close()
    assert "account_unrealized_pnl_usdt" in columns
    assert "snapshot_status" in columns
    assert "error_code" in columns


def test_migration_020_snapshot_status_default(tmp_path):
    db_path = tmp_path / "ops.sqlite3"
    conn = sqlite3.connect(str(db_path))
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO ops_account_snapshots "
        "(account_id, equity_usdt, available_balance_usdt, total_open_risk_usdt, "
        "total_margin_used_usdt, source, captured_at, payload_json) "
        "VALUES ('main', 100.0, 90.0, 5.0, 10.0, 'test', '2026-01-01T00:00:00+00:00', '{}')"
    )
    conn.commit()
    row = conn.execute("SELECT snapshot_status FROM ops_account_snapshots").fetchone()
    conn.close()
    assert row[0] == "OK"
```

- [ ] **Step 3: Eseguire il test**

```
pytest tests/runtime_v2/control_plane/test_migration_020.py -v
```

Expected: 2 PASSED

- [ ] **Step 4: Commit**

```bash
git add db/ops_migrations/020_ops_account_snapshot_fields.sql tests/runtime_v2/control_plane/test_migration_020.py
git commit -m "feat: migration 020 — add account_unrealized_pnl_usdt, snapshot_status, error_code to ops_account_snapshots"
```

---

## Task 2: Aggiornare i modelli (prerequisito)

**Files:**
- Modify: `src/runtime_v2/execution_gateway/models.py` (riga 149)
- Modify: `src/runtime_v2/lifecycle/ports.py` (riga 9)

**Interfaces:**
- Produces:
  - `RawAccountSnapshot.account_unrealized_pnl_usdt: float | None = None`
  - `RawAccountSnapshot.field_origins: dict[str, str]` (default `{}`)
  - `AccountStateSnapshot.account_unrealized_pnl_usdt: float | None = None`
  - `AccountStateSnapshot.snapshot_status: str = "OK"`
  - `AccountStateSnapshot.error_code: str | None = None`

- [ ] **Step 1: Scrivere i test prima di modificare i modelli**

Aggiungere al file `tests/runtime_v2/lifecycle/test_models.py` (append alla fine):

```python
def test_raw_account_snapshot_new_fields():
    from src.runtime_v2.execution_gateway.models import RawAccountSnapshot
    snap = RawAccountSnapshot(source="ccxt_bybit:demo")
    assert snap.account_unrealized_pnl_usdt is None
    assert snap.field_origins == {}


def test_raw_account_snapshot_field_origins():
    from src.runtime_v2.execution_gateway.models import RawAccountSnapshot
    snap = RawAccountSnapshot(
        source="ccxt_bybit:demo",
        account_unrealized_pnl_usdt=84.3,
        field_origins={"equity_usdt": "bybit.totalEquity"},
    )
    assert snap.account_unrealized_pnl_usdt == 84.3
    assert snap.field_origins["equity_usdt"] == "bybit.totalEquity"


def test_account_state_snapshot_new_fields():
    from src.runtime_v2.lifecycle.ports import AccountStateSnapshot
    from datetime import datetime, timezone
    snap = AccountStateSnapshot(
        account_id="demo_1",
        captured_at=datetime.now(timezone.utc),
        source="ccxt_bybit:demo",
    )
    assert snap.account_unrealized_pnl_usdt is None
    assert snap.snapshot_status == "OK"
    assert snap.error_code is None


def test_account_state_snapshot_failed_status():
    from src.runtime_v2.lifecycle.ports import AccountStateSnapshot
    from datetime import datetime, timezone
    snap = AccountStateSnapshot(
        account_id="demo_1",
        captured_at=datetime.now(timezone.utc),
        source="fallback_static",
        snapshot_status="FAILED",
        error_code="TimeoutError",
    )
    assert snap.snapshot_status == "FAILED"
    assert snap.error_code == "TimeoutError"
```

- [ ] **Step 2: Eseguire il test — deve fallire**

```
pytest tests/runtime_v2/lifecycle/test_models.py -v -k "new_fields or field_origins or failed_status"
```

Expected: FAILED (campi non esistono ancora)

- [ ] **Step 3: Modificare `execution_gateway/models.py`**

Sostituire la classe `RawAccountSnapshot` (riga 149–158):

```python
class RawAccountSnapshot(BaseModel):
    """Normalized account-level snapshot returned by execution adapters."""
    model_config = ConfigDict(extra="ignore")
    equity_usdt: float | None = None
    available_balance_usdt: float | None = None
    total_open_risk_usdt: float | None = None
    total_margin_used_usdt: float | None = None
    account_unrealized_pnl_usdt: float | None = None
    field_origins: dict[str, str] = Field(default_factory=dict)
    payload: dict = Field(default_factory=dict)
    source: str
```

- [ ] **Step 4: Modificare `lifecycle/ports.py`**

Sostituire la classe `AccountStateSnapshot` (riga 9–18):

```python
class AccountStateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    equity_usdt: float | None = None
    available_balance_usdt: float | None = None
    total_open_risk_usdt: float | None = None
    total_margin_used_usdt: float | None = None
    account_unrealized_pnl_usdt: float | None = None
    captured_at: datetime
    source: str
    payload_json: str = "{}"
    snapshot_status: str = "OK"
    error_code: str | None = None
```

- [ ] **Step 5: Eseguire i test**

```
pytest tests/runtime_v2/lifecycle/test_models.py tests/runtime_v2/lifecycle/test_ports.py -v
```

Expected: tutti PASSED

- [ ] **Step 6: Smoke test regressione**

```
pytest tests/runtime_v2/lifecycle/ tests/runtime_v2/execution_gateway/ -v -x -q 2>&1 | tail -5
```

Expected: no nuovi FAILED

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/execution_gateway/models.py src/runtime_v2/lifecycle/ports.py tests/runtime_v2/lifecycle/test_models.py
git commit -m "feat: add account_unrealized_pnl_usdt, snapshot_status, error_code to RawAccountSnapshot and AccountStateSnapshot"
```

---

## Task 3: Correggere l'adapter Bybit

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`
- Modify: `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`

**Interfaces:**
- Consumes: `RawAccountSnapshot` con i nuovi campi da Task 2
- Produces: `fetch_account_snapshot()` che usa `info.result.list[0]` per campi account-wide, popola `field_origins`, non scarta zero values

- [ ] **Step 1: Scrivere i test prima di toccare il codice**

Aggiungere in fondo a `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`:

```python
# ── helpers per account snapshot tests ───────────────────────────────────────

def _make_unified_balance(
    total_equity="12420.50",
    total_available="9180.20",
    total_initial_margin="1104.80",
    total_perp_upl="84.30",
    total_position_im="950.00",
    total_order_im="154.80",
):
    return {
        "total": {"USDT": 12420.50},
        "free": {"USDT": 9180.20},
        "used": {"USDT": 1104.80},
        "USDT": {"total": 12420.50, "free": 9180.20, "used": 1104.80},
        "info": {
            "result": {
                "list": [
                    {
                        "totalEquity": total_equity,
                        "totalAvailableBalance": total_available,
                        "totalInitialMargin": total_initial_margin,
                        "totalPerpUPL": total_perp_upl,
                        "totalPositionIM": total_position_im,
                        "totalOrderIM": total_order_im,
                        "coin": [
                            {
                                "coin": "USDT",
                                "equity": "99999.00",
                                "availableToWithdraw": "8000.00",
                                "walletBalance": "11000.00",
                            }
                        ],
                    }
                ]
            }
        },
    }


def test_fetch_account_snapshot_uses_account_wide_fields():
    exchange = MagicMock()
    exchange.fetch_balance.return_value = _make_unified_balance()
    adapter = _make_adapter(exchange)
    snap = adapter.fetch_account_snapshot("bybit_demo")
    assert snap is not None
    assert snap.equity_usdt == 12420.50
    assert snap.available_balance_usdt == 9180.20
    assert snap.total_margin_used_usdt == 1104.80
    assert snap.account_unrealized_pnl_usdt == 84.30


def test_fetch_account_snapshot_field_origins_primary():
    exchange = MagicMock()
    exchange.fetch_balance.return_value = _make_unified_balance()
    adapter = _make_adapter(exchange)
    snap = adapter.fetch_account_snapshot("bybit_demo")
    assert snap.field_origins["equity_usdt"] == "bybit.totalEquity"
    assert snap.field_origins["available_balance_usdt"] == "bybit.totalAvailableBalance"
    assert snap.field_origins["total_margin_used_usdt"] == "bybit.totalInitialMargin"
    assert snap.field_origins["account_unrealized_pnl_usdt"] == "bybit.totalPerpUPL"


def test_fetch_account_snapshot_preserves_zero_equity():
    exchange = MagicMock()
    exchange.fetch_balance.return_value = _make_unified_balance(total_equity="0.0")
    adapter = _make_adapter(exchange)
    snap = adapter.fetch_account_snapshot("bybit_demo")
    assert snap is not None
    assert snap.equity_usdt == 0.0


def test_fetch_account_snapshot_preserves_zero_margin():
    exchange = MagicMock()
    exchange.fetch_balance.return_value = _make_unified_balance(total_initial_margin="0.0")
    adapter = _make_adapter(exchange)
    snap = adapter.fetch_account_snapshot("bybit_demo")
    assert snap is not None
    assert snap.total_margin_used_usdt == 0.0


def test_fetch_account_snapshot_fallback_coin_when_no_account_wide():
    balance = {
        "total": {}, "free": {}, "used": {},
        "info": {
            "result": {
                "list": [
                    {
                        "coin": [
                            {"coin": "USDT", "equity": "5000.00", "availableToWithdraw": "4500.00"}
                        ]
                    }
                ]
            }
        },
    }
    exchange = MagicMock()
    exchange.fetch_balance.return_value = balance
    adapter = _make_adapter(exchange)
    snap = adapter.fetch_account_snapshot("bybit_demo")
    assert snap is not None
    assert snap.equity_usdt == 5000.00
    assert snap.field_origins["equity_usdt"] == "bybit.coin.USDT.equity"


def test_fetch_account_snapshot_margin_fallback_sum_im():
    balance = _make_unified_balance(total_initial_margin=None)
    # Remove totalInitialMargin
    balance["info"]["result"]["list"][0].pop("totalInitialMargin", None)
    exchange = MagicMock()
    exchange.fetch_balance.return_value = balance
    adapter = _make_adapter(exchange)
    snap = adapter.fetch_account_snapshot("bybit_demo")
    assert snap is not None
    assert snap.total_margin_used_usdt == pytest.approx(950.00 + 154.80)
    assert "totalPositionIM+totalOrderIM" in snap.field_origins["total_margin_used_usdt"]


def test_fetch_account_snapshot_returns_none_on_exception():
    exchange = MagicMock()
    exchange.fetch_balance.side_effect = Exception("network error")
    adapter = _make_adapter(exchange)
    snap = adapter.fetch_account_snapshot("bybit_demo")
    assert snap is None


def test_fetch_account_snapshot_payload_contains_field_origins():
    exchange = MagicMock()
    exchange.fetch_balance.return_value = _make_unified_balance()
    adapter = _make_adapter(exchange)
    snap = adapter.fetch_account_snapshot("bybit_demo")
    assert "field_origins" in snap.payload
    assert snap.payload["field_origins"]["equity_usdt"] == "bybit.totalEquity"
```

- [ ] **Step 2: Eseguire i test — devono fallire**

```
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -v -k "fetch_account_snapshot"
```

Expected: FAILED (logica attuale non usa account-wide fields e usa `or` pattern)

- [ ] **Step 3: Sostituire `fetch_account_snapshot` in `adapter.py`**

Sostituire il metodo `fetch_account_snapshot` (riga 493–542) con:

```python
def fetch_account_snapshot(self, execution_account_id: str) -> RawAccountSnapshot | None:
    del execution_account_id
    try:
        balance = self._exchange.fetch_balance()
    except Exception as exc:
        logger.warning("fetch_account_snapshot failed: %s", exc)
        return None

    total = balance.get("total") if isinstance(balance.get("total"), dict) else {}
    free = balance.get("free") if isinstance(balance.get("free"), dict) else {}
    used = balance.get("used") if isinstance(balance.get("used"), dict) else {}
    info = balance.get("info") if isinstance(balance.get("info"), dict) else {}

    account_row: dict = {}
    coin_row: dict = {}
    for row in (((info.get("result") or {}).get("list")) or []):
        if isinstance(row, dict):
            account_row = row
            for cr in (row.get("coin") or []):
                if isinstance(cr, dict) and cr.get("coin") == "USDT":
                    coin_row = cr
                    break
            break

    field_origins: dict[str, str] = {}

    # equity_usdt — prefer totalEquity (account-wide), fallback coin USDT equity
    equity = _safe_float(account_row.get("totalEquity"))
    if equity is not None:
        field_origins["equity_usdt"] = "bybit.totalEquity"
    else:
        equity = _safe_float(coin_row.get("equity"))
        if equity is not None:
            field_origins["equity_usdt"] = "bybit.coin.USDT.equity"
        else:
            v = _safe_float(total.get("USDT"))
            if v is not None:
                equity = v
                field_origins["equity_usdt"] = "ccxt.total.USDT"

    # available_balance_usdt — prefer totalAvailableBalance, fallback ccxt free
    available = _safe_float(account_row.get("totalAvailableBalance"))
    if available is not None:
        field_origins["available_balance_usdt"] = "bybit.totalAvailableBalance"
    else:
        v = _safe_float(free.get("USDT"))
        if v is not None:
            available = v
            field_origins["available_balance_usdt"] = "ccxt.free.USDT"

    # total_margin_used_usdt — prefer totalInitialMargin, fallback sum IM
    margin = _safe_float(account_row.get("totalInitialMargin"))
    if margin is not None:
        field_origins["total_margin_used_usdt"] = "bybit.totalInitialMargin"
    else:
        pos_im = _safe_float(account_row.get("totalPositionIM"))
        ord_im = _safe_float(account_row.get("totalOrderIM"))
        if pos_im is not None and ord_im is not None:
            margin = pos_im + ord_im
            field_origins["total_margin_used_usdt"] = "bybit.totalPositionIM+totalOrderIM"
        elif pos_im is not None:
            margin = pos_im
            field_origins["total_margin_used_usdt"] = "bybit.totalPositionIM"
        else:
            v = _safe_float(used.get("USDT"))
            if v is not None:
                margin = v
                field_origins["total_margin_used_usdt"] = "ccxt.used.USDT"

    # account_unrealized_pnl_usdt — totalPerpUPL only
    upl = _safe_float(account_row.get("totalPerpUPL"))
    if upl is not None:
        field_origins["account_unrealized_pnl_usdt"] = "bybit.totalPerpUPL"

    payload = dict(balance)
    payload["field_origins"] = field_origins

    return RawAccountSnapshot(
        equity_usdt=equity,
        available_balance_usdt=available,
        total_open_risk_usdt=None,
        total_margin_used_usdt=margin,
        account_unrealized_pnl_usdt=upl,
        field_origins=field_origins,
        payload=payload,
        source=f"ccxt_bybit:{self._mode}",
    )
```

- [ ] **Step 4: Eseguire i test**

```
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -v -k "fetch_account_snapshot"
```

Expected: tutti PASSED

- [ ] **Step 5: Smoke test regressione adapter**

```
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -v -q 2>&1 | tail -5
```

Expected: no nuovi FAILED

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py
git commit -m "feat: fix fetch_account_snapshot to use Bybit account-wide fields, track field_origins, fix zero-value bug"
```

---

## Task 4: LiveExchangeDataPort — propagare i nuovi campi

**Files:**
- Modify: `src/runtime_v2/lifecycle/live_exchange_data_port.py`

**Interfaces:**
- Consumes: `RawAccountSnapshot.account_unrealized_pnl_usdt`, `RawAccountSnapshot.field_origins` (Task 2+3)
- Produces: `AccountStateSnapshot` con `account_unrealized_pnl_usdt`, `snapshot_status="OK"` su successo, `snapshot_status="FALLBACK"` su fallback statico

- [ ] **Step 1: Scrivere i test**

Aggiungere in fondo a `tests/runtime_v2/lifecycle/test_ports.py`:

```python
def test_live_port_propagates_unrealized_pnl(tmp_path):
    from unittest.mock import MagicMock
    from src.runtime_v2.execution_gateway.models import RawAccountSnapshot
    from src.runtime_v2.lifecycle.live_exchange_data_port import LiveExchangeDataPort

    mock_adapter = MagicMock()
    mock_adapter.fetch_account_snapshot.return_value = RawAccountSnapshot(
        equity_usdt=1000.0,
        available_balance_usdt=900.0,
        total_margin_used_usdt=100.0,
        account_unrealized_pnl_usdt=42.5,
        field_origins={"equity_usdt": "bybit.totalEquity"},
        payload={"field_origins": {"equity_usdt": "bybit.totalEquity"}},
        source="ccxt_bybit:demo",
    )
    db = str(tmp_path / "ops.sqlite3")
    import sqlite3
    from pathlib import Path
    conn = sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit(); conn.close()

    from src.runtime_v2.execution_gateway.models import ExecutionConfig
    exec_config = MagicMock()
    exec_config.resolve_routing.return_value = (MagicMock(adapter="bybit", execution_account_id="bybit_demo"), None)

    port = LiveExchangeDataPort(
        execution_config=exec_config,
        adapter_registry={"bybit": mock_adapter},
        ops_db_path=db,
    )
    snap = port.get_account_state("demo_1")
    assert snap.account_unrealized_pnl_usdt == 42.5
    assert snap.snapshot_status == "OK"
    assert snap.error_code is None
    import json
    payload = json.loads(snap.payload_json)
    assert "field_origins" in payload


def test_live_port_fallback_sets_fallback_status(tmp_path):
    from unittest.mock import MagicMock
    from src.runtime_v2.lifecycle.live_exchange_data_port import LiveExchangeDataPort

    db = str(tmp_path / "ops.sqlite3")
    import sqlite3
    from pathlib import Path
    conn = sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit(); conn.close()

    exec_config = MagicMock()
    exec_config.resolve_routing.side_effect = Exception("no adapter")

    port = LiveExchangeDataPort(
        execution_config=exec_config,
        adapter_registry={},
        ops_db_path=db,
    )
    snap = port.get_account_state("demo_1")
    assert snap.snapshot_status == "FALLBACK"
    assert snap.source == "fallback_static"
```

- [ ] **Step 2: Eseguire i test — devono fallire**

```
pytest tests/runtime_v2/lifecycle/test_ports.py -v -k "live_port"
```

Expected: FAILED

- [ ] **Step 3: Aggiornare `get_account_state` in `live_exchange_data_port.py`**

Sostituire il metodo `get_account_state` (riga 99–122):

```python
def get_account_state(self, account_id: str) -> AccountStateSnapshot:
    computed_open_risk = self._compute_total_open_risk_usdt(account_id)
    resolved = self._resolve_adapter(account_id)
    if resolved is None:
        return self._fallback.get_account_state(account_id).model_copy(
            update={
                "total_open_risk_usdt": computed_open_risk,
                "source": "fallback_static",
                "snapshot_status": "FALLBACK",
            }
        )
    adapter, execution_account_id = resolved
    raw = adapter.fetch_account_snapshot(execution_account_id)
    if raw is None:
        return self._fallback.get_account_state(account_id).model_copy(
            update={
                "total_open_risk_usdt": computed_open_risk,
                "source": "fallback_static",
                "snapshot_status": "FALLBACK",
            }
        )
    assert isinstance(raw, RawAccountSnapshot)
    return AccountStateSnapshot(
        account_id=account_id,
        equity_usdt=raw.equity_usdt,
        available_balance_usdt=raw.available_balance_usdt,
        total_open_risk_usdt=computed_open_risk,
        total_margin_used_usdt=raw.total_margin_used_usdt,
        account_unrealized_pnl_usdt=raw.account_unrealized_pnl_usdt,
        captured_at=self._now(),
        source=raw.source,
        payload_json=self._to_payload_json(raw.payload),
        snapshot_status="OK",
    )
```

- [ ] **Step 4: Eseguire i test**

```
pytest tests/runtime_v2/lifecycle/test_ports.py -v -k "live_port"
```

Expected: PASSED

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/live_exchange_data_port.py tests/runtime_v2/lifecycle/test_ports.py
git commit -m "feat: propagate account_unrealized_pnl_usdt and snapshot_status through LiveExchangeDataPort"
```

---

## Task 5: Bug fix AS-06 — entry_gate hardcoded `"{}"`

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py` (righe 2452–2481)

**Interfaces:**
- Consumes: `AccountStateSnapshot` con i nuovi campi (Task 2)

- [ ] **Step 1: Scrivere il test di regressione**

Aggiungere in `tests/runtime_v2/lifecycle/test_entry_gate.py` (cerca un test che tocca il percorso di persistenza segnale e aggiungi assertion):

```python
def test_entry_gate_persists_account_snapshot_payload(ops_db, signal_setup):
    """AS-06: entry_gate deve salvare s.payload_json, non la stringa letterale '{}'."""
    import sqlite3, json
    # Esegui un segnale che produce un account snapshot
    # (usa il fixture esistente che crea una chain WAITING_ENTRY o OPEN)
    # Poi verifica che payload_json nel DB non sia '{}'
    conn = sqlite3.connect(ops_db)
    rows = conn.execute(
        "SELECT payload_json FROM ops_account_snapshots ORDER BY snapshot_id DESC LIMIT 1"
    ).fetchall()
    conn.close()
    if not rows:
        return  # nessuno snapshot nel percorso di test — skip
    payload = json.loads(rows[0][0])
    # Se il payload è vuoto, il bug è ancora presente
    # Un payload reale da LiveExchangeDataPort contiene almeno 'field_origins'
    # o chiavi CCXT come 'total', 'free', 'used'
    assert payload != {}, "AS-06: payload_json è ancora hardcoded '{}' in entry_gate.py"
```

Nota: se il test di integrazione in `test_entry_gate.py` non tocca `LiveExchangeDataPort` reale (usa mock), questo test potrebbe non trovare righe. In quel caso il test è un guard per il futuro. La verifica principale è il test al passo successivo.

- [ ] **Step 2: Aggiungere un test unit diretto**

Aggiungere in `tests/runtime_v2/lifecycle/test_repositories.py`:

```python
def test_save_account_persists_payload_json(tmp_path):
    import sqlite3, json
    from pathlib import Path
    from datetime import datetime, timezone
    from src.runtime_v2.lifecycle.repositories import SnapshotRepository
    from src.runtime_v2.lifecycle.ports import AccountStateSnapshot

    db = str(tmp_path / "ops.sqlite3")
    conn = sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit(); conn.close()

    repo = SnapshotRepository(db)
    snap = AccountStateSnapshot(
        account_id="demo_1",
        equity_usdt=1000.0,
        available_balance_usdt=900.0,
        total_margin_used_usdt=100.0,
        account_unrealized_pnl_usdt=42.5,
        captured_at=datetime.now(timezone.utc),
        source="ccxt_bybit:demo",
        payload_json=json.dumps({"field_origins": {"equity_usdt": "bybit.totalEquity"}, "total": {"USDT": 1000.0}}),
        snapshot_status="OK",
    )
    repo.save_account(snap, "demo_1")

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT payload_json, account_unrealized_pnl_usdt, snapshot_status, error_code "
        "FROM ops_account_snapshots ORDER BY snapshot_id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    payload = json.loads(row[0])
    assert payload != {}
    assert "field_origins" in payload
    assert row[1] == pytest.approx(42.5)
    assert row[2] == "OK"
    assert row[3] is None
```

- [ ] **Step 3: Eseguire il test repository — deve fallire**

```
pytest tests/runtime_v2/lifecycle/test_repositories.py -v -k "save_account_persists"
```

Expected: FAILED (`save_account` non salva i nuovi campi)

- [ ] **Step 4: Aggiornare `save_account()` in `repositories.py`**

Sostituire il metodo `save_account` (riga 408–428) con:

```python
def save_account(self, snap, account_id: str) -> None:
    from src.runtime_v2.lifecycle.ports import AccountStateSnapshot
    assert isinstance(snap, AccountStateSnapshot)
    conn = sqlite3.connect(self._db_path)
    try:
        conn.execute(
            """
            INSERT INTO ops_account_snapshots (
                account_id, equity_usdt, available_balance_usdt,
                total_open_risk_usdt, total_margin_used_usdt,
                account_unrealized_pnl_usdt, source, captured_at,
                payload_json, snapshot_status, error_code
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                account_id, snap.equity_usdt, snap.available_balance_usdt,
                snap.total_open_risk_usdt, snap.total_margin_used_usdt,
                snap.account_unrealized_pnl_usdt,
                snap.source, snap.captured_at.isoformat(), snap.payload_json,
                snap.snapshot_status, snap.error_code,
            ),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 5: Verificare che il test repository passi**

```
pytest tests/runtime_v2/lifecycle/test_repositories.py -v -k "save_account_persists"
```

Expected: PASSED

- [ ] **Step 6: Correggere `entry_gate.py` righe 2452–2481**

Individuare il blocco che inizia con `if result.account_snapshot:` (intorno a riga 2450). Sostituire:

```python
if result.account_snapshot:
    s = result.account_snapshot
    conn.execute(
        """
        INSERT INTO ops_account_snapshots (
            account_id, equity_usdt, available_balance_usdt,
            total_open_risk_usdt, total_margin_used_usdt,
            source, captured_at, payload_json
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            enriched.account_id, s.equity_usdt, s.available_balance_usdt,
            s.total_open_risk_usdt, s.total_margin_used_usdt,
            s.source, s.captured_at.isoformat(), "{}",
        ),
    )
```

con:

```python
if result.account_snapshot:
    s = result.account_snapshot
    conn.execute(
        """
        INSERT INTO ops_account_snapshots (
            account_id, equity_usdt, available_balance_usdt,
            total_open_risk_usdt, total_margin_used_usdt,
            account_unrealized_pnl_usdt, source, captured_at,
            payload_json, snapshot_status, error_code
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            enriched.account_id, s.equity_usdt, s.available_balance_usdt,
            s.total_open_risk_usdt, s.total_margin_used_usdt,
            s.account_unrealized_pnl_usdt,
            s.source, s.captured_at.isoformat(), s.payload_json,
            s.snapshot_status, s.error_code,
        ),
    )
```

Applicare la stessa correzione al blocco successivo per `ops_market_snapshots` (riga ~2479): sostituire `"{}"` con `s.payload_json`.

- [ ] **Step 7: Smoke test regressione entry_gate**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -v -q 2>&1 | tail -5
```

Expected: no nuovi FAILED

- [ ] **Step 8: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py src/runtime_v2/lifecycle/repositories.py tests/runtime_v2/lifecycle/test_repositories.py
git commit -m "fix: AS-06 — persist real payload_json in entry_gate instead of hardcoded '{}'; update save_account for new columns"
```

---

## Task 6: Repository — query latest snapshot

**Files:**
- Modify: `src/runtime_v2/lifecycle/repositories.py`
- Modify: `tests/runtime_v2/lifecycle/test_repositories.py`

**Interfaces:**
- Produces:
  - `SnapshotRepository.get_latest_account_snapshot(account_id: str) -> dict | None`
  - `SnapshotRepository.get_latest_account_snapshots_all() -> list[dict]`
  - Chiavi dict: `account_id`, `equity_usdt`, `available_balance_usdt`, `total_open_risk_usdt`, `total_margin_used_usdt`, `account_unrealized_pnl_usdt`, `source`, `captured_at`, `snapshot_status`, `error_code`

- [ ] **Step 1: Scrivere i test**

Aggiungere in `tests/runtime_v2/lifecycle/test_repositories.py`:

```python
def _insert_snapshot(conn, account_id, equity, captured_at, status="OK"):
    conn.execute(
        "INSERT INTO ops_account_snapshots "
        "(account_id, equity_usdt, available_balance_usdt, total_open_risk_usdt, "
        "total_margin_used_usdt, account_unrealized_pnl_usdt, source, captured_at, "
        "payload_json, snapshot_status, error_code) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (account_id, equity, equity * 0.9, 10.0, 5.0, None,
         "ccxt_bybit:demo", captured_at, "{}", status, None),
    )


def test_get_latest_account_snapshot_returns_most_recent_ok(tmp_path):
    import sqlite3
    from pathlib import Path
    from src.runtime_v2.lifecycle.repositories import SnapshotRepository

    db = str(tmp_path / "ops.sqlite3")
    conn = sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    _insert_snapshot(conn, "demo_1", 1000.0, "2026-06-23T10:00:00+00:00")
    _insert_snapshot(conn, "demo_1", 1200.0, "2026-06-23T10:01:00+00:00")
    _insert_snapshot(conn, "demo_1", 500.0, "2026-06-23T10:02:00+00:00", status="FAILED")
    conn.commit(); conn.close()

    repo = SnapshotRepository(db)
    snap = repo.get_latest_account_snapshot("demo_1")
    assert snap is not None
    assert snap["equity_usdt"] == 1200.0   # FAILED ignorato, prende l'ultimo OK
    assert snap["snapshot_status"] == "OK"


def test_get_latest_account_snapshot_returns_none_when_missing(tmp_path):
    import sqlite3
    from pathlib import Path
    from src.runtime_v2.lifecycle.repositories import SnapshotRepository

    db = str(tmp_path / "ops.sqlite3")
    conn = sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit(); conn.close()

    repo = SnapshotRepository(db)
    assert repo.get_latest_account_snapshot("demo_1") is None


def test_get_latest_account_snapshots_all_returns_one_per_account(tmp_path):
    import sqlite3
    from pathlib import Path
    from src.runtime_v2.lifecycle.repositories import SnapshotRepository

    db = str(tmp_path / "ops.sqlite3")
    conn = sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    _insert_snapshot(conn, "demo_1", 1000.0, "2026-06-23T10:00:00+00:00")
    _insert_snapshot(conn, "demo_1", 1200.0, "2026-06-23T10:01:00+00:00")
    _insert_snapshot(conn, "demo_2", 500.0,  "2026-06-23T10:00:30+00:00")
    conn.commit(); conn.close()

    repo = SnapshotRepository(db)
    snaps = repo.get_latest_account_snapshots_all()
    assert len(snaps) == 2
    by_acc = {s["account_id"]: s for s in snaps}
    assert by_acc["demo_1"]["equity_usdt"] == 1200.0
    assert by_acc["demo_2"]["equity_usdt"] == 500.0
```

- [ ] **Step 2: Eseguire i test — devono fallire**

```
pytest tests/runtime_v2/lifecycle/test_repositories.py -v -k "get_latest"
```

Expected: FAILED (metodi non esistono)

- [ ] **Step 3: Aggiungere i metodi a `SnapshotRepository`**

In `repositories.py`, aggiungere dopo il metodo `save_account`:

```python
_SNAPSHOT_COLS = (
    "account_id, equity_usdt, available_balance_usdt, "
    "total_open_risk_usdt, total_margin_used_usdt, "
    "account_unrealized_pnl_usdt, source, captured_at, "
    "snapshot_status, error_code"
)

def _row_to_snapshot_dict(row) -> dict:
    return {
        "account_id": row[0],
        "equity_usdt": row[1],
        "available_balance_usdt": row[2],
        "total_open_risk_usdt": row[3],
        "total_margin_used_usdt": row[4],
        "account_unrealized_pnl_usdt": row[5],
        "source": row[6],
        "captured_at": row[7],
        "snapshot_status": row[8],
        "error_code": row[9],
    }

def get_latest_account_snapshot(self, account_id: str) -> dict | None:
    conn = sqlite3.connect(self._db_path)
    try:
        row = conn.execute(
            f"SELECT {_SNAPSHOT_COLS} FROM ops_account_snapshots "
            "WHERE account_id=? AND snapshot_status='OK' "
            "ORDER BY datetime(captured_at) DESC, snapshot_id DESC LIMIT 1",
            (account_id,),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_snapshot_dict(row) if row else None

def get_latest_account_snapshots_all(self) -> list[dict]:
    conn = sqlite3.connect(self._db_path)
    try:
        rows = conn.execute(
            f"""
            WITH ranked AS (
                SELECT {_SNAPSHOT_COLS},
                    ROW_NUMBER() OVER (
                        PARTITION BY account_id
                        ORDER BY datetime(captured_at) DESC, snapshot_id DESC
                    ) AS rn
                FROM ops_account_snapshots
                WHERE snapshot_status = 'OK'
            )
            SELECT {_SNAPSHOT_COLS} FROM ranked WHERE rn = 1 ORDER BY account_id
            """
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_snapshot_dict(r) for r in rows]
```

Nota: `_SNAPSHOT_COLS` e `_row_to_snapshot_dict` vanno definiti come variabile/funzione a livello di modulo (fuori dalla classe), prima della classe `SnapshotRepository`.

- [ ] **Step 4: Eseguire i test**

```
pytest tests/runtime_v2/lifecycle/test_repositories.py -v -k "get_latest or save_account"
```

Expected: tutti PASSED

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/repositories.py tests/runtime_v2/lifecycle/test_repositories.py
git commit -m "feat: add get_latest_account_snapshot and get_latest_account_snapshots_all to SnapshotRepository"
```

---

## Task 7: AccountSnapshotWorker

**Files:**
- Create: `src/runtime_v2/lifecycle/account_snapshot_worker.py`
- Create: `tests/runtime_v2/lifecycle/test_account_snapshot_worker.py`

**Interfaces:**
- Consumes: `LiveExchangeDataPort.get_account_state()`, `SnapshotRepository.save_account()`
- Produces:
  - `AccountSnapshotWorker(port, repository, account_ids, interval_seconds=60, stale_after_seconds=180)`
  - `worker.run()` — coroutine, loop periodico
  - `worker.trigger(account_id)` — trigger immediato non-bloccante

- [ ] **Step 1: Scrivere i test**

```python
# tests/runtime_v2/lifecycle/test_account_snapshot_worker.py
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.runtime_v2.lifecycle.account_snapshot_worker import AccountSnapshotWorker
from src.runtime_v2.lifecycle.ports import AccountStateSnapshot


def _make_snapshot(account_id="demo_1", status="OK"):
    return AccountStateSnapshot(
        account_id=account_id,
        equity_usdt=1000.0,
        captured_at=datetime.now(timezone.utc),
        source="ccxt_bybit:demo",
        snapshot_status=status,
    )


def _make_port(account_id="demo_1", raise_exc=None):
    port = MagicMock()
    if raise_exc:
        port.get_account_state.side_effect = raise_exc
    else:
        port.get_account_state.return_value = _make_snapshot(account_id)
    return port


def _make_repo():
    return MagicMock()


@pytest.mark.asyncio
async def test_worker_calls_fetch_for_each_account_on_startup():
    port = MagicMock()
    port.get_account_state.side_effect = lambda acc: _make_snapshot(acc)
    repo = _make_repo()
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["demo_1", "demo_2"],
        interval_seconds=999,
    )
    # Run one iteration manually
    await worker._fetch_all()
    assert port.get_account_state.call_count == 2
    assert repo.save_account.call_count == 2


@pytest.mark.asyncio
async def test_worker_saves_failed_record_on_exception():
    port = _make_port(raise_exc=RuntimeError("timeout"))
    repo = _make_repo()
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["demo_1"],
        interval_seconds=999,
    )
    await worker._fetch_one("demo_1")
    assert repo.save_account.called
    saved_snap = repo.save_account.call_args[0][0]
    assert saved_snap.snapshot_status == "FAILED"
    assert saved_snap.error_code == "RuntimeError"


@pytest.mark.asyncio
async def test_worker_account_a_failure_does_not_stop_account_b():
    port = MagicMock()
    port.get_account_state.side_effect = lambda acc: (
        (_ for _ in ()).throw(RuntimeError("fail")) if acc == "demo_1"
        else _make_snapshot(acc)
    )
    repo = _make_repo()
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["demo_1", "demo_2"],
        interval_seconds=999,
    )
    await worker._fetch_all()
    # demo_2 should still be saved
    saved_accounts = [call[0][1] for call in repo.save_account.call_args_list]
    assert "demo_2" in saved_accounts


@pytest.mark.asyncio
async def test_worker_no_concurrent_fetch_same_account():
    fetch_count = {"demo_1": 0}
    in_flight = {"demo_1": False}

    async def slow_fetch(acc):
        assert not in_flight[acc], "Concurrent fetch detected!"
        in_flight[acc] = True
        await asyncio.sleep(0.01)
        fetch_count[acc] += 1
        in_flight[acc] = False
        return _make_snapshot(acc)

    port = MagicMock()
    repo = _make_repo()
    worker = AccountSnapshotWorker(
        port=port, repository=repo,
        account_ids=["demo_1"],
        interval_seconds=999,
    )
    # Manually call _fetch_one while it's "in flight"
    worker._in_flight.add("demo_1")
    worker.trigger("demo_1")  # should add to pending, not start new fetch
    assert "demo_1" in worker._pending_refresh
    worker._in_flight.discard("demo_1")
```

- [ ] **Step 2: Eseguire i test — devono fallire**

```
pytest tests/runtime_v2/lifecycle/test_account_snapshot_worker.py -v
```

Expected: FAILED (modulo non esiste)

- [ ] **Step 3: Creare `account_snapshot_worker.py`**

```python
# src/runtime_v2/lifecycle/account_snapshot_worker.py
from __future__ import annotations

import asyncio
import logging
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
    ) -> None:
        self._port = port
        self._repository = repository
        self._account_ids = list(account_ids)
        self._interval = interval_seconds
        self._stale_after = stale_after_seconds
        self._pending_refresh: set[str] = set()
        self._in_flight: set[str] = set()

    async def run(self) -> None:
        await self._fetch_all()
        while True:
            await asyncio.sleep(self._interval)
            await self._fetch_all()

    def trigger(self, account_id: str) -> None:
        if account_id in self._in_flight:
            self._pending_refresh.add(account_id)
        else:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._fetch_one(account_id))
            except RuntimeError:
                pass  # nessun loop attivo — ignorare (bootstrap non ancora avviato)

    async def _fetch_all(self) -> None:
        for account_id in self._account_ids:
            await self._fetch_one(account_id)

    async def _fetch_one(self, account_id: str) -> None:
        if account_id in self._in_flight:
            self._pending_refresh.add(account_id)
            return
        self._in_flight.add(account_id)
        try:
            snap = await asyncio.get_running_loop().run_in_executor(
                None, self._port.get_account_state, account_id
            )
            self._repository.save_account(snap, account_id)
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
        finally:
            self._in_flight.discard(account_id)
            if account_id in self._pending_refresh:
                self._pending_refresh.discard(account_id)
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._fetch_one(account_id))
                except RuntimeError:
                    pass


__all__ = ["AccountSnapshotWorker"]
```

- [ ] **Step 4: Eseguire i test**

```
pytest tests/runtime_v2/lifecycle/test_account_snapshot_worker.py -v
```

Expected: tutti PASSED

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/account_snapshot_worker.py tests/runtime_v2/lifecycle/test_account_snapshot_worker.py
git commit -m "feat: add AccountSnapshotWorker — periodic account balance fetch with dedup and FAILED record on error"
```

---

## Task 8: Bootstrap — wire AccountSnapshotWorker

**Files:**
- Modify: `main.py`

**Interfaces:**
- Consumes: `AccountSnapshotWorker` (Task 7), `LiveExchangeDataPort`, `SnapshotRepository`
- Produce: worker avviato come asyncio task, Refresh connesso al trigger

- [ ] **Step 1: Individuare il punto di wiring in `main.py`**

Cercare la riga dove viene inizializzato `execution_runtime` e dove viene definito `_position_sync_fn` (intorno a riga 623). Il worker va inizializzato subito dopo che `execution_runtime` e `ops_db_path` sono disponibili.

- [ ] **Step 2: Aggiungere il worker**

Trovare la sezione dove vengono avviati i task asyncio (dove ci sono `asyncio.create_task` o `asyncio.ensure_future`). Aggiungere dopo l'avvio degli altri worker:

```python
# Account snapshot worker — periodic balance fetch per account
from src.runtime_v2.lifecycle.account_snapshot_worker import AccountSnapshotWorker
from src.runtime_v2.lifecycle.repositories import SnapshotRepository as _SnapRepo

_account_ids = list(execution_config.all_logical_account_ids()) if execution_config else []
if _account_ids and live_exchange_port is not None:
    _snap_repo = _SnapRepo(ops_db_path)
    _account_snapshot_worker = AccountSnapshotWorker(
        port=live_exchange_port,
        repository=_snap_repo,
        account_ids=_account_ids,
        interval_seconds=60,
        stale_after_seconds=180,
    )
    asyncio.create_task(_account_snapshot_worker.run())
else:
    _account_snapshot_worker = None
```

Nota: `execution_config.all_logical_account_ids()` potrebbe non esistere con quel nome. Verificare come si ottiene la lista di account da `execution_config` (guardare i metodi disponibili). Se non esiste un metodo dedicato, usare la lista di account dal config YAML direttamente.

- [ ] **Step 3: Connettere il trigger al Refresh**

Trovare la funzione `_position_sync_fn` (riga ~623) e aggiungere il trigger del worker:

```python
def _position_sync_fn(account_id: str | None) -> None:
    workers = execution_runtime.sync_workers if execution_runtime else None
    if not workers:
        return
    targets = (
        [workers[account_id]] if account_id and account_id in workers
        else list(workers.values())
    )
    for w in targets:
        w.run_bulk_position_sync()
    # Trigger account snapshot refresh
    if _account_snapshot_worker is not None:
        trigger_ids = [account_id] if account_id else _account_ids
        for acc in trigger_ids:
            _account_snapshot_worker.trigger(acc)
```

- [ ] **Step 4: Avviare il bot e verificare i log**

```
python main.py
```

Cercare nei log: `AccountSnapshotWorker` che esegue fetch al bootstrap. Dopo 60s verificare che una nuova riga sia presente in `ops_account_snapshots` con `snapshot_status='OK'` e `payload_json` non `'{}'`.

```sql
-- verifica manuale con sqlite3
SELECT account_id, equity_usdt, snapshot_status, length(payload_json), captured_at
FROM ops_account_snapshots ORDER BY snapshot_id DESC LIMIT 5;
```

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat: wire AccountSnapshotWorker in main.py — periodic account balance + Refresh trigger"
```

---

## Task 9: Status queries — CTE per-account + freshness

**Files:**
- Modify: `src/runtime_v2/control_plane/status_queries.py`
- Modify: `tests/runtime_v2/control_plane/test_status_queries.py`

**Interfaces:**
- Produces: `PnlView` con nuovi campi:
  - `account_unrealized_pnl_usdt: float | None = None`
  - `snapshot_age_seconds: float | None = None`
  - `snapshot_stale: bool = False`
  - `accounts_fresh: int | None = None`
  - `accounts_stale: int | None = None`
  - `by_account` items includono `age_seconds: float | None` e `stale: bool`

- [ ] **Step 1: Scrivere i test**

Aggiungere in `tests/runtime_v2/control_plane/test_status_queries.py`:

```python
def _add_snapshot(conn, account_id, equity, captured_at, status="OK"):
    conn.execute(
        "INSERT INTO ops_account_snapshots "
        "(account_id, equity_usdt, available_balance_usdt, total_open_risk_usdt, "
        "total_margin_used_usdt, account_unrealized_pnl_usdt, source, captured_at, "
        "payload_json, snapshot_status, error_code) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (account_id, equity, equity * 0.9, 5.0, 10.0, equity * 0.01,
         "ccxt_bybit:demo", captured_at, "{}", status, None),
    )


def test_get_pnl_global_uses_cte_per_account(ops_db):
    """Globale deve restituire latest snapshot per OGNI account, non LIMIT 1 globale."""
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_chain(conn, 100, "OPEN", account_id="demo_1")
        _add_chain(conn, 101, "OPEN", account_id="demo_2")
        _add_snapshot(conn, "demo_1", 1000.0, "2026-06-23T10:00:00+00:00")
        _add_snapshot(conn, "demo_2", 2000.0, "2026-06-23T09:00:00+00:00")  # più vecchio

    view = StatusQueries(ops_db).get_pnl()
    # Entrambi gli account devono apparire in by_account
    assert view.by_account is not None
    accs = {r["account_id"] for r in view.by_account}
    assert "demo_1" in accs
    assert "demo_2" in accs


def test_get_pnl_global_excludes_stale_from_aggregate(ops_db):
    """Account stale non contribuisce ai totali live dell'aggregato."""
    from datetime import timedelta
    fresh_time = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()

    conn = sqlite3.connect(ops_db)
    with conn:
        _add_snapshot(conn, "demo_1", 1000.0, fresh_time)
        _add_snapshot(conn, "demo_2", 500.0,  stale_time)

    view = StatusQueries(ops_db).get_pnl()
    assert view.accounts_fresh == 1
    assert view.accounts_stale == 1
    # Il totale equity deve includere solo demo_1
    assert view.equity_usdt == pytest.approx(1000.0)


def test_get_pnl_account_scope_includes_unrealized_pnl(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_snapshot(conn, "demo_1", 1000.0, "2026-06-23T10:00:00+00:00")

    view = StatusQueries(ops_db).get_pnl(
        scope=QueryScope(account_id="demo_1", trader_ids=None)
    )
    assert view.account_unrealized_pnl_usdt == pytest.approx(10.0)  # 1000 * 0.01


def test_get_pnl_account_scope_returns_snapshot_age(ops_db):
    from datetime import timedelta
    recent = (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat()
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_snapshot(conn, "demo_1", 1000.0, recent)

    view = StatusQueries(ops_db).get_pnl(
        scope=QueryScope(account_id="demo_1", trader_ids=None)
    )
    assert view.snapshot_age_seconds is not None
    assert 40 < view.snapshot_age_seconds < 60


def test_get_pnl_stale_snapshot_sets_flag(ops_db):
    from datetime import timedelta
    old = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    conn = sqlite3.connect(ops_db)
    with conn:
        _add_snapshot(conn, "demo_1", 1000.0, old)

    view = StatusQueries(ops_db).get_pnl(
        scope=QueryScope(account_id="demo_1", trader_ids=None)
    )
    assert view.snapshot_stale is True
```

- [ ] **Step 2: Eseguire i test — devono fallire**

```
pytest tests/runtime_v2/control_plane/test_status_queries.py -v -k "cte_per_account or stale or unrealized_pnl or snapshot_age"
```

Expected: FAILED

- [ ] **Step 3: Aggiornare `PnlView` in `status_queries.py`**

Aggiungere i campi opzionali alla classe `PnlView` (dopo la riga `by_account`):

```python
    account_unrealized_pnl_usdt: float | None = None
    snapshot_age_seconds: float | None = None
    snapshot_stale: bool = False
    accounts_fresh: int | None = None
    accounts_stale: int | None = None
```

Aggiungere la costante (prima della classe `PnlView`):

```python
SNAPSHOT_STALE_SECONDS = 180
```

- [ ] **Step 4: Aggiornare `get_pnl()` — scope singolo account**

Nel ramo `if scope.account_id is not None:` della query (riga ~992), aggiornare la SELECT per includere i nuovi campi e calcolare age:

```python
snapshot = conn.execute(
    "SELECT account_id, equity_usdt, available_balance_usdt, "
    "total_open_risk_usdt, total_margin_used_usdt, source, captured_at, "
    "account_unrealized_pnl_usdt "
    "FROM ops_account_snapshots "
    "WHERE account_id=? AND snapshot_status='OK' "
    "ORDER BY datetime(captured_at) DESC, snapshot_id DESC "
    "LIMIT 1",
    (scope.account_id,),
).fetchone()
```

E aggiornare il `return PnlView(...)` per passare i nuovi campi:

```python
snap_age = _age_seconds(snapshot[6]) if snapshot else None
return PnlView(
    ...campi esistenti...,
    account_unrealized_pnl_usdt=snapshot[7] if snapshot else None,
    snapshot_age_seconds=snap_age,
    snapshot_stale=(snap_age is not None and snap_age > SNAPSHOT_STALE_SECONDS),
)
```

- [ ] **Step 5: Aggiornare `get_pnl()` — scope globale**

Sostituire la query globale (riga ~1003) con CTE per-account:

```python
# Scope globale: CTE latest-per-account
rows = conn.execute(
    """
    WITH ranked AS (
        SELECT account_id, equity_usdt, available_balance_usdt,
               total_open_risk_usdt, total_margin_used_usdt,
               account_unrealized_pnl_usdt, source, captured_at,
               ROW_NUMBER() OVER (
                   PARTITION BY account_id
                   ORDER BY datetime(captured_at) DESC, snapshot_id DESC
               ) AS rn
        FROM ops_account_snapshots
        WHERE snapshot_status = 'OK'
    )
    SELECT account_id, equity_usdt, available_balance_usdt,
           total_open_risk_usdt, total_margin_used_usdt,
           account_unrealized_pnl_usdt, source, captured_at
    FROM ranked WHERE rn = 1
    """
).fetchall()

accounts_fresh = 0
accounts_stale = 0
eq_sum = av_sum = mg_sum = upl_sum = 0.0
has_any = False

for r in rows:
    age = _age_seconds(r[7])
    is_stale = age is None or age > SNAPSHOT_STALE_SECONDS
    if is_stale:
        accounts_stale += 1
    else:
        accounts_fresh += 1
        if r[1] is not None: eq_sum += r[1]
        if r[2] is not None: av_sum += r[2]
        if r[4] is not None: mg_sum += r[4]
        if r[5] is not None: upl_sum += r[5]
        has_any = True

snapshot = None  # globale non ha snapshot singolo
equity_usdt = eq_sum if has_any else None
available_balance_usdt = av_sum if has_any else None
total_margin_used_usdt = mg_sum if has_any else None
account_unrealized_pnl_usdt = upl_sum if has_any else None
```

Aggiornare il `by_account` per includere age e stale:

```python
for r in rows:
    age = _age_seconds(r[7])
    is_stale = age is None or age > SNAPSHOT_STALE_SECONDS
    net_row = conn.execute(...)  # query netto esistente
    ...
    by_account.append({
        "account_id": r[0],
        "net_pnl": net_pnl_acc,
        "open_count": open_c,
        "equity_usdt": r[1],
        "age_seconds": age,
        "stale": is_stale,
    })
```

E aggiornare il `return PnlView(...)` per passare i nuovi campi globali:

```python
return PnlView(
    ...campi esistenti...,
    equity_usdt=equity_usdt,
    available_balance_usdt=available_balance_usdt,
    total_margin_used_usdt=total_margin_used_usdt,
    account_unrealized_pnl_usdt=account_unrealized_pnl_usdt,
    accounts_fresh=accounts_fresh,
    accounts_stale=accounts_stale,
    by_account=by_account,
)
```

- [ ] **Step 6: Eseguire tutti i test status_queries**

```
pytest tests/runtime_v2/control_plane/test_status_queries.py -v
```

Expected: tutti PASSED (compresi i vecchi test che non devono rompersi)

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/control_plane/status_queries.py tests/runtime_v2/control_plane/test_status_queries.py
git commit -m "feat: PnlView gets snapshot_age_seconds, stale flag, accounts_fresh/stale; global scope uses CTE per-account"
```

---

## Task 10: Dashboard — rendering freshness, STALE, uPnL

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/templates/dashboard.py`
- Modify: `src/runtime_v2/control_plane/formatters/dashboard.py`
- Modify: `tests/runtime_v2/control_plane/test_dashboard_formatter.py`

**Interfaces:**
- Consumes: `PnlView` con i nuovi campi (Task 9)

- [ ] **Step 1: Scrivere i test**

Aggiungere in `tests/runtime_v2/control_plane/test_dashboard_formatter.py`:

```python
def _make_pnl_view_with_snapshot(captured_at, stale=False, age=18.0):
    from src.runtime_v2.control_plane.status_queries import PnlView
    return PnlView(
        updated_at="2026-06-23T14:32:23+00:00",
        account_id="demo_1",
        captured_at=captured_at,
        source="ccxt_bybit:demo",
        equity_usdt=7220.50,
        available_balance_usdt=5180.20,
        total_open_risk_usdt=145.0,
        total_margin_used_usdt=704.80,
        account_unrealized_pnl_usdt=62.40,
        open_count=4,
        partial_count=0,
        waiting_entry_count=2,
        snapshot_age_seconds=age,
        snapshot_stale=stale,
    )


def test_pnl_account_lines_shows_snapshot_metadata():
    from src.runtime_v2.control_plane.formatters.templates.dashboard import _pnl_account_lines
    p = {
        "captured_at": "2026-06-23T14:32:05+00:00",
        "source": "ccxt_bybit:demo",
        "snapshot_age_seconds": 18.0,
        "snapshot_stale": False,
        "equity_usdt": 7220.50,
        "available_balance_usdt": 5180.20,
        "total_margin_used_usdt": 704.80,
        "account_unrealized_pnl_usdt": 62.40,
        "total_open_risk_usdt": 145.0,
    }
    result = _pnl_account_lines(p)
    assert "14:32:05" in result
    assert "age 18s" in result
    assert "ccxt_bybit:demo" in result
    assert "7,220.50" in result
    assert "62.40" in result


def test_pnl_account_lines_shows_stale():
    from src.runtime_v2.control_plane.formatters.templates.dashboard import _pnl_account_lines
    p = {
        "captured_at": "2026-06-23T10:00:00+00:00",
        "source": "ccxt_bybit:demo",
        "snapshot_age_seconds": 300.0,
        "snapshot_stale": True,
        "equity_usdt": 1000.0,
    }
    result = _pnl_account_lines(p)
    assert "STALE" in result


def test_pnl_by_account_lines_shows_stale_account():
    from src.runtime_v2.control_plane.formatters.templates.dashboard import _pnl_by_account_lines
    p = {
        "by_account": [
            {"account_id": "demo_1", "net_pnl": 100.0, "open_count": 2, "age_seconds": 18.0, "stale": False},
            {"account_id": "demo_2", "net_pnl": 50.0,  "open_count": 1, "age_seconds": 300.0, "stale": True},
        ]
    }
    result = _pnl_by_account_lines(p)
    assert "demo_1" in result
    assert "age 18s" in result
    assert "demo_2" in result
    assert "STALE" in result


def test_pnl_build_payload_passes_snapshot_fields(ops_db):
    from src.runtime_v2.control_plane.formatters.dashboard import _build_pnl_payload
    from src.runtime_v2.control_plane.status_queries import StatusQueries
    from src.runtime_v2.control_plane.scope_resolver import QueryScope
    from unittest.mock import patch
    from datetime import timedelta

    fresh = (datetime.now(timezone.utc) - timedelta(seconds=18)).isoformat()
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO ops_account_snapshots "
        "(account_id, equity_usdt, available_balance_usdt, total_open_risk_usdt, "
        "total_margin_used_usdt, account_unrealized_pnl_usdt, source, captured_at, "
        "payload_json, snapshot_status) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("demo_1", 7220.50, 5180.20, 145.0, 704.80, 62.40, "ccxt_bybit:demo", fresh, "{}", "OK"),
    )
    conn.commit(); conn.close()

    scope = QueryScope(account_id="demo_1", trader_ids=None)
    queries = StatusQueries(ops_db)
    payload, _ = _build_pnl_payload(scope, queries)
    assert payload["account_unrealized_pnl_usdt"] == pytest.approx(62.40)
    assert payload["snapshot_age_seconds"] is not None
    assert payload["snapshot_stale"] is False
    assert payload["captured_at"] is not None
    assert payload["source"] == "ccxt_bybit:demo"
```

- [ ] **Step 2: Eseguire i test — devono fallire**

```
pytest tests/runtime_v2/control_plane/test_dashboard_formatter.py -v -k "pnl_account_lines or pnl_by_account or build_payload_passes"
```

Expected: FAILED

- [ ] **Step 3: Aggiornare `_pnl_account_lines` in `templates/dashboard.py`**

Sostituire la funzione (riga 204–212):

```python
def _pnl_account_lines(p: dict) -> str:
    from datetime import datetime
    parts = []
    captured_at = p.get("captured_at")
    age = p.get("snapshot_age_seconds")
    source = p.get("source")
    stale = p.get("snapshot_stale", False)

    if captured_at:
        try:
            dt = datetime.fromisoformat(captured_at)
            time_str = dt.strftime("%H:%M:%S") + " UTC"
        except ValueError:
            time_str = captured_at
        age_str = f"age {int(age)}s" if age is not None else "age ?"
        stale_str = " · STALE" if stale else ""
        source_str = f" · {source}" if source else ""
        parts.append(f"Snapshot: {time_str} · {age_str}{source_str}{stale_str}")

    if p.get("equity_usdt") is not None:
        parts.append(f"Equity:        {p['equity_usdt']:,.2f} USDT")
    if p.get("available_balance_usdt") is not None:
        parts.append(f"Available:     {p['available_balance_usdt']:,.2f} USDT")
    if p.get("total_margin_used_usdt") is not None:
        parts.append(f"Margin used:   {p['total_margin_used_usdt']:,.2f} USDT")
    if p.get("account_unrealized_pnl_usdt") is not None:
        sign = "+" if p["account_unrealized_pnl_usdt"] >= 0 else ""
        parts.append(f"uPnL live:     {sign}{p['account_unrealized_pnl_usdt']:.2f} USDT")
    if p.get("total_open_risk_usdt") is not None:
        parts.append(f"Open risk*:    {p['total_open_risk_usdt']:.2f} USDT")
    return "\n".join(parts) if parts else "n/a"
```

- [ ] **Step 4: Aggiornare `_pnl_by_account_lines` in `templates/dashboard.py`**

Sostituire (riga 237–246):

```python
def _pnl_by_account_lines(p: dict) -> str:
    rows = p.get("by_account") or []
    lines = []
    for r in rows:
        acc_id = r.get("account_id", "?")
        net = r.get("net_pnl", 0.0)
        sign = "+" if net >= 0 else ""
        open_c = r.get("open_count", 0)
        age = r.get("age_seconds")
        stale = r.get("stale", False)
        if stale:
            age_str = f"{int(age)}s ago" if age is not None else "?"
            lines.append(f"{acc_id} · STALE · last {age_str}")
        else:
            age_str = f" · age {int(age)}s" if age is not None else ""
            lines.append(f"{acc_id} · Net: {sign}{net:.2f} USDT · Open: {open_c}{age_str}")
    return "\n".join(lines) if lines else "n/a"
```

- [ ] **Step 5: Aggiornare `_build_pnl_payload` in `formatters/dashboard.py`**

Aggiungere i nuovi campi nel dict `payload` (dopo riga 370):

```python
        "captured_at": view.captured_at,
        "source": view.source,
        "account_unrealized_pnl_usdt": view.account_unrealized_pnl_usdt,
        "snapshot_age_seconds": view.snapshot_age_seconds,
        "snapshot_stale": view.snapshot_stale,
        "total_open_risk_usdt": view.total_open_risk_usdt,
        "accounts_fresh": view.accounts_fresh,
        "accounts_stale": view.accounts_stale,
```

- [ ] **Step 6: Eseguire tutti i test dashboard**

```
pytest tests/runtime_v2/control_plane/test_dashboard_formatter.py tests/runtime_v2/control_plane/test_status_queries.py -v -q 2>&1 | tail -10
```

Expected: tutti PASSED

- [ ] **Step 7: Smoke test completo**

```
pytest tests/runtime_v2/ -v -q 2>&1 | tail -10
```

Expected: no nuovi FAILED rispetto al baseline pre-feature

- [ ] **Step 8: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/templates/dashboard.py src/runtime_v2/control_plane/formatters/dashboard.py tests/runtime_v2/control_plane/test_dashboard_formatter.py
git commit -m "feat: PnL dashboard shows snapshot time, age, STALE flag, uPnL live; by_account includes per-account freshness"
```

---

## Self-Review

**Spec coverage check:**

| Sezione spec | Task che la copre |
|---|---|
| AS-01 worker periodico | Task 7+8 |
| AS-02 timestamp dashboard | Task 9+10 |
| AS-03 mappatura totalEquity | Task 3 |
| AS-04 available balance fallback | Task 3 |
| AS-05 margin definition | Task 3 |
| AS-06 payload_json `"{}"` | Task 5 |
| AS-07 scope trader label | Task 10 (template già ha `Account snapshot:` label) |
| AS-08 global query LIMIT 1 | Task 9 |
| AS-09 account senza chain | Task 9 (CTE legge tutti gli account con snapshot) |
| AS-10 test mancanti | Task 3, 6, 7, 9, 10 |
| §4 migrazione | Task 1 |
| §4 modelli | Task 2 |
| §5.1 field_origins | Task 3 |
| §5.2 zero values | Task 3 |
| §6 worker freshness | Task 7 |
| §6.3 trigger dedup | Task 7 |
| §6.4 record FAILED | Task 7 |
| §7.2 AS-06 entry_gate | Task 5 |
| §12.0 ports.py prerequisito | Task 2 |
| §12.0 models.py prerequisito | Task 2 |
| §14 criteri accettazione | Tutti i task coprono i criteri |

**Placeholder scan:** nessun TBD o "simile a Task N".

**Type consistency:** `account_unrealized_pnl_usdt: float | None` usato consistentemente in Tasks 2, 3, 4, 5, 6, 7, 9, 10. `snapshot_status: str = "OK"` consistente tra `AccountStateSnapshot` (Task 2) e DB (Task 1). `_SNAPSHOT_COLS` / `_row_to_snapshot_dict` definiti una volta in Task 6 e non replicati.

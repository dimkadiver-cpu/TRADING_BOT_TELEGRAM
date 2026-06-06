# ROI Net Peak Margin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separare semanticamente `ROI net` da `Return on Risk`, calcolare `ROI net` sul `peak_margin_used` reale della chain e introdurre il minimo backfill compatibile per le chain storiche.

**Architecture:** La modifica resta nel runtime V2 esistente. Lo schema `ops_trade_chains` acquisisce due nuovi campi persistiti (`initial_risk_amount`, `peak_margin_used`), `entry_gate.py` continua a essere l'owner della creazione chain, `workers.py` mantiene il picco con update monotono sullo stato post-evento, e `outbox_writer.py` usa il nuovo denominatore per `final_result` senza inventare fallback falsi.

**Tech Stack:** Python, SQLite migrations raw SQL, Pydantic v2, pytest, runtime_v2 lifecycle/control-plane

**Acceptance Contract:**

- Done significa che ogni nuova chain salva `initial_risk_amount`, ogni evento lifecycle rilevante può alzare `peak_margin_used`, e il `POSITION_CLOSED` finale usa `peak_margin_used` come base di `roi_net_pct`.
- Criterio 1: una chain nuova con `risk_snapshot_json.risk_amount=100.0` salva `initial_risk_amount=100.0` senza cambiare la semantica di `risk_snapshot_json`.
- Criterio 2: il lifecycle worker aggiorna `peak_margin_used` in modo monotono su first fill, partial close e scale-in.
- Criterio 3: `final_result["roi_net_pct"]` usa `peak_margin_used`; `final_result["return_on_risk_pct"]` usa `initial_risk_amount`; denominatori assenti producono `None`.
- Criterio 4: il backfill minimo non inventa valori quando i dati sono insufficienti; lascia `peak_margin_used` a `NULL` e il formatter continua a mostrare `n/a`.
- Criterio 5: documentazione runtime allineata alla nuova semantica, con drift esplicito rimosso nei file indicati dalla spec.

---

## File Structure

- Create: `db/ops_migrations/013_ops_roi_peak_margin.sql`
  - Responsabilita': aggiungere `initial_risk_amount` e `peak_margin_used` a `ops_trade_chains`.
- Create: `tests/runtime_v2/control_plane/test_migration_013.py`
  - Responsabilita': verificare che la migration esponga le nuove colonne.
- Modify: `src/runtime_v2/lifecycle/models.py`
  - Responsabilita': estendere `TradeChain` con i nuovi campi persistiti.
- Modify: `src/runtime_v2/lifecycle/repositories.py`
  - Responsabilita': leggere/scrivere i nuovi campi, mantenere compatibilita' con `allocated_margin`.
- Modify: `tests/runtime_v2/lifecycle/test_repositories.py`
  - Responsabilita': verificare `initial_risk_amount` e round-trip repository.
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
  - Responsabilita': popolare `initial_risk_amount` nel path reale di insert chain.
- Modify: `src/runtime_v2/lifecycle/workers.py`
  - Responsabilita': calcolare `current_margin_used` sullo stato post-evento e mantenere `peak_margin_used`.
- Modify: `tests/runtime_v2/lifecycle/test_workers.py`
  - Responsabilita': coprire first fill, partial close, scale-in e close finale senza riduzione del picco.
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
  - Responsabilita': calcolare `roi_net_pct` con `peak_margin_used` e aggiungere `return_on_risk_pct`.
- Modify: `tests/runtime_v2/control_plane/test_outbox_writer.py`
  - Responsabilita': verificare `final_result` nuovo e fallback `None`.
- Create: `src/runtime_v2/control_plane/backfill_peak_margin.py`
  - Responsabilita': backfill minimo degradato per chain esistenti.
- Create: `tests/runtime_v2/control_plane/test_backfill_peak_margin.py`
  - Responsabilita': verificare fallback ammesso e caso insufficiente.
- Modify: `docs/runtime_v2/exchange_sync_technical.md`
- Modify: `docs/runtime_v2/exchange_sync_overview.md`
- Modify: `docs/Raggionamento/Controllo_Notifica/CLEAN_LOG_SPEC.md`
  - Responsabilita': allineare la semantica user-facing di `ROI net`.

## Task 1: Estendere schema, modello e repository per i nuovi campi persistiti

**Files:**
- Create: `db/ops_migrations/013_ops_roi_peak_margin.sql`
- Create: `tests/runtime_v2/control_plane/test_migration_013.py`
- Modify: `src/runtime_v2/lifecycle/models.py`
- Modify: `src/runtime_v2/lifecycle/repositories.py`
- Modify: `tests/runtime_v2/lifecycle/test_repositories.py`

- [ ] **Step 1: Scrivere il test migration che richiede le nuove colonne**

Creare `tests/runtime_v2/control_plane/test_migration_013.py` con:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path


def test_migration_013_adds_roi_peak_margin_columns(tmp_path):
    db_path = tmp_path / "ops.sqlite3"
    conn = sqlite3.connect(str(db_path))
    for migration in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(ops_trade_chains)")}
    conn.close()
    assert {"initial_risk_amount", "peak_margin_used"} <= columns
```

- [ ] **Step 2: Eseguire il test migration e verificare che fallisca**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_migration_013.py -q
```

Expected: `FAIL` per colonne mancanti in `ops_trade_chains`.

- [ ] **Step 3: Aggiungere la migration SQL**

Creare `db/ops_migrations/013_ops_roi_peak_margin.sql` con:

```sql
ALTER TABLE ops_trade_chains ADD COLUMN initial_risk_amount REAL;
ALTER TABLE ops_trade_chains ADD COLUMN peak_margin_used REAL;
```

- [ ] **Step 4: Estendere `TradeChain` e il repository**

In `src/runtime_v2/lifecycle/models.py`, aggiungere a `TradeChain`:

```python
    initial_risk_amount: float | None = None
    peak_margin_used: float | None = None
```

In `src/runtime_v2/lifecycle/repositories.py`, aggiornare `_CHAIN_COLS`, `_chain_from_row()` e `save()` in questo modo:

```python
_CHAIN_COLS = (
    "trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
    "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
    "entry_avg_price, current_stop_price, expected_stop_price, be_protection_status, "
    "entry_timeout_at, management_plan_json, risk_snapshot_json, "
    "planned_entry_qty, filled_entry_qty, open_position_qty, closed_position_qty, "
    "last_position_sync_at, execution_mode, risk_already_realized, risk_remaining, "
    "plan_state_json, source_chat_id, telegram_message_id, cumulative_gross_pnl, "
    "cumulative_fees, cumulative_funding, allocated_margin, initial_risk_amount, "
    "peak_margin_used, created_at, updated_at"
)
```

```python
        initial_risk_amount=initial_risk_amount,
        peak_margin_used=peak_margin_used,
```

```python
        initial_risk_amount = chain.initial_risk_amount
        allocated_margin = None
        if chain.risk_snapshot_json:
            try:
                risk_snapshot = json.loads(chain.risk_snapshot_json)
                raw_risk = risk_snapshot.get("risk_amount")
                if raw_risk is not None and initial_risk_amount is None:
                    initial_risk_amount = float(raw_risk)
                if raw_risk is not None:
                    allocated_margin = float(raw_risk)
            except (TypeError, ValueError, Exception):
                allocated_margin = None
```

- [ ] **Step 5: Aggiornare i test repository**

In `tests/runtime_v2/lifecycle/test_repositories.py`, sostituire il test legacy con:

```python
def test_chain_repo_save_populates_initial_risk_amount_from_risk_amount(ops_db):
    import json
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.lifecycle.repositories import TradeChainRepository

    repo = TradeChainRepository(ops_db)
    chain = TradeChain(
        source_enrichment_id=99,
        canonical_message_id=990,
        raw_message_id=9900,
        trader_id="trader_a",
        account_id="main",
        symbol="BTC/USDT",
        side="LONG",
        lifecycle_state="WAITING_ENTRY",
        entry_mode="ONE_SHOT",
        management_plan_json="{}",
        risk_snapshot_json=json.dumps({"risk_amount": 100.0}),
    )
    saved = repo.save(chain)
    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT allocated_margin, initial_risk_amount FROM ops_trade_chains WHERE trade_chain_id=?",
        (saved.trade_chain_id,),
    ).fetchone()
    conn.close()
    assert row == (100.0, 100.0)
```

- [ ] **Step 6: Rieseguire i test schema/repository**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_migration_013.py tests\runtime_v2\lifecycle\test_repositories.py -q
```

Expected: test verdi; il repository continua a salvare `allocated_margin` per compatibilita', ma introduce `initial_risk_amount`.

- [ ] **Step 7: Non fare commit automatici**

Non eseguire `git add` o `git commit` senza richiesta esplicita dell'utente.

## Task 2: Popolare `initial_risk_amount` nel path reale di creazione chain

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Modify: `tests/runtime_v2/lifecycle/test_workers.py`

- [ ] **Step 1: Aggiungere il test che verifica il path reale `entry_gate.py`**

In `tests/runtime_v2/lifecycle/test_workers.py`, subito dopo `test_worker_processes_signal_creates_chain`, aggiungere:

```python
def test_worker_processes_signal_persists_initial_risk_amount(dbs):
    parser_db, ops_db = dbs
    enriched = _make_enriched_signal(enrichment_id=11, risk_pct=1.0, capital_base_usdt=10000.0)
    _insert_enriched(parser_db, 11, enriched)

    worker = _make_worker(parser_db, ops_db)
    count = worker.run_once()
    assert count == 1

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT initial_risk_amount, peak_margin_used FROM ops_trade_chains"
    ).fetchone()
    conn.close()
    assert row[0] == pytest.approx(100.0)
    assert row[1] is None
```

- [ ] **Step 2: Eseguire il test e verificare che fallisca**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_workers.py::test_worker_processes_signal_persists_initial_risk_amount -q
```

Expected: `FAIL` per colonna non popolata nel path `INSERT OR IGNORE` di `entry_gate.py`.

- [ ] **Step 3: Modificare l'insert diretto in `entry_gate.py`**

Nel blocco `INSERT OR IGNORE INTO ops_trade_chains` dentro `_persist_signal()`, aggiungere le colonne e i valori:

```python
                        INSERT OR IGNORE INTO ops_trade_chains (
                            source_enrichment_id, canonical_message_id, raw_message_id,
                            trader_id, account_id, symbol, side, lifecycle_state, entry_mode,
                            entry_avg_price, current_stop_price, expected_stop_price,
                            be_protection_status, entry_timeout_at, management_plan_json,
                            risk_snapshot_json, planned_entry_qty, filled_entry_qty,
                            open_position_qty, closed_position_qty, last_position_sync_at,
                            execution_mode, risk_already_realized, risk_remaining,
                            plan_state_json, source_chat_id, telegram_message_id,
                            initial_risk_amount, peak_margin_used,
                            created_at, updated_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
```

Prima dell'`execute`, calcolare:

```python
                    initial_risk_amount = None
                    try:
                        risk_snapshot = json.loads(c.risk_snapshot_json or "{}")
                        raw_risk = risk_snapshot.get("risk_amount")
                        if raw_risk is not None:
                            initial_risk_amount = float(raw_risk)
                    except Exception:
                        initial_risk_amount = None
```

e passare:

```python
                            c.plan_state_json, src_chat_id, tg_msg_id,
                            initial_risk_amount, None,
                            now, now,
```

- [ ] **Step 4: Rieseguire il test worker mirato**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_workers.py::test_worker_processes_signal_persists_initial_risk_amount -q
```

Expected: `PASS`

- [ ] **Step 5: Verificare regressione minima sul flusso signal**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_workers.py::test_worker_processes_signal_creates_chain tests\runtime_v2\lifecycle\test_workers.py::test_worker_marks_lifecycle_processed tests\runtime_v2\lifecycle\test_workers.py::test_worker_idempotent_on_double_run -q
```

Expected: `3 passed`

## Task 3: Mantenere `peak_margin_used` nel lifecycle worker con update monotono

**Files:**
- Modify: `src/runtime_v2/lifecycle/workers.py`
- Modify: `tests/runtime_v2/lifecycle/test_workers.py`

- [ ] **Step 1: Scrivere i test per first fill, partial close e scale-in**

Aggiungere in `tests/runtime_v2/lifecycle/test_workers.py` questi tre test:

```python
def test_worker_entry_fill_sets_peak_margin_used(tmp_path):
    import json as _json
    import sqlite3 as _sqlite3
    from pathlib import Path
    from unittest.mock import MagicMock
    from src.runtime_v2.lifecycle.event_processor import EventProcessorResult
    from src.runtime_v2.lifecycle.models import LifecycleEvent
    from src.runtime_v2.lifecycle.workers import LifecycleEventWorker
    from src.runtime_v2.lifecycle.repositories import (
        ExecutionCommandRepository, ExchangeEventRepository,
        LifecycleEventRepository, TradeChainRepository,
    )

    db = str(tmp_path / "ops.sqlite3")
    conn = _sqlite3.connect(db)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    now_str = "2026-06-06T00:00:00+00:00"
    chain_id = 301
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
        "trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
        "risk_snapshot_json, created_at, updated_at) "
        "VALUES (?,1,1,1,'t','main','BTC/USDT','LONG','WAITING_ENTRY','ONE_SHOT',?,?,?)",
        (chain_id, _json.dumps({"leverage": 5, "risk_amount": 100.0}), now_str, now_str),
    )
    conn.commit()
    conn.close()

    result = EventProcessorResult(
        new_lifecycle_state="OPEN",
        new_be_protection_status=None,
        entry_avg_price=65000.0,
        current_stop_price=None,
        lifecycle_events=[LifecycleEvent(
            trade_chain_id=chain_id,
            event_type="ENTRY_FILLED",
            source_type="exchange_event",
            payload_json=_json.dumps({"fill_price": 65000.0, "filled_qty": 0.01}),
            idempotency_key=f"entry:{chain_id}:1",
        )],
        execution_commands=[],
        new_filled_entry_qty=0.01,
        new_open_position_qty=0.01,
    )

    worker = LifecycleEventWorker(
        ops_db_path=db,
        processor=MagicMock(),
        chain_repo=TradeChainRepository(db),
        event_repo=LifecycleEventRepository(db),
        command_repo=ExecutionCommandRepository(db),
        exchange_event_repo=MagicMock(),
    )
    worker._persist_result(chain_id, result)

    conn2 = _sqlite3.connect(db)
    peak = conn2.execute(
        "SELECT peak_margin_used FROM ops_trade_chains WHERE trade_chain_id=?",
        (chain_id,),
    ).fetchone()[0]
    conn2.close()
    assert peak == pytest.approx(130.0)
```

```python
def test_worker_partial_close_does_not_reduce_peak_margin_used(tmp_path):
    # seeded chain: entry_avg_price=65000, leverage=5, existing_peak=260, open_position_qty=0.02
    # result: new_open_position_qty=0.01 -> current margin 130, peak must remain 260
```

```python
def test_worker_scale_in_raises_peak_margin_used(tmp_path):
    # seeded chain: entry_avg_price=65000, leverage=5, existing_peak=130, open_position_qty=0.01
    # result: entry_avg_price=65500, new_open_position_qty=0.03 -> current margin 393, peak must become 393
```

Nei due test compatti sopra, replicare lo stesso harness del primo variando solo i valori SQL seed e le assertion finali.

- [ ] **Step 2: Eseguire i test nuovi e verificare che falliscano**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_workers.py::test_worker_entry_fill_sets_peak_margin_used tests\runtime_v2\lifecycle\test_workers.py::test_worker_partial_close_does_not_reduce_peak_margin_used tests\runtime_v2\lifecycle\test_workers.py::test_worker_scale_in_raises_peak_margin_used -q
```

Expected: `FAIL` per `peak_margin_used` mancante o invariato a `NULL`.

- [ ] **Step 3: Introdurre helper puro nel worker**

In `src/runtime_v2/lifecycle/workers.py`, sopra `LifecycleEventWorker`, aggiungere:

```python
def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _compute_peak_margin_update(
    *,
    chain_row: tuple,
    result: EventProcessorResult,
) -> float | None:
    current_entry_avg, current_open_qty, risk_snapshot_json, existing_peak = chain_row
    effective_entry_avg = (
        result.entry_avg_price if result.entry_avg_price is not None else _safe_float(current_entry_avg)
    )
    effective_open_qty = (
        result.new_open_position_qty if result.new_open_position_qty is not None else _safe_float(current_open_qty)
    )
    leverage = None
    try:
        leverage = _safe_float(json.loads(risk_snapshot_json or "{}").get("leverage"))
    except Exception:
        leverage = None
    if effective_entry_avg is None or effective_open_qty is None or leverage is None or leverage <= 0:
        return _safe_float(existing_peak)
    if effective_open_qty <= 0:
        return _safe_float(existing_peak)
    current_margin_used = effective_open_qty * effective_entry_avg / leverage
    base_peak = _safe_float(existing_peak) or 0.0
    return max(base_peak, current_margin_used)
```

- [ ] **Step 4: Usare l'helper nello stesso transaction boundary di `_persist_result()`**

Nel blocco `with conn:` di `_persist_result()`, prima dell'`UPDATE ops_trade_chains`, leggere lo stato corrente:

```python
                chain_row = conn.execute(
                    "SELECT entry_avg_price, open_position_qty, risk_snapshot_json, peak_margin_used "
                    "FROM ops_trade_chains WHERE trade_chain_id=?",
                    (chain_id,),
                ).fetchone()
                new_peak_margin_used = (
                    _compute_peak_margin_update(chain_row=chain_row, result=result)
                    if chain_row is not None
                    else None
                )
```

Poi, quando `has_chain_update` e' vero, aggiungere:

```python
                    if new_peak_margin_used is not None:
                        fields.append("peak_margin_used=?")
                        vals.append(new_peak_margin_used)
```

e includere `or result.entry_avg_price is not None or result.new_open_position_qty is not None` nel trigger logico già esistente, senza creare un secondo `UPDATE`.

- [ ] **Step 5: Rieseguire i tre test del picco**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_workers.py::test_worker_entry_fill_sets_peak_margin_used tests\runtime_v2\lifecycle\test_workers.py::test_worker_partial_close_does_not_reduce_peak_margin_used tests\runtime_v2\lifecycle\test_workers.py::test_worker_scale_in_raises_peak_margin_used -q
```

Expected: `3 passed`

- [ ] **Step 6: Coprire il caso close finale a zero**

Aggiungere un quarto test:

```python
def test_worker_close_full_keeps_historical_peak_margin_used(tmp_path):
    # seeded chain: existing_peak=260, open_position_qty=0.02, leverage=5
    # result: new_open_position_qty=0.0 after CLOSE_FULL_FILLED
    # assert peak_margin_used remains 260.0
```

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_workers.py::test_worker_close_full_keeps_historical_peak_margin_used -q
```

Expected: `PASS`

- [ ] **Step 7: Eseguire la regressione worker minima**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_workers.py tests\runtime_v2\lifecycle\test_event_processor.py -q
```

Expected: suite verde sul lifecycle, senza regressioni su fill, TP, SL e accumulo PnL.

## Task 4: Proiettare `ROI net` su `peak_margin_used` e introdurre `Return on Risk`

**Files:**
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
- Modify: `tests/runtime_v2/control_plane/test_outbox_writer.py`

- [ ] **Step 1: Aggiungere il test sul nuovo `final_result`**

In `tests/runtime_v2/control_plane/test_outbox_writer.py`, subito dopo `test_position_closed_final_result_subtracts_positive_funding_cost`, aggiungere:

```python
def test_position_closed_final_result_uses_peak_margin_and_return_on_risk(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 712)
        conn.execute(
            "UPDATE ops_trade_chains "
            "SET cumulative_gross_pnl=?, cumulative_fees=?, cumulative_funding=?, "
            "peak_margin_used=?, initial_risk_amount=? "
            "WHERE trade_chain_id=?",
            (46.73088, 6.33921077, 0.0, 571.62, 200.0, 712),
        )
        _seed_event(conn, 712, "CLOSE_FULL_FILLED", "close_full:712:1", {
            "fill_price": 0.2539,
            "filled_qty": 6042.0,
            "exec_fee": 0.84373509,
            "closed_size": 6042.0,
        })
        project_clean_log_for_chain(conn, 712)
    row = conn.execute("SELECT payload_json FROM ops_notification_outbox").fetchone()
    conn.close()
    payload = json.loads(row[0])
    final_result = payload["final_result"]
    assert final_result["total_pnl_net"] == pytest.approx(40.39166923)
    assert final_result["roi_net_pct"] == pytest.approx(7.0667, rel=1e-3)
    assert final_result["return_on_risk_pct"] == pytest.approx(20.1958, rel=1e-3)
```

- [ ] **Step 2: Aggiungere il test per denominatori mancanti**

Nello stesso file, aggiungere:

```python
def test_position_closed_final_result_keeps_roi_none_when_peak_margin_missing(ops_db):
    conn = sqlite3.connect(ops_db)
    with conn:
        _seed_chain(conn, 713)
        conn.execute(
            "UPDATE ops_trade_chains "
            "SET cumulative_gross_pnl=?, cumulative_fees=?, cumulative_funding=?, "
            "peak_margin_used=?, initial_risk_amount=? "
            "WHERE trade_chain_id=?",
            (10.0, 1.0, 0.0, None, 50.0, 713),
        )
        _seed_event(conn, 713, "CLOSE_FULL_FILLED", "close_full:713:1", {"filled_qty": 1.0})
        project_clean_log_for_chain(conn, 713)
    row = conn.execute("SELECT payload_json FROM ops_notification_outbox").fetchone()
    conn.close()
    final_result = json.loads(row[0])["final_result"]
    assert final_result["roi_net_pct"] is None
    assert final_result["return_on_risk_pct"] == pytest.approx(18.0)
```

- [ ] **Step 3: Eseguire i test nuovi e verificare che falliscano**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_outbox_writer.py::test_position_closed_final_result_uses_peak_margin_and_return_on_risk tests\runtime_v2\control_plane\test_outbox_writer.py::test_position_closed_final_result_keeps_roi_none_when_peak_margin_missing -q
```

Expected: `FAIL` per colonne non lette e `final_result` ancora basato su `allocated_margin`.

- [ ] **Step 4: Aggiornare `_final_result()` e `project_clean_log_for_chain()`**

In `src/runtime_v2/control_plane/outbox_writer.py`, sostituire `_final_result()` con:

```python
def _final_result(
    *,
    gross_pnl: float | None,
    fees: float | None,
    funding: float | None,
    peak_margin_used: float | None,
    initial_risk_amount: float | None,
    close_reason: str,
) -> dict:
    gross = float(gross_pnl) if gross_pnl is not None else None
    fee_total = float(fees) if fees is not None else None
    funding_total = float(funding) if funding is not None else None
    net = None
    if gross is not None and fee_total is not None and funding_total is not None:
        net = gross - fee_total - funding_total
    roi = None
    if net is not None and peak_margin_used and float(peak_margin_used) > 0.0:
        roi = round(net / float(peak_margin_used) * 100.0, 4)
    return_on_risk = None
    if net is not None and initial_risk_amount and float(initial_risk_amount) > 0.0:
        return_on_risk = round(net / float(initial_risk_amount) * 100.0, 4)
    return {
        "roi_net_pct": roi,
        "return_on_risk_pct": return_on_risk,
        "total_pnl_net": round(net, 8) if net is not None else None,
        "gross_pnl": round(gross, 8) if gross is not None else None,
        "fees": round(-fee_total, 8) if fee_total is not None else None,
        "funding": round(-funding_total, 8) if funding_total is not None else None,
        "close_reason": close_reason,
    }
```

Aggiornare anche la query iniziale di `project_clean_log_for_chain()`:

```python
        "cumulative_gross_pnl, cumulative_fees, cumulative_funding, allocated_margin, "
        "initial_risk_amount, peak_margin_used, "
        "filled_entry_qty, open_position_qty, be_protection_status "
```

e passare ai call-site:

```python
                peak_margin_used=peak_margin_used,
                initial_risk_amount=initial_risk_amount,
```

- [ ] **Step 5: Rieseguire i test `outbox_writer` mirati**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_outbox_writer.py::test_position_closed_final_result_uses_peak_margin_and_return_on_risk tests\runtime_v2\control_plane\test_outbox_writer.py::test_position_closed_final_result_keeps_roi_none_when_peak_margin_missing tests\runtime_v2\control_plane\test_outbox_writer.py::test_position_closed_final_result_subtracts_positive_funding_cost -q
```

Expected: `3 passed`

- [ ] **Step 6: Eseguire la regressione control-plane minima**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_outbox_writer.py tests\runtime_v2\control_plane\test_clean_log_formatter.py -q
```

Expected: suite verde; il formatter continua a mostrare `n/a` quando il campo nel payload è `None`.

## Task 5: Implementare il backfill minimo degradato per le chain esistenti

**Files:**
- Create: `src/runtime_v2/control_plane/backfill_peak_margin.py`
- Create: `tests/runtime_v2/control_plane/test_backfill_peak_margin.py`

- [ ] **Step 1: Scrivere i test del backfill minimo**

Creare `tests/runtime_v2/control_plane/test_backfill_peak_margin.py` con:

```python
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.runtime_v2.control_plane.backfill_peak_margin import backfill_minimum_roi_fields


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/ops_migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def test_backfill_minimum_populates_initial_risk_and_peak_from_final_state(tmp_path):
    db = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO ops_trade_chains "
        "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, trader_id, "
        "account_id, symbol, side, lifecycle_state, entry_mode, risk_snapshot_json, "
        "entry_avg_price, filled_entry_qty, updated_at, created_at) "
        "VALUES (1,1,1,1,'t','main','BTCUSDT','CLOSED','ONE_SHOT',?,?,?,?,?)",
        (
            json.dumps({"risk_amount": 200.0, "leverage": 5}),
            28580.94,
            0.1,
            "2026-06-06T00:00:00+00:00",
            "2026-06-06T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    updated = backfill_minimum_roi_fields(db)
    assert updated == 1

    conn2 = sqlite3.connect(db)
    row = conn2.execute(
        "SELECT initial_risk_amount, peak_margin_used FROM ops_trade_chains WHERE trade_chain_id=1"
    ).fetchone()
    conn2.close()
    assert row[0] == 200.0
    assert row[1] == 571.6188
```

```python
def test_backfill_minimum_leaves_peak_null_when_data_insufficient(tmp_path):
    # seeded chain with risk_amount but missing leverage and entry_avg_price
    # assert initial_risk_amount populated and peak_margin_used stays NULL
```

- [ ] **Step 2: Eseguire i test nuovi e verificare che falliscano**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_backfill_peak_margin.py -q
```

Expected: `FAIL` per modulo mancante.

- [ ] **Step 3: Implementare il backfill minimo**

Creare `src/runtime_v2/control_plane/backfill_peak_margin.py` con:

```python
from __future__ import annotations

import json
import sqlite3


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def backfill_minimum_roi_fields(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    updated = 0
    try:
        rows = conn.execute(
            "SELECT trade_chain_id, risk_snapshot_json, initial_risk_amount, peak_margin_used, "
            "entry_avg_price, filled_entry_qty, open_position_qty "
            "FROM ops_trade_chains"
        ).fetchall()
        with conn:
            for chain_id, risk_json, initial_risk, peak_margin, entry_avg, filled_qty, open_qty in rows:
                try:
                    risk = json.loads(risk_json or "{}")
                except Exception:
                    risk = {}
                new_initial = _safe_float(initial_risk)
                if new_initial is None:
                    new_initial = _safe_float(risk.get("risk_amount"))

                new_peak = _safe_float(peak_margin)
                leverage = _safe_float(risk.get("leverage"))
                qty = _safe_float(filled_qty) or _safe_float(open_qty)
                price = _safe_float(entry_avg)
                if new_peak is None and leverage and leverage > 0 and qty and price:
                    new_peak = round(qty * price / leverage, 8)

                if new_initial != _safe_float(initial_risk) or new_peak != _safe_float(peak_margin):
                    conn.execute(
                        "UPDATE ops_trade_chains SET initial_risk_amount=?, peak_margin_used=? WHERE trade_chain_id=?",
                        (new_initial, new_peak, chain_id),
                    )
                    updated += 1
    finally:
        conn.close()
    return updated
```

- [ ] **Step 4: Rieseguire i test backfill**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_backfill_peak_margin.py -q
```

Expected: test verdi, con `peak_margin_used` lasciato `NULL` nei casi non difendibili.

- [ ] **Step 5: Limitare esplicitamente lo scope**

Non agganciare ancora il backfill a `main.py` o ad automazioni di startup. In questa fase la funzione deve restare invocabile da test/script dedicato, perché la spec definisce il replay/backfill corretto come fase successiva.

## Task 6: Allineare la documentazione runtime alla nuova semantica

**Files:**
- Modify: `docs/runtime_v2/exchange_sync_technical.md`
- Modify: `docs/runtime_v2/exchange_sync_overview.md`
- Modify: `docs/Raggionamento/Controllo_Notifica/CLEAN_LOG_SPEC.md`

- [ ] **Step 1: Cercare i punti con semantica vecchia**

Run:

```powershell
rg -n "allocated_margin|ROI net|return on risk|risk_amount" docs\runtime_v2\exchange_sync_technical.md docs\runtime_v2\exchange_sync_overview.md docs\Raggionamento\Controllo_Notifica\CLEAN_LOG_SPEC.md
```

Expected: riferimenti espliciti alla formula legacy o a `allocated_margin` come base del ROI.

- [ ] **Step 2: Aggiornare `exchange_sync_technical.md`**

Sostituire la definizione semantica con:

```markdown
- `ROI net` = `total_pnl_net / peak_margin_used * 100`
- `Return on Risk` = `total_pnl_net / initial_risk_amount * 100`
- `allocated_margin` resta campo legacy compatibile, non piu' fonte di verita' per il report finale.
```

- [ ] **Step 3: Aggiornare `exchange_sync_overview.md`**

Inserire una nota sintetica:

```markdown
Il report finale `POSITION CLOSED` usa il massimo margine reale storicamente impiegato (`peak_margin_used`) come denominatore del ROI. Il rischio monetario iniziale (`initial_risk_amount`) resta disponibile come metrica distinta di `Return on Risk`.
```

- [ ] **Step 4: Aggiornare `CLEAN_LOG_SPEC.md`**

Nel punto che descrive `final_result`, usare:

```markdown
- `roi_net_pct`: percentuale netta su `peak_margin_used`
- `return_on_risk_pct`: percentuale netta su `initial_risk_amount`
- se il denominatore richiesto manca o non e' difendibile, il campo resta `null` e il renderer mostra `n/a`
```

- [ ] **Step 5: Verifica finale docs**

Run:

```powershell
rg -n "ROI net = total_pnl_net / allocated_margin|allocated_margin.*ROI" docs\runtime_v2 docs\Raggionamento\Controllo_Notifica
```

Expected: nessun match residuo nei file allineati; eventuali match in documenti storici vanno lasciati fuori scope solo se archiviati.

## Self-Review

- Copertura spec:
  - schema nuovi campi: Task 1
  - owner reale `entry_gate.py`: Task 2
  - update monotono nel worker sullo stato post-evento: Task 3
  - `ROI net` su `peak_margin_used` e `Return on Risk` separato: Task 4
  - backfill minimo degradato: Task 5
  - allineamento docs: Task 6
- Placeholder scan:
  - nessun `TODO`, `TBD` o "handle appropriately"
  - i due test compatti del Task 3 e il secondo test del Task 5 vanno scritti replicando l'harness mostrato, senza cambiare API o inventare helper extra
- Coerenza tipi:
  - i nomi canonici sono sempre `initial_risk_amount`, `peak_margin_used`, `roi_net_pct`, `return_on_risk_pct`
  - `allocated_margin` resta solo compatibilita' legacy, mai denominatore primario del nuovo ROI

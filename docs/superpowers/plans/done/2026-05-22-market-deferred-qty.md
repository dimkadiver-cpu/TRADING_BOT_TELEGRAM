# MARKET Deferred Qty Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rimuovere il blocco che impedisce ai segnali MARKET senza mark_price di procedere, spostando il calcolo della qty al momento del submit ordine nel gateway, con supporto per ordini misti MARKET+LIMIT.

**Architecture:** Il risk engine calcola `risk_amount` per leg (indipendente dall'entry price) e lo salva nel `risk_snapshot`. L'entry gate produce payload con `qty_mode=deferred_market` per le leg MARKET senza mark_price. Il gateway fetcha il mark_price live al submit e calcola `qty = risk_amount / abs(mark_price - sl_price)`.

**Tech Stack:** Python 3.12, Pydantic v2, ccxt, pytest, sqlite3

---

## File map

| File | Azione | Responsabilità |
|------|--------|----------------|
| `src/runtime_v2/lifecycle/risk_capacity.py` | Modify | Rimuove blocco MARKET, aggiunge `legs` al risk_snapshot |
| `src/runtime_v2/lifecycle/entry_gate.py` | Modify | Legge `legs` dal snapshot, produce payload deferred per MARKET |
| `src/runtime_v2/execution_gateway/adapters/base.py` | Modify | Aggiunge `fetch_mark_price` all'interfaccia astratta |
| `src/runtime_v2/execution_gateway/adapters/fake.py` | Modify | Implementa `fetch_mark_price` configurabile per test |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py` | Modify | Implementa `fetch_mark_price` via ccxt |
| `src/runtime_v2/execution_gateway/gateway.py` | Modify | Risolve qty deferred prima di `place_order` |
| `tests/runtime_v2/lifecycle/test_risk_capacity.py` | Modify | Test per MARKET deferred + mixed legs |
| `tests/runtime_v2/lifecycle/test_entry_gate_cd.py` | Modify | Test per payload deferred in C/D mode |
| `tests/runtime_v2/execution_gateway/test_gateway.py` | Modify | Test per risoluzione qty nel gateway |
| `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py` | Modify | Test per fetch_mark_price |

---

## Task 1: RiskCapacityEngine — rimuovi blocco MARKET, aggiungi per-leg snapshot

**Files:**
- Modify: `src/runtime_v2/lifecycle/risk_capacity.py:62-141`
- Test: `tests/runtime_v2/lifecycle/test_risk_capacity.py`

- [ ] **Step 1.1: Scrivi i test che falliscono**

Aggiungi in fondo a `tests/runtime_v2/lifecycle/test_risk_capacity.py`:

```python
def test_market_entry_no_mark_price_passes():
    """MARKET senza mark_price non deve essere bloccato."""
    engine = RiskCapacityEngine()
    enriched = _make_enriched(entry_type="MARKET", entry_price=None)
    result = engine.validate(enriched, [], None, None)
    assert result.passed is True
    assert result.reason is None


def test_market_entry_no_mark_price_sets_deferred_flag():
    enriched = _make_enriched(entry_type="MARKET", entry_price=None)
    result = RiskCapacityEngine().validate(enriched, [], None, None)
    assert result.risk_snapshot["entry_price_deferred"] is True
    assert result.risk_snapshot["entry_price"] is None
    assert result.risk_snapshot["size_usdt"] is None


def test_market_entry_no_mark_price_legs_snapshot():
    """Il legs snapshot deve contenere qty_mode=deferred_market per leg MARKET senza mark_price."""
    enriched = _make_enriched(entry_type="MARKET", entry_price=None, sl_price=0.45)
    result = RiskCapacityEngine().validate(enriched, [], None, None)
    legs = result.risk_snapshot["legs"]
    assert len(legs) == 1
    assert legs[0]["qty_mode"] == "deferred_market"
    assert legs[0]["qty"] is None
    assert legs[0]["risk_amount"] > 0


def test_market_entry_with_mark_price_not_deferred():
    """MARKET con mark_price disponibile: comportamento invariato, non deferred."""
    engine = RiskCapacityEngine()
    enriched = _make_enriched(entry_type="MARKET", entry_price=None, sl_price=49000.0)
    snapshot = SymbolMarketSnapshot(
        symbol="BTC/USDT",
        mark_price=50000.0,
        source="test",
        captured_at=datetime.now(),
    )
    result = engine.validate(enriched, [], None, snapshot)
    assert result.passed is True
    assert result.risk_snapshot["entry_price_deferred"] is False
    assert result.risk_snapshot["entry_price"] == 50000.0
    assert result.risk_snapshot["size_usdt"] is not None
    legs = result.risk_snapshot["legs"]
    assert legs[0]["qty_mode"] == "fixed"
    assert legs[0]["qty"] is not None


def test_mixed_market_limit_per_leg_risk():
    """Multi-leg MARKET+LIMIT: risk_amount allocato per weight su ogni leg."""
    from src.runtime_v2.signal_enrichment.models import EnrichedEntryLeg

    entry_market = EnrichedEntryLeg(sequence=1, entry_type="MARKET", price=None, role="PRIMARY", weight=0.7)
    entry_limit = EnrichedEntryLeg(sequence=2, entry_type="LIMIT",
                                   price=_make_price(0.48), role="SECONDARY", weight=0.3)
    take_profits = [TakeProfit(sequence=1, price=_make_price(0.55))]
    stop_loss = StopLoss(price=_make_price(0.45))

    enriched_signal = EnrichedSignalPayload(
        symbol="TOKEN/USDT", side="LONG", entry_structure="TWO_STEP",
        entries=[entry_market, entry_limit],
        take_profits=take_profits,
        stop_loss=stop_loss,
    )
    policy_snapshot = _make_policy_snapshot(
        capital_base_usdt=1000.0, risk_pct=1.0,
    )
    enriched = EnrichedCanonicalMessage(
        enrichment_id=99, canonical_message_id=100, raw_message_id=200,
        trader_id="trader_a", account_id="acc1",
        primary_class="SIGNAL", enrichment_decision="PASS",
        enriched_signal=enriched_signal,
        policy_snapshot=policy_snapshot,
        management_plan=ManagementPlanConfig(),
    )

    result = RiskCapacityEngine().validate(enriched, [], None, None)
    assert result.passed is True

    legs = result.risk_snapshot["legs"]
    market_leg = next(l for l in legs if l["sequence"] == 1)
    limit_leg = next(l for l in legs if l["sequence"] == 2)

    total_risk = result.risk_snapshot["risk_amount"]
    assert abs(market_leg["risk_amount"] - total_risk * 0.7) < 0.01
    assert abs(limit_leg["risk_amount"] - total_risk * 0.3) < 0.01

    assert market_leg["qty_mode"] == "deferred_market"
    assert market_leg["qty"] is None

    assert limit_leg["qty_mode"] == "fixed"
    assert limit_leg["qty"] is not None
    # qty_limit = risk_amount_limit / abs(0.48 - 0.45)
    expected_qty = limit_leg["risk_amount"] / abs(0.48 - 0.45)
    assert abs(limit_leg["qty"] - expected_qty) < 0.01
```

- [ ] **Step 1.2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/lifecycle/test_risk_capacity.py::test_market_entry_no_mark_price_passes tests/runtime_v2/lifecycle/test_risk_capacity.py::test_market_entry_no_mark_price_sets_deferred_flag tests/runtime_v2/lifecycle/test_risk_capacity.py::test_market_entry_no_mark_price_legs_snapshot tests/runtime_v2/lifecycle/test_risk_capacity.py::test_market_entry_with_mark_price_not_deferred tests/runtime_v2/lifecycle/test_risk_capacity.py::test_mixed_market_limit_per_leg_risk -v
```

Expected: tutti FAIL.

- [ ] **Step 1.3: Implementa il nuovo `RiskCapacityEngine.validate`**

Sostituisci l'intero blocco `# ── entry price resolution` e `# ── size calculation` in `src/runtime_v2/lifecycle/risk_capacity.py`.

Sezione entry price resolution (sostituisce righe 62-74):

```python
        # ── stop-loss required (spostato prima di entry price) ───────────────
        if signal.stop_loss is None or signal.stop_loss.price is None:
            return RiskDecision(passed=False, reason="missing_stop_loss_for_risk_calc")
        sl_price = signal.stop_loss.price.value

        # ── entry price resolution ────────────────────────────────────────────
        if not signal.entries:
            return RiskDecision(passed=False, reason="no_entry_legs")

        first_leg = signal.entries[0]
        entry_price_deferred = False

        if first_leg.entry_type == "MARKET":
            if market_snapshot is not None and market_snapshot.mark_price is not None:
                entry_price: float | None = market_snapshot.mark_price
            else:
                entry_price = None
                entry_price_deferred = True
        else:
            if first_leg.price is None:
                return RiskDecision(passed=False, reason="missing_limit_price")
            entry_price = first_leg.price.value

        if not entry_price_deferred:
            risk_distance: float | None = abs(entry_price - sl_price)  # type: ignore[arg-type]
            if risk_distance == 0:
                return RiskDecision(passed=False, reason="zero_risk_distance")
        else:
            risk_distance = None
```

Rimuovi il vecchio blocco `# ── stop-loss required` (ora è spostato sopra).

Sezione size calculation (sostituisce riga 120):

```python
        # ── per-leg risk allocation ───────────────────────────────────────────
        n_legs = len(signal.entries)
        legs_snapshot: list[dict] = []
        for leg in signal.entries:
            w = float(leg.weight) if leg.weight is not None else 1.0 / n_legs
            leg_risk = risk_amount * w
            leg_price_val = leg.price.value if leg.price else (
                entry_price if not entry_price_deferred else None
            )
            is_leg_deferred = leg.entry_type == "MARKET" and entry_price_deferred
            if not is_leg_deferred and leg_price_val is not None:
                leg_rd = abs(leg_price_val - sl_price)
                leg_qty_val: float | None = leg_risk / leg_rd if leg_rd > 0 else 0.0
                qty_mode = "fixed"
            else:
                leg_qty_val = None
                qty_mode = "deferred_market"
            legs_snapshot.append({
                "sequence": leg.sequence,
                "entry_type": leg.entry_type,
                "weight": w,
                "price": leg_price_val,
                "risk_amount": leg_risk,
                "qty": leg_qty_val,
                "qty_mode": qty_mode,
            })

        # ── size calculation ──────────────────────────────────────────────────
        if not entry_price_deferred:
            size_usdt: float | None = risk_amount / risk_distance * entry_price  # type: ignore[operator]
        else:
            size_usdt = None
        leverage = risk.leverage
```

Aggiungi `"entry_price_deferred"` e `"legs"` al `risk_snapshot` (dopo `"leverage"`):

```python
        risk_snapshot = {
            "capital": capital,
            "risk_amount": risk_amount,
            "entry_price": entry_price,
            "entry_price_deferred": entry_price_deferred,
            "sl_price": sl_price,
            "risk_distance": risk_distance,
            "size_usdt": size_usdt,
            "leverage": leverage,
            "hedge_mode": config.hedge_mode,
            "capital_base_mode": risk.capital_base_mode,
            "legs": legs_snapshot,
        }
```

- [ ] **Step 1.4: Verifica che i nuovi test passino e quelli vecchi non si rompano**

```
pytest tests/runtime_v2/lifecycle/test_risk_capacity.py -v
```

Expected: tutti PASS.

- [ ] **Step 1.5: Commit**

```bash
git add src/runtime_v2/lifecycle/risk_capacity.py tests/runtime_v2/lifecycle/test_risk_capacity.py
git commit -m "feat(risk): remove MARKET block, add per-leg risk snapshot for deferred qty"
```

---

## Task 2: LifecycleEntryGate — payload deferred per leg MARKET

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate_cd.py`

- [ ] **Step 2.1: Scrivi i test che falliscono**

Aggiungi in fondo a `tests/runtime_v2/lifecycle/test_entry_gate_cd.py`:

```python
def test_c_mode_market_no_mark_price_produces_deferred_payload():
    """C mode con MARKET senza mark_price: payload deve avere qty_mode=deferred_market."""
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort

    gate = LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(),
        simple_attached_enabled=True,
    )
    enriched = _make_enriched_c(entry_type="MARKET", entry_price=None, sl_price=0.45, tp_price=0.60)
    result = gate.process_signal(enriched, [], "NORMAL")

    assert result.review_reason is None, result.review_reason
    assert len(result.execution_commands) == 1
    cmd = result.execution_commands[0]
    assert cmd.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"
    payload = json.loads(cmd.payload_json)
    assert payload["qty_mode"] == "deferred_market"
    assert "risk_amount" in payload
    assert payload["risk_amount"] > 0
    assert payload["sl_price"] == 0.45
    assert "qty" not in payload


def test_d_mode_market_no_mark_price_produces_deferred_payload():
    """D mode multi-TP con MARKET senza mark_price: payload entry ha qty_mode=deferred_market."""
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort

    gate = LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(),
        simple_attached_enabled=True,
    )
    enriched = _make_enriched_d_multi_tp(entry_type="MARKET", entry_price=None, sl_price=0.45)
    result = gate.process_signal(enriched, [], "NORMAL")

    assert result.review_reason is None, result.review_reason
    entry_cmds = [c for c in result.execution_commands if c.command_type == "PLACE_ENTRY"]
    assert len(entry_cmds) == 1
    payload = json.loads(entry_cmds[0].payload_json)
    assert payload["qty_mode"] == "deferred_market"
    assert payload["risk_amount"] > 0
    assert "qty" not in payload


def test_d_mode_mixed_market_limit_legs():
    """Mixed: leg1 MARKET deferred, leg2 LIMIT con qty calcolata."""
    from src.runtime_v2.lifecycle.entry_gate import LifecycleEntryGate
    from src.runtime_v2.lifecycle.risk_capacity import RiskCapacityEngine
    from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort
    from src.runtime_v2.signal_enrichment.models import EnrichedEntryLeg

    gate = LifecycleEntryGate(
        risk_engine=RiskCapacityEngine(),
        exchange_port=StaticExchangeDataPort(),
        simple_attached_enabled=True,
    )
    enriched = _make_enriched_mixed_legs(sl_price=0.45, limit_price=0.48)
    result = gate.process_signal(enriched, [], "NORMAL")

    assert result.review_reason is None, result.review_reason
    entry_cmds = sorted(
        [c for c in result.execution_commands if c.command_type == "PLACE_ENTRY"],
        key=lambda c: json.loads(c.payload_json)["sequence"],
    )
    assert len(entry_cmds) == 2

    p1 = json.loads(entry_cmds[0].payload_json)
    assert p1["entry_type"] == "MARKET"
    assert p1["qty_mode"] == "deferred_market"
    assert "qty" not in p1

    p2 = json.loads(entry_cmds[1].payload_json)
    assert p2["entry_type"] == "LIMIT"
    assert "qty" in p2
    assert p2["qty"] > 0
    assert "qty_mode" not in p2
```

Aggiungi i seguenti helper nello stesso file (prima dei test):

```python
def _make_enriched_mixed_legs(sl_price: float, limit_price: float):
    """Helper: 2 entry (MARKET seq=1 weight=0.7, LIMIT seq=2 weight=0.3), 1 TP."""
    from src.runtime_v2.signal_enrichment.models import EnrichedEntryLeg
    from src.parser_v2.contracts.entities import Price, StopLoss, TakeProfit

    def _p(v): return Price(raw=str(v), value=v)

    entry_market = EnrichedEntryLeg(sequence=1, entry_type="MARKET", price=None,
                                    role="PRIMARY", weight=0.7)
    entry_limit = EnrichedEntryLeg(sequence=2, entry_type="LIMIT", price=_p(limit_price),
                                   role="SECONDARY", weight=0.3)
    # ... (stessa struttura di _make_enriched_d usato nel file esistente,
    #      ma con 2 entry e entry_structure="TWO_STEP")
    # Nota: adatta agli helper già presenti nel file di test
```

**Nota**: gli helper `_make_enriched_c` e `_make_enriched_d_multi_tp` probabilmente esistono già nel file. Usa quelli esistenti aggiungendo supporto per `entry_type="MARKET"` e `entry_price=None`. Se non esistono, creali modellandoli su `_make_enriched` in `test_risk_capacity.py`.

- [ ] **Step 2.2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_c_mode_market_no_mark_price_produces_deferred_payload tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_d_mode_market_no_mark_price_produces_deferred_payload tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_d_mode_mixed_market_limit_legs -v
```

Expected: tutti FAIL.

- [ ] **Step 2.3: Aggiungi `_find_leg_snap` e aggiorna `_build_entry_commands`**

In `src/runtime_v2/lifecycle/entry_gate.py`, aggiungi subito dopo gli import, come funzione a livello modulo:

```python
def _find_leg_snap(legs_snap: list[dict], sequence: int) -> dict | None:
    for snap in legs_snap or []:
        if snap.get("sequence") == sequence:
            return snap
    return None
```

In `_build_entry_commands`, aggiungi dopo `close_pcts = ...`:

```python
        legs_snap: list[dict] = decision.risk_snapshot.get("legs", [])
```

Aggiorna le chiamate a `_build_c_commands` e `_build_d_commands` aggiungendo `legs_snap` come argomento:

```python
        if use_c:
            return self._build_c_commands(
                signal, eid, size_usdt, fallback_entry_price,
                leverage, hedge_mode, position_idx, sl_price, legs_snap,
            )
        return self._build_d_commands(
            signal, eid, size_usdt, fallback_entry_price,
            leverage, hedge_mode, position_idx, sl_price,
            tp_count, close_pcts, legs_snap,
        )
```

- [ ] **Step 2.4: Aggiorna `_build_c_commands`**

Aggiungi `legs_snap: list[dict]` come ultimo parametro. Sostituisci il calcolo di `leg_qty` con:

```python
    def _build_c_commands(
        self, signal, eid, size_usdt, fallback_entry_price,
        leverage, hedge_mode, position_idx, sl_price, legs_snap,
    ) -> list[ExecutionCommand]:
        leg = signal.entries[0]
        tp = signal.take_profits[0]
        tp_price = tp.price.value if tp.price else None

        leg_snap = _find_leg_snap(legs_snap, leg.sequence)
        is_deferred = leg_snap is not None and leg_snap.get("qty_mode") == "deferred_market"

        base_payload: dict = {
            "execution_strategy": "C_SIMPLE_ATTACHED",
            "symbol": signal.symbol,
            "side": signal.side,
            "entry_type": leg.entry_type,
            "price": leg.price.value if leg.entry_type == "LIMIT" else None,
            "leverage": leverage,
            "hedge_mode": hedge_mode,
            "position_idx": position_idx,
            "attached_tpsl": {
                "mode": "FULL",
                "take_profit": tp_price,
                "stop_loss": sl_price,
                "tp_trigger_by": "MarkPrice",
                "sl_trigger_by": "MarkPrice",
            },
        }

        if is_deferred:
            payload = {
                **base_payload,
                "qty_mode": "deferred_market",
                "risk_amount": leg_snap["risk_amount"],
                "sl_price": sl_price,
            }
        else:
            if leg_snap and leg_snap.get("qty") is not None:
                leg_qty = float(leg_snap["qty"])
            else:
                leg_price = leg.price.value if leg.price else fallback_entry_price
                leg_qty = self._qty_from_notional(size_usdt, leg_price)
            payload = {**base_payload, "qty": leg_qty}

        return [ExecutionCommand(
            trade_chain_id=0,
            command_type="PLACE_ENTRY_WITH_ATTACHED_TPSL",
            status="PENDING",
            payload_json=json.dumps(payload),
            idempotency_key=f"place_entry_attached:{eid}",
        )]
```

- [ ] **Step 2.5: Aggiorna `_build_d_commands`**

Aggiungi `legs_snap: list[dict]` come ultimo parametro. Sostituisci il loop entry con:

```python
    def _build_d_commands(
        self, signal, eid, size_usdt, fallback_entry_price,
        leverage, hedge_mode, position_idx, sl_price,
        tp_count, close_pcts, legs_snap,
    ) -> list[ExecutionCommand]:
        commands: list[ExecutionCommand] = []

        for leg in signal.entries:
            leg_snap = _find_leg_snap(legs_snap, leg.sequence)
            is_deferred = leg_snap is not None and leg_snap.get("qty_mode") == "deferred_market"

            if is_deferred:
                entry_payload: dict = {
                    "execution_strategy": "D_POSITION_TPSL",
                    "symbol": signal.symbol,
                    "side": signal.side,
                    "entry_type": leg.entry_type,
                    "price": None,
                    "qty_mode": "deferred_market",
                    "risk_amount": leg_snap["risk_amount"],
                    "sl_price": sl_price,
                    "leverage": leverage,
                    "hedge_mode": hedge_mode,
                    "position_idx": position_idx,
                    "sequence": leg.sequence,
                }
            else:
                if leg_snap and leg_snap.get("qty") is not None:
                    leg_qty = float(leg_snap["qty"])
                else:
                    leg_price = leg.price.value if leg.price else fallback_entry_price
                    leg_notional = size_usdt * float(leg.weight or 0.0)
                    leg_qty = self._qty_from_notional(leg_notional, leg_price)
                entry_payload = {
                    "execution_strategy": "D_POSITION_TPSL",
                    "symbol": signal.symbol,
                    "side": signal.side,
                    "entry_type": leg.entry_type,
                    "price": leg.price.value if leg.entry_type == "LIMIT" else None,
                    "qty": leg_qty,
                    "leverage": leverage,
                    "hedge_mode": hedge_mode,
                    "position_idx": position_idx,
                    "sequence": leg.sequence,
                }
            commands.append(ExecutionCommand(
                trade_chain_id=0,
                command_type="PLACE_ENTRY",
                status="PENDING",
                payload_json=json.dumps(entry_payload),
                idempotency_key=f"place_entry:{eid}:leg{leg.sequence}",
            ))

        # TP/SL commands — invariati rispetto all'implementazione corrente
        # (il codice restante di _build_d_commands rimane uguale)
```

**Nota**: il resto di `_build_d_commands` (logica TP/SL con `SET_POSITION_TPSL_FULL` e `SET_POSITION_TPSL_PARTIAL`) rimane invariato. Cambia solo il loop delle entry.

- [ ] **Step 2.6: Verifica che i nuovi test passino e quelli vecchi non si rompano**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py -v
pytest tests/runtime_v2/lifecycle/test_entry_gate.py -v
```

Expected: tutti PASS.

- [ ] **Step 2.7: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate_cd.py
git commit -m "feat(entry_gate): produce deferred_market payload for MARKET legs without mark_price"
```

---

## Task 3: Adapter — aggiungi `fetch_mark_price`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/base.py`
- Modify: `src/runtime_v2/execution_gateway/adapters/fake.py`
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`
- Test: `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`

- [ ] **Step 3.1: Scrivi i test che falliscono**

Aggiungi a `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`:

```python
def test_fetch_mark_price_returns_mark_price(mock_exchange):
    """fetch_mark_price ritorna markPrice dal ticker."""
    mock_exchange.fetch_ticker = lambda symbol: {"markPrice": 50123.45, "last": 50100.0}
    adapter = CcxtBybitAdapter(
        api_key="k", api_secret="s", connector="bybit", _exchange=mock_exchange
    )
    result = adapter.fetch_mark_price("BTC/USDT", "acc1")
    assert result == 50123.45


def test_fetch_mark_price_falls_back_to_last(mock_exchange):
    """fetch_mark_price usa 'last' se markPrice è assente."""
    mock_exchange.fetch_ticker = lambda symbol: {"last": 50100.0}
    adapter = CcxtBybitAdapter(
        api_key="k", api_secret="s", connector="bybit", _exchange=mock_exchange
    )
    result = adapter.fetch_mark_price("BTC/USDT", "acc1")
    assert result == 50100.0


def test_fetch_mark_price_returns_none_on_error(mock_exchange):
    """fetch_mark_price ritorna None se ccxt solleva eccezione."""
    mock_exchange.fetch_ticker = lambda symbol: (_ for _ in ()).throw(Exception("network error"))
    adapter = CcxtBybitAdapter(
        api_key="k", api_secret="s", connector="bybit", _exchange=mock_exchange
    )
    result = adapter.fetch_mark_price("BTC/USDT", "acc1")
    assert result is None
```

Aggiungi per FakeAdapter in `tests/runtime_v2/execution_gateway/test_fake_adapter.py`:

```python
def test_fake_adapter_fetch_mark_price_configured():
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    adapter = FakeAdapter(mark_prices={"BTC/USDT": 50000.0})
    assert adapter.fetch_mark_price("BTC/USDT", "acc1") == 50000.0


def test_fake_adapter_fetch_mark_price_missing_returns_none():
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    adapter = FakeAdapter()
    assert adapter.fetch_mark_price("BTC/USDT", "acc1") is None


def test_fake_adapter_set_mark_price():
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    adapter = FakeAdapter()
    adapter.set_mark_price("ETH/USDT", 3000.0)
    assert adapter.fetch_mark_price("ETH/USDT", "acc1") == 3000.0
```

- [ ] **Step 3.2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py::test_fetch_mark_price_returns_mark_price tests/runtime_v2/execution_gateway/test_fake_adapter.py::test_fake_adapter_fetch_mark_price_configured -v
```

Expected: FAIL con `AttributeError: 'CcxtBybitAdapter' object has no attribute 'fetch_mark_price'`.

- [ ] **Step 3.3: Aggiungi `fetch_mark_price` all'interfaccia base**

In `src/runtime_v2/execution_gateway/adapters/base.py`, aggiungi dopo `get_position_qty`:

```python
    @abstractmethod
    def fetch_mark_price(
        self,
        symbol: str,
        execution_account_id: str,
    ) -> float | None: ...
```

- [ ] **Step 3.4: Implementa `fetch_mark_price` in `FakeAdapter`**

In `src/runtime_v2/execution_gateway/adapters/fake.py`:

Aggiungi `mark_prices: dict[str, float] | None = None` al costruttore:

```python
    def __init__(
        self,
        *,
        capabilities: AdapterCapabilities | None = None,
        fail_on: set[str] | None = None,
        simulate_timeout: bool = False,
        positions: dict[str, float] | None = None,
        mark_prices: dict[str, float] | None = None,
    ) -> None:
        # ... (parametri esistenti invariati)
        self._mark_prices: dict[str, float] = mark_prices or {}
```

Aggiungi metodi:

```python
    def fetch_mark_price(self, symbol: str, execution_account_id: str) -> float | None:
        return self._mark_prices.get(symbol)

    def set_mark_price(self, symbol: str, price: float) -> None:
        self._mark_prices[symbol] = price
```

- [ ] **Step 3.5: Implementa `fetch_mark_price` in `CcxtBybitAdapter`**

In `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`, aggiungi dopo `get_position_qty` (o alla fine dei metodi pubblici, prima di `__all__`):

```python
    def fetch_mark_price(self, symbol: str, execution_account_id: str) -> float | None:
        try:
            ticker = self._exchange.fetch_ticker(symbol)
            mark = ticker.get("markPrice") or ticker.get("last")
            return float(mark) if mark is not None else None
        except Exception as exc:
            logger.warning("fetch_mark_price failed for %s: %s", symbol, exc)
            return None
```

- [ ] **Step 3.6: Verifica che tutti i test passino**

```
pytest tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py -v
pytest tests/runtime_v2/execution_gateway/test_fake_adapter.py -v
```

Expected: tutti PASS.

- [ ] **Step 3.7: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapters/base.py src/runtime_v2/execution_gateway/adapters/fake.py src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py tests/runtime_v2/execution_gateway/test_fake_adapter.py
git commit -m "feat(adapter): add fetch_mark_price to ExecutionAdapter interface and implementations"
```

---

## Task 4: ExecutionGateway — risolvi qty deferred prima di `place_order`

**Files:**
- Modify: `src/runtime_v2/execution_gateway/gateway.py:103-158`
- Test: `tests/runtime_v2/execution_gateway/test_gateway.py`

- [ ] **Step 4.1: Scrivi i test che falliscono**

Aggiungi a `tests/runtime_v2/execution_gateway/test_gateway.py`:

```python
def test_deferred_market_resolves_qty_from_mark_price(ops_db, tmp_path):
    """Gateway con payload deferred_market: fetcha mark_price e calcola qty prima del place_order."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    payload = {
        "symbol": "SOL/USDT",
        "side": "LONG",
        "entry_type": "MARKET",
        "qty_mode": "deferred_market",
        "risk_amount": 10.0,
        "sl_price": 140.0,
        "leverage": 5,
        "hedge_mode": False,
        "position_idx": 0,
        "execution_strategy": "D_POSITION_TPSL",
        "sequence": 1,
    }
    _insert_cmd(ops_db, cmd_id=10, cmd_type="PLACE_ENTRY", payload=payload)

    adapter = FakeAdapter(mark_prices={"SOL/USDT": 150.0})
    config_path = tmp_path / "exec_config.yaml"
    config_path.write_text(
        "accounts:\n"
        "  acc_1:\n"
        "    adapter: fake\n"
        "    mode: paper\n"
        "    connector: bybit\n"
        "    live_safety:\n"
        "      allow_live_trading: false\n"
        "    retry:\n"
        "      max_attempts: 3\n"
        "      backoff_seconds: [1, 5, 30]\n"
        "routing:\n"
        "  acc_1:\n"
        "    adapter: fake\n"
        "    execution_account_id: acc_1\n"
    )
    config = ExecutionConfigLoader.load(str(config_path))
    repo = GatewayCommandRepository(ops_db)
    gateway = ExecutionGateway(config, {"fake": adapter}, repo)

    from src.runtime_v2.lifecycle.models import ExecutionCommand
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    cmd = ExecutionCommand(
        command_id=10, trade_chain_id=1, command_type="PLACE_ENTRY",
        status="PENDING", payload_json=json.dumps(payload),
        idempotency_key="idem:10", created_at=now,
    )
    gateway.process(cmd, account_id="acc_1")

    # Verifica che place_order sia stato chiamato con qty calcolata
    place_calls = [c for c in adapter.calls if c["action"] == "place_order"]
    assert len(place_calls) == 1
    # qty = risk_amount / abs(mark_price - sl_price) = 10.0 / abs(150.0 - 140.0) = 1.0
    assert abs(adapter._last_place_qty - 1.0) < 0.001  # vedi step 4.3


def test_deferred_market_no_mark_price_marks_review_required(ops_db, tmp_path):
    """Gateway con deferred_market e nessun mark_price: REVIEW_REQUIRED."""
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    payload = {
        "symbol": "SOL/USDT", "side": "LONG", "entry_type": "MARKET",
        "qty_mode": "deferred_market", "risk_amount": 10.0, "sl_price": 140.0,
        "leverage": 5, "hedge_mode": False, "position_idx": 0,
        "execution_strategy": "D_POSITION_TPSL", "sequence": 1,
    }
    _insert_cmd(ops_db, cmd_id=11, cmd_type="PLACE_ENTRY", payload=payload)

    adapter = FakeAdapter()  # nessun mark_price configurato
    config_path = tmp_path / "exec_config.yaml"
    config_path.write_text(
        "accounts:\n"
        "  acc_1:\n"
        "    adapter: fake\n"
        "    mode: paper\n"
        "    connector: bybit\n"
        "    live_safety:\n"
        "      allow_live_trading: false\n"
        "    retry:\n"
        "      max_attempts: 3\n"
        "      backoff_seconds: [1, 5, 30]\n"
        "routing:\n"
        "  acc_1:\n"
        "    adapter: fake\n"
        "    execution_account_id: acc_1\n"
    )
    config = ExecutionConfigLoader.load(str(config_path))
    repo = GatewayCommandRepository(ops_db)
    gateway = ExecutionGateway(config, {"fake": adapter}, repo)

    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    from src.runtime_v2.lifecycle.models import ExecutionCommand
    cmd = ExecutionCommand(
        command_id=11, trade_chain_id=1, command_type="PLACE_ENTRY",
        status="PENDING", payload_json=json.dumps(payload),
        idempotency_key="idem:11", created_at=now,
    )
    gateway.process(cmd, account_id="acc_1")

    place_calls = [c for c in adapter.calls if c["action"] == "place_order"]
    assert len(place_calls) == 0  # non deve aver chiamato place_order

    conn = sqlite3.connect(ops_db)
    row = conn.execute(
        "SELECT status, review_reason FROM ops_execution_commands WHERE command_id=11"
    ).fetchone()
    conn.close()
    assert row[0] == "REVIEW_REQUIRED"
    assert "deferred_market_no_mark_price" in (row[1] or "")
```

**Nota**: il test usa `adapter._last_place_qty` — aggiungerai questa property a FakeAdapter nello step 4.3.

- [ ] **Step 4.2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/execution_gateway/test_gateway.py::test_deferred_market_resolves_qty_from_mark_price tests/runtime_v2/execution_gateway/test_gateway.py::test_deferred_market_no_mark_price_marks_review_required -v
```

Expected: FAIL.

- [ ] **Step 4.3: Aggiungi `_last_place_qty` tracking a `FakeAdapter`**

In `src/runtime_v2/execution_gateway/adapters/fake.py`, aggiorna `place_order` per tracciare la qty:

```python
    def place_order(self, *, command_type, payload, client_order_id,
                    execution_account_id, connector):
        self.calls.append({"action": "place_order", "command_type": command_type,
                           "client_order_id": client_order_id, "payload": payload})
        self._last_place_qty: float | None = float(payload.get("qty", 0.0))
        # ... resto invariato
```

- [ ] **Step 4.4: Aggiungi la risoluzione deferred in `gateway.py`**

In `src/runtime_v2/execution_gateway/gateway.py`, nel metodo `process`, aggiungi dopo `payload = json.loads(cmd.payload_json)` e prima del blocco `# Set leverage`:

```python
        # ── Resolve deferred MARKET qty ───────────────────────────────────────
        if payload.get("qty_mode") == "deferred_market":
            mark_price = adapter.fetch_mark_price(symbol, routing.execution_account_id)
            if mark_price is None:
                self._repo.mark_review_required(
                    cmd.command_id, reason="deferred_market_no_mark_price"
                )
                return
            risk_amount_leg = float(payload["risk_amount"])
            sl_price_val = float(payload["sl_price"])
            risk_dist = abs(mark_price - sl_price_val)
            if risk_dist == 0.0:
                self._repo.mark_review_required(
                    cmd.command_id, reason="deferred_market_zero_risk_distance"
                )
                return
            computed_qty = risk_amount_leg / risk_dist
            payload = {
                k: v for k, v in payload.items()
                if k not in ("qty_mode", "risk_amount", "sl_price")
            }
            payload["qty"] = computed_qty
```

- [ ] **Step 4.5: Verifica che i nuovi test passino e quelli vecchi non si rompano**

```
pytest tests/runtime_v2/execution_gateway/test_gateway.py -v
```

Expected: tutti PASS.

- [ ] **Step 4.6: Commit**

```bash
git add src/runtime_v2/execution_gateway/gateway.py src/runtime_v2/execution_gateway/adapters/fake.py tests/runtime_v2/execution_gateway/test_gateway.py
git commit -m "feat(gateway): resolve deferred MARKET qty via fetch_mark_price at submit time"
```

---

## Task 5: Verifica finale — suite completa

- [ ] **Step 5.1: Esegui tutti i test impattati**

```
pytest tests/runtime_v2/lifecycle/test_risk_capacity.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate_cd.py tests/runtime_v2/execution_gateway/test_gateway.py tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py tests/runtime_v2/execution_gateway/test_fake_adapter.py -v
```

Expected: tutti PASS. Nessuna regressione.

- [ ] **Step 5.2: Esegui la suite completa**

```
pytest tests/ -v --tb=short
```

Expected: tutti PASS o stessa quantità di skip/xfail pre-esistenti.

- [ ] **Step 5.3: Aggiorna il doc di design**

In `docs/debugging/Market/market_entry_qty_deferred.md`, aggiungi in cima:

```markdown
## Stato implementazione

✅ Implementato — Task completato il 2026-05-22.
File modificati: risk_capacity.py, entry_gate.py, adapters/base.py, adapters/fake.py,
adapters/ccxt_bybit/adapter.py, gateway.py.
```

- [ ] **Step 5.4: Commit finale**

```bash
git add docs/debugging/Market/market_entry_qty_deferred.md
git commit -m "docs: mark MARKET deferred qty as implemented"
```

---

## Note implementative

**Compatibilità backward**: il campo `legs` nel risk_snapshot è opzionale — l'entry gate usa `decision.risk_snapshot.get("legs", [])` e il fallback è il comportamento pre-esistente (usa `size_usdt` e `fallback_entry_price`). I test esistenti non passano `legs` e continuano a funzionare.

**Ordini legacy**: `_build_legacy_commands` non viene modificato — i MARKET senza mark_price con routing legacy continuano a essere prodotti con `qty=0` (comportamento pre-esistente). Il fix si applica solo ai routing C e D.

**Tolerance check** (futuro): il doc menziona `market_execution.tolerance_pct` per validare la distanza tra mark_price_live e signal_price indicativo. Non è incluso in questo piano — è un hardening opzionale da aggiungere nel gateway dopo che il fix base è stabile.

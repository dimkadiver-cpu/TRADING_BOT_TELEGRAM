# Execution Strategy — Casi 1_1, 2, 2_2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementare C_MULTI_TP (1 entry + N TP), D_MULTI_ENTRY_1TP (N entry + 1 TP), D_MULTI_ENTRY_MULTI_TP (N entry + N TP) con SL sempre attached order-level e rebuild dinamico dei TP partial.

**Architecture:** Nuova routing matrix 2×2 in `entry_gate._build_entry_commands`; 3 nuovi builder; helper condiviso `_place_entry_attached_cmd`; `event_processor._process_entry_filled` emette SET_POSITION_TPSL_PARTIAL post-fill per D_MULTI_ENTRY_MULTI_TP; gateway cancella i comandi TP precedenti quando riceve `supersedes_previous=True`.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, SQLite (ops DB via sqlite3).

**Spec:** `docs/superpowers/specs/2026-05-22-execution-strategy-cases-design.md`

---

## File Map

| File | Operazione | Responsabilità |
|------|------------|----------------|
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py` | Modify | Supporta `mode` in `attached_tpsl` (FULL/PARTIAL_TP/SL_ONLY); `preserve_sl` in SET_POSITION_TPSL_PARTIAL |
| `src/runtime_v2/execution_gateway/repositories.py` | Modify | Aggiunge `cancel_tp_partial_commands()` |
| `src/runtime_v2/execution_gateway/gateway.py` | Modify | Gestisce `supersedes_previous=True` in SET_POSITION_TPSL_PARTIAL |
| `src/runtime_v2/lifecycle/entry_gate.py` | Modify | Routing matrix; 3 nuovi builder; helper `_place_entry_attached_cmd`; `tp_rebuild` in risk_snapshot |
| `src/runtime_v2/lifecycle/event_processor.py` | Modify | Post-fill TP rebuild per D_MULTI_ENTRY_MULTI_TP |
| `tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py` | Modify | Test nuovi modi attached_tpsl + preserve_sl |
| `tests/runtime_v2/execution_gateway/test_gateway.py` | Modify | Test supersedes_previous |
| `tests/runtime_v2/lifecycle/test_entry_gate_cd.py` | Modify | Test routing matrix + 3 nuovi builder |
| `tests/runtime_v2/lifecycle/test_event_processor.py` | Modify | Test post-fill rebuild D_MULTI_ENTRY_MULTI_TP |

---

## Task 1: order_builder — `attached_tpsl` mode support

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py`
- Test: `tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py`

- [ ] **Step 1: Scrivi i test fallenti per i nuovi modi**

Aggiungi in coda a `tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py`:

```python
# ── PLACE_ENTRY_WITH_ATTACHED_TPSL — nuovi modi ──────────────────────────────

def test_place_entry_attached_sl_only():
    """SL_ONLY: solo stopLoss nel payload Bybit, nessun takeProfit/tpslMode."""
    params = _b().build(
        "PLACE_ENTRY_WITH_ATTACHED_TPSL",
        {
            "symbol": "BTC/USDT:USDT", "side": "LONG",
            "entry_type": "LIMIT", "price": 65000.0, "qty": 0.01,
            "leverage": 5, "hedge_mode": False, "position_idx": 0,
            "attached_tpsl": {
                "mode": "SL_ONLY",
                "stop_loss": 63000.0,
                "sl_trigger_by": "MarkPrice",
            },
        },
        "tsb:1:1:entry:1",
    )
    assert params.action == "create_order"
    assert params.extra_params["stopLoss"] == 63000.0
    assert "takeProfit" not in params.extra_params
    assert "tpslMode" not in params.extra_params
    assert params.extra_params["slOrderType"] == "Market"
    assert params.extra_params["slTriggerBy"] == "MarkPrice"


def test_place_entry_attached_partial_tp():
    """PARTIAL_TP: SL full + TP parziale con tpSize."""
    params = _b().build(
        "PLACE_ENTRY_WITH_ATTACHED_TPSL",
        {
            "symbol": "BTC/USDT:USDT", "side": "LONG",
            "entry_type": "LIMIT", "price": 65000.0, "qty": 0.01,
            "leverage": 5, "hedge_mode": False, "position_idx": 0,
            "attached_tpsl": {
                "mode": "PARTIAL_TP",
                "stop_loss": 63000.0,
                "take_profit": 70000.0,
                "tp_qty": 0.005,
                "tp_trigger_by": "MarkPrice",
                "sl_trigger_by": "MarkPrice",
            },
        },
        "tsb:1:1:entry:1",
    )
    assert params.extra_params["tpslMode"] == "Partial"
    assert params.extra_params["takeProfit"] == 70000.0
    assert params.extra_params["stopLoss"] == 63000.0
    assert params.extra_params["tpSize"] == "0.005"
    assert params.extra_params["tpTriggerBy"] == "MarkPrice"
    assert params.extra_params["slTriggerBy"] == "MarkPrice"


def test_place_entry_attached_full_mode_unchanged():
    """FULL mode (o mode assente): comportamento invariato rispetto al test esistente."""
    params = _b().build(
        "PLACE_ENTRY_WITH_ATTACHED_TPSL",
        {
            "symbol": "BTC/USDT:USDT", "side": "LONG",
            "entry_type": "LIMIT", "price": 65000.0, "qty": 0.01,
            "leverage": 5, "hedge_mode": False, "position_idx": 0,
            "attached_tpsl": {
                "mode": "FULL",
                "take_profit": 70000.0,
                "stop_loss": 63000.0,
                "tp_trigger_by": "MarkPrice",
                "sl_trigger_by": "MarkPrice",
            },
        },
        "tsb:1:1:entry:1",
    )
    assert params.extra_params["tpslMode"] == "Full"
    assert params.extra_params["takeProfit"] == 70000.0
    assert params.extra_params["stopLoss"] == 63000.0
    assert "tpSize" not in params.extra_params
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py::test_place_entry_attached_sl_only tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py::test_place_entry_attached_partial_tp -v
```

Expected: FAIL con `KeyError: 'take_profit'` o assert error.

- [ ] **Step 3: Aggiorna `_place_entry_with_attached_tpsl` in `order_builder.py`**

Sostituisci il metodo `_place_entry_with_attached_tpsl` (righe 176-199) con:

```python
def _place_entry_with_attached_tpsl(self, payload: dict, client_order_id: str) -> BybitOrderParams:
    entry_type = payload["entry_type"]
    price = float(payload["price"]) if entry_type == "LIMIT" and payload.get("price") else None
    tpsl = payload["attached_tpsl"]
    mode = tpsl.get("mode", "FULL")

    extra: dict = {
        "slOrderType": "Market",
        "slTriggerBy": tpsl.get("sl_trigger_by", "MarkPrice"),
    }

    if mode == "SL_ONLY":
        extra["stopLoss"] = float(tpsl["stop_loss"])
    elif mode == "PARTIAL_TP":
        extra.update({
            "takeProfit": float(tpsl["take_profit"]),
            "stopLoss": float(tpsl["stop_loss"]),
            "tpslMode": "Partial",
            "tpOrderType": "Market",
            "tpTriggerBy": tpsl.get("tp_trigger_by", "MarkPrice"),
            "tpSize": str(float(tpsl["tp_qty"])),
        })
    else:  # "FULL"
        extra.update({
            "takeProfit": float(tpsl["take_profit"]),
            "stopLoss": float(tpsl["stop_loss"]),
            "tpslMode": "Full",
            "tpOrderType": "Market",
            "tpTriggerBy": tpsl.get("tp_trigger_by", "MarkPrice"),
        })

    return BybitOrderParams(
        action="create_order",
        symbol=payload["symbol"],
        order_type=entry_type.lower(),
        side=_ENTRY_SIDE[payload["side"]],
        amount=float(payload["qty"]),
        price=price,
        order_link_id=client_order_id,
        extra_params=extra,
    )
```

- [ ] **Step 4: Verifica che tutti i test del builder passino**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py -v
```

Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py
git commit -m "feat(order_builder): support SL_ONLY and PARTIAL_TP modes in attached_tpsl"
```

---

## Task 2: order_builder — `preserve_sl` in SET_POSITION_TPSL_PARTIAL

**Files:**
- Modify: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py`
- Test: `tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py`

- [ ] **Step 1: Scrivi il test fallente**

Aggiungi in coda a `tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py`:

```python
# ── SET_POSITION_TPSL_PARTIAL — preserve_sl ──────────────────────────────────

def test_set_position_tpsl_partial_preserve_sl_omits_sl_fields():
    """preserve_sl=True: stopLoss e slSize assenti dal payload Bybit."""
    params = _b().build(
        "SET_POSITION_TPSL_PARTIAL",
        {
            "symbol": "BTC/USDT:USDT", "side": "LONG",
            "position_idx": 0, "tp_sequence": 1,
            "take_profit": 70000.0, "tp_size": 0.005,
            "tp_order_type": "Limit", "tp_limit_price": 70000.0,
            "tp_trigger_by": "MarkPrice",
            "preserve_sl": True,
        },
        "tsb:1:1:tp:1",
    )
    assert params.action == "trading_stop_partial"
    assert params.extra_params["takeProfit"] == "70000.0"
    assert params.extra_params["tpSize"] == "0.005"
    assert "stopLoss" not in params.extra_params
    assert "slSize" not in params.extra_params
    assert "slOrderType" not in params.extra_params


def test_set_position_tpsl_partial_default_includes_sl():
    """preserve_sl assente (default False): stopLoss e slSize presenti."""
    params = _b().build(
        "SET_POSITION_TPSL_PARTIAL",
        {
            "symbol": "BTC/USDT:USDT", "side": "LONG",
            "position_idx": 0, "tp_sequence": 1,
            "take_profit": 70000.0, "stop_loss": 63000.0,
            "tp_size": 0.005, "sl_size": 0.005,
            "tp_order_type": "Limit", "tp_limit_price": 70000.0,
            "tp_trigger_by": "MarkPrice", "sl_trigger_by": "MarkPrice",
        },
        "tsb:1:1:tp:1",
    )
    assert "stopLoss" in params.extra_params
    assert "slSize" in params.extra_params
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py::test_set_position_tpsl_partial_preserve_sl_omits_sl_fields -v
```

Expected: FAIL (stopLoss è presente mentre non dovrebbe esserlo).

- [ ] **Step 3: Aggiorna `_set_position_tpsl_partial` in `order_builder.py`**

Sostituisci il metodo `_set_position_tpsl_partial` (righe 218-239):

```python
def _set_position_tpsl_partial(self, payload: dict) -> BybitOrderParams:
    tp_order_type = payload.get("tp_order_type", "Limit")
    preserve_sl = bool(payload.get("preserve_sl", False))
    extra: dict = {
        "positionIdx": int(payload.get("position_idx", 0)),
        "tpslMode": "Partial",
        "takeProfit": str(float(payload["take_profit"])),
        "tpSize": str(float(payload["tp_size"])),
        "tpOrderType": tp_order_type,
        "tpTriggerBy": payload.get("tp_trigger_by", "MarkPrice"),
    }
    if not preserve_sl:
        extra["stopLoss"] = str(float(payload["stop_loss"]))
        extra["slSize"] = str(float(payload["sl_size"]))
        extra["slOrderType"] = payload.get("sl_order_type", "Market")
        extra["slTriggerBy"] = payload.get("sl_trigger_by", "MarkPrice")
    if tp_order_type == "Limit" and payload.get("tp_limit_price"):
        extra["tpLimitPrice"] = str(float(payload["tp_limit_price"]))
    return BybitOrderParams(
        action="trading_stop_partial",
        symbol=payload["symbol"],
        position_side=payload["side"],
        extra_params=extra,
    )
```

- [ ] **Step 4: Verifica che tutti i test del builder passino**

```
pytest tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py -v
```

Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py
git commit -m "feat(order_builder): preserve_sl flag omits SL fields from SET_POSITION_TPSL_PARTIAL"
```

---

## Task 3: entry_gate — routing matrix + `tp_rebuild` in process_signal

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate_cd.py`

- [ ] **Step 1: Scrivi i test fallenti per il routing**

Aggiungi in coda a `tests/runtime_v2/lifecycle/test_entry_gate_cd.py`:

```python
# ── Routing matrix ────────────────────────────────────────────────────────────

def test_routing_1entry_1tp_uses_c_simple_attached():
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=1, tp_count=1), [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "C_SIMPLE_ATTACHED"


def test_routing_1entry_multi_tp_uses_c_multi_tp():
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=1, tp_count=2), [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "C_MULTI_TP"


def test_routing_multi_entry_1tp_uses_d_multi_entry_1tp():
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=1), [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "D_MULTI_ENTRY_1TP"


def test_routing_multi_entry_multi_tp_uses_d_multi_entry_multi_tp():
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=2), [], "NONE")
    assert result.trade_chain is not None
    assert result.trade_chain.execution_mode == "D_MULTI_ENTRY_MULTI_TP"


def test_routing_no_sl_falls_back_to_d_position_tpsl():
    """Senza SL il routing NON usa la nuova matrice."""
    from src.runtime_v2.signal_enrichment.models import (
        AccountConfig, EffectiveEnrichmentConfig, EnrichedCanonicalMessage,
        EnrichedEntryLeg, EnrichedSignalPayload, EntryRangeConfig,
        EntrySplitConfig, LimitEntrySplitConfig, ManagementPlanConfig,
        MarketEntrySplitConfig, MarketExecutionConfig, PriceCorrectionsConfig,
        PriceSanityConfig, RiskConfig, SignalPolicyConfig, SlConfig,
        TpConfig, EntryWeightsConfig,
    )
    from src.parser_v2.contracts.entities import Price, TakeProfit

    entries = [EnrichedEntryLeg(sequence=1, entry_type="LIMIT",
                                price=Price(raw="65000", value=65000.0), weight=1.0)]
    tps = [TakeProfit(price=Price(raw="70000", value=70000.0), sequence=1)]
    signal = EnrichedSignalPayload(
        symbol="BTC/USDT:USDT", side="LONG", entry_structure="ONE_SHOT",
        entries=entries, take_profits=tps, stop_loss=None,
    )
    w = EntryWeightsConfig(weights={"E1": 1.0})
    r = EntryRangeConfig(weights={"E1": 0.5, "E2": 0.5})
    risk = RiskConfig(leverage=5, capital_base_usdt=1000.0, risk_pct_of_capital=1.0)
    account = AccountConfig(id="main", capital_base_usdt=1000.0, max_leverage=10,
                            max_capital_at_risk_pct=10.0, hard_max_per_signal_risk_pct=2.0)
    sp = SignalPolicyConfig(
        accepted_entry_structures=["ONE_SHOT"],
        market_execution=MarketExecutionConfig(),
        entry_split=EntrySplitConfig(
            LIMIT=LimitEntrySplitConfig(single=w, range=r, averaging=w, ladder=w),
            MARKET=MarketEntrySplitConfig(single=w, averaging=w),
        ),
        tp=TpConfig(), sl=SlConfig(),
        price_corrections=PriceCorrectionsConfig(),
        price_sanity=PriceSanityConfig(),
    )
    cfg = EffectiveEnrichmentConfig(
        trader_id="t1", enabled=True, gate_mode="block", hedge_mode=False,
        account_id="main", signal_policy=sp, update_admission={},
        management_plan=ManagementPlanConfig(),
        risk=risk, account=account,
    )
    enriched = EnrichedCanonicalMessage(
        enrichment_id=99, canonical_message_id=990, raw_message_id=9900,
        trader_id="t1", account_id="main", primary_class="SIGNAL",
        enrichment_decision="PASS", enriched_signal=signal, enriched_actions=None,
        management_plan=ManagementPlanConfig(), policy_snapshot=cfg.model_dump(),
    )
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(enriched, [], "NONE")
    # missing SL → risk engine blocks signal
    assert result.review_reason == "missing_stop_loss_for_risk_calc"


def test_routing_d_multi_entry_multi_tp_injects_tp_rebuild_in_snapshot():
    """D_MULTI_ENTRY_MULTI_TP: risk_snapshot_json contiene tp_rebuild con 2 livelli."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=2), [], "NONE")
    assert result.trade_chain is not None
    snap = json.loads(result.trade_chain.risk_snapshot_json)
    assert "tp_rebuild" in snap
    levels = snap["tp_rebuild"]["levels"]
    assert len(levels) == 2
    assert levels[0]["sequence"] == 1
    assert levels[1]["sequence"] == 2
    assert all("price" in lv and "close_pct" in lv for lv in levels)
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_routing_1entry_multi_tp_uses_c_multi_tp tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_routing_multi_entry_1tp_uses_d_multi_entry_1tp tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_routing_multi_entry_multi_tp_uses_d_multi_entry_multi_tp -v
```

Expected: FAIL (execution_mode è ancora "D_POSITION_TPSL").

- [ ] **Step 3: Aggiorna il blocco di routing in `process_signal` (`entry_gate.py`)**

Trova il blocco (righe ~125-134):

```python
            use_c = (
                self._simple_attached_enabled is True
                and entry_count_for_decision == 1
                and tp_count_for_decision == 1
                and sl_price_for_decision is not None
            )
            chain_execution_mode = "C_SIMPLE_ATTACHED" if use_c else "D_POSITION_TPSL"
```

Sostituiscilo con:

```python
            if self._simple_attached_enabled is True and sl_price_for_decision is not None:
                if entry_count_for_decision == 1 and tp_count_for_decision == 1:
                    chain_execution_mode = "C_SIMPLE_ATTACHED"
                elif entry_count_for_decision == 1 and tp_count_for_decision > 1:
                    chain_execution_mode = "C_MULTI_TP"
                elif entry_count_for_decision > 1 and tp_count_for_decision == 1:
                    chain_execution_mode = "D_MULTI_ENTRY_1TP"
                else:
                    chain_execution_mode = "D_MULTI_ENTRY_MULTI_TP"
            else:
                chain_execution_mode = "D_POSITION_TPSL"
```

- [ ] **Step 4: Aggiungi iniezione `tp_rebuild` in `process_signal` (subito prima di `chain = TradeChain(...)`)**

Trova la riga `chain = TradeChain(` in `process_signal` (circa riga 136). Inserisci prima di essa:

```python
        if chain_execution_mode == "D_MULTI_ENTRY_MULTI_TP":
            _mp = enriched.management_plan or ManagementPlanConfig()
            _tp_count = len(signal.take_profits)
            _close_pcts = self._get_close_pcts(_mp, _tp_count)
            decision.risk_snapshot["tp_rebuild"] = {
                "levels": [
                    {
                        "sequence": tp.sequence,
                        "price": tp.price.value if tp.price else None,
                        "close_pct": (
                            _close_pcts[i]
                            if i < len(_close_pcts)
                            else 100.0 / _tp_count
                        ),
                    }
                    for i, tp in enumerate(signal.take_profits)
                ]
            }
```

- [ ] **Step 5: Aggiorna il blocco `_build_entry_commands` per il nuovo dispatch**

Trova il blocco corrente (righe ~236-247):

```python
        use_c = (
            self._simple_attached_enabled is True
            and entry_count == 1
            and tp_count == 1
            and sl_price is not None
        )

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

Sostituiscilo con:

```python
        if not (self._simple_attached_enabled is True and sl_price is not None):
            return self._build_d_commands(
                signal, eid, size_usdt, fallback_entry_price,
                leverage, hedge_mode, position_idx, sl_price,
                tp_count, close_pcts, legs_snap,
            )

        if entry_count == 1 and tp_count == 1:
            return self._build_c_commands(
                signal, eid, size_usdt, fallback_entry_price,
                leverage, hedge_mode, position_idx, sl_price, legs_snap,
            )
        if entry_count == 1 and tp_count > 1:
            return self._build_c_multi_tp_commands(
                signal, eid, size_usdt, fallback_entry_price,
                leverage, hedge_mode, position_idx, sl_price, close_pcts, legs_snap,
            )
        if entry_count > 1 and tp_count == 1:
            return self._build_d_multi_entry_1tp_commands(
                signal, eid, size_usdt, fallback_entry_price,
                leverage, hedge_mode, position_idx, sl_price, close_pcts, legs_snap,
            )
        return self._build_d_multi_entry_multi_tp_commands(
            signal, eid, size_usdt, fallback_entry_price,
            leverage, hedge_mode, position_idx, sl_price,
            tp_count, close_pcts, legs_snap,
        )
```

I tre nuovi metodi non esistono ancora — aggiungi stub temporanei subito dopo `_build_d_commands` (prima di `resolve_position_idx`):

```python
    def _build_c_multi_tp_commands(
        self, signal, eid, size_usdt, fallback_entry_price,
        leverage, hedge_mode, position_idx, sl_price, close_pcts, legs_snap,
    ) -> list[ExecutionCommand]:
        raise NotImplementedError("C_MULTI_TP not yet implemented")

    def _build_d_multi_entry_1tp_commands(
        self, signal, eid, size_usdt, fallback_entry_price,
        leverage, hedge_mode, position_idx, sl_price, close_pcts, legs_snap,
    ) -> list[ExecutionCommand]:
        raise NotImplementedError("D_MULTI_ENTRY_1TP not yet implemented")

    def _build_d_multi_entry_multi_tp_commands(
        self, signal, eid, size_usdt, fallback_entry_price,
        leverage, hedge_mode, position_idx, sl_price,
        tp_count, close_pcts, legs_snap,
    ) -> list[ExecutionCommand]:
        raise NotImplementedError("D_MULTI_ENTRY_MULTI_TP not yet implemented")
```

- [ ] **Step 6: Verifica che i test di routing passino**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_routing_1entry_1tp_uses_c_simple_attached tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_routing_1entry_multi_tp_uses_c_multi_tp tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_routing_multi_entry_1tp_uses_d_multi_entry_1tp tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_routing_multi_entry_multi_tp_uses_d_multi_entry_multi_tp tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_routing_d_multi_entry_multi_tp_injects_tp_rebuild_in_snapshot -v
```

Expected: tutti PASS (il routing sceglie il mode corretto; gli stub non vengono chiamati da questi test).

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate_cd.py
git commit -m "feat(entry_gate): routing matrix 2x2 + tp_rebuild injection for D_MULTI_ENTRY_MULTI_TP"
```

---

## Task 4: entry_gate — `_place_entry_attached_cmd` helper + `_build_c_multi_tp_commands` (Caso_1_1)

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate_cd.py`

- [ ] **Step 1: Scrivi il test fallente per C_MULTI_TP**

Aggiungi in coda a `tests/runtime_v2/lifecycle/test_entry_gate_cd.py`:

```python
# ── C_MULTI_TP (1 entry + 2 TP) ──────────────────────────────────────────────

def test_c_multi_tp_entry_has_sl_and_last_tp_attached():
    """C_MULTI_TP: entry ha PLACE_ENTRY_WITH_ATTACHED_TPSL con PARTIAL_TP (SL + ultimo TP)."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=1, tp_count=2), [], "NONE")
    cmds = result.execution_commands
    entry_cmds = [c for c in cmds if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    assert len(entry_cmds) == 1
    p = json.loads(entry_cmds[0].payload_json)
    tpsl = p["attached_tpsl"]
    assert tpsl["mode"] == "PARTIAL_TP"
    assert tpsl["stop_loss"] == 63000.0
    assert tpsl["take_profit"] == 70500.0   # sequence=2, price=70000+1*500
    assert tpsl["tp_qty"] > 0


def test_c_multi_tp_intermediate_tps_are_waiting_position():
    """C_MULTI_TP: TP intermedi (non ultimo) sono SET_POSITION_TPSL_PARTIAL WAITING_POSITION con preserve_sl=True."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=1, tp_count=2), [], "NONE")
    cmds = result.execution_commands
    tp_cmds = [c for c in cmds if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    assert len(tp_cmds) == 1    # 1 intermedio (TP seq=1), l'ultimo è attached
    assert tp_cmds[0].status == "WAITING_POSITION"
    p = json.loads(tp_cmds[0].payload_json)
    assert p["preserve_sl"] is True
    assert p["take_profit"] == 70000.0   # TP sequence=1


def test_c_multi_tp_3tp_has_2_intermediate_commands():
    """C_MULTI_TP con 3 TP: 2 comandi WAITING_POSITION (seq 1,2), 1 TP attached (seq 3)."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=1, tp_count=3), [], "NONE")
    cmds = result.execution_commands
    tp_cmds = [c for c in cmds if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    assert len(tp_cmds) == 2
    seqs = sorted(json.loads(c.payload_json)["tp_sequence"] for c in tp_cmds)
    assert seqs == [1, 2]
    entry_cmds = [c for c in cmds if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    p = json.loads(entry_cmds[0].payload_json)
    assert p["attached_tpsl"]["take_profit"] == 71000.0  # seq=3, price=70000+2*500
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_c_multi_tp_entry_has_sl_and_last_tp_attached -v
```

Expected: FAIL con `NotImplementedError: C_MULTI_TP not yet implemented`.

- [ ] **Step 3: Aggiungi `_place_entry_attached_cmd` helper e implementa `_build_c_multi_tp_commands`**

Sostituisci lo stub `_build_c_multi_tp_commands` (la riga `raise NotImplementedError(...)`) con l'implementazione completa. Aggiungi il helper subito prima:

```python
    def _place_entry_attached_cmd(
        self,
        *,
        signal,
        leg,
        eid: int,
        label: str,
        leverage: int,
        hedge_mode: bool,
        position_idx: int,
        sl_price: float,
        leg_snap: dict | None,
        qty: float | None,
        tpsl_mode: str = "FULL",
        tp_price: float | None = None,
        tp_qty: float | None = None,
    ) -> ExecutionCommand:
        is_deferred = leg_snap is not None and leg_snap.get("qty_mode") == "deferred_market"
        attached_tpsl: dict = {
            "mode": tpsl_mode,
            "stop_loss": sl_price,
            "sl_trigger_by": "MarkPrice",
        }
        if tpsl_mode != "SL_ONLY" and tp_price is not None:
            attached_tpsl["take_profit"] = tp_price
            attached_tpsl["tp_trigger_by"] = "MarkPrice"
            if tpsl_mode == "PARTIAL_TP" and tp_qty is not None:
                attached_tpsl["tp_qty"] = tp_qty
        base: dict = {
            "execution_strategy": label,
            "symbol": signal.symbol,
            "side": signal.side,
            "entry_type": leg.entry_type,
            "price": leg.price.value if leg.entry_type == "LIMIT" else None,
            "leverage": leverage,
            "hedge_mode": hedge_mode,
            "position_idx": position_idx,
            "attached_tpsl": attached_tpsl,
        }
        if is_deferred:
            payload: dict = {
                **base,
                "qty_mode": "deferred_market",
                "risk_amount": float(leg_snap["risk_amount"]),
                "sl_price": sl_price,
            }
        else:
            payload = {**base, "qty": qty}
        return ExecutionCommand(
            trade_chain_id=0,
            command_type="PLACE_ENTRY_WITH_ATTACHED_TPSL",
            status="PENDING",
            payload_json=json.dumps(payload),
            idempotency_key=f"place_entry_attached:{eid}:leg{leg.sequence}",
        )

    def _build_c_multi_tp_commands(
        self, signal, eid, size_usdt, fallback_entry_price,
        leverage, hedge_mode, position_idx, sl_price, close_pcts, legs_snap,
    ) -> list[ExecutionCommand]:
        leg = signal.entries[0]
        tp_count = len(signal.take_profits)
        leg_snap = _find_leg_snap(legs_snap, leg.sequence)
        is_deferred = leg_snap is not None and leg_snap.get("qty_mode") == "deferred_market"

        if not is_deferred:
            if leg_snap and leg_snap.get("qty") is not None:
                leg_qty = float(leg_snap["qty"])
            else:
                leg_price = leg.price.value if leg.price else fallback_entry_price
                leg_qty = self._qty_from_notional(size_usdt, leg_price)
        else:
            leg_qty = 0.0  # sconosciuta; il gateway risolve al submit

        last_tp = signal.take_profits[-1]
        last_tp_price = last_tp.price.value if last_tp.price else None
        last_close_pct = close_pcts[-1] if close_pcts else (100.0 / tp_count)
        last_tp_qty = round(leg_qty * last_close_pct / 100.0, 8) if not is_deferred else None

        entry_cmd = self._place_entry_attached_cmd(
            signal=signal, leg=leg, eid=eid, label="C_MULTI_TP",
            leverage=leverage, hedge_mode=hedge_mode, position_idx=position_idx,
            sl_price=sl_price, leg_snap=leg_snap,
            qty=leg_qty if not is_deferred else None,
            tpsl_mode="PARTIAL_TP",
            tp_price=last_tp_price,
            tp_qty=last_tp_qty,
        )

        commands: list[ExecutionCommand] = [entry_cmd]

        allocated_qty = 0.0
        for i, tp in enumerate(signal.take_profits[:-1]):
            tp_price = tp.price.value if tp.price else None
            close_pct = close_pcts[i] if i < len(close_pcts) else (100.0 / tp_count)
            tp_qty = round(leg_qty * close_pct / 100.0, 8) if not is_deferred else None
            if tp_qty is not None:
                allocated_qty += tp_qty
            partial_payload: dict = {
                "execution_strategy": "C_MULTI_TP",
                "symbol": signal.symbol,
                "side": signal.side,
                "position_idx": position_idx,
                "tp_sequence": tp.sequence,
                "take_profit": tp_price,
                "tp_size": tp_qty if tp_qty is not None else 0.0,
                "tp_order_type": "Limit",
                "tp_limit_price": tp_price,
                "tp_trigger_by": "MarkPrice",
                "preserve_sl": True,
            }
            commands.append(ExecutionCommand(
                trade_chain_id=0,
                command_type="SET_POSITION_TPSL_PARTIAL",
                status="WAITING_POSITION",
                payload_json=json.dumps(partial_payload),
                idempotency_key=f"set_tp_partial:{eid}:tp{tp.sequence}",
            ))

        return commands
```

- [ ] **Step 4: Verifica che i test C_MULTI_TP passino**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_c_multi_tp_entry_has_sl_and_last_tp_attached tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_c_multi_tp_intermediate_tps_are_waiting_position tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_c_multi_tp_3tp_has_2_intermediate_commands -v
```

Expected: tutti PASS.

- [ ] **Step 5: Verifica che i test esistenti passino ancora**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py -v
```

Expected: tutti PASS.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate_cd.py
git commit -m "feat(entry_gate): implement C_MULTI_TP builder with SL+partial_TP attached"
```

---

## Task 5: entry_gate — `_build_d_multi_entry_1tp_commands` (Caso_2)

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate_cd.py`

- [ ] **Step 1: Scrivi i test fallenti per D_MULTI_ENTRY_1TP**

Aggiungi in coda a `tests/runtime_v2/lifecycle/test_entry_gate_cd.py`:

```python
# ── D_MULTI_ENTRY_1TP (2 entry + 1 TP) ──────────────────────────────────────

def test_d_multi_entry_1tp_each_leg_has_attached_tpsl():
    """D_MULTI_ENTRY_1TP: ogni leg produce PLACE_ENTRY_WITH_ATTACHED_TPSL mode FULL con SL+TP."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=1), [], "NONE")
    cmds = result.execution_commands
    entry_cmds = [c for c in cmds if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    assert len(entry_cmds) == 2
    seqs = sorted(json.loads(c.payload_json)["attached_tpsl"]["stop_loss"] for c in entry_cmds)
    assert all(sl == 63000.0 for sl in seqs)
    for c in entry_cmds:
        p = json.loads(c.payload_json)
        assert p["attached_tpsl"]["mode"] == "FULL"
        assert p["attached_tpsl"]["take_profit"] == 70000.0
        assert p["attached_tpsl"]["stop_loss"] == 63000.0


def test_d_multi_entry_1tp_no_waiting_position_commands():
    """D_MULTI_ENTRY_1TP: nessun comando SET_POSITION_TPSL_FULL o SET_POSITION_TPSL_PARTIAL."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=1), [], "NONE")
    cmds = result.execution_commands
    assert not any(c.command_type in {"SET_POSITION_TPSL_FULL", "SET_POSITION_TPSL_PARTIAL"}
                   for c in cmds)
    assert not any(c.status == "WAITING_POSITION" for c in cmds)


def test_d_multi_entry_1tp_idempotency_keys_are_distinct():
    """D_MULTI_ENTRY_1TP: ogni leg ha una idempotency_key diversa."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=1), [], "NONE")
    entry_cmds = [c for c in result.execution_commands
                  if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    keys = [c.idempotency_key for c in entry_cmds]
    assert len(set(keys)) == 2
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_d_multi_entry_1tp_each_leg_has_attached_tpsl -v
```

Expected: FAIL con `NotImplementedError: D_MULTI_ENTRY_1TP not yet implemented`.

- [ ] **Step 3: Implementa `_build_d_multi_entry_1tp_commands`**

Sostituisci lo stub:

```python
    def _build_d_multi_entry_1tp_commands(
        self, signal, eid, size_usdt, fallback_entry_price,
        leverage, hedge_mode, position_idx, sl_price, close_pcts, legs_snap,
    ) -> list[ExecutionCommand]:
        tp = signal.take_profits[0]
        tp_price = tp.price.value if tp.price else None
        commands: list[ExecutionCommand] = []

        for leg in signal.entries:
            leg_snap = _find_leg_snap(legs_snap, leg.sequence)
            is_deferred = leg_snap is not None and leg_snap.get("qty_mode") == "deferred_market"

            if not is_deferred:
                if leg_snap and leg_snap.get("qty") is not None:
                    leg_qty = float(leg_snap["qty"])
                else:
                    leg_price = leg.price.value if leg.price else fallback_entry_price
                    leg_notional = size_usdt * float(leg.weight or 0.0)
                    leg_qty = self._qty_from_notional(leg_notional, leg_price)
            else:
                leg_qty = None

            commands.append(self._place_entry_attached_cmd(
                signal=signal, leg=leg, eid=eid, label="D_MULTI_ENTRY_1TP",
                leverage=leverage, hedge_mode=hedge_mode, position_idx=position_idx,
                sl_price=sl_price, leg_snap=leg_snap,
                qty=leg_qty,
                tpsl_mode="FULL",
                tp_price=tp_price,
            ))

        return commands
```

- [ ] **Step 4: Verifica che tutti i test D_MULTI_ENTRY_1TP passino**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_d_multi_entry_1tp_each_leg_has_attached_tpsl tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_d_multi_entry_1tp_no_waiting_position_commands tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_d_multi_entry_1tp_idempotency_keys_are_distinct -v
```

Expected: tutti PASS.

- [ ] **Step 5: Verifica regressioni**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py -v
```

Expected: tutti PASS.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate_cd.py
git commit -m "feat(entry_gate): implement D_MULTI_ENTRY_1TP builder with SL+TP attached per leg"
```

---

## Task 6: entry_gate — `_build_d_multi_entry_multi_tp_commands` (Caso_2_2)

**Files:**
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate_cd.py`

- [ ] **Step 1: Scrivi i test fallenti per D_MULTI_ENTRY_MULTI_TP**

Aggiungi in coda a `tests/runtime_v2/lifecycle/test_entry_gate_cd.py`:

```python
# ── D_MULTI_ENTRY_MULTI_TP (2 entry + 2 TP) ──────────────────────────────────

def test_d_multi_entry_multi_tp_each_leg_has_sl_only_attached():
    """D_MULTI_ENTRY_MULTI_TP: ogni leg ha PLACE_ENTRY_WITH_ATTACHED_TPSL mode SL_ONLY."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=2), [], "NONE")
    cmds = result.execution_commands
    entry_cmds = [c for c in cmds if c.command_type == "PLACE_ENTRY_WITH_ATTACHED_TPSL"]
    assert len(entry_cmds) == 2
    for c in entry_cmds:
        p = json.loads(c.payload_json)
        tpsl = p["attached_tpsl"]
        assert tpsl["mode"] == "SL_ONLY"
        assert tpsl["stop_loss"] == 63000.0
        assert "take_profit" not in tpsl


def test_d_multi_entry_multi_tp_no_tp_commands_at_creation():
    """D_MULTI_ENTRY_MULTI_TP: nessun comando SET_POSITION_TPSL al momento della creazione."""
    gate = _make_gate(simple_attached_enabled=True)
    result = gate.process_signal(_make_enriched_signal(entry_count=2, tp_count=2), [], "NONE")
    cmds = result.execution_commands
    assert not any(c.command_type in {"SET_POSITION_TPSL_FULL", "SET_POSITION_TPSL_PARTIAL"}
                   for c in cmds)
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_d_multi_entry_multi_tp_each_leg_has_sl_only_attached -v
```

Expected: FAIL con `NotImplementedError: D_MULTI_ENTRY_MULTI_TP not yet implemented`.

- [ ] **Step 3: Implementa `_build_d_multi_entry_multi_tp_commands`**

Sostituisci lo stub:

```python
    def _build_d_multi_entry_multi_tp_commands(
        self, signal, eid, size_usdt, fallback_entry_price,
        leverage, hedge_mode, position_idx, sl_price,
        tp_count, close_pcts, legs_snap,
    ) -> list[ExecutionCommand]:
        commands: list[ExecutionCommand] = []

        for leg in signal.entries:
            leg_snap = _find_leg_snap(legs_snap, leg.sequence)
            is_deferred = leg_snap is not None and leg_snap.get("qty_mode") == "deferred_market"

            if not is_deferred:
                if leg_snap and leg_snap.get("qty") is not None:
                    leg_qty = float(leg_snap["qty"])
                else:
                    leg_price = leg.price.value if leg.price else fallback_entry_price
                    leg_notional = size_usdt * float(leg.weight or 0.0)
                    leg_qty = self._qty_from_notional(leg_notional, leg_price)
            else:
                leg_qty = None

            commands.append(self._place_entry_attached_cmd(
                signal=signal, leg=leg, eid=eid, label="D_MULTI_ENTRY_MULTI_TP",
                leverage=leverage, hedge_mode=hedge_mode, position_idx=position_idx,
                sl_price=sl_price, leg_snap=leg_snap,
                qty=leg_qty,
                tpsl_mode="SL_ONLY",
            ))

        return commands
```

- [ ] **Step 4: Verifica che tutti i test D_MULTI_ENTRY_MULTI_TP passino**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_d_multi_entry_multi_tp_each_leg_has_sl_only_attached tests/runtime_v2/lifecycle/test_entry_gate_cd.py::test_d_multi_entry_multi_tp_no_tp_commands_at_creation -v
```

Expected: tutti PASS.

- [ ] **Step 5: Verifica suite completa entry_gate**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py tests/runtime_v2/lifecycle/test_entry_gate.py -v
```

Expected: tutti PASS.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate_cd.py
git commit -m "feat(entry_gate): implement D_MULTI_ENTRY_MULTI_TP builder with SL_ONLY attached per leg"
```

---

## Task 7: event_processor — post-fill TP rebuild per D_MULTI_ENTRY_MULTI_TP

**Files:**
- Modify: `src/runtime_v2/lifecycle/event_processor.py`
- Test: `tests/runtime_v2/lifecycle/test_event_processor.py`

- [ ] **Step 1: Scrivi i test fallenti**

Aggiungi in coda a `tests/runtime_v2/lifecycle/test_event_processor.py`:

```python
# ── D_MULTI_ENTRY_MULTI_TP post-fill ─────────────────────────────────────────

import json as _json


def _make_chain_multi_tp(
    *,
    trade_chain_id: int = 10,
    state: str = "WAITING_ENTRY",
    filled_entry_qty: float = 0.0,
    open_position_qty: float = 0.0,
):
    from src.runtime_v2.lifecycle.models import TradeChain
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig
    mp = ManagementPlanConfig()
    risk_snap = {
        "tp_rebuild": {
            "levels": [
                {"sequence": 1, "price": 0.52, "close_pct": 50.0},
                {"sequence": 2, "price": 0.55, "close_pct": 50.0},
            ]
        }
    }
    return TradeChain(
        trade_chain_id=trade_chain_id,
        source_enrichment_id=trade_chain_id,
        canonical_message_id=trade_chain_id * 10,
        raw_message_id=trade_chain_id * 100,
        trader_id="trader_a", account_id="acc_1",
        symbol="TOKEN/USDT", side="LONG",
        lifecycle_state=state,
        entry_mode="TWO_STEP",
        management_plan_json=mp.model_dump_json(),
        risk_snapshot_json=_json.dumps(risk_snap),
        execution_mode="D_MULTI_ENTRY_MULTI_TP",
        filled_entry_qty=filled_entry_qty,
        open_position_qty=open_position_qty,
    )


def test_d_multi_entry_multi_tp_first_fill_emits_tp_partial_commands():
    """Primo fill: emette SET_POSITION_TPSL_PARTIAL per ogni livello TP."""
    proc = _make_processor()
    chain = _make_chain_multi_tp(state="WAITING_ENTRY")
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 0.50, "filled_qty": 0.7},
    )
    result = proc.process(event, chain, [])
    tp_cmds = [c for c in result.execution_commands if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    assert len(tp_cmds) == 2
    sizes = sorted(float(_json.loads(c.payload_json)["tp_size"]) for c in tp_cmds)
    assert abs(sizes[0] - 0.35) < 1e-6   # 0.7 * 50%
    assert abs(sizes[1] - 0.35) < 1e-6   # residuo


def test_d_multi_entry_multi_tp_second_fill_emits_supersedes_previous():
    """Secondo fill: i nuovi comandi TP hanno supersedes_previous=True e qty aggiornata."""
    proc = _make_processor()
    chain = _make_chain_multi_tp(
        state="OPEN",
        filled_entry_qty=0.7,
        open_position_qty=0.7,
    )
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 0.48, "filled_qty": 0.3},
    )
    result = proc.process(event, chain, [])
    tp_cmds = [c for c in result.execution_commands if c.command_type == "SET_POSITION_TPSL_PARTIAL"]
    assert len(tp_cmds) == 2
    for c in tp_cmds:
        p = _json.loads(c.payload_json)
        assert p.get("supersedes_previous") is True
    sizes = sorted(float(_json.loads(c.payload_json)["tp_size"]) for c in tp_cmds)
    assert abs(sizes[0] - 0.5) < 1e-6   # 1.0 * 50%
    assert abs(sizes[1] - 0.5) < 1e-6   # residuo


def test_d_multi_entry_multi_tp_tp_prices_match_tp_rebuild_levels():
    """I comandi TP emessi hanno i prezzi corretti dai livelli tp_rebuild."""
    proc = _make_processor()
    chain = _make_chain_multi_tp(state="WAITING_ENTRY")
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 0.50, "filled_qty": 1.0},
    )
    result = proc.process(event, chain, [])
    tp_cmds = result.execution_commands
    prices = {_json.loads(c.payload_json)["take_profit"] for c in tp_cmds
              if c.command_type == "SET_POSITION_TPSL_PARTIAL"}
    assert prices == {0.52, 0.55}


def test_non_multi_entry_multi_tp_entry_fill_emits_no_tp_commands():
    """Chain con execution_mode diverso da D_MULTI_ENTRY_MULTI_TP: nessun TP command al fill."""
    proc = _make_processor()
    chain = _make_chain(state="WAITING_ENTRY")  # execution_mode default = "a_sequential"
    event = _make_exchange_event(
        event_type="ENTRY_FILLED",
        payload={"fill_price": 50000.0, "filled_qty": 0.1},
    )
    result = proc.process(event, chain, [])
    assert result.execution_commands == []
```

- [ ] **Step 2: Verifica che i test falliscano**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py::test_d_multi_entry_multi_tp_first_fill_emits_tp_partial_commands -v
```

Expected: FAIL (nessun comando TP emesso).

- [ ] **Step 3: Aggiorna `event_processor.py`**

Aggiungi il metodo helper `_build_tp_partial_commands_after_fill` alla classe `LifecycleEventProcessor`, subito dopo `_process_entry_filled`:

```python
    def _build_tp_partial_commands_after_fill(
        self, chain: TradeChain, new_filled: float, exchange_event_id: int
    ) -> list[ExecutionCommand]:
        try:
            risk_snap = json.loads(chain.risk_snapshot_json or "{}")
            levels = risk_snap.get("tp_rebuild", {}).get("levels", [])
        except Exception:
            return []
        if not levels:
            return []

        chain_id = chain.trade_chain_id
        total_levels = len(levels)
        commands: list[ExecutionCommand] = []
        allocated_qty = 0.0

        for i, level in enumerate(levels):
            is_last = (i == total_levels - 1)
            tp_price = level.get("price")
            close_pct = float(level.get("close_pct", 100.0 / total_levels))
            sequence = int(level.get("sequence", i + 1))

            if is_last:
                tp_qty = round(max(0.0, new_filled - allocated_qty), 8)
            else:
                tp_qty = round(new_filled * close_pct / 100.0, 8)
                allocated_qty += tp_qty

            payload: dict = {
                "symbol": chain.symbol,
                "side": chain.side,
                "tp_sequence": sequence,
                "take_profit": tp_price,
                "tp_size": tp_qty,
                "tp_order_type": "Limit",
                "tp_limit_price": tp_price,
                "tp_trigger_by": "MarkPrice",
                "preserve_sl": True,
                "supersedes_previous": True,
            }
            commands.append(ExecutionCommand(
                trade_chain_id=chain_id,
                command_type="SET_POSITION_TPSL_PARTIAL",
                payload_json=json.dumps(payload),
                idempotency_key=(
                    f"tp_partial_fill:{chain_id}:{exchange_event_id}:tp{sequence}"
                ),
            ))

        return commands
```

Poi modifica `_process_entry_filled` per chiamare il metodo sopra. Trova la riga `return EventProcessorResult(` in `_process_entry_filled` e aggiungi prima:

```python
        commands: list[ExecutionCommand] = []
        if chain.execution_mode == "D_MULTI_ENTRY_MULTI_TP":
            commands = self._build_tp_partial_commands_after_fill(
                chain, new_filled, exchange_event.exchange_event_id
            )
```

Poi nella `return EventProcessorResult(`, sostituisci `execution_commands=[]` con `execution_commands=commands`.

Il metodo `_process_entry_filled` completo diventa:

```python
    def _process_entry_filled(
        self, exchange_event: ExchangeEvent, chain: TradeChain
    ) -> EventProcessorResult:
        payload = json.loads(exchange_event.payload_json)
        fill_price = float(payload.get("fill_price") or 0.0)
        fill_qty = float(payload.get("filled_qty") or 0.0)
        eid = exchange_event.exchange_event_id
        chain_id = chain.trade_chain_id

        old_filled = chain.filled_entry_qty
        old_avg = chain.entry_avg_price or 0.0
        new_filled = old_filled + fill_qty
        if new_filled > 0:
            new_avg = ((old_avg * old_filled) + (fill_price * fill_qty)) / new_filled
        else:
            new_avg = fill_price
        new_open = chain.open_position_qty + fill_qty

        is_first_fill = chain.lifecycle_state == "WAITING_ENTRY"
        new_state: LifecycleState | None = "OPEN" if is_first_fill else None

        events: list[LifecycleEvent] = [
            LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="ENTRY_FILLED",
                source_type="exchange_event",
                source_id=str(eid),
                previous_state=chain.lifecycle_state,
                next_state=new_state or chain.lifecycle_state,
                payload_json=json.dumps({"fill_price": fill_price, "filled_qty": fill_qty}),
                idempotency_key=f"entry_filled:{chain_id}:{eid}",
            ),
            LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="POSITION_SIZE_UPDATED",
                source_type="exchange_event",
                source_id=str(eid),
                payload_json=json.dumps({"filled_entry_qty": new_filled, "open_position_qty": new_open}),
                idempotency_key=f"pos_size_updated:{chain_id}:{eid}",
            ),
            LifecycleEvent(
                trade_chain_id=chain_id,
                event_type="ENTRY_AVG_PRICE_UPDATED",
                source_type="exchange_event",
                source_id=str(eid),
                payload_json=json.dumps({"entry_avg_price": new_avg}),
                idempotency_key=f"avg_price_updated:{chain_id}:{eid}",
            ),
        ]

        commands: list[ExecutionCommand] = []
        if chain.execution_mode == "D_MULTI_ENTRY_MULTI_TP":
            commands = self._build_tp_partial_commands_after_fill(chain, new_filled, eid)

        return EventProcessorResult(
            new_lifecycle_state=new_state,
            new_be_protection_status=None,
            entry_avg_price=new_avg,
            current_stop_price=None,
            lifecycle_events=events,
            execution_commands=commands,
            new_filled_entry_qty=new_filled,
            new_open_position_qty=new_open,
            release_waiting_position=is_first_fill,
        )
```

- [ ] **Step 4: Verifica che tutti i test event_processor passino**

```
pytest tests/runtime_v2/lifecycle/test_event_processor.py -v
```

Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/event_processor.py tests/runtime_v2/lifecycle/test_event_processor.py
git commit -m "feat(event_processor): emit SET_POSITION_TPSL_PARTIAL after fill for D_MULTI_ENTRY_MULTI_TP"
```

---

## Task 8: repository + gateway — `supersedes_previous` handling

**Files:**
- Modify: `src/runtime_v2/execution_gateway/repositories.py`
- Modify: `src/runtime_v2/execution_gateway/gateway.py`
- Test: `tests/runtime_v2/execution_gateway/test_gateway.py`

- [ ] **Step 1: Scrivi il test fallente**

Aggiungi in coda a `tests/runtime_v2/execution_gateway/test_gateway.py`:

```python
def test_supersedes_previous_cancels_old_tp_partial_commands(ops_db):
    """supersedes_previous=True: i vecchi SET_POSITION_TPSL_PARTIAL PENDING per la chain
    vengono marcati CANCELLED prima dell'invio del nuovo comando."""
    import sqlite3 as _sq
    from src.runtime_v2.execution_gateway.adapters.fake import FakeAdapter
    from src.runtime_v2.execution_gateway.config_loader import ExecutionConfigLoader
    from src.runtime_v2.execution_gateway.gateway import ExecutionGateway
    from src.runtime_v2.execution_gateway.repositories import GatewayCommandRepository

    # Inserisci due vecchi SET_POSITION_TPSL_PARTIAL PENDING per chain_id=1
    _insert_cmd(ops_db, 2001, chain_id=1, cmd_type="SET_POSITION_TPSL_PARTIAL",
                payload={"symbol": "BTC/USDT", "side": "LONG", "take_profit": 70000.0,
                         "tp_size": 0.007, "tp_order_type": "Limit",
                         "tp_limit_price": 70000.0, "tp_trigger_by": "MarkPrice",
                         "preserve_sl": True})
    _insert_cmd(ops_db, 2002, chain_id=1, cmd_type="SET_POSITION_TPSL_PARTIAL",
                payload={"symbol": "BTC/USDT", "side": "LONG", "take_profit": 75000.0,
                         "tp_size": 0.003, "tp_order_type": "Limit",
                         "tp_limit_price": 75000.0, "tp_trigger_by": "MarkPrice",
                         "preserve_sl": True})

    # Inserisci il nuovo comando con supersedes_previous=True
    _insert_cmd(ops_db, 2003, chain_id=1, cmd_type="SET_POSITION_TPSL_PARTIAL",
                payload={"symbol": "BTC/USDT", "side": "LONG",
                         "take_profit": 70000.0, "tp_size": 0.01,
                         "tp_order_type": "Limit", "tp_limit_price": 70000.0,
                         "tp_trigger_by": "MarkPrice",
                         "preserve_sl": True,
                         "supersedes_previous": True})

    repo = GatewayCommandRepository(ops_db)
    gw = ExecutionGateway(
        config=ExecutionConfigLoader("config/execution.yaml").load(),
        adapter_registry={"bybit_demo": FakeAdapter()},
        repo=repo,
    )
    # Processa solo il comando con supersedes_previous
    conn = _sq.connect(ops_db)
    row = conn.execute(
        "SELECT command_id, trade_chain_id, command_type, status, payload_json, "
        "idempotency_key, created_at, updated_at "
        "FROM ops_execution_commands WHERE command_id=2003"
    ).fetchone()
    conn.close()
    from src.runtime_v2.lifecycle.models import ExecutionCommand
    from datetime import datetime, timezone
    import json
    cmd = ExecutionCommand(
        command_id=row[0], trade_chain_id=row[1], command_type=row[2],
        status=row[3], payload_json=row[4], idempotency_key=row[5],
        created_at=datetime.now(timezone.utc),
    )
    gw.process(cmd, account_id="acc_1")

    # I vecchi comandi devono essere CANCELLED
    conn = _sq.connect(ops_db)
    statuses = {
        r[0]: r[1] for r in conn.execute(
            "SELECT command_id, status FROM ops_execution_commands "
            "WHERE command_id IN (2001, 2002, 2003)"
        ).fetchall()
    }
    conn.close()
    assert statuses[2001] == "CANCELLED"
    assert statuses[2002] == "CANCELLED"
    assert statuses[2003] in ("SENT", "ACK", "DONE")
```

- [ ] **Step 2: Verifica che il test fallisca**

```
pytest tests/runtime_v2/execution_gateway/test_gateway.py::test_supersedes_previous_cancels_old_tp_partial_commands -v
```

Expected: FAIL (vecchi comandi ancora PENDING).

- [ ] **Step 3: Aggiungi `cancel_tp_partial_commands` a `GatewayCommandRepository`**

Aggiungi in coda alla classe `GatewayCommandRepository` in `repositories.py`:

```python
    def cancel_tp_partial_commands(self, trade_chain_id: int, exclude_command_id: int) -> None:
        """Marca CANCELLED tutti i SET_POSITION_TPSL_PARTIAL attivi per la chain,
        escluso il comando corrente (che li sostituisce)."""
        now = _now()
        conn = sqlite3.connect(self._db)
        try:
            conn.execute(
                "UPDATE ops_execution_commands SET status='CANCELLED', updated_at=? "
                "WHERE trade_chain_id=? AND command_type='SET_POSITION_TPSL_PARTIAL' "
                "AND status IN ('PENDING','SENT','ACK') AND command_id != ?",
                (now, trade_chain_id, exclude_command_id),
            )
            conn.commit()
        finally:
            conn.close()
```

- [ ] **Step 4: Aggiungi gestione `supersedes_previous` in `gateway.process`**

In `gateway.py`, nel metodo `process`, dopo il blocco `if payload.get("qty_mode") == "deferred_market":` e prima del blocco `leverage`, aggiungi:

```python
        # Supersede previous SET_POSITION_TPSL_PARTIAL commands for this chain
        if (
            cmd.command_type == "SET_POSITION_TPSL_PARTIAL"
            and payload.get("supersedes_previous")
            and cmd.command_id is not None
        ):
            self._repo.cancel_tp_partial_commands(
                cmd.trade_chain_id, exclude_command_id=cmd.command_id
            )
            payload = {k: v for k, v in payload.items() if k != "supersedes_previous"}
```

- [ ] **Step 5: Verifica che il test gateway passi**

```
pytest tests/runtime_v2/execution_gateway/test_gateway.py::test_supersedes_previous_cancels_old_tp_partial_commands -v
```

Expected: PASS.

- [ ] **Step 6: Verifica tutta la suite gateway**

```
pytest tests/runtime_v2/execution_gateway/test_gateway.py -v
```

Expected: tutti PASS.

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/execution_gateway/repositories.py src/runtime_v2/execution_gateway/gateway.py tests/runtime_v2/execution_gateway/test_gateway.py
git commit -m "feat(gateway): cancel previous SET_POSITION_TPSL_PARTIAL when supersedes_previous=True"
```

---

## Verifica finale

- [ ] **Esegui l'intera suite dei test coinvolti**

```
pytest tests/runtime_v2/lifecycle/test_entry_gate_cd.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/lifecycle/test_event_processor.py tests/runtime_v2/execution_gateway/test_bybit_order_builder_cd.py tests/runtime_v2/execution_gateway/test_gateway.py -v
```

Expected: tutti PASS, nessuna regressione.

- [ ] **Commit finale (se necessario)**

```bash
git commit --allow-empty -m "chore: verify execution strategy cases implementation complete"
```

# Symbol Canonicalization Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendere `symbol` canonico in formato raw (`FIDAUSDT`) in tutto il `runtime_v2` per le nuove chain, mantenendo il display slash-style solo nei formatter.

**Architecture:** La normalizzazione avviene al boundary di ingresso del runtime, dentro `signal_enrichment`, cosi `EnrichedSignalPayload`, `TradeChain`, `ops_trade_chains` e i command payload nascono gia coerenti. I formatter continuano a convertire raw -> display con `display_symbol()`, mentre il matching exchange `symbol + side` resta semplice e basato su raw-to-raw.

**Tech Stack:** Python 3.12, pytest, SQLite, Pydantic v2, runtime_v2 lifecycle/execution/control-plane

---

## File Map

**Create:**
- `src/runtime_v2/symbols.py` — helper canonica per convertire simboli in raw format in modo idempotente
- `tests/runtime_v2/test_symbols.py` — unit test della helper di canonicalizzazione

**Modify:**
- `src/runtime_v2/signal_enrichment/processor.py` — normalizzare `signal.symbol` prima di costruire `EnrichedSignalPayload`
- `src/runtime_v2/lifecycle/static_exchange_data_port.py` — allineare lookup mercato e known symbols al formato raw
- `tests/runtime_v2/lifecycle/test_ports.py` — aggiornare aspettative del port statico
- `tests/runtime_v2/lifecycle/test_entry_gate.py` — aggiungere test che la chain nuova persista `symbol` raw
- `tests/runtime_v2/control_plane/test_worker_clean_log_integration.py` — confermare che il display resti slash-style con symbol raw
- `tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py` or `tests/runtime_v2/control_plane/test_outbox_writer.py` — aggiungere copertura funding raw -> chain raw -> final result funding

## Task 1: Add shared runtime symbol canonicalizer

**Files:**
- Create: `src/runtime_v2/symbols.py`
- Test: `tests/runtime_v2/test_symbols.py`

- [ ] **Step 1: Write the failing unit tests for canonicalization**

```python
from src.runtime_v2.symbols import to_raw_symbol


def test_to_raw_symbol_keeps_raw_symbol():
    assert to_raw_symbol("FIDAUSDT") == "FIDAUSDT"


def test_to_raw_symbol_converts_slash_symbol():
    assert to_raw_symbol("FIDA/USDT") == "FIDAUSDT"


def test_to_raw_symbol_converts_ccxt_style_symbol():
    assert to_raw_symbol("FIDA/USDT:USDT") == "FIDAUSDT"


def test_to_raw_symbol_is_case_and_whitespace_insensitive():
    assert to_raw_symbol("  fida/usdt  ") == "FIDAUSDT"


def test_to_raw_symbol_preserves_none():
    assert to_raw_symbol(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/test_symbols.py -q`
Expected: FAIL with `ModuleNotFoundError` or missing `to_raw_symbol`

- [ ] **Step 3: Write minimal implementation**

```python
from __future__ import annotations


def to_raw_symbol(symbol: str | None) -> str | None:
    if symbol is None:
        return None
    normalized = symbol.strip().upper()
    if not normalized:
        return None
    if ":" in normalized:
        normalized = normalized.split(":", 1)[0]
    return normalized.replace("/", "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/test_symbols.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/symbols.py tests/runtime_v2/test_symbols.py
git commit -m "feat: add runtime raw symbol canonicalizer"
```

## Task 2: Normalize symbol at runtime ingress

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/processor.py`
- Test: `tests/runtime_v2/lifecycle/test_entry_gate.py`

- [ ] **Step 1: Add the failing lifecycle test for persisted raw symbol**

Add a test near the existing `EnrichedSignalPayload(...)` lifecycle coverage:

```python
def test_new_chain_persists_raw_symbol_when_signal_uses_slash_format(tmp_path):
    # build minimal enriched signal with symbol="FIDA/USDT"
    # run through the existing lifecycle gate path that creates a new chain
    # assert persisted ops_trade_chains.symbol == "FIDAUSDT"
```

The assertion must read the DB row directly:

```python
row = conn.execute(
    "SELECT symbol FROM ops_trade_chains WHERE trade_chain_id=?",
    (chain_id,),
).fetchone()
assert row[0] == "FIDAUSDT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k persists_raw_symbol -q`
Expected: FAIL because the persisted value is still slash-style

- [ ] **Step 3: Normalize before constructing `EnrichedSignalPayload`**

Update `src/runtime_v2/signal_enrichment/processor.py` so the local `symbol` variable is canonicalized before `EnrichedSignalPayload(...)`:

```python
from src.runtime_v2.symbols import to_raw_symbol

# ...
symbol = to_raw_symbol(signal.symbol) or ""

enriched_signal = EnrichedSignalPayload(
    symbol=symbol or None,
    side=signal.side,
    ...
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/lifecycle/test_entry_gate.py -k persists_raw_symbol -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/signal_enrichment/processor.py tests/runtime_v2/lifecycle/test_entry_gate.py
git commit -m "feat: normalize runtime symbols before chain creation"
```

## Task 3: Align static lifecycle port with raw symbols

**Files:**
- Modify: `src/runtime_v2/lifecycle/static_exchange_data_port.py`
- Test: `tests/runtime_v2/lifecycle/test_ports.py`

- [ ] **Step 1: Add failing tests for raw lookup compatibility**

Add tests that verify the static port works when asked for raw symbols:

```python
def test_static_port_get_symbol_market_state_accepts_raw_symbol():
    snap = port.get_symbol_market_state("acc_1", "BTCUSDT")
    assert snap.symbol == "BTCUSDT"


def test_static_port_symbol_exists_accepts_raw_symbol():
    assert port.symbol_exists("acc_1", "BTCUSDT") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/lifecycle/test_ports.py -q`
Expected: FAIL because current fixtures/lookups are slash-oriented

- [ ] **Step 3: Implement minimal normalization inside the static port boundary**

Update the port to normalize incoming lookup keys and known-symbol membership checks with the shared helper:

```python
from src.runtime_v2.symbols import to_raw_symbol

lookup_symbol = to_raw_symbol(symbol) or symbol
```

Use the normalized value consistently for:

- `_markets` lookup
- returned fallback `SymbolMarketSnapshot.symbol`
- `symbol_exists`

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/lifecycle/test_ports.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/lifecycle/static_exchange_data_port.py tests/runtime_v2/lifecycle/test_ports.py
git commit -m "fix: align static exchange port with raw symbol runtime contract"
```

## Task 4: Preserve slash-style display for user-facing output

**Files:**
- Test: `tests/runtime_v2/control_plane/test_worker_clean_log_integration.py`

- [ ] **Step 1: Add the failing formatter integration test**

Add a test that seeds a chain with raw symbol and verifies display formatting stays human-friendly:

```python
def test_clean_log_displays_slash_symbol_when_chain_symbol_is_raw(ops_db):
    chain = chain_repo.save(TradeChain(
        source_enrichment_id=1,
        canonical_message_id=1,
        raw_message_id=1,
        trader_id="trader_a",
        account_id="main",
        symbol="BTCUSDT",
        side="LONG",
        lifecycle_state="OPEN",
        entry_mode="ONE_SHOT",
        management_plan_json="{}",
    ))
    # trigger existing projection/render path
    assert "BTC/USDT" in text
```

- [ ] **Step 2: Run test to verify current behavior**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/control_plane/test_worker_clean_log_integration.py -k slash_symbol -q`
Expected: PASS or FAIL; if it already passes, keep the test as regression coverage and proceed

- [ ] **Step 3: Make only the minimal code change if needed**

If the test fails, adjust only the display path and keep `display_symbol()` as the sole presentation-layer fix point. Do not change persistence or runtime-domain symbol format in this task.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/control_plane/test_worker_clean_log_integration.py -k slash_symbol -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/runtime_v2/control_plane/test_worker_clean_log_integration.py src/runtime_v2/control_plane/formatters
git commit -m "test: lock slash-style display for raw runtime symbols"
```

## Task 5: Prove funding attribution works with new raw-symbol chains

**Files:**
- Modify: `tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py`
- Modify: `tests/runtime_v2/control_plane/test_outbox_writer.py` or `tests/runtime_v2/lifecycle/test_workers.py`

- [ ] **Step 1: Add failing coverage for raw-symbol funding attribution**

Add a test that:

1. creates a new chain with raw symbol
2. inserts or simulates a `FUNDING_SETTLED` event with matching raw symbol and side
3. runs the existing worker/projection path
4. asserts the chain funding and final result funding are non-zero

Skeleton:

```python
def test_raw_symbol_chain_receives_funding_and_final_result_uses_it(...):
    # seed chain symbol="FIDAUSDT", side="LONG"
    # seed cumulative_gross_pnl and cumulative_fees as needed
    # simulate funding event payload {"exec_fee": 9.14732091, "source": "exchange_auto"}
    # run worker / project_clean_log_for_chain
    # assert cumulative_funding updated
    # assert final_result["funding"] == pytest.approx(-9.14732091)
```

- [ ] **Step 2: Run test to verify it fails before the normalization chain is in place**

Run: `.venv\\Scripts\\python.exe -m pytest tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py tests/runtime_v2/control_plane/test_outbox_writer.py -k funding -q`
Expected: FAIL on the new scenario if any slash/raw mismatch remains

- [ ] **Step 3: Implement only the missing glue if the test still fails**

Allowed fixes in this task:

- normalize any remaining symbol comparison boundary that still assumes slash-format
- keep `resolve_chain_for_fill(symbol, side)` exact-match based after normalization

Do not introduce fallback matching for mixed historical formats.

- [ ] **Step 4: Run focused tests to verify it passes**

Run:

```bash
.venv\Scripts\python.exe -m pytest \
  tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py \
  tests/runtime_v2/lifecycle/test_workers.py \
  tests/runtime_v2/control_plane/test_outbox_writer.py -k "funding or raw_symbol" -q
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py tests/runtime_v2/lifecycle/test_workers.py tests/runtime_v2/control_plane/test_outbox_writer.py src/runtime_v2/execution_gateway src/runtime_v2/lifecycle
git commit -m "fix: attribute funding to new raw-symbol chains"
```

## Task 6: Run the focused regression pack

**Files:**
- No code changes required unless a regression is found

- [ ] **Step 1: Run symbol/lifecycle/control-plane focused tests**

Run:

```bash
.venv\Scripts\python.exe -m pytest \
  tests/runtime_v2/test_symbols.py \
  tests/runtime_v2/lifecycle/test_ports.py \
  tests/runtime_v2/lifecycle/test_entry_gate.py \
  tests/runtime_v2/control_plane/test_worker_clean_log_integration.py \
  tests/runtime_v2/control_plane/test_outbox_writer.py \
  tests/runtime_v2/execution_gateway/test_bybit_ws_fill_watcher.py -q
```

Expected: PASS

- [ ] **Step 2: If any test fails, fix the owning layer only**

Allowed owning layers:

- `src/runtime_v2/signal_enrichment/processor.py`
- `src/runtime_v2/lifecycle/static_exchange_data_port.py`
- `src/runtime_v2/execution_gateway/*` only if a remaining raw/slash boundary exists

Do not patch symptoms in formatters unless the failure is purely presentation-related.

- [ ] **Step 3: Re-run the same regression pack**

Run the same command from Step 1.
Expected: PASS

- [ ] **Step 4: Commit final verification state**

```bash
git add src/runtime_v2 tests/runtime_v2
git commit -m "test: verify raw symbol runtime canonicalization"
```

## Spec Coverage Check

- Canonico interno raw: Task 1 + Task 2
- Persistenza nuove chain raw: Task 2
- Boundary compatibili raw: Task 3
- Display slash-style invariato: Task 4
- Funding raw attribuito correttamente: Task 5
- Verifica regressioni focalizzate: Task 6

## Placeholder Scan

Nessun `TODO`, `TBD` o task rinviato. Le uniche scelte condizionali sono esplicitate come guardrail su dove intervenire se un test resta rosso.

## Type Consistency Check

- helper condivisa: `to_raw_symbol`
- contratto target runtime: `EnrichedSignalPayload.symbol == raw`
- persistenza target: `ops_trade_chains.symbol == raw`
- display target: `display_symbol(raw) -> slash-style`


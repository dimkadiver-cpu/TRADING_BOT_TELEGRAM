# Bug: MARKET entry bloccata da missing_market_price_for_market_entry

## Sintomo

```
ops_lifecycle_events: REVIEW_REQUIRED
  reason: missing_market_price_for_market_entry
  source_id: enrichment_id=8
```

Il segnale XRPUSDT LONG ONE_SHOT MARKET (trader_a) viene bloccato nel lifecycle gate
e non crea trade chain né execution commands.

## Causa radice

`risk_capacity.py:68` blocca se `market_snapshot is None` per ordini MARKET:

```python
if first_leg.entry_type == "MARKET":
    if market_snapshot is None or market_snapshot.mark_price is None:
        return RiskDecision(passed=False, reason="missing_market_price_for_market_entry")
```

`StaticExchangeDataPort` non fa fetch live — restituisce sempre `None`.

## Design originale (da rispettare)

- Il prezzo MARKET nel segnale è **opzionale**
- La qty viene calcolata **a runtime** dall'execution layer usando il prezzo live
  del momento, prima di piazzare l'ordine sull'exchange

## Fix da implementare (5 file)

1. **`risk_capacity.py`** — per MARKET senza market_snapshot: non bloccare,
   passare con `size_usdt=None` e `risk_amount`/`sl_price` nel `risk_snapshot`

2. **`entry_gate.py`** (`_build_entry_commands`) — se `entry_price` deferred:
   mettere `"qty_mode": "deferred_market"`, `"risk_amount"`, `"sl_price"`
   nel payload invece di `"qty"` calcolata a zero

3. **`adapters/base.py`** — aggiungere metodo astratto:
   `fetch_mark_price(symbol, execution_account_id) -> float | None`

4. **`adapters/ccxt_bybit/adapter.py`** — implementare `fetch_mark_price`
   via `ccxt.fetch_ticker(symbol)['last']`

5. **`gateway.py`** (`process`) — prima di `place_order` su PLACE_ENTRY:
   se `payload.get("qty_mode") == "deferred_market"`, chiamare
   `adapter.fetch_mark_price`, calcolare
   `qty = risk_amount / abs(mark_price - sl_price)`,
   iniettare `qty` nel payload

## File coinvolti

- `src/runtime_v2/lifecycle/risk_capacity.py`
- `src/runtime_v2/lifecycle/entry_gate.py`
- `src/runtime_v2/execution_gateway/adapters/base.py`
- `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`
- `src/runtime_v2/execution_gateway/gateway.py`

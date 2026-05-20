# Bug: CANCEL_PENDING_ENTRY non cancella l'ordine su exchange

## Sintomo

Il comando `CANCEL_PENDING_ENTRY` risulta `SENT` nel DB ma l'ordine limite rimane
aperto su Bybit. Il `result_payload_json` mostra `exchange_order_id: null`.

## Causa radice

In `entry_gate.py:555` il payload del cancel contiene solo symbol e side:

```python
payload_json=json.dumps({"symbol": chain.symbol, "side": chain.side})
```

In `gateway.py`, il `client_order_id` generato per il comando di cancel usa
il proprio `command_id` (es. 5) → `tsb:1:5:entry:1`.

In `order_builder._cancel_pending_entry`, questo ID viene passato come `order_link_id`
a `fetch_open_orders(orderLinkId=...)` — ma Bybit non trova nessun ordine perché
l'ordine originale ha `orderLinkId = tsb:1:1:entry:1` (command_id dell'entry = 1).

## Fix da implementare

Il payload di `CANCEL_PENDING_ENTRY` deve includere il `client_order_id` originale
dell'entry leg da cancellare.

In `entry_gate.py`, quando si costruisce il cancel command, recuperare il
`client_order_id` del `PLACE_ENTRY` originale dal repository e aggiungerlo al payload:

```python
payload_json=json.dumps({
    "symbol": chain.symbol,
    "side": chain.side,
    "entry_client_order_id": "<tsb:1:1:entry:1>",  # client_order_id del PLACE_ENTRY
})
```

In `order_builder._cancel_pending_entry`, usare `payload["entry_client_order_id"]`
come `order_link_id` invece del `client_order_id` del comando corrente.

## File coinvolti

- `src/runtime_v2/lifecycle/entry_gate.py` — aggiungere `entry_client_order_id` al payload
- `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py` — leggere `entry_client_order_id` dal payload

## Workaround manuale

Cancellare direttamente via CCXT usando l'`exchange_order_id` dalla tabella
`ops_execution_commands` (colonna `result_payload_json.exchange_order_id`):

```python
ex.cancel_order('<exchange_order_id>', 'XRPUSDT')
```

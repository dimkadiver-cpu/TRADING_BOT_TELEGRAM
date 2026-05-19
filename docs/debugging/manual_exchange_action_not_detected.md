# Bug: azioni manuali sull'exchange non rilevate dal sync worker

## Sintomo

- Ordine entry cancellato manualmente su Bybit → chain rimane `WAITING_ENTRY` nel DB
- Posizione chiusa manualmente su Bybit → chain rimane `OPEN` nel DB

## Causa radice

`event_sync.py:48` — il `run_reconciliation` controlla solo se l'ordine è `FILLED`:

```python
if raw and raw.is_filled:   # CANCELLED ignorato completamente
    saved = self._normalize_and_save(...)
```

Se l'ordine è `CANCELLED` su Bybit, `is_filled = False`, il worker non fa nulla.
Il comando resta `SENT`, la chain resta bloccata nello stato corrente.

## Casistiche

### 1. Entry cancellata manualmente (chain in WAITING_ENTRY)
- `get_order_status` restituisce `status=CANCELLED`
- `is_filled = False` → nessuna azione
- Chain resta `WAITING_ENTRY` finché non scatta il timeout (24h)
- Nuovo segnale stesso simbolo → **bloccato** da `duplicate_position`

### 2. Posizione chiusa manualmente (chain in OPEN)
- Il sync non controlla la position qty reale su Bybit
- Chain resta `OPEN` nel DB con ordini SL/TP pendenti
- Gli ordini SL/TP su Bybit vengono rifiutati (posizione inesistente)
- Nuovo segnale stesso simbolo → **bloccato** da `duplicate_position`

## Fix da implementare

### Caso 1 — rilevare CANCELLED
In `event_sync.py`, dopo il check `is_filled`, aggiungere:

```python
if raw and raw.status == "CANCELLED":
    # emettere ExchangeEvent PENDING_ENTRY_CANCELLED_CONFIRMED
    # → lifecycle porta chain a CANCELLED
```

### Caso 2 — riconciliare posizione chiusa esternamente
Aggiungere un controllo periodico che chiama `adapter.get_position_qty(symbol, side)`
e se restituisce 0 mentre la chain è `OPEN`, emette `CLOSE_FULL_FILLED` con
`filled_qty = chain.open_position_qty`.

## File coinvolti

- `src/runtime_v2/execution_gateway/event_sync.py` — aggiungere rilevamento CANCELLED
- `src/runtime_v2/execution_gateway/event_sync.py` — aggiungere position reconciliation
- `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py` — `get_position_qty` già implementato

## Workaround manuale

Pulire il DB (`DELETE FROM ops_trade_chains WHERE lifecycle_state='WAITING_ENTRY'`)
oppure attendere il timeout automatico (24h se `cancel_pending_on_timeout: true`).

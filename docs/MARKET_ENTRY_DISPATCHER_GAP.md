# Gap architetturale: MARKET entry dispatcher e Freqtrade Trade lifecycle

**Data scoperta:** 2026-03-31
**Contesto:** sessione di test con injection scenari trader_a (s01_market_long)
**Stato attuale:** parzialmente mitigato con safety guard

---

## Problema

I segnali con entry MARKET non vengono eseguiti attraverso il lifecycle normale di Freqtrade.

### Flusso LIMIT (funzionante)

```
signal PENDING
    ↓
populate_entry_trend → enter_long/enter_short = 1
    ↓
Freqtrade chiama confirm_trade_entry
    ↓
Trade record creato in tradesv3.dryrun.sqlite
    ↓
Freqtrade gestisce SL / TP / position tracking
```

### Flusso MARKET (incompleto)

```
signal PENDING
    ↓
_maybe_dispatch_market_entries() → MarketEntryDispatcher
    ↓
gateway.create_entry_market_order() → exchange.create_order() diretto
    ↓
signal → ACTIVE nel bot DB
    ↓
[VUOTO] — nessun Trade record in Freqtrade
    ↓
Freqtrade non sa nulla della posizione aperta
→ niente SL, niente TP, niente position tracking
```

---

## Causa

`populate_entry_trend` esce esplicitamente per i segnali MARKET:

```python
# Strategy path handles only LIMIT entries.
# MARKET entries are handled by the market entry dispatcher.
if context.first_entry_order_type != "LIMIT":
    return dataframe
```

Il `MarketEntryDispatcher` è stato scritto come classe autonoma che chiama `exchange.create_order()` direttamente, bypassando il normale lifecycle Freqtrade (`confirm_trade_entry` → Trade record). Di conseguenza:

- L'ordine viene inviato all'exchange (o simulato in dry-run)
- Il bot DB aggiorna il signal a ACTIVE
- Ma Freqtrade non ha il Trade record → non piazza SL/TP, non traccia la posizione, non riporta P&L

---

## Stato prima della sessione

Il `MarketEntryDispatcher` esisteva come classe ma **non veniva mai chiamato** da nessuna parte nel codice di produzione. I segnali MARKET erano silenziosamente ignorati.

---

## Modifiche introdotte in questa sessione

### 1. Wiring del dispatcher in `SignalBridgeStrategy.populate_indicators`

```python
# freqtrade/user_data/strategies/SignalBridgeStrategy.py
def populate_indicators(self, dataframe, metadata):
    self._maybe_run_execution_reconciliation()
    self._maybe_dispatch_market_entries()   # ← AGGIUNTO
    ...
```

### 2. Fix `freqtrade_exchange_backend.py` — rimosso argomento `params`

La firma reale di `Exchange.create_order` in Freqtrade non accetta `params=`. Rimosso e sostituito con `leverage=1, reduceOnly=reduce_only` diretti.

### 3. Fix `market_entry_dispatcher.py` — fill immediato per MARKET

Il dry-run Freqtrade restituisce sempre `status: "open"` per gli ordini, mai `"FILLED"`. Il callback veniva chiamato solo su `"FILLED"`. Corretto a:

```python
if order.status in ("FILLED", "OPEN"):   # MARKET = fill immediato
```

### 4. Fix `fill_price: 0.0`

`create_entry_market_order` passava `rate=0.0` (MARKET senza prezzo). Il normalized order aveva `price=0.0` che passava il check `is not None`. Corretto a:

```python
fill_price = order.price if order.price is not None and order.price > 0 else reference_price
```

### 5. Safety guard dry-run only

Per evitare che il dispatcher apra posizioni reali senza SL/TP in produzione:

```python
def _maybe_dispatch_market_entries(self) -> None:
    config = getattr(self, "config", None)
    if not (isinstance(config, dict) and config.get("dry_run", False)):
        return   # bloccato in live
    ...
```

---

## Comportamento attuale (post-fix)

| Modalità | LIMIT | MARKET |
|----------|-------|--------|
| `dry_run: true` | Freqtrade Trade ✓ | Signal ACTIVE ✓, ma nessun FT Trade |
| `dry_run: false` | Freqtrade Trade ✓ | Bloccato dal safety guard |

---

## Soluzione da implementare (TODO)

Per completare il supporto MARKET in produzione serve una delle due strade:

### Opzione A — MARKET via populate_entry_trend (consigliata)

Estendere `populate_entry_trend` per segnalare anche i MARKET, poi gestire in `confirm_trade_entry` il tipo d'ordine. Richiede che Freqtrade sia configurato con `order_type.entry: market` oppure gestione condizionale per-segnale.

```python
# populate_entry_trend — aggiungere MARKET
if context.first_entry_order_type in ("LIMIT", "MARKET"):
    column = "enter_long" if context.side == "long" else "enter_short"
    self._set_last_row_value(dataframe, column, 1)
```

In `confirm_trade_entry` il check del tipo d'ordine va allineato di conseguenza.

**Pro:** Freqtrade gestisce il Trade record, SL/TP, position tracking — tutto funziona.
**Contro:** richiede config `order_type.entry: market` o logica per-segnale.

### Opzione B — Dispatcher crea Trade record manualmente

Dopo che il dispatcher crea l'ordine, inserire manualmente un record in `trades` e `orders` di Freqtrade con i dati del segnale. Molto fragile: dipende dallo schema interno di Freqtrade che cambia tra versioni.

**Pro:** nessuna modifica alla strategia.
**Contro:** accoppiamento forte con gli internals di Freqtrade, difficile da mantenere.

---

## File coinvolti

| File | Modifica |
|------|----------|
| `freqtrade/user_data/strategies/SignalBridgeStrategy.py` | wiring dispatcher + safety guard |
| `src/execution/market_entry_dispatcher.py` | fix fill status + fill_price |
| `src/execution/freqtrade_exchange_backend.py` | fix params → leverage/reduceOnly |

---

## Quando rimuovere il safety guard

Quando l'Opzione A (o B) è implementata e testata in dry-run con verifica che:

1. Il segnale MARKET diventa ACTIVE nel bot DB
2. Un Trade record compare in `tradesv3.dryrun.sqlite`
3. Gli ordini SL e TP vengono piazzati da Freqtrade
4. La chiusura del trade aggiorna correttamente il segnale nel bot DB

Solo a quel punto rimuovere il guard o sostituirlo con una flag di config dedicata (`execution.market_dispatch_enabled: true`).

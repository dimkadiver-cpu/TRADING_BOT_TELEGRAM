# PROMEMORIA — Ritardo notifiche Telegram (ENTRY_FILLED, TP_FILLED)

**Data:** 2026-06-18

## Causa strutturale

`execution_command_worker.run_once()` esegue REST calls ccxt **sincrone** direttamente
nell'asyncio event loop — blocca tutto il loop finché Bybit risponde (200ms–2s+).
Il `TelegramNotificationDispatcher` non può girare durante questo blocco,
poi aspetta ulteriori 0–2s sul suo poll fisso.

## Flusso con ritardo

```
WS fill ricevuto
    │ ~0ms
lifecycle_event_worker.run_once()  → scrive ENTRY_OPENED / TP_FILLED in outbox
    │ ~0ms
execution_command_worker.run_once() → REST calls Bybit (BLOCCANTE)
    │ 200ms–2s+   ← il dispatcher NON può girare qui
asyncio loop libero
    │ 0–2s   ← poll fisso dispatcher
Messaggio Telegram inviato
```

Worst case osservabile: **4–5 secondi**.

## Perché ENTRY_FILLED è peggio

Dopo un entry fill con `rebuild_policy=ON_EACH_ENTRY_FILL` viene generato
immediatamente `REBUILD_PARTIAL_TPS` → multiple REST calls nella stessa iterazione.

## Perché TP_FILLED è "sometimes"

Dipende da quanti comandi pendenti ci sono in quella iterazione
(es. `MOVE_STOP_TO_BREAKEVEN` dopo TP1 se `be_trigger=tp1`) e dalla latenza
Bybit in quel momento (variabile).

## File chiave

| File | Riga | Nota |
|---|---|---|
| `main.py` | 373–380 | sequenza sincrona dei worker nell'event loop |
| `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py` | 222–260 | REST calls sincrone (`create_order`, `fetch_open_orders`, `edit_order`) |
| `src/runtime_v2/control_plane/notification_dispatcher.py` | 408–414 | `poll_interval_seconds=2.0` fisso |

## Opzioni fix

| Fix | Effort | Effetto | Rischi |
|---|---|---|---|
| `poll_interval` 2.0 → 0.5s | minimo | riduce worst-case, nessun blocco rimosso | nessuno |
| `run_in_executor` per `execution_command_worker` | basso | libera il loop | SQLite lock, race condition ordering comandi |
| **Migrare adapter a `ccxt.async_support`** | alto | elimina il blocco alla radice | refactor gateway completo |

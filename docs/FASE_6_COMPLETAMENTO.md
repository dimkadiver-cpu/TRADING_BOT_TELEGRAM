# Fase 6 - Completamento operativo

Questo documento raccoglie l'evidenza minima di chiusura della Fase 6 in modalita `exchange_manager`.

Riferimenti:

- `docs/PRD_FASE_6.md`
- `docs/FREQTRADE_CONFIG.md`
- `docs/FREQTRADE_RUNBOOK.md`
- `docs/AUDIT.md`

## Stato di chiusura

La Fase 6 e chiusa sul piano implementativo e validata in dry-run avanzato exchange-backed.

Evidenza osservata localmente:

- `NEW_SIGNAL` -> entry fill -> creazione di `SL` + ladder `TP`
- tutti i protettivi con `exchange_order_id` persistito
- `U_MOVE_STOP` applicato al vero `SL` exchange-backed con replace conservativo
- `TP1` fillato con riallineamento di size residua e ordini residui
- restart del runtime con `bootstrap_sync_open_trades()` e riconciliazione riuscita
- nessun doppio owner `strategy`/`manager`
- nessun ordine duplicato nel backend runtime aperto

## Evidenza eseguita

Test end-to-end usato come prova di fase:

```powershell
pytest C:\TeleSignalBot\src\execution\tests\test_phase6_e2e.py -q
```

Esito osservato:

- `1 passed`

Suite execution completa:

```powershell
pytest C:\TeleSignalBot\src\execution -q
```

Esito osservato:

- `59 passed`

## Sequenza verificata

Scenario verificato nel test `test_phase6_end_to_end_entry_update_tp_restart_reconciliation`:

1. creazione `NEW_SIGNAL` nel DB
2. `entry fill`
3. creazione `SL` + `TP1` + `TP2` + `TP3` sul backend exchange fake ma stateful
4. `U_MOVE_STOP` con cancel del vecchio `SL` e create del nuovo
5. `TP1` fill con rebuild coerente del ladder residuo
6. restart del runtime
7. `bootstrap_sync_open_trades()` invocata dal startup execution-side
8. riconciliazione riuscita con riallineamento finale degli ordini aperti

## Stato finale osservato

Ordini protettivi finali aperti dopo restart + reconciliation:

- `SL` -> `atk_phase6_e2e:SL:0:R3`
- `TP2` -> `atk_phase6_e2e:TP:1:R2`
- `TP3` -> `atk_phase6_e2e:TP:2:R2`

Quantita finali aperte:

- `SL.qty = 1.4`
- `TP2.qty = 0.6`
- `TP3.qty = 0.8`

Eventi chiave osservati nel DB:

- `ENTRY_FILLED`
- `STOP_REPLACED`
- `TP_FILL_SYNCED`
- `RECONCILIATION_COMPLETED`

Warnings osservati nel caso end-to-end:

- nessuno

## Query DB minime di verifica

Protettivi exchange-backed:

```sql
SELECT purpose, idx, client_order_id, exchange_order_id, status, qty, price, trigger_price
FROM orders
WHERE attempt_key = '<attempt_key>'
  AND purpose IN ('SL', 'TP')
ORDER BY order_pk;
```

Eventi:

```sql
SELECT event_type, created_at
FROM events
WHERE attempt_key = '<attempt_key>'
ORDER BY event_id;
```

Warnings:

```sql
SELECT code, detail_json, created_at
FROM warnings
WHERE attempt_key = '<attempt_key>'
ORDER BY warning_id;
```

## Checklist finale

- [x] `SL` reale presente nel backend exchange-backed
- [x] ladder `TP` reale presente nel backend exchange-backed
- [x] `exchange_order_id` persistiti nel DB
- [x] update di stop applicato agli ordini reali
- [x] fill di un TP gestito con riallineamento coerente
- [x] restart con riconciliazione riuscita
- [x] nessun doppio owner `strategy`/`manager`
- [x] nessun ordine duplicato nel backend runtime aperto

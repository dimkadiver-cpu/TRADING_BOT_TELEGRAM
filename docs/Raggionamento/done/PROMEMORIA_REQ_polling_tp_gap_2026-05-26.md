# Promemoria — gap polling TP su REQ

**Data:** 2026-05-26

## Contesto

Caso osservato su `db/ops.sqlite3`, chain `trade_chain_id=10`, symbol `REQUSDT`, side `SHORT`.

Nel DB risultano:

- 2 eventi `ENTRY_FILLED`
- 0 eventi `TP_FILLED`
- 1 `SET_POSITION_TPSL_PARTIAL` `SUPERSEDED`
- 1 `SET_POSITION_TPSL_PARTIAL` `DONE`
- chain ancora `OPEN` con `filled_entry_qty=open_position_qty=10882.0`

## Domanda da affrontare

Perché il polling trade-based ogni 60s non ha registrato i TP parziali / finali nel caso REQ?

## Stato attuale del runtime

Il polling periodico è attivo:

- `main.py` avvia sempre `_run_position_reconciliation_periodically(...)`
- dentro quel loop vengono chiamati:
  - `run_position_reconciliation()`
  - `run_trade_based_reconciliation()`
  - `run_protective_orders_reconciliation()`

Il polling TP attuale (`run_trade_based_reconciliation`) legge i target da `_get_tp_reconciliation_entries()`.

## Punto chiave

`_get_tp_reconciliation_entries()` considera solo:

- `SET_POSITION_TPSL_PARTIAL`
- `SET_POSITION_TPSL_FULL`

Non considera:

- `PLACE_ENTRY_WITH_ATTACHED_TPSL`

Questo è un gap importante perché nel caso REQ il **final TP** è attached al primo entry command, non è rappresentato come `SET_POSITION_TPSL_FULL`.

## Struttura REQ rilevata

Per la chain `10`:

- `PLACE_ENTRY_WITH_ATTACHED_TPSL`
  - contiene TP finale attached `0.0793`
- `PLACE_ENTRY`
  - seconda leg
- `SET_POSITION_TPSL_PARTIAL`
  - TP intermedio `0.08483`, prima qty `3475.5`, poi superseded
- `SET_POSITION_TPSL_PARTIAL`
  - TP intermedio `0.08483`, qty finale `5441.0`, stato `DONE`

## Conclusione tecnica attuale

Il piano `docs/superpowers/plans/2026-05-26-tp-reconciliation-overhaul.md` ha migliorato il polling:

- da confronto qty
- a polling trade-based con `fetch_my_trades()`

Ma **non ha chiuso il gap di ownership del dato**:

- il polling nuovo continua a dipendere da quali TP sono rappresentati nei `command_type` letti dal DB
- il final TP attached non entra in quel set
- il partial TP rebuild entra, ma tramite un path fragile (`SET_POSITION_TPSL_PARTIAL` multipli + supersede chain-wide)

## Altri stati già coperti

Il runtime oggi copre già:

- `ENTRY_FILLED`
- `SL_FILLED`
- `CLOSE_PARTIAL_FILLED`
- `CLOSE_FULL_FILLED`
- `PENDING_ENTRY_CANCELLED_CONFIRMED`
- chiusure manuali full position via `run_position_reconciliation()`
- rimozione ordini protettivi via `PROTECTIVE_ORDERS_MISSING`

Il gap principale resta sui TP in setup misti:

- final TP attached
- partial TP rebuild post-fill

## Ipotesi forti da validare

1. Il polling non può registrare il final TP REQ perché non legge `PLACE_ENTRY_WITH_ATTACHED_TPSL`.
2. Il polling può perdere o interpretare male il partial TP se lo stato exchange-side diverge dai `SET_POSITION_TPSL_PARTIAL` rimasti attivi nel DB.
3. Il problema non sembra essere il timer dei 60s, ma il modello dei TP osservabili.

## Direzioni di lavoro

1. Estendere `_get_tp_reconciliation_entries()` per rappresentare anche i final TP attached, o introdurre una vista canonica unica dei TP osservabili.
2. Eliminare il path fragile di rebuild post-fill basato su multipli `SET_POSITION_TPSL_PARTIAL`.
3. Introdurre una riconciliazione che ragioni sullo stato protettivo reale exchange-side, non solo sui command type DB-side.

## Messaggio corto

Il polling è stato modernizzato, ma non ha ancora una source of truth completa per i TP del caso REQ.

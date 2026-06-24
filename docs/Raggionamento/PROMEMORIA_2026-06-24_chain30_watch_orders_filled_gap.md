# PROMEMORIA 2026-06-24 — chain 30, ritardo notifica e gap `watch_orders`

## Contesto

Analisi eseguita sul dataset:

- `C:\TeleSignalBot\db\Test_live\db\ops.sqlite3`
- `C:\TeleSignalBot\db\Test_live\db\parser.sqlite3`

Caso osservato: `trade_chain_id = 30` (`FLOWUSDT`, `SHORT`, account `demo_1`).

## Sintomo osservato

La notifica `ENTRY_OPENED` arriva in ritardo, ma il ritardo non nasce nel dispatcher Telegram.

Timeline ricostruita da `ops.sqlite3`:

- `2026-06-24T17:53:06.672646+00:00` — chain creata, `SIGNAL_ACCEPTED`
- `2026-06-24T17:53:07.374000+00:00` — fill exchange (`exchange_time`)
- `2026-06-24T17:53:07.479763+00:00` — evento raw WS ricevuto in `exchange_raw_events`
- `2026-06-24T17:53:57.676566+00:00` — `ENTRY_FILLED` inserito in `ops_exchange_events`
- `2026-06-24T17:53:57.733372+00:00` — outbox `ENTRY_OPENED`
- `2026-06-24T17:54:00.064187+00:00` — messaggio Telegram inviato

Gap principale:

- circa `50.2s` tra ricezione del raw event WS e promozione a evento operativo
- circa `2.3s` tra outbox e invio Telegram

Conclusione: il collo di bottiglia è prima della notifica.

## Root cause identificata

Nel record `exchange_raw_events.raw_event_id = 2119` era già presente un evento `watch_orders` con:

- `order_status = "closed"`
- `order_link_id = "tsb:30:231:entry:1:mqsdg12o"`
- `cum_exec_qty = 58139.5`
- `avgPrice = 0.02759`
- `trade_chain_id = 30`

Quindi il sistema aveva già l'informazione sufficiente per riconoscere un fill noto della chain.

Problema nel classifier:

- ramo `watch_orders`
- per status `Filled/closed` con `orderLinkId` noto
- l'evento veniva degradato a `ORDER_OPEN_UPDATE`
- quindi `forwarded_to_lifecycle = 0`
- il fill veniva poi recuperato solo dal fallback REST/reconciliation

## Fix applicato

Modifica in:

- `src/runtime_v2/execution_gateway/event_ingest/classifier.py`

Nuovo comportamento:

- per `watch_orders` con `orderStatus in {Filled, closed}` e `orderLinkId` noto
- usa `_event_from_role(...)`
- quindi classifica subito come evento actionable (`ENTRY_FILLED`, `TP_FILLED`, `SL_FILLED`, `CLOSE_PARTIAL_FILLED`, `CLOSE_FULL_FILLED`)

Test aggiunto:

- `tests/runtime_v2/execution_gateway/test_event_ingest_classifier.py`
- caso nuovo: `watch_orders` + `Filled` + `orderLinkId` entry noto -> `ENTRY_FILLED`

## Stato copertura eventi exchange

### Coperti bene

- `watch_my_trades` / execution streams con `orderLinkId` noto
- `TP_FILLED` / `SL_FILLED` position-level via `createType` + enrichment `symbol/side`
- `MANUAL_CLOSE_FULL` / `MANUAL_CLOSE_PARTIAL`
- `FUNDING_SETTLED`
- fallback REST:
  - `ENTRY_FILLED`
  - `PENDING_ENTRY_CANCELLED_CONFIRMED`
  - `TP_FILLED`
  - `CLOSE_FULL_FILLED` sintetico
  - `PROTECTIVE_ORDER_CANCELLED`

### Coperti dopo il fix

- `watch_orders` `Filled/closed` con `orderLinkId` noto e ruolo noto

### Residui / gap

- `watch_orders` `Filled/closed` senza `orderLinkId` noto
- casi con `orderLinkId` sconosciuto o non mappato
- copertura test non ancora esplicita per tutte le combinazioni:
  - `watch_orders` + `tp`
  - `watch_orders` + `sl`
  - `watch_orders` + `exit_partial`
  - `watch_orders` + `exit_full`
- drift tra test e comportamento reale su `watch_positions` empty slot:
  - test si aspettano `UNKNOWN`
  - codice restituisce `POSITION_SNAPSHOT_EMPTY`

## Nota sul dataset parser

Nel dataset `db\Test_live\db\parser.sqlite3` non risultano presenti:

- `raw_message_id = 585`
- `canonical_message_id = 582`
- `enrichment_id = 582`

quindi il lato parser della stessa chain non è allineato a `ops.sqlite3` in questa copia locale.

Conseguenza:

- l'audit end-to-end dal parser al lifecycle è completo sul lato operativo
- ma non è completamente verificabile in questa copia dal lato parser

## Follow-up consigliati

1. Aggiungere test espliciti per `watch_orders filled` su ruoli `tp`, `sl`, `exit_partial`, `exit_full`.
2. Decidere semanticamente se `watch_positions` empty slot deve essere `UNKNOWN` o `POSITION_SNAPSHOT_EMPTY`, poi riallineare test e codice.
3. Fare un audit separato sui casi senza `orderLinkId`.
4. Verificare perché la copia `parser.sqlite3` del dataset live non contiene gli ID referenziati da `ops.sqlite3`.

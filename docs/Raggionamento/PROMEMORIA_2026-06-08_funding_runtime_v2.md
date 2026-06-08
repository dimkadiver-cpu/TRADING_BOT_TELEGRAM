# Promemoria - Funding nel runtime v2

## Punto da fissare

Nel runtime v2 il funding non e piu "non tracciato" in assoluto.

Oggi esiste un path reale che:

- riceve un evento exchange `FUNDING_SETTLED`;
- lo attribuisce a una `trade_chain_id`;
- accumula il costo in `ops_trade_chains.cumulative_funding`;
- usa quel valore nel `final_result` dei clean log finali.

## Comportamento reale nel codice

Il worker lifecycle gestisce `FUNDING_SETTLED` separatamente dal resto degli eventi:

- file: `src/runtime_v2/lifecycle/workers.py`
- funzione: `_handle_funding_settled()`

Logica attuale:

1. se `trade_chain_id` e assente, non fa nulla;
2. legge `payload_json`;
3. prende `exec_fee` come importo funding;
4. se l'importo e `0.0`, non scrive nulla;
5. altrimenti esegue:

```sql
UPDATE ops_trade_chains
SET cumulative_funding = COALESCE(cumulative_funding, 0.0) + ?
WHERE trade_chain_id=?
```

Quindi il funding viene persistito su chain come costo cumulato positivo.

## Segno del dato

In persistenza:

- `cumulative_funding > 0` significa costo funding pagato.

Nel clean log finale:

- `final_result.funding` viene mostrato con segno negativo;
- `total_pnl_net = gross_pnl - fees - funding`.

Questo e coerente con `src/runtime_v2/control_plane/outbox_writer.py`, funzione `_final_result()`.

Esempio:

- `cumulative_funding = 0.07628025`
- `final_result["funding"] = -0.07628025`

Quindi:

- nel DB il funding e memorizzato come costo assoluto positivo;
- nel messaggio finale viene esposto come contributo negativo al netto.

## Copertura test

Esistono gia test che fissano questo comportamento:

- `tests/runtime_v2/lifecycle/test_workers.py::test_lifecycle_worker_funding_settled_stores_positive_exchange_fee_as_positive_cost`
- `tests/runtime_v2/control_plane/test_outbox_writer.py::test_position_closed_final_result_subtracts_positive_funding_cost`

In sintesi i test verificano che:

- il worker scrive `cumulative_funding` su `ops_trade_chains`;
- il clean log finale sottrae il funding dal netto;
- il campo renderizzato in output finale e negativo.

## Limite reale da ricordare

Il funding non genera un clean log dedicato.

L'evento `FUNDING_SETTLED` oggi serve come dato contabile:

- aggiorna la chain;
- influenza il `final_result` delle notifiche finali (`POSITION_CLOSED`, `SL_FILLED`, `BE_EXIT`);
- non produce un messaggio utente separato.

Quindi il funding e visibile soprattutto a chiusura trade, non come evento operativo standalone.

## Deriva documentale

Alcune doc nel repository dicono ancora che nessun worker scrive `cumulative_funding`.

In particolare:

- `docs/runtime_v2/exchange_sync_overview.md`
- `docs/runtime_v2/exchange_sync_technical.md`

Questo non e piu vero rispetto al codice corrente.

## Conclusione pratica

Stato corretto da ricordare:

- il funding e supportato a livello contabile nel runtime v2;
- viene accumulato su `ops_trade_chains.cumulative_funding`;
- rientra nel `final_result` netto dei clean log finali;
- non esiste ancora come notifica autonoma dedicata;
- le doc che lo descrivono come "mai scritto" sono da considerare stale.

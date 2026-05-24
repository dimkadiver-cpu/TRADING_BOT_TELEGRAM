# Ops DB - descrizione tabelle

Questo documento descrive il contenuto delle tabelle di `db/ops.sqlite3` nello stato corrente del runtime V2.

Fonte primaria:

- `db/ops_migrations/001_ops_lifecycle_core.sql`
- `db/ops_migrations/002_ops_execution_gateway.sql`
- `db/ops_migrations/003_ops_quantity_runtime.sql`
- `db/ops_migrations/004_ops_plan_state.sql`

Nota:

- non tutte le tabelle devono necessariamente avere righe in ogni test;
- alcune sono tabelle operative, altre sono audit/snapshot, altre sono supporto;
- il runtime corrente scrive solo in un sottoinsieme delle tabelle disponibili.

## Sintesi rapida

| Tabella | Ruolo | Scritta da runtime | Necessaria nel live minimo |
|---|---|---:|---:|
| `ops_trade_chains` | stato della chain operativa | sì | sì |
| `ops_lifecycle_events` | eventi decisionali del lifecycle | sì | sì |
| `ops_execution_commands` | comandi verso exchange/gateway | sì | sì |
| `ops_exchange_events` | eventi normalizzati da exchange/reconciliation | sì | sì, quando il flusso arriva a fill/cancel/close |
| `ops_account_snapshots` | snapshot account per risk/audit | sì | no |
| `ops_market_snapshots` | snapshot mercato per risk/audit | sì | no |
| `ops_order_snapshots` | snapshot ordini grezzi | no, non usata dal runtime attuale | no |
| `ops_position_snapshots` | snapshot posizioni grezze | no, non usata dal runtime attuale | no |
| `ops_control_state` | blocchi/stop di controllo | sì, ma solo se configurati | no |

## `ops_trade_chains`

Contiene una riga per ogni chain operativa creata a partire da un segnale passato dall'enrichment/lifecycle gate.

Scopo:

- rappresentare l'unità operativa del trade;
- mantenere lo stato della chain lungo tutto il ciclo di vita;
- collegare il messaggio sorgente ai comandi e agli eventi successivi.

Campi principali:

- `trade_chain_id`: chiave primaria.
- `source_enrichment_id`: riferimento univoco al messaggio arricchito sorgente.
- `canonical_message_id`, `raw_message_id`: tracciamento verso parser e intake.
- `trader_id`, `account_id`, `symbol`, `side`: identità operativa della chain.
- `lifecycle_state`: stato corrente, per esempio `WAITING_ENTRY`, `OPEN`, `PARTIALLY_CLOSED`, `CLOSED`, `CANCELLED`, `EXPIRED`.
- `entry_mode`: modalità di ingresso, per esempio `MARKET` o `LIMIT`.
- `entry_avg_price`, `current_stop_price`, `expected_stop_price`: stato prezzo della posizione e dello stop.
- `be_protection_status`: stato della protezione break-even.
- `entry_timeout_at`: scadenza del pending entry, se prevista.
- `management_plan_json`, `risk_snapshot_json`, `plan_state_json`: payload JSON di piano, rischio e stato del piano.
- `planned_entry_qty`, `filled_entry_qty`, `open_position_qty`, `closed_position_qty`: quantità operative.
- `last_position_sync_at`: ultimo sync posizione.
- `execution_mode`: modalità operativa della chain.
- `created_at`, `updated_at`: audit temporale.

Writer attuale:

- `src/runtime_v2/lifecycle/repositories.py`
- `src/runtime_v2/lifecycle/entry_gate.py`
- `src/runtime_v2/lifecycle/workers.py`

Uso nel live:

- è la tabella principale per capire se il segnale è diventato una trade chain reale;
- è la prima tabella da controllare quando un caso live non produce il comportamento atteso.

## `ops_lifecycle_events`

Contiene gli eventi decisionali prodotti dal sistema operativo.

Scopo:

- registrare le decisioni del lifecycle;
- rendere auditabile il motivo per cui una chain cambia stato;
- evitare logica implicita non tracciata.

Campi principali:

- `event_id`: chiave primaria.
- `trade_chain_id`: chain collegata, se disponibile.
- `event_type`: tipo di evento, per esempio `SIGNAL_ACCEPTED`, `TRADE_CHAIN_CREATED`, `ENTRY_FILLED`, `TP_FILLED`, `SL_FILLED`, `TIMEOUT_REACHED`, `REVIEW_REQUIRED`.
- `source_type`: origine dell'evento, per esempio `signal`, `exchange_event`, `timeout`, `manual`.
- `source_id`: identificativo esterno o interno della sorgente.
- `previous_state`, `next_state`: transizione di stato.
- `payload_json`: dati aggiuntivi.
- `idempotency_key`: chiave unica per evitare duplicati.
- `created_at`: audit temporale.

Writer attuale:

- `src/runtime_v2/lifecycle/repositories.py`
- `src/runtime_v2/lifecycle/entry_gate.py`
- `src/runtime_v2/lifecycle/workers.py`

Uso nel live:

- deve contenere almeno gli eventi principali della chain accettata;
- è il posto giusto per capire perché una chain è stata bloccata, chiusa o mandata in revisione.

## `ops_execution_commands`

Contiene i comandi che il runtime vuole inviare o ha già inviato all'exchange o al gateway di esecuzione.

Scopo:

- separare la decisione operativa dall'invio reale;
- rendere possibile retry, idempotenza e recovery;
- tracciare lo stato di ogni comando.

Campi principali:

- `command_id`: chiave primaria.
- `trade_chain_id`: chain di appartenenza.
- `command_type`: comando operativo, per esempio `PLACE_ENTRY`, `PLACE_ENTRY_WITH_ATTACHED_TPSL`, `PLACE_PROTECTIVE_STOP`, `PLACE_TAKE_PROFIT`, `MOVE_STOP_TO_BREAKEVEN`, `MOVE_STOP`, `CANCEL_PENDING_ENTRY`, `CLOSE_PARTIAL`, `CLOSE_FULL`, `SYNC_PROTECTIVE_ORDERS`.
- `status`: stato corrente, per esempio `PENDING`, `SENT`, `ACK`, `WAITING_POSITION`, `DONE`, `FAILED`, `REVIEW_REQUIRED`, `CANCELLED`, `SUPERSEDED`.
- `payload_json`: payload del comando.
- `idempotency_key`: chiave unica per evitare duplicazioni.
- `adapter`, `execution_account_id`: routing verso adapter/account.
- `client_order_id`: identificatore deterministico usato verso l'exchange.
- `result_payload_json`: risposta o risultato normalizzato.
- `sent_at`, `acknowledged_at`, `completed_at`: timestamp di avanzamento.
- `retry_count`, `next_retry_at`: logica di retry.
- `created_at`, `updated_at`: audit temporale.

Writer attuale:

- `src/runtime_v2/lifecycle/repositories.py`
- `src/runtime_v2/lifecycle/entry_gate.py`
- `src/runtime_v2/lifecycle/workers.py`
- `src/runtime_v2/execution_gateway/repositories.py`
- `src/runtime_v2/execution_gateway/command_worker.py`

Uso nel live:

- è la tabella più importante per verificare che il comando sia stato effettivamente preso in carico;
- se un test live fallisce, qui si vede quasi sempre il primo sintomo.

## `ops_exchange_events`

Contiene gli eventi normalizzati in ingresso dall'exchange o dal layer di reconciliation.

Scopo:

- registrare fill, cancel, reject e altri eventi provenienti dal mercato;
- alimentare il lifecycle worker;
- rendere la riconciliazione auditabile.

Campi principali:

- `exchange_event_id`: chiave primaria.
- `trade_chain_id`: chain collegata, se nota.
- `event_type`: tipo evento, per esempio `ENTRY_FILLED`, `TP_FILLED`, `SL_FILLED`, `CLOSE_PARTIAL_FILLED`, `CLOSE_FULL_FILLED`, `STOP_MOVED_CONFIRMED`, `PENDING_ENTRY_CANCELLED_CONFIRMED`, `ORDER_REJECTED`, `ORDER_CANCELLED`.
- `payload_json`: payload normalizzato.
- `processing_status`: stato del consumer, per esempio `NEW`, `DONE`, `FAILED`.
- `idempotency_key`: chiave unica contro duplicati.
- `received_at`, `processed_at`: audit temporale.

Writer attuale:

- `src/runtime_v2/lifecycle/repositories.py`
- `src/runtime_v2/execution_gateway/event_sync.py`
- `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/ws_fill_watcher.py`
- `src/runtime_v2/lifecycle/workers.py`

Uso nel live:

- deve popolarsi quando arrivano fill, cancel o riconciliazioni;
- è la base per verificare che il runtime stia leggendo il mercato in modo coerente.

## `ops_account_snapshots`

Contiene snapshot dello stato account usati per audit e risk.

Scopo:

- registrare equity, saldo disponibile e rischio totale;
- supportare validazioni di rischio e diagnosi operative;
- conservare uno storico minimale dello stato account.

Campi principali:

- `snapshot_id`: chiave primaria.
- `account_id`: account logico.
- `equity_usdt`, `available_balance_usdt`, `total_open_risk_usdt`, `total_margin_used_usdt`: metriche account.
- `source`: origine dello snapshot.
- `captured_at`: momento della cattura.
- `payload_json`: payload grezzo.

Writer attuale:

- `src/runtime_v2/lifecycle/repositories.py`
- `src/runtime_v2/lifecycle/entry_gate.py`

Uso nel live:

- utile, ma non indispensabile per la verifica minima di un singolo caso;
- se è vuota, non significa necessariamente che il runtime sia rotto.

## `ops_market_snapshots`

Contiene snapshot del mercato usati per audit e risk.

Scopo:

- registrare mark price, bid/ask e precisioni di mercato;
- supportare sizing e controlli operativi;
- conservare uno storico minimo del contesto di mercato.

Campi principali:

- `snapshot_id`: chiave primaria.
- `account_id`: account logico associato.
- `symbol`: simbolo monitorato.
- `mark_price`, `bid`, `ask`: prezzi correnti.
- `min_order_size`, `price_precision`, `qty_precision`: vincoli di mercato.
- `source`: origine dello snapshot.
- `captured_at`: momento della cattura.
- `payload_json`: payload grezzo.

Writer attuale:

- `src/runtime_v2/lifecycle/repositories.py`
- `src/runtime_v2/lifecycle/entry_gate.py`

Uso nel live:

- utile per audit e risk;
- non è un criterio sufficiente da solo per giudicare il successo di un test.

## `ops_order_snapshots`

Contiene snapshot grezzi degli ordini.

Stato attuale:

- la tabella esiste nello schema;
- nel runtime attuale non risulta scritta dal flusso operativo principale.

Scopo previsto:

- conservare payload ordini grezzi;
- supportare un audit dettagliato degli ordini del provider.

Uso nel live:

- di fatto non è una tabella da aspettarsi piena nei test live correnti.

## `ops_position_snapshots`

Contiene snapshot grezzi delle posizioni.

Stato attuale:

- la tabella esiste nello schema;
- nel runtime attuale non risulta scritta dal flusso operativo principale.

Scopo previsto:

- conservare payload posizione grezzi;
- supportare audit e recovery futuro.

Uso nel live:

- non è una tabella da usare come criterio di verifica del test corrente.

## `ops_control_state`

Contiene i blocchi di controllo globali o per scope.

Scopo:

- bloccare nuovi ingressi;
- applicare stop operativi per account, trader, simbolo o side;
- consentire una gestione manuale di emergenza.

Campi principali:

- `control_id`: chiave primaria.
- `scope_type`: ambito, per esempio `GLOBAL`, `ACCOUNT`, `TRADER`, `SYMBOL`, `SIDE`.
- `scope_value`: valore dello scope, quando previsto.
- `execution_pause_mode`: modalità di pausa, per esempio `NONE`, `BLOCK_NEW_ENTRIES`, `FULL_STOP`.
- `emergency_action`, `reason`, `created_by`: metadati di controllo.
- `active`: flag di attivazione.
- `created_at`, `updated_at`: audit temporale.

Writer attuale:

- `src/runtime_v2/lifecycle/repositories.py`
- test e setup manuali del runtime

Uso nel live:

- si popola solo quando vuoi verificare un blocco di controllo;
- non deve avere righe per forza nei test normali.

## Nota operativa

Per un test live minimo, le tabelle che devi davvero controllare sono:

1. `ops_trade_chains`
2. `ops_lifecycle_events`
3. `ops_execution_commands`
4. `ops_exchange_events`

Le tabelle snapshot e `ops_control_state` servono per casi specifici o audit, ma non sono obbligatorie in ogni scenario.


# Automatismi del runtime v2

Questo documento descrive solo gli automatismi effettivamente implementati oggi in `src/runtime_v2/`.
La fonte di verita e il codice nei moduli `lifecycle/`, `execution_gateway/` e `control_plane/`.

## Ambito

Gli automatismi reali si concentrano in questi punti:

- `src/runtime_v2/lifecycle/entry_gate.py`
- `src/runtime_v2/lifecycle/event_processor.py`
- `src/runtime_v2/lifecycle/workers.py`
- `src/runtime_v2/lifecycle/post_fill_rebuilder.py`
- `src/runtime_v2/lifecycle/cancel_expander.py`
- `src/runtime_v2/execution_gateway/gateway.py`
- `src/runtime_v2/control_plane/outbox_writer.py`

## Stati e primitive usate dal runtime

### Stati chain

Le chain runtime usano questi stati:

- `CREATED`
- `WAITING_ENTRY`
- `OPEN`
- `PARTIALLY_CLOSED`
- `BE_MOVE_PENDING`
- `PROTECTED_BE`
- `CLOSED`
- `CANCELLED`
- `EXPIRED`
- `REVIEW_REQUIRED`
- `ERROR`

Gli stati terminali sono:

- `CLOSED`
- `CANCELLED`
- `EXPIRED`

### Tipi di comando che il sistema puo emettere

- `PLACE_ENTRY`
- `PLACE_ENTRY_WITH_ATTACHED_TPSL`
- `SET_POSITION_TPSL_FULL`
- `SET_POSITION_TPSL_PARTIAL`
- `MOVE_STOP_TO_BREAKEVEN`
- `MOVE_STOP`
- `MOVE_POSITION_STOP`
- `CANCEL_PENDING_ENTRY`
- `CANCEL_POSITION_TPSL`
- `CLOSE_PARTIAL`
- `CLOSE_FULL`
- `REBUILD_PARTIAL_TPS`

Nota importante: non tutti i command type definiti nel modello vengono generati oggi dagli automatismi. Quelli effettivamente emessi dal flusso automatico corrente sono documentati nelle sezioni sotto.

## 1. Arrivo di un nuovo SIGNAL

Trigger: `LifecycleGateWorker` processa una riga `enriched_canonical_messages` con `primary_class="SIGNAL"`.

### Automatismi applicati

1. `control_mode` gate
   - Se il control plane e in `BLOCK_NEW_ENTRIES` o `FULL_STOP`, il segnale non genera ordini.
   - Il runtime produce `REVIEW_REQUIRED`.

2. Validazione minima del segnale
   - Mancanza di `symbol`, `side` o legs di entry -> `REVIEW_REQUIRED`.

3. Snapshot runtime
   - Il gate legge stato account e snapshot mercato dall`ExchangeDataPort`.

4. Risk capacity check
   - `RiskCapacityEngine.validate(...)` decide se la chain puo partire.
   - Se la decisione fallisce, il segnale viene fermato con `REVIEW_REQUIRED`.

5. Creazione chain
   - Se il check passa, nasce una `TradeChain` in stato `WAITING_ENTRY`.
   - Vengono emessi gli eventi:
     - `SIGNAL_ACCEPTED`
     - `TRADE_CHAIN_CREATED`

6. Scheduling timeout pending
   - Se `management_plan.cancel_pending_on_timeout` e `true`, il gate valorizza `entry_timeout_at = now + pending_timeout_hours`.

7. Scelta execution mode
   - Se SL esiste e `simple_attached_enabled=true`, la chain entra in `UNIFIED_PLAN`.
   - Altrimenti usa `D_POSITION_TPSL`.

### Comandi generati automaticamente

#### Modalita `UNIFIED_PLAN`

- prima leg: `PLACE_ENTRY_WITH_ATTACHED_TPSL`
  - include SL attached
  - include il TP finale, se disponibile
- leg successive: `PLACE_ENTRY`
- i TP intermedi non vengono piazzati qui
  - saranno ricostruiti dopo il fill con `REBUILD_PARTIAL_TPS`

#### Modalita `D_POSITION_TPSL`

- per ogni leg di entrata: `PLACE_ENTRY`
- se esiste un solo TP:
  - `SET_POSITION_TPSL_FULL` in stato `WAITING_POSITION`
- se esistono piu TP:
  - una serie di `SET_POSITION_TPSL_PARTIAL` in stato `WAITING_POSITION`

### Effetto pratico

Il runtime non apre direttamente la posizione nel modello dati: crea la chain, pianifica i comandi di entry e lascia al gateway e agli eventi exchange il passaggio a `OPEN`.

## 2. Primo `ENTRY_FILLED` o fill successivi

Trigger: `LifecycleEventWorker` consuma un `ops_exchange_events.event_type="ENTRY_FILLED"` e lo passa a `LifecycleEventProcessor`.

### Automatismi applicati

1. Aggiornamento quantita e prezzo medio
   - aggiorna `filled_entry_qty`
   - aggiorna `open_position_qty`
   - ricalcola `entry_avg_price` come media pesata delle fill

2. Aggiornamento rischio consumato
   - se lo `sl_price` e disponibile nel `risk_snapshot_json`, aggiorna:
     - `risk_already_realized`
     - `risk_remaining`

3. Cambio stato iniziale
   - se la chain era `WAITING_ENTRY`, passa a `OPEN`
   - se la chain era gia aperta, il fill aggiorna quantita e media senza cambiare stato

4. Release dei comandi `WAITING_POSITION`
   - al primo fill, `workers.py` converte tutti i comandi `WAITING_POSITION` della chain in `PENDING`
   - questo sblocca i `SET_POSITION_TPSL_*`

5. Aggiornamento execution plan
   - il leg relativo viene marcato `FILLED` in `plan_state_json`
   - il matching usa prima `entry_client_order_id`
   - se serve, usa fallback sul `sequence`

### Comandi generati automaticamente

6. Ricostruzione TP intermedi
   - se il piano ha `rebuild_policy="ON_EACH_ENTRY_FILL"` e contiene `intermediate_tps`, viene emesso:
     - `REBUILD_PARTIAL_TPS`
   - il comando preserva SL e full TP gia presenti

7. Breakeven differito su race condition
   - se in precedenza un TP ha richiesto auto-cancel delle averaging legs e il fill arriva prima della conferma di cancel, il sistema puo emettere:
     - `MOVE_STOP_TO_BREAKEVEN`
   - questo accade quando l'ultima averaging leg rimasta viene fillata e il flag `_be_deferred_by_auto_cancel` viene consumato

## 3. `TP_FILLED`

Trigger: `ops_exchange_events.event_type="TP_FILLED"`.

### Automatismi applicati

1. Aggiornamento stato posizione
   - TP non finale -> `PARTIALLY_CLOSED`
   - TP finale -> `CLOSED`

2. Aggiornamento quantita
   - riduce `open_position_qty`
   - incrementa `closed_position_qty`

3. Auto-cancel averaging legs
   - se `management_plan.cancel_pending_by_engine=true`
   - e `cancel_averaging_pending_after` coincide con il TP colpito
   - e ci sono averaging legs ancora `PENDING`
   - allora il sistema emette:
     - `CANCEL_PENDING_ENTRY`
   - inoltre registra l'evento:
     - `AUTO_CANCEL_AVERAGING_REQUESTED`

4. Trigger BE automatico
   - se `management_plan.be_trigger` coincide con il TP colpito:
     - se la protezione e gia attiva -> `NOOP_ALREADY_PROTECTED_BE`
     - se esiste gia un comando BE in volo -> `NOOP_DUPLICATE_COMMAND`
     - altrimenti emette `MOVE_STOP_TO_BREAKEVEN` e mette `be_protection_status="BE_MOVE_PENDING"`

5. Interazione tra BE e auto-cancel
   - se lo stesso TP deve sia spostare il BE sia cancellare le averaging legs, il BE non parte subito
   - il runtime salva un flag `_be_deferred_by_auto_cancel` nel piano
   - il vero `MOVE_STOP_TO_BREAKEVEN` partira dopo la conferma dei cancel, oppure dopo una fill racing dell'ultima leg residua

## 4. `STOP_MOVED_CONFIRMED`

Trigger: evento exchange generato dal gateway per i comandi fire-and-forget di move stop.

### Automatismi applicati

- aggiorna `current_stop_price`
- se il comando era di tipo BE, porta `be_protection_status` a `PROTECTED`
- scrive l'evento lifecycle `STOP_MOVE_CONFIRMED`

Nota: il gateway emette subito questo evento per:

- `MOVE_STOP_TO_BREAKEVEN`
- `MOVE_STOP`
- `MOVE_POSITION_STOP`

## 5. `PENDING_ENTRY_CANCELLED_CONFIRMED`

Trigger: conferma exchange che un ordine pending di entry e stato cancellato.

### Automatismi applicati

1. Aggiornamento piano
   - il leg corrispondente viene marcato `CANCELLED` in `plan_state_json`
   - se il piano contiene placeholder anziche il vero `client_order_id`, il runtime usa il fallback su `sequence`

2. Emissione BE differito
   - se il piano porta il flag `_be_deferred_by_auto_cancel`
   - e dopo questa conferma non restano averaging legs `PENDING`
   - allora il runtime emette `MOVE_STOP_TO_BREAKEVEN`
   - poi rimuove il flag dal piano

3. Eventuale chiusura della chain senza posizione aperta
   - se `open_position_qty == 0`
   - e non restano leg pending
   - e non ci sono entry commands ancora in `SENT/ACK`
   - la chain passa a `CANCELLED`

4. Race guard
   - se ci sono ancora leg pending o entry commands in volo, il runtime non finalizza la chain
   - registra `NOOP_CANCEL_CONFIRMED_POSITION_UNRESOLVED`

## 6. `SL_FILLED`, `CLOSE_PARTIAL_FILLED`, `CLOSE_FULL_FILLED`

### `SL_FILLED`

- porta la chain a `CLOSED`
- azzera `open_position_qty`
- incrementa `closed_position_qty`

### `CLOSE_PARTIAL_FILLED`

- riduce `open_position_qty`
- incrementa `closed_position_qty`
- stato finale:
  - `PARTIALLY_CLOSED` se resta posizione
  - `CLOSED` se la quantita residua arriva a zero

### `CLOSE_FULL_FILLED`

- porta la chain a `CLOSED`
- azzera `open_position_qty`
- incrementa `closed_position_qty`
- emette anche:
  - `CANCEL_PENDING_ENTRY`
  - scopo: pulire eventuali averaging entries ancora pendenti

## 7. Timeout automatico dei pending

Trigger: `TimeoutWorker.run_once()`.

### Condizione

La worker cerca chain:

- in stato `WAITING_ENTRY`
- con `entry_timeout_at <= now`

### Automatismi applicati

1. Stato chain -> `EXPIRED`
2. Scrittura evento lifecycle:
   - `TIMEOUT_REACHED`
3. Accodamento cancel delle entry ancora pendenti:
   - uno o piu `CANCEL_PENDING_ENTRY`
   - se il runtime conosce i `client_order_id` reali, li emette in forma concreta
   - altrimenti parte da un cancel generico che verra espanso in seguito

## 8. Update Telegram supportati oggi

Gli update vengono gestiti da `LifecycleEntryGate.process_update(...)`.
Il supporto reale oggi e questo:

- `MOVE_STOP`
  - instradato al path di breakeven
  - puo emettere `MOVE_STOP_TO_BREAKEVEN`
  - protegge da duplicate request e da chain gia protette

- `CLOSE`
  - `FULL` -> `CLOSE_FULL` + `CANCEL_PENDING_ENTRY`
  - `PARTIAL` -> `CLOSE_PARTIAL`

- `CANCEL_PENDING`
  - emette `CANCEL_PENDING_ENTRY`
  - su chain non pending produce `NOOP_NOT_PENDING`

- `MODIFY_ENTRIES`
  - supporta i kind:
    - `MARKET_NOW`
    - `UPDATE_PRICE`
    - `REPLACE_ENTRY`
  - usa `ExecutionPlanDiffEngine` per confrontare il piano corrente con quello target
  - per i leg da rimuovere emette `CANCEL_PENDING_ENTRY`
  - per i leg da sostituire/emettere crea nuovi comandi entry
  - il caso `MARKET_NOW` puo anche convertire un leg in market e cancellare i successivi secondo la policy

### Eventi lifecycle prodotti dagli update accettati

- `TELEGRAM_UPDATE_ACCEPTED`
- eventuali `NOOP_*` quando l'azione non e applicabile

### Casi che non aprono ordini

Il gate manda in review se:

- il target dell'update e ambiguo
- non esiste alcuna chain compatibile
- manca lo stop loss richiesto per `MODIFY_ENTRIES`
- il piano/risk snapshot non sono coerenti
- il tipo di update non e supportato

## 9. Espansione automatica dei cancel

`cancel_expander.py` e un automatismo infrastrutturale importante.

Quando un comando `CANCEL_PENDING_ENTRY` entra nel DB:

- se contiene gia un `entry_client_order_id` reale, viene usato quello
- se contiene un placeholder tipo `place_entry:...`, il runtime prova a risolverlo verso il `tsb:...` reale
- se il cancel e generico, viene espanso in un comando concreto per ogni entry attiva `PENDING/SENT/ACK`

Effetto pratico:

- gli auto-cancel del lifecycle e i cancel manuali arrivano al gateway con gli identificativi reali dell'exchange

## 10. Comandi fire-and-forget nel gateway

Nel gateway alcuni comandi vengono marcati `DONE` subito dopo `mark_sent`, senza attesa di polling exchange:

- `CANCEL_PENDING_ENTRY`
- `MOVE_STOP_TO_BREAKEVEN`
- `MOVE_STOP`
- `MOVE_POSITION_STOP`
- `REBUILD_PARTIAL_TPS`
- `SET_POSITION_TPSL_PARTIAL`
- `SET_POSITION_TPSL_FULL`

Effetti da ricordare:

- `MOVE_STOP_*` genera subito un evento `STOP_MOVED_CONFIRMED`
- `CANCEL_PENDING_ENTRY` non genera conferma diretta: la conferma vera arriva come `PENDING_ENTRY_CANCELLED_CONFIRMED`
- `REBUILD_PARTIAL_TPS` non emette eventi lifecycle diretti

## 11. Proiezione automatica nel control plane

`outbox_writer.py` proietta eventi lifecycle in notifiche user-facing.

### Mapping attivo rilevante per gli automatismi

- `ENTRY_FILLED` -> `ENTRY_OPENED`
- `TP_FILLED` -> `TP_FILLED`
- `SL_FILLED` -> `SL_FILLED`
- `CLOSE_FULL_FILLED` -> `POSITION_CLOSED`
- `PENDING_ENTRY_CANCELLED` -> `ENTRY_CANCELLED`

### Promozioni e filtri automatici

- `TP_FILLED` con `is_final=true` viene promosso a `TP_FILLED_FINAL`
- `CLOSE_FULL_FILLED` su chain con `be_protection_status="PROTECTED"` viene promosso a `BE_EXIT`
- `ENTRY_CANCELLED` con `cancel_reason="position_closed"` viene filtrato e non mostrato all'utente

### Ritardi intenzionali nel dispatch

- `UPDATE_DONE`, `UPDATE_PARTIAL`, `UPDATE_REJECTED`: invio ritardato di 20 secondi
- `TP_FILLED` e `TP_FILLED_FINAL`: invio ritardato di 30 secondi per favorire aggregazione

## 12. Automatismi configurabili presenti nel modello ma non ancora usati qui

Nel `ManagementPlanConfig` esistono campi che non risultano attualmente agganciati a un automatismo runtime esplicito in questi moduli:

- `cancel_unfilled_pending_after`
- `risk_freed_by_be`
- parte della semantica di `protective_sl_mode`

Questo significa che:

- sono configurazioni ammesse dal modello
- ma non vanno documentate come comportamento attivo finche non compaiono davvero nel flusso lifecycle/gateway

## 13. Catalogo sintetico degli automatismi realmente attivi

| Trigger | Effetto automatico principale | Comandi/eventi emessi |
|---|---|---|
| `SIGNAL` valido | crea chain e pianifica entry | `SIGNAL_ACCEPTED`, `TRADE_CHAIN_CREATED`, `PLACE_ENTRY*`, `SET_POSITION_TPSL_*` |
| primo `ENTRY_FILLED` | chain `OPEN`, qty/media aggiornate, sblocco protezioni | `ENTRY_FILLED`, `POSITION_SIZE_UPDATED`, `ENTRY_AVG_PRICE_UPDATED`, eventuale `REBUILD_PARTIAL_TPS` |
| `TP_FILLED` non finale | partial close, possibile auto-cancel e BE | `TP_FILLED`, eventuale `CANCEL_PENDING_ENTRY`, eventuale `MOVE_STOP_TO_BREAKEVEN` |
| `TP_FILLED` finale | chain `CLOSED` | `TP_FILLED_FINAL` lato control plane |
| `STOP_MOVED_CONFIRMED` | stop attuale aggiornato, BE protetto | `STOP_MOVE_CONFIRMED` |
| `PENDING_ENTRY_CANCELLED_CONFIRMED` | leg marcato cancellato, eventuale chain `CANCELLED`, eventuale BE differito | `PENDING_ENTRY_CANCELLED`, eventuale `MOVE_STOP_TO_BREAKEVEN` |
| `SL_FILLED` | chiusura totale | `SL_FILLED` |
| `CLOSE_FULL_FILLED` | chiusura totale e cleanup pending | `CLOSE_FULL_FILLED`, `CANCEL_PENDING_ENTRY` |
| timeout pending | chain `EXPIRED` + cancel | `TIMEOUT_REACHED`, `CANCEL_PENDING_ENTRY` |
| update Telegram | modifica ordini/stop/close | `TELEGRAM_UPDATE_ACCEPTED`, eventuali `NOOP_*`, eventuali nuovi comandi |

## 14. Limite di questo documento

Questo file non descrive:

- design futuri non ancora cablati nel codice
- vecchie ipotesi del PRD non riscontrabili in `src/runtime_v2/`
- comportamenti derivati solo da nomenclatura del modello ma non usati dai worker

Se il codice cambia, questo documento va riallineato partendo da:

- `entry_gate.py`
- `event_processor.py`
- `workers.py`
- `gateway.py`
- `outbox_writer.py`

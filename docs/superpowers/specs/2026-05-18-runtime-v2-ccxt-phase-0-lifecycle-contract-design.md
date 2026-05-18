# Spec - Runtime V2 CCXT Phase 0 Lifecycle Contract

Data: 2026-05-18
Stato: approvata — decisioni di revisione integrate
Ambito: `runtime_v2` lifecycle contract, execution command semantics, ops DB contract

---

## 1. Contesto

I PRD in `docs/Raggionamento/CCXT/` definiscono una migrazione del runtime esecutivo da
Hummingbot a CCXT REST + CCXT Pro verso Bybit Demo/Live. La decisione tecnica principale e'
che CCXT deve sostituire solo il transport verso exchange, non il lifecycle engine interno.

Il codice attuale ha gia' un `runtime_v2` con:

- `LifecycleEntryGate`;
- `LifecycleEventProcessor`;
- `ops_trade_chains`;
- `ops_execution_commands`;
- `ops_exchange_events`;
- `ExecutionGateway`;
- adapter Hummingbot e fake adapter per test.

Prima di introdurre CCXT reale, il contratto interno va reso coerente. Oggi ci sono gap
concreti:

- `lifecycle_state` contiene ancora stati di protezione BE (`BE_MOVE_PENDING`,
  `PROTECTED_BE`);
- `be_protection_status` duplica parte dello stesso concetto;
- `CLOSE_FULL` e `CLOSE_PARTIAL` usano ancora role `entry` nel `client_order_id`;
- `CANCEL_PENDING_ENTRY` funziona solo con chain `WAITING_ENTRY`;
- `ops_trade_chains` non conserva le quantity runtime necessarie;
- i fill entry sovrascrivono `entry_avg_price` invece di calcolare una media ponderata;
- `event_sync` e il processor sono ancora centrati su ordine terminalmente fillato, non su
  execution fill granulari.

---

## 2. Obiettivo

Implementare la Fase 0 come refactor del contratto interno, senza CCXT reale.

La Fase 0 deve rendere testabile il modello target:

```text
LifecycleEntryGate
  -> ops_trade_chains
  -> ops_execution_commands
  -> ExecutionGateway/FakeAdapter
  -> ops_exchange_events
  -> LifecycleEventProcessor
  -> ops_trade_chains aggiornato
```

Al termine, Fase 1 potra' introdurre `CcxtBybitAdapter` senza dover correggere ancora la
semantica lifecycle/execution.

---

## 3. Non Obiettivi

- Non implementare `CcxtBybitAdapter`.
- Non usare CCXT REST.
- Non usare CCXT Pro WebSocket.
- Non validare Bybit Main Demo empiricamente.
- Non rimuovere Hummingbot dal repository.
- Non rimuovere gli adapter Hummingbot esistenti.
- Non cambiare parser, enrichment, operation rules o risk model generale.
- Non abilitare live trading.

---

## 4. Acceptance Contract

Done significa che il runtime contract locale e' coerente con i PRD CCXT Fase 0 ed e'
coperto da test automatici senza exchange reale.

Criteri osservabili:

1. `LifecycleEntryGate` produce comandi iniziali corretti per `a_sequential`,
   `b_entry_stop_then_tp` e `c_native_attached_tpsl`.
2. `ops_trade_chains` contiene e persiste le quantity runtime:
   `planned_entry_qty`, `filled_entry_qty`, `open_position_qty`, `closed_position_qty`,
   `last_position_sync_at`.
3. `ENTRY_FILLED` aggiorna quantity e `entry_avg_price` ponderato.
4. `MOVE_STOP_TO_BREAKEVEN` non cambia piu' `lifecycle_state`; aggiorna solo
   `be_protection_status`.
5. `CLOSE_PARTIAL` e `CLOSE_FULL` producono role `exit_partial` e `exit_full`.
6. `CANCEL_PENDING_ENTRY` funziona anche su chain `OPEN` con entry residue.
7. Mode C ibrida crea entry con SL attached e ultimo TP attached, poi dopo fill transiziona
   TP intermedi da `WAITING_POSITION` a `PENDING`.

Segnale primario:

```text
tests/runtime_v2/lifecycle/
tests/runtime_v2/execution_gateway/
```

devono passare sul nuovo contratto, esclusi eventuali test gated che richiedono servizi esterni.

---

## 5. State Model Target

`lifecycle_state` rappresenta solo lo stato della posizione/chain:

```text
CREATED
WAITING_ENTRY
OPEN
PARTIALLY_CLOSED
CLOSED
CANCELLED
EXPIRED
REVIEW_REQUIRED
ERROR
```

La protezione a breakeven e' ortogonale:

```text
be_protection_status:
  NOT_PROTECTED
  BE_MOVE_PENDING
  PROTECTED
```

La nuova logica non deve piu' produrre:

```text
lifecycle_state = BE_MOVE_PENDING
lifecycle_state = PROTECTED_BE
```

Questi valori possono restare tollerati solo come compatibilita' transitoria in lettura se dati
storici li contengono. Non sono stati target. `TERMINAL_STATES` non include questi valori.
Chain con stati legacy BE in `lifecycle_state` vengono skippate dal `LifecycleEventWorker`.

La distinzione tra stati terminali e' la seguente:

- `CANCELLED`: cancellazione esplicita da update Telegram (`CANCEL_PENDING_ENTRY`).
- `EXPIRED`: timeout automatico gestito esclusivamente dal `TimeoutWorker`.

---

## 6. Ops DB Contract

### 6.1 Quantity runtime (migration additiva)

Aggiungere una migration additiva agli `ops_migrations` e allineare eventuali migration runtime
equivalenti usate dal progetto:

```text
planned_entry_qty       REAL NOT NULL DEFAULT 0
filled_entry_qty        REAL NOT NULL DEFAULT 0
open_position_qty       REAL NOT NULL DEFAULT 0
closed_position_qty     REAL NOT NULL DEFAULT 0
last_position_sync_at   TEXT NULL
```

Semantica:

- `planned_entry_qty`: size teorica iniziale calcolata dal risk sizing.
- `filled_entry_qty`: quantita' cumulata realmente entrata.
- `open_position_qty`: quantita' netta attualmente aperta.
- `closed_position_qty`: quantita' cumulata uscita tramite TP, SL o close manuali.
- `last_position_sync_at`: ultimo allineamento affidabile con fonte exchange/reconciliation.

Repository, model Pydantic e insert/update path devono leggere e scrivere questi campi senza
richiedere dati exchange reali. Per righe legacy, i valori di default zero sono trattati come
quantity ignota.

### 6.2 Execution mode (migration additiva)

Aggiungere alla stessa migration additiva:

```text
execution_mode          TEXT NOT NULL DEFAULT 'a_sequential'
```

Semantica:

- `execution_mode` e' una capability dell'adapter, dichiarata in `ExecutionConfig` per account.
- Il valore viene scritto da `LifecycleEntryGate` alla creazione della chain e non cambia per
  tutta la durata del ciclo di vita.
- `LifecycleEntryGate` lo legge per strutturare i comandi iniziali (status `PENDING` vs
  `WAITING_POSITION`).
- `LifecycleEventProcessor` lo legge per determinare il comportamento post-fill (in particolare
  Mode C).
- Valori validi: `a_sequential`, `b_entry_stop_then_tp`, `c_native_attached_tpsl`.

---

## 7. Order Modes

`execution_mode` e' parte vincolante del contratto e viene sourciato da `ExecutionConfig`
(config globale per account/adapter). Il valore e' persistito su `ops_trade_chains`.

### 7.1 Mode A - `a_sequential`

Comandi iniziali:

```text
PLACE_ENTRY                 -> PENDING
PLACE_PROTECTIVE_STOP       -> WAITING_POSITION
PLACE_TAKE_PROFIT x N       -> WAITING_POSITION
```

Dopo il primo `ENTRY_FILLED`, la chain passa a `OPEN` e il command worker rilascia i comandi in
`WAITING_POSITION`.

### 7.2 Mode B - `b_entry_stop_then_tp`

Comandi iniziali:

```text
PLACE_ENTRY                 -> PENDING
PLACE_PROTECTIVE_STOP       -> PENDING
PLACE_TAKE_PROFIT x N       -> WAITING_POSITION
```

Dopo il primo `ENTRY_FILLED`, la chain passa a `OPEN` e il command worker rilascia i TP.

### 7.3 Mode C - `c_native_attached_tpsl`

Mode C e' ibrida. Richiede che l'adapter supporti `native_attached_tpsl` (capability dichiarata
in `ExecutionConfig`).

Comandi iniziali:

```text
PLACE_ENTRY                              -> PENDING
PLACE_TAKE_PROFIT TP1..TP(n-1)          -> WAITING_POSITION  (reduce_only=true)
```

Il payload entry contiene:

```text
native_attached_tpsl = true
attached_stop_loss = <stop loss del segnale>
attached_take_profit = <prezzo ultimo TP del segnale>
attached_take_profit_sequence = <sequence ultimo TP>
```

Le quantita' dei TP intermedi sono calcolate alla creazione della chain con la stessa logica
di Mode A/B: `qty = total_qty * close_pcts[i] / 100` dove `close_pcts` viene da
`management_plan.close_distribution.table[tp_count]` e `total_qty` da `risk_snapshot_json`.

Se il segnale ha un solo TP, non vengono creati TP separati — nessun comando `WAITING_POSITION`.

Dopo `ENTRY_FILLED`, `LifecycleEventProcessor` transiziona i comandi `WAITING_POSITION` a
`PENDING` (identico al comportamento di Mode A/B). Non genera nuovi comandi.

Mode C in Fase 0 e' validata solo con fake adapter e test di contratto. La compatibilita' reale
con Bybit e' rimandata alla Fase 1/Fase 2.

**Open Decision OD-C1 (Fase 1):** il payload Mode C deve includere `attached_take_profit_qty`
esplicita per evitare conflitto di sizing tra il TP attached Bybit (che copre di default l'intera
posizione) e i TP intermedi `reduce_only`. Da validare empiricamente su Bybit Demo.

---

## 8. Command And Event Contract

### 8.1 Command types

Aggiungere il command type:

```text
SYNC_PROTECTIVE_ORDERS
```

### 8.2 Exchange events

Prodotti da `ExchangeEventSyncWorker`, persistiti in `ops_exchange_events`, fonte: adapter/fake.

```text
ENTRY_FILLED
TP_FILLED
SL_FILLED
CLOSE_PARTIAL_FILLED
CLOSE_FULL_FILLED
STOP_MOVED_CONFIRMED
PENDING_ENTRY_CANCELLED_CONFIRMED
PROTECTIVE_ORDERS_SYNCED
ORDER_REJECTED
ORDER_CANCELLED
```

Il `FakeAdapter` genera `PROTECTIVE_ORDERS_SYNCED` automaticamente in risposta a
`SYNC_PROTECTIVE_ORDERS`, rendendo il loop testabile end-to-end in Fase 0.

### 8.3 Lifecycle events

Prodotti da `LifecycleEventProcessor` e gate, persistiti in `ops_lifecycle_events`.

```text
POSITION_SIZE_UPDATED
ENTRY_AVG_PRICE_UPDATED
PROTECTIVE_SYNC_REQUESTED
STOP_MOVE_CONFIRMED
PENDING_ENTRY_CANCELLED
```

Questi eventi devono essere disponibili nel modello e nei test anche se alcuni saranno prodotti
da adapter/reconciliation solo nelle fasi successive.

---

## 9. Client Order ID Roles

Estendere i role validi da:

```text
entry | sl | tp
```

a:

```text
entry | sl | tp | exit_partial | exit_full | sync
```

Mapping target:

```text
PLACE_ENTRY                -> entry
PLACE_PROTECTIVE_STOP      -> sl
PLACE_TAKE_PROFIT          -> tp
MOVE_STOP_TO_BREAKEVEN     -> sl
MOVE_STOP                  -> sl
CANCEL_PENDING_ENTRY       -> entry
CLOSE_PARTIAL              -> exit_partial
CLOSE_FULL                 -> exit_full
SYNC_PROTECTIVE_ORDERS     -> sync
```

`CLOSE_PARTIAL` e `CLOSE_FULL` non devono piu' essere normalizzati come `entry`.

---

## 10. Lifecycle Processing

### 10.1 Entry Fill

`ENTRY_FILLED` deve essere execution-fill based. Payload minimo:

```json
{
  "client_order_id": "tsb:15:101:entry:1",
  "exchange_order_id": "abc",
  "exchange_trade_id": "trade-xyz",
  "role": "entry",
  "sequence": 1,
  "fill_qty": 0.01,
  "fill_price": 67350.5,
  "order_fully_filled": false,
  "position_qty_after": 0.01,
  "event_source": "fake_or_future_ccxt"
}
```

Processing:

- incrementa `filled_entry_qty`;
- incrementa `open_position_qty`;
- calcola `entry_avg_price` ponderato;
- porta `WAITING_ENTRY -> OPEN` solo al primo fill;
- lascia `OPEN` invariato sui fill entry successivi;
- transiziona comandi `WAITING_POSITION` a `PENDING` in base alla mode;
- genera lifecycle events `POSITION_SIZE_UPDATED` e `ENTRY_AVG_PRICE_UPDATED`.

Formula media ponderata:

```text
new_avg = ((old_avg * old_filled_qty) + (fill_price * fill_qty))
          / (old_filled_qty + fill_qty)
```

Se `old_filled_qty` e' zero, `new_avg = fill_price`.

### 10.2 TP/SL/Close Fill

`TP_FILLED`, `SL_FILLED`, `CLOSE_PARTIAL_FILLED` e `CLOSE_FULL_FILLED`:

- riducono `open_position_qty`;
- aumentano `closed_position_qty`;
- portano a `PARTIALLY_CLOSED` se resta qty aperta;
- portano a `CLOSED` se la qty aperta arriva a zero;
- generano `SYNC_PROTECTIVE_ORDERS` se resta posizione aperta e gli ordini protettivi possono
  richiedere resize.

### 10.3 Stop Move

`MOVE_STOP_TO_BREAKEVEN` richiesto:

- crea command `MOVE_STOP_TO_BREAKEVEN`;
- imposta `be_protection_status = BE_MOVE_PENDING`;
- non cambia `lifecycle_state`.

`STOP_MOVED_CONFIRMED`:

- aggiorna `current_stop_price`;
- se riferito a BE, imposta `be_protection_status = PROTECTED`;
- mantiene `lifecycle_state` su `OPEN` o `PARTIALLY_CLOSED`.

### 10.4 Cancel Pending Entry

`CANCEL_PENDING_ENTRY` non dipende solo dallo stato chain.

Regola:

- se la chain e' `WAITING_ENTRY` e non c'e' posizione aperta: cancella entry pendenti e la
  chain diventa `CANCELLED` (cancellazione esplicita da update Telegram). `EXPIRED` e'
  riservato esclusivamente al `TimeoutWorker`.
- se la chain e' `OPEN` o `PARTIALLY_CLOSED`: cancella solo entry residue ancora attive,
  mantiene lo stato posizione, emette `SYNC_PROTECTIVE_ORDERS`. I comandi `WAITING_POSITION`
  orfani non vengono toccati — la riconciliazione e' responsabilita' del sync worker (Fase 1).
- se non esistono entry cancellabili: produce un NOOP o review secondo evidenza disponibile.

`PENDING_ENTRY_CANCELLED_CONFIRMED` deve avere payload con:

```json
{
  "cancelled_order_ids": ["..."],
  "cancelled_pending_qty": 0.02,
  "position_already_open": true
}
```

---

## 11. Protective Sync Contract

La Fase 0 introduce `SYNC_PROTECTIVE_ORDERS` come comando e come evento di richiesta, senza
implementare ancora una strategia exchange reale.

Trigger minimi:

- entry fill aggiuntivo dopo posizione gia' aperta;
- TP fill non finale;
- `CLOSE_PARTIAL_FILLED`;
- SL fill parziale;
- `CANCEL_PENDING_ENTRY` su chain `OPEN` con entry residue;
- mismatch quantity esplicito emerso da evento fake/test.

Il `FakeAdapter` risponde `PROTECTIVE_ORDERS_SYNCED` automaticamente, rendendo il loop
end-to-end testabile in Fase 0. La responsabilita' completa del sync su ordini reali resta per
Fase 1/Fase 4.

---

## 12. Testing Strategy

La Fase 0 e' TDD-first.

Test da aggiungere o aggiornare:

- `LifecycleEntryGate` produce status iniziali corretti per A/B/C.
- Mode C produce payload entry con `native_attached_tpsl`, SL attached e ultimo TP attached.
- Mode C multi-TP pre-crea TP1..TP(n-1) come `WAITING_POSITION` alla creazione della chain.
- `ENTRY_FILLED` transiziona comandi `WAITING_POSITION` a `PENDING` per tutte le mode.
- Migration additiva crea i nuovi campi con default corretti.
- Repository e model round-trip preservano le quantity runtime e `execution_mode`.
- `ENTRY_FILLED` aggiorna `filled_entry_qty`, `open_position_qty` e media ponderata.
- Fill entry successivi mantengono `lifecycle_state = OPEN`.
- `TP_FILLED` e `CLOSE_PARTIAL_FILLED` riducono qty e richiedono sync protettivo.
- `CLOSE_FULL_FILLED` porta `open_position_qty = 0` e `lifecycle_state = CLOSED`.
- `MOVE_STOP_TO_BREAKEVEN` aggiorna solo `be_protection_status`.
- `STOP_MOVED_CONFIRMED` imposta `be_protection_status = PROTECTED` e `current_stop_price`.
- `CLOSE_PARTIAL` usa role `exit_partial`.
- `CLOSE_FULL` usa role `exit_full`.
- `CANCEL_PENDING_ENTRY` su `WAITING_ENTRY` porta a `CANCELLED`.
- `CANCEL_PENDING_ENTRY` su `OPEN` emette `SYNC_PROTECTIVE_ORDERS`, mantiene stato posizione.
- `FakeAdapter` risponde `PROTECTIVE_ORDERS_SYNCED` su `SYNC_PROTECTIVE_ORDERS`.

Validation command target:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle tests\runtime_v2\execution_gateway -q
```

I test gated verso Hummingbot/Bybit devono restare esclusi o skipped se richiedono servizi esterni.

---

## 13. Documentation Impact

Aggiornare documentazione durable solo se l'implementazione cambia effettivamente:

- `docs/Raggionamento/CCXT/*` resta contesto PRD, non fonte runtime diretta.
- `docs/runtime_v2/*` va aggiornato se descrive stati lifecycle, command types o schema ops.
- `README.md` non richiede update per Fase 0 se non cambia setup o comando utente.

---

## 14. Migration And Rollout

La migration e' additiva e compatibile con dati esistenti.

Rollout locale:

1. applicare migration ops additiva (quantity runtime + `execution_mode`);
2. aggiornare model/repository;
3. aggiornare gate/processor/gateway contract;
4. aggiornare test;
5. eseguire suite runtime_v2 mirata.

Rollback logico:

- i nuovi campi hanno default sicuri (`NOT NULL DEFAULT 0` per quantity, `DEFAULT 'a_sequential'`
  per execution_mode);
- vecchi record senza quantity runtime vengono trattati come zero;
- stati legacy BE in `lifecycle_state` non sono prodotti dalla nuova logica, ma vengono tollerati
  in lettura e skippati dal `LifecycleEventWorker`.

---

## 15. Open Decisions

### Chiuse da questa spec

- Fase 0 e' solo contratto interno, senza CCXT reale.
- La migration quantity runtime e' inclusa.
- Le tre mode A/B/C sono tutte incluse.
- `execution_mode` e' colonna dedicata in `ops_trade_chains`, sourciata da `ExecutionConfig`.
- Mode C usa SL attached e ultimo TP attached; TP intermedi pre-creati come `WAITING_POSITION`.
- In Mode C multi-TP, dopo fill il processor transiziona `WAITING_POSITION -> PENDING` senza
  generare nuovi comandi.
- Il role `entry` non viene piu' usato per close manuali.
- BE e' separato dallo stato posizione.
- `CANCEL_PENDING_ENTRY` → sempre `CANCELLED`. `EXPIRED` riservato a `TimeoutWorker`.
- `CANCEL_PENDING_ENTRY` su chain `OPEN` emette `SYNC_PROTECTIVE_ORDERS`, non tocca comandi
  `WAITING_POSITION` orfani.
- Exchange events e lifecycle events sono distinti per tabella e produttore.
- `FakeAdapter` risponde `PROTECTIVE_ORDERS_SYNCED` automaticamente in Fase 0.

### Aperte (rinviate a Fase 1)

**OD-C1:** il payload Mode C deve includere `attached_take_profit_qty` esplicita per evitare
conflitto di sizing tra TP attached Bybit (copre di default l'intera posizione) e TP intermedi
`reduce_only`. Da validare empiricamente su Bybit Demo.

---

## 16. Handoff To Planning

La prossima fase dopo review utente e' un piano implementativo TDD-first con task ordinati:

1. migration e model/repository quantity runtime + `execution_mode`;
2. enum/contract command-event-role (nuovi tipi, split exchange/lifecycle events);
3. order mode A/B/C nel gate (comandi iniziali + `WAITING_POSITION`);
4. event processor quantity + weighted average + transizione `WAITING_POSITION`;
5. BE state separation;
6. cancel pending su chain aperta + `SYNC_PROTECTIVE_ORDERS`;
7. gateway/client order id role mapping (`exit_partial`, `exit_full`, `sync`);
8. fake adapter `PROTECTIVE_ORDERS_SYNCED`;
9. regression suite runtime_v2.

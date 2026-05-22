# PRD - Fix routing BE move su Bybit per protezioni attached/full

## Obiettivo

Correggere il comportamento di `MOVE_STOP_TO_BREAKEVEN` nel runtime V2 su Bybit quando la protezione iniziale della posizione non e' un ordine SL aperto standalone, ma nasce da:

- TP/SL attached all'entry
- TP/SL position-level

Il sistema deve usare il meccanismo corretto di modifica SL su Bybit, evitando i fallimenti `retCode=10001` osservati in produzione demo.

---

## Problema osservato

Nei dati runtime attuali esistono almeno due casi confermati:

- `BTCUSDT`, chain `2`, `execution_mode = C_MULTI_TP`
- `XRPUSDT`, chain `5`, `execution_mode = C_MULTI_TP`

In entrambi i casi:

- la chain e' stata aperta con `PLACE_ENTRY_WITH_ATTACHED_TPSL`
- il comando `MOVE_STOP_TO_BREAKEVEN` e' stato effettivamente generato
- il comando e' fallito con:
- `bybit retCode=10001, retMsg="Request parameter error."`

Quindi il problema non e' isolato a un singolo trade o simbolo.

---

## Root cause reale

### Routing attuale sbagliato

Oggi il codice traduce sempre:

- `MOVE_STOP_TO_BREAKEVEN`
- `MOVE_STOP`

nel path:

- `action = "edit_sl"`

Questo path assume che esista uno stop order aperto, recuperabile via:

- `fetch_open_orders(...)`

e modificabile via:

- `edit_order(..., params={"triggerPrice": ...})`

### Perche' fallisce

Nei flow `C_MULTI_TP` la protezione iniziale nasce da:

- `PLACE_ENTRY_WITH_ATTACHED_TPSL`

con:

- `attached_tpsl.mode = "FULL"`

In questo scenario, la protezione SL/TP su Bybit non va trattata come un normale ordine SL aperto da emendare via `edit_order`.

Per questi casi, il meccanismo corretto e':

- `Set Trading Stop`
- path adapter `trading_stop_move_sl`

Il bug quindi non e' nel calcolo del prezzo BE, ma nel meccanismo di aggiornamento usato per inviarlo all'exchange.

---

## Ambito d'impatto

### Casi sicuramente colpiti

Il bug colpisce con alta probabilita' tutti i flow che soddisfano queste condizioni:

- adapter = Bybit
- comando emesso = `MOVE_STOP_TO_BREAKEVEN`
- la posizione e' nata da `PLACE_ENTRY_WITH_ATTACHED_TPSL`
- la protezione attiva e' attached/full oppure position-level, non uno stop order standalone aperto

Confermati dai dati:

- `C_MULTI_TP` con entry attached/full

### Casi potenzialmente non colpiti

Il path `edit_sl` puo' ancora essere corretto se il sistema usa davvero:

- un ordine SL separato, aperto, visibile in `fetch_open_orders`

Quindi il bug non va trattato come "rompe ogni move stop su Bybit", ma come:

- routing unico troppo grezzo
- mancanza di distinzione tra SL standalone e SL attached/position-level

---

## Comportamento target

Il runtime deve distinguere tra due famiglie di protezione:

### Caso A - SL standalone order

Se la protezione e' un vero ordine SL aperto:

- `MOVE_STOP_TO_BREAKEVEN` puo' continuare a usare `edit_sl`

### Caso B - SL attached/full o position-level

Se la protezione e' nata da:

- `PLACE_ENTRY_WITH_ATTACHED_TPSL`
- oppure da `SET_POSITION_TPSL_FULL`
- oppure da `SET_POSITION_TPSL_PARTIAL` con SL position-level

allora `MOVE_STOP_TO_BREAKEVEN` deve usare:

- `trading_stop_move_sl`

e non `edit_sl`.

---

## Decisione tecnica raccomandata

Per Bybit, il sistema deve smettere di trattare `MOVE_STOP_TO_BREAKEVEN` come un'operazione unica e cieca.

La regola raccomandata e':

- `MOVE_STOP_TO_BREAKEVEN` usa `trading_stop_move_sl` quando la chain usa protezioni attached/full o position-level
- `MOVE_STOP_TO_BREAKEVEN` usa `edit_sl` solo nei flow legacy con SL standalone aperto

---

## Scelta implementativa consigliata

### Opzione raccomandata

Introdurre routing consapevole del tipo di protezione.

Possibili segnali per decidere:

- `execution_mode` della chain
- origine del protettivo iniziale
- presenza di flow `PLACE_ENTRY_WITH_ATTACHED_TPSL`
- metadata/payload del comando o della chain che indichino `protection_style`

### Opzione minima pragmatica

Per i flow già noti come colpiti:

- `C_MULTI_TP`
- altri flow attached/full equivalenti

tradurre direttamente `MOVE_STOP_TO_BREAKEVEN` in:

- `trading_stop_move_sl`

senza passare da `edit_sl`

Questa e' la fix minima consigliata per fermare il bug reale osservato.

---

## Comportamento target dettagliato

Per una chain Bybit con protezione attached/full:

1. la posizione viene aperta con TP/SL attached o con protezione position-level
2. arriva update manuale `stoppa in BE` oppure trigger automatico da TP
3. il lifecycle genera `MOVE_STOP_TO_BREAKEVEN`
4. il builder deve produrre un'azione `trading_stop_move_sl`
5. l'adapter deve chiamare:
   - `private_post_v5_position_trading_stop`
6. il payload deve contenere il nuovo `stopLoss`
7. al successo deve arrivare la conferma runtime e la chain deve uscire da `BE_MOVE_PENDING`

---

## File coinvolti

### 1. `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py`

Modifica richiesta:

- rivedere il routing di `MOVE_STOP_TO_BREAKEVEN`
- non mandarlo sempre a `edit_sl`
- supportare la scelta `trading_stop_move_sl` nei flow attached/full

### 2. `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`

Modifica richiesta:

- mantenere `edit_sl` solo per i casi con vero stop order aperto
- usare `private_post_v5_position_trading_stop` per i BE move su protezioni attached/full

### 3. `src/runtime_v2/execution_gateway/gateway.py`

Possibile modifica:

- se necessario, passare abbastanza contesto al builder per distinguere il tipo di protezione

### 4. Lifecycle / metadata chain

Possibile modifica:

- rendere esplicito nel payload o nella chain il tipo di protezione attiva
- esempio: `protection_style = standalone_order | attached_full | position_level`

---

## Requisito di calcolo del BE

Il calcolo del nuovo prezzo stop non cambia.

Regola:

- `LONG`: `entry_avg_price * (1 + be_buffer_pct)`
- `SHORT`: `entry_avg_price * (1 - be_buffer_pct)`

Il bug non e' il valore calcolato.
Il bug e' l'API exchange usata per applicarlo.

---

## Contratto funzionale target

### Contratto 1

Dato un trade Bybit con entry `PLACE_ENTRY_WITH_ATTACHED_TPSL`,
quando viene emesso `MOVE_STOP_TO_BREAKEVEN`,
il comando non deve usare `edit_order`.

### Contratto 2

Dato un trade Bybit con protezione attached/full,
il comando deve usare `trading_stop_move_sl`.

### Contratto 3

Dato un flow legacy con SL standalone aperto,
il comando puo' continuare a usare `edit_sl`.

### Contratto 4

Un BE move riuscito deve produrre:

- conferma exchange
- evento `STOP_MOVE_CONFIRMED`
- aggiornamento `current_stop_price`
- aggiornamento `be_protection_status` verso `PROTECTED`

---

## Criteri di accettazione

La modifica e' accettata se:

1. i casi oggi falliti su `BTCUSDT` e `XRPUSDT` non restituiscono piu' `retCode=10001`
2. `MOVE_STOP_TO_BREAKEVEN` su flow attached/full usa `trading_stop_move_sl`
3. i flow legacy con SL standalone non regrediscono
4. i test distinguono esplicitamente i due path:
   - `edit_sl`
   - `trading_stop_move_sl`
5. il runtime produce `STOP_MOVE_CONFIRMED` nei casi di successo

---

## Test da aggiungere o aggiornare

### Builder

- test che `MOVE_STOP_TO_BREAKEVEN` su flow attached/full produca `trading_stop_move_sl`
- test che `MOVE_STOP_TO_BREAKEVEN` su flow legacy produca ancora `edit_sl`

### Adapter

- test che il ramo attached/full chiami `private_post_v5_position_trading_stop`
- test che il ramo legacy continui a usare `edit_order`

### Runtime integration

- riprodurre il caso `BTCUSDT`
- riprodurre il caso `XRPUSDT`
- verificare uscita da `BE_MOVE_PENDING`

---

## Non obiettivi

Questa modifica non deve:

- cambiare il parser
- cambiare il calcolo del BE
- cambiare i trigger `be_trigger`
- ridefinire i mode C/D

Deve solo correggere il meccanismo con cui il nuovo stop viene applicato a Bybit.

---

## Decisione finale raccomandata

Il bug non e' un'anomalia di singolo trade.
E' un errore di integrazione adapter-level:

- `MOVE_STOP_TO_BREAKEVEN` usa sempre `edit_sl`
- ma Bybit richiede `trading_stop` nei flow attached/full

La soluzione corretta e' introdurre routing esplicito tra:

- `edit_sl` per SL standalone
- `trading_stop_move_sl` per protezioni attached/full o position-level

Questa e' la modifica minima coerente con i dati runtime osservati e con il modello reale di Bybit.


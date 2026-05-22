# PRD - D_MULTI_ENTRY_MULTI_TP con ultimo TP attached per leg

## Obiettivo

Modificare il comportamento del runtime V2 per i segnali con:

- 2+ entry
- 2+ take profit
- stop loss presente

in modo che ogni leg di entry venga piazzata con:

- SL exchange-native attached
- ultimo TP attached alla singola leg

e che i TP intermedi vengano creati o aggiornati dopo ogni fill sulla size realmente fillata.

---

## Problema osservato

Nel comportamento attuale, il routing `D_MULTI_ENTRY_MULTI_TP` genera per ogni leg:

- `PLACE_ENTRY_WITH_ATTACHED_TPSL`
- `attached_tpsl.mode = "SL_ONLY"`

Questo ha due problemi:

1. Protezione incompleta al placement:
   - ogni leg ha SL attached ma non ha un TP finale attached
   - se il bot cade dopo il fill e prima del rebuild dei TP intermedi, la posizione non ha target finale exchange-native

2. Incompatibilita' concreta con Bybit nel path attuale:
   - il builder invia `slOrderType` senza `tpslMode`
   - Bybit rifiuta il payload con errore:
   - `slOrderType can not have a value when tpSlMode is empty`

---

## Regola di business target

Per `entry_count > 1` e `tp_count > 1`, il sistema deve trattare ogni leg come una entry protetta autonomamente:

- leg market: order-level `SL + ultimo TP`
- leg limit: order-level `SL + ultimo TP`
- dopo ogni fill: aggiunta o riallineamento dei TP intermedi sulla qty totale fillata

Il TP finale attached rappresenta la protezione finale minima exchange-native della leg.
I TP intermedi restano dinamici e vengono gestiti dal bot a livello posizione.

---

## Comportamento target dettagliato

### Caso canonico: 2 entry + 2 TP

Input logico:

- Entry 1: MARKET
- Entry 2: LIMIT
- TP1: intermedio
- TP2: finale
- SL: comune

Output desiderato:

1. `PLACE_ENTRY_WITH_ATTACHED_TPSL` per Entry 1
   - `mode = "PARTIAL_TP"`
   - `stop_loss = SL`
   - `take_profit = TP2`
   - `tp_qty = qty_leg1`

2. `PLACE_ENTRY_WITH_ATTACHED_TPSL` per Entry 2
   - `mode = "PARTIAL_TP"`
   - `stop_loss = SL`
   - `take_profit = TP2`
   - `tp_qty = qty_leg2`

3. Dopo il fill di Entry 1
   - chain passa a `OPEN`
   - `filled_entry_qty = qty_leg1`
   - il sistema emette `SET_POSITION_TPSL_PARTIAL` solo per `TP1`
   - qty di `TP1` calcolata sulla size totale fillata al momento

4. Dopo il fill di Entry 2
   - `filled_entry_qty = qty_leg1 + qty_leg2`
   - il sistema cancella/supersede i vecchi partial TP
   - riemette `SET_POSITION_TPSL_PARTIAL` solo per `TP1`
   - qty di `TP1` aggiornata sulla nuova size totale fillata

5. Dopo fill di `TP1`
   - il sistema riallinea le protezioni residue con `SYNC_PROTECTIVE_ORDERS`
   - il TP finale attached resta la protezione finale residua exchange-native

---

## Principi architetturali

### 1. Exchange-first per SL e target finale

Il sistema deve garantire che ogni leg fillata abbia immediatamente:

- uno stop loss nativo exchange
- un target finale minimo nativo exchange

senza dipendere da un worker post-fill per la sopravvivenza di base della posizione.

### 2. Bot-driven per i TP intermedi

I TP intermedi non devono essere attached alla entry.
Devono essere costruiti a livello posizione dopo ogni fill, perche':

- dipendono dalla size realmente fillata
- devono essere ricalcolati quando nuove leg entrano
- non devono essere pre-calcolati sulla posizione pianificata totale

### 3. Nessuna duplicazione del TP finale

Se il TP finale e' gia' attached a ogni leg, il rebuild post-fill non deve ricreare anche l'ultimo TP come partial TP position-level.

Il rebuild post-fill deve creare solo i TP intermedi.

---

## Differenza rispetto al comportamento attuale

### Attuale

`D_MULTI_ENTRY_MULTI_TP`:

- entry per leg con `SL_ONLY`
- nessun TP attached al placement
- TP costruiti dopo i fill sulla qty cumulata

### Nuovo target

`D_MULTI_ENTRY_MULTI_TP`:

- entry per leg con `PARTIAL_TP`
- `SL + ultimo TP` attached su ogni leg
- TP intermedi costruiti dopo i fill sulla qty cumulata
- rebuild post-fill limitato ai soli TP intermedi

---

## Decisione di prodotto

Per `D_MULTI_ENTRY_MULTI_TP`, adottare questa regola:

- ogni leg usa `attached_tpsl.mode = "PARTIAL_TP"`
- `take_profit` attached = ultimo livello TP
- `tp_qty` attached = qty della singola leg
- i livelli TP precedenti all'ultimo vengono gestiti solo via `SET_POSITION_TPSL_PARTIAL`

Questa e' la decisione raccomandata e sostituisce la regola attuale `SL_ONLY per leg`.

---

## File coinvolti

### 1. `src/runtime_v2/lifecycle/entry_gate.py`

Modifica richiesta:

- in `_build_d_multi_entry_multi_tp_commands`
- sostituire `tpsl_mode="SL_ONLY"` con `tpsl_mode="PARTIAL_TP"`
- usare l'ultimo TP del segnale come `tp_price`
- usare la qty della singola leg come `tp_qty`

Regola per qty:

- leg non deferred: `tp_qty = qty_leg`
- leg deferred market: passare un'informazione equivalente che consenta al gateway di risolvere la qty attached della singola leg al submit

### 2. `src/runtime_v2/lifecycle/event_processor.py`

Modifica richiesta:

- `_build_tp_partial_commands_after_fill` non deve iterare su tutti i livelli TP
- deve iterare solo sui livelli intermedi
- l'ultimo TP va escluso dal rebuild, perche' gia' attached alle entry

Con `2 TP`:

- dopo i fill si crea solo `TP1`
- `TP2` non va ricreato

Con `3 TP`:

- dopo i fill si creano `TP1` e `TP2`
- `TP3` resta solo attached

### 3. `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py`

Modifica richiesta:

- mantenere il ramo `PARTIAL_TP` come path principale per questo caso
- verificare che il payload Bybit inviato sia:
  - `takeProfit`
  - `stopLoss`
  - `tpslMode = Partial`
  - `tpOrderType`
  - `tpTriggerBy`
  - `tpSize`

Il ramo `SL_ONLY` non deve piu' essere usato per `D_MULTI_ENTRY_MULTI_TP`.

### 4. Test lifecycle

Aggiornare i test che oggi verificano:

- `D_MULTI_ENTRY_MULTI_TP -> SL_ONLY`

in:

- `D_MULTI_ENTRY_MULTI_TP -> PARTIAL_TP con ultimo TP attached`

### 5. Test builder Bybit

Confermare che `PARTIAL_TP` produca il payload corretto per Bybit.

---

## Contratto funzionale target

### Contratto 1 - placement iniziale

Dato un segnale `2 entry + 2 TP + SL`:

- devono essere creati 2 comandi `PLACE_ENTRY_WITH_ATTACHED_TPSL`
- ogni comando deve avere `SL + ultimo TP`
- nessun comando entry deve essere `SL_ONLY`

### Contratto 2 - primo fill

Dopo il primo `ENTRY_FILLED`:

- la chain deve andare in `OPEN`
- `filled_entry_qty` deve riflettere il fill reale
- devono essere emessi solo i TP intermedi
- il TP finale non deve essere ricreato

### Contratto 3 - secondo fill

Dopo il secondo `ENTRY_FILLED`:

- i TP intermedi precedenti devono essere superseded/cancelled
- i nuovi TP intermedi devono essere ricalcolati sulla size totale fillata
- il TP finale attached deve restare l'unica protezione finale native

### Contratto 4 - resilienza bot down

Se il bot cade:

- dopo il placement ma prima del fill: le entry hanno gia' SL + TP finale attached
- dopo il fill ma prima del rebuild intermedi: la posizione ha comunque SL + TP finale exchange-native

---

## Criteri di accettazione

La modifica e' accettata se:

1. un segnale `2 entry + 2 TP + SL` genera per ogni leg un payload `PARTIAL_TP`, non `SL_ONLY`
2. Bybit non rifiuta piu' il placement iniziale per assenza di `tpslMode`
3. al primo fill vengono creati solo i TP intermedi
4. al secondo fill i TP intermedi vengono ricalcolati sulla nuova size totale
5. il TP finale non viene duplicato tra attached order-level e partial position-level
6. la protezione minima exchange-native in ogni momento e':
   - SL
   - target finale

---

## Rischi da controllare

### 1. Sovrascrittura del TP finale

Bybit potrebbe trattare il TP attached e il TP position-level come configurazioni concorrenti se il codice ricrea anche l'ultimo TP nel rebuild.

Contromisura:

- escludere sempre l'ultimo TP dal rebuild post-fill

### 2. Gestione qty per leg deferred market

Per leg `MARKET` con qty deferred, il sistema deve essere in grado di risolvere correttamente anche il `tp_qty` attached al submit.

Contromisura:

- usare il medesimo dato di qty risolta al submit anche per `tpSize`

### 3. Rebuild multipli su fill successivi

Più fill ravvicinati possono generare più `SET_POSITION_TPSL_PARTIAL` superseding.

Contromisura:

- mantenere l'idempotenza e la logica `supersedes_previous`

---

## Non obiettivi

Questa modifica non deve:

- cambiare il comportamento di `C_SIMPLE_ATTACHED`
- cambiare il comportamento di `C_MULTI_TP`
- cambiare il comportamento di `D_MULTI_ENTRY_1TP`
- introdurre nuove dipendenze
- cambiare i contratti parser/enrichment

---

## Decisione finale raccomandata

Per `D_MULTI_ENTRY_MULTI_TP`, il comportamento corretto da implementare e':

- `SL + ultimo TP attached` per ogni leg
- `TP intermedi post-fill` sulla qty realmente fillata
- `ultimo TP escluso dal rebuild`

Questa scelta allinea:

- la protezione exchange-native
- la logica incrementale multi-fill
- la semantica attesa per `market + limit averaging`

e rimuove la debolezza dell'attuale strategia `SL_ONLY`.


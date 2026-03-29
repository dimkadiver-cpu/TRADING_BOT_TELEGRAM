# PRD - Fase 6: Order Manager Exchange-Backed

> **Stato:** BOZZA - da approvare prima di implementazione
> **Prerequisiti:** Fase 5 funzionante in dry-run/live per entry bridge via freqtrade
> **Riferimento:** `docs/PRD_FASE_5.md`
> **Sequenza operativa:** `docs/FASE_6_PROMPTS.md`

---

## Obiettivo

Fase 6 introduce un **order manager exchange-backed** per gestire gli ordini protettivi reali:

- stop loss
- take profit ladder
- aggiornamenti sugli ordini gia aperti
- riconciliazione al riavvio

L'obiettivo non e piu limitarsi a una strategy freqtrade che "decide" quando uscire.
Quando un trade e attivo, il sistema deve essere in grado di avere sul mercato ordini reali
coerenti con il segnale e con gli eventuali `UPDATE`.

In sintesi:

1. `NEW_SIGNAL` -> entry gestita dal bridge attuale
2. entry fillata -> il sistema piazza subito `SL + TP ladder` reali su exchange
3. `UPDATE` -> il sistema modifica o cancella gli ordini gia presenti
4. restart / disallineamento -> il sistema riconcilia stato DB <-> exchange

---

## Perche questa fase esiste

La Fase 5 ha portato il bridge via freqtrade fino a:

- entry reali/dry-run
- stoploss via meccanismo freqtrade
- TP gestiti dalla strategy al raggiungimento del prezzo
- callback verso il DB del bot

Questo approccio e sufficiente per un bridge iniziale, ma non soddisfa il requisito operativo:

- gli ordini di uscita non sono tutti visibili subito sul mercato
- gli `UPDATE` non operano su ordini exchange gia esistenti
- al riavvio non esiste una riconciliazione forte degli ordini protettivi
- c'e il rischio di divergenza tra stato interno e stato exchange

Fase 6 sposta la responsabilita dei protettivi da "decisione strategy" a "gestione ordini reale".

---

## Principi architetturali

### 1. Freqtrade resta il motore di entry

La Fase 6 non elimina freqtrade dal percorso di entrata.
Freqtrade puo continuare a:

- leggere i segnali dal DB
- aprire la posizione
- notificare i fill
- mantenere FreqUI e il workflow operativo attuale

### 2. I protettivi diventano responsabilita del bot

Una volta che la posizione e aperta, la gestione di:

- stop loss
- take profit ladder
- cancellazioni
- sostituzioni
- ribilanciamento quantita

non deve dipendere solo dai callback strategy di freqtrade, ma da un layer dedicato.

### 3. Una sola sorgente di verita per gli ordini protettivi

Per ogni trade attivo deve esserci un solo owner logico degli ordini protettivi.

Quando Fase 6 e attiva:

- il nuovo order manager e owner di `SL` e `TP`
- la strategy non deve creare uscite autonome sugli stessi livelli
- `stoploss_on_exchange` di freqtrade non deve creare un secondo SL concorrente per i trade gestiti dal manager

### 4. DB interno come control-plane

Il DB del bot resta il piano di controllo.
L'exchange e il piano di esecuzione.

Il DB deve sapere:

- quali ordini dovrebbero esistere
- quali ordini esistono davvero su exchange
- quali sono stati aggiornati, cancellati, fillati o rifiutati

---

## Risultato atteso

Per un `NEW_SIGNAL` con entry limit, SL e 3 TP:

1. il segnale viene aperto via bridge
2. all'entry fill:
   - viene creato 1 ordine `SL` reduce-only
   - vengono creati 3 ordini `TP` reduce-only
3. ogni ordine ha `exchange_order_id` persistito nel DB
4. se arriva `U_MOVE_STOP`, il vecchio `SL` viene sostituito con il nuovo
5. se arriva `U_CLOSE_FULL`, tutti i protettivi residui vengono cancellati e la posizione viene chiusa
6. se colpisce `TP1`:
   - il DB marca `TP1` come fillato
   - il sistema applica la logica successiva prevista dalle regole
   - gli ordini residui restano coerenti con la size residua
7. se il processo si riavvia:
   - il manager interroga l'exchange
   - ricostruisce e riconcilia lo stato degli ordini

---

## Scope

### In scope

- persistenza completa `exchange_order_id` per `SL` e `TP`
- piazzamento reale degli ordini protettivi dopo fill entry
- supporto a TP multipli con quantita distribuite
- aggiornamento/cancellazione ordini su `UPDATE`
- riconciliazione al riavvio
- test unitari e di integrazione del lifecycle ordini

### Out of scope

- riscrittura completa della logica di entry
- motore portfolio multi-exchange
- strategie tecniche autonome
- trailing stop sofisticati non presenti nel segnale/regole
- gestione manuale via UI prima della base automatica

---

## Architettura target

```text
signals / operational_signals / trades / orders / positions
        |
        |  control-plane
        v
src/execution/exchange_order_manager.py
  -> decide quali ordini protettivi devono esistere
  -> calcola qty e price dei TP
  -> applica replace/cancel/create
        |
        v
src/execution/exchange_gateway.py
  -> wrapper exchange-backed
  -> create / cancel / fetch / amend ordini
  -> adapter unico per Bybit/ccxt o altro backend scelto
        |
        v
exchange reale
        |
        v
src/execution/order_reconciliation.py
  -> confronta stato exchange e DB
  -> risolve mismatch
  -> aggiorna DB e/o pianifica remediation
```

Freqtrade resta nel percorso:

```text
signals PENDING -> SignalBridgeStrategy -> entry -> fill callback
                                                |
                                                v
                                  exchange_order_manager.sync_after_entry_fill()
```

---

## Componenti nuovi o estesi

### 1. `src/execution/exchange_gateway.py`

Wrapper minimo e testabile verso il backend exchange.

Responsabilita:

- creare ordini reduce-only
- cancellare ordini per `exchange_order_id`
- fetch ordini aperti/chiusi per symbol o trade
- fetch posizione corrente
- normalizzare risposta exchange in formato interno

Regola:

- tutta la dipendenza da ccxt / API venue vive qui
- il resto del sistema non parla con il client exchange direttamente

### 2. `src/execution/exchange_order_manager.py`

Core orchestration dei protettivi.

Responsabilita:

- costruire il piano ordini desiderato dopo fill entry
- creare `SL` e `TP`
- applicare `UPDATE` su ordini esistenti
- aggiornare il DB con `exchange_order_id`, status e metadata
- mantenere coerenza delle quantita residue dopo partial fill

API attese:

- `sync_after_entry_fill(attempt_key, fill_qty, fill_price, ...)`
- `apply_update(attempt_key, update_context, ...)`
- `sync_after_tp_fill(attempt_key, tp_idx, closed_qty, ...)`
- `sync_after_stop_fill(attempt_key, ...)`
- `cancel_all_protective_orders(attempt_key, reason, ...)`

### 3. `src/execution/order_reconciliation.py`

Motore di riconciliazione DB <-> exchange.

Responsabilita:

- bootstrap al riavvio
- watchdog periodico
- rilevazione mismatch
- correzione conservativa

Esempi mismatch:

- ordine presente su exchange ma non nel DB
- ordine nel DB ancora `NEW` ma su exchange `filled/canceled/rejected`
- size posizione residua incompatibile con TP residui
- stop loss mancante

### 4. Estensione `src/execution/freqtrade_callback.py`

Il callback non deve piu limitarsi a persistere il fill.
Deve anche delegare al manager quando appropriato.

Eventi chiave:

- entry fill -> crea protettivi reali
- TP fill -> aggiorna DB e ripianifica ordini residui
- SL fill -> chiude e cancella il resto
- full close -> cancella ordini residui

### 5. Riduzione responsabilita della strategy

Quando Fase 6 e attiva:

- `custom_stoploss()` non deve essere owner del livello reale di stop
- `adjust_trade_position()` non deve essere owner della ladder TP
- `custom_exit()` non deve emulare l'ultimo TP se il manager ha gia ordini reali

La strategy resta owner di:

- entry
- gate finale pre-entry
- callback di fill

---

## Modello dati e persistenza

Le tabelle esistenti coprono gran parte del bisogno:

- `orders`
- `trades`
- `positions`
- `events`

Ma Fase 6 richiede una verifica esplicita del modello.

### Minimo richiesto in `orders`

Per ogni ordine protettivo il DB deve poter salvare:

- `attempt_key`
- `purpose` (`SL`, `TP`)
- `idx`
- `qty`
- `price`
- `trigger_price`
- `status`
- `client_order_id`
- `exchange_order_id`

### Campi addizionali consigliati

Se non gia presenti tramite campi esistenti o metadata, valutare migration per:

- `venue_status_raw`
- `last_exchange_sync_at`
- `last_exchange_payload_json`
- `replace_group`
- `cancel_reason`

La scelta finale dipende da quanto si vuole spingere la riconciliazione automatica.
Il PRD non impone subito tutte le migration, ma richiede che il design sia esplicito prima di implementare.

### Stato ordini

Stati minimi canonici:

- `NEW`
- `OPEN`
- `PARTIALLY_FILLED`
- `FILLED`
- `CANCELLED`
- `REJECTED`
- `EXPIRED`

Regola:

- lo stato nel DB e una normalizzazione conservativa dello stato venue

---

## Lifecycle operativo

### Caso A - Entry fill -> protettivi iniziali

1. freqtrade notifica fill entry
2. callback aggiorna `signals`, `trades`, `positions`
3. callback invoca `exchange_order_manager.sync_after_entry_fill()`
4. manager legge:
   - side
   - qty fillata
   - stop iniziale
   - lista TP
   - distribuzione TP
5. manager crea:
   - 1 SL reduce-only
   - N TP reduce-only
6. manager persiste `exchange_order_id` e status

### Caso B - TP fill

1. exchange segnala fill di un TP
2. il sistema marca quel TP come `FILLED`
3. aggiorna `positions.size`
4. applica eventuale logica post-TP:
   - move stop
   - cancella TP superati
   - ricalcola qty residue
5. sincronizza gli ordini residui

### Caso C - Update su trade attivo

Esempi:

- `U_MOVE_STOP`
- `U_CLOSE_FULL`
- `U_CLOSE_PARTIAL`
- `U_CANCEL_PENDING`
- futuro `U_UPDATE_TAKE_PROFITS`

Regola generale:

1. parser/router/target resolver aggiornano il DB canonico
2. order manager legge il nuovo stato desiderato
3. confronta ordini target con ordini esistenti
4. applica le differenze:
   - `create`
   - `cancel`
   - `replace`

### Caso D - Riavvio

1. il processo parte
2. `order_reconciliation.bootstrap_sync()` carica tutti i trade aperti
3. per ogni `attempt_key`:
   - legge ordini DB
   - legge ordini exchange
   - legge posizione exchange
4. genera remediation conservativa:
   - aggiorna status mancanti
   - ricrea protettivi mancanti se sicuro
   - apre warning se mismatch ambiguo

---

## Regole chiave di coerenza

### Regola 1 - No duplicati SL

Se il manager e owner del `SL`, freqtrade non deve creare un secondo `stoploss_on_exchange`
sullo stesso trade.

Decisione richiesta in implementazione:

- o disabilitare globalmente `stoploss_on_exchange`
- o introdurre modalita per cui i trade managed non usano lo stoploss exchange di freqtrade

### Regola 2 - No doppio owner dei TP

Se esistono TP reali su exchange:

- `adjust_trade_position()` non deve piu creare partial exit per gli stessi TP
- `custom_exit()` non deve piu chiudere sull'ultimo TP in parallelo

### Regola 3 - Replace conservativo

Per molti exchange, modificare un ordine equivale a:

1. cancellare il vecchio
2. creare il nuovo

Il sistema deve trattare il replace come operazione non atomica e auditabile.

### Regola 4 - Quantita residue sempre coerenti

Dopo un partial TP:

- somma qty TP residue <= size posizione residua
- SL riflette size residua
- nessun ordine reduce-only supera la posizione aperta

---

## Integrazione con UPDATE e regole operative

Le `management_rules` restano la fonte del comportamento desiderato.

Fase 6 non cambia la semantica di alto livello delle regole.
Cambia il meccanismo con cui vengono applicate.

Esempi:

- `tp_handling.tp_close_distribution` determina le qty dei TP reali
- `U_MOVE_STOP` sostituisce il vero ordine `SL`
- `U_CLOSE_FULL` cancella tutti i protettivi e chiude la posizione
- `U_CLOSE_PARTIAL` puo richiedere market/limit exit attivo e successiva ricostruzione del ladder residuo

---

## Modalita operative

### Modalita 1 - `strategy_managed`

Stato attuale Fase 5.

- SL via freqtrade
- TP gestiti dalla strategy

### Modalita 2 - `exchange_manager`

Target Fase 6.

- entry via freqtrade
- SL + TP via order manager
- strategy con responsabilita ridotta

Serve un feature flag esplicito, ad esempio:

- `execution.protective_orders_mode = strategy_managed | exchange_manager`

---

## Failure modes da coprire

- entry fillata ma creazione TP fallisce parzialmente
- exchange accetta SL ma rifiuta alcuni TP
- update arriva mentre un replace e in corso
- partial fill venue non ancora riflesso nel DB
- riavvio durante una sequenza cancel/create
- ordine presente su exchange ma `exchange_order_id` non persistito

Per ogni caso il sistema deve:

- registrare evento
- aggiornare warning
- evitare azioni distruttive doppie
- lasciare il trade in stato riconciliabile

---

## Test richiesti

### Unit test

- costruzione piano TP da signal + management rules
- create/cancel/replace plan
- mapping venue status -> status interno
- selezione owner strategy vs manager

### Integration test DB

- entry fill -> SL + TP creati con `exchange_order_id`
- TP1 fill -> posizione ridotta, TP1 `FILLED`, residui coerenti
- move stop -> vecchio SL cancel, nuovo SL create
- close full -> tutti i protettivi cancellati/chiusi

### Reconciliation test

- restart con ordini exchange gia aperti
- DB mancante `exchange_order_id`
- ordine TP fillato fuori dal processo

### Live/dry-run verification

- almeno 1 trade con entry, SL reale e ladder TP reale
- almeno 1 update di stop
- almeno 1 riavvio con riconciliazione riuscita

---

## Ordine di sviluppo

### Step 21

Definire il contratto dati e il feature flag.

### Step 22

Implementare `exchange_gateway` e `exchange_order_manager` per:

- persistenza completa `exchange_order_id`
- create protettivi dopo entry fill

### Step 23

Implementare update management sugli ordini gia aperti:

- move stop
- close full
- partial / future TP updates

### Step 24

Implementare riconciliazione al riavvio e watchdog periodico.

### Step 25

Hardening, smoke live/dry-run, runbook finale.

---

## Deliverable minimi della fase

- `docs/PRD_FASE_6.md`
- `docs/FASE_6_PROMPTS.md`
- feature flag manager protettivi
- gateway exchange-backed testabile
- order manager con TP/SL reali
- riconciliazione bootstrap
- test verdi su DB + integrazione

---

## Decisioni da bloccare prima di coding

1. Il backend exchange del manager sara:
   - diretto via ccxt
   - wrapper su API/gateway freqtrade
   - altro adapter dedicato

2. Quando `exchange_manager` e attivo:
   - `stoploss_on_exchange` freqtrade va disabilitato globalmente
   - oppure va escluso solo per i trade managed

3. La migration ordini per metadata/raw venue state serve subito o puo aspettare Step 24.

4. La riconciliazione bootstrap deve essere:
   - solo audit/warning
   - oppure auto-remediation quando la correzione e sicura

---

## Regola finale di fase

La fase e considerata completata solo quando almeno un trade reale o dry-run avanzato mostra:

- entry fillata
- `SL` reale presente su exchange
- tutti i `TP` previsti presenti su exchange
- `exchange_order_id` persistiti nel DB
- update di stop applicato all'ordine reale
- riavvio con riconciliazione riuscita


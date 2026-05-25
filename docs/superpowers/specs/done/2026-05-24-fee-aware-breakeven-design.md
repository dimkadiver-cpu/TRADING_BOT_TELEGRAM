# Fee-Aware Breakeven Design

## Goal

Sostituire il concetto di `be_buffer_pct` con una semantica unica di breakeven fee-aware nel runtime V2.

Quando il sistema riceve o genera un `MOVE_STOP_TO_BREAKEVEN` senza prezzo stop esplicito e senza target `TP_LEVEL`, il nuovo stop non deve piu' essere calcolato come `entry_avg_price +/- pct`, ma come il prezzo che porta il trade a `PnL netto = 0` considerando:

- fee reale di apertura gia' sostenuta dalla chain;
- fee stimata di chiusura coerente con il tipo di uscita protettiva previsto dalla chain;
- fallback account/exchange quando la chain non contiene abbastanza informazioni per stimare la fee di chiusura.

Il nuovo comportamento deve valere in tutti i casi:

- update manuali Telegram che producono `SET_STOP target_type="ENTRY"`;
- trigger automatici `be_trigger` dopo un TP non finale.

## Scope

Questa modifica riguarda solo il runtime V2 e la configurazione di enrichment/runtime correlata.

Fuori scope:

- parser e riconoscimento dell'intento `MOVE_STOP_TO_BE`;
- `MOVE_STOP` con prezzo esplicito;
- `MOVE_STOP` verso `TP_LEVEL`;
- routing exchange tra `edit_sl` e `trading_stop_move_sl` salvo adeguamento del payload del comando;
- migrazione del dato storico gia' persistito in DB oltre alla compatibilita' in lettura necessaria a non rompere chain esistenti.

## Decisione Architetturale

Il punto corretto di ownership del calcolo BE e' il lifecycle, non l'adapter exchange.

Motivi:

- il lifecycle definisce il significato di "BE o meglio";
- `entry_gate.py` genera i comandi manuali `MOVE_STOP_TO_BREAKEVEN`;
- `event_processor.py` genera i comandi automatici `MOVE_STOP_TO_BREAKEVEN`;
- `entry_gate.py` contiene gia' il controllo "already protected" che deve usare la stessa semantica del prezzo effettivamente inviato all'exchange;
- lasciare il calcolo nel builder/adapter produrrebbe disallineamento tra stato runtime e stop reale.

Conseguenza:

- il lifecycle deve emettere un `new_stop_price` gia' corretto;
- il builder exchange deve limitarsi a instradare e inviare quel prezzo;
- `be_buffer_pct` va rimosso dal modello di management plan.

## User-Facing Behavior

### Regola base

Per ogni richiesta di breakeven implicito:

- se il target e' `ENTRY`, il runtime calcola il prezzo corretto fee-aware;
- se il target e' un prezzo esplicito, il runtime usa quel prezzo esplicito senza correzione BE;
- se il target e' `TP_LEVEL`, il runtime risolve il livello TP come gia' fa oggi senza correzione BE.

### Obiettivo economico

Il prezzo BE corretto e' il prezzo tale che, in caso di stop colpito a quel livello:

- profitto lordo della posizione residua
- meno fee di apertura gia' pagata imputabile alla posizione residua
- meno fee di chiusura stimata

dia zero.

### Regola fallback

Se la chain non contiene dati sufficienti per determinare con certezza la fee di chiusura:

- il runtime usa un fallback configurato per account/exchange;
- non blocca il comando;
- aggiunge un warning runtime/logging utile al debug.

## Data Model Changes

### ManagementPlanConfig

Rimuovere:

- `be_buffer_pct: float`

Introdurre:

- `be_fee_correction_enabled: bool = false`
- `be_fee_fallback_profile: str | None = None`

Semantica:

- `false`: BE puro a `entry_avg_price`
- `true`: BE fee-aware

Questa scelta rende la semantica unica. Non esiste piu' una modalita' percentuale.

### Fee fallback profile

Serve un profilo configurabile per account/exchange che consenta di stimare la fee di chiusura quando la chain non basta.

La forma esatta puo' essere adattata alla struttura config gia' esistente, ma deve supportare almeno:

- fee rate per uscita protettiva `standalone_order`;
- fee rate per uscita protettiva `attached_full` o equivalente position-level;
- eventuale distinzione maker/taker solo se la chain la espone in modo affidabile.

Regola di priorita':

1. dati chain specifici;
2. profilo fee esplicito nel management plan;
3. fallback account/exchange.

## Fee Inputs

### Fee di apertura

Il calcolo usa la fee reale di apertura gia' sostenuta dalla chain, quando disponibile.

Fonte preferita:

- dati runtime/exchange gia' consolidati sulla chain o ricostruibili dagli eventi di fill.

Requisito:

- la fee di apertura va considerata sulla quantita' ancora aperta, non sull'intera size originaria se ci sono gia' stati TP o close parziali.

Se l'allocazione precisa della fee di apertura residua non e' disponibile, il runtime puo' stimarla pro-rata sulla `open_position_qty`.

### Fee di chiusura

La fee di chiusura va stimata dal tipo di uscita protettiva previsto dalla chain, non dal messaggio Telegram.

Fonte preferita:

- `execution_mode`;
- `protection_style`;
- metadata del comando/protezione attiva;
- qualunque altro dato chain-level che permetta di capire come quello stop verra' eseguito.

Se questi dati non bastano:

- usare `be_fee_fallback_profile` o il fallback account/exchange.

## Pricing Algorithm

### Inputs minimi richiesti

- `side`
- `entry_avg_price`
- `open_position_qty`
- fee apertura residua imputabile alla quantita' aperta
- fee rate di chiusura stimata

### Formula concettuale

Per una quantity residua `Q`:

- notional entry residuo = `entry_avg_price * Q`
- fee apertura residua = valore reale o stimato sulla posizione ancora aperta
- fee chiusura stimata = `close_price * Q * close_fee_rate`

#### LONG

Trovare `close_price` tale che:

`(close_price - entry_avg_price) * Q - open_fee_residual - (close_price * Q * close_fee_rate) = 0`

#### SHORT

Trovare `close_price` tale che:

`(entry_avg_price - close_price) * Q - open_fee_residual - (close_price * Q * close_fee_rate) = 0`

### Requisiti implementativi

- il risultato deve essere un unico helper di dominio riusabile;
- l'helper deve restituire anche i metadati diagnostici minimi utili a logging e test:
  - source della fee di chiusura (`chain` o `fallback`);
  - fee di apertura usata;
  - fee rate di chiusura usato;
  - prezzo finale calcolato.

## Command Contract Changes

Per `MOVE_STOP_TO_BREAKEVEN` il payload runtime deve smettere di usare:

- `target_price`
- `be_buffer_pct`

Nuovo contratto raccomandato:

- `symbol`
- `side`
- `new_stop_price`
- `is_breakeven: true`
- `protection_style`
- `position_idx`

Questo contratto rende esplicito che il lifecycle ha gia' deciso il prezzo corretto.

Compatibilita':

- l'adapter puo' continuare a tollerare il vecchio alias `entry_price/target_price` solo come bridge temporaneo, ma il nuovo codice non deve piu' emetterli per BE.

## Owning Code Changes

### 1. `src/runtime_v2/signal_enrichment/models.py`

- rimuovere `be_buffer_pct`;
- aggiungere i campi fee-aware del management plan.

### 2. `src/runtime_v2/signal_enrichment/config_loader.py`

- smettere di leggere `be_buffer_pct`;
- leggere i nuovi campi;
- garantire default sicuri per config esistenti.

### 3. `src/runtime_v2/lifecycle/entry_gate.py`

Cambiare due responsabilita':

- emissione del payload `MOVE_STOP_TO_BREAKEVEN` per update manuali;
- controllo `_is_be_or_better`.

Nuovo comportamento:

- calcolare `new_stop_price` tramite helper fee-aware quando il target e' `ENTRY` e la correzione e' abilitata;
- in `_is_be_or_better` confrontare `current_stop_price` con il prezzo BE fee-aware, non con `entry_avg_price +/- pct`.

### 4. `src/runtime_v2/lifecycle/event_processor.py`

- per i trigger automatici `be_trigger`, emettere lo stesso `new_stop_price` fee-aware;
- mantenere invariata la logica di duplicate/noop e il passaggio a `BE_MOVE_PENDING`.

### 5. `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/order_builder.py`

- per `MOVE_STOP_TO_BREAKEVEN`, usare direttamente `payload["new_stop_price"]`;
- non ricalcolare il prezzo da `target_price` e percentuale;
- preservare il routing `attached_full` vs `standalone_order`.

### 6. `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`

- mantenere eventuale bridge di compatibilita' in lettura se necessario;
- il nuovo path normale deve arrivare con `new_stop_price` gia' risolto.

## Suggested New Helper

Introdurre un helper unico di dominio, ad esempio:

- `src/runtime_v2/lifecycle/breakeven_pricing.py`

API concettuale:

- input: `TradeChain`, `ManagementPlanConfig`, eventuale contesto fee/fallback
- output: `BreakevenPriceResult`

Output raccomandato:

- `new_stop_price`
- `open_fee_residual`
- `close_fee_rate`
- `close_fee_source`
- `protection_style`

Questo evita duplicazione fra `entry_gate` e `event_processor`.

## Edge Cases

### Quantita' zero o chain non aperta

Se `open_position_qty <= 0`:

- non calcolare BE fee-aware;
- comportamento invariato di noop/review secondo il flusso gia' esistente.

### Entry average price mancante

Se `entry_avg_price` manca:

- non e' possibile calcolare un BE corretto;
- usare il comportamento attuale di review/noop dove gia' previsto dal runtime;
- non inventare fallback sul prezzo di entry parser-level.

### Fee apertura non disponibile

Se la fee reale di apertura non e' disponibile:

- usare stima coerente col profilo fee di apertura disponibile;
- loggare che la source e' stimata.

### Partial take profit gia' eseguiti

La fee di apertura deve essere imputata solo alla parte ancora aperta.

Se non esiste un dato nativo pronto:

- usare allocazione proporzionale su qty residua rispetto al totale fillato.

### Correzione disabilitata

Se `be_fee_correction_enabled = false`:

- `MOVE_STOP_TO_BREAKEVEN` usa BE puro a `entry_avg_price`;
- il sistema resta operativo senza fee correction.

## Validation Plan

### Primary signal

Per un BE implicito con correzione fee-aware attiva, il runtime deve emettere uno stop tale che una chiusura a quel prezzo porti il netto a zero entro la tolleranza numerica definita dai test.

### Secondary signals

Test da aggiornare o aggiungere:

- config loader: nuovi campi management plan, rimozione `be_buffer_pct`;
- `entry_gate`: payload manuale BE usa `new_stop_price`;
- `entry_gate`: `_is_be_or_better` usa prezzo fee-aware;
- `event_processor`: BE automatico usa `new_stop_price`;
- builder Bybit: `MOVE_STOP_TO_BREAKEVEN` consuma `new_stop_price`;
- test LONG e SHORT con fee di apertura reale + fee di chiusura stimata;
- test fallback chain -> account/exchange;
- test no correction quando `be_fee_correction_enabled=false`;
- test che `MOVE_STOP` con prezzo esplicito non entri nella logica fee-aware;
- test che `MOVE_STOP` verso `TP_LEVEL` non entri nella logica fee-aware.

### Numerical tolerance

I test del calcolo devono usare una tolleranza esplicita e stretta, sufficiente a gestire floating point ma non cosi' larga da nascondere errori economici.

## Migration And Rollout

### Config migration

- rimuovere `be_buffer_pct` da `config/operation_config.yaml` e trader config che lo usano;
- sostituirlo con i nuovi campi fee-aware;
- aggiungere il profilo fallback account/exchange richiesto dal runtime.

### Runtime compatibility

Per evitare regressioni su chain gia' in corso:

- il codice deve tollerare `management_plan_json` vecchi dove `be_buffer_pct` e' ancora presente;
- il nuovo runtime deve ignorarlo e applicare default dei nuovi campi quando mancano.

Non e' necessaria una migrazione DB distruttiva del JSON storico se il reader resta backward-compatible.

## Risks

- disponibilita' incompleta delle fee reali di apertura nella chain;
- difficolta' nel determinare con certezza il tipo di fee di chiusura per alcuni execution mode;
- possibili differenze tra fee stimata e fee effettiva exchange in casi borderline;
- rischio di incoerenza se alcuni rami continuano a emettere `target_price` invece di `new_stop_price`.

## Acceptance Criteria

1. `be_buffer_pct` non e' piu' usato dal runtime V2 per il calcolo BE.
2. Un BE implicito manuale o automatico con correzione attiva emette un `new_stop_price` fee-aware.
3. `MOVE_STOP` con prezzo esplicito continua a usare il prezzo richiesto senza logica BE fee-aware.
4. `MOVE_STOP` verso `TP_LEVEL` continua a funzionare senza logica BE fee-aware.
5. `_is_be_or_better` usa la stessa semantica del prezzo inviato all'exchange.
6. Il builder Bybit non ricalcola piu' il BE da percentuale.
7. Se la chain non basta per stimare la fee di chiusura, il runtime usa fallback account/exchange.
8. I test coprono LONG, SHORT, partial residual open fee allocation e fallback.

## Open Implementation Note

La spec non impone un formato unico del profilo fee fallback nel file config, ma impone il comportamento. Durante l'implementazione va scelto il formato piu' coerente con la struttura config gia' presente nel repository, senza introdurre una nuova gerarchia non necessaria.

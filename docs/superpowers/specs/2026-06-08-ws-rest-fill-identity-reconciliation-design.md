# WS/REST Fill Identity + Reconciliation Design

**Data**: 2026-06-08  
**Stato**: Draft

---

## Obiettivo

Correggere in modo universale il flusso exchange-driven runtime v2 per evitare che:

- un fill reale arrivato via WebSocket venga perso per collisione di dedupe;
- la chiusura finale di una chain dipenda dalla reconciliation REST quando il WS ha giÃ  visto il fill;
- `watch_positions` o la reconciliation vengano usati come fonte primaria della chiusura, invece che come conferma o safety net.

Done significa:

- ogni fill exchange ha una identitÃ  univoca stabile separata dalla semantica lifecycle;
- WS e REST possono convergere sullo stesso fatto senza duplicazioni o perdita di eventi;
- la chiusura finale viene prodotta dal path WS quando il fill finale esiste;
- la reconciliation REST resta fallback di recupero, non owner della veritÃ .

---

## Problema osservato

Caso reale verificato su chain `1` nel DB:

- TP1 parziale ricevuto via `watch_my_trades` e persistito come `TP_FILLED`;
- TP finale ricevuto via `watch_my_trades` e salvato in `exchange_raw_events`;
- il TP finale non Ã¨ stato promosso in `ops_exchange_events`;
- `watch_positions` ha visto `pos_qty = 0`, ma lo snapshot Ã¨ stato classificato `UNKNOWN`;
- `run_position_reconciliation()` ha poi chiuso la chain con `CLOSE_FULL_FILLED` sintetico.

### Root cause

La deduplica degli eventi promossi al lifecycle usa una chiave semantica:

- `TP_FILLED:<chain_id>`
- oppure `TP_FILLED:<chain_id>:level:<tp_level>`

Quando Bybit invia TP position-level senza `orderLinkId`, il classifier:

- riconosce correttamente il fill come `TP_FILLED`;
- ma non riesce a determinare `tp_level`;
- quindi sia TP parziale sia TP finale finiscono con la stessa chiave `TP_FILLED:<chain_id>`.

Il secondo evento viene quindi ignorato da `INSERT OR IGNORE`.

### Problema strutturale

Il sistema oggi mescola due responsabilitÃ  diverse:

1. **identitÃ  del fatto exchange**
   - "questo fill Ã¨ successo davvero"
2. **semantica lifecycle**
   - "questo fill significa TP intermedio / TP finale / close manuale / SL"

Queste due responsabilitÃ  devono essere separate.

---

## Acceptance Contract

### Criterio principale

Se un fill finale esiste nei raw exchange facts, il runtime deve essere in grado di chiudere la chain senza aspettare una reconciliation sintetica.

### Pass/Fail Criteria

1. Due fill distinti della stessa chain non possono collidere solo perchÃ© condividono `event_type = TP_FILLED` e `tp_level = None`.
2. Lo stesso fill osservato sia via WS che via REST produce un solo evento logico, non due, e non viene perso.
3. `watch_positions` con `pos_qty = 0` non deve essere la fonte primaria della chiusura, ma puÃ² confermare o rilevare drift.
4. La reconciliation REST deve inserire eventi mancanti solo quando il fill non Ã¨ giÃ  stato acquisito dal path principale.
5. Il caso Bybit con TP attached position-level senza `orderLinkId` deve restare corretto su TP singoli, TP multipli, close manuali e downtime.

### Segnali secondari

- test unitari su dedupe identity vs classification;
- test integrazione WS + REST sullo stesso fill;
- test sul caso TP multipli con `tp_level = None`;
- test sul caso `watch_positions qty=0` senza fill trade;
- verifica su DB reale/replay che la chain venga chiusa dal fill WS e non dalla reconciliation sintetica.

---

## Decisione

### 1. WS resta fonte primaria dei fill

I fill osservati via `watch_my_trades` sono la fonte canonica dei fatti di esecuzione.

### 2. REST resta safety net

Il polling REST serve solo a:

- recuperare fill persi durante downtime o disconnessioni;
- confermare stato posizione/ordini;
- rilevare drift o buchi di acquisizione.

REST non deve sovrascrivere o duplicare un fill giÃ  visto dal WS.

### 3. Dedupe per identitÃ  exchange, non per semantica lifecycle

La chiave primaria di un fill deve essere basata sull'identitÃ  reale dell'evento exchange:

- per trade/fill: `exchange_event_id` / `execId`
- per order snapshot: `order_id + status + updated_time` o equivalente
- per position snapshot: `symbol + side + seq`

La semantica `TP_FILLED`, `SL_FILLED`, `CLOSE_FULL_FILLED` non deve essere usata come identitÃ  primaria del fatto.

### 4. Classificazione semantica dopo l'acquisizione del fatto

Dopo il dedupe identity-based, il lifecycle puÃ² derivare:

- TP intermedio
- TP finale
- SL
- close manuale parziale
- close manuale totale
- evento inferred da reconciliation

Questa derivazione puÃ² usare:

- `create_type`
- `stop_order_type`
- `closed_size`
- `pos_qty`
- stato chain precedente
- piano TP/SL della chain
- snapshot posizione prima/dopo il fill

---

## Modello concettuale

## Livello 1: Raw Exchange Fact

Ogni stream exchange entra come fatto grezzo con identitÃ  nativa:

- `source_stream`
- `exchange_event_id`
- `symbol`
- `side`
- `order_id`
- `order_link_id`
- `exec_qty`
- `closed_size`
- `pos_qty`
- `exchange_time`

Questo Ã¨ il layer auditabile e stabile.

## Livello 2: Canonical Exchange Event

Da uno o piÃ¹ raw facts si ottiene un evento canonico di esecuzione:

- tipo logico
- chain associata
- attributi di fill
- eventuale `tp_level`
- fonte principale / fonte di recupero

Qui avviene il dedupe cross-stream.

## Livello 3: Lifecycle Meaning

Il lifecycle interpreta il canonical event nel contesto della chain e produce:

- transizione di stato
- eventi lifecycle
- eventuali command di follow-up

---

## Owner dei vari stream

### `watch_my_trades`

Owner dei fill reali.

ResponsabilitÃ :

- acquisire tutti i trade/fill;
- promuovere i fill al lifecycle;
- fungere da fonte primaria per TP/SL/manual close.

### `watch_orders`

Owner dello stato ordini.

ResponsabilitÃ :

- cancel confermati;
- stato ordini aperti/chiusi;
- supporto di contesto, non owner del fill finale.

### `watch_positions`

Owner dello snapshot posizione.

ResponsabilitÃ :

- conferma dello stato posizione dopo un fill;
- rilevazione drift;
- rilevazione rimozione protective;
- non owner primario del significato di chiusura.

### `fetch_my_trades`

Safety net per fill mancati dal WS.

ResponsabilitÃ :

- recuperare fill non presenti localmente;
- convergere sulla stessa identitÃ  del fill WS quando possibile.

### `fetch_positions`

Safety net di stato.

ResponsabilitÃ :

- confermare posizione attuale;
- rilevare mismatch DB vs exchange;
- produrre eventi inferred solo se manca il fill reale.

---

## Design delle chiavi di dedupe

## Principio

La tabella che alimenta il lifecycle non deve deduplicare con chiavi del tipo:

- `TP_FILLED:<chain_id>`
- `SL_FILLED:<chain_id>`
- `CLOSE_FULL_FILLED:<chain_id>`

perchÃ© queste chiavi rappresentano significato, non identitÃ .

## Regola proposta

Per i fill:

- dedupe key primaria = `fill:<exchange_event_id>`

Per gli order snapshot:

- dedupe key primaria = `order:<order_id>:<normalized_status>:<exchange_time_or_seq>`

Per i position snapshot:

- dedupe key primaria = `position:<symbol>:<side>:<seq_or_updated_time>`

## Conseguenza

Due TP diversi della stessa chain:

- non collidono mai se hanno `exchange_event_id` diversi;
- possono entrambi essere classificati `TP_FILLED`;
- il lifecycle decide poi se il secondo Ã¨ finale.

---

## Gestione del caso `tp_level = None`

### Regola

L'assenza di `tp_level` non invalida il fill.

`tp_level` Ã¨ un attributo di arricchimento, non una precondizione per accettare l'evento.

### Come derivare il significato finale

Se arriva un `TP_FILLED` senza `tp_level`:

1. il fill viene sempre acquisito come fatto unico;
2. si guarda lo stato della chain prima del fill;
3. si confronta `open_position_qty` residuo atteso;
4. si usa `watch_positions` o snapshot posizione per capire se dopo il fill la posizione Ã¨ andata a zero;
5. se la posizione Ã¨ chiusa, il lifecycle produce la chiusura finale coerente.

### Importante

Il sistema non deve dipendere da `tp_level` per distinguere:

- TP intermedio
- TP finale

quando il dato exchange non lo fornisce.

---

## Ruolo della reconciliation

## Reconciliation trade-based

Serve per recuperare fill mancati.

Regola:

- se il fill identity-based esiste giÃ , nessun nuovo evento;
- se manca, si inserisce il fill recuperato con la stessa identitÃ  o con identitÃ  riconciliabile;
- solo in assenza dell'identitÃ  reale si puÃ² marcare l'evento come recovered/inferred.

## Position reconciliation

Serve per recuperare mismatch di stato.

Regola:

- se posizione = 0 ma manca un fill finale reale, puÃ² sintetizzare `CLOSE_FULL_FILLED` inferred;
- se il fill WS o REST Ã¨ giÃ  presente, non deve generare una seconda chiusura;
- deve essere considerata l'ultima rete di sicurezza, non il path normale.

---

## Implicazioni applicative

### `exchange_raw_events`

Resta il log completo dei fatti grezzi per stream.

### `ops_exchange_events`

Deve diventare la tabella dei fatti canonici deduplicati per identitÃ  exchange.

### `lifecycle`

Deve interpretare i canonical facts senza assumere che:

- un TP senza `tp_level` sia sempre ambiguo;
- `watch_positions qty=0` basti da solo a dire TP/SL/manual close;
- la reconciliation sia la chiusura standard.

---

## Opzioni considerate

### Opzione A - allargare le chiavi semantiche esistenti

Esempi:

- `TP_FILLED:<chain>:<exchange_order_id>`
- `TP_FILLED:<chain>:<exchange_time>`

Pro:

- diff piccolo

Contro:

- resta confusione tra identitÃ  e significato;
- non generalizza bene a REST, snapshot e casi futuri.

### Opzione B - usare `watch_positions qty=0` come chiusura universale

Pro:

- semplice da capire

Contro:

- non distingue TP/SL/manual close;
- non preserva il fill reale;
- peggiora la semantica finanziaria e i dettagli del report.

### Opzione C - separare identity e classification

Pro:

- modello corretto e generale;
- funziona con WS, REST e Bybit attached orders senza `orderLinkId`;
- riduce il ruolo delle euristiche fragili.

Contro:

- richiede refactor mirato di ingest e dedupe.

### Raccomandazione

Usare l'Opzione C.

---

## Scope di implementazione

## In scope

- ridisegno delle chiavi di dedupe per `ops_exchange_events`;
- convergenza WS/REST sullo stesso fill identity-based;
- gestione corretta dei TP finali senza `tp_level`;
- ridefinizione del ruolo di `watch_positions` come conferma/drift;
- mantenimento della reconciliation come fallback.

## Out of scope

- redesign completo di tutti i report control-plane;
- nuove UI o nuovi template Telegram;
- rework generale dei modelli di risk/PnL.

---

## Test richiesti

1. TP1 e TP finale via WS, entrambi senza `orderLinkId`
   - entrambi devono entrare
   - il secondo deve chiudere la chain

2. Stesso fill visto prima da WS e poi da REST
   - un solo evento logico finale

3. WS fill finale presente + `watch_positions qty=0`
   - nessuna chiusura sintetica via reconciliation

4. Nessun WS fill finale, ma `fetch_my_trades` lo trova
   - la chain si chiude dal fill recuperato, non da snapshot puro

5. Nessun fill disponibile, ma posizione a zero
   - la reconciliation puÃ² ancora produrre un evento inferred di sicurezza

---

## Rischi e note

- se la separazione identity/classification resta incompleta, continueranno a esistere collisioni sottili su altri exchange event reduce-only;
- i casi multi-chain stesso symbol/side richiedono ancora attenzione nell'attribuzione chain, ma questo problema Ã¨ distinto dalla dedupe identity-based;
- la migration logica deve preservare idempotenza e non generare replay doppi su DB giÃ  popolati.

---

## Suggested Implementation Order

1. Introdurre dedupe identity-based per i fill canonicalizzati.
2. Mantenere i raw WS/REST come audit trail indipendente.
3. Aggiornare la promozione a `ops_exchange_events` per non usare chiavi semantiche `TP_FILLED:<chain>`.
4. Aggiornare il lifecycle per chiudere correttamente i fill finali senza dipendere da `tp_level`.
5. Ridurre `run_position_reconciliation()` a fallback puro.
6. Aggiungere test di convergenza WS/REST e del caso finale senza `orderLinkId`.

---

## Suggested commit message

`design identity-based exchange fill dedupe and keep reconciliation as fallback`

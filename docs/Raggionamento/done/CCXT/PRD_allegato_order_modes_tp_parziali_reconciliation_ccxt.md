# PRD Allegato — CCXT Execution Design: modalità ordini, TP parziali e riconciliazione stato exchange

**Documento collegato:** `PRD_runtime_v2_passaggio_hummingbot_a_ccxt_lifecycle_rev2.md`  
**Data:** 2026-05-18  
**Ambito:** `runtime_v2` — execution gateway / CCXT Bybit adapter / exchange event ingestion / reconciliation  
**Scopo:** chiarire e fissare due aspetti architetturali rimasti aperti:

1. modalità di gestione ordini e trattamento dei Take Profit parziali;
2. riconciliazione dello stato locale con lo stato reale su exchange.

---

# 1. Decisioni sintetiche

## 1.1 Gestione ordini

Il sistema mantiene **tre modalità configurabili** di gestione iniziale della posizione:

```text
a_sequential
b_entry_stop_then_tp
c_native_attached_tpsl
```

La denominazione precedente `c_bracket` / `OCO` viene deprecata perché, nel caso Bybit perpetual/futures, il riferimento più corretto è a **TP/SL nativi attached / position-level**, non a OCO generico.

## 1.2 Take Profit parziali

Per le modalità:

```text
a_sequential
b_entry_stop_then_tp
```

i TP parziali vengono sempre gestiti come:

```text
ordini di uscita separati, reduceOnly=true
```

Esempio:

```text
Posizione LONG size 1000

TP1 50%  → SELL 500 reduceOnly
TP2 30%  → SELL 300 reduceOnly
TP3 20%  → SELL 200 reduceOnly
```

Questa è la modalità standard del sistema per:

- multi-TP;
- TP1 → move stop to BE;
- resize protettivi;
- reconciliation deterministica;
- audit chiaro per ogni target.

## 1.3 Modalità C

La modalità:

```text
c_native_attached_tpsl
```

non è il percorso standard per la gestione multi-TP del sistema.

È una modalità speciale, utile per sfruttare i meccanismi nativi Bybit di TP/SL, ma con limiti da esplicitare:

- semantica diversa rispetto ai multi-TP separati;
- maggiore complessità di reconciliation;
- minore flessibilità per move stop a BE;
- gestione dei TP parziali non equivalente al modello operativo interno.

## 1.4 Riconciliazione

La riconciliazione non è un semplice “polling ordine”.  
Va trattata come un sottosistema composto da quattro assi:

```text
1. Command ↔ Exchange Order reconciliation
2. Execution / Fill reconciliation
3. Position reconciliation
4. Protective Orders reconciliation
```

---

# 2. Modalità di gestione ordini

## 2.1 Mode A — `a_sequential`

### Definizione

```text
1. Invia entry
2. Attende fill entry
3. Invia stop loss
4. Invia TP1..TPn come ordini reduce-only separati
```

### Stato iniziale dei comandi

```text
PLACE_ENTRY                 → PENDING
PLACE_PROTECTIVE_STOP       → WAITING_POSITION
PLACE_TAKE_PROFIT x N       → WAITING_POSITION
```

### Flusso

```text
Signal accepted
  ↓
TradeChain WAITING_ENTRY
  ↓
PLACE_ENTRY inviato
  ↓
ENTRY_FILLED
  ↓
TradeChain OPEN
  ↓
rilascio STOP e TP da WAITING_POSITION a PENDING
  ↓
invio SL + TP reduce-only
```

### Vantaggi

- semantica più pulita;
- ordini protettivi dimensionati sulla quantità realmente fillata;
- ideale per:
  - multi-entry;
  - ladder/range;
  - partial fills;
  - average entry price ponderato;
  - protective sync.

### Svantaggio

Esiste una breve finestra temporale tra:

```text
fill entry
```

e:

```text
creazione stop loss
```

in cui la posizione è aperta e non ancora protetta.

### Uso consigliato

Modalità più rigorosa e più semplice da implementare correttamente.  
È il fallback sicuro se la modalità B non viene validata in modo soddisfacente su Bybit Demo.

---

# 3. Mode B — `b_entry_stop_then_tp`

## 3.1 Definizione

```text
1. Invia entry
2. Invia stop loss subito
3. Attende fill entry
4. Invia TP1..TPn come ordini reduce-only separati
```

## 3.2 Stato iniziale dei comandi

```text
PLACE_ENTRY                 → PENDING
PLACE_PROTECTIVE_STOP       → PENDING
PLACE_TAKE_PROFIT x N       → WAITING_POSITION
```

## 3.3 Flusso

```text
Signal accepted
  ↓
TradeChain WAITING_ENTRY
  ↓
PLACE_ENTRY inviato
  ↓
PLACE_PROTECTIVE_STOP inviato subito
  ↓
ENTRY_FILLED
  ↓
TradeChain OPEN
  ↓
rilascio TP da WAITING_POSITION a PENDING
  ↓
invio TP reduce-only
```

## 3.4 Vantaggi

- riduce o elimina la finestra senza stop dopo il fill entry;
- mantiene la gestione TP separata e auditabile;
- resta compatibile con:
  - più TP;
  - TP1 → move BE;
  - partial close;
  - lifecycle interno esistente.

## 3.5 Punto da validare

Questa modalità presuppone che l’exchange accetti e gestisca in modo affidabile un ordine di stop protettivo prima che la posizione sia già aperta.

### Decisione

`b_entry_stop_then_tp` resta:

```text
default candidato
```

ma deve essere:

```text
validato empiricamente su Bybit Main Demo
```

prima di essere dichiarato default definitivo.

## 3.6 Criterio di accettazione specifico

La modalità B è confermata solo se, su Bybit Main Demo:

1. il `PLACE_ENTRY` è inserito;
2. il `PLACE_PROTECTIVE_STOP` pre-position è accettato;
3. quando l’entry viene fillata, lo stop risulta coerente e immediatamente protettivo;
4. non si producono:
   - reject anomali;
   - stop non correlabili;
   - stop attivi in assenza di posizione;
   - comportamenti non riconciliabili.

---

# 4. Mode C — `c_native_attached_tpsl`

## 4.1 Definizione

La modalità C usa la gestione nativa Bybit per TP/SL:

- attached TP/SL in creazione ordine, quando applicabile;
- oppure trading stop / TP-SL position-level.

## 4.2 Obiettivo

Ridurre la logica applicativa necessaria per:

- creazione stop;
- cancellazione automatica ordini protettivi;
- alcuni casi di resize legati alla posizione.

## 4.3 Stato concettuale

La modalità C non deve essere descritta come:

```text
OCO generico
```

ma come:

```text
TP/SL native attached
```

## 4.4 Limiti

La modalità C non è equivalente al modello standard multi-TP del progetto.

Problemi principali:

1. il modello interno vuole:
   ```text
   1 stop globale + più TP parziali separati
   ```

2. la gestione nativa Bybit può ragionare in modo diverso:
   - coppie TP/SL parziali;
   - TP/SL associati alla posizione;
   - binding interno più difficile da mappare sul lifecycle.

3. la modifica dello stop a BE può diventare più fragile, perché il runtime deve capire se:
   - modificare un TP/SL position-level;
   - ricreare una struttura nativa;
   - rinunciare al binding originario.

4. la reconciliation diventa più complessa perché alcuni ordini possono essere creati internamente dall’exchange con relazioni non perfettamente sovrapponibili ai command del runtime.

## 4.5 Decisione

La modalità C viene mantenuta come:

```text
modalità avanzata / gated
```

ma non deve essere la modalità standard per il sistema multi-TP.

## 4.6 Regola consigliata

- `a_sequential` e `b_entry_stop_then_tp`
  - supportano il modello multi-TP pieno;
  - sono il percorso standard.

- `c_native_attached_tpsl`
  - può essere supportata inizialmente solo per scenari più semplici;
  - può richiedere limitazioni su:
    - massimo un TP;
    - no ladder;
    - no multi-entry;
    - no logiche avanzate di BE move;
  - oppure va mantenuta fuori dall’MVP fino a design dedicato.

---

# 5. Take Profit parziali

## 5.1 Decisione principale

Per le modalità A e B:

```text
TP parziali = ordini di uscita separati, reduceOnly=true
```

## 5.2 Motivazione

Il modello `reduceOnly` garantisce che:

- un ordine TP non possa aumentare o invertire la posizione;
- ogni TP sia autonomamente tracciabile;
- ogni fill generi un evento preciso:
  ```text
  TP_FILLED
  ```
- il lifecycle possa aggiornare:
  - `open_position_qty`;
  - `closed_position_qty`;
  - stato `PARTIALLY_CLOSED` / `CLOSED`;
- la riconciliazione possa essere eseguita per target.

## 5.3 Payload command

Esempio `PLACE_TAKE_PROFIT`:

```json
{
  "symbol": "XRP/USDT",
  "side": "LONG",
  "tp_price": 0.75,
  "sequence": 1,
  "close_pct": 50.0,
  "qty": 500.0,
  "reduce_only": true
}
```

## 5.4 Calcolo qty TP

Input:

```text
open_position_qty = qty realmente aperta
```

Non usare la qty teorica iniziale se la posizione non è ancora completamente fillata.

### Formula

```text
tp_qty_i = open_position_qty * close_pct_i / 100
```

## 5.5 Residuo di rounding

L’ultimo TP deve assorbire il residuo generato da:

- precisione quantità;
- round down richiesto dall’exchange;
- distribuzioni percentuali non perfettamente rappresentabili.

### Regola

```text
TP1..TP(n-1) = qty calcolata e arrotondata
TPn = open_position_qty - somma(TP precedenti)
```

## 5.6 Caso qty sotto minimum order size

Se un TP produce:

```text
qty < min_order_size
```

si applica la policy configurata:

```text
take_profit.min_order_policy
```

Opzioni consigliate:

```text
review
merge_into_previous
assign_to_last_tp
```

## 5.7 Eventi

Ogni ordine TP deve poter produrre eventi separati:

```text
TP_FILLED
```

con payload:

```json
{
  "tp_level": 1,
  "fill_qty": 500.0,
  "fill_price": 0.75,
  "exchange_order_id": "xxx",
  "exchange_trade_id": "yyy",
  "order_fully_filled": true
}
```

---

# 6. Riconciliazione dello stato exchange

# 6.1 Principio generale

```text
Exchange = verità sullo stato esecutivo reale.
Ops DB = verità su intenzione operativa, correlazione, lifecycle e audit.
```

La riconciliazione deve riallineare le due viste senza:
- duplicare eventi;
- inventare stati;
- correggere automaticamente situazioni ambigue.

---

# 7. Tipi di riconciliazione

## 7.1 Reconciliation A — Command ↔ Exchange Order

### Scopo

Verificare che ogni comando locale abbia un corrispondente stato exchange coerente.

### Input locali

```text
ops_execution_commands
  - SENT
  - ACK
  - WAITING_POSITION se rilevante
```

### Verifiche exchange

Per ogni `client_order_id`:

- ordine esistente e aperto;
- ordine filled;
- ordine cancelled;
- ordine rejected;
- ordine assente.

### Esiti

| Stato locale | Stato exchange | Azione |
|---|---|---|
| `SENT` | ordine trovato open | command → `ACK` |
| `SENT` | ordine non trovato | retry o `REVIEW_REQUIRED` |
| `ACK` | ordine open | nessuna azione |
| `ACK` | ordine filled | genera fill event mancante |
| `ACK` | ordine cancelled | genera `ORDER_CANCELLED` |
| `ACK` | ordine rejected | genera `ORDER_REJECTED` |
| `PENDING` | ordine già esistente | recovery idempotente, command → `ACK` |

---

## 7.2 Reconciliation B — Execution / Fill

### Scopo

Ricostruire eventuali fill persi dal WebSocket.

### Fonte exchange

- trade executions;
- order trade history;
- eventuali fill legati a `exchange_trade_id`.

### Regola

Per ogni execution identificata:

```text
se exchange_trade_id non è già stato processato:
    genera ops_exchange_events
```

### Eventi generabili

```text
ENTRY_FILLED
TP_FILLED
SL_FILLED
CLOSE_PARTIAL_FILLED
CLOSE_FULL_FILLED
```

### Idempotency key raccomandata

```text
<event_type>:<trade_chain_id>:<exchange_trade_id>
```

---

## 7.3 Reconciliation C — Position

### Scopo

Verificare che lo stato posizione locale corrisponda allo stato reale su exchange.

### Confronto

Locale:

- chain `OPEN` / `PARTIALLY_CLOSED`;
- `open_position_qty`;
- `entry_avg_price`;
- `side`;
- `symbol`.

Exchange:

- posizione aperta reale;
- qty;
- avg price;
- side;
- symbol.

### Mismatch da gestire

| Caso | Azione |
|---|---|
| locale OPEN, exchange qty = 0 | cercare fill exit mancanti; se non trovati → review |
| exchange qty > 0, nessuna chain | review / posizione esterna |
| qty locale ≠ qty exchange | replay executions; se non basta → review |
| avg price diversa | replay entry fills; se non basta → review |

---

## 7.4 Reconciliation D — Protective Orders

### Scopo

Verificare che la posizione aperta sia correttamente protetta.

### Per ogni chain OPEN / PARTIALLY_CLOSED verificare:

- stop loss presente;
- TP attesi presenti;
- qty stop coerente con `open_position_qty`;
- somma qty TP coerente con `open_position_qty`;
- `reduceOnly=true` quando richiesto;
- prezzi coerenti;
- ordini correlabili tramite `client_order_id`.

### Esiti

| Situazione | Azione |
|---|---|
| stop mancante | `SYNC_PROTECTIVE_ORDERS` |
| TP mancanti | `SYNC_PROTECTIVE_ORDERS` |
| qty SL/TP incoerente | `SYNC_PROTECTIVE_ORDERS` |
| ordini protettivi estranei | `REVIEW_REQUIRED` |
| protective coerenti | nessuna azione |

---

# 8. Trigger della reconciliation

## 8.1 All’avvio runtime

Obbligatoria.

Serve a recuperare:

- ordini inviati prima di crash;
- fill avvenuti durante downtime;
- TP/SL mancanti;
- posizioni ancora aperte;
- posizioni chiuse mentre il bot era offline.

## 8.2 Periodica

Intervallo consigliato:

```text
30–60 secondi
```

Config:

```yaml
event_stream:
  reconcile_every_seconds: 30
```

## 8.3 Dopo reconnect WebSocket

Ogni volta che il flusso CCXT Pro si riconnette:

```text
reconnect → reconciliation breve
```

per coprire il possibile buco temporale.

---

# 9. Correzioni automatiche vs review

## 9.1 Correzioni automatiche ammesse

Il sistema può correggere automaticamente quando l’evidenza exchange è chiara:

- execution non processata → genera fill event;
- ordine correlato esistente → aggiorna command ad ACK;
- ordine correlato cancellato → genera cancel event;
- protective order mancante ma posizione e chain coerenti → genera sync protettivi;
- mismatch qty spiegabile da execution events → riallinea tramite replay.

## 9.2 Casi da review manuale

Il sistema deve evitare auto-fix aggressivi quando:

- posizione exchange non correlabile a chain;
- chain OPEN senza posizione e senza execution exit ricostruibile;
- più chain candidate per la stessa posizione;
- ordine exchange non correlabile;
- differenza qty non spiegabile da executions;
- protective orders estranei e non attribuibili con certezza.

---

# 10. Impatto sul PRD principale

Il PRD principale va corretto / integrato con i seguenti punti.

## 10.1 Ripristinare tre modalità di gestione ordini

```text
a_sequential
b_entry_stop_then_tp
c_native_attached_tpsl
```

## 10.2 Dichiarare esplicitamente

```text
TP parziali in A/B = ordini reduceOnly separati
```

## 10.3 Correggere il posizionamento di SL/TP iniziali

### Mode A

```text
Entry PENDING
SL WAITING_POSITION
TP WAITING_POSITION
```

### Mode B

```text
Entry PENDING
SL PENDING
TP WAITING_POSITION
```

### Mode C

Comportamento separato, da trattare come native attached TP/SL.

## 10.4 Espandere il capitolo reconciliation

Da semplice:

```text
polling/recovery
```

a:

```text
A. command/order reconciliation
B. execution/fill reconciliation
C. position reconciliation
D. protective-orders reconciliation
```

---

# 11. Acceptance criteria dell’allegato

## 11.1 Order modes

1. Il sistema supporta A e B in modo esplicito.
2. La modalità C viene rinominata e trattata separatamente.
3. Il PRD non usa più `OCO` come termine generico per Bybit perpetual.
4. Mode A produce:
   - entry prima;
   - SL/TP dopo fill.
5. Mode B produce:
   - entry + SL subito;
   - TP dopo fill.
6. Mode B resta gated da test Bybit Demo.

## 11.2 TP parziali

7. In A/B i TP parziali sono ordini separati `reduceOnly=true`.
8. Ogni TP ha:
   - sequence;
   - qty;
   - close percentage;
   - client order ID proprio.
9. L’ultimo TP assorbe il residuo di rounding.
10. Quantità sotto minimum order size seguono policy configurata.

## 11.3 Reconciliation

11. All’avvio il sistema riconcilia ordini, fills, posizioni e protective orders.
12. La reconciliation periodica non duplica eventi.
13. Un fill perso dal websocket viene ricostruito da execution history.
14. Una posizione senza chain non viene auto-assorbita: va in review.
15. Protective orders incoerenti generano sync o review secondo evidenza disponibile.

---

# 12. Decisione finale

La revisione da adottare è:

```text
- mantenere tre mode di order management;
- usare TP parziali reduceOnly separati nelle mode A/B;
- trattare la mode C come native attached TP/SL, non come OCO standard;
- definire la reconciliation come sottosistema a quattro livelli.
```

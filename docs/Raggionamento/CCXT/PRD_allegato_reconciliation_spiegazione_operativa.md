# Allegato PRD — Reconciliation dello stato Exchange in parole operative

**Documento collegato:**  
- `PRD_runtime_v2_passaggio_hummingbot_a_ccxt_lifecycle_rev2.md`
- `PRD_allegato_order_modes_tp_parziali_reconciliation_ccxt.md`

**Data:** 2026-05-18  
**Ambito:** `runtime_v2` — `CcxtReconciliationWorker` / ops DB / Bybit via CCXT  
**Scopo:** chiarire in modo semplice e operativo come deve funzionare la riconciliazione tra stato locale del bot e stato reale dell’exchange.

---

# 1. Concetto base

La reconciliation serve a rispondere a una domanda:

```text
Quello che il bot crede nel suo DB coincide con ciò che esiste davvero su Bybit?
```

Il sistema ha due fonti:

```text
Ops DB
  → cosa il bot crede sia successo

Exchange Bybit
  → cosa è successo davvero
```

La reconciliation confronta le due viste e:

- corregge automaticamente ciò che è dimostrabile;
- crea eventi mancanti;
- segnala in review ciò che è ambiguo.

---

# 2. Perché serve

Senza reconciliation, il bot può restare incoerente se:

- si spegne mentre un TP viene fillato;
- perde un evento WebSocket;
- invia un ordine ma perde la risposta API;
- l’exchange chiude/cancella un ordine mentre il runtime non riceve l’evento;
- c’è un partial fill non registrato;
- dopo un crash il DB locale rimane indietro rispetto all’exchange.

---

# 3. Esempio semplice

## Caso

Il bot ha nel DB:

```text
Chain BTC LONG OPEN
TP1 ancora attivo
Stop presente
```

Poi:

```text
1. il bot si spegne
2. TP1 viene colpito su Bybit
3. il bot riparte
```

## Senza reconciliation

Il bot continua a credere:

```text
TP1 non fillato
posizione ancora intera
nessun move stop a BE
```

## Con reconciliation

Al riavvio:

1. legge lo storico executions su Bybit;
2. trova il fill di TP1 non ancora processato;
3. genera internamente:

```text
TP_FILLED
```

4. il lifecycle aggiorna:
   - posizione parzialmente chiusa;
   - quantity residua;
   - eventuale `MOVE_STOP_TO_BREAKEVEN`.

---

# 4. Struttura della reconciliation

La reconciliation deve avere **4 blocchi** distinti:

```text
1. Ordini
2. Fill / executions
3. Posizione
4. Ordini protettivi
```

---

# 5. Blocco 1 — Controllo ordini

## Domanda

```text
Gli ordini che il bot dice di aver inviato esistono davvero su Bybit?
```

## Input locale

Comandi in:

```text
SENT
ACK
```

e, in casi specifici, anche:

```text
PENDING
```

se il sistema deve verificare idempotenza/recovery.

## Casi tipici

### Caso A — ordine trovato

DB:

```text
PLACE_ENTRY = SENT
```

Bybit:

```text
ordine aperto
```

Azione:

```text
command → ACK
```

---

### Caso B — ordine già fillato

DB:

```text
PLACE_TAKE_PROFIT = ACK
```

Bybit:

```text
ordine chiuso perché fillato
```

Azione:

```text
non basta marcare DONE
si devono leggere le executions e generare TP_FILLED
```

---

### Caso C — ordine cancellato

DB:

```text
PLACE_ENTRY = ACK
```

Bybit:

```text
ordine cancellato
```

Azione:

```text
ORDER_CANCELLED
```

e il lifecycle decide se:
- chain resta valida;
- va in review;
- diventa `CANCELLED` / `EXPIRED`.

---

### Caso D — ordine non trovato

DB:

```text
PLACE_ENTRY = SENT
```

Bybit:

```text
ordine assente
```

Azione:

```text
retry oppure REVIEW_REQUIRED
```

Dipende da:
- tentativi già fatti;
- presenza di `client_order_id`;
- eventuali errori precedenti;
- policy di sicurezza.

---

# 6. Blocco 2 — Controllo fill / executions

## Domanda

```text
Ci sono fill avvenuti su Bybit che il bot non ha registrato?
```

Questo è il blocco più importante.

## Perché non basta lo stato ordine

Un ordine può:

- essere fillato in più pezzi;
- produrre più execution;
- restare parzialmente aperto.

Quindi non basta sapere:

```text
ordine = FILLED
```

Bisogna sapere:

```text
quali executions precise sono avvenute?
```

---

## Regola

Per ogni execution lato exchange:

```text
se exchange_trade_id non è già stato processato:
    genera un ExchangeEvent interno
```

## Eventi producibili

```text
ENTRY_FILLED
TP_FILLED
SL_FILLED
CLOSE_PARTIAL_FILLED
CLOSE_FULL_FILLED
```

---

## Esempio partial fill entry

Ordine entry da:

```text
100 unità
```

Bybit esegue:

```text
fill 1 = 30
fill 2 = 70
```

Il sistema deve poter processare:

```text
ENTRY_FILLED qty=30
ENTRY_FILLED qty=70
```

o, se la policy scelta lo richiede, accumulare questi raw fills e poi consolidarli.  
Ma in ogni caso **non deve perderli**.

---

# 7. Blocco 3 — Controllo posizione

## Domanda

```text
La posizione che il bot crede aperta coincide con quella reale su Bybit?
```

## Confronto

### Locale

```text
open_position_qty
entry_avg_price
symbol
side
lifecycle_state
```

### Exchange

```text
position_qty
avg_price
symbol
side
```

---

## Casi tipici

### Caso A — tutto coincide

DB:

```text
BTC LONG qty 0.02
```

Bybit:

```text
BTC LONG qty 0.02
```

Azione:

```text
nessuna
```

---

### Caso B — quantità diversa

DB:

```text
qty 0.02
```

Bybit:

```text
qty 0.01
```

Possibili cause:
- TP partial eseguito e perso;
- close partial perso;
- fill non processato.

Azione:

```text
1. cercare executions mancanti
2. se trovate → ricostruire eventi
3. se non spiegabile → REVIEW_REQUIRED
```

---

### Caso C — DB OPEN ma exchange senza posizione

DB:

```text
chain OPEN
```

Bybit:

```text
nessuna posizione
```

Possibili cause:
- SL colpito e evento perso;
- close full eseguita e persa;
- liquidazione/manual close non gestita.

Azione:

```text
1. cercare executions exit mancanti
2. se trovate → ricostruire
3. se non trovate → REVIEW_REQUIRED
```

---

### Caso D — exchange ha posizione ma nessuna chain

Bybit:

```text
BTC LONG aperto
```

DB:

```text
nessuna chain collegata
```

Azione:

```text
REVIEW_REQUIRED
```

Il sistema non deve “adottare” automaticamente posizioni sconosciute.

---

# 8. Blocco 4 — Controllo ordini protettivi

## Domanda

```text
La posizione aperta è davvero protetta come il bot crede?
```

Per ogni chain:

```text
OPEN
PARTIALLY_CLOSED
```

verificare:

- stop loss presente;
- take profit attesi presenti;
- quantity stop coerente con posizione residua;
- somma quantity TP coerente con posizione residua;
- prezzi corretti;
- ordini correlabili tramite `client_order_id`;
- `reduceOnly=true` dove richiesto.

---

## Casi tipici

### Caso A — stop mancante

DB:

```text
posizione OPEN
stop atteso presente
```

Bybit:

```text
stop assente
```

Azione:

```text
SYNC_PROTECTIVE_ORDERS
```

---

### Caso B — TP residui sovradimensionati

DB:

```text
posizione residua qty = 500
```

Bybit:

```text
TP residui totali = 1000
```

Azione:

```text
SYNC_PROTECTIVE_ORDERS
```

---

### Caso C — ordini sconosciuti

Bybit:

```text
ordine TP presente
```

DB:

```text
nessun client_order_id compatibile
```

Azione:

```text
REVIEW_REQUIRED
```

Il sistema non cancella automaticamente ordini non attribuibili con certezza.

---

# 9. Quando eseguire la reconciliation

## 9.1 All’avvio del runtime

Obbligatoria.

Serve a capire cosa è successo mentre il bot era offline.

### Deve controllare almeno:

- command `SENT` / `ACK`;
- executions recenti;
- posizioni aperte;
- ordini protettivi attivi.

---

## 9.2 Periodicamente

Intervallo consigliato:

```text
30–60 secondi
```

Obiettivo:

```text
sanity check continuo
```

Non deve sostituire gli eventi WebSocket real-time, ma fare da rete di sicurezza.

---

## 9.3 Dopo reconnect WebSocket

Se CCXT Pro perde la connessione e poi si riconnette:

```text
reconnect → reconciliation breve
```

Serve a coprire eventuali eventi persi durante il blackout.

---

# 10. Cosa può correggere automaticamente

Il sistema può auto-riparare solo quando l’evidenza è forte.

## Esempi ammessi

### Execution non processata

```text
trovato exchange_trade_id nuovo
```

Azione:

```text
genera evento fill mancante
```

---

### Ordine inviato e presente su exchange

```text
command SENT
ordine trovato open
```

Azione:

```text
command → ACK
```

---

### Stop mancante ma chain coerente

```text
posizione OPEN
stop atteso
nessun stop presente
```

Azione:

```text
SYNC_PROTECTIVE_ORDERS
```

---

### Quantità differente ma spiegabile da fills mancanti

Azione:

```text
replay eventi mancanti
aggiorna lifecycle
```

---

# 11. Quando mandare in review

La reconciliation non deve “indovinare”.

Va in:

```text
REVIEW_REQUIRED
```

quando:

- posizione exchange non correlabile a chain;
- chain OPEN senza posizione e senza evento ricostruibile;
- più chain possibili per la stessa posizione;
- ordini exchange non attribuibili;
- qty locale ≠ qty exchange senza spiegazione;
- stop/TP presenti ma non correlabili;
- stato ambiguo dopo errori multipli.

---

# 12. Componente software suggerito

## Nuovo worker

```text
CcxtReconciliationWorker
```

## Responsabilità

```text
run_on_startup()
run_periodically()
run_after_ws_reconnect()
```

## Internamente divide il lavoro in:

```text
reconcile_commands_and_orders()
reconcile_executions()
reconcile_positions()
reconcile_protective_orders()
```

---

# 13. Output della reconciliation

La reconciliation non deve modificare direttamente il lifecycle in modo arbitrario.

Deve produrre:

```text
ops_exchange_events
```

come se gli eventi fossero arrivati live.

Esempi:

```text
ENTRY_FILLED
TP_FILLED
SL_FILLED
CLOSE_PARTIAL_FILLED
CLOSE_FULL_FILLED
ORDER_CANCELLED
ORDER_REJECTED
```

Poi il normale:

```text
LifecycleEventWorker
```

aggiorna le trade chain.

---

# 14. Esempio completo: bot spento durante TP1

## Stato iniziale

```text
Chain BTC LONG OPEN
open_position_qty = 1.0
TP1 attivo 0.5
TP2 attivo 0.5
SL attivo 1.0
```

## Mentre il bot è offline

```text
TP1 fillato su Bybit
```

## Al riavvio

### Reconciliation executions
Trova:

```text
TP1 execution qty=0.5
```

Genera:

```text
TP_FILLED
```

### Lifecycle
Aggiorna:

```text
open_position_qty = 0.5
closed_position_qty = 0.5
state = PARTIALLY_CLOSED
```

Se policy prevista:

```text
MOVE_STOP_TO_BREAKEVEN
```

### Reconciliation protective
Controlla che:

```text
SL qty = 0.5
TP2 qty = 0.5
```

Se non coincide:

```text
SYNC_PROTECTIVE_ORDERS
```

---

# 15. Formula finale

```text
Reconciliation =
  confronto DB ↔ exchange
  + recupero eventi persi
  + riallineamento sicuro
  + review dei casi ambigui
```

Oppure, in modo ancora più semplice:

```text
Il bot confronta il proprio diario con quello dell’exchange.
Corregge ciò che può dimostrare.
Segnala ciò che non può dimostrare.
```

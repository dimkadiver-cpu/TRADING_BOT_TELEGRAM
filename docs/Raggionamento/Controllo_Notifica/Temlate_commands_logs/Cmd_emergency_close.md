# Template — /close_all · /close · /cancel_all

Tutti i comandi distruttivi seguono il pattern:
1. Preview con lista chains/ordini + inline keyboard [✅ Conferma] [❌ Annulla]
2. Click Conferma → bot edita lo stesso messaggio con risultato ESEGUITO
3. Click Annulla → bot edita lo stesso messaggio con ANNULLATO
4. Pending scade dopo 5 min senza risposta — **lazy deletion**: se l'utente clicca dopo la scadenza,
   il bot cancella il messaggio preview e risponde al callback `"⏱ Azione scaduta — reinvia il comando."`

---

## /close_all

### Step 1 — Preview (messaggio con keyboard inline)

```
🚨 CLOSE ALL — demo_1
────────────────
Posizioni da chiudere: 3

#5  📈 BTCUSDT   LONG    OPEN
#7  📉 ETHUSDT   SHORT   OPEN
#9  📈 SOLUSDT   LONG    PARTIALLY_CLOSED

⚠️ Verranno inviati ordini MARKET di chiusura.

Confermi?
[✅ Conferma]  [❌ Annulla]
```

### Step 2a — Eseguito

```
🚨 CLOSE ALL — demo_1
────────────────
#5  📈 BTCUSDT   LONG
#7  📉 ETHUSDT   SHORT
#9  📈 SOLUSDT   LONG

✅ ESEGUITO — 14:32:10
3 comandi MARKET_CLOSE inseriti.
⚡ Monitorare con /trades
```

### Step 2b — Annullato

```
🚨 CLOSE ALL — demo_1
────────────────
#5  📈 BTCUSDT   LONG
#7  📉 ETHUSDT   SHORT
#9  📈 SOLUSDT   LONG

❌ ANNULLATO — 14:32:08
Nessuna azione eseguita.
```

---

### /close_all trader_a — Step 1

```
🚨 CLOSE ALL — demo_1 · trader_a
────────────────
Posizioni da chiudere: 1

#5  📈 BTCUSDT   LONG    OPEN

⚠️ Verranno inviati ordini MARKET di chiusura.

Confermi?
[✅ Conferma]  [❌ Annulla]
```

---

### Nessuna posizione aperta

```
🚨 CLOSE ALL — demo_1
────────────────
Nessuna posizione aperta da chiudere.
```

> Nessuna conferma richiesta — risposta immediata senza keyboard.

---

## /close

### Sintassi

```
/close BTCUSDT
/close trader_a BTCUSDT
```

### Step 1 — Preview (singola chain)

```
🚨 CLOSE — demo_1
────────────────
Posizione da chiudere:

#5  📈 BTCUSDT   LONG    OPEN
    Entry: 63,500  |  PnL: +12.40 USDT

⚠️ Verrà inviato un ordine MARKET di chiusura.

Confermi?
[✅ Conferma]  [❌ Annulla]
```

### Step 2a — Eseguito

```
🚨 CLOSE — demo_1
────────────────
#5  📈 BTCUSDT   LONG

✅ ESEGUITO — 14:32:10
1 comando MARKET_CLOSE inserito.
⚡ Monitorare con /trade #5
```

### Step 2b — Annullato

```
🚨 CLOSE — demo_1
────────────────
#5  📈 BTCUSDT   LONG

❌ ANNULLATO — 14:32:08
```

---

### Simbolo non trovato (o non aperto nel scope)

```
🚨 CLOSE — demo_1
────────────────
XYZUSDT: nessuna posizione aperta trovata.
```

---

### Più chain aperte sullo stesso simbolo (edge case)

```
🚨 CLOSE — demo_1
────────────────
Trovate 2 posizioni su BTCUSDT:

#5  📈 BTCUSDT   LONG    OPEN
    Entry: 63,500  |  PnL: +12.40 USDT
#11 📉 BTCUSDT   SHORT   OPEN
    Entry: 64,200  |  PnL: -5.10 USDT

⚠️ Verranno chiuse entrambe.

Confermi?
[✅ Conferma]  [❌ Annulla]
```

Step 2a eseguito (multi):

```
🚨 CLOSE — demo_1
────────────────
#5  📈 BTCUSDT   LONG
#11 📉 BTCUSDT   SHORT

✅ ESEGUITO — 14:32:10
2 comandi MARKET_CLOSE inseriti.
⚡ Monitorare con /trades
```

---

## /cancel_all

### Step 1 — Preview

```
🛑 CANCEL ALL — demo_1
────────────────
Ordini entry in attesa: 4

#2   📈 NEARUSDT  LONG    WAITING_ENTRY
#4   📉 ZECUSDT   SHORT   WAITING_ENTRY
#6   📈 SOLUSDT   LONG    WAITING_ENTRY
#8   📉 BNBUSDT   SHORT   WAITING_ENTRY

Posizioni aperte non toccate: 2

Confermi la cancellazione?
[✅ Conferma]  [❌ Annulla]
```

### Step 2a — Eseguito

```
🛑 CANCEL ALL — demo_1
────────────────
#2   NEARUSDT  LONG
#4   ZECUSDT   SHORT
#6   SOLUSDT   LONG
#8   BNBUSDT   SHORT

✅ ESEGUITO — 14:33:01
4 ordini WAITING_ENTRY cancellati.
Posizioni aperte non toccate: 2
/trades per verificare.
```

### Step 2b — Annullato

```
🛑 CANCEL ALL — demo_1
────────────────
#2   NEARUSDT  LONG
#4   ZECUSDT   SHORT
#6   SOLUSDT   LONG
#8   BNBUSDT   SHORT

❌ ANNULLATO — 14:33:00
```

---

### /cancel_all trader_a

```
🛑 CANCEL ALL — demo_1 · trader_a
────────────────
Ordini entry in attesa: 2

#2   📈 NEARUSDT  LONG    WAITING_ENTRY
#6   📈 SOLUSDT   LONG    WAITING_ENTRY

Posizioni aperte non toccate: 1

Confermi la cancellazione?
[✅ Conferma]  [❌ Annulla]
```

---

### Nessun ordine in attesa

```
🛑 CANCEL ALL — demo_1
────────────────
Nessun ordine WAITING_ENTRY da cancellare.
```

---

## Note generali

| Caso | Comportamento |
|---|---|
| Nessuna chain nel scope | Risposta immediata, nessuna keyboard |
| Pending scaduto (5 min) — lazy deletion | Al click: bot cancella il messaggio preview + risponde callback `"⏱ Azione scaduta — reinvia il comando."` |
| Nessun timer in background | La scadenza è gestita solo al momento del click, non proattivamente |
| Gateway offline al momento dell'esecuzione | Comandi inseriti in PENDING comunque — eseguiti quando gateway torna online |

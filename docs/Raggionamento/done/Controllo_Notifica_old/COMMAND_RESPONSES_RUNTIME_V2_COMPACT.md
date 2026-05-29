# Command Responses — Runtime V2 Telegram Control Plane

Versione: 1.0  
Scope: risposte ai comandi Telegram nel topic `COMMANDS`  
Non sostituisce `CLEAN_LOG`: qui si mostrano snapshot operative, non eventi storici.

---

## 1. Principio generale

Le risposte ai comandi devono essere brevi, leggibili e orientate alla decisione.

Separazione:

```text
CLEAN_LOG  → eventi/milestone del ciclo vita trade
COMMANDS   → snapshot richiesta dall'operatore
TECH_LOG   → diagnostica tecnica, warning, errori
```

Quindi:

```text
/status  → salute bot + conteggi + rischi principali
/trades  → lista compatta trade aperti
/control → blocchi e permessi operativi
/trade #id → dettaglio singola chain
/reviews → casi bloccati o da decidere
```

---

## 2. Regole comuni per tutte le risposte comando

### 2.1 Formato

Usare messaggi compatti:

```text
<TITOLO>
────────────────
<sezioni brevi>

Commands:
/comando1
/comando2
```

### 2.2 Cosa evitare

Non inserire nelle risposte rapide:

```text
- JSON/debug
- traceback
- execution command id tecnici
- order id exchange lunghi
- raw message id
- parser confidence
- storico completo lifecycle
- lista completa degli eventi
```

I dettagli vanno in:

```text
/trade #id
/reviews
/logs
TECH_LOG
DB audit
```

### 2.3 Aggiornamento temporale

La riga `Updated:` è utile per `/status` e `/trades`, ma può essere omessa da `/control` se si vuole massima compattezza.

Formato consigliato:

```text
Updated: 14:32:10
```

---

# 3. `/trades`

## 3.1 Obiettivo

Mostrare rapidamente tutti i trade aperti o rilevanti.

Domanda a cui risponde:

```text
Quali trade sono aperti ora e quali richiedono attenzione?
```

## 3.2 Versione compatta standard

```text
📊 OPEN TRADES — 7 active
────────────────
Updated: 14:32:10
Control: ACTIVE | Exchange: OK | Sync: 4s ago

Total:
Unrealized: +128.40 USDT
Fees today: 7.25 USDT
Funding today: -1.80 USDT

────────────────
#145 BTC LONG  | +83.20 USDT | +3.19% | SL: BE     | TP: 1/2
#148 ETH LONG  | +31.50 USDT | +1.32% | SL: 3,280  | TP: 0/3 | Pend: E2
#151 SOL SHORT | +13.70 USDT | +1.50% | SL: 176.00 | TP: 0/2
#155 XRP LONG  | -4.80 USDT  | -0.60% | SL: 0.512  | TP: 0/2
#160 ADA SHORT | +5.20 USDT  | +0.90% | SL: BE     | TP: 1/3

────────────────
Warnings: 2
⚠️ #148 pending limit entry
⚠️ #151 SL not at BE

Use:
/trade #id for details
/reviews for blocked cases
```

## 3.3 Campi per riga

Formato riga:

```text
#chain_id SYMBOL SIDE | PnL USDT | ROI % | SL | TP progress | optional flags
```

Esempio:

```text
#145 BTC LONG | +83.20 USDT | +3.19% | SL: BE | TP: 1/2
```

## 3.4 Flag ammessi

Usare solo flag brevi:

```text
Pend: E2   → entry limit ancora pendente
NoSL       → SL mancante
Risk       → rischio operativo
Review     → richiede controllo
Sync?      → dato exchange non fresco
```

## 3.5 Ordinamento consigliato

Non ordinare alfabeticamente.

Ordine:

```text
1. trade con warning/rischio
2. trade in perdita
3. trade aperti normali
4. partially closed
5. waiting entry / pending setup
```

## 3.6 Troppi trade

```text
0 trade:
→ messaggio vuoto chiaro

1-10 trade:
→ mostra tutti

>10 trade:
→ mostra top 10 per rischio/importanza
→ aggiungi "Showing 10/24"
```

Esempio:

```text
📊 OPEN TRADES — 24 active
────────────────
Updated: 14:32:10
Showing: 10/24, sorted by risk

#151 SOL SHORT | -42.10 USDT | -3.20% | SL: 176 | TP: 0/2 | Risk
#148 ETH LONG  | -18.30 USDT | -0.80% | SL: 3,280 | TP: 0/3 | Pend: E2
...

Use:
/trades 2 for next page
/trade #id for details
```

## 3.7 Cosa non mostrare in `/trades`

```text
- dettagli entry completi
- tutti gli ordini exchange
- messaggi originali
- lifecycle completo
- execution command ids
- JSON/debug
```

---

# 4. `/status`

## 4.1 Obiettivo

Mostrare salute generale del bot, non la lista dei trade.

Domanda a cui risponde:

```text
Il bot è sano? Sta lavorando? Può aprire nuovi trade? Ci sono problemi da guardare?
```

## 4.2 Versione standard

```text
🟢 Runtime V2 — STATUS
────────────────
Updated: 14:32:10

Mode:
New entries: ENABLED
Control: none
Exchange: OK
Sync: fresh, 4s ago

Workers:
Parser: OK
Lifecycle: OK
Execution: OK
Exchange sync: OK
Notifications: OK

Trades:
Open: 7
Waiting entry: 2
Partial: 1
Review required: 1

Execution:
Pending commands: 2
Failed commands: 0
Rejected last hour: 1

Risk:
No SL: 0
SL not at BE: 3
Reconciliation warnings: 1

PnL:
Unrealized: +128.40 USDT
Fees today: 7.25 USDT
Funding today: -1.80 USDT

Use:
/trades
/reviews
/control
```

## 4.3 Versione con warning

```text
🟡 Runtime V2 — STATUS
────────────────
Updated: 14:32:10

Mode:
New entries: ENABLED
Control: none
Exchange: OK
Sync: stale, 48s ago

Workers:
Parser: OK
Lifecycle: OK
Execution: OK
Exchange sync: WARNING
Notifications: OK

Trades:
Open: 7
Waiting entry: 2
Partial: 1
Review required: 3

Execution:
Pending commands: 5
Failed commands: 1
Rejected last hour: 2

Risk:
No SL: 1
SL not at BE: 3
Reconciliation warnings: 2

PnL:
Unrealized: +128.40 USDT
Fees today: 7.25 USDT
Funding today: -1.80 USDT

Attention:
⚠️ #151 missing SL
⚠️ Exchange sync stale
⚠️ 3 reviews required

Use:
/trades
/reviews
/control
```

## 4.4 Semaforo status

```text
🟢 OK
- workers attivi
- exchange sync fresca
- nessun trade senza SL
- nessun errore critico

🟡 WARNING
- sync vecchia
- review required
- failed commands
- reconciliation warning
- trade con SL non ideale

🔴 CRITICAL
- exchange offline
- execution worker fermo
- trade aperto senza SL
- adapter error persistente
- DB non accessibile
```

## 4.5 Cosa non mostrare in `/status`

```text
- lista completa trade
- dettagli entry/TP/SL per ogni chain
- link ai messaggi originali
- lifecycle event history
- order id exchange
- JSON/debug
```

---

# 5. `/control`

## 5.1 Obiettivo

Mostrare solo i blocchi operativi e l’effetto pratico sul runtime.

Domanda a cui risponde:

```text
Il runtime può aprire nuovi trade?
Ci sono blocchi attivi?
Le posizioni aperte vengono ancora gestite?
```

## 5.2 Versione minimale — nessun blocco

```text
🛡️ CONTROL

New entries: ENABLED
Open positions: managed
Updates: processed

Active blocks: none

Commands:
/pause
/resume
```

## 5.3 Versione minimale — pausa globale

```text
🛡️ CONTROL

New entries: BLOCKED
Open positions: managed
Updates: processed

Active block:
GLOBAL — BLOCK_NEW_ENTRIES

Effect:
New signals go to REVIEW_REQUIRED.

Commands:
/resume
/status
/trades
```

## 5.4 Versione minimale — blocco parziale

```text
🛡️ CONTROL

New entries: PARTIALLY BLOCKED
Open positions: managed
Updates: processed

Active blocks:
trader_b
BTC/USDT

Effect:
Matching signals go to REVIEW_REQUIRED.

Commands:
/pause
/resume
/status
```

## 5.5 Versione futura — modalità severa

Non inclusa nel MVP, ma il formato può già prevederla.

```text
🛡️ CONTROL

New entries: BLOCKED
Execution: BLOCKED
Open positions: check required

Active mode:
FULL_STOP

Effect:
No new execution commands are allowed.

Commands:
/status
/reviews
```

## 5.6 Campi da tenere

```text
New entries
Open positions
Updates
Active blocks
Effect
Commands
```

## 5.7 Campi da togliere da `/control`

```text
Created by
Created at
Reason dettagliata
ID controllo
Scope tecnico
Mode tecnico ripetuto
Effective rule
Lista lunga controlli
```

Questi dettagli possono stare in un futuro:

```text
/control_detail
```

oppure nel DB audit.

---

# 6. `/trade #id`

## 6.1 Obiettivo

Mostrare il dettaglio operativo di una singola chain.

Uso:

```text
/trade #145
```

## 6.2 Template consigliato

```text
📌 TRADE #145
────────────────
BTC/USDT — 📈 LONG
Trader: trader_a

Position:
Avg entry: 65,020
Mark: 67,100
Size: 0.004 BTC
Unrealized PnL: +83.20 USDT / +3.19%

Protection:
SL: 65,020 BE

Targets:
TP_1: 68,000 — filled 50%
TP_2: 71,000 — pending 50%

Orders:
Entry_1: filled
Entry_2: cancelled
SL: active
TP_2: active

Costs:
Fees: 2.10 USDT
Funding: -0.35 USDT

Last events:
14:10 Entry filled
14:25 TP1 filled
14:25 SL moved to BE

Original:
https://t.me/c/3927267771/206
```

## 6.3 Cosa può contenere `/trade #id`

A differenza di `/trades`, qui sono accettabili più dettagli:

```text
- entry originali e correnti
- stato target
- ordini attivi/cancellati
- costi
- ultimi eventi principali
- link al messaggio originale
```

Non deve comunque diventare debug tecnico.

---

# 7. `/reviews`

## 7.1 Obiettivo

Mostrare casi che richiedono attenzione manuale.

## 7.2 Template compatto

```text
⚠️ REVIEWS — 3 required
────────────────
Updated: 14:32:10

#151 SOL LONG | missing SL | action required
#166 BTC SHORT | ambiguous update | parser review
#170 ETH LONG | order rejected | exchange review

Use:
/trade #id for details
/control for pause/resume
```

---

# 8. Decisione finale

La separazione definitiva è:

```text
/status  = salute bot + conteggi + rischi principali
/trades  = trade aperti, formato compatto
/control = permessi/blocchi operativi, formato minimale
/trade #id = dettaglio singola chain
/reviews = casi bloccati o da decidere
```

Regola chiave:

```text
Le risposte ai comandi non sono CLEAN_LOG.
Sono snapshot operative generate su richiesta dell'utente.
```

---

# 9. Acceptance criteria

La feature è accettata quando:

```text
1. /trades mostra una lista compatta dei trade aperti.
2. /trades non mostra dettagli tecnici o storico lifecycle.
3. /status mostra salute bot, workers, conteggi e rischi principali.
4. /status non duplica /trades.
5. /control mostra solo blocchi operativi e loro effetto.
6. /control è leggibile in pochi secondi.
7. /trade #id mostra dettaglio singola chain.
8. /reviews mostra solo casi da controllare.
9. Tutte le risposte sono inviate nel topic COMMANDS.
10. Nessuna risposta comando viene scritta come evento CLEAN_LOG.
```

# Dashboard Telegram — `/dashboard`

Messaggio pinnabile con inline keyboard.
Esiste **un solo dashboard attivo per chat + topic**.

Il dashboard viene creato tramite `/dashboard` esclusivamente da:

* topic `clean_log`, inclusi topic trader e fallback;
* topic `commands`;
* mai da `tech_log`.

Il messaggio viene aggiornato tramite `edit_message_text`; nessun messaggio aggiuntivo viene inviato durante i refresh normali.

---

## Scope

Lo scope viene determinato una sola volta alla creazione e non cambia tramite callback.

| Origine comando                  | Scope dashboard             |
| -------------------------------- | --------------------------- |
| `clean_log` trader_a, thread 316 | Solo `trader_a`             |
| `clean_log` trader_b, thread 318 | Solo `trader_b`             |
| `clean_log` fallback, thread 2   | Tutti i trader dell’account |
| `commands` demo_1, thread 4      | Tutti i trader di `demo_1`  |
| `tech_log`                       | Comando rifiutato           |

Risposta in `tech_log`:

```text
Comando non disponibile in questo topic.
```

Nei dashboard con scope account, ogni trade mostra `[trader_id]`.

---

## Stato persistito

Tabella consigliata: `ops_dashboards`.

```text
chat_id
thread_id
dashboard_message_id
scope_type              # trader | account
account_id
trader_id               # nullable se scope account
current_view            # active | closed | blocked | pnl | stats
current_page             # integer, default 0
last_edit_at
pending_refresh
is_active
created_at
updated_at
```

Vincolo univoco:

```text
(chat_id, thread_id, is_active = true)
```

`thread_id = 0` rappresenta la chat senza topic.

---

## Regole di aggiornamento

Il dashboard viene aggiornato quando:

* viene cliccata una vista;
* viene cliccato `🔄 Refresh`;
* viene cliccato un controllo di paginazione;
* cambia il lifecycle di un trade appartenente allo scope;
* arriva uno snapshot posizione/account che modifica dati visibili.

Gli snapshot aggiornano automaticamente solo le viste:

```text
Attivi
PnL
```

Un cambio lifecycle aggiorna tutte le viste, perché può modificare conteggi, PnL realizzato, statistiche e paginazione.

Regole operative:

```text
- minimo 5 secondi tra due edit dello stesso dashboard;
- eventi durante cooldown vengono coalesciti, non scartati;
- MessageNotModified viene ignorato;
- dashboard cancellato/non modificabile -> record marcato inactive;
- pagina fuori range dopo refresh -> clamp all’ultima pagina disponibile;
- cambio vista -> current_page = 0;
- refresh manuale mantiene vista e pagina correnti;
- ogni edit riattacca sempre la keyboard.
```

---

## Keyboard

Vista selezionata indicata con `•`.

```text
[•⚡ Attivi]   [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]       [📉 Stats]   [🔄 Refresh]
```

La riga di paginazione appare solo nelle viste `Attivi`, `Chiusi`, `Bloccati` e solo se il totale trade è maggiore di 5.

Pagina iniziale:

```text
[Pagina 1/3]  [Succ →]
```

Pagina intermedia:

```text
[← Prec]  [Pagina 2/3]  [Succ →]
```

Ultima pagina:

```text
[← Prec]  [Pagina 3/3]
```

`Pagina N/M` usa `callback_data = "noop"` e deve rispondere alla callback senza modificare il messaggio.

---

## Categorie

### Attivi

Include trade chain ancora operative:

```text
WAITING_ENTRY
PARTIALLY_FILLED
OPEN
PARTIALLY_CLOSED
```

### Chiusi

Include chain terminali.  // da vedere le categorie usate in codice

```text
CLOSED
TP_COMPLETE
SL_HIT
MANUAL_CLOSE
EXCHANGE_CLOSE
CANCELLED_UNFILLED
```

`CANCELLED_UNFILLED` deve mostrare chiaramente che il trade non è mai entrato e non deve entrare in Win Rate, Best/Worst o PnL realizzato.

### Bloccati

Include chain che richiedono intervento:

```text
REVIEW_REQUIRED
EXEC_FAILED
RECONCILIATION_REQUIRED
REJECTED  (regettati dalla polycu)
```

---

# Template messaggi

## Dashboard appena creato — trader scope

```text
📊 Dashboard — demo_1 · trader_a
────────────────────────
Aggiornato: 14:32:05

Seleziona una vista o pinna questo messaggio.
```

## Dashboard appena creato — account scope

```text
📊 Dashboard — demo_1
────────────────────────
Aggiornato: 14:32:05

Seleziona una vista o pinna questo messaggio.
```

---

## Vista Attivi — trader scope

```text
⚡ Attivi — demo_1 · trader_a
────────────────────────
Update: 14:32:05 · Snapshots positions: 18s 

#5  BTC/USDT  LONG  PARTIALLY_CLOSED
Origine: Segnale

Entry: 63,500 ✓ · 63,200 × · 62,800 ×
TP:    64,000 ✓ · 65,200 · 66,500
SL:    62,000 · BE: sì
uPnL:  +34.20 USDT

Azioni: /trade 5 · /cancel 5 · /close 5 // vedere come acetta il sistema di comando
- - - - - - - - - - - - - - - - - - - -

#9  SOL/USDT  LONG  WAITING_ENTRY
Origine: Segnale

Entry: 148.50 · 147.00
TP:    155.00 · 160.00
SL:    143.00
State: In attesa di fill

Action: /trade 9 · /cancel 9 · /close 9
```

`Segnale` è un hyperlink Telegram verso il messaggio originale. Se il link non esiste, la riga viene omessa.

Legenda:

```text
✓ = filled
× = cancelled
nessun simbolo = pending
```

`uPnL` significa sempre Unrealized PnL, non PnL realizzato.

---

## Vista Attivi — account scope

```text
📊 ⚡ Attivi — demo_1
────────────────────────
Aggiornato: 14:32:05 · Snapshot posizioni: 18s fa

#5  BTC/USDT  LONG   OPEN  [trader_a]
Entry: 63,500 ✓
TP:    64,000
SL:    62,800 · BE: sì
uPnL:  +12.40 USDT
Origine: Segnale
- - - - - - - - - - - - - - - - - - - -

#7  ETH/USDT  SHORT  OPEN  [trader_b]


Entry: 2,140 ✓
TP:    2,000
SL:    2,180
uPnL:  -3.20 USDT
Origine: Segnale
```

---

## Vista Attivi — nessun trade

```text
⚡ Attivi — demo_1 · trader_a
────────────────────────
Aggiornato: 14:32:05

Nessun trade attivo.
```

---

## Snapshot stale

La soglia deve essere configurabile, ad esempio:

```text
position_snapshot_stale_after_seconds: 90
```

Esempio:

```text
📊 ⚡ Attivi — demo_1 · trader_a
────────────────────────
Aggiornato: 14:32:05 · Snapshot posizioni: 183s fa ⚠️

#5  BTC/USDT  LONG  OPEN
Entry: 63,500 ✓
TP:    64,000
SL:    62,800
uPnL:  +12.40 USDT
```

Il warning segnala solo dati potenzialmente non aggiornati; non implica che il trade sia in errore.

---

## Vista Chiusi

```text
📊 ✅ Chiusi — demo_1 · trader_a
────────────────────────
Aggiornato: 14:32:05

#22  BTC/USDT  LONG  CLOSED
Motivo: STOP_LOSS
Aperto: 14 Jun 11:52 · Segnale
Chiuso: 14 Jun 14:26 · Chiusura
PnL netto: -3.20 USDT · ⏱ 2h 34m
- - - - - - - - - - - - - - - - - - - -

#18  SOL/USDT  LONG  CLOSED
Motivo: TP_COMPLETE
Aperto: 14 Jun 09:10 · Segnale
Chiuso: 14 Jun 13:55 · Chiusura
PnL netto: +34.50 USDT · ⏱ 4h 45m
```

`Motivo` non deve mai essere vuoto. Fallback:

```text
Motivo: UNKNOWN
```

Trade `CANCELLED_UNFILLED`:

```text
#24  ETH/USDT  LONG  CANCELLED_UNFILLED
Motivo: CANCEL_PENDING
Creato: 14 Jun 16:12 · Segnale
PnL: — · Nessun fill
```

---

## Vista Bloccati

```text
📊 🚫 Bloccati — demo_1 · trader_a
────────────────────────
Aggiornato: 14:32:05

#7  ETH/USDT  LONG  REVIEW_REQUIRED
Motivo: missing_sl
Bloccato: 14 Jun 11:52
Origine: Segnale
- - - - - - - - - - - - - - - - - - - -

#12  SOL/USDT  LONG  EXEC_FAILED
Motivo: insufficient_margin
Bloccato: 14 Jun 14:26
Origine: Errore tecnico
```

Per `REVIEW_REQUIRED`, il link punta al segnale/clean log.
Per `EXEC_FAILED`, il link punta al messaggio tecnico o al record di errore disponibile.

---

## Vista PnL

```text
📊 💰 PnL — demo_1 · trader_a
────────────────────────
Aggiornato: 14:32:05 · Snapshot account: 18s fa

Account demo_1:
Equity:        10,432.50 USDT
Balance:        9,100.00 USDT
Margin used:      820.00 USDT

Realizzato trader_a:
Gross:          +142.60 USDT
Fees:            -11.20 USDT
Netto:          +130.00 USDT

Open: 1 · Waiting entry: 1
```

Per account scope:

```text
Realizzato tutti i trader:
```

Equity, balance e margin sono sempre account-level.
Gross, fees e netto rispettano invece lo scope dashboard.

Formula:

```text
Netto = Gross - Fees
```

---

## Vista Stats

```text
📊 📉 Stats — demo_1 · trader_a
────────────────────────
Aggiornato: 14:32:05

Periodo        Trade   Win%      Netto
Oggi               1   100%    +18.40
Ultimi 7g          6    67%    +62.10
Ultimi 30g        19    63%   +148.30
Totale            31    61%    +98.20

Best:  #8  SOL/USDT  +34.50 USDT
Worst: #22 BNB/USDT -12.80 USDT
```

Regole statistiche:

```text
- timezone configurabile; default Europe/Rome;
- include solo trade chiusi con almeno un fill;
- Win% = trade con PnL netto > 0 / trade chiusi con PnL netto ≠ 0;
- PnL = realized gross - fees;
- CANCELLED_UNFILLED esclusi;
- Best/Worst ordinati per PnL netto.
```

---

## Limiti tecnici di rendering

```text
- 5 trade per pagina;
- massimo 3.900 caratteri renderizzati;
- se Entry o TP sono troppo lunghi, mostrare i primi valori + “+N”;
- usare sempre display_symbol(), ad esempio BTC/USDT;
- escape HTML per ogni testo proveniente da DB o Telegram;
- disabilitare preview link Telegram.
```

---

## `/dashboard` nello stesso topic

Comportamento corretto:

```text
1. Cerca dashboard attivo per chat_id + thread_id.
2. Se esiste e il messaggio è modificabile:
   - resetta vista a active:0;
   - esegue refresh in-place;
   - non crea un nuovo messaggio.
3. Se il messaggio non esiste o non è modificabile:
   - marca il record precedente inactive;
   - crea nuovo messaggio;
   - salva nuovo dashboard_message_id.
```

Le callback provenienti da una dashboard inattiva devono ricevere solo:

```text
Dashboard non più attiva. Usa /dashboard per crearne una nuova.
```

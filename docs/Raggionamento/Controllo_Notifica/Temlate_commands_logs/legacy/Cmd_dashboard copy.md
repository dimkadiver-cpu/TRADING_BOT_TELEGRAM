# Template — /dashboard

Messaggio inline pinnabile, **uno per topic**.
Creato con `/dashboard` da topic `clean_log` (tutti i thread, incluso fallback) o `commands` (non tech_log).
Aggiornato in-place (edit_message_text) — nessun messaggio nuovo, nessuno spam.

Il scope del dashboard corrisponde al topic da cui è stato creato:
- Da clean_log trader_a (thread 316) → mostra solo trader_a
- Da clean_log trader_b (thread 318) → mostra solo trader_b
- Da clean_log fallback (thread 2)   → mostra tutti i trader dell'account
- Da commands demo_1  (thread 4)     → mostra tutti i trader di demo_1

Contenuto: solo trading — Attivi, Chiusi, Bloccati, PnL, Stats. Niente Status/Health/Control.

Aggiornamenti:  **deve comprendere aggiornamento di poszioni (snapshots delle psosioni) **
- Click su tasto vista
- Click [🔄 Refresh]
- Click [← Prec] / [Succ →] per paginazione
- Automatico a ogni cambio stato lifecycle (fill, TP, SL, close) per trade nel scope

---

## Keyboard

```
[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
[← Prec]  [  Pagina 2/5  ]  [Succ →]   ← riga condizionale
```

**Regola paginazione:** la terza riga appare solo per le viste Attivi / Chiusi / Bloccati
quando il totale trade supera **5**. Pagine da 5 trade.

- `[← Prec]` assente a pagina 0
- `[Succ →]` assente all'ultima pagina
- `[Pagina N/M]` è bottone inerte (`callback_data = "noop"`) — display only

`current_view` in DB codifica `"vista:pagina"` — es. `"attivi:0"`, `"chiusi:2"`, `"bloccati:1"`.
Default alla creazione: `"attivi:0"`.

---

## Creazione — /dashboard da clean_log trader_a (thread 316)

```
📊 DASHBOARD — demo_1 · trader_a
────────────────
Aggiornato: 14:32:05

[seleziona una vista o pinna questo messaggio]

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

## Creazione — /dashboard da commands demo_1 (thread 4)

```
📊 DASHBOARD — demo_1
────────────────
Aggiornato: 14:32:05

[seleziona una vista o pinna questo messaggio]

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

---

## Vista: Attivi — trader_a

```
📊⚡ Attivi
 A— demo_1 · trader_a
────────────────
14:32:05  |  Snapshot: 18s fa

#5  BTC/USDT   LONG   PARTIALLY_CLOSED         niente emojy
    https://t.me/c/4240829081/316/987
                                             **spaziatore **spaziatore una riga
    Entry: 63,500✓ · 63,200✗ · 62,800✗
    TP: 64,000✓ · 65,200 · 66,500
    SL: 62,000  BE: ✓
    PnL: +34.20 USDT
                                          **spaziatore una riga
    /trade #id /cancel_all  /close         **Agiunta dell scorciatoe per azioni del singolo trade 
 - - - - - - - - - - - - - - - - - -      **spaziatore tra trade
#9  SOL/USDT   LONG   WAITING_ENTRY
    https://t.me/c/4240829081/316/1024

    Entry: 148.50 · 147.00
    TP: 155.00 · 160.00
    SL: 143.00
    In attesa di riempimento

    /trade #id /cancel_all /close  
 - - - - - - - - - - - - - - - - - -
[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

> Simboli a destra del prezzo: `✓` = riempita/filled · `✗` = cancellata · senza simbolo = in attesa
> Link = segnale originale su Telegram. Omesso se non disponibile.
> `display_symbol()` usato per formato ETH/USDT.

## Vista: Attivi — account intero (scope tutti i trader)

```
📊 DASHBOARD — demo_1
────────────────
14:32:05  |  Snapshot: 18s fa

#5  BTC/USDT   LONG   OPEN          [trader_a]
    Entry: 63,500✓
    TP: 64,000
    SL: 62,800  BE: ✓
    PnL: +12.40 USDT
    https://t.me/c/4240829081/316/987

#7  ETH/USDT   SHORT  OPEN          [trader_b]
    Entry: 2,140✓
    TP: 2,000
    SL: 2,180
    PnL: -3.20 USDT
    https://t.me/c/4240829081/318/1001

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

## Vista: Attivi — nessun trade

```
📊 DASHBOARD — demo_1 · trader_a
────────────────
14:32:05

Nessun trade attivo.

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

## Vista: Attivi — snapshot stale

```
📊 DASHBOARD — demo_1 · trader_a
────────────────
14:32:05  |  Snapshot: 183s fa  ⚠️

#5  BTC/USDT   LONG   OPEN
    Entry: 63,500✓
    TP: 64,000
    SL: 62,800
    PnL: +12.40 USDT

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

---

## Vista: Chiusi — trader_a (con paginazione)

```
✅ Chiusi — demo_1 · trader_a
────────────────
14:32:05

#22  BTC/USDT   LONG   CLOSED
     Reason:                               **aggiungere resos
     PnL: -3.20 USDT
     Opened: 14 Jun 11:52
     https://t.me/c/4240829081/316/987

     Closed: 14 Jun 14:26
     https://t.me/c/4240829081/316/1043
- - - - - - - -
     PnL: -12.80 USDT   ⏱ 2h 34m

#18  SOL/USDT   LONG   CLOSED
     Opened: 14 Jun 09:10
     https://t.me/c/4240829081/316/901
- - - - - - - -
     Closed: 14 Jun 13:55
     https://t.me/c/4240829081/316/998
- - - - - - - -
     PnL: +34.50 USDT   ⏱ 4h 45m

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
[← Prec]  [  Pagina 1/3  ]  [Succ →]
```

> Link Opened = segnale accettato (clean_log). Link Closed = position closed (clean_log).
> Separatore `- - - - -` generato da `SeparatorBlock()`, larghezza dinamica.
> Link omessi se non disponibili.

## Vista: Chiusi — prima pagina (← Prec assente)

```
[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
            [  Pagina 1/3  ]  [Succ →]
```

## Vista: Chiusi — ultima pagina (Succ → assente)

```
[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
[← Prec]  [  Pagina 3/3  ]
```

## Vista: Chiusi — nessun trade

```
✅ DASHBOARD — demo_1 · trader_a
────────────────
14:32:05

Nessun trade chiuso.

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

---

## Vista: Bloccati — trader_a

```
🚫  Bloccati— demo_1 · trader_a
────────────────
14:32:05

#7   ETH/USDT   LONG   REVIEW_REQUIRED
     Motivo: missing_sl
     14 Jun 11:52
     https://t.me/c/4240829081/316/987

#12  SOL/USDT   LONG   EXEC_FAILED
     Motivo: insufficient_margin
     14 Jun 14:26
     https://t.me/c/4240829081/42/1103

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

> REVIEW_REQUIRED → link segnale originale (clean_log).
> EXEC_FAILED → link messaggio tech_log dell'errore.
> Link omesso se non disponibile.
> `review_reason` da `ops_trade_chains`, `error_message` da `ops_execution_commands`.

## Vista: Bloccati — nessun trade

```
🚫 DASHBOARD — demo_1 · trader_a
────────────────
14:32:05

Nessun trade bloccato.

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

---

## Vista: PnL — trader_a

```
💰 DASHBOARD — demo_1 · trader_a
────────────────
14:32:05

Account:
  Equity:    10,432.50 USDT
  Balance:    9,100.00 USDT
  Margin:       820.00 USDT

Realizzato (trader_a):
  Gross:      +142.60 USDT
  Fees:        -11.20 USDT
  Netto:      +130.00 USDT

Open: 1  |  Waiting: 1

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

## Vista: PnL — account intero

```
💰 DASHBOARD — demo_1
────────────────
14:32:05

Account:
  Equity:    10,432.50 USDT
  Balance:    9,100.00 USDT
  Margin:       820.00 USDT

Realizzato (tutti i trader):
  Gross:      +234.80 USDT
  Fees:        -18.40 USDT
  Netto:      +214.30 USDT

Open: 3  |  Waiting: 2

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

---

## Vista: Stats — trader_a

```
📉 DASHBOARD — demo_1 · trader_a
────────────────
14:32:05

           Trades  Win%    Netto
Oggi:           1   100%  +18.40
7g:             6    67%  +62.10
30g:            19   63% +148.30
Tot:            31   61%  +98.20

Best:  #8  SOLUSDT  +34.50
Worst: #22 BNBUSDT  -12.80

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

---

## Auto-refresh — esempi

### Fill su #5 BTCUSDT (trader_a)

Dashboard in thread 316 (trader_a) e thread 4 (tutti demo_1) vengono aggiornati.
Dashboard in thread 318 (trader_b) non viene toccato.

### Trade chiuso #5 (trader_a)

Dashboard scope trader_a: #5 scompare da Attivi, appare in Chiusi. PnL realizzato aggiornato in PnL/Stats.
Dashboard scope account: idem.

---

## Note

| Comportamento | Dettaglio |
|---|---|
| `MessageNotModified` | Se dati invariati dopo edit → errore Telegram gestito silenziosamente |
| Throttle edit | Min 5s tra edit successivi sullo stesso messaggio. Se arriva evento durante cooldown, edit schedulata dopo — non scartata |
| `/dashboard` stesso topic | Sovrascrive il record — nuovo messaggio, vecchio abbandonato (keyboard smette di funzionare) |
| Scope account intero | Aggiunge `[trader_id]` per riga nelle viste Attivi/Chiusi/Bloccati |
| tech_log topic | `/dashboard` non accettato — risposta "comando non disponibile in questo topic" |
| Keyboard dopo auto-refresh | Sempre ri-allegata dopo ogni edit |
| Paginazione | Terza riga keyboard solo se totale > 5. Reset pagina a 0 su cambio vista |
| `thread_id = 0` | Rappresenta "nessun thread" (private bot mode) nel DB |

# Template — `/dashboard`

Messaggio inline pinnabile, **uno per topic**.
Creato con `/dashboard` da topic `clean_log` o `commands`; non disponibile in `tech_log`.

Aggiornato in-place tramite `edit_message_text`: nessun messaggio aggiuntivo durante refresh, callback o lifecycle update.

---

## Keyboard

```text
[⚡ Active]  [✅ Closed]  [🚫 Blocked]
[💰 PnL]     [📉 Stats]   [🔄 Refresh]
[← Prev]     [Page 2/5]  [Next →]
```

Regole:

* Terza riga visibile solo per `Active`, `Closed`, `Blocked` e solo se i trade sono più di 5.
* `← Prev` assente sulla prima pagina.
* `Next →` assente sull’ultima pagina.
* `Page N/M` è un bottone inerte: `callback_data = "noop"`.
* Cambio vista: reset pagina a `0`.
* Default alla creazione: `active:0`.

---

## Dashboard appena creato — trader scope // apre la prim tabs [⚡ Active]

```text
Dashboard — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

Select a view or pin this message.
```

---

## Dashboard appena creato — account scope

```text
Dashboard — demo_1
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

Select a view or pin this message.
```

---

# View: Active — trader scope

```text
⚡ Active — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05 

#5  BTC/USDT  LONG  PARTIALLY_CLOSED
Source: Signal

Entry: 63,500 ✓ · 63,200 × · 62,800 ×
TP:    64,000 ✓ · 65,200 · 66,500
SL:    62,000 · BE: Yes
uPnL:  +34.20 USDT
rPnL:  +14.20 USDT

Actions: /trade 5 · /cancel 5 · /close 5
- - - - - - - - - - - - - - - - - - - -

#9  SOL/USDT  LONG  WAITING_ENTRY
Source: Signal

Entry: 148.50 · 147.00
TP:    155.00 · 160.00
SL:    143.00
Status: Waiting for fill

Actions: /trade 9 · /cancel 9 · /close 9
```

Legenda:

```text
✓ = Filled
× = Cancelled
nessun simbolo = Pending
```

---

## View: Active — account scope

```text
⚡ Active — demo_1
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05 

#5  BTC/USDT  LONG   OPEN  [trader_a]
Entry: 63,500 ✓
TP:    64,000
SL:    62,800 · BE: Yes
uPnL:  +12.40 USDT
Source: Signal
- - - - - - - - - - - - - - - - - - - -

#7  ETH/USDT  SHORT  OPEN  [trader_b]
Entry: 2,140 ✓
TP:    2,000
SL:    2,180
uPnL:  -3.20 USDT
Source: Signal
```

---

## View: Active — nessun trade

```text
⚡ Active — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

No active trades.
```

---

## Snapshot stale

```text
⚡ Active — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05 

#5  BTC/USDT  LONG  OPEN
Entry: 63,500 ✓
TP:    64,000
SL:    62,800
uPnL:  +12.40 USDT
```

---

# View: Closed

```text
✅ Closed — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

#22  BTC/USDT  LONG  CLOSED
Reason: STOP_LOSS
Opened: 14 Jun 11:52 · Signal
Closed: 14 Jun 14:26 · Close event
Net PnL: -3.20 USDT · ⏱ 2h 34m
- - - - - - - - - - - - - - - - - - - -

#18  SOL/USDT  LONG  CLOSED
Reason: TP_COMPLETE
Opened: 14 Jun 09:10 · Signal
Closed: 14 Jun 13:55 · Close event
Net PnL: +34.50 USDT · ⏱ 4h 45m
```

Trade annullato senza fill:

```text
#24  ETH/USDT  LONG  CANCELLED_UNFILLED
Reason: CANCEL_PENDING
Created: 14 Jun 16:12 · Signal
PnL: — · No fill
```

Nessun trade:

```text
✅ Closed — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

No closed trades.
```

---

# View: Blocked

```text
🚫 Blocked — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

#7  ETH/USDT  LONG  REVIEW_REQUIRED
Reason: missing_sl
Blocked: 14 Jun 11:52
Source: Signal
- - - - - - - - - - - - - - - - - - - -

#12  SOL/USDT  LONG  EXEC_FAILED
Reason: insufficient_margin
Blocked: 14 Jun 14:26
Source: Technical error
```

Nessun trade:

```text
🚫 Blocked — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

No blocked trades.
```

---

# View: PnL

```text
💰 PnL — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05 

Account demo_1:
Equity:        10,432.50 USDT
Balance:        9,100.00 USDT
Margin used:      820.00 USDT

Realized — trader_a:
Gross:          +142.60 USDT
Fees:            -11.20 USDT
Net:            +130.00 USDT

Open: 1 · Waiting entry: 1
```

Per scope account:

```text
Realized — All traders:
```

---

# View: Stats

```text
Stats — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Updated: 14:32:05

Period          Trades   Win%      Net
Today                1   100%   +18.40
Last 7d             6    67%   +62.10
Last 30d           19    63%  +148.30
All time           31    61%   +98.20

Best:  #8  SOL/USDT  +34.50 USDT
Worst: #22 BNB/USDT -12.80 USDT
```

---

## Paginazione

Prima pagina:

```text
[⚡ Active]  [✅ Closed]  [🚫 Blocked]
[💰 PnL]     [📉 Stats]   [🔄 Refresh]
[Page 1/3]   [Next →]
```

Pagina intermedia:

```text
[⚡ Active]  [✅ Closed]  [🚫 Blocked]
[💰 PnL]     [📉 Stats]   [🔄 Refresh]
[← Prev]     [Page 2/3]  [Next →]
```

Ultima pagina:

```text
[⚡ Active]  [✅ Closed]  [🚫 Blocked]
[💰 PnL]     [📉 Stats]   [🔄 Refresh]
[← Prev]     [Page 3/3]
```

---

## Comandi trade

```text
/trade <id>      # mostra dettaglio trade
/cancel <id>     # cancella gli entry pending del trade
/close <id>      # richiede chiusura del trade
```

Non usare `/cancel_all` nella card di un singolo trade: è ambiguo e rischia di agire su più chain.

---

## Risposta da tech_log

```text
Command is not available in this topic.
```

## Callback da dashboard non più attiva

```text
Dashboard is no longer active. Use /dashboard to create a new one.
```
## Filter system

Il dashboard ha due livelli distinti:

```text
Scope dashboard
→ limite massimo dei dati visibili, deciso con /dashboard.

Filters
→ restringono temporaneamente i dati dentro lo scope.
```

Esempio:

```text
Dashboard scope: demo_1, all traders
Global trader filter: trader_a
Active filter: Open

Risultato:
mostra solo i trade OPEN di trader_a nell’account demo_1.
```

Un dashboard creato nel topic `trader_a` ha scope fisso su `trader_a`; il filtro trader non viene mostrato.

---

## Main keyboard

```text
[⚡ Active]  [✅ Closed]  [🚫 Blocked]
[💰 PnL]     [📉 Stats]   [🔄 Refresh]
 [🔎 Filters     ] [🧹 Clear]
[← Prev]     [Page 2/5]  [Next →]
```

Regole:

```text
- 🔎 Filters apre il pannello filtri della vista corrente.
- 🧹 Clear rimuove tutti i filtri, incluso il trader filter.
- Il trader filter è globale: resta applicato passando tra le tab.
- Gli altri filtri sono specifici per tab.
- Modifica di un filtro → current_page = 0.
- Refresh mantiene i filtri correnti.
- La paginazione viene calcolata dopo l’applicazione dei filtri.
- La riga di paginazione appare solo nelle viste Active, Closed e Blocked.
```

Quando almeno un filtro è attivo, sotto l’header:

```text
Filters: trader_a · Open · Long
```

---

## Filter panel — Active

```text
🔎 Filters — Active
- - - - - - - - - - - - - - - - - - - -
Trader: All traders
Status: All statuses
Side: All sides

[Trader ▸]  [Status ▸]  [Side ▸]
[🧹 Clear view]  [← Back]
```

### Trader

Disponibile solo nello scope account.

```text
[All traders]
[trader_a]  [trader_b]  [trader_c]
[← Back]
```

Se i trader sono troppi, il selettore trader è paginato.

### Status

```text
[All statuses]
[Waiting entry]      [Partially filled]
[Open]               [Partially closed]
[Closing]
[← Back]
```

Mapping canonico:

```text
Waiting entry      -> WAITING_ENTRY
Partially filled   -> PARTIALLY_FILLED
Open               -> OPEN
Partially closed   -> PARTIALLY_CLOSED
Closing            -> CLOSE_PENDING
```

### Side

```text
[All sides]  [Long]  [Short]
[← Back]
```

---

## Filter panel — Closed

```text
🔎 Filters — Closed
- - - - - - - - - - - - - - - - - - - -
Trader: All traders
Exit: All exits
Period: All time

[Trader ▸]  [Exit ▸]  [Period ▸]
[🧹 Clear view]  [← Back]
```

### Exit

```text
[All exits]
[Take profit]      [Stop loss]
[Manual close]     [Exchange close]
[Cancelled no fill]
[Other]
[← Back]
```

Mapping basato su `close_reason`, non sul solo lifecycle:

```text
Take profit         -> TP_COMPLETE
Stop loss           -> SL_HIT | STOP_LOSS
Manual close        -> MANUAL_CLOSE
Exchange close      -> EXCHANGE_CLOSE
Cancelled no fill   -> CANCELLED_UNFILLED
Other               -> UNKNOWN | altri motivi terminali
```

### Period

```text
[All time]  [Today]  [Last 7d]
[Last 30d]  [This month]
[← Back]
```

Il periodo viene calcolato sulla data di chiusura.
`CANCELLED_UNFILLED` appare in `Closed`, ma resta escluso da PnL netto, Win Rate, Best e Worst.

---

## Filter panel — Blocked

```text
🔎 Filters — Blocked
- - - - - - - - - - - - - - - - - - - -
Trader: All traders
Type: All types
Age: Any age

[Trader ▸]  [Type ▸]  [Age ▸]
[🧹 Clear view]  [← Back]
```

### Type

```text
[All types]
[Review required]
[Execution failed]
[Reconciliation required]
[← Back]
```

Mapping:

```text
Review required         -> REVIEW_REQUIRED
Execution failed        -> EXEC_FAILED
Reconciliation required -> RECONCILIATION_REQUIRED
```

### Age

```text
[Any age]  [Last hour]  [Last 24h]
[Older than 24h]
[← Back]
```

L’età si calcola da `blocked_at`; fallback su `updated_at`.

---

## Filter panel — PnL

```text
🔎 Filters — PnL
- - - - - - - - - - - - - - - - - - - -
Trader: All traders
Period: All time

[Trader ▸]  [Period ▸]
[🧹 Clear view]  [← Back]
```

Valori `Period`:

```text
[All time]  [Today]  [Last 7d]
[Last 30d]  [This month]
[← Back]
```

Regole PnL:

```text
- Equity, Balance e Margin used restano sempre account-level.
- Gross, Fees, Net, Open e Waiting rispettano trader e periodo selezionati.
- Se è attivo un filtro trader, il titolo deve indicarlo chiaramente.
```

Esempio:

```text
💰 PnL — demo_1
- - - - - - - - - - - - - - - - - - - -
Filters: trader_a · Last 7d

Account snapshot:
Equity:        10,432.50 USDT
Balance:        9,100.00 USDT
Margin used:      820.00 USDT

Realized — trader_a · Last 7d:
Gross:          +142.60 USDT
Fees:            -11.20 USDT
Net:            +130.00 USDT
```

---

## Filter panel — Stats

```text
🔎 Filters — Stats
- - - - - - - - - - - - - - - - - - - -
Trader: All traders
Side: All sides

[Trader ▸]  [Side ▸]
[🧹 Clear view]  [← Back]
```

Per `Stats`, non inserire un filtro periodo: la tab mostra già contemporaneamente `Today`, `Last 7d`, `Last 30d` e `All time`.

Il filtro `Side` è coerente:

```text
[All sides]  [Long]  [Short]
[← Back]
```

Esempio:

```text
📉 Stats — demo_1
- - - - - - - - - - - - - - - - - - - -
Filters: trader_a · Long

Period          Trades   Win%      Net
Today                1   100%   +18.40
Last 7d             6    67%   +62.10
Last 30d           19    63%  +148.30
All time           31    61%   +98.20
```

Non introdurre filtri `Wins only` o `Losses only` in questa tab: farebbero diventare il Win Rate artificialmente 100% o 0%.

---

## Stato DB

```text
current_view
current_page

filter_trader_id              # globale; null = tutti nello scope
active_filter_status          # null = tutti
active_filter_side            # null = tutti

closed_filter_exit_reason     # null = tutti
closed_filter_period          # null = all_time

blocked_filter_type           # null = tutti
blocked_filter_age            # null = any_age

pnl_filter_period             # null = all_time

stats_filter_side             # null = tutti
```

Alternativa più pulita: un singolo JSON versionato.

```json
{
  "trader_id": null,
  "active": {
    "status": null,
    "side": null
  },
  "closed": {
    "exit_reason": null,
    "period": "all_time"
  },
  "blocked": {
    "type": null,
    "age": "any_age"
  },
  "pnl": {
    "period": "all_time"
  },
  "stats": {
    "side": null
  }
}
```

---

## Auto-refresh con filtri

```text
1. Arriva un evento lifecycle o uno snapshot.
2. Vengono ricaricati i dati nel dashboard scope.
3. Vengono applicati i filtri correnti.
4. Vengono ricalcolati testo, totale pagine e keyboard.
5. Se il rendering non cambia, nessun edit Telegram.
6. Se la pagina non esiste più, viene portata all’ultima pagina valida.
```

Un evento non appartenente ai filtri attivi non deve causare un edit inutile, salvo che modifichi conteggi o contenuti realmente visibili.

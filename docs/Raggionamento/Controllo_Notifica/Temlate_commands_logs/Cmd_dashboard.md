# `/dashboard` — Template e funzionalita`

## Scopo

`/dashboard` e` la vista operativa compatta del topic corrente.

Serve a:

- vedere subito cosa richiede attenzione;
- navigare tra `Active`, `Closed`, `Blocked`, `PnL`, `Stats`;
- applicare filtri e paginazione senza generare spam;
- aprire il dettaglio completo di un trade con `/trade n`.

`/dashboard` non sostituisce `/trade n`.
Il dashboard riassume. Il dettaglio audit e` delegato a `/trade n`.

---

## Principi UI

- un solo dashboard attivo per topic;
- stesso messaggio Telegram aggiornato in-place;
- header compatto e uniforme su tutte le viste;
- item per-trade compressi in `3 righe`, massimo `4` se serve;
- i dettagli lunghi di `entry / tp / sl / eventi` non stanno nel dashboard;
- ogni trade deve portare naturalmente a `Details: /trade n` oppure ad azioni dirette.

---

## Modello funzionale

Il dashboard ha due livelli:

```text
Scope
-> insieme massimo dei dati visibili, deciso quando eseguo /dashboard.

Stato UI
-> view corrente, pagina corrente, filtri attivi.
```

### Scope supportati

| Scope | Header | Dati visibili |
|---|---|---|
| Global scope | `All accounts` | tutti gli account e tutti i trader |
| Account scope | `demo_1` | tutti i trader dell'account |
| Trader scope | `demo_1 · trader_a` | solo quel trader |

### Regole

- `commands` topic generale -> global scope
- `commands` topic account-specifico -> account scope
- `clean_log` fallback topic -> account scope
- `clean_log` per-trader topic -> trader scope
- `tech_log` -> `/dashboard` non disponibile
- i filtri possono restringere lo scope, mai espanderlo

### Topic non supportato

```text
Command is not available in this topic.
```

### Callback scaduta o dashboard non piu` attivo

```text
Dashboard is no longer active. Use /dashboard to create a new one.
```

---

## Header comune

Tutte le viste usano questa struttura:

```text
<view icon> <View name> — <account> [· <trader>]
- - - - - - - - - - - - - - - - - - - -
Total: <n>   Page: <x>/<y>   Updated: HH:MM:SS
Filters: <active filters>
- - - - - - - - - - - - - - - - - - - -

<view content>
```

### Regole header

- `Filters:` compare solo se c'e` almeno un filtro attivo
- `Page: 1/1` puo` restare visibile per coerenza
- `Updated` rappresenta il render corrente del dashboard
- se la vista non e` paginata, `Page: 1/1`

### Esempio header senza filtri

```text
⚡ Active — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Total: 10   Page: 1/2   Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
```

### Esempio header con filtri

```text
✅ Closed — demo_1
- - - - - - - - - - - - - - - - - - - -
Total: 24   Page: 2/5   Updated: 14:32:05
Filters: trader_a · Last 7d · Stop loss
- - - - - - - - - - - - - - - - - - - -
```

### Esempio header global scope

```text
⚡ Active — All accounts
- - - - - - - - - - - - - - - - - - - -
Total: 27   Page: 1/6   Updated: 14:32:05
Filters: All accounts · All traders
Order: Updated desc
- - - - - - - - - - - - - - - - - - - -
```

---

## Keyboard principale

### Layout canonico

```text
[⚡ Active]  [✅ Closed]  [🚫 Blocked]
[💰 PnL]     [📉 Stats]   [🔄 Refresh]
[🔎 Filters] [🧹 Clear]
[← Prev]     [Page 2/5]   [Next →]
```

### Regole

- cambio view -> `current_page = 0`
- `Refresh` mantiene view, pagina e filtri
- `Filters` apre il pannello filtri della view corrente
- `Clear` azzera tutti i filtri, incluso trader globale
- `Page N/M` e` inerte
- la riga paginazione compare solo per `Active`, `Closed`, `Blocked`
- la paginazione e` calcolata dopo i filtri

### Varianti paginazione

Prima pagina:

```text
[Page 1/3]  [Next →]
```

Pagina intermedia:

```text
[← Prev]  [Page 2/3]  [Next →]
```

Ultima pagina:

```text
[← Prev]  [Page 3/3]
```

---

## Vista `Active`

### Scopo

Mostra i trade non terminali del topic corrente in forma estremamente compatta.

### Template target

```text
⚡ Active — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Total: 10   Page: 1/2   Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
#5 · BTC/USDT · LONG · PARTIALLY_CLOSED
uPnL: +34.20 USDT  rPnL: +14.20 USDT
/trade 5 · /cancel 5 · /close 5
- - - - - - - - - - - - - - - - - - - -
#6 · BTC/USDT · LONG · OPEN
uPnL: +11.40 USDT  rPnL: +0.00 USDT
/trade 6 · /cancel 6 · /close 6
- - - - - - - - - - - - - - - - - - - -
#7 · SOL/USDT · LONG · WAITING_ENTRY
rPnL: —
/trade 7 · /cancel 7 · /close 7
```

### Regole `Active`

- riga 1: identificazione trade
- riga 2: stato economico rapido
- riga 3: azioni
- per `WAITING_ENTRY`:
  - `uPnL` assente
  - `rPnL` assente oppure `—`
- niente `entry / tp / sl` nel dashboard
- niente `source`, niente timeline

### Template `Active` — global scope

Nel topic `commands` generale, la vista `Active` usa un layout a `4 righe` per evitare ambiguita` tra account e trader.

```text
⚡ Active — All accounts
- - - - - - - - - - - - - - - - - - - -
Total: 27   Page: 1/6   Updated: 14:32:05
Filters: All accounts · All traders
Order: Updated desc
- - - - - - - - - - - - - - - - - - - -
#5 · BTC/USDT · LONG · PARTIALLY_CLOSED
Trader: trader_devos_crypto · Account: demo_2
uPnL: +34.20 USDT  rPnL: +14.20 USDT
/trade 5 · /cancel 5 · /close 5
- - - - - - - - - - - - - - - - - - - -
#17 · ETH/USDT · SHORT · OPEN
Trader: trader_alpha · Account: demo_1
uPnL: -3.20 USDT  rPnL: -0.20 USDT
/trade 17 · /cancel 17 · /close 17
- - - - - - - - - - - - - - - - - - - -
#22 · SOL/USDT · LONG · WAITING_ENTRY
Trader: trader_beta · Account: demo_3
rPnL: —
/trade 22 · /cancel 22 · /close 22
```

### Regole `Active` in global scope

- ordine di default consigliato: `Updated desc`
- il contesto `Trader + Account` sta in seconda riga
- target `4 righe` per item
- in global scope non conviene comprimere `account` e `trader` nella prima riga

### Empty state

```text
⚡ Active — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Total: 0   Page: 1/1   Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
No active trades.
```

### Stati visibili in `Active`

| UI value |
|---|
| `WAITING_ENTRY` |
| `PARTIALLY_FILLED` |
| `OPEN` |
| `PARTIALLY_CLOSED` |
| `CLOSE_PENDING` |

---

## Vista `Closed`

### Scopo

Mostra i trade terminali in formato compatto e orientato a consultazione storica veloce.

### Template target

```text
✅ Closed — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Total: 10   Page: 1/2   Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
#22 · BTC/USDT · LONG · STOP_LOSS
Net PnL: -3.20 USDT · ⏱ 2h 34m
Details: /trade 22
- - - - - - - - - - - - - - - - - - - -
#18 · SOL/USDT · LONG · TP_COMPLETE
Net PnL: +34.50 USDT · ⏱ 4h 45m
Details: /trade 18
- - - - - - - - - - - - - - - - - - - -
#24 · ETH/USDT · LONG · CANCELLED_UNFILLED
PnL: No fill
Details: /trade 24
```

### Regole `Closed`

- la quarta colonna e` il motivo terminale, non solo lo stato generico
- `Details: /trade n` sempre presente
- `CANCELLED_UNFILLED` e` visibile qui
- per `CANCELLED_UNFILLED`:
  - niente durata obbligatoria
  - `PnL: No fill`

### Template `Closed` — global scope

```text
✅ Closed — All accounts
- - - - - - - - - - - - - - - - - - - -
Total: 24   Page: 2/5   Updated: 14:32:05
Filters: demo_2 · All traders · Last 7d
Order: Closed desc
- - - - - - - - - - - - - - - - - - - -
#22 · BTC/USDT · LONG · STOP_LOSS
Trader: trader_devos_crypto · Account: demo_2
Net PnL: -3.20 USDT · ⏱ 2h 34m
Details: /trade 22
- - - - - - - - - - - - - - - - - - - -
#31 · ETH/USDT · SHORT · TP_COMPLETE
Trader: trader_alpha · Account: demo_1
Net PnL: +18.50 USDT · ⏱ 1h 12m
Details: /trade 31
```

### Empty state

```text
✅ Closed — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Total: 0   Page: 1/1   Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
No closed trades.
```

---

## Vista `Blocked`

### Scopo

Mostra i trade che richiedono intervento o verifica manuale.

### Template target

```text
🚫 Blocked — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Total: 1   Page: 1/1   Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
#7 · ETH/USDT · LONG
Blocked: 14 Jun 11:52 · Reason: missing_sl
Details: /trade 7
```

### Regole `Blocked`

- niente testo superfluo
- `Reason` e` obbligatoria se disponibile
- `Details: /trade n` sempre presente
- se utile, il blocco puo` usare 4 righe max, ma il target e` 3 righe

### Template `Blocked` — global scope

```text
🚫 Blocked — All accounts
- - - - - - - - - - - - - - - - - - - -
Total: 3   Page: 1/1   Updated: 14:32:05
Filters: All accounts · All traders
Order: Blocked desc
- - - - - - - - - - - - - - - - - - - -
#7 · ETH/USDT · LONG
Trader: trader_devos_crypto · Account: demo_2
Blocked: 14 Jun 11:52 · Reason: missing_sl
Details: /trade 7
```

### Empty state

```text
🚫 Blocked — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Total: 0   Page: 1/1   Updated: 14:32:05
- - - - - - - - - - - - - - - - - - - -
No blocked trades.
```

---

## Vista `PnL`

### Scopo

Mostra snapshot account e realized performance del perimetro selezionato.

### Template target

```text
💰 PnL — demo_1
- - - - - - - - - - - - - - - - - - - -
Total: 1   Page: 1/1   Updated: 14:32:05
Filters: trader_a · Last 7d
- - - - - - - - - - - - - - - - - - - -
Account snapshot:
Equity:        10,432.50 USDT
Balance:        9,100.00 USDT
Margin used:      820.00 USDT

Realized — trader_a · Last 7d:
Gross:          +142.60 USDT
Fees:            -11.20 USDT
Net:            +130.00 USDT

Open: 1 · Waiting entry: 1
```

### Regole `PnL`

- `Equity / Balance / Margin used` restano account-level
- la parte `Realized` rispetta filtri trader e periodo
- `Open` e `Waiting entry` sono conteggi correnti, non storici
- niente card per-trade

### Template `PnL` — global scope

Nel `global scope`, `PnL` deve rendere esplicito che stai guardando una vista aggregata cross-account.

```text
💰 PnL — All accounts
- - - - - - - - - - - - - - - - - - - -
Total: 3   Page: 1/1   Updated: 14:32:05
Filters: All accounts · All traders · Last 7d
Order: Net desc
- - - - - - - - - - - - - - - - - - - -
Accounts in scope: 3
Snapshot mode: per-account latest

Realized — All accounts · Last 7d:
Gross:          +412.60 USDT
Fees:            -31.20 USDT
Net:            +381.40 USDT

Open: 7 · Waiting entry: 4
- - - - - - - - - - - - - - - - - - - -
By account:
demo_2 · Net: +210.40 USDT · Open: 3
demo_1 · Net: +121.00 USDT · Open: 2
demo_3 · Net: +50.00 USDT · Open: 2
```

### Regole `PnL` in global scope

- niente somma artificiale di `Equity / Balance / Margin used` se non e` desiderata come metrica di prodotto
- la vista base puo` mostrare solo realized aggregato + breakdown per account
- `Accounts in scope` deve essere visibile
- `By account` aiuta a capire subito dove sta il risultato
- se in futuro si decide di mostrare anche snapshot aggregati, la spec dovra` esplicitare la semantica

---

## Vista `Stats`

### Scopo

Mostra aggregati prestazionali multi-periodo.

### Template target

```text
📉 Stats — demo_1 · trader_a
- - - - - - - - - - - - - - - - - - - -
Total: 1   Page: 1/1   Updated: 14:32:05
Filters: Long
- - - - - - - - - - - - - - - - - - - -

Period          Trades   Win%      Net
Today                1   100%   +18.40
Last 7d              6    67%   +62.10
Last 30d            19    63%  +148.30
All time            31    61%   +98.20

Best:  #8  SOL/USDT  +34.50 USDT
Worst: #22 BNB/USDT -12.80 USDT
```

### Regole `Stats`

- vista aggregata, non per-trade
- nessun filtro periodo selezionabile dentro `Stats`
- `Cancelled no fill` escluso da trade count, win rate, net, best, worst

### Template `Stats` — global scope

```text
📉 Stats — All accounts
- - - - - - - - - - - - - - - - - - - -
Total: 3   Page: 1/1   Updated: 14:32:05
Filters: All accounts · All traders
Order: Net desc
- - - - - - - - - - - - - - - - - - - -

Period          Trades   Win%      Net
Today                4    75%   +32.40
Last 7d             18    67%  +381.40
Last 30d            64    61%  +912.10
All time           142    59% +1,284.70

Best:  #8   SOL/USDT  +34.50 USDT
Worst: #22  BNB/USDT  -12.80 USDT
- - - - - - - - - - - - - - - - - - - -
By account:
demo_2 · Trades: 8  · Win%: 75% · Net: +210.40
demo_1 · Trades: 6  · Win%: 67% · Net: +121.00
demo_3 · Trades: 4  · Win%: 50% · Net: +50.00
```

### Regole `Stats` in global scope

- il blocco principale resta la tabella aggregata totale
- il breakdown `By account` e` secondario ma molto utile
- `Best` e `Worst` restano globali sul perimetro filtrato
- niente duplicazione di tabella completa per ogni account

---

## Sistema filtri

## Regole globali

- filtro trader globale e persistente tra le viste
- in global scope esiste anche filtro account globale
- tutti gli altri filtri sono locali alla vista
- ogni cambio filtro resetta la pagina a `0`
- `Refresh` conserva i filtri

### Sintesi filtri

```text
Filters: trader_a · Open · Long
```

### Clear actions

| Pulsante | Effetto |
|---|---|
| `Clear` | pulisce trader globale e filtri di tutte le viste |
| `Clear view` | pulisce solo i filtri della vista corrente |

### Trader selector

Disponibile solo in account scope.

```text
[All traders]
[trader_a]  [trader_b]  [trader_c]
[← Back]
```

### Account selector

Disponibile solo in global scope.

```text
[All accounts]
[demo_1]  [demo_2]  [demo_3]
[← Back]
```

### Side selector

```text
[All sides]  [Long]  [Short]
[← Back]
```

### Realized period selector

Usato in `Closed` e `PnL`.

```text
[All time]  [Today]  [Last 7d]
[Last 30d]  [This month]
[← Back]
```

---

## Pannelli filtri

## `Active`

```text
🔎 Filters — Active
- - - - - - - - - - - - - - - - - - - -
Account: All accounts
Trader: All traders
Status: All statuses
Side: All sides

[Account ▸]  [Trader ▸]  [Status ▸]
[Side ▸]
[🧹 Clear view]  [← Back]
```

Status selector:

```text
[All statuses]
[Waiting entry]      [Partially filled]
[Open]               [Partially closed]
[Closing]
[← Back]
```

## `Closed`

```text
🔎 Filters — Closed
- - - - - - - - - - - - - - - - - - - -
Account: All accounts
Trader: All traders
Exit: All exits
Period: All time

[Account ▸]  [Trader ▸]  [Exit ▸]
[Period ▸]
[🧹 Clear view]  [← Back]
```

Exit selector:

```text
[All exits]
[Take profit]      [Stop loss]
[Manual close]     [Exchange close]
[Cancelled no fill]
[Other]
[← Back]
```

## `Blocked`

```text
🔎 Filters — Blocked
- - - - - - - - - - - - - - - - - - - -
Account: All accounts
Trader: All traders
Type: All types
Age: Any age

[Account ▸]  [Trader ▸]  [Type ▸]
[Age ▸]
[🧹 Clear view]  [← Back]
```

## `PnL`

```text
🔎 Filters — PnL
- - - - - - - - - - - - - - - - - - - -
Account: All accounts
Trader: All traders
Period: All time

[Account ▸]  [Trader ▸]
[Period ▸]
[🧹 Clear view]  [← Back]
```

## `Stats`

```text
🔎 Filters — Stats
- - - - - - - - - - - - - - - - - - - -
Account: All accounts
Trader: All traders
Side: All sides

[Account ▸]  [Trader ▸]
[Side ▸]
[🧹 Clear view]  [← Back]
```

---

## Relazione con `/trade n`

Ogni vista per-trade del dashboard deve poter condurre al dettaglio completo:

- in `Active`: tramite `/trade n` tra le azioni
- in `Closed`: tramite `Details: /trade n`
- in `Blocked`: tramite `Details: /trade n`

Il dashboard non contiene:

- lista completa `entry / tp / sl`
- cronologia eventi
- link ai `clean_log`
- final result esteso

Tutto questo vive in `/trade n`.

Nel `global scope`, il dashboard resta una vista di supervisione:

- mostra trade di account diversi nello stesso elenco;
- rende espliciti `Trader` e `Account` nel card item;
- usa i filtri per restringere rapidamente il perimetro.

---

## Refresh e aggiornamento

### Regole

1. arriva un evento lifecycle o snapshot account
2. si ricaricano i dati nello scope del dashboard
3. si riapplicano filtri e paginazione
4. si rigenera testo e keyboard
5. si aggiorna il messaggio solo se testo o keyboard cambiano

### Vincoli

- nessun nuovo messaggio per refresh o callback
- nessun cambio automatico di vista
- se la pagina corrente diventa invalida, clamp all'ultima valida
- un evento filtrato fuori non deve produrre edit inutile

---

## Criteri di accettazione

1. `/dashboard` crea al massimo un dashboard attivo per topic.
2. Il dashboard si apre su `Active`, pagina 1.
3. Tutti i callback aggiornano solo il messaggio originale.
4. Lo scope non puo` essere espanso dai filtri.
5. L'header mostra sempre `Total + Page + Updated`, e `Filters` se presenti.
6. Gli item `Active`, `Closed`, `Blocked` rispettano il formato compatto 3 righe target.
7. `Closed` e `Blocked` puntano sempre a `Details: /trade n`.
8. `PnL` e `Stats` restano viste aggregate.
9. La paginazione e` calcolata dopo i filtri.
10. Nessun edit Telegram viene inviato se render e keyboard non cambiano.

# SPEC — Dashboard `Stats` globale, metriche coerenti e filtri gerarchici

**Stato:** proposta da implementare  
**Area:** `src/runtime_v2/control_plane`  
**Componenti principali:** `StatusQueries`, `DashboardManager`, formatter dashboard, template dashboard, test control-plane

---

## 1. Obiettivo

Rendere la vista `📉 Stats` del dashboard affidabile e leggibile quando opera su:

- un trader singolo;
- tutti i trader di un account;
- tutti gli account e tutti i trader, nel topic `commands`.

La modifica deve correggere le incongruenze attuali di periodo, PnL e win rate, introdurre una visualizzazione gerarchica `Account → Trader` nello scope globale e sostituire i filtri piatti con filtri coerenti con la relazione reale dei dati:

```text
Account → Trader → Side
```

---

## 2. Principi vincolanti

1. Ogni trader appartiene a un account.
2. Un filtro trader è valido solo dopo la selezione di un account.
3. Le statistiche realizzate sono basate sulla chiusura del trade, non sulla sua creazione.
4. Tutte le metriche che il messaggio etichetta come `Net` devono usare lo stesso calcolo netto.
5. Un dashboard globale deve aggregare tutto, ma deve mantenere visibile la provenienza `account → trader`.
6. I filtri di scope (`account`, `trader`) sono comuni a tutte le viste. I filtri funzionali (`side`, `status`, `period`) appartengono alla singola vista.
7. Il dashboard deve continuare a usare un solo messaggio Telegram aggiornato in-place.

---

## 3. Stato attuale e difetti da correggere

### 3.1 Metriche `Stats`

| Difetto | Comportamento attuale | Comportamento richiesto |
|---|---|---|
| Periodo | usa `created_at` | usa `closed_at` oppure timestamp dell’evento terminale |
| Win rate | win se `gross_pnl > 0` | win se `net_pnl > 0` |
| Best / Worst | ordinati per gross PnL | ordinati per net PnL |
| Trade BE | confluiscono implicitamente nei non-win | devono essere mostrati separatamente o esclusi dal denominatore Win% |
| Closed tab | etichetta `Net PnL`, ma mostra gross PnL | deve mostrare Net PnL reale oppure cambiare etichetta in Gross PnL |

### 3.2 Filtri

| Difetto | Correzione |
|---|---|
| Trader selezionabile senza account nel dashboard globale | disabilitare Trader finché Account = All accounts |
| Cambio account lascia trader precedente | cambio account rimuove automaticamente il filtro trader |
| Lista trader globale | lista trader limitata all’account selezionato |
| Un solo `filters_json` per tutte le viste | filtri di scope comuni + filtri specifici per vista |
| `PnL` espone Period ma non lo applica | rimuovere Period dalla vista PnL in questa modifica |
| `Closed` può ricevere Side senza poterlo gestire | Side deve essere un filtro locale anche in Closed oppure deve essere eliminato quando si entra in Closed |
| `Clear view` azzera tutto | separare clear selettivi e reset completo |

### 3.3 Visualizzazione globale

| Difetto | Correzione |
|---|---|
| `Total` in Stats indica account o zero | header dedicato: `Accounts`, `Traders`, `Closed trades` |
| `Order: Net desc` è ambiguo | rimuoverlo dall’header generale; indicare ordinamento solo nella sezione `By account` |
| Titolo non riflette filtri | titolo usa scope effettivo risultante dai filtri |
| Breakdown solo per account | aggiungere struttura `Account → Trader` |
| Best/Worst senza provenienza | includere account e trader quando sono fuori dal titolo/scope corrente |

---

## 4. Definizioni metriche

### 4.1 Trade incluso

Un trade entra nelle statistiche quando:

```text
lifecycle_state = 'CLOSED'
```

`CANCELLED_UNFILLED` è escluso perché non ha una posizione eseguita né PnL realizzato.

### 4.2 Timestamp di chiusura

Il timestamp statistico è:

```text
COALESCE(closed_at, updated_at)
```

`closed_at` deve essere usato quando disponibile. `updated_at` è solo fallback di compatibilità per dati storici o schema incompleto.

### 4.3 Net PnL

```text
net_pnl = cumulative_gross_pnl - cumulative_fees - cumulative_funding
```

Questo valore è l’unica base ammessa per:

- colonna `Net`;
- win / loss / breakeven;
- Best;
- Worst;
- aggregati per account;
- aggregati per trader;
- Closed view quando etichettata `Net PnL`.

### 4.4 Esito di un trade

```text
win        net_pnl > 0
loss       net_pnl < 0
breakeven  net_pnl = 0
```

### 4.5 Win rate

Formula:

```text
win_pct = wins / (wins + losses) × 100
```

I breakeven sono esclusi dal denominatore.

Se `wins + losses = 0`, il valore visualizzato è `—`.

### 4.6 Finestre temporali

| Riga | Filtro richiesto |
|---|---|
| Today | data di chiusura uguale alla data UTC corrente |
| Last 7d | chiusura negli ultimi 7 giorni UTC |
| Last 30d | chiusura negli ultimi 30 giorni UTC |
| All time | tutti i trade chiusi nello scope |

---

## 5. Scope dashboard

### 5.1 Scope base da topic

| Topic | Scope base |
|---|---|
| `commands` | tutti gli account, tutti i trader |
| `clean_log` fallback di un account | account singolo, tutti i trader |
| `clean_log` per trader | account singolo, trader singolo |

### 5.2 Scope effettivo

Lo scope effettivo è dato da:

```text
scope_base + scope_filters(account, trader)
```

I filtri possono solo restringere lo scope base; non possono ampliarlo.

Esempi:

| Scope base | Filtro | Risultato |
|---|---|---|
| globale | account = `demo_1` | `demo_1`, tutti i trader |
| globale | account = `demo_1`, trader = `trader_a` | solo `demo_1 · trader_a` |
| account `demo_1` | trader = `trader_a` | solo `demo_1 · trader_a` |
| trader `demo_1 · trader_a` | account = `demo_2` | non consentito / non mostrato |

---

## 6. Modello filtri persistito

Sostituire il JSON piatto con una struttura esplicita.

```json
{
  "scope": {
    "account": "demo_1",
    "trader": "trader_a"
  },
  "views": {
    "active": {
      "status": "OPEN",
      "side": "LONG"
    },
    "closed": {
      "period": "week",
      "side": "LONG"
    },
    "stats": {
      "side": "LONG"
    },
    "pnl": {}
  }
}
```

### 6.1 Regole di normalizzazione

1. Se `scope.account` cambia, rimuovere sempre `scope.trader`.
2. Se `scope.account` è assente, `scope.trader` deve essere assente.
3. `scope.trader` deve essere validato contro l’account selezionato.
4. I filtri non supportati dalla vista corrente non devono essere mostrati né applicati.
5. `Reset all` rimuove `scope` e tutte le chiavi in `views`.
6. `Clear Account` rimuove anche `Trader`.
7. `Clear Trader` mantiene `Account`.
8. `Clear Side`, `Clear Status`, `Clear Period` agiscono solo sulla vista corrente.

### 6.2 Compatibilità con record esistenti

Se `filters_json` è nel vecchio formato piatto:

```json
{"account":"demo_1","side":"LONG"}
```

al primo caricamento deve essere convertito logicamente in:

```json
{
  "scope": {"account":"demo_1"},
  "views": {"active":{"side":"LONG"}}
}
```

La conversione può essere lazy, senza migration DB separata.

---

## 7. UX filtri

### 7.1 Pannello principale `Stats`

```text
🔎 Filters — Stats

Account: All accounts
Trader:  All traders
Side:    All sides
─────────────────────────────────────
[Account ▸] [Trader ▸]
[Side ▸]
[Clear Account] [Clear Trader]
[Clear Side] [🧹 Reset all]
[← Back]
```

### 7.2 Selettore Account

```text
🔎 Select account

Current: All accounts

[All accounts]
[demo_1]
[demo_2]
[← Back]
```

### 7.3 Selettore Trader senza account

```text
🔎 Select trader

Select an account first.

[← Back]
```

Non mostrare una lista trader trasversale agli account.

### 7.4 Selettore Trader con account

```text
🔎 Select trader — demo_1

Current: All traders

[All traders]
[trader_a]
[trader_b]
[← Back]
```

La query deve restituire solo trader distinti di `demo_1` e deve rispettare lo scope base del dashboard.

### 7.5 Cambio account con trader presente

Input precedente:

```json
{"scope":{"account":"demo_1","trader":"trader_a"}}
```

Quando l’utente seleziona `demo_2`, risultato obbligatorio:

```json
{"scope":{"account":"demo_2"}}
```

Il dashboard deve tornare alla vista precedente con trader rimosso. Nessun popup separato è necessario; la riga `Filters:` aggiornata è sufficiente.

---

## 8. Rendering `Stats`

### 8.1 Header

L’header deve dipendere dallo scope effettivo, non da quello originale.

#### Scope globale

```text
📉 Stats — All accounts
─────────────────────────────────────
Accounts: 2 · Traders: 4 · Closed trades: 92
Updated: 14:32:05
─────────────────────────────────────
```

#### Account singolo

```text
📉 Stats — demo_1
─────────────────────────────────────
Traders: 2 · Closed trades: 48
Updated: 14:32:05
─────────────────────────────────────
```

#### Trader singolo

```text
📉 Stats — demo_1 · trader_a
─────────────────────────────────────
Closed trades: 30
Updated: 14:32:05
─────────────────────────────────────
```

`Page` non deve comparire in `Stats` perché non esiste paginazione. `Order` non deve comparire nell’header principale.

### 8.2 Tabella periodo

```text
Period       Trades  Win%   Net
Today        4       50%    +8.40
Last 7d      18      61%    +42.10
Last 30d     45      56%    +75.20
All time     92      54%    +101.60
```

### 8.3 Best / Worst

#### Scope globale

```text
Best:  #22 BTC/USDT · demo_1 · trader_a · +18.00 USDT
Worst: #17 SOL/USDT · demo_2 · trader_b · -9.10 USDT
```

#### Scope account singolo

```text
Best:  #22 BTC/USDT · trader_a · +18.00 USDT
Worst: #17 SOL/USDT · trader_b · -9.10 USDT
```

#### Scope trader singolo

```text
Best:  #22 BTC/USDT · +18.00 USDT
Worst: #17 SOL/USDT · -9.10 USDT
```

### 8.4 Breakdown scope globale

```text
By account:

demo_1 · Trades: 48 · Win%: 58% · Net: +72.10
│
├─ trader_a · Trades: 30 · Win%: 60% · Net: +46.10
│
└─ trader_b · Trades: 18 · Win%: 56% · Net: +26.00

─────────────────────────────────────
demo_2 · Trades: 44 · Win%: 50% · Net: +29.50
│
├─ trader_a · Trades: 20 · Win%: 55% · Net: +18.70
│
└─ trader_b · Trades: 24 · Win%: 46% · Net: +10.80
```

Regole:

- ordinare account per `net_pnl` decrescente;
- ordinare trader per `net_pnl` decrescente dentro il proprio account;
- la somma dei trader deve coincidere con il totale account per conteggio, net PnL, wins, losses e BE;
- se un account non ha trade chiusi nello scope filtrato, non mostrarlo;
- non mostrare breakdown se scope effettivo è già un trader singolo.

### 8.5 Breakdown account singolo

```text
By trader:

trader_a · Trades: 30 · Win%: 60% · Net: +46.10
trader_b · Trades: 18 · Win%: 56% · Net: +26.00
```

### 8.6 Nessun breakdown trader singolo

Per scope effettivo `account + trader` non stampare `By account` o `By trader`.

---

## 9. Query dati richieste

### 9.1 Base dataset statistiche

Tutte le query `Stats` devono usare un’espressione condivisa:

```sql
net_pnl_expr = cumulative_gross_pnl - cumulative_fees - cumulative_funding
closed_ts_expr = COALESCE(closed_at, updated_at)
```

### 9.2 Aggregati per finestra

Pseudo-SQL:

```sql
SELECT
  COUNT(*) AS trade_count,
  SUM(CASE WHEN net_pnl_expr > 0 THEN 1 ELSE 0 END) AS wins,
  SUM(CASE WHEN net_pnl_expr < 0 THEN 1 ELSE 0 END) AS losses,
  SUM(CASE WHEN net_pnl_expr = 0 THEN 1 ELSE 0 END) AS breakevens,
  SUM(net_pnl_expr) AS pnl_net,
  SUM(cumulative_fees + cumulative_funding) AS fees
FROM ops_trade_chains
WHERE lifecycle_state = 'CLOSED'
  AND scope_predicate
  AND side_predicate
  AND closed_ts_predicate;
```

### 9.3 Breakdown `Account → Trader`

```sql
SELECT
  account_id,
  trader_id,
  COUNT(*) AS trade_count,
  SUM(CASE WHEN net_pnl_expr > 0 THEN 1 ELSE 0 END) AS wins,
  SUM(CASE WHEN net_pnl_expr < 0 THEN 1 ELSE 0 END) AS losses,
  SUM(CASE WHEN net_pnl_expr = 0 THEN 1 ELSE 0 END) AS breakevens,
  SUM(net_pnl_expr) AS pnl_net
FROM ops_trade_chains
WHERE lifecycle_state = 'CLOSED'
  AND scope_predicate
  AND side_predicate
GROUP BY account_id, trader_id
ORDER BY account_id, pnl_net DESC, trader_id ASC;
```

Il dataset deve poi essere raggruppato in Python per account, mantenendo anche l’aggregato account.

### 9.4 Best / Worst

```sql
SELECT trade_chain_id, symbol, account_id, trader_id, net_pnl_expr AS pnl_net
FROM ops_trade_chains
WHERE lifecycle_state = 'CLOSED'
  AND scope_predicate
  AND side_predicate
ORDER BY pnl_net DESC
LIMIT 1;
```

Usare `ASC` per Worst.

---

## 10. Modifiche tecniche previste

### 10.1 `status_queries.py`

- Estendere `StatsRow` con `wins`, `losses`, `breakevens` se necessario per test e rendering futuro.
- Estendere `StatsView` con:
  - `closed_trade_count`;
  - `account_count`;
  - `trader_count`;
  - `breakdown_accounts` nidificato;
  - Best/Worst completi di `account_id`, `trader_id`, `net_pnl`.
- Correggere le finestre da `created_at` a `COALESCE(closed_at, updated_at)`.
- Calcolare win/loss/BE da Net PnL.
- Correggere Best/Worst da Gross a Net.
- Esporre una query per i trader filtrati da account, per il pannello filtri.

### 10.2 `dashboard_manager.py`

- Sostituire lettura/scrittura filtri piatti con modello `scope + views`.
- Aggiungere normalizzazione filtri.
- Vincolare `Trader` a `Account`.
- Nel cambio account, rimuovere trader.
- Nel rendering, passare alla vista solo i filtri pertinenti alla vista corrente più scope comune.
- Aggiornare i callback per clear selettivo e reset totale.

### 10.3 `formatters/dashboard.py`

- Calcolare e passare `effective_scope` anche per header e non solo per query.
- Sostituire payload Stats `total/page_display/order_str` con metadata specifici.
- Passare breakdown nidificato e contatori scope.
- Rimuovere filtri non applicabili dalla stringa `Filters:`.
- Rimuovere `period` dal pannello/view PnL in questa modifica.

### 10.4 `formatters/templates/dashboard.py`

- Creare header dedicato `Stats`, separato da `_dash_header_full`.
- Creare renderer `By account → trader`.
- Best/Worst devono stampare contesto in base allo scope.
- Correggere Closed view: usare Net PnL reale o rinominare esplicitamente il campo.

### 10.5 Test

Aggiornare o creare test per:

- statistiche per chiusura, non creazione;
- win/loss/BE netti;
- Best/Worst netti;
- aggregato globale e breakdown per account/trader;
- somma trader = account;
- filtro account → trader;
- trader non selezionabile senza account;
- cambio account che resetta trader;
- filtri locali per vista;
- reset totale e clear selettivo;
- Closed view Net PnL coerente;
- compatibilità vecchio `filters_json`.

---

## 11. Criteri di accettazione

### Metriche

- [ ] Un trade creato 20 giorni fa e chiuso oggi compare in `Today`.
- [ ] Un trade con gross positivo ma net negativo conta come loss.
- [ ] Un trade net zero non altera Win%.
- [ ] `Best` e `Worst` coincidono con massimo/minimo Net PnL.
- [ ] Closed view non etichetta come Net un valore lordo.

### Scope e filtri

- [ ] Nel topic globale, senza filtri, Stats aggrega tutti gli account e trader.
- [ ] Trader non può essere selezionato senza un account.
- [ ] Cambiare account rimuove trader automaticamente.
- [ ] La lista trader contiene solo valori dell’account selezionato.
- [ ] Un dashboard trader-specifico non può espandere account/trader attraverso filtri.
- [ ] I filtri `side`, `status`, `period` sono isolati per vista.
- [ ] `Reset all` cancella tutto; clear selettivo modifica solo il filtro previsto.

### Rendering

- [ ] Header globale mostra `Accounts`, `Traders`, `Closed trades`, non `Total` e `Page`.
- [ ] Header riflette sempre scope effettivo.
- [ ] Dashboard globale mostra breakdown `Account → Trader`.
- [ ] Dashboard account mostra `By trader`.
- [ ] Dashboard trader non mostra breakdown ridondante.
- [ ] Best/Worst mostrano account/trader solo quando necessari al contesto.
- [ ] Ogni totale account coincide con la somma dei trader visualizzati.

---

## 12. Fuori scope

- Grafici equity, drawdown, expectancy, profit factor, ROI/R-multiple.
- Filtri multi-selezione di account o trader.
- Personalizzazione del periodo Stats oltre alle quattro finestre predefinite.
- Ricalcolo o riconciliazione dei PnL su exchange: questa spec usa i dati già persistiti in `ops_trade_chains`.
- Migrazione SQL obbligatoria del DB; eventuali fallback schema devono restare compatibili.

---

## 13. Ordine di implementazione consigliato

1. Correggere semantica PnL / periodi / win rate / Best-Worst in `StatusQueries`.
2. Correggere Closed view affinché `Net PnL` sia realmente netto.
3. Introdurre payload Stats e breakdown `Account → Trader`.
4. Implementare il nuovo modello filtri e la sua compatibilità lazy.
5. Aggiornare pannelli Telegram e template.
6. Aggiungere test unitari e test di integrazione callback/dashboard.


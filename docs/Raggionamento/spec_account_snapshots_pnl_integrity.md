# Spec — Account Snapshots Exchange & PnL Data Integrity

**Status:** Proposed  
**Scope:** Runtime V2 · Bybit via CCXT · `ops_account_snapshots` · tab `💰 PnL`  
**Priority:** P0 for data correctness, P1 for dashboard usability

---

## 1. Obiettivo

Rendere verificabili e semanticamente corretti i dati account mostrati nel tab `💰 PnL`.

Il sistema deve distinguere chiaramente tra:

1. dati ottenuti dall’exchange;
2. dati calcolati localmente dal bot;
3. PnL storico derivato dalle chain;
4. timestamp di rendering del dashboard e timestamp effettivo dello snapshot.

Il dashboard non deve presentare un valore come live, aggiornato o reale quando è soltanto un dato storico in database, un fallback locale o uno snapshot non recente.

---

## 2. Stato attuale

### 2.1 Origine snapshot

Il percorso attuale è:

```text
Nuovo segnale
  → LifecycleEntryGate.process_signal()
  → LiveExchangeDataPort.get_account_state(account_id)
  → CcxtBybitAdapter.fetch_account_snapshot()
  → ccxt.bybit.fetch_balance()
  → INSERT ops_account_snapshots
```

La chiamata `fetch_balance()` è una chiamata reale a Bybit tramite CCXT.

Con `mode: demo`, l’adapter attiva Bybit Demo Trading. Quindi i valori provengono da un conto Demo Bybit reale, non da un conto live.

### 2.2 Quando lo snapshot viene creato

Lo snapshot account viene acquisito durante la valutazione di un nuovo segnale.

Non esiste un worker periodico dedicato all’account balance.

Conseguenze:

- nessun nuovo segnale → nessun nuovo snapshot account;
- `🔄 Refresh` dashboard non esegue `fetch_balance()`;
- il refresh dashboard legge semplicemente l’ultimo record DB;
- gli intervalli di position sync non aggiornano automaticamente balance/equity account.

### 2.3 Problemi attuali

| ID | Problema | Severità |
|---|---|---|
| AS-01 | Snapshot non periodico: Equity/Balance/Margin possono essere vecchi | P0 |
| AS-02 | `Updated` dashboard indica render time, non `captured_at` dello snapshot | P0 |
| AS-03 | Mappatura `Equity` non usa in modo esplicito il campo account-wide Bybit `totalEquity` | P0 |
| AS-04 | Mappatura `Available Balance` usa fallback semanticamente diversi e può usare un campo deprecato | P0 |
| AS-05 | `Margin used` non rappresenta una definizione univoca; fallback non somma position IM e order IM | P0 |
| AS-06 | Payload raw CCXT/Bybit viene prodotto, ma lo snapshot persistito nel percorso segnale salva `{}` | P0 |
| AS-07 | Scope trader mostra snapshot account, con rischio di attribuirlo al trader | P1 |
| AS-08 | Vista globale non aggrega le latest snapshot per account e non mostra freshness per account | P1 |
| AS-09 | Account senza trade chain può sparire dalla vista globale | P1 |
| AS-10 | Mancano test con payload Bybit realistici e test di stale snapshot | P1 |

---

## 3. Principi invarianti

### 3.1 Dati exchange

I campi seguenti sono dati exchange e devono essere marcati come tali:

- equity;
- available balance;
- margin used;
- account-wide unrealized PnL, se disponibile;
- timestamp exchange o timestamp di acquisizione;
- source adapter/mode.

### 3.2 Dati locali

I campi seguenti sono calcolati localmente e non devono essere venduti come dati Bybit:

- total open risk;
- risk remaining;
- numero chain open/waiting/review;
- PnL realizzato storico dalle chain;
- PnL per trader.

### 3.3 Snapshot ownership

Uno snapshot appartiene sempre a un `account_id`.

Un trader può avere chain in un account, ma non possiede uno snapshot separato.

Nel trader scope il dashboard deve dichiarare:

```text
Account snapshot — demo_1
```

e non deve lasciare intendere che Equity/Available/Margin siano metriche di `trader_a`.

### 3.4 Freshness

Ogni snapshot deve avere:

- `captured_at`: timestamp UTC assegnato dal bot immediatamente dopo la risposta exchange;
- `source`: per esempio `ccxt_bybit:demo`;
- `freshness_seconds`: calcolato nel rendering;
- `stale`: booleano derivato dalla freshness.

---

## 4. Modello dati target

La tabella esistente resta append-only:

```text
ops_account_snapshots
```

Ogni record deve contenere almeno:

| Campo | Tipo | Significato |
|---|---|---|
| `account_id` | text | Account logico bot |
| `equity_usdt` | real nullable | Equity account-wide in USDT/USD-equivalent |
| `available_balance_usdt` | real nullable | Capitale disponibile per nuove posizioni |
| `total_margin_used_usdt` | real nullable | Initial margin usato, con definizione esplicita |
| `account_unrealized_pnl_usdt` | real nullable | uPnL account-wide, se disponibile |
| `total_open_risk_usdt` | real nullable | Rischio bot calcolato localmente |
| `source` | text | `ccxt_bybit:demo`, `ccxt_bybit:live`, `fallback_static` |
| `captured_at` | ISO UTC | Tempo acquisizione |
| `payload_json` | json | Payload raw CCXT/Bybit completo o redatto |
| `snapshot_status` | text | `OK`, `FALLBACK`, `FAILED` |
| `error_code` | text nullable | Errore sintetico, senza segreti |

### 4.1 Migrazione richiesta

Aggiungere, se assenti:

```sql
ALTER TABLE ops_account_snapshots ADD COLUMN account_unrealized_pnl_usdt REAL;
ALTER TABLE ops_account_snapshots ADD COLUMN snapshot_status TEXT NOT NULL DEFAULT 'OK';
ALTER TABLE ops_account_snapshots ADD COLUMN error_code TEXT;
```

Non sovrascrivere record vecchi.

### 4.2 Indici

```sql
CREATE INDEX IF NOT EXISTS idx_ops_account_snapshots_account_captured
ON ops_account_snapshots(account_id, captured_at DESC, snapshot_id DESC);
```

---

## 5. Mappatura Bybit target

L’adapter deve usare il payload raw Bybit in modo esplicito.

Per Unified Account, i campi account-wide preferiti sono:

| Campo interno | Campo Bybit preferito | Fallback ammesso |
|---|---|---|
| `equity_usdt` | `totalEquity` | `coin.USDT.equity` solo se account-wide assente |
| `available_balance_usdt` | `totalAvailableBalance` | valore CCXT `free.USDT` con flag `field_origin=ccxt_free_usdt` |
| `total_margin_used_usdt` | `totalInitialMargin` | `totalPositionIM + totalOrderIM` |
| `account_unrealized_pnl_usdt` | `totalPerpUPL` | somma posizioni, se affidabile |
| `total_open_risk_usdt` | n/a | calcolo bot |

### 5.1 Regole di fallback

1. Un fallback deve essere esplicitamente tracciato nel payload derivato:
   ```json
   {
     "field_origins": {
       "equity_usdt": "bybit.totalEquity",
       "available_balance_usdt": "ccxt.free.USDT",
       "total_margin_used_usdt": "bybit.totalPositionIM_plus_totalOrderIM"
     }
   }
   ```

2. Non usare `availableToWithdraw` come primary source. È ammesso solo per legacy accounts se il payload non contiene alternative e deve essere marcato come fallback legacy.

3. Non usare `walletBalance` come equivalente di `available_balance_usdt`.

4. Se non è possibile estrarre un campo in modo affidabile, salvarlo `NULL`, non sostituirlo con un campo semanticamente diverso.

5. Un valore pari a `0` è valido e non deve essere scartato per via di `or` Python.

### 5.2 Correzione obbligatoria: zero values

Il codice non deve usare catene come:

```python
_safe_float(a) or _safe_float(b)
```

perché `0.0` viene trattato come falso e sostituito con il fallback.

Usare invece:

```python
def first_not_none(*values):
    for value in values:
        if value is not None:
            return value
    return None
```

---

## 6. Account Snapshot Worker

### 6.1 Nuovo componente

Introdurre un worker dedicato:

```text
AccountSnapshotWorker
```

Responsabilità:

1. risolvere tutti gli account configurati;
2. chiamare `adapter.fetch_account_snapshot(execution_account_id)`;
3. costruire `AccountStateSnapshot`;
4. salvare record append-only;
5. non bloccare lifecycle/execution;
6. gestire errori per account indipendentemente.

### 6.2 Frequenza

Configurazione per adapter/account:

```yaml
websocket:
  account_snapshot_interval_seconds: 60
  account_snapshot_stale_after_seconds: 180
```

Valori iniziali consigliati:

- update periodico: 60 secondi;
- stale warning: 180 secondi;
- timeout request: 10 secondi;
- nessun retry aggressivo nel worker loop;
- retry normale alla prossima iterazione; log rate-limited.

### 6.3 Trigger aggiuntivi

Il worker periodico è la fonte primaria.

Sono ammessi trigger immediati, coalesced per account:

- al bootstrap;
- dopo fill entry;
- dopo close/reduce fill;
- dopo comando manuale che modifica capitale;
- al click dashboard `Refresh account snapshot`.

Un trigger immediato non deve creare richieste concorrenti sullo stesso account. Se esiste una richiesta in corso, deve essere marcato `refresh_requested` e rieseguito una sola volta dopo la risposta.

### 6.4 Degradazione

Se exchange non risponde:

- non cancellare o sovrascrivere l’ultimo snapshot valido;
- registrare un record `FAILED` oppure una health event;
- dashboard mostra l’ultimo snapshot valido con age reale;
- se age > stale threshold, dashboard mostra `STALE`;
- nessun fallback statico deve essere presentato come dato exchange.

---

## 7. Persistenza payload e audit

### 7.1 Payload raw

Il record snapshot deve salvare il payload CCXT/Bybit in `payload_json`.

Non devono essere presenti:

- API key;
- API secret;
- signature;
- auth headers;
- cookie/session data.

Il payload può includere:

- `total`, `free`, `used` CCXT;
- `info.result.list`;
- valori raw usati;
- `field_origins`;
- adapter mode;
- timestamp exchange se presente.

### 7.2 Persistenza corretta

Il percorso lifecycle deve salvare:

```python
payload_json = account_snapshot.payload_json
```

e non:

```python
payload_json = "{}"
```

### 7.3 Query di audit

Ultimo snapshot per account:

```sql
WITH ranked AS (
  SELECT
    snapshot_id,
    account_id,
    equity_usdt,
    available_balance_usdt,
    total_margin_used_usdt,
    account_unrealized_pnl_usdt,
    total_open_risk_usdt,
    source,
    captured_at,
    snapshot_status,
    error_code,
    ROW_NUMBER() OVER (
      PARTITION BY account_id
      ORDER BY datetime(captured_at) DESC, snapshot_id DESC
    ) AS rn
  FROM ops_account_snapshots
)
SELECT *
FROM ranked
WHERE rn = 1
ORDER BY account_id;
```

Verifica raw payload di uno snapshot:

```sql
SELECT
  snapshot_id,
  account_id,
  source,
  captured_at,
  payload_json
FROM ops_account_snapshots
WHERE account_id = :account_id
ORDER BY datetime(captured_at) DESC, snapshot_id DESC
LIMIT 1;
```

---

## 8. Query PnL target

### 8.1 Snapshot singolo account

Per scope account o trader:

- leggere l’ultimo snapshot `OK` dell’account;
- calcolare age;
- non cercare snapshot per trader;
- usare PnL storico filtrato per scope.

### 8.2 Scope globale

Per scope globale:

1. ottenere latest valid snapshot per ogni account configurato;
2. aggregare solo snapshot con freshness entro soglia;
3. indicare account stale o missing separatamente;
4. non selezionare un solo snapshot “più recente tra tutti”.

Struttura target:

```python
{
  "accounts": [
    {
      "account_id": "demo_1",
      "snapshot": {...},
      "age_seconds": 18,
      "stale": False,
      "realized_net": 72.10,
      "open_count": 3,
      "waiting_count": 1
    },
    {
      "account_id": "demo_2",
      "snapshot": {...},
      "age_seconds": 244,
      "stale": True,
      "realized_net": 29.50,
      "open_count": 1,
      "waiting_count": 0
    }
  ],
  "aggregate": {
    "equity_usdt": 12340.50,
    "available_balance_usdt": 9180.20,
    "margin_used_usdt": 1104.80,
    "unrealized_pnl_usdt": 84.30,
    "realized_net": 101.60
  }
}
```

### 8.3 Regole aggregate

- Sommare solo gli account con snapshot valido e non stale per i totali live.
- Gli account stale non devono contaminare un totale dichiarato live.
- Se esistono account stale, mostrare:
  ```text
  Live aggregate: partial (1/2 fresh)
  ```
- Se nessuno snapshot è fresh:
  ```text
  Account state: unavailable — latest snapshots stale
  ```

---

## 9. Dashboard `💰 PnL` target

### 9.1 Scope globale

```text
💰 PnL — All accounts
─────────────────────────────────────
Live account state: partial · Fresh: 2/3
Latest snapshot: 14:32:05 UTC · max age: 42s
─────────────────────────────────────
Equity:        12,420.50 USDT
Available:      9,180.20 USDT
Margin used:    1,104.80 USDT
uPnL live:        +84.30 USDT
Open risk*:       245.00 USDT
─────────────────────────────────────
Realized — All time:
Gross:          +420.20 USDT
Fees:             38.40 USDT
Funding:           7.20 USDT
Net:            +374.60 USDT
─────────────────────────────────────
Positions: 6 · Pending entry: 3 · Review: 1
─────────────────────────────────────
By account:
demo_1 · Equity 7,220.50 · Net +280.20 · uPnL +62.40 · Pos 4 · age 18s
demo_2 · Equity 5,200.00 · Net +94.40  · uPnL +21.90 · Pos 2 · age 42s
demo_3 · STALE · last snapshot 9m 12s ago
─────────────────────────────────────
* Open risk is calculated by bot.
```

### 9.2 Scope account

```text
💰 PnL — demo_1
─────────────────────────────────────
Snapshot: 14:32:05 UTC · age 18s · ccxt_bybit:demo
─────────────────────────────────────
Equity:        7,220.50 USDT
Available:     5,180.20 USDT
Margin used:     704.80 USDT
uPnL live:       +62.40 USDT
Open risk*:      145.00 USDT
...
```

### 9.3 Scope trader

```text
💰 PnL — demo_1 · trader_a
─────────────────────────────────────
Account snapshot — demo_1:
14:32:05 UTC · age 18s · ccxt_bybit:demo
Equity:        7,220.50 USDT
Available:     5,180.20 USDT
Margin used:     704.80 USDT
─────────────────────────────────────
Realized — trader_a:
...
```

Il blocco snapshot deve avere label esplicita `Account snapshot — demo_1`.

### 9.4 Header e timestamp

Rimuovere il generico:

```text
Updated: HH:MM:SS
```

oppure mantenerlo solo come:

```text
Dashboard rendered: HH:MM:SS UTC
```

Lo stato dati account deve mostrare sempre `captured_at` e `age`.

---

## 10. Filtri PnL

### 10.1 Scope

Applicare il design già approvato:

```text
Account → Trader
```

- Trader disponibile solo dopo account.
- Cambio account cancella trader.
- La lista trader è limitata all’account selezionato.
- Trader scope non cambia lo snapshot account mostrato.

### 10.2 Period

Il filtro `Period` non deve essere mostrato finché non viene implementato.

Scelta iniziale:

- rimuovere `Period` dal tab PnL;
- mantenere `Realized — All time`;
- aggiungere periodi solo in una fase successiva con query basate su `closed_at`.

### 10.3 Side

Non aggiungere `Side` al tab PnL nella prima implementazione.

Se verrà aggiunto in futuro, dovrà filtrare solo performance chain, non equity/balance/margin account.

---

## 11. Definizioni conteggi

Usare definizioni coerenti tra totale e breakdown.

| Metrica | Stati inclusi |
|---|---|
| `Positions` | `OPEN`, `PARTIALLY_CLOSED`, `PROTECTED_BE`, `BE_MOVE_PENDING`, `CLOSE_PENDING` |
| `Pending entry` | `WAITING_ENTRY`, `PARTIALLY_FILLED` se qty posizione = 0 |
| `Review` | `REVIEW_REQUIRED` |
| `Closed` | solo stato terminale canonicalizzato `CLOSED` |

Non usare `Open` se include solo una parte degli stati.

---

## 12. Implementazione

### 12.1 Adapter

File: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`

- estrarre prima i campi account-wide raw Bybit;
- implementare `first_not_none`;
- evitare fallback `or` che scarta zero;
- aggiungere `account_unrealized_pnl_usdt`;
- aggiungere metadata `field_origins`;
- mantenere payload raw redatto.

### 12.2 Live exchange port

File: `src/runtime_v2/lifecycle/live_exchange_data_port.py`

- mantenere `captured_at` UTC;
- distinguere `source` live/demo/fallback;
- propagare payload JSON completo;
- segnare fallback statico in modo esplicito.

### 12.3 Worker

Nuovo file proposto:

```text
src/runtime_v2/lifecycle/account_snapshot_worker.py
```

- scheduling periodico per account;
- dedupe refresh per account;
- persistenza via `SnapshotRepository`;
- metriche/log rate-limited;
- health status snapshot.

### 12.4 Bootstrap

File: `main.py`

- inizializzare `AccountSnapshotWorker`;
- avviarlo con task async;
- trigger startup snapshot;
- collegare `Refresh account snapshot` dashboard a un trigger worker non bloccante.

### 12.5 Repository

File: `src/runtime_v2/lifecycle/repositories.py`

- persistere payload reale;
- salvare status/error;
- fornire query latest snapshot per account;
- non sovrascrivere history.

### 12.6 Control plane

File principali:

```text
src/runtime_v2/control_plane/status_queries.py
src/runtime_v2/control_plane/formatters/dashboard.py
src/runtime_v2/control_plane/formatters/templates/dashboard.py
src/runtime_v2/control_plane/dashboard_manager.py
```

- query latest snapshot per singolo account e per tutti gli account;
- aggregazione global only over fresh snapshots;
- rendering freshness;
- fix title/scope dopo filtro account;
- rimuovere filtro Period PnL fino a implementazione reale.

---

## 13. Test obbligatori

### 13.1 Adapter unit

1. payload Unified Bybit con:
   - `totalEquity`;
   - `totalAvailableBalance`;
   - `totalInitialMargin`;
   - `totalPerpUPL`.

2. fallback coin USDT.

3. valore `0.0` conservato senza fallback.

4. `totalPositionIM + totalOrderIM` quando `totalInitialMargin` assente.

5. risposta incompleta → `None`, non valore semanticamente errato.

6. errore `fetch_balance()` → snapshot `None`.

### 13.2 Live exchange port

1. source `ccxt_bybit:demo` propagato;
2. payload raw propagato;
3. fallback statico marcato `fallback_static`;
4. `total_open_risk_usdt` resta dato locale.

### 13.3 Worker

1. startup snapshot per ogni account configurato;
2. periodic snapshot per ogni account;
3. account A fallisce, account B continua;
4. non creare due fetch concorrenti stesso account;
5. persistenza payload non `{}`;
6. old snapshot conservato dopo failure.

### 13.4 PnL dashboard

1. account scope mostra captured_at e age;
2. trader scope etichetta `Account snapshot — <account>`;
3. globale aggrega latest snapshot per account;
4. globale esclude snapshot stale dai live totals;
5. globale mostra account stale;
6. account senza trade ma con snapshot compare;
7. `Refresh` richiede un nuovo account snapshot;
8. `Period` non è visibile finché non applicato davvero;
9. totale Positions coincide con somma breakdown account.

---

## 14. Criteri di accettazione

La feature è accettata solo se:

1. Ogni valore account nel dashboard può essere ricondotto a:
   - account;
   - source;
   - captured_at;
   - payload audit;
   - field origin.

2. Un dashboard aggiornato alle `14:32:05` non può far credere live uno snapshot delle `12:10:00`.

3. Il dashboard globale non può usare il snapshot di un solo account come rappresentazione di tutti gli account.

4. `equity_usdt`, `available_balance_usdt` e `margin_used_usdt` usano campi Bybit con semantica documentata oppure sono `NULL`.

5. Il payload raw è persistito senza segreti.

6. Il tab PnL non espone controlli che non influenzano i dati.

7. Gli account senza trade chain ma con snapshot sono visibili nel globale.

8. Un trader scope non attribuisce mai equity/balance account al singolo trader.

---

## 15. Non scope

Questa spec non implementa:

- calcolo del ROI;
- equity curve storica;
- transfer history;
- spot/collateral multi-asset valuation completa;
- trading PnL tax/accounting;
- riconciliazione storica completa tra PnL Bybit e chain;
- websocket wallet stream.

Questi elementi possono essere aggiunti dopo che snapshot REST, freshness e audit sono corretti.

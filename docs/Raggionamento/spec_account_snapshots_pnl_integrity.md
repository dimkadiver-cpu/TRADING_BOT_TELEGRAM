# Spec — Account Snapshots Exchange & PnL Data Integrity

**Status:** Proposed  
**Scope:** Runtime V2 · Bybit via CCXT · `ops_account_snapshots` · tab `💰 PnL`  
**Priority:** P0 for data correctness, P1 for dashboard usability

---

## 1. Obiettivo

Rendere verificabili e semanticamente corretti i dati account mostrati nel tab `💰 PnL`.

Il sistema deve distinguere chiaramente tra:

1. dati ottenuti dall'exchange;
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

Con `mode: demo`, l'adapter attiva Bybit Demo Trading. Quindi i valori provengono da un conto Demo Bybit reale, non da un conto live.

### 2.2 Quando lo snapshot viene creato

Lo snapshot account viene acquisito durante la valutazione di un nuovo segnale.

Non esiste un worker periodico dedicato all'account balance.

Conseguenze:

- nessun nuovo segnale → nessun nuovo snapshot account;
- `🔄 Refresh` dashboard non esegue `fetch_balance()` — vedi §2.3;
- il refresh dashboard legge semplicemente l'ultimo record DB;
- gli intervalli di position sync non aggiornano automaticamente balance/equity account.

### 2.3 Comportamento del pulsante `🔄 Refresh`

Il pulsante `🔄 Refresh` esegue due operazioni distinte:

1. **Position sync (REST):** chiama `run_bulk_position_sync()` → `fetch_all_positions()` su Bybit. Aggiorna le posizioni aperte nel DB (`ops_market_snapshots`, quote per chain).
2. **Re-render dal DB:** legge `status_queries.get_pnl()` che restituisce l'ultimo record di `ops_account_snapshots`.

**Non viene eseguita nessuna chiamata `fetch_balance()`.**

Equity, balance e margin mostrati dopo il Refresh rispecchiano l'ultimo snapshot account salvato nel DB, non un dato aggiornato dall'exchange. Questo è il comportamento attuale, non quello target.

### 2.4 Problemi attuali

| ID | Problema | Severità |
|---|---|---|
| AS-01 | Snapshot non periodico: Equity/Balance/Margin possono essere vecchi | P0 |
| AS-02 | `Updated` dashboard indica render time, non `captured_at` dello snapshot. Refresh aggiorna le posizioni (REST) ma non l'account balance | P0 |
| AS-03 | Mappatura `Equity` non usa in modo esplicito il campo account-wide Bybit `totalEquity` | P0 |
| AS-04 | Mappatura `Available Balance` usa fallback semanticamente diversi e può usare un campo deprecato (`availableToWithdraw`, `walletBalance`) | P0 |
| AS-05 | `Margin used` non rappresenta una definizione univoca; fallback non somma position IM e order IM correttamente | P0 |
| AS-06 | Il payload raw CCXT/Bybit viene costruito correttamente da `LiveExchangeDataPort`, ma `entry_gate.py:2463` lo sovrascrive con la stringa letterale `"{}"` prima della persistenza | P0 |
| AS-07 | Scope trader mostra snapshot account, con rischio di attribuirlo al trader | P1 |
| AS-08 | Vista globale non aggrega le latest snapshot per account; usa `LIMIT 1` sul record più recente tra tutti gli account | P1 |
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

### 3.4 Freshness e validità

Ogni snapshot deve avere:

- `captured_at`: timestamp UTC assegnato dal bot immediatamente dopo la risposta exchange;
- `source`: per esempio `ccxt_bybit:demo`;
- `freshness_seconds`: calcolato nel rendering come `now_utc() - captured_at` (dove `now_utc` è il tempo del server al momento del render, non il timestamp Telegram);
- `stale`: booleano derivato dalla freshness.

**Definizioni formali:**

- Uno snapshot è **valid** se `snapshot_status = 'OK'`.
- Uno snapshot è **fresh** se `age_seconds < account_snapshot_stale_after_seconds` (configurazione §6.2).
- L'aggregato globale include solo snapshot **valid AND fresh**.
- Un snapshot **valid ma non fresh** è mostrato come `STALE`, non escluso dal DB.

---

## 4. Modello dati target

### 4.1 Schema corrente della tabella

La tabella `ops_account_snapshots` esiste già con le colonne seguenti:

| Colonna | Tipo | Note |
|---|---|---|
| `snapshot_id` | INTEGER PRIMARY KEY | autoincrement |
| `account_id` | TEXT | |
| `equity_usdt` | REAL | |
| `available_balance_usdt` | REAL | |
| `total_open_risk_usdt` | REAL | |
| `total_margin_used_usdt` | REAL | |
| `source` | TEXT | |
| `captured_at` | TEXT | ISO UTC |
| `payload_json` | TEXT | già presente; attualmente salvato come `"{}"` per il percorso segnale (bug AS-06) |

### 4.2 Colonne da aggiungere

```sql
ALTER TABLE ops_account_snapshots ADD COLUMN account_unrealized_pnl_usdt REAL;
ALTER TABLE ops_account_snapshots ADD COLUMN snapshot_status TEXT NOT NULL DEFAULT 'OK';
ALTER TABLE ops_account_snapshots ADD COLUMN error_code TEXT;
```

Non sovrascrivere record vecchi. I record pre-migrazione avranno `snapshot_status = 'OK'` per default (coerente con il loro stato di inserimento senza errori noti).

**File schema da aggiornare:** verificare la presenza di uno script SQL di init nel progetto (tipicamente `schema.sql` o `migrations/`). Aggiornare quello script oltre a eseguire le ALTER TABLE sull'istanza esistente.

### 4.3 Schema target completo

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
| `payload_json` | json | Payload raw CCXT/Bybit completo o redatto, con `field_origins` embedded |
| `snapshot_status` | text | `OK`, `FALLBACK`, `FAILED` |
| `error_code` | text nullable | Errore sintetico, senza segreti |

### 4.4 Indici

```sql
CREATE INDEX IF NOT EXISTS idx_ops_account_snapshots_account_captured
ON ops_account_snapshots(account_id, captured_at DESC, snapshot_id DESC);
```

---

## 5. Mappatura Bybit target

L'adapter deve usare il payload raw Bybit in modo esplicito.

Per Unified Account, il payload di `fetch_balance()` restituisce in `info.result.list[0]` i campi account-wide. I campi preferiti sono a livello account (non a livello coin):

| Campo interno | Campo Bybit preferito | Percorso nel payload | Fallback ammesso |
|---|---|---|---|
| `equity_usdt` | `totalEquity` | `info.result.list[0].totalEquity` | `coin.USDT.equity` solo se account-wide assente |
| `available_balance_usdt` | `totalAvailableBalance` | `info.result.list[0].totalAvailableBalance` | valore CCXT `free.USDT` con flag `field_origin=ccxt_free_usdt` |
| `total_margin_used_usdt` | `totalInitialMargin` | `info.result.list[0].totalInitialMargin` | `totalPositionIM + totalOrderIM` |
| `account_unrealized_pnl_usdt` | `totalPerpUPL` | `info.result.list[0].totalPerpUPL` | somma posizioni, se affidabile |
| `total_open_risk_usdt` | n/a | n/a | calcolo bot |

### 5.1 Regole di fallback

1. Un fallback deve essere esplicitamente tracciato nel `payload_json` derivato nel campo `field_origins`:
   ```json
   {
     "field_origins": {
       "equity_usdt": "bybit.totalEquity",
       "available_balance_usdt": "ccxt.free.USDT",
       "total_margin_used_usdt": "bybit.totalPositionIM_plus_totalOrderIM"
     }
   }
   ```
   `field_origins` è embedded in `payload_json` (non una colonna separata della tabella).

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

1. risolvere tutti gli account configurati da `ExecutionConfig` (es. `execution_config.all_account_ids()` o equivalente disponibile);
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
- stale warning: 180 secondi (usato sia nel worker sia nel rendering dashboard);
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

**Meccanismo di deduplica:** il worker mantiene internamente un set in-memory `_pending_refresh: set[str]` indicizzato per `account_id`. Un trigger immediato non deve creare richieste concorrenti sullo stesso account: se esiste una richiesta in corso per quell'account, aggiunge l'`account_id` a `_pending_refresh`; al termine della richiesta corrente, se l'account è in `_pending_refresh`, viene rieseguito una sola volta e rimosso dal set.

### 6.4 Degradazione

Se exchange non risponde:

- non cancellare o sovrascrivere l'ultimo snapshot valido;
- registrare **sempre** un record `FAILED` nella tabella `ops_account_snapshots` (per audit trail); emettere opzionalmente anche una health event;
- dashboard mostra l'ultimo snapshot valido con age reale;
- se age > `account_snapshot_stale_after_seconds`, dashboard mostra `STALE`;
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
- `field_origins` (embedded nel JSON);
- adapter mode;
- timestamp exchange se presente.

### 7.2 Correzione bug AS-06

Il bug si trova in `src/runtime_v2/lifecycle/entry_gate.py:2463`:

```python
# codice attuale — BUG
s.source, s.captured_at.isoformat(), "{}",
```

`LiveExchangeDataPort.get_account_state()` propaga già correttamente `payload_json` via `_to_payload_json(raw.payload)`. Il payload viene perso perché `entry_gate.py` sovrascrive il campo con la stringa letterale `"{}"`.

Il fix richiesto:

```python
# fix
s.source, s.captured_at.isoformat(), s.payload_json,
```

La stessa sostituzione va applicata al record `ops_market_snapshots` alla riga successiva (`entry_gate.py:2479`), dove il medesimo pattern è presente per `market_snapshot`.

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

- leggere l'ultimo snapshot **valid** (`snapshot_status = 'OK'`) dell'account;
- calcolare age come `now_utc() - captured_at`;
- non cercare snapshot per trader;
- usare PnL storico filtrato per scope.

### 8.2 Scope globale

Per scope globale:

1. ottenere latest valid snapshot per ogni account configurato (CTE con `ROW_NUMBER() PARTITION BY account_id`);
2. aggregare solo snapshot con freshness entro `account_snapshot_stale_after_seconds`;
3. indicare account stale o missing separatamente;
4. non selezionare un solo snapshot "più recente tra tutti" (bug AS-08 attuale).

Struttura target:

```python
{
  "accounts": [
    {
      "account_id": "demo_1",
      "snapshot": {...},
      "age_seconds": 18,
      "stale": False,   # bool: age > account_snapshot_stale_after_seconds
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

- Sommare solo gli account con snapshot **valid AND fresh** (`snapshot_status='OK'` AND `age_seconds < account_snapshot_stale_after_seconds`) per i totali live.
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
─────────────────────────────────────
Realized — All time:
Gross:          +280.20 USDT
Fees:             22.10 USDT
Funding:           4.30 USDT
Net:            +253.80 USDT
─────────────────────────────────────
Positions: 4 · Pending entry: 2 · Review: 0
─────────────────────────────────────
* Open risk is calculated by bot.
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

Lo stato dati account deve mostrare sempre `captured_at` e `age` calcolato come `now_utc() - captured_at` al momento del render. L'age mostrato rispecchia la freschezza del dato, non il tempo dall'ultimo click Refresh (che aggiorna le posizioni ma non l'account balance — §2.3).

---

## 10. Filtri PnL

### 10.1 Scope

Applicare il design già approvato nella spec dei filtri dashboard (`docs/Raggionamento/spec_dashboard_stats_filters.md` o documento equivalente più recente):

```text
Account → Trader
```

- Trader disponibile solo dopo account.
- Cambio account cancella trader.
- La lista trader è limitata all'account selezionato.
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
| `Pending entry` | `WAITING_ENTRY`, `PARTIALLY_FILLED` con qty posizione aperta = 0 |
| `Review` | `REVIEW_REQUIRED` |
| `Closed` | solo stato terminale canonicalizzato `CLOSED` |

Non usare `Open` se include solo una parte degli stati.

La condizione "qty posizione aperta = 0" per `PARTIALLY_FILLED` richiede un join con `ops_position_snapshots` o il campo `open_position_qty` sulla chain. Usare il campo già presente sulla chain, non un join live sulle posizioni.

---

## 12. Implementazione

### 12.0 Prerequisiti — modelli (da fare prima di ogni altro step)

**File: `src/runtime_v2/execution_gateway/models.py`**

Aggiornare `RawAccountSnapshot`:

- aggiungere campo `account_unrealized_pnl_usdt: float | None = None`;
- aggiungere campo `field_origins: dict[str, str] = {}` (tracciamento delle sorgenti, embedded nel payload).

**File: `src/runtime_v2/lifecycle/ports.py`**

Aggiornare `AccountStateSnapshot`:

- aggiungere campo `account_unrealized_pnl_usdt: float | None = None`;
- aggiungere campo `snapshot_status: str = "OK"`;
- aggiungere campo `error_code: str | None = None`.

Questi due file sono prerequisiti per tutti gli step successivi. Senza di essi, adapter, port e repository non possono propagare i nuovi campi.

### 12.1 Adapter

File: `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`

- estrarre prima i campi account-wide raw Bybit da `info.result.list[0]` (livello account, non coin);
- implementare `first_not_none` e sostituire tutte le catene `_safe_float(a) or _safe_float(b)`;
- aggiungere `account_unrealized_pnl_usdt` da `totalPerpUPL`;
- popolare `field_origins` con le sorgenti effettive usate per ogni campo;
- mantenere payload raw redatto (senza API key/secret).

### 12.2 Live exchange port

File: `src/runtime_v2/lifecycle/live_exchange_data_port.py`

- propagare `account_unrealized_pnl_usdt` da `raw` a `AccountStateSnapshot`;
- propagare `snapshot_status` e `error_code`;
- il fallback statico deve impostare `source = "fallback_static"` e `snapshot_status = "FALLBACK"`.

### 12.3 Bug fix AS-06 — entry gate

File: `src/runtime_v2/lifecycle/entry_gate.py`

- **Riga 2463:** sostituire `"{}"` con `s.payload_json` nell'INSERT di `ops_account_snapshots`;
- **Riga 2479:** stessa sostituzione per `ops_market_snapshots` (bug gemello);
- aggiungere le nuove colonne `account_unrealized_pnl_usdt`, `snapshot_status`, `error_code` nell'INSERT.

### 12.4 Worker

Nuovo file:

```text
src/runtime_v2/lifecycle/account_snapshot_worker.py
```

- scheduling periodico per account (intervallo da config §6.2);
- dedupe refresh per account tramite `_pending_refresh: set[str]` (§6.3);
- persistenza via `SnapshotRepository.save_account()`;
- log rate-limited;
- gestione errori per account indipendente: record `FAILED` su eccezione.

### 12.5 Bootstrap

File: `main.py` (entry point confermato; `src/runtime_v2/control_plane/bootstrap.py` gestisce solo il control plane)

- inizializzare `AccountSnapshotWorker` con riferimento a `execution_config` e adapter registry;
- avviarlo come task async all'avvio;
- trigger startup snapshot per ogni account;
- collegare il pulsante `🔄 Refresh` a un trigger worker non bloccante per l'account snapshot (in aggiunta all'esistente `position_sync_fn`).

### 12.6 Repository

File: `src/runtime_v2/lifecycle/repositories.py`

- aggiornare `save_account()` per includere le nuove colonne (`account_unrealized_pnl_usdt`, `snapshot_status`, `error_code`);
- aggiungere query `get_latest_snapshot(account_id)` che restituisce l'ultimo record `valid`;
- aggiungere query `get_latest_snapshots_all_accounts()` con CTE `ROW_NUMBER() PARTITION BY account_id`;
- garantire persistenza append-only (nessun UPDATE su record esistenti).

### 12.7 Control plane

File principali:

```text
src/runtime_v2/control_plane/status_queries.py
src/runtime_v2/control_plane/formatters/dashboard.py
src/runtime_v2/control_plane/formatters/templates/dashboard.py
src/runtime_v2/control_plane/dashboard_manager.py
```

- sostituire query globale da `LIMIT 1` globale a CTE per-account (§8.2);
- aggregazione global only over fresh + valid snapshots;
- rendering freshness con `age_seconds` calcolato al momento del render;
- fix title/scope dopo filtro account;
- rimuovere filtro Period PnL fino a implementazione reale;
- dashboard mostra sempre `captured_at` e age, mai solo render time.

---

## 13. File di test

I test vanno in:

```text
tests/runtime_v2/lifecycle/test_account_snapshot_worker.py
tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_snapshot.py
tests/runtime_v2/control_plane/test_pnl_dashboard.py
```

### 13.1 Adapter unit

1. payload Unified Bybit con:
   - `totalEquity`;
   - `totalAvailableBalance`;
   - `totalInitialMargin`;
   - `totalPerpUPL`.

2. fallback coin USDT quando campi account-wide assenti.

3. valore `0.0` conservato senza fallback (`first_not_none` vs `or`).

4. `totalPositionIM + totalOrderIM` quando `totalInitialMargin` assente.

5. risposta incompleta → `None`, non valore semanticamente errato.

6. errore `fetch_balance()` → snapshot `None`.

7. `field_origins` popolato correttamente per ogni path (primary, fallback, assente).

### 13.2 Live exchange port

1. source `ccxt_bybit:demo` propagato;
2. payload raw propagato (non `{}`);
3. fallback statico marcato `source=fallback_static`, `snapshot_status=FALLBACK`;
4. `total_open_risk_usdt` resta dato locale.

### 13.3 Worker

1. startup snapshot per ogni account configurato;
2. periodic snapshot per ogni account;
3. account A fallisce → record FAILED salvato; account B continua;
4. non creare due fetch concorrenti sullo stesso account (`_pending_refresh`);
5. payload persistito non `{}`;
6. vecchio snapshot conservato dopo failure (append-only).

### 13.4 PnL dashboard

1. account scope mostra `captured_at` e age calcolato al render;
2. trader scope etichetta `Account snapshot — <account>`;
3. globale aggrega latest snapshot per account (non LIMIT 1 globale);
4. globale esclude snapshot stale dai live totals;
5. globale mostra account stale con age;
6. account senza trade ma con snapshot compare nel globale;
7. `Refresh` — posizione sync già esistente; dopo implementazione worker, aggiunge trigger account snapshot;
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

9. Dopo un click `🔄 Refresh`, il dashboard mostra age dell'account snapshot aggiornato (non dell'ultimo render). Se il worker non è ancora tornato, mostra il dato precedente con age reale.

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

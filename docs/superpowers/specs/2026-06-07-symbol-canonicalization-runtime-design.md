# Runtime V2 Symbol Canonicalization Design

Date: 2026-06-07
Status: Draft for review
Scope: runtime_v2 only, forward-only for new data

## Goal

Eliminare la doppia rappresentazione interna del simbolo nel `runtime_v2`.

Da ora in poi il formato canonico interno sara sempre raw exchange-style:

- `FIDAUSDT`
- `BTCUSDT`

Il formato user-facing con slash:

- `FIDA/USDT`
- `BTC/USDT`

resta confinato al rendering e non deve piu entrare nella persistenza o nella logica di matching runtime.

## Problem

Oggi il sistema usa due dialetti per `symbol`:

- il runtime/lifecycle/control-plane spesso lavora con `FIDA/USDT`
- l'ingest exchange normalizza i raw event a `FIDAUSDT`

Questo crea mismatch nei punti che fanno lookup `symbol + side`, in particolare per l'attribuzione di funding event alla chain corretta.

Effetto osservato:

- `ops_trade_chains.symbol` puo contenere `FIDA/USDT`
- un funding event exchange arriva come `FIDAUSDT`
- `resolve_chain_for_fill(symbol, side)` usa match esatto
- il funding non viene agganciato alla chain
- il `POSITION CLOSED` finale mostra `Funding: +-0.00 USDT` anche se l'exchange ha funding reale

## Decision

La source of truth interna per `symbol` nel `runtime_v2` diventa `raw`.

Decisioni operative:

- normalizzazione il prima possibile, appena il segnale entra nel runtime
- nessun backfill dei dati storici
- nessuna modifica alla semantica del formatter display
- nessuna introduzione di un doppio campo `symbol_raw` / `symbol_display`

## Chosen Approach

Approccio scelto: `raw-first nel runtime`.

Il simbolo viene convertito a raw in ingresso al runtime, prima che sia usato da:

- `signal_enrichment`
- `entry_gate`
- persistenza `ops_trade_chains`
- payload dei command
- lookup runtime che usano `symbol + side`

I formatter continuano a chiamare `display_symbol()` per convertire `BTCUSDT -> BTC/USDT` solo in uscita verso l'utente.

## Architecture

### Owner layer

Il layer owner della canonicalizzazione e il boundary di ingresso del `runtime_v2`, non il control-plane formatter e non il repository exchange.

La regola deve valere prima che `signal.symbol` venga propagato nel dominio runtime.

### Invariants

Per nuove chain e nuovi eventi runtime:

- `EnrichedSignalPayload.symbol` deve essere raw
- `TradeChain.symbol` deve essere raw
- `ops_trade_chains.symbol` deve essere raw
- i command payload runtime devono contenere raw
- i raw event exchange normalizzati devono continuare a contenere raw

Per il display:

- il formatter deve continuare a mostrare slash-style quando opportuno

## Implementation Surface

### 1. Normalize at runtime ingress

Nel punto in cui `signal_enrichment` legge `result.canonical_message.signal.symbol`, il valore viene normalizzato a raw prima di costruire `EnrichedSignalPayload`.

La funzione di normalizzazione deve essere idempotente:

- `FIDAUSDT -> FIDAUSDT`
- `FIDA/USDT -> FIDAUSDT`
- `FIDA/USDT:USDT -> FIDAUSDT`

Se il simbolo e vuoto o assente, il comportamento resta invariato.

### 2. Preserve downstream usage

`entry_gate` e `TradeChainRepository` non devono introdurre nuove conversioni.

Devono limitarsi a usare `signal.symbol` / `chain.symbol`, che a quel punto sono gia canonici.

### 3. Leave exchange ingest logic simple

Il matching funding/TP/SL basato su `symbol + side` non va complicato con fallback multi-formato.

La semplificazione desiderata e:

- runtime domain raw
- exchange raw raw
- match esatto raw-to-raw

### 4. Keep display helper pure

`display_symbol()` resta una helper di presentazione.

Non deve essere importata nei layer di:

- persistenza
- lifecycle ownership
- exchange event resolution

## Compatibility Notes

Il rischio principale non e nei formatter, ma nei boundary che oggi possono aspettarsi slash-format:

- `symbol_exists(account_id, symbol)`
- `get_symbol_market_state(account_id, symbol)`
- eventuali test o adapter fake che costruiscono mercati keyed con slash-format

Il design richiede di allineare questi boundary al formato raw oppure di confermare che li supportino gia.

Non e previsto supporto storico per chain vecchie con simboli slash-format in questa modifica. La compatibilita storica resta best-effort e fuori scope.

## Testing Strategy

### Primary signal

Per una nuova chain FIDA:

- la chain nasce con `symbol=FIDAUSDT`
- un funding event `FIDAUSDT` viene attribuito correttamente
- `cumulative_funding` viene aggiornato
- il `POSITION CLOSED` finale mostra funding reale, non `+-0.00 USDT`

### Required tests

1. Test unit di normalizzazione al boundary runtime

- input `FIDA/USDT`
- input `FIDAUSDT`
- input `FIDA/USDT:USDT`
- output sempre `FIDAUSDT`

2. Test lifecycle/repository

- nuova chain creata da segnale slash-style
- `ops_trade_chains.symbol` persistito come raw

3. Test formatter

- payload con `BTCUSDT`
- output user-facing con `BTC/USDT`

4. Test integrazione funding

- chain nuova con simbolo raw
- funding event raw sulla stessa side
- funding accumulato sulla chain
- `final_result.funding` e `total_pnl_net` coerenti

## Non-Goals

- backfill del DB storico
- migrazione delle chain esistenti
- redesign del formatter
- introduzione di due campi simbolo distinti nel dominio
- supporto permanente a formati misti nel core runtime

## Risks

1. Alcuni punti del runtime potrebbero dipendere implicitamente dal formato slash-style.

Mitigazione:

- verificare esplicitamente `symbol_exists`, `get_symbol_market_state`, adapter fake, test di integrazione gateway

2. Alcuni test potrebbero riflettere il formato vecchio invece del contratto desiderato.

Mitigazione:

- aggiornare i test che controllano la persistenza/runtime domain
- lasciare invariati i test di puro display

3. Catene storiche gia persistite con slash-format resteranno incoerenti rispetto alla nuova regola.

Mitigazione:

- accettato esplicitamente: ambiente di sviluppo, forward-only

## Acceptance Criteria

La modifica e accettata quando tutte queste condizioni sono vere:

1. Per nuove chain `ops_trade_chains.symbol` usa sempre il formato raw.
2. I formatter continuano a mostrare slash-format all'utente.
3. Un funding event raw viene attribuito correttamente a una nuova chain raw.
4. Il `POSITION CLOSED` finale include funding reale quando presente.
5. Nessuna logica runtime nuova dipende da `display_symbol()`.

## Rollout

Rollout semplice, senza migrazione:

- cambiare la canonicalizzazione in ingresso al runtime
- aggiornare i test
- usare solo nuove chain per verificare il comportamento

## Open Decisions Resolved

- Canonico interno: `raw`
- Scope storico: nessun backfill
- Punto di normalizzazione: il prima possibile nel runtime
- Formatter: solo presentation layer

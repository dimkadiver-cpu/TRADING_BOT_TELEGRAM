# Bug / Design Note V2: MARKET entry con qty deferred

## Stato

Documento aggiornato per il runtime reale attuale.

Il contenuto precedente individuava bene il sintomo, ma sottostimava tre aspetti:

- il path `C_SIMPLE_ATTACHED`;
- il path `D_POSITION_TPSL` con multi-TP partial;
- il comportamento reale di Bybit su TP/SL partial, che ragionano in `qty`, non in percentuale dinamica della posizione.

---

## Sintomo osservato

Segnale `MARKET` senza prezzo numerico nel messaggio:

- passa parser/enrichment;
- arriva al lifecycle;
- viene bloccato con:

```text
REVIEW_REQUIRED
reason = missing_market_price_for_market_entry
```

Effetto:

- nessuna trade chain utile;
- nessun `PLACE_ENTRY`;
- nessuna execution chain.

---

## Root cause reale

### 1. Il lifecycle pretende un prezzo MARKET subito

In `src/runtime_v2/lifecycle/risk_capacity.py`:

```python
if first_leg.entry_type == "MARKET":
    if market_snapshot is None or market_snapshot.mark_price is None:
        return RiskDecision(passed=False, reason="missing_market_price_for_market_entry")
    entry_price = market_snapshot.mark_price
```

Quindi oggi il risk engine pretende di conoscere il prezzo entry prima di creare i command payload.

### 2. Il port usato dal gate non garantisce un mark price utile

`StaticExchangeDataPort` restituisce normalmente uno snapshot senza `mark_price` valorizzato se non viene iniettato esplicitamente.

### 3. La qty oggi viene calcolata troppo presto

Il flusso attuale e':

1. `RiskCapacityEngine` calcola `size_usdt`
2. `LifecycleEntryGate` converte `size_usdt -> qty`
3. `ExecutionGateway` inoltra il payload gia' pronto

Per un `LIMIT` questo va bene.

Per un `MARKET` senza prezzo certo, no: il layer che crea la qty non ha ancora un prezzo live affidabile.

---

## Regola di business da rispettare

### MARKET con prezzo nel messaggio

Il prezzo nel messaggio e' solo:

- un riferimento indicativo;
- opzionalmente un vincolo di tolleranza.

Non deve essere trattato come fill garantito.

### MARKET senza prezzo nel messaggio

Deve essere ammesso.

La qty finale deve essere calcolata al momento del submit ordine usando il prezzo live.

---

## Modello corretto: due layer, due responsabilita'

### Layer rischio

Decide:

- quanto si puo' perdere su quel trade;
- se il setup e' ammissibile;
- come suddividere il rischio tra le leg.

Output target:

- `risk_amount`
- `sl_price`
- `leverage`
- `hedge_mode`
- eventuale quota rischio per leg

### Layer execution

Decide:

- quale prezzo live usare per la leg MARKET;
- quale qty finale inviare all'exchange;
- se la discrepanza col prezzo indicativo del segnale e' accettabile.

Output target:

- `qty`
- `resolved_mark_price`
- eventuale review/fail pre-submit

---

## Nuova logica target

## Caso 1: una sola entry MARKET

### Risk layer

Se `entry_type == MARKET` e non c'e' `mark_price` disponibile:

- non bloccare;
- richiedere comunque `stop_loss`;
- calcolare `risk_amount`;
- salvare nel `risk_snapshot`:
  - `risk_amount`
  - `sl_price`
  - `leverage`
  - `hedge_mode`
  - `entry_price_deferred = true`
  - `signal_price` se il messaggio lo contiene
  - config `market_execution`

`size_usdt` puo' restare `None`.

### Entry gate

Il command payload entry non deve contenere una qty finale fittizia.

Deve contenere invece:

```json
{
  "qty_mode": "deferred_market",
  "risk_amount": 10.0,
  "sl_price": 0.4900,
  "signal_price": 0.5000,
  "market_execution_mode": "tolerance",
  "market_tolerance_pct": 0.5
}
```

### Execution gateway

Prima di `place_order`:

1. `fetch_mark_price(symbol, execution_account_id)`
2. se manca prezzo live -> `REVIEW_REQUIRED`
3. se esiste `signal_price` e `mode == tolerance`:
   - calcola `deviation_pct`
   - se supera `tolerance_pct` -> `REVIEW_REQUIRED`
4. calcola:

```text
risk_distance = abs(mark_price - sl_price)
qty = risk_amount / risk_distance
```

5. inietta `qty` nel payload
6. invia l'ordine

---

## Caso 2: multi-entry con prima leg MARKET e altre LIMIT

Esempio:

- `E1 MARKET = 70%`
- `E2 LIMIT = 30%`

### Regola corretta

Il rischio totale va prima allocato per leg.

Esempio:

- `risk_amount_total = 10 USDT`
- `risk_amount_e1 = 7 USDT`
- `risk_amount_e2 = 3 USDT`

### Leg MARKET

La qty si calcola solo al submit:

```text
qty_market = risk_amount_e1 / abs(mark_price_live - sl_price)
```

### Leg LIMIT

La qty si puo' calcolare subito:

```text
qty_limit = risk_amount_e2 / abs(limit_price - sl_price)
```

### Conclusione

Nel mixed setup:

- il gate decide il rischio per leg;
- il gateway risolve solo la leg MARKET;
- le leg LIMIT restano deterministiche nel gate.

---

## Caso 3: MARKET + TP/SL su Bybit

Qui c'e' il punto piu' importante che il documento precedente non esplicitava.

### Bybit non ragiona in percentuale dinamica della posizione per i partial TP/SL

Nel path runtime attuale i TP/SL partial vengono inviati con size esplicite:

- `tpSize`
- `slSize`

Quindi:

- il bot pensa in `%`;
- Bybit esegue in `qty`.

Una volta inviati:

- quelle qty restano fissate;
- Bybit non le ricalcola automaticamente quando la posizione cresce dopo un nuovo fill entry.

### Implicazione pratica

Se:

- filla `E1 MARKET = 0.7`
- i TP vengono creati su `0.7`
- poi filla `E2 LIMIT = 0.3`

la posizione reale diventa `1.0`, ma i TP/SL partial possono restare dimensionati sulla vecchia qty.

Quindi il nuovo `0.3` puo' risultare:

- non coperto correttamente dallo SL size;
- non distribuito correttamente sui TP partial.

---

## Bybit: cosa auto-scala e cosa no

### Entire Position

Bybit puo' lavorare su TP/SL position-level "entire position".

Questo e' il caso piu' vicino a una protezione auto-scalata.

### Partial Position / partial TP

Quando usi partial TP/SL con size esplicita:

- non hai auto-resize dinamico affidabile sul nuovo volume totale;
- serve sync/apply lato bot.

### Conseguenza per il runtime

Per `multi-TP partial` il bot deve essere considerato owner del resize.

---

## Caso 4: update che modifica una entry LIMIT in MARKET

Questo documento deve coprire anche un requisito futuro:

- un update Telegram puo' cambiare una leg entry gia' pianificata da `LIMIT` a `MARKET`.

Esempio tipico:

- entry originaria pending a prezzo limit;
- update successivo del trader:
  - "entra a mercato"
  - "market instead"
  - equivalente semantico di replace/modify entry verso `MARKET`

### Implicazione architetturale

Questo caso non e' un semplice amend di prezzo.

Dal punto di vista runtime significa:

1. la leg originaria `LIMIT` non va piu' trattata come qty deterministica da prezzo fisso;
2. l'entry deve diventare una leg `MARKET` con qty deferred;
3. il rischio della leg deve essere ricalcolato/riletto nel nuovo contesto;
4. il submit effettivo deve passare dal medesimo path `deferred_market`;
5. eventuali TP/SL gia' calcolati sulla vecchia qty devono essere rivalutati.

### Regola target

Un update `LIMIT -> MARKET` deve essere trattato semanticamente come:

- cancellazione o disattivazione della vecchia entry limit residua;
- creazione di una nuova intenzione entry market sulla quota residua di rischio/posizione;
- calcolo qty al submit usando prezzo live;
- sync protettivi dopo il fill reale.

### Cosa non fare

Non basta:

- cambiare `entry_type` nel payload;
- lasciare invariata la qty calcolata col vecchio prezzo limit.

Quello produrrebbe una qty incoerente rispetto al rischio reale.

### Requisito futuro esplicito

La V2 del design deve quindi restare compatibile con una futura estensione che supporti:

- update di una leg `LIMIT` pendente verso `MARKET`;
- update dell'intero setup entry da `LIMIT` a `MARKET`;
- riallineamento di TP/SL e size dopo la conversione.

---

## Impatto sui mode attuali

## Mode C - `C_SIMPLE_ATTACHED`

### Single TP

Supportabile bene anche con `deferred_market`, se il gateway risolve la qty prima di inviare `PLACE_ENTRY_WITH_ATTACHED_TPSL`.

### Multi-TP

Non e' il caso principale di `C_SIMPLE_ATTACHED`, ma appena compaiono qty attached parziali il problema torna identico:

- non basta creare una qty iniziale;
- serve riallineamento se la posizione cambia dopo.

## Mode D - `D_POSITION_TPSL`

### One TP

Supportabile:

- entry MARKET deferred;
- TP/SL position-level semplice dopo il fill.

### Multi-TP partial

Questo e' il caso critico.

Le `SET_POSITION_TPSL_PARTIAL` oggi nascono con `tp_size/sl_size` calcolati troppo presto.

Per questo caso ci sono solo due opzioni sane:

1. **versione conservativa**
   - mandare in `REVIEW_REQUIRED` i setup `deferred_market + multi_tp_partial`

2. **versione corretta completa**
   - creare/ricalcolare i TP partial solo dopo il fill reale della posizione
   - e rieseguire il sync dopo ogni nuovo fill entry successivo

---

## Scelta consigliata

### Fase 1 - fix robusto minimo

Supportare:

- `ONE_SHOT MARKET`
- `MARKET + LIMIT`
- `MARKET` con `PLACE_ENTRY`
- `MARKET` con `PLACE_ENTRY_WITH_ATTACHED_TPSL`
- `D_POSITION_TPSL` con un solo TP

Bloccare o mettere in review:

- `deferred_market + multi_tp_partial`

### Fase 2 - supporto completo

Implementare:

- sync TP/SL anche dopo fill entry successivi;
- rebuild/amend delle size partial su Bybit;
- riallineamento continuo a `open_position_qty`.
- supporto update `LIMIT -> MARKET` con percorso `deferred_market`.

---

## File realmente coinvolti

### Sicuri

- `src/runtime_v2/lifecycle/risk_capacity.py`
- `src/runtime_v2/lifecycle/entry_gate.py`
- `src/runtime_v2/execution_gateway/gateway.py`
- `src/runtime_v2/execution_gateway/adapters/base.py`
- `src/runtime_v2/execution_gateway/adapters/ccxt_bybit/adapter.py`
- `src/runtime_v2/execution_gateway/adapters/fake.py`

### Test da aggiornare

- `tests/runtime_v2/lifecycle/test_risk_capacity.py`
- `tests/runtime_v2/lifecycle/test_entry_gate.py`
- `tests/runtime_v2/lifecycle/test_entry_gate_cd.py`
- `tests/runtime_v2/execution_gateway/test_gateway.py`
- `tests/runtime_v2/execution_gateway/test_bybit_order_builder.py`
- `tests/runtime_v2/execution_gateway/test_ccxt_bybit_adapter_unit.py`

---

## Contratto target per il fix

Done significa:

1. un segnale MARKET senza prezzo non viene bloccato nel lifecycle solo per assenza di mark snapshot;
2. la qty della leg MARKET viene calcolata solo al submit ordine;
3. la tolleranza percentuale usa `signal_policy.market_execution`;
4. i path `PLACE_ENTRY` e `PLACE_ENTRY_WITH_ATTACHED_TPSL` supportano `deferred_market`;
5. i setup `multi_tp_partial` non restano in stato incoerente:
   - o supportati con sync vero;
   - o rifiutati/reviewati esplicitamente.

---

## Decisione tecnica raccomandata

Non spostare la fetch del prezzo live dentro `RiskCapacityEngine`.

Il punto corretto e':

- `RiskCapacityEngine` decide il rischio;
- `ExecutionGateway` risolve il prezzo live e la qty finale.

Questo mantiene separati:

- decisione di rischio;
- side effect exchange / execution.

---

## Messaggio finale

Il problema non e' solo "MARKET senza prezzo".

Il problema vero e':

- il sistema oggi calcola alcune qty troppo presto;
- Bybit, nei partial TP/SL, ragiona in size esplicita;
- quindi il mixed setup `MARKET + LIMIT + TP partial` richiede ownership applicativa del resize.

La V2 del fix deve quindi essere pensata come:

- deferred qty per MARKET;
- tolleranza opzionale sul prezzo indicativo;
- supporto esplicito dei casi semplici;
- gestione conservativa o completa dei multi-TP partial.

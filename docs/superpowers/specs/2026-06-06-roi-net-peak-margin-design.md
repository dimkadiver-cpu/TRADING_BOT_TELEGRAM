# ROI Net Redesign — Peak Margin + Return on Risk

**Data**: 2026-06-06  
**Stato**: Draft approvato a livello di design

---

## Obiettivo

Correggere la semantica del report finale `POSITION CLOSED` per le metriche di performance:

- `ROI net` non deve più usare `risk_amount` come denominatore;
- il denominatore del ROI finale deve riflettere il margine realmente impiegato dalla chain;
- il sistema deve distinguere chiaramente `ROI net` da `Return on Risk`;
- i casi reali multi-entry / partial close / TP multipli devono produrre un risultato coerente.

---

## Problema attuale

Oggi il control-plane calcola:

```python
total_pnl_net = cumulative_gross_pnl - cumulative_fees - cumulative_funding
roi_net_pct = total_pnl_net / allocated_margin * 100
```

e `allocated_margin` viene popolato leggendo `risk_snapshot_json.risk_amount` al salvataggio della chain.

Questo modello ha due problemi distinti:

1. **Semantica sbagliata**
   - `risk_amount` è il budget di rischio monetario a stop loss.
   - non è il margine realmente impiegato sulla posizione.
   - usare `risk_amount` come denominatore produce una metrica che assomiglia a `return on risk`, non a un ROI su margine.

2. **Fragilità dati**
   - la colonna `allocated_margin` può risultare `NULL` anche quando `risk_snapshot_json.risk_amount` è presente;
   - di conseguenza il report finale mostra `ROI net: n/a` anche su chain chiuse con PnL finale disponibile.

### Esempi di casi che il modello attuale non rappresenta bene

1. Due entry pianificate, solo una fillata, l'altra cancellata  
   Il denominatore non deve riflettere il piano teorico, ma l'esposizione reale aperta.

2. Partial close da update  
   Il PnL finale è cumulativo sulla chain intera; il denominatore non deve collassare alla size residua finale.

3. TP multipli distribuiti nel tempo  
   Il denominatore deve riflettere il massimo margine realmente impiegato, non il rischio teorico iniziale.

---

## Decisione

Introdurre due metriche separate:

1. **ROI net**
   - Formula: `total_pnl_net / peak_margin_used * 100`
   - Scopo: misurare il rendimento reale della trade rispetto al massimo margine effettivamente impiegato.

2. **Return on Risk**
   - Formula: `total_pnl_net / initial_risk_amount * 100`
   - Scopo: misurare il rendimento rispetto al budget di rischio iniziale deciso dal sistema.

`ROI net` resta la metrica principale del report finale.  
`Return on Risk` viene introdotta come metrica distinta, opzionale da esporre nel rendering in una seconda fase.

---

## Definizioni

### `initial_risk_amount`

- valore iniziale di `risk_snapshot_json.risk_amount` al momento della creazione chain;
- immutabile;
- rappresenta il budget di rischio iniziale della trade.

### `current_margin_used`

Formula:

```python
current_margin_used = open_position_qty * entry_avg_price / leverage
```

Valida solo se:

- `open_position_qty > 0`
- `entry_avg_price is not None`
- `leverage > 0`

### `peak_margin_used`

- massimo storico di `current_margin_used` durante la vita della chain;
- monotono crescente;
- non diminuisce su TP, partial close o close finale;
- cresce solo quando l'esposizione contemporanea reale cresce.

### `total_pnl_net`

Formula:

```python
total_pnl_net = cumulative_gross_pnl - cumulative_fees - cumulative_funding
```

### `roi_net_pct`

Formula:

```python
roi_net_pct = total_pnl_net / peak_margin_used * 100
```

### `return_on_risk_pct`

Formula:

```python
return_on_risk_pct = total_pnl_net / initial_risk_amount * 100
```

---

## Perché `peak_margin_used`

`peak_margin_used` è preferito a:

- somma delle entry fillate;
- margine residuo finale;
- `risk_amount` iniziale;
- notional totale scambiato.

Perché è l'unico denominatore che:

- considera solo la posizione realmente aperta;
- resta corretto con scale-in e scale-out;
- non sovrastima il capitale impiegato quando ci sono entry cancellate;
- non sottostima il capitale impiegato quando la posizione è stata più grande in un momento intermedio.

### Esempio

Sequenza:

1. entry 1 fillata
2. partial close 50%
3. entry 2 fillata

La somma delle fill non rappresenta il capitale contemporaneamente impegnato.  
Il valore corretto per il ROI finale è il massimo margine mai effettivamente in uso, cioè `peak_margin_used`.

---

## Modifiche schema

### Nuove colonne in `ops_trade_chains`

Da aggiungere con una nuova migration:

- `initial_risk_amount REAL`
- `peak_margin_used REAL`

### Stato di `allocated_margin`

`allocated_margin` resta in schema solo per backward compatibility durante la migrazione, ma:

- non deve più essere usato come denominatore principale di `ROI net`;
- può essere deprecato in una fase successiva.

---

## Owner layer e responsabilità

### 1. Creazione chain

**Owner primario**: `src/runtime_v2/lifecycle/entry_gate.py`  
**Owner secondario / compatibilità**: `src/runtime_v2/lifecycle/repositories.py`

Il path reale di creazione di `ops_trade_chains` passa oggi da `entry_gate.py`
tramite `INSERT OR IGNORE` diretto.

Di conseguenza:

- `initial_risk_amount` deve essere popolato nel path di insert di `entry_gate.py`;
- eventuali helper in `repositories.py` devono essere allineati per non lasciare
  path secondari semanticamente divergenti;
- la spec non deve assumere che `TradeChainRepository.save()` sia il punto unico
  di persistenza della chain.

Regola:

- al momento della creazione chain, leggere `risk_snapshot_json.risk_amount`;
- persistarlo in `initial_risk_amount`;
- non usare più questo valore come base di `ROI net`.

`peak_margin_used` iniziale:

- `NULL` se i dati di posizione non sono ancora disponibili;
- oppure primo valore calcolabile se `entry_avg_price`, `open_position_qty`,
  `leverage` sono già presenti.

### 2. Aggiornamento runtime del picco

**Owner**: `src/runtime_v2/lifecycle/workers.py`  
**Source of truth del nuovo stato**: `EventProcessorResult`

`peak_margin_used` deve essere calcolato usando lo stato posizione
**post-evento**, non lo stato precedente letto dalla riga chain prima
dell'update.

In pratica, dopo `processor.process(...)`, il worker deve costruire i valori
effettivi post-evento:

- `effective_entry_avg_price`
- `effective_open_position_qty`
- `effective_leverage`

Regola:

- se `EventProcessorResult` contiene un nuovo valore, usare quello;
- altrimenti usare il valore corrente già presente sulla chain.

Poi calcolare:

```python
current_margin_used = (
    effective_open_position_qty * effective_entry_avg_price / effective_leverage
)
```

e aggiornare monotonicamente:

```python
peak_margin_used = max(existing_peak_margin_used or 0.0, current_margin_used)
```

Vincoli:

- il calcolo e il persist di `peak_margin_used` devono avvenire nella stessa
  transazione che persiste lo stato post-evento della chain;
- `peak_margin_used` non deve mai diminuire.

### 3. Rendering del report finale

**Owner**: `src/runtime_v2/control_plane/outbox_writer.py`

Nel builder `final_result`:

- `roi_net_pct` deve usare `peak_margin_used`;
- `return_on_risk_pct` può essere aggiunto come nuovo campo opzionale;
- se il denominatore richiesto manca, il valore deve restare `None` e il formatter deve mostrare `n/a`.

---

## Eventi che devono aggiornare `peak_margin_used`

Il ricalcolo non è guidato dal nome dell'evento in sé, ma dal fatto che il
`EventProcessorResult` modifichi `entry_avg_price` e/o `open_position_qty`.

In pratica il worker deve tentare il ricalcolo dopo ogni exchange event
processato che può alterare lo stato posizione, inclusi almeno:

- `ENTRY_FILLED`
- `ENTRY_UPDATED`
- `TP_FILLED`
- `SL_FILLED`
- `CLOSE_PARTIAL_FILLED`
- `CLOSE_FULL_FILLED`

Nota:

- per `SL_FILLED` e `CLOSE_FULL_FILLED` il nuovo `current_margin_used` può essere
  zero, ma `peak_margin_used` storico deve restare invariato;
- il picco non va mai abbassato.

---

## Casi funzionali che il design deve coprire

### Caso A — multi-entry con una leg cancellata

- il picco deve considerare solo la size realmente aperta;
- una leg mai fillata e poi cancellata non deve aumentare il denominatore del ROI.

### Caso B — partial close da update

- il PnL finale è cumulativo;
- il ROI finale deve continuare a usare il picco storico, non il margine residuo finale.

### Caso C — TP multipli

- la chiusura finale via `TP_FILLED_FINAL` usa `total_pnl_net` cumulativo;
- il denominatore è il picco storico della chain.

### Caso D — scale-in successivo

- se una fill successiva porta la posizione a una size maggiore, `peak_margin_used` deve aumentare.

### Caso E — chain chiusa con dati incompleti storici

- se `peak_margin_used` non è ricostruibile in modo affidabile, `ROI net` resta `n/a`;
- `Return on Risk` può comunque essere disponibile se `initial_risk_amount` esiste.

---

## Backfill

### Fase 1 — backfill minimo e degradato

Per chain esistenti:

- `initial_risk_amount`:
  - se nullo, leggere `risk_snapshot_json.risk_amount`

- `peak_margin_used`:
  - se possibile stimare da stato finale:

```python
filled_entry_qty * entry_avg_price / leverage
```

Questa stima è ammessa solo come fallback degradato, non come ricostruzione
storicamente accurata.

Vincoli:

- il fallback non deve essere presentato come valore storicamente affidabile;
- se i dati non bastano per una stima minimamente difendibile, lasciare
  `peak_margin_used = NULL` e `ROI net = n/a`.

### Fase 2 — backfill corretto

Per catene storiche complesse:

- rileggere la sequenza eventi lifecycle/exchange;
- ricostruire cronologicamente:
  - `entry_avg_price`
  - `open_position_qty`
  - `current_margin_used`
  - `peak_margin_used`

Questa è la strategia corretta per:

- multi-entry
- partial close
- scale-in / scale-out
- TP multipli

---

## Rollout consigliato

### Fase A — nuovi campi e nuove chain

1. aggiungere colonne `initial_risk_amount` e `peak_margin_used`
2. popolare `initial_risk_amount` alla creazione chain
3. aggiornare `peak_margin_used` durante il lifecycle runtime
4. cambiare `ROI net` in outbox per usare `peak_margin_used`

### Fase B — compatibilità e backfill

5. backfill minimo per le chain esistenti
6. introdurre replay/backfill corretto per chain complesse
7. opzionale: introdurre `Return on Risk` nel rendering finale

### Fase C — cleanup

8. deprecare `allocated_margin` come fondazione del ROI
9. mantenere `allocated_margin` solo come campo legacy finché necessario

---

## Allineamento contratti e documentazione

Questa modifica cambia la semantica user-facing di `ROI net`.

Prima del rollout completo vanno allineati i documenti che oggi definiscono:

```text
ROI net = total_pnl_net / allocated_margin
```

in particolare:

- `docs/runtime_v2/exchange_sync_technical.md`
- `docs/runtime_v2/exchange_sync_overview.md`
- `docs/Raggionamento/Controllo_Notifica/CLEAN_LOG_SPEC.md`
- eventuali spec/design precedenti che descrivono `allocated_margin` come base
  del ROI finale

Fino all'allineamento, questa spec è la source of truth desiderata ma il repo
ha doc drift esplicito su questa metrica.

---

## Impatto sui file

### Da modificare

- `db/ops_migrations/...`
  - nuova migration per `initial_risk_amount`, `peak_margin_used`
- `src/runtime_v2/lifecycle/repositories.py`
  - persist `initial_risk_amount`
- `src/runtime_v2/lifecycle/workers.py`
  - update monotono di `peak_margin_used`
- `src/runtime_v2/control_plane/outbox_writer.py`
  - `ROI net` su `peak_margin_used`
  - opzionale `return_on_risk_pct`
- `src/runtime_v2/control_plane/formatters/clean_log.py`
  - nessun cambio obbligatorio in fase A oltre al consumo di `roi_net_pct`

### Test da aggiungere o aggiornare

- repository save:
  - persistenza `initial_risk_amount`
- lifecycle worker:
  - first fill imposta `peak_margin_used`
  - partial close non abbassa il picco
  - scale-in alza il picco
- outbox writer:
  - `ROI net` usa `peak_margin_used`
  - `Return on Risk` usa `initial_risk_amount` se introdotto
- control-plane formatter:
  - `n/a` quando il denominatore non esiste

---

## Decisioni esplicite

1. `ROI net` e `Return on Risk` sono due metriche diverse e non devono condividere lo stesso denominatore.
2. `risk_amount` non è una base corretta per il ROI finale della trade.
3. Il punto corretto per mantenere il denominatore del ROI è il lifecycle runtime, non il formatter e non il dispatcher.
4. Il report finale deve preferire `n/a` a un valore stimato o semanticamente falso.
5. Il path reale di creazione chain è `entry_gate.py`; ogni path secondario deve essere allineato ma non è l'owner primario della semantica.
6. `peak_margin_used` è una proprietà derivata dallo stato posizione post-evento e va aggiornata nello stesso transaction boundary del persist lifecycle.

---

## Esempio: chain 11

Caso osservato:

- `risk_snapshot_json.risk_amount = 200.0`
- `cumulative_gross_pnl = 46.73088`
- `cumulative_fees = 6.33921077`
- `cumulative_funding = 0.0`
- `total_pnl_net = 40.39166923`
- `allocated_margin = NULL`

Con il modello proposto:

- `initial_risk_amount = 200.0`
- `peak_margin_used ≈ entry_exec_value / leverage ≈ 2858.094 / 5 ≈ 571.62`

Metriche attese:

- `ROI net ≈ 40.39166923 / 571.62 * 100 ≈ 7.06%`
- `Return on Risk ≈ 40.39166923 / 200.0 * 100 ≈ 20.20%`

Questo esempio mostra chiaramente perché i due concetti vanno separati.

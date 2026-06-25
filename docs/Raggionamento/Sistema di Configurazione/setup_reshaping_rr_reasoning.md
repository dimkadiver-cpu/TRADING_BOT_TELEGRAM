# Ragionamento — Setup Reshaping e ridimensionamento TP per RR

**Contesto:** Trading Bot Telegram / Runtime V2  
**Stato:** documento di ragionamento architetturale  
**Data:** 25 giugno 2026  

---

## 1. Problema

I segnali ricevuti da Telegram possono contenere una struttura più ampia di quella che il bot deve realmente eseguire.

Esempio:

```text
4 Entry limit:
E1 / E2 / E3 / E4

8 Take Profit:
TP1 ... TP8

1 Stop Loss originale:
SL0
```

L’obiettivo non è limitarsi a cambiare i pesi, ma permettere una trasformazione logica del setup:

```text
E1  → scartata
E2  → entry operativa 1
E3  → entry operativa 2
E4  → nuovo Stop Loss
SL0 → sostituito / archiviato
TP1...TP8 → ridotti, selezionati o ricreati secondo RR
```

Il setup che arriva dal trader deve rimanere integro come fonte originale. Il bot deve produrre un secondo setup, derivato e auditabile, sul quale calcolare rischio, quantità e ordini exchange.

---

# 2. Principio fondamentale

La trasformazione non deve essere un insieme di flag indipendenti:

```yaml
skip_entry_1: true
use_entry_4_as_sl: true
tp_count: 4
```

Questo modello è fragile perché:

- le regole possono entrare in conflitto;
- non è chiaro l’ordine di applicazione;
- non è semplice capire quale sia il setup finale;
- audit e debug diventano ambigui;
- cambiare un singolo flag può alterare il rischio in modo non evidente.

Il modello corretto è una **proiezione strutturale unica**:

```text
setup sorgente
    ↓
normalizzazione
    ↓
reshape esplicito
    ↓
setup eseguibile
    ↓
risk sizing e execution
```

Ogni elemento del setup sorgente riceve un destino esplicito:

```text
ENTRY
STOP_LOSS
TAKE_PROFIT
DISCARDED
ARCHIVED
```

---

# 3. Separazione dei livelli dati

Il bot non deve sovrascrivere il setup parsato.

Servono almeno quattro livelli distinti.

```text
1. parsed_setup
   └─ ciò che il parser ha estratto dal messaggio Telegram

2. normalized_setup
   └─ ordine e semantica resi coerenti con LONG/SHORT

3. reshaped_setup
   └─ risultato delle regole di trasformazione

4. execution_setup
   └─ quantità, ordini, TP/SL e dettagli exchange
```

## 3.1 `parsed_setup`

È la fonte immutabile.

```json
{
  "side": "LONG",
  "entries": [
    {"sequence": 1, "price": 100},
    {"sequence": 2, "price": 98},
    {"sequence": 3, "price": 96},
    {"sequence": 4, "price": 94}
  ],
  "take_profits": [102, 104, 106, 108, 110, 112, 114, 116],
  "stop_loss": 92
}
```

Non deve essere modificato.

## 3.2 `normalized_setup`

La normalizzazione rende stabile l’indice `E1`, `E2`, `E3`, `E4`.

Regola proposta:

| Side | Ordine normalizzato |
|---|---|
| LONG | entry dalla più alta alla più bassa |
| SHORT | entry dalla più bassa alla più alta |

Quindi:

```text
LONG:
E1 > E2 > E3 > E4

SHORT:
E1 < E2 < E3 < E4
```

Con questa convenzione, `E4` è sempre l’entry più distante e può essere candidata naturale a Stop Loss nel modello descritto.

## 3.3 `reshaped_setup`

È il setup economico che il bot intende eseguire.

```json
{
  "entries": [
    {"sequence": 1, "source_sequence": 2, "price": 98, "weight": 0.60},
    {"sequence": 2, "source_sequence": 3, "price": 96, "weight": 0.40}
  ],
  "stop_loss": {
    "price": 94,
    "source": "entry_reclassified",
    "source_sequence": 4,
    "replaced_original_stop": 92
  }
}
```

## 3.4 `execution_setup`

Aggiunge elementi calcolati:

```text
- planned weighted average entry
- distanza R
- rischio assegnato per leg
- quantità per entry
- TP finali
- close distribution
- ordini exchange
```

---

# 4. Dove inserire il reshape nel flusso

Ordine obbligatorio:

```text
Parser
  ↓
Normalizzazione per side
  ↓
Risoluzione range, se applicabile
  ↓
Setup Reshaping
  ↓
Validazione del setup risultante
  ↓
Calcolo RR
  ↓
TP Reshaping
  ↓
Risk sizing
  ↓
Execution Plan
  ↓
Invio ordini exchange
```

## 4.1 Perché deve precedere il risk sizing

Il rischio dipende da:

```text
- entry operative
- pesi delle entry
- stop loss effettivo
```

Se il sistema calcola prima il rischio sulle quattro entry e poi trasforma E4 in SL, la size risultante è sbagliata.

Il reshape cambia il setup economico. Perciò deve avvenire prima di qualsiasi calcolo di quantità.

---

# 5. Modello concettuale di Setup Reshaping

Ogni regola deve avere:

```text
- una condizione di attivazione;
- una proiezione delle entry;
- una decisione sullo SL;
- una policy TP;
- vincoli di validità;
- un comportamento in caso di fallimento;
- una traccia audit.
```

## 5.1 Regola esempio

```yaml
setup_reshaping:
  enabled: true

  rules:
    - id: ladder_4_to_2_entries_stop
      priority: 100
      enabled: true

      match:
        entry_type: LIMIT
        entry_structure: LADDER
        normalized_entry_count: 4
        tp_count:
          min: 1
          max: 10

      source_indexing: side_normalized

      projection:
        entries:
          - source_sequence: 2
            output_role: ENTRY
            output_sequence: 1
            weight: 0.60

          - source_sequence: 3
            output_role: ENTRY
            output_sequence: 2
            weight: 0.40

          - source_sequence: 4
            output_role: STOP_LOSS
            replace_original_stop: true

        discarded_sources:
          - source_sequence: 1
            reason: initial_entry_skipped

      take_profits:
        mode: select_existing_by_rr

      on_failure: REVIEW
```

## 5.2 Perché `source_indexing: side_normalized`

Non usare l’ordine letterale del messaggio Telegram.

Esempio LONG ricevuto male:

```text
Entry: 94 / 100 / 96 / 98
```

Senza normalizzazione, `E4` potrebbe essere 98 invece del livello più distante.

Dopo normalizzazione LONG:

```text
E1 = 100
E2 = 98
E3 = 96
E4 = 94
```

La regola ha un significato stabile.

---

# 6. Trasformazione delle entry

## 6.1 Caso obiettivo: 4 entry → 2 entry + SL

```text
Input LONG normalizzato:

E1 = 100
E2 = 98
E3 = 96
E4 = 94
SL0 = 92
```

Trasformazione:

```text
E1 = discarded
E2 = Entry_1, 60%
E3 = Entry_2, 40%
E4 = Stop Loss
SL0 = archived / replaced
```

Output:

```text
Entry_1 = 98, peso 60%
Entry_2 = 96, peso 40%
SL effettivo = 94
```

## 6.2 Significato economico

La trasformazione non “sposta” semplicemente lo stop.

Fa tre operazioni:

1. Esclude una entry più aggressiva.
2. Riduce il numero delle entry eseguibili.
3. Porta lo stop più vicino alle entry residue.

Questo può ridurre il rischio assoluto per quantità equivalente, ma il sizing a rischio fisso probabilmente aumenterà la size nominale. Non deve essere interpretato automaticamente come setup più sicuro: dipende dal nuovo rischio unitario e dalla probabilità che il nuovo SL venga colpito.

## 6.3 Il caso `E4` come SL non è sempre valido

Può fallire se:

```text
LONG:
E4 >= E2 o E3
oppure E4 coincide con una entry residua

SHORT:
E4 <= E2 o E3
oppure E4 coincide con una entry residua
```

La regola deve fallire in `REVIEW`, non cercare una correzione implicita.

---

# 7. Invarianti obbligatori

Una trasformazione è valida solo se rispetta gli invarianti seguenti.

## 7.1 Coerenza long

```text
SL < tutte le entry residue
TP > tutte le entry residue
```

## 7.2 Coerenza short

```text
SL > tutte le entry residue
TP < tutte le entry residue
```

## 7.3 Entry

```text
- almeno una entry residua;
- peso di ogni entry >= 0;
- somma pesi = 1;
- nessun prezzo entry nullo;
- nessuna entry coincide con lo SL;
- nessuna duplicazione non esplicitamente ammessa.
```

## 7.4 Stop Loss

```text
- esiste uno SL effettivo;
- distanza da anchor > 0;
- distanza minima configurabile;
- è nella direzione di perdita;
- non supera eventuali limiti di distanza massima.
```

## 7.5 Take profit

```text
- almeno un TP;
- livelli monotoni nella direzione corretta;
- ogni TP è oltre l’anchor;
- close distribution somma a 100%;
- quantità risultanti rispettano min size e precisione exchange.
```

---

# 8. Definizione dell’anchor per RR

Per convertire i TP in RR, bisogna prima definire il prezzo di riferimento.

Possibili modelli:

| Modalità | Definizione | Vantaggi | Problemi |
|---|---|---|---|
| `first_entry` | prezzo della prima entry residua | semplice | ignora averaging |
| `planned_weighted_average` | media ponderata delle entry pianificate | coerente con il piano iniziale | dipende dall’esecuzione delle leg |
| `filled_average` | media reale dei fill | economicamente precisa | non nota prima dei fill |
| `worst_case_entry` | entry più sfavorevole | conservativa | altera la strategia e gli RR |

## 8.1 Scelta consigliata

Per il piano iniziale:

```yaml
rr_anchor:
  mode: planned_weighted_average
```

Formula:

```text
anchor =
Σ(entry_price_i × weight_i)
```

Per il caso:

```text
Entry_1 = 98, peso 0,60
Entry_2 = 96, peso 0,40
```

```text
anchor = 98 × 0,60 + 96 × 0,40
anchor = 97,2
```

Con:

```text
SL = 94
```

```text
R = 97,2 - 94 = 3,2
```

Per uno SHORT:

```text
R = SL - anchor
```

## 8.2 Fill parziali e anchor

Il prezzo medio reale può essere diverso dall’anchor pianificato se:

```text
- viene fillata solo Entry_1;
- Entry_2 resta pending;
- il bot cancella l’averaging dopo TP;
- l’ordine market subisce slippage;
- l’exchange arrotonda quantità o prezzi.
```

Perciò bisogna scegliere una policy esplicita:

```yaml
tp_repricing:
  mode: none
```

oppure:

```yaml
tp_repricing:
  mode: after_first_fill
```

oppure:

```yaml
tp_repricing:
  mode: after_each_entry_fill
```

### Raccomandazione iniziale

```yaml
tp_repricing:
  mode: none
```

Mantenere i TP calcolati sul piano iniziale è più semplice, stabile e meno rischioso. Il repricing post-fill richiede cancel/replace atomico o compensato e può introdurre finestre senza protezione.

---

# 9. TP Reshaping: problema separato

La riduzione dei TP non deve essere mescolata alla trasformazione delle entry.

Le decisioni indipendenti sono:

```text
A. Quali TP mantenere?
B. I TP devono restare livelli originali o diventare RR esatti?
C. Come distribuire la chiusura?
D. Quando deve scattare BE?
E. Quando devono essere cancellate le averaging entry?
```

---

# 10. Strategie di riduzione TP

## 10.1 `keep_parsed`

Mantiene tutti i TP del trader.

```yaml
take_profits:
  mode: keep_parsed
```

### Vantaggi

```text
- massima fedeltà al trader;
- non introduce livelli artificiali;
- nessuna perdita di informazione.
```

### Problemi

```text
- 8–10 TP aumentano complessità ordine/lifecycle;
- distribuzioni troppo frammentate;
- TP troppo vicini possono essere poco utili;
- BE e auto-cancel basati su “TP1” possono avere poco significato economico.
```

---

## 10.2 `select_existing_by_rr`

Mantiene alcuni TP originali, selezionando quelli più vicini a RR desiderati.

```yaml
take_profits:
  mode: select_existing_by_rr

  rr_anchor:
    mode: planned_weighted_average

  selection:
    target_count: 4
    desired_rr: [1.0, 1.5, 2.5, 3.5]
    strategy: nearest_unique
    min_rr: 0.8
    max_rr: 8.0
```

### Esempio

```text
anchor = 97,2
SL = 94
R = 3,2
```

TP originali:

```text
98 / 100 / 102 / 104 / 106 / 108 / 110 / 112
```

RR effettivi:

| TP | RR |
|---:|---:|
| 98 | 0,25R |
| 100 | 0,88R |
| 102 | 1,50R |
| 104 | 2,13R |
| 106 | 2,75R |
| 108 | 3,38R |
| 110 | 4,00R |
| 112 | 4,63R |

Con target:

```text
[1.0R, 1.5R, 2.5R, 3.5R]
```

Il selettore può scegliere:

```text
100 → 0,88R
102 → 1,50R
106 → 2,75R
108 → 3,38R
```

### Regola `nearest_unique`

Ogni TP sorgente può essere scelto una sola volta.

Questo evita:

```text
target 1.0R → TP 100
target 1.1R → ancora TP 100
```

### Vantaggi

```text
- conserva livelli tecnici del trader;
- riduce il numero di ordini;
- costruisce una struttura economica più uniforme;
- facilmente auditabile.
```

### Problemi

```text
- il RR risultante è approssimato;
- alcuni target desiderati possono non avere un TP vicino;
- la qualità dipende dalla distribuzione originale dei TP.
```

### Scelta consigliata come default

È la modalità più equilibrata per segnali discrezionali Telegram.

---

## 10.3 `generate_from_rr`

Ignora i TP sorgente e genera livelli matematici.

```yaml
take_profits:
  mode: generate_from_rr

  rr_anchor:
    mode: planned_weighted_average

  generation:
    rr_levels: [1.0, 1.5, 2.5, 3.5]
```

Formula LONG:

```text
TP(RR=x) = anchor + x × R
```

Formula SHORT:

```text
TP(RR=x) = anchor - x × R
```

### Vantaggi

```text
- RR esatti;
- struttura coerente;
- semplice per backtest e comparazione trader;
- nessuna dipendenza dalla qualità dei TP sorgente.
```

### Problemi

```text
- perde livelli tecnici del trader;
- può produrre TP non coerenti con resistenze/supporti;
- può non rispettare tick size e market microstructure;
- può alterare molto la strategia originale.
```

### Uso corretto

Da usare soltanto per:

```text
- strategie completamente standardizzate;
- trader selezionati;
- profili esplicitamente “bot-owned”;
- test quantitativi.
```

Non come default universale.

---

## 10.4 `compress_by_rr_buckets`

Raggruppa gli 8 TP in fasce RR e mantiene un rappresentante per fascia.

```yaml
take_profits:
  mode: compress_by_rr_buckets

  buckets:
    - min_rr: 0.8
      max_rr: 1.5
    - min_rr: 1.5
      max_rr: 2.5
    - min_rr: 2.5
      max_rr: 4.0
    - min_rr: 4.0
      max_rr: null

  select:
    strategy: farthest_in_bucket
```

Può essere utile se i TP del trader sono molto densi vicino all’entry.

### Problema

È più difficile da spiegare e auditare di `select_existing_by_rr`. Non lo userei nella prima versione.

---

# 11. Close distribution

Ridurre i TP significa anche decidere quanta posizione chiudere a ogni target.

La distribuzione non deve essere un side effect del numero di TP.

## 11.1 Modello esplicito

```yaml
close_distribution:
  mode: custom
  weights: [30, 25, 25, 20]
```

Invarianti:

```text
- numero pesi = numero TP effettivi;
- ogni peso >= 0;
- somma = 100;
- l’ultimo TP può ricevere il residuo per arrotondamento.
```

## 11.2 Alternative

| Modello | Esempio per 4 TP | Commento |
|---|---|---|
| `equal` | 25/25/25/25 | semplice, neutro |
| `front_loaded` | 40/30/20/10 | realizza presto |
| `back_loaded` | 10/20/30/40 | massimizza payoff se trend continua |
| `custom` | 30/25/25/20 | migliore per strategie definite |
| `rr_weighted` | proporzionale al RR | concettualmente possibile, ma non intuitivo |

## 11.3 Raccomandazione iniziale

Per segnali con TP ridotti a 4:

```yaml
close_distribution:
  mode: custom
  weights: [30, 25, 25, 20]
```

oppure, se vuoi più realizzazione iniziale:

```yaml
close_distribution:
  mode: custom
  weights: [40, 30, 20, 10]
```

La scelta dipende dalla statistica reale dei trader, non dalla sola estetica della curva RR.

---

# 12. BE e auto-cancel: meglio soglie RR, non indici TP

Nel sistema attuale concetti come:

```yaml
be_trigger: tp1
cancel_averaging_pending_after: tp1
```

dipendono dal numero e dall’ordine dei TP.

Dopo un reshape, `TP1` può cambiare radicalmente di significato:

```text
prima del reshape:
TP1 = 0,25R

dopo il reshape:
TP1 = 1,0R
```

Oppure il primo TP effettivo potrebbe restare sotto 1R.

## 12.1 Modello più robusto

```yaml
management_plan:
  be_trigger:
    mode: rr_threshold
    min_rr: 1.0
    require_target_fill: true

  cancel_averaging_pending_after:
    mode: rr_threshold
    min_rr: 1.0
    require_target_fill: true
```

Semantica:

```text
BE non scatta perché “è TP1”.
BE scatta quando un TP realmente fillato realizza almeno 1R.
```

## 12.2 Compatibilità graduale

Può coesistere un modello legacy:

```yaml
be_trigger:
  mode: target_index
  target: tp1
```

e uno nuovo:

```yaml
be_trigger:
  mode: rr_threshold
  min_rr: 1.0
```

La policy effettiva deve ammettere una sola delle due forme.

---

# 13. Audit obbligatorio

Ogni reshape deve produrre un record leggibile.

```json
{
  "reshape_rule_id": "ladder_4_to_2_entries_stop",
  "input": {
    "entry_count": 4,
    "tp_count": 8,
    "original_stop": 92
  },
  "normalized_entries": [
    {"source_sequence": 1, "price": 100},
    {"source_sequence": 2, "price": 98},
    {"source_sequence": 3, "price": 96},
    {"source_sequence": 4, "price": 94}
  ],
  "entry_projection": {
    "discarded": [
      {"source_sequence": 1, "price": 100, "reason": "initial_entry_skipped"}
    ],
    "retained": [
      {"source_sequence": 2, "output_sequence": 1, "price": 98, "weight": 0.60},
      {"source_sequence": 3, "output_sequence": 2, "price": 96, "weight": 0.40}
    ],
    "reclassified": [
      {
        "source_sequence": 4,
        "price": 94,
        "from_role": "ENTRY",
        "to_role": "STOP_LOSS"
      }
    ]
  },
  "risk_reference": {
    "anchor": 97.2,
    "stop": 94,
    "r_unit": 3.2
  },
  "tp_reshape": {
    "mode": "select_existing_by_rr",
    "desired_rr": [1.0, 1.5, 2.5, 3.5],
    "selected": [
      {"source_tp": 2, "price": 100, "actual_rr": 0.875},
      {"source_tp": 3, "price": 102, "actual_rr": 1.5},
      {"source_tp": 5, "price": 106, "actual_rr": 2.75},
      {"source_tp": 6, "price": 108, "actual_rr": 3.375}
    ],
    "discarded_tp_sequences": [1, 4, 7, 8]
  }
}
```

L’audit deve poter rispondere senza calcoli manuali a:

```text
- quale regola è stata applicata?
- quale entry è stata scartata?
- quale entry è diventata SL?
- qual era lo SL originale?
- qual è l’anchor RR?
- quali TP sono stati conservati?
- quali RR effettivi hanno?
- perché una regola non è stata applicata?
```

---

# 14. Fallimenti: BLOCK o REVIEW?

Regola generale consigliata:

| Causa | Esito |
|---|---|
| setup non corrisponde al match | nessun reshape, usa policy normale |
| match corrisponde ma trasformazione incoerente | `REVIEW` |
| SL derivato è dal lato sbagliato | `REVIEW` |
| TP non validi dopo reshape | `REVIEW` |
| nessun TP selezionabile entro tolleranza RR | `REVIEW` |
| quantità sotto minimo exchange | `REVIEW` oppure `BLOCK` secondo policy |
| pesi non validi | `BLOCK` come errore configurazione |

Non fare fallback automatico al setup originale quando una regola di reshape era intenzionalmente applicabile ma fallisce. Il fallback silenzioso produce un trade economicamente diverso da quello che l’operatore si aspetta.

---

# 15. Tolleranza RR per selezione TP

Per `select_existing_by_rr`, bisogna definire quanto un TP sorgente può essere lontano dall’RR desiderato.

```yaml
selection:
  desired_rr: [1.0, 1.5, 2.5, 3.5]
  max_rr_deviation_abs: 0.35
```

Esempio:

```text
Target desiderato: 1.0R
TP candidato: 0.60R
Differenza: 0.40R
```

Con soglia `0.35`:

```text
→ non selezionabile
```

## 15.1 Perché serve

Senza soglia, il motore potrebbe definire “TP 1R” un target da `0.12R`, solo perché è il più vicino disponibile. Questo renderebbe BE e auto-cancel economicamente incoerenti.

## 15.2 Fallback sensato

```yaml
on_missing_target:
  mode: skip_target
  min_effective_tp_count: 2
```

Oppure, più prudente:

```yaml
on_missing_target:
  mode: REVIEW
```

Per una prima versione, `REVIEW` è preferibile.

---

# 16. Proposta di configurazione completa

```yaml
setup_reshaping:
  enabled: true

  rules:
    - id: ladder_4_to_2_entries_stop
      priority: 100
      enabled: true

      match:
        entry_type: LIMIT
        entry_structure: LADDER
        normalized_entry_count: 4
        tp_count:
          min: 4
          max: 10

      source_indexing: side_normalized

      projection:
        entries:
          - source_sequence: 2
            output_role: ENTRY
            output_sequence: 1
            weight: 0.60

          - source_sequence: 3
            output_role: ENTRY
            output_sequence: 2
            weight: 0.40

          - source_sequence: 4
            output_role: STOP_LOSS
            replace_original_stop: true

        discarded_sources:
          - source_sequence: 1
            reason: initial_entry_skipped

      take_profits:
        mode: select_existing_by_rr

        rr_anchor:
          mode: planned_weighted_average

        selection:
          desired_rr: [1.0, 1.5, 2.5, 3.5]
          strategy: nearest_unique
          max_rr_deviation_abs: 0.35
          min_effective_tp_count: 4
          preserve_last_source_tp: false
          on_missing_target: REVIEW

        close_distribution:
          mode: custom
          weights: [30, 25, 25, 20]

      constraints:
        require_stop_on_loss_side: true
        require_stop_beyond_all_retained_entries: true
        require_positive_risk_distance: true
        min_stop_distance_pct: 0.10
        require_monotonic_tp_order: true
        reject_if_any_entry_equals_stop: true
        reject_if_tp_not_profitable: true

      on_failure: REVIEW

management_plan:
  be_trigger:
    mode: rr_threshold
    min_rr: 1.0
    require_target_fill: true

  cancel_averaging_pending_after:
    mode: rr_threshold
    min_rr: 1.0
    require_target_fill: true
```

---

# 17. Possibili modelli alternativi

## Modello A — configurazione per struttura

```text
LADDER 4 entry
→ applica reshape 4→2+SL
```

### Pro

```text
- semplice;
- facile da capire;
- buona prevedibilità.
```

### Contro

```text
- rigido;
- non considera il trader;
- può essere troppo generalista.
```

## Modello B — configurazione per trader

```text
trader_devos_crypto:
  usa reshape 4→2+SL
```

### Pro

```text
- adattato al comportamento del trader;
- evita che una regola buona per un trader danneggi un altro.
```

### Contro

```text
- più configurazioni;
- più difficile confronto trasversale;
- maggiore carico di manutenzione.
```

## Modello C — configurazione per profilo di strategia

```text
strategy_profile: conservative_ladder
```

### Pro

```text
- separa il concetto trader dal concetto strategia;
- riutilizzabile;
- più ordinato nel lungo periodo.
```

### Contro

```text
- richiede una tassonomia delle strategie;
- aggiunge un livello concettuale.
```

## Raccomandazione

Partire da:

```text
regola globale molto selettiva
+
override trader espliciti
```

Non introdurre subito profili strategia generici finché non esistono dati sufficienti per classificare stabilmente i trader.

---

# 18. Decisioni che devono essere prese prima di implementare

## Decisione 1 — E4 come SL è sempre una scelta valida?

Domanda reale:

```text
Quando un trader pubblica quattro entry, l’ultima è veramente un livello di invalidazione plausibile?
```

Non è una regola tecnica. È una scelta di strategia.

Va verificata con dati storici per trader:

```text
- frequenza con cui E4 viene fillata;
- frequenza con cui il prezzo raggiunge E4 e poi recupera;
- frequenza con cui un SL su E4 avrebbe chiuso trade poi vincenti;
- RR netto rispetto allo SL originale.
```

## Decisione 2 — Quanti TP devono restare?

Possibili scelte:

```text
2 TP  → semplice, meno gestione
3 TP  → compromesso
4 TP  → buon controllo
8 TP  → massima fedeltà, maggiore complessità
```

Per il modello descritto, 4 TP è un buon punto iniziale.

## Decisione 3 — TP originali o RR generati?

Default consigliato:

```text
select_existing_by_rr
```

Eccezione:

```text
generate_from_rr
```

solo per profili interamente controllati dal bot.

## Decisione 4 — BE a TP1 o a 1R?

Scelta consigliata:

```text
BE dopo fill di un TP >= 1R
```

Non dopo un semplice indice TP.

## Decisione 5 — Repricing TP dopo fill?

Scelta iniziale consigliata:

```text
nessun repricing automatico
```

Rinviare il repricing a una fase successiva, perché richiede gestione robusta di cancel/replace e riconciliazione exchange.

---

# 19. Piano minimo di implementazione

## Fase 1 — Pure transformation

Implementare un modulo puro:

```text
src/runtime_v2/signal_enrichment/setup_reshaper.py
```

Input:

```text
normalized_setup + effective policy
```

Output:

```text
reshaped_setup + audit trace
```

Nessuna chiamata exchange, nessun DB, nessun side effect.

## Fase 2 — Validazione

Implementare:

```text
setup_reshape_validator.py
```

Controlla invarianti di entry, SL, TP e RR.

## Fase 3 — TP selection RR

Implementare:

```text
tp_rr_selector.py
```

Responsabilità:

```text
- calcolo anchor;
- calcolo R;
- calcolo RR per TP sorgente;
- nearest unique selection;
- tolleranza;
- audit.
```

## Fase 4 — Integrare nel processor

Posizione:

```text
dopo normalizzazione entry
prima di RiskCapacityEngine.validate()
```

## Fase 5 — Evolvere management plan

Aggiungere semantica:

```text
target_index
rr_threshold
```

per BE e auto-cancel.

## Fase 6 — Telemetria

Creare clean log e audit che mostrino:

```text
SETUP_RESHAPED
TP_RESHAPED
SETUP_RESHAPE_REVIEW
```

---

# 20. Test obbligatori

## 20.1 Entry reshape

```text
- LONG 4 entry ordinate
- LONG 4 entry disordinate
- SHORT 4 entry ordinate
- SHORT 4 entry disordinate
- E4 non valida come SL
- 3 entry: regola non matcha
- 5 entry: regola non matcha
- entry duplicate
- SL derivato coincidente con entry
```

## 20.2 TP RR

```text
- 8 TP e 4 target RR
- due target RR vicini allo stesso TP
- nessun TP nella tolleranza
- TP non monotoni
- TP lato errato
- 1 solo TP disponibile
- precisione/tick size exchange
```

## 20.3 Risk sizing

```text
- size corretta dopo reshape
- pesi 60/40
- long e short
- SL originario sostituito
- rischio totale invariantemente pari al rischio configurato
```

## 20.4 Lifecycle

```text
- BE al primo TP effettivo >= 1R
- auto-cancel averaging dopo TP >= 1R
- TP parziali correttamente associati alla nuova sequenza
- close distribution sommata al 100%
```

---

# 21. Conclusione

Il ridimensionamento deve essere trattato come una trasformazione di setup, non come una modifica marginale delle entry o dei TP.

Il modello più solido è:

```text
setup originale immutabile
    ↓
normalizzazione side-aware
    ↓
entry/SL reshaping
    ↓
anchor e R
    ↓
TP selection per RR
    ↓
risk sizing
    ↓
execution
```

Per il caso discusso:

```text
4 entry → E1 scartata, E2/E3 operative, E4 diventa SL
8 TP → 4 TP originali selezionati per vicinanza a RR target
BE / auto-cancel → soglia RR, non indice TP
```

La prima versione dovrebbe privilegiare:

```text
- regole molto selettive;
- `REVIEW` su qualsiasi ambiguità;
- TP esistenti selezionati per RR;
- nessun repricing post-fill;
- audit completo.
```

Questa impostazione mantiene la separazione tra segnale del trader e strategia effettiva del bot, rende il rischio calcolabile correttamente e permette di valutare nel tempo se il reshape migliora o peggiora i risultati.

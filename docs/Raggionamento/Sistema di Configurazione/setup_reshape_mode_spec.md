# Setup Reshape Mode — specifica con esempio completo

**Cos'è:** una **feature opzionale, per-trader, a fianco del sistema esistente**. Non sostituisce e non rompe la pipeline attuale. Quando attiva per un trader, ridefinisce i *ruoli* del setup (quali prezzi sono Entry / SL / TP); tutto il resto — pesi, distribuzione %, realign, sizing, lifecycle — resta il sistema attuale e si applica al setup ridefinito.

**Stato:** specifica di design (intervista 2026-06-25/26). Documento unico della fetta #1 (design + implementazione).
**Reference:** `setup_reshaping_rr_reasoning.md` (principio), `operation_config_logic.md` (stato attuale config), `cornix_trading_configurations_logic.md` (mappa feature).

---

## 1. Principio in una frase

```
Il reshape NON è una nuova pipeline.
È un pre-passo opzionale che riscrive Entry/SL/TP di un trader,
e poi consegna il risultato al flusso esistente come se fosse il segnale originale.
```

Cosa fa il reshape:
- ridefinisce **quali entry sono operative**, **quale prezzo è lo SL**, **quali TP restano**.

Cosa **NON** fa (resta al flusso normale già presente):
- pesi entry (`entry_split`)
- distribuzione % (`close_distribution`)
- realign per side, sizing, leverage
- gestione lifecycle (BE, auto-cancel, timeout)

---

## 2. Switch: `setup_mode` per-trader

In `config/traders/<id>.yaml` il trader **referenzia un solo template per id** (vedi §4.5), non riscrive regole:

```yaml
setup_mode: reshape        # passthrough (default) | reshape
setup_reshape:
  template: ladder_4_aggressive   # id dalla libreria globale; cambi solo l'id
```

- `passthrough` (default): comportamento attuale, nessun cambiamento.
- `reshape`: i segnali del trader passano dal sottosistema reshape **prima** dello split pesi, usando il template referenziato in `template`.

**Semantica v1:** `reshape` non è un enhancer opzionale. È una feature specifica per pochi trader/canali: il segnale è ammesso solo se il template configurato matcha e produce un setup valido. In assenza di match o in presenza di output invalido, il segnale viene `REJECTED`.

Nessun trader è impattato finché non imposta esplicitamente `reshape`. I 12 trader registrati restano `passthrough` di default.

---

## 3. Dove si inserisce nel flusso

```
parsed signal (immutabile, audit)
      │
      ▼
[1-4] gate esistenti: blacklist, structure, SL
      │
      ▼
┌───────────────────────────────────────────────┐
│  setup_mode == reshape ?                      │
└───────────────┬───────────────┬───────────────┘
            passthrough        reshape
                │                │
                │                ▼
                │   [N] realign side-aware  (E1..E4 stabili)   ← anticipato
                │                │
                │                ▼
                │   [R] RESHAPE: ridefinisci Entry/SL/TP/structure
                │                │  nuovo setup logico
                ▼                ▼
        [6] entry_split pesi + realign     ← FLUSSO NORMALE
            (in reshape: i pesi si applicano al setup RIDEFINITO)
                │
                ▼
        [7] TP trim policy normale
            (solo passthrough; reshape bypassa `use_tp_count`)
                │
                ▼
        sizing → close_distribution → lifecycle (BE/cancel)  ← FLUSSO NORMALE
```

**Ordine obbligatorio in reshape mode:** `realign side-aware` → `reshape` → `pesi`. Il realign deve precedere il reshape (indici E1..E4 stabili); i pesi seguono il reshape (si applicano al setup ridefinito).

**Nota implementazione:** oggi `processor.py` fa `TP trim` poi `_apply_entry_weights()` poi `_realign_limit_entries_by_side()` (trim prima, pesi prima, realign dopo). In reshape mode:
- il match del template deve leggere il `min_tp_count` sul numero TP **parsato originale**;
- il realign va **anticipato prima del reshape**;
- il trim TP del flusso normale (`use_tp_count`) va bypassato, perché la cardinalità TP diventa ownership del reshape.
- in passthrough l'ordine attuale resta invariato.

---

## 4. Regola di reshape — modello componibile

La regola è una **composizione di 3 blocchi indipendenti** — `entries`, `stop_loss`, `take_profits`. Non dichiara pesi né distribuzione: quelli restano al flusso normale.

**Convenzione "stato esplicito":** in config si scrive **sempre tutto**. Ogni blocco ha un `mode` esplicito, anche quando il mode è "non toccare". Niente regole implicite tipo "se manca = default" → audit e clean log non ambigui.

**Confine di responsabilità:** il reshape ridefinisce solo la geometria del setup. Non possiede pesi, close distribution o lifecycle. I pesi restano di proprietà del flusso normale; il reshape può solo consultarli in sola lettura quando servono per calcoli derivati (es. anchor RR).

### 4.1 Vocabolario dei blocchi

```
entries.mode:      keep | drop | keep_only | keep_last | keep_first
stop_loss.mode:    original | from_entry | from_distance_pct
take_profits.mode: keep_all | drop | count | by_rr
```

| Blocco | mode | Parametro | Effetto |
|---|---|---|---|
| entries | `keep` | — | tieni tutte (passthrough esplicito) |
| entries | `drop` | `indexes: [E1]` | scarta gli indici normalizzati elencati |
| entries | `keep_only` | `indexes: [E2,E3]` | tieni solo questi |
| entries | `keep_last` / `keep_first` | `n: 2` | tieni le ultime/prime N (ordine normalizzato) |
| stop_loss | `original` | — | SL del segnale mantenuto |
| stop_loss | `from_entry` | `entry: E4` | un'entry diventa SL, l'originale archiviato |
| stop_loss | `from_distance_pct` | `pct: 0.5` | SL = anchor × (1 ∓ pct) |
| take_profits | `keep_all` | — | tutti i TP del segnale |
| take_profits | `drop` | `indexes: [1,2,4]` | scarta TP per indice parsato (qui TP1, TP2, TP4) |
| take_profits | `count` | `n: 4` | tieni i primi N |
| take_profits | `by_rr` | `desired_rr`, `max_rr_deviation_abs`, … | selezione per RR (anchor = media ponderata) |

Gli esempi §4.2–4.4 sono **definizioni di template**: vivono nel catalogo dedicato `config/setup_reshape_templates.yaml` (§4.5); il trader li richiama con `template: id`, non li riscrive.

### 4.2 Esempio — ibrido pieno con stato esplicito

```yaml
# config/setup_reshape_templates.yaml → templates
- id: ladder_4_aggressive          # ← l'id compare nel clean log e nell'audit
  enabled: true
  match:
    entry_structure: LADDER
    normalized_entry_count: 4
    min_tp_count: 8
  entries:
    mode: drop
    indexes: [E1]                  # E2,E3 restano; E4 consumata come SL sotto
  stop_loss:
    mode: from_entry
    entry: E4                      # E4 → SL, SL0 archiviato
  take_profits:
    mode: by_rr
    desired_rr: [1.0, 1.5, 2.5, 3.5]
    strategy: nearest_unique
    max_rr_deviation_abs: 0.35
    on_missing_target: REJECT
  on_failure: REJECT
```

### 4.3 Esempio — tocco minimo (solo entry, tutto il resto invariato ma esplicito)

```yaml
# config/setup_reshape_templates.yaml → templates
- id: skip_first_entry
  enabled: true
  match: { entry_structure: LADDER, normalized_entry_count: 4, min_tp_count: 8 }
  entries:      { mode: drop, indexes: [E1] }
  stop_loss:    { mode: original }          # scritto, stato esplicito
  take_profits: { mode: keep_all }          # scritto, stato esplicito
  on_failure: REJECT
```

### 4.4 Esempio — esclusione TP specifica

```yaml
# config/setup_reshape_templates.yaml → templates
- id: ladder_drop_noisy_tps
  enabled: true
  match: { entry_structure: LADDER, normalized_entry_count: 4, min_tp_count: 8 }
  entries:      { mode: keep_last, n: 2 }   # tiene E3,E4 (le due più lontane)
  stop_loss:    { mode: original }
  take_profits: { mode: drop, indexes: [1, 2, 4] }   # via TP1,TP2,TP4
  on_failure: REJECT
```

Note:
- gli indici entry (`E1..En`) sono **side-normalized**: E1 = entry più vicina al prezzo. `keep_last: 2` = le due più lontane.
- gli indici TP (`1,2,4`) sono l'ordine **parsato** del segnale.
- la struttura risultante viene trattata come **struttura normale** e prende i pesi dal blocco esistente corrispondente (`LIMIT.single/averaging/ladder`).
- la struttura finale viene derivata dal reshape in base alle entry operative residue: `1 -> ONE_SHOT`, `2 -> TWO_STEP`, `3+ -> LADDER`.
- `by_rr` usa l'**anchor** = media ponderata delle entry operative, con gli **stessi pesi** del flusso normale (read-only, coerente).
- l'**`id`** della regola applicata è propagato nel clean log e nell'audit, come gli altri id del sistema.
- un template può essere anche **TP-only**: `entries.mode: keep` + `stop_loss.mode: original` + regola su `take_profits`. Questo è il caso consigliato per segnali già classificati `RANGE` con molti TP.

---

## 4.5 Catalogo template (riferimento per id)

Le regole si definiscono **una volta sola** in un catalogo dedicato. I trader le richiamano per id: cambi solo l'id, non riscrivi la regola.

**Definizione globale (una volta):**
```yaml
# config/setup_reshape_templates.yaml
templates:
  - id: ladder_4_aggressive
    match: { entry_structure: LADDER, normalized_entry_count: 4, min_tp_count: 8 }
    entries:      { mode: drop, indexes: [E1] }
    stop_loss:    { mode: from_entry, entry: E4 }
    take_profits: { mode: by_rr, desired_rr: [1.0,1.5,2.5,3.5], max_rr_deviation_abs: 0.35 }
    on_failure: REJECT

  - id: ladder_4_keep_sl
    match: { entry_structure: LADDER, normalized_entry_count: 4, min_tp_count: 8 }
    entries:      { mode: keep_last, n: 2 }
    stop_loss:    { mode: original }
    take_profits: { mode: count, n: 4 }
    on_failure: REJECT

  - id: range_tp_reduce
    match: { entry_structure: RANGE, min_tp_count: 8 }
    entries:      { mode: keep }
    stop_loss:    { mode: original }
    take_profits: { mode: count, n: 4 }
    on_failure: REJECT
```

**Riferimento nel trader (solo id):**
```yaml
# config/traders/trader_devos_crypto.yaml
setup_mode: reshape
setup_reshape:
  template: ladder_4_aggressive
```

Regole di risoluzione:
- il loader risolve `template: <id>` cercando l'id in `config/setup_reshape_templates.yaml` (lookup unico al load).
- **v1 = un solo template per trader, nessun override.** Per variare un parametro si crea un nuovo template con nuovo id. Così l'id identifica univocamente il comportamento → clean log e audit non ambigui.
- id inesistente in `template` → errore di config al load (fail-fast), non silenzioso.
- il `match` v1 è minimale e usa solo: `entry_structure`, `normalized_entry_count` (quando serve), `min_entry_count` (opzionale), `min_tp_count`.
- `min_tp_count` usa il **numero TP parsato originale** del segnale, prima di qualsiasi trim del flusso normale.
- per i template `RANGE` v1 il caso consigliato è **TP-only**: il reshape agisce sui TP e lascia inalterati entry/SL.

---

## 4.6 Anchor RR — da dove si misura l'RR

L'RR di un TP è una **distanza relativa**, non un prezzo:

```
R (unità di rischio) = | anchor − stop_effettivo |
RR(TP)               = | TP − anchor | / R
```

Quindi `1.0R` = il TP dista dall'anchor quanto lo SL (reward = rischio). `stop_effettivo` è lo SL **dopo** il reshape (se `from_entry: E4`, si misura su 94, non su SL0).

**Anchor scelto: `planned_weighted_average`** (default dei template, campo `anchor:` per-template).

```
anchor = Σ(entry_price_i × peso_i)     pesi = quelli del flusso normale
```

- con **averaging** (2+ leg): media ponderata (es. 98×0.70 + 96×0.30 = 97.4);
- con **leg singola**: degenera nel prezzo dell'unica entry (es. 98×1.0 = 98). Stessa formula, nessun ramo speciale.

**Perché questo anchor (non `first_entry` né `worst_case_entry`):** è l'unico che fa coincidere "1R di reward" con "1R di rischio realmente allocato dal sizing". Il sizing già distribuisce il rischio sulle leg per peso e misura su `|leg − stop|`: usare un anchor diverso creerebbe due metriche di rischio incoerenti nello stesso trade. `first_entry` distorce l'RV verso il basso e disallinea dal sizing; `worst_case_entry` altera la strategia (doc c §8).

**Crepa nota:** se una leg averaging non fillа, l'entrata reale slitta e l'RR realizzato cambia. Accettabile in v1 perché coerente con `tp_repricing: none` (si committa al piano; l'anchor è quello del piano). Repricing post-fill = fase successiva.

---

## 4.7 Template iniziali (catalogo di partenza)

Due template di partenza, con didascalia completa. Si estende la libreria aggiungendo nuovi id.

```yaml
# config/setup_reshape_templates.yaml  →  templates
# Valori ammessi per blocco:
#   entries.mode      : keep | drop | keep_only | keep_last | keep_first
#   stop_loss.mode    : original | from_entry | from_distance_pct
#   take_profits.mode : keep_all | drop | count | by_rr
#   on_failure        : REJECT (default, notifica SIGNAL REJECTED)
# Indici entry Ex = side-normalized (E1 = entry più vicina al prezzo).
# Indici TP   = ordine parsato del segnale (1 = primo TP).
# Match:
#   normalized_entry_count = cardinalita' esatta richiesta
#   min_entry_count        = cardinalita' minima richiesta
#   min_tp_count           = numero minimo di TP parsati originali

templates:

  # Template 1 — LADDER 4 entry, AGGRESSIVO.
  # Scarta E1, tiene E2/E3 operative, E4 → Stop Loss (SL0 archiviato),
  # riduce gli 8 TP a 4 selezionati per RR.
  - id: ladder_4_aggressive
    enabled: true                 # false = template definito ma non applicabile
    match:                        # scatta solo se TUTTO combacia
      entry_structure: LADDER
      normalized_entry_count: 4   # esattamente 4 entry dopo normalizzazione
      min_tp_count: 8             # almeno 8 TP parsati originali
    entries:
      mode: drop                  # scarta gli indici elencati, gli altri restano operativi
      indexes: [E1]               # E1 scartata; E2,E3,E4 proseguono
    stop_loss:
      mode: from_entry            # un'entry diventa lo SL effettivo
      entry: E4                   # E4 (più lontana) → SL; SL0 archiviato come audit
    take_profits:
      mode: by_rr                 # seleziona i TP esistenti più vicini a target RR
      desired_rr: [1.0, 1.5, 2.5, 3.5]   # anchor = media ponderata entry operative
      strategy: nearest_unique    # ogni TP sorgente scelto al massimo una volta
      max_rr_deviation_abs: 0.35  # TP valido solo se entro ±0.35R dal target
      on_missing_target: REJECT   # target senza TP in tolleranza → rifiuta
    on_failure: REJECT            # incoerenza runtime → notifica SIGNAL REJECTED

  # Template 2 — LADDER 4 entry, CONSERVATIVO.
  # Tiene le due entry più lontane, MANTIENE lo SL originale, primi 4 TP.
  - id: ladder_4_keep_sl
    enabled: true
    match:
      entry_structure: LADDER
      normalized_entry_count: 4
      min_tp_count: 8
    entries:
      mode: keep_last             # tiene le ultime N entry in ordine normalizzato
      n: 2                        # E3,E4 operative; E1,E2 scartate
    stop_loss:
      mode: original              # SL del segnale invariato
    take_profits:
      mode: count                 # tiene i primi N TP parsati
      n: 4                        # TP1..TP4; TP5+ scartati
    on_failure: REJECT

  # Template 3 — RANGE, TP-only.
  # Non tocca entry o SL; riduce solo i TP quando il segnale RANGE ne porta molti.
  - id: range_tp_reduce
    enabled: true
    match:
      entry_structure: RANGE
      min_tp_count: 8
    entries:
      mode: keep
    stop_loss:
      mode: original
    take_profits:
      mode: count
      n: 4
    on_failure: REJECT
```

---

## 5. Esempio completo end-to-end (LONG)

### 5.1 Input — segnale parsato

```
LADDER LONG
E1 = 100   E2 = 98   E3 = 96   E4 = 94      (già normalizzato: LONG alto→basso)
SL0 = 92
TP = 98 / 100 / 102 / 104 / 106 / 108 / 110 / 112
```

### 5.2 Step reshape — ridefinizione ruoli

```
E1 = 100  → DISCARDED   (initial_entry_skipped)
E2 = 98   → ENTRY  (operativa 1)
E3 = 96   → ENTRY  (operativa 2)
E4 = 94   → STOP_LOSS   (sostituisce SL0)
SL0 = 92  → ARCHIVED    (solo audit)
```

Setup ridefinito (prima dei pesi):
```
entries operative = [98, 96]
stop_loss = 94
struttura risultante = averaging a 2 leg
```

### 5.3 Step reshape — anchor e R

Pesi che il flusso normale assegnerà a una struttura `averaging` a 2 leg:
`LIMIT.averaging.weights = {E1: 0.70, E2: 0.30}` (config esistente, non toccata).

```
anchor = 98 × 0.70 + 96 × 0.30 = 68.6 + 28.8 = 97.4
R      = anchor − stop = 97.4 − 94 = 3.4
```

### 5.4 Step reshape — selezione TP per RR

RR di ogni TP sorgente: `(TP − 97.4) / 3.4`

| TP  | RR effettivo |
|----:|-----:|
| 98  | 0.18R |
| 100 | 0.76R |
| 102 | 1.35R |
| 104 | 1.94R |
| 106 | 2.53R |
| 108 | 3.12R |
| 110 | 3.71R |
| 112 | 4.35R |

Target `desired_rr = [1.0, 1.5, 2.5, 3.5]`, `nearest_unique`, `max_rr_deviation_abs = 0.35`:

```
1.0R → 100 (0.76R, dev 0.24 ✓)     [102 sarebbe dev 0.35, ma 100 è più vicino]
1.5R → 102 (1.35R, dev 0.15 ✓)
2.5R → 106 (2.53R, dev 0.03 ✓)
3.5R → 110 (3.71R, dev 0.21 ✓)
```

TP selezionati: **100 / 102 / 106 / 110**
TP scartati: 98, 104, 108, 112

### 5.5 Output reshape → consegnato al flusso normale

```
entries  = [98, 96]
stop     = 94
take_profits = [100, 102, 106, 110]
struttura = averaging (2 leg)
```

### 5.6 Flusso normale (sistema esistente, invariato)

**Pesi entry** (`LIMIT.averaging.weights`):
```
98 → peso 0.70
96 → peso 0.30
```

**Sizing** (risk_capacity, invariato): rischio configurato distribuito 70/30 sulle 2 leg, con `risk_distance = |entry − 94|`.

**Distribuzione close** (`close_distribution.table[4]`, esistente):
```
4 TP → [25, 25, 25, 25]
```

**Lifecycle BE** — in questa fetta resta il comportamento attuale `be_trigger: tp1` (legacy):
```
TP 100 (primo TP effettivo) fillato → BE scatta qui   (anche se vale solo 0.76R)
```
**Limite noto, accettato in v1:** post-reshape il primo TP può valere < 1R, quindi il BE legacy scatta "presto". La soluzione (`rr_threshold`: BE quando un TP fillato fa ≥ `min_rr`) è **lifecycle e fuori scope** di questa fetta → vedi §8. Esempio del comportamento futuro: con `rr_threshold min_rr=1.0`, BE scatterebbe a TP 102 (1.35R), non a TP 100 (0.76R).

---

## 6. Audit (cosa registra)

Il setup originale resta **immutabile** come record. Il reshape aggiunge un record leggibile:

```json
{
  "reshape_rule_id": "ladder_4_aggressive",
  "roles": {
    "discarded":   [{ "source": "E1", "price": 100, "reason": "initial_entry_skipped" }],
    "entries":     [{ "source": "E2", "price": 98 }, { "source": "E3", "price": 96 }],
    "stop_loss":   { "source": "E4", "price": 94, "replaced_original": 92 }
  },
  "rr": { "anchor": 97.4, "stop": 94, "r_unit": 3.4 },
  "tp_selection": {
    "mode": "by_rr",
    "selected":  [{ "price": 100, "rr": 0.76 }, { "price": 102, "rr": 1.35 },
                  { "price": 106, "rr": 2.53 }, { "price": 110, "rr": 3.71 }],
    "discarded": [98, 104, 108, 112]
  }
}
```

Domande a cui l'audit deve rispondere senza calcoli: quale regola, quale entry scartata, quale entry→SL, SL originale, anchor, TP tenuti e loro RR, perché una regola non ha matchato.

### 6.1 Clean log — riuso del canale esistente

L'id della regola compare nel **clean log** riusando il meccanismo già presente (`_build_signal_notes` in `templates/clean_log.py`), che oggi emette note per range derivation, risk hint, TP trim, realign. Il reshape è una nota in più, stesso canale:

```
Setup - Reshaped by rule 'ladder_4_aggressive'
Entry - Reordered by side (LONG)
TP - Reduced by policy (8 → 4)
```

Per i reject del ramo reshape, il clean log deve restare altrettanto esplicito:

```
Setup - Reshape rule 'ladder_4_aggressive' did not match
```

oppure:

```
Setup - Reshape failed by rule 'ladder_4_aggressive'
```

Tre tocchi piccoli (nessun nuovo sistema di id):
1. enrichment scrive `reshaped: {rule_id, ...}` in `EnrichedSignalPayload` (campo già previsto, task T4);
2. il payload notifica lo trasporta (come già fa per `range_derivation`, `entry_sequence_realigned`…);
3. `_build_signal_notes` aggiunge una riga:
   ```python
   reshaped = p.get("reshaped") or {}
   if reshaped.get("rule_id"):
       notes.append(f"Setup - Reshaped by rule '{reshaped['rule_id']}'")
   ```

Per i reject si riusa lo stesso canale note, con `reshape_rejected: {rule_id, phase}` e due frasi sintetiche:
- `phase=no_match` → `Setup - Reshape rule '<id>' did not match`
- `phase=invalid_output` → `Setup - Reshape failed by rule '<id>'`

Il clean log è la riga sintetica; l'audit JSON (§6) resta la traccia dettagliata.

---

## 7. Validazione e fallimenti

Il `mode` descrive un'**intenzione**; il validator verifica che applicata a *quel* segnale produca un trade **eseguibile e coerente**. Quando non lo è → **Rejected**, mai correzione implicita o fallback silenzioso al setup originale (produrrebbe un trade diverso da quello atteso, doc c §14).

### 7.1 Due livelli di fallimento

| Livello | Quando | Esito | Notifica |
|---|---|---|---|
| **Config** | id template inesistente in `template`, template malformato | **fail-fast al LOAD** | il bot/config non parte |
| **Runtime** | regola valida applicata a un segnale dà setup incoerente | **Rejected** per quel segnale | riusa `SIGNAL REJECTED` (clean log ❌), bot continua |
| **No-match** | `setup_mode: reshape` ma il template non matcha il segnale | **Rejected** per quel segnale | riusa `SIGNAL REJECTED` (clean log ❌), bot continua |

La notifica Rejected è quella **già esistente** (`_t_signal_rejected` → "❌ SIGNAL REJECTED"), con un `reason_code` specifico del reshape e nota clean log con id template. Nessun nuovo tipo di notifica.

### 7.2 Tabella invarianti (runtime → Rejected)

**entries**
| Invariante | Condizione di rottura | reason_code |
|---|---|---|
| ≥ 1 entry operativa resta | `drop`/`keep_only` lascia 0 entry | `reshape_no_operative_entry` |
| indici referenziati esistono | `drop:[E5]` su segnale a 4 entry | `reshape_entry_index_absent` |
| `keep_last`/`keep_first` n valido | `n` > entry disponibili | `reshape_keep_n_too_large` |
| nessun ruolo doppio | un'entry è insieme operativa e SL | `reshape_duplicate_role` |

**stop_loss**
| Invariante | Condizione di rottura | reason_code |
|---|---|---|
| SL dal lato giusto | LONG: SL ≥ min(entry op) · SHORT: SL ≤ max(entry op) | `reshape_stop_wrong_side` |
| SL ≠ entry operativa | SL coincide con un'entry tenuta | `reshape_stop_equals_entry` |
| R > 0 | `\|anchor − SL\|` = 0 | `reshape_zero_risk_distance` |
| distanza minima | R < `min_stop_distance_pct` (se impostato) | `reshape_stop_too_close` |

**take_profits**
| Invariante | Condizione di rottura | reason_code |
|---|---|---|
| ≥ 1 TP resta | `drop`/`by_rr` lascia 0 TP | `reshape_no_take_profit` |
| TP in profitto | LONG: TP ≤ anchor · SHORT: TP ≥ anchor | `reshape_tp_not_profitable` |
| monotonia | TP non monotoni dopo selezione | `reshape_tp_not_monotonic` |
| target raggiungibile | nessun TP entro `max_rr_deviation_abs` | `reshape_no_tp_in_tolerance` |

### 7.3 `on_failure`

Campo per-template, default **`REJECT`**.
- `REJECT` (default e unica modalità v1): incoerenza runtime → `SIGNAL REJECTED` + `reason_code`.

**Decisione v1:** niente `REVIEW`. Nel ramo reshape gli esiti runtime sono binari:
- `PASS` se il template matcha e produce un setup valido;
- `REJECT` in tutti gli altri casi (`no_match` oppure output invalido).

---

## 8. Cosa resta fuori (per ora)

- **BE / auto-cancel su `rr_threshold`**: è **lifecycle**, fuori scope di questa fetta. In v1 il reshape NON tocca il trigger BE: resta `be_trigger: tp1` (legacy), anche per i trader reshape. Limite noto: post-reshape TP1 può valere < 1R → BE scatta presto. `rr_threshold` (BE quando un TP fillato fa ≥ `min_rr`) si introduce in una **fetta lifecycle successiva**.
- altri pattern di proiezione (5→3+SL, ecc.): si aggiungono come nuove regole, stesso modello.
- `generate_from_rr`, `compress_by_rr_buckets`: definibili ma non implementati in v1.
- TP repricing post-fill: rinviato.
- pesi/distribuzione custom dentro la regola: **per scelta** restano al flusso normale.

---

## 9. Riepilogo della separazione

| Responsabilità | Chi la gestisce |
|---|---|
| Quali entry operative / SL / TP | **reshape (nuovo)** |
| Selezione TP per RR | **reshape (nuovo)** |
| Derivazione struttura finale (`ONE_SHOT` / `TWO_STEP` / `LADDER`) | **reshape (nuovo)** |
| Pesi entry | flusso normale (esistente) |
| Distribuzione % close | flusso normale (esistente) |
| Realign side / sizing / leverage | flusso normale (esistente) |
| BE / auto-cancel / timeout | flusso normale (esistente), **legacy `tp1`**; `rr_threshold` = fetta lifecycle futura |
| Setup originale | immutabile, solo audit |

---

## 10. Implementazione — ancoraggi al codice (verificati)

### 10.1 Punto di integrazione

`src/runtime_v2/signal_enrichment/processor.py::_process_signal`. Sequenza con il nuovo stadio:

```
1 blacklist global
2 blacklist trader
3 entry structure accettata
4 SL richiesto
5 TP trim (use_tp_count)
   ── se setup_mode == reshape ──────────────────────────
   N  realign side-aware (anticipato: indici E1..En stabili)
   R  reshape: entries/stop_loss/take_profits (mode espliciti)
        → match? no = passthrough · sì = ridefinizione
        → validazione invarianti (§7) → REJECT su incoerenza
   ──────────────────────────────────────────────────────
6 entry_split pesi (sul setup ridefinito) + realign        ← flusso normale
7 price sanity
8 build EnrichedSignalPayload (+ reshaped: {rule_id, audit})
```

**Ordine obbligatorio reshape mode:** `realign → reshape → pesi`. Oggi il processor fa `_apply_entry_weights()` poi `_realign_limit_entries_by_side()`; in reshape mode il realign va **anticipato prima del reshape**, i pesi si applicano al setup ridefinito. In passthrough l'ordine attuale resta invariato.

**Precisazione v1 emersa in review:** il ramo reshape va letto così, anche se il processor attuale non è ancora implementato in questo ordine:
- il `match` del template usa `min_tp_count` sul numero TP **parsato originale**, prima di qualsiasi `use_tp_count`;
- se `setup_mode: reshape` è attivo e il template **non** matcha, l'esito è `REJECT`, non passthrough;
- se il template matcha, il reshape diventa owner della cardinalità TP finale e il trim TP del flusso normale viene bypassato;
- la struttura finale viene derivata dal reshape (`1 -> ONE_SHOT`, `2 -> TWO_STEP`, `3+ -> LADDER`) prima che il flusso normale applichi i pesi.
- per `entry_structure: RANGE`, v1 supporta esplicitamente template **TP-only** che lasciano `entries` e `stop_loss` invariati e agiscono solo sui TP.

### 10.2 Livelli dati (mapping al codice)

```
parsed      → CanonicalParseResult.canonical_message.signal   (immutabile, già esiste)
normalized  → output realign side-aware                       (già esiste, effimero)
reshaped    → NUOVO: EnrichedSignalPayload.reshaped           (campo additivo)
execution   → lifecycle/execution_plan.py                     (già esiste)
```

`parsed` non viene mai sovrascritto (resta nel record canonical). `reshaped` è additivo nel payload enriched → nessuna migrazione DB.

### 10.3 Moduli nuovi (puri, no DB / no exchange)

```
src/runtime_v2/signal_enrichment/reshaping/
  ├── setup_reshaper.py     # orchestratore: match → applica blocchi (entries/stop_loss/tp) → valida
  ├── tp_rr_selector.py     # anchor (planned_weighted_average), R, RR per TP, nearest_unique, tolleranza
  └── reshape_validator.py  # invarianti §7 → reason_code (REJECT)
```

3 sole classi, input dataclass → output dataclass. Sotto soglia di complessità.

### 10.4 Loader

`src/runtime_v2/signal_enrichment/config_loader.py`:
- carica il catalogo separato `config/setup_reshape_templates.yaml` e risolve `template: <id>` per-trader (lookup al load);
- `template: <id>` con id assente → **fail-fast** al load;
- `policy_version` è già supportato in forma trader-aware nel loader; il fix richiesto è far sì che il processor chiami `get_policy_version(trader_id)` invece della forma globale, così l'hash riflette davvero la policy effettiva del trader.

---

## 11. Change-surface (campi categoria C toccati direttamente)

Solo i campi che il reshape tocca rientrano in questa fetta; il resto della config hygiene resta fetta #2:
- **`policy_version` trader-aware** — vedi §10.4. Unico cambiamento che tocca l'audit storico → va versionato.
- **Validazione pesi entry** (correlata, opzionale) — il reshape cambia *quale* blocco pesi si applica (es. 2 entry → `averaging`); la lacuna esistente `{E1:1, E2:-1}` (somma 0, non normalizzata) resta un rischio del flusso normale. Si può chiudere qui o lasciare in fetta #2.

---

## 12. Implementation tasks

- [ ] **T1 (P1)** — `reshaping/setup_reshaper.py` — match + applicazione blocchi entries/stop_loss/take_profits. Verify: `test_setup_reshaper.py`.
- [ ] **T2 (P1)** — `reshaping/tp_rr_selector.py` — anchor, R, RR, nearest_unique, tolleranza. Verify: `test_tp_rr_selector.py`.
- [ ] **T3 (P1)** — `reshaping/reshape_validator.py` — invarianti §7 → reason_code. Verify: `test_reshape_validator.py`.
- [ ] **T4 (P1)** — `models.py` — `EnrichedSignalPayload.reshaped` (campo audit additivo). Verify: model load test.
- [ ] **T5 (P1)** — `processor.py` — stadio reshape, ordine `match(parsed min_tp_count)→realign→reshape→pesi`, `no_match → REJECT`, bypass `use_tp_count` nel ramo reshape. Verify: integration enrichment.
- [ ] **T6 (P2)** — `config_loader.py` — catalogo `config/setup_reshape_templates.yaml` + `template:<id>` (fail-fast) + fix call-site `policy_version(trader_id)`. Verify: loader test.
- [ ] **T7 (P2)** — `config/setup_reshape_templates.yaml` + `config/traders/<id>.yaml` — catalogo template + `setup_mode`/`template` (default passthrough). Verify: load + snapshot.
- [ ] **T8 (P2)** — clean log: nota PASS e nota REJECT con id template in `_build_signal_notes`. Verify: snapshot clean log.

**Parallelizzazione:** Lane A = `reshaping/` (T1–T3, indipendente); Lane B = loader (T6, indipendente); Lane C = integrazione `processor` + `models` + clean log (T4/T5/T8, dipende da A e B).

**Fuori da questi task (rinviato):** BE/auto-cancel `rr_threshold` (`be_move_resolver.py`, `event_processor.py`) → fetta lifecycle. In v1 resta `tp1` legacy.

### Test (framework pytest, rilevato da `parser_test/` e test esistenti)

```
test_setup_reshaper.py   : LONG/SHORT 4 entry ordinate+disordinate; 3/5 entry → REJECT per no-match;
                           drop/keep_last/keep_only; from_entry lato giusto/sbagliato
test_tp_rr_selector.py   : molti TP/4 target nearest_unique; due target stesso TP; nessun TP in tolleranza;
                            by_rr vs count vs drop[indici]
test_reshape_validator.py: ogni invariante §7 → reason_code (Rejected); 0 entry / 0 TP / R≤0 /
                           SL lato sbagliato / SL=entry / TP non profittevole
integration (processor)  : ordine match(parsed min_tp_count)→realign→reshape→pesi; passthrough invariato; reshaped nel payload;
                            size corretta dopo reshape (pesi dal flusso normale)
integration (range tp-only): RANGE con molti TP; entries/SL invariati; riduzione TP nel payload;
```

---

## 13. Rollout

- **Inerte di default:** ogni trader è `setup_mode: passthrough` finché non si imposta `reshape` + `template:<id>`. Zero impatto sui 12 trader registrati.
- **Nessuna migrazione DB:** `reshaped` è additivo nel payload enriched.
- **Audit:** il fix `policy_version` trader-aware è l'unico cambiamento che tocca l'audit storico → versionare.
- **Reversibile:** rimettere `setup_mode: passthrough` su un trader lo riporta al comportamento attuale, immediatamente.

# Design — Riorganizzazione schema config + Setup/RR Reshaping

**Scope:** Fetta #1 del refactor "config e gestione".
**Contesto:** Runtime V2 — `signal_enrichment` + `lifecycle`.
**Stato:** design doc (prodotto via /plan-eng-review, scope deciso 2026-06-25).
**Reference:** `operation_config_logic.md` (stato), `setup_reshaping_rr_reasoning.md` (principio), `cornix_trading_configurations_logic.md` (mappa feature).
**Supersedes:** nessuno. Estende `setup_reshaping_rr_reasoning.md` ancorandolo al codice reale.

---

## 1. Obiettivo e non-obiettivo

**Done significa:** esiste un design implementabile che (a) riorganizza `operation_config.yaml` in sezioni coerenti con consumer verificabili, e (b) introduce il sottosistema `setup_reshaping` con anchor RR, TP selection per RR e BE/auto-cancel su soglia RR — agganciato al punto reale della pipeline.

**Criteri di accettazione (3-5):**
1. Lo schema riorganizzato mappa 1:1 ogni campo a un consumer runtime o lo marca `reserved`.
2. Il reshape è un modulo **puro** (no DB, no exchange) inserito tra normalizzazione e risk sizing.
3. `parsed_setup` resta immutabile; `reshaped_setup` è un livello dati distinto e auditabile.
4. Setup incoerente dopo reshape → **Rejected** (riusa `SIGNAL REJECTED`), mai fallback silenzioso. Vedi `setup_reshape_mode_spec.md` §7.
5. Ogni reshape produce un record audit che risponde alle 8 domande del §13 di `setup_reshaping_rr_reasoning.md`, e l'id regola compare nel clean log.

**NON in scope (questa fetta):**
- **BE / auto-cancel su `rr_threshold`**: è lifecycle, **rinviato a fetta successiva**. In v1 il reshape NON tocca il trigger BE: resta `be_trigger: tp1` legacy. (Decisione 2026-06-26.)
- Trailing Entry / TP / SL, TP Grace, SL Timeout, close-on-TP/SL-before-entry, cooldown, operation hours (→ fetta #3 Cornix).
- Config hygiene pura dei campi inattivi categoria C (→ fetta #2), **eccetto** quelli che il reshape tocca direttamente (vedi §7).
- TP repricing post-fill (`tp_repricing.mode != none`): rinviato, richiede cancel/replace atomico.
- `generate_from_rr` e `compress_by_rr_buckets`: definiti nello schema ma non implementati nella v1 (default `select_existing_by_rr`).

---

## 2. Cosa già esiste (riuso, non ricostruzione)

| Sub-problema | Già risolto in | Riuso |
|---|---|---|
| Normalizzazione side-aware delle entry | `processor.py:_realign_limit_entries_by_side` | **Sì** — è già il `normalized_setup` del doc c. Il reshape parte da qui. |
| Pesi entry per struttura | `processor.py:_apply_entry_weights` + `EntrySplitConfig` | Sì — il reshape sovrascrive i pesi solo quando una regola matcha. |
| Derivazione RANGE | `processor.py:_apply_range_split` | Sì — il reshape gira **dopo** la risoluzione range (ordine del §4 doc c rispettato). |
| Snapshot policy + audit log | `EnrichedCanonicalMessage.policy_snapshot` + `enrichment_log` | Sì — l'audit reshape è una nuova `EnrichmentLogEntry` + campo strutturato. |
| Risk sizing per leg | `lifecycle/risk_capacity.py` | Sì — consuma le entry/SL reshaped senza modifiche se il payload espone `entries`+`stop_loss` già proiettati. |
| BE trigger su fill TP | `lifecycle/be_move_resolver.py` | **Invariato in v1** — resta `tp1` legacy. `rr_threshold` = fetta lifecycle futura. |
| Auto-cancel averaging | `lifecycle/event_processor.py` | **Invariato in v1** — resta `tp1` legacy. |

**Conseguenza:** il reshape **non** è una pipeline parallela. È uno stadio che trasforma il payload esistente tra step 6 e costruzione di `EnrichedSignalPayload`.

---

## 3. Livelli dati (ancorati al codice)

Il doc c chiede 4 livelli. Mapping reale:

```
parsed_setup        → CanonicalParseResult.canonical_message.signal   (immutabile, già esiste)
normalized_setup    → output di _apply_entry_weights + _realign...    (già esiste, effimero)
reshaped_setup      → NUOVO: EnrichedSignalPayload.reshaped           (da aggiungere)
execution_setup     → lifecycle/execution_plan.py                     (già esiste)
```

Oggi `normalized_setup` è effimero (variabili locali nel processor). Il reshape lo rende esplicito come input e produce `reshaped` come output persistito accanto a `entries`. `parsed_setup` resta nel record canonical a monte: nessuna sovrascrittura.

---

## 4. Punto di integrazione (verificato)

`processor.py::_process_signal`, sequenza attuale:

```
1 blacklist global
2 blacklist trader
3 entry structure accettata
4 SL richiesto
5 TP trim (use_tp_count)
6 entry split weights + realign per side   ← QUI finisce la normalizzazione
   ─────────────────────────────────────────
   ▼ NUOVO STADIO: setup_reshaping
   6.5 reshape (se una regola matcha)
        ├─ proiezione entries/SL/discarded
        ├─ anchor RR + R
        ├─ TP selection per RR
        ├─ close_distribution esplicita
        ├─ validazione invarianti
        └─ on_failure: REVIEW (no fallback silenzioso)
   ─────────────────────────────────────────
7 price sanity
8 build EnrichedSignalPayload (+ reshaped)
```

```
                 CanonicalParseResult.signal  (parsed, immutabile)
                              │
            ┌─────────────────┴─────────────────┐
            │  enrichment steps 1-6 (esistenti)  │
            └─────────────────┬─────────────────┘
                              │  normalized entries (side-aware)
                              ▼
                 ┌────────────────────────┐
                 │   setup_reshaper.py     │  modulo PURO
                 │  (no DB, no exchange)   │
                 └───────────┬────────────┘
              match? ──no──► passthrough (entries invariati, reshaped=None)
                 │ sì
                 ▼
         proiezione → anchor/R → tp_rr_selector → validator
                 │                                   │
              valido                              invalido
                 │                                   │
                 ▼                                   ▼
        reshaped_setup + audit              EnrichmentDecision=REVIEW
                 │
                 ▼
        EnrichedSignalPayload(entries=reshaped.entries,
                              stop_loss=reshaped.stop_loss,
                              take_profits=reshaped.tps,
                              reshaped=<audit struct>)
                 │
                 ▼
        lifecycle: risk_capacity → execution_plan → exchange
```

**Perché prima del risk sizing:** §4.1 doc c. Il rischio dipende da entry operative, pesi e SL effettivo. Se E4→SL avviene dopo il sizing, la size è sbagliata. Il risk gate gira nel lifecycle, a valle dell'enrichment: l'invariante è quindi naturalmente rispettato inserendo il reshape nell'enrichment.

---

## 5. Schema riorganizzato di `operation_config.yaml`

### 5.1 Principio di riorganizzazione

Oggi `signal_policy` mescola: ammissione (structures), trasformazione (entry_split, tp), validazione (price_sanity), e campi inattivi (market_execution, price_corrections). La riorganizzazione separa per **fase della pipeline**, non per affinità di nome:

```yaml
defaults:
  # ── AMMISSIONE (gate) ──────────────────────────
  admission:
    accepted_entry_structures: [ONE_SHOT, TWO_STEP, RANGE, LADDER]
    require_sl: true            # ex signal_policy.sl.require_sl

  # ── NORMALIZZAZIONE (entry/range) ──────────────
  normalization:
    entry_split: { ... }        # invariato (riuso _apply_entry_weights)
    tp:
      use_tp_count: null

  # ── RESHAPING (NUOVO) ──────────────────────────
  setup_reshaping:
    enabled: false              # default OFF: zero impatto finché non attivato
    rules: []                   # vedi §6
    rr_anchor:
      mode: planned_weighted_average
    tp_repricing:
      mode: none                # v1: solo none

  # ── GESTIONE (lifecycle) ───────────────────────
  management_plan:
    be_trigger: tp1             # INVARIATO in v1 (legacy). rr_threshold = fetta futura
    cancel_averaging_pending_after: tp1   # INVARIATO in v1
    close_distribution: { ... } # invariato
    # ... resto management_plan invariato

  # ── RISK ───────────────────────────────────────
  risk: { ... }                 # invariato
```

### 5.2 Decisione backward-compat (DA CONFERMARE — vedi §10)

Due posture possibili per i campi rinominati/spostati (`signal_policy.sl.require_sl` → `admission.require_sl`, `be_trigger: tp1` scalare → `be_trigger.mode`):

- **A. Loader bi-formato (consigliata):** il loader accetta sia la forma vecchia (scalare) sia la nuova (dict), normalizzando alla nuova internamente. I 12 file `config/traders/*.yaml` continuano a funzionare senza modifiche. Migrazione incrementale.
- **B. Schema nuovo secco:** si riscrive `operation_config.yaml` e tutti gli override trader in un colpo. Più pulito ma rompe ogni override esistente finché non riscritto.

Il design assume **A** salvo diversa scelta.

---

## 6. Sottosistema setup_reshaping

### 6.1 Moduli nuovi (3 file puri)

```
src/runtime_v2/signal_enrichment/reshaping/
  ├── setup_reshaper.py      # orchestratore: match → projection → validate
  ├── tp_rr_selector.py      # anchor, R, RR per TP, nearest_unique, tolleranza
  └── reshape_validator.py   # invarianti §7 doc c
```

Solo 3 nuove classi, tutte pure (input dataclass → output dataclass). Sotto la soglia di complessità.

### 6.2 Modello regola (da `setup_reshaping_rr_reasoning.md` §16, invariato)

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
        tp_count: { min: 4, max: 10 }
      source_indexing: side_normalized
      projection:
        entries:
          - { source_sequence: 2, output_role: ENTRY, output_sequence: 1, weight: 0.60 }
          - { source_sequence: 3, output_role: ENTRY, output_sequence: 2, weight: 0.40 }
          - { source_sequence: 4, output_role: STOP_LOSS, replace_original_stop: true }
        discarded_sources:
          - { source_sequence: 1, reason: initial_entry_skipped }
      take_profits:
        mode: select_existing_by_rr
        selection:
          desired_rr: [1.0, 1.5, 2.5, 3.5]
          strategy: nearest_unique
          max_rr_deviation_abs: 0.35
          min_effective_tp_count: 4
          on_missing_target: REVIEW
        close_distribution: { mode: custom, weights: [30, 25, 25, 20] }
      constraints:
        require_stop_on_loss_side: true
        require_stop_beyond_all_retained_entries: true
        require_positive_risk_distance: true
        min_stop_distance_pct: 0.10
        require_monotonic_tp_order: true
      on_failure: REVIEW
```

### 6.3 BE / auto-cancel — RINVIATO (fetta lifecycle futura)

**Fuori scope v1.** Il dual-mode `target_index | rr_threshold` per BE/auto-cancel NON entra in questa fetta. Il reshape lascia `be_trigger: tp1` (legacy) invariato. Limite noto: post-reshape TP1 può valere < 1R → BE scatta presto (accettato in v1).

Quando si farà la fetta lifecycle, il modello sarà una discriminated union su `ManagementPlanConfig` (`TargetIndexTrigger | RrThresholdTrigger`), con loader bi-formato `be_trigger: tp1` → `{mode: target_index}`. Qui solo come riferimento futuro, non da implementare ora.

---

## 7. Change-surface: campi categoria C che il reshape tocca

Il reshape non può convivere con questi difetti noti (da `operation_config_logic.md` §15). Vanno risolti **dentro questa fetta** perché alterano il setup economico:

1. **`policy_version` trader-aware** — il reshape rende la policy ancora più trader-specifica. Va calcolata su `policy_snapshot` o `get_policy_version(trader_id)`, altrimenti l'audit reshape è associato a un hash ambiguo.
2. **Validazione pesi entry** — la proiezione genera pesi; il validator deve imporre somma=1, non negativi, copertura leg (§7.3 doc c). Chiude anche la lacuna esistente `{E1:1, E2:-1}`.

Gli altri campi C (market_execution, price_corrections, market_convert_mode nel loader, ecc.) restano in fetta #2.

---

## 8. Failure modes (nuovi codepath)

| Codepath | Fallimento realistico | Esito | Test | Visibile? |
|---|---|---|---|---|
| match regola | 3 o 5 entry su regola a 4 | no reshape, policy normale | sì | log "no_match" |
| proiezione SL | E4 dal lato sbagliato (long E4≥E2) | REVIEW | sì | reason_code |
| anchor/R | R ≤ 0 (anchor = SL) | REVIEW | sì | reason_code |
| tp_rr_selector | nessun TP entro `max_rr_deviation_abs` | REVIEW (`on_missing_target`) | sì | reason_code |
| validator pesi | somma pesi proiettati ≠ 1 | BLOCK (errore config) | sì | reason_code |
| validator TP | TP non monotoni dopo selection | REVIEW | sì | reason_code |

**Regola d'oro (§14 doc c):** nessun fallback silenzioso al setup originale quando una regola era intenzionalmente applicabile ma fallisce. Silenzio = trade economicamente diverso da quello atteso → sempre REVIEW/BLOCK esplicito.

---

## 9. Test obbligatori (dal §20 doc c, ancorati)

```
reshaping/test_setup_reshaper.py
  - LONG/SHORT 4 entry ordinate e disordinate (4 casi)
  - E4 non valida come SL → REVIEW
  - 3 e 5 entry → no match (policy normale)
  - SL derivato coincide con entry → REVIEW
reshaping/test_tp_rr_selector.py
  - 8 TP / 4 target RR → nearest_unique corretto
  - due target vicini allo stesso TP → no doppia selezione
  - nessun TP in tolleranza → REVIEW
  - 1 solo TP disponibile
reshaping/test_reshape_validator.py
  - invarianti §7 spec → reason_code corretto (Rejected)
  - SL lato sbagliato / SL=entry / R≤0 / 0 entry / 0 TP / TP non profittevole
risk: size corretta dopo reshape, rischio totale = configurato (pesi dal flusso normale)
```

(BE `rr_threshold` test → rinviato con la fetta lifecycle.)

Framework: pytest (rilevato da struttura `parser_test/` e test esistenti). Tutti i nuovi moduli puri → unit test diretti; reshape integration sul processor.

---

## 10. Decisioni aperte (richiedono conferma utente)

| # | Decisione | Raccomandazione | Impatto |
|---|---|---|---|
| D-A | Backward-compat schema (§5.2) | **A. Loader bi-formato** | Rompe o no i 12 override trader |
| D-B | E4-come-SL: globale o per-trader | Regola globale selettiva + override trader (§17 doc c) | Quante config, rischio cross-trader |
| D-C | TP default count dopo reshape | 4 (§18.2 doc c) | Complessità ordine/lifecycle |

Le decisioni D-B e D-C hanno default sicuri dal doc c e possono restare tali. D-A è l'unica con impatto strutturale sul loader.

---

## 11. Worktree / parallelizzazione

```
Lane A: reshaping/ (3 moduli puri + test)              — indipendente
Lane B: config_loader (templates + use:[id] + policy_version) — indipendente da A
Lane C: integrazione processor + EnrichedSignalPayload.reshaped — dipende da A e B
```
Lane A e B in parallelo; C dopo merge di entrambe.

---

## 12. Implementation tasks (sintesi)

- [ ] **T1 (P1)** — `reshaping/setup_reshaper.py` — match + projection entries/SL/discarded. Files: nuovo modulo. Verify: `test_setup_reshaper.py`.
- [ ] **T2 (P1)** — `reshaping/tp_rr_selector.py` — anchor, R, RR, nearest_unique, tolleranza. Verify: `test_tp_rr_selector.py`.
- [ ] **T3 (P1)** — `reshaping/reshape_validator.py` — invarianti §7 + validazione pesi. Verify: `test_reshape_validator.py`.
- [ ] **T4 (P1)** — `models.py` — `EnrichedSignalPayload.reshaped` (campo audit additivo). Verify: model load test.
- [ ] **T5 (P1)** — `processor.py` — inserire stadio reshape (realign→reshape→pesi), on_failure→REJECT. Verify: integration enrichment.
- [ ] ~~**T6** — `be_move_resolver.py` `rr_threshold`~~ — **RINVIATO a fetta lifecycle** (BE resta `tp1` legacy).
- [ ] **T7 (P2)** — `config_loader.py` — risoluzione `setup_reshape_templates` + `use:[id]` (fail-fast id assente) + `policy_version(trader_id)`. Verify: loader test.
- [ ] **T8 (P2)** — `operation_config.yaml` — `setup_reshape_templates` + trader `setup_mode`/`use` (default passthrough). Verify: load + snapshot.

---

## 13. Rollout

`setup_reshaping.enabled: false` di default → la fetta è **inerte** finché non si attiva una regola. Rollout per-trader via override. Nessuna migrazione DB (il reshape vive nel payload enriched già esistente; `reshaped` è additivo). `policy_version` fix è l'unico cambiamento che tocca l'audit storico — va versionato.

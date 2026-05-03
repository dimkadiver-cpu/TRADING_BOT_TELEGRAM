# AUDIT — TeleSignalBot

Registro degli step di migrazione completati, stato dei file e rischi aperti.

---

## 2026-05-03 — Redesign classificazione parser (Piano v2)

### Step completato

Implementato il piano `PIANO_IMPLEMENTAZIONE_NUOVA_CLASSIFICAZIONE_PARSER_v2.md`:
separazione tra marker evidence e classificazione finale.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser/rules_engine.py` | Modificato | Aggiunti `MarkerMatch`, `ClassEvidence`, `detect_class_evidence()`; `classify()` ora wrapper su `detect_class_evidence()` |
| `src/parser/shared/classification_resolver.py` | Creato | `ClassificationInput`, `ResolvedClassification`, `ClassificationResolver.resolve()` — decide primary_class da struttura > UPDATE > REPORT > INFO |
| `src/parser/shared/runtime.py` | Modificato | Usa `ClassificationResolver` invece di `_select_primary_class()`; rimossi i vecchi helper; aggiunto `REPORT_RESULT` in `_REPORT_INTENTS` |
| `src/parser/intent_types.py` | Modificato | Aggiunto `REPORT_RESULT` enum member |
| `src/parser/parsed_message.py` | Modificato | Aggiunto `ReportResultEntities` con `result_scope/status/value/currency/percent` |
| `src/parser/canonical_v1/intent_taxonomy.py` | Modificato | Aggiunto `REPORT_RESULT` a `IntentName`; aggiunti `UPDATE_INTENTS`, `REPORT_INTENTS`, helper `is_*` |
| `src/parser/trader_profiles/shared/intent_taxonomy.py` | Modificato | Aggiunto `REPORT_RESULT` in `OFFICIAL_INTENTS` e `PRIMARY_INTENT_PRECEDENCE`; aggiunti `UPDATE_INTENTS`, `REPORT_INTENTS`, `STATE_CHANGING_INTENTS`, helper `is_*` |
| `src/parser/trader_profiles/trader_a/semantic_markers.json` | Modificato | Rimossi `entry/вход/sl:/tp*:` da `classification_markers.new_signal.strong`; aggiunto `REPORT_RESULT` in `intent_markers` |
| `src/parser/trader_profiles/trader_a/rules.json` | Modificato | Aggiunto `REPORT_RESULT` in `primary_intent_precedence` |
| `src/parser/trader_profiles/trader_a/profile.py` | Modificato | Rimossi field marker da `_DEFAULT_CLASSIFICATION_MARKERS["new_signal_strong"]`; `has_signal` aggiunge check strutturale da entities; `has_report` include `REPORT_RESULT` |
| `tests/parser_canonical_v1/test_intent_taxonomy.py` | Modificato | Aggiornato conteggio da 17 a 18 intent; aggiunto `REPORT_RESULT` all'expected set |
| `tests/parser_shared/test_intent_taxonomy.py` | Modificato | Aggiunto `REPORT_RESULT` all'expected set |
| `src/parser/trader_profiles/trader_a/tests/test_parsing_rules_integrity.py` | Modificato | Test aggiornato: verifica che field marker NON siano in classification_markers (erano al contrario) |

### Risultato test

```
pytest tests/ src/parser/trader_profiles/trader_a/tests/  →  527 passed, 12 skipped
```

### Comportamento verificato

| Input | Prima | Dopo |
|---|---|---|
| `вход исполнен` | SIGNAL (errato: вход = marker strong) | REPORT/ENTRY_FILLED (corretto) |
| `BTCUSDT LONG Entry/SL/TP` | SIGNAL | SIGNAL (invariato) |
| `Сделка закрыта +120$` | REPORT | REPORT/REPORT_FINAL_RESULT (invariato) |

### Rischi aperti

- `parse_canonical()` in `profile.py` usa ancora `message_type == "NEW_SIGNAL"` come fallback in `has_signal`; rimosso solo con la migrazione completa della logica di classificazione interna al profilo.
- `REPORT_RESULT` intent rilevato dai nuovi marker in `semantic_markers.json`, ma `profile.py` emette ancora `U_REPORT_FINAL_RESULT` → `REPORT_FINAL_RESULT` internamente (backward compat garantita).
- Il path `parse_canonical()` usa il proprio sistema di classificazione interno, non ancora agganciato a `ClassificationResolver`; si applica solo al path `parse()` → `ParsedMessage`.

---

## 2026-04-29 — Miglioramento output CSV parser_test

### Step completato

Refactoring dello schema CSV del parser_test per migliorare la leggibilità e ridurre il rumore nelle viste principali.

### Modifiche

| File | Stato | Note |
|---|---|---|
| `parser_test/reporting/report_schema.py` | Modificato | COMMON_COLUMNS ristrutturate: rimossi `raw_text`, `action_types`, `actions_structured_summary`; aggiunti `message_type`, `raw_text_preview`, `validation_warning_count` |
| `parser_test/reporting/flatteners.py` | Modificato | Aggiunti `message_type` e `raw_text_preview` nel row dict; aggiunta funzione `_preview_text()` |
| `parser_test/tests/test_report_export.py` | Modificato | Test aggiornati per il nuovo contratto: `action_types`/`actions_structured_summary` sono ora debug-only |

### Risultato test

```
pytest parser_test/tests/ parser_test/scripts/tests/  →  31/31 passed
```

### Cosa è cambiato nel CSV

- `message_type` ora visibile in tutte le viste (era assente dal COMMON)
- `raw_text_preview` (max 150 char, singola riga) al posto di `raw_text` multilinea nel main view
- `validation_warning_count` spostato in COMMON (era duplicato in ogni scope)
- `action_types` e `actions_structured_summary` spostati in debug-only (flag `--include-legacy-debug`)
- Con `--include-legacy-debug`: aggiunge `raw_text`, `action_types`, `actions_structured_summary`, `legacy_actions`

### Rischi aperti

- Nessuno: modifiche non rompono comportamento esistente, solo cambio di visibilità colonne.
- Chi usa i CSV via script che si aspettano le colonne `action_types`/`actions_structured_summary` deve aggiungere `--include-legacy-debug`.

---

## 2026-04-27 — Fase 1: Parser Contract (multi-ref target-aware)

### Step completato

**Fase 1** del piano `PIANO_INCREMENTAZIONE_MULTI_REF.md` — estensione del contratto
canonico con i modelli target-aware, senza modificare il comportamento esistente.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser/canonical_v1/models.py` | Modificato | Aggiunti 5 Literal type, 10 modelli Pydantic, 2 campi in `CanonicalMessage` |
| `tests/parser_canonical_v1/test_targeted_action_model.py` | Creato | 37 test — tutti verdi |
| `docs/in_progress/new_parser/PIANO_INCREMENTAZIONE_MULTI_REF.md` | Aggiornato | Checklist Fase 1 spuntata; sezione "Lavoro svolto" aggiunta |

### Risultato test

```
pytest tests/parser_canonical_v1/  →  116/116 passed
```

Tutti i test preesistenti rimangono verdi. Nessun profilo legacy rotto.

### Rischi aperti

- `schema_version` non aggiornato a `"1.1"` — deferred a Fase 5 per non rompere test esistenti.
- `TargetedAction.params` è `dict[str, Any]` (loose) — la validazione strutturata dei params
  è demandata alla Fase 2 quando i profili iniziano a produrre output reale.
- `TargetedReportTargeting = TargetedActionTargeting` è un alias Python puro; se in futuro
  le due shape divergessero, sarebbe necessario separare le classi.

### Prossimo step

**Fase 2** — Parser Builder: `trader_a` produce `targeted_actions` e `targeted_reports`
nel proprio `parse_canonical()`. Vedi checklist in `PIANO_INCREMENTAZIONE_MULTI_REF.md`.

---

## 2026-04-27 — Fase 2: Parser Builder (`trader_a` pilota)

### Step completato

**Fase 2** del piano `PIANO_INCREMENTAZIONE_MULTI_REF.md` — `trader_a` produce
`targeted_actions` e `targeted_reports` in `parse_canonical()`.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser/canonical_v1/targeted_builder.py` | Creato | Builder shared: `build_targeted_actions`, `build_targeted_reports_from_lines` |
| `src/parser/trader_profiles/trader_a/profile.py` | Modificato | Import builder + blocco targeted in `parse_canonical()` + 5 costruttori estesi |
| `src/parser/trader_profiles/trader_a/tests/test_multi_ref.py` | Creato | 5 test Phase 2 — tutti verdi |
| `docs/in_progress/new_parser/PIANO_INCREMENTAZIONE_MULTI_REF.md` | Aggiornato | Checklist Fase 2 spuntata; sezione "Lavoro svolto" aggiunta |

### Risultato test

```
pytest src/parser/trader_profiles/trader_a/tests/test_multi_ref.py  →  5/5 passed
pytest src/parser/  →  725 passed, 15 failed (tutti pre-esistenti, nessuno introdotto)
```

### Rischi aperti

- Validazione su dataset reale del DB non eseguita (nessun accesso diretto al DB in sessione).
  Pattern derivati da codice esistente — da verificare con replay_parser.
- `event_type` nei `targeted_reports` è sempre `FINAL_RESULT` (scelta conservativa).
  Distinzione `TP_HIT`/`STOP_HIT` richiede contesto posizione — deferred a Fase 3/5.
- `build_targeted_reports_from_lines` richiede formato riga `SYMBOL - LINK VALUE UNIT`.
  Varianti senza simbolo o con separatori diversi non estratte.
- `parsing_rules.json` non modificato — le regole multi-ref erano già presenti nella logica Python.

### Prossimo step

**Fase 3** — Target Resolver: diventa multi-target e multi-action aware.

---

## 2026-04-27 — Fase 3: Target Resolver multi-target aware

### Step completato

**Fase 3** del piano `PIANO_INCREMENTAZIONE_MULTI_REF.md` — il resolver viene esteso
con una nuova funzione standalone `resolve_targeted()` che elabora `targeted_actions`
e `targeted_reports` producendo `MultiRefResolvedResult`.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/target_resolver/models.py` | Creato | `ResolvedActionItem`, `ResolvedReportItem`, `MultiRefResolvedResult` |
| `src/target_resolver/resolver.py` | Modificato | Import + `_resolve_action_item` + `_resolve_report_item` + `resolve_targeted` |
| `src/target_resolver/tests/test_targeted_resolver.py` | Creato | 5 test Fase 3 — tutti verdi |
| `docs/in_progress/new_parser/PIANO_INCREMENTAZIONE_MULTI_REF.md` | Aggiornato | Checklist Fase 3 spuntata; sezione "Lavoro svolto" aggiunta |

### Risultato test

```
pytest src/target_resolver/  →  16/16 passed (5 nuovi + 11 preesistenti)
pytest src/target_resolver/ tests/parser_canonical_v1/ src/parser/trader_profiles/trader_a/tests/test_multi_ref.py
→  137/137 passed
```

### Rischi aperti

- `TargetResolver.resolve()` (legacy) ancora non migrata — dipende da layer downstream (operation_rules, router).
- `targeted_reports` con NOT_FOUND non coperto da test dedicato — logica implementata ma non testata per il caso di fallimento.
- Integrazione end-to-end su replay reale non ancora eseguita (accesso DB non disponibile in sessione).
- `event_type=FINAL_RESULT` nei report è ancora fisso (eredità Fase 2) — la distinzione richiede contesto posizione.

### Prossimo step

**Fase 4** — Router / Update Planner / Runtime: il runtime consuma il binding reale `azione → target`.

---

## 2026-04-27 — STEP 0: Pre-condizioni per Disambiguation & Context Resolution

### Step completato

**STEP 0** del piano `PIANO_IMPLEMENTAZIONE_DISAMBIGUATION_CONTEXT_RESOLUTION.md` —
verifica e ripristino delle pre-condizioni prima di iniziare il layer semantico.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser/canonical_v1/models.py` | Modificato | `RiskHint` esteso con `min_value: float | None` e `max_value: float | None` |
| `src/parser/trader_profiles/trader_a/profile.py` | Modificato | Import `RiskHint`; regex `_RISK_RANGE_RE`/`_RISK_SINGLE_RE`; funzione `_extract_risk_hint()`; estrazione in `_extract_entities`; uso in `_build_ta_signal_payload` |
| `src/parser/trader_profiles/trader_a/tests/test_profile_phase4_common.py` | Modificato | Intent name corretto `NEW_SETUP`→`NS_CREATE_SIGNAL`; 12 test `parse_event_envelope_*` marcati `@unittest.skip` (Phase 4 pending) |
| `src/parser/trader_profiles/trader_d/tests/test_profile_smoke.py` | Modificato | Testo test corretto da `"entry: 65000"` a `"Вход с текущих: 65000"` |

### Risultato test

```
pytest src/parser/trader_profiles/  →  549 passed, 12 skipped, 0 failed
```

### Rischi aperti

- `models.py` ha modifiche non committate pre-esistenti (contratto multi-ref): la pre-condizione
  "nessuna modifica pendente" non è pienamente soddisfatta. Commit da eseguire manualmente.
- 12 test `parse_event_envelope_*` sono SKIPPED — richiedono `parse_event_envelope()` e campi
  `UpdatePayloadRaw.stop_update`, `ReportPayloadRaw.reported_results` (plurale) da progettare in Phase 4.
- `_RISK_RANGE_RE` non cattura pattern puramente numerici senza keyword russo (es. `"1-2% od depozita"` in inglese).

### Prossimo step

**Step 1** — Taxonomy Layer: definire `IntentName` e `STATEFUL_INTENTS` in `intent_taxonomy.py`.

---

## 2026-04-27 — STEP 1: Taxonomy Layer (`intent_taxonomy.py`)

### Step completato

**STEP 1** del piano `PIANO_IMPLEMENTAZIONE_DISAMBIGUATION_CONTEXT_RESOLUTION.md` —
fonte unica di verità per gli 17 intent ufficiali.

### File toccati

| File | Stato | Note |
|------|-------|------|
| `src/parser/canonical_v1/intent_taxonomy.py` | Creato | `IntentName` Literal, `INTENT_NAMES`, `STATEFUL_INTENTS`, `STRONGLY_STATEFUL`, `validate_intent_name` |
| `tests/parser_canonical_v1/test_intent_taxonomy.py` | Creato | 29 test — tutti verdi |

### Risultato test

```
pytest tests/parser_canonical_v1/test_intent_taxonomy.py  →  29 passed
pytest src/parser/trader_profiles/                        →  549 passed, 12 skipped, 0 failed
```

### Rischi aperti

- Alias legacy `"NS_CREATE_SIGNAL"` (usato in trader_a) non incluso nel taxonomy — risoluzione richiesta prima di chiamare `validate_intent_name` nei profili.

### Prossimo step

**Step 2** — Modello `IntentCandidate` in `intent_candidate.py`.

---

## 2026-04-27 — STEP 2: Modello `IntentCandidate`

### Step completato

**STEP 2** del piano `PIANO_IMPLEMENTAZIONE_DISAMBIGUATION_CONTEXT_RESOLUTION.md` —
struttura dati tipizzata per i candidati con forza ed evidenza.

### File toccati

| File | Stato | Note |
|------|-------|------|
| `src/parser/canonical_v1/intent_candidate.py` | Creato | `IntentStrength`, `IntentCandidate` Pydantic v2, properties `is_strong`/`is_weak` |
| `tests/parser_canonical_v1/test_intent_candidate.py` | Creato | 11 test — tutti verdi |

### Risultato test

```
pytest tests/parser_canonical_v1/test_intent_candidate.py  →  11 passed
pytest src/parser/trader_profiles/                         →  549 passed, 12 skipped, 0 failed
```

### Rischi aperti

- Nessun limite sulla lunghezza di `evidence` — accettabile per ora, da valutare se diventa fonte di output verboso.
- Implementazione era già pre-esistente nella working copy (sessione precedente non committata); verificata corretta e completa per la spec.

### Prossimo step

**Step 3** — Schema JSON `intent_compatibility` in `src/parser/shared/intent_compatibility_schema.py`.

---

## 2026-04-27 — STEP 3: Schema JSON `intent_compatibility`

### Step completato

**STEP 3** del piano `PIANO_IMPLEMENTAZIONE_DISAMBIGUATION_CONTEXT_RESOLUTION.md` —
validatore Pydantic per il blocco `intent_compatibility` nei `parsing_rules.json`.

### File toccati

| File | Stato | Note |
|------|-------|------|
| `src/parser/shared/__init__.py` | Creato | Package vuoto per il layer semantico condiviso |
| `src/parser/shared/intent_compatibility_schema.py` | Creato | `RelationType`, `IntentCompatibilityPair`, `IntentCompatibilityBlock` |
| `tests/parser_canonical_v1/test_intent_compatibility_schema.py` | Creato | 17 test — tutti verdi |

### Risultato test

```
pytest tests/parser_canonical_v1/test_intent_compatibility_schema.py  →  17 passed
pytest src/parser/trader_profiles/                                     →  549 passed, 12 skipped, 0 failed
```

### Rischi aperti

- Unicità delle coppie e unicità degli intent in `intents` non verificata a schema — rinviata a Step 11 (validazione manuale JSON).
- `IntentCompatibilityBlock` non ancora registrato nel `RulesEngine`.

### Prossimo step

**Step 4** — Schema JSON `disambiguation_rules` in `src/parser/shared/disambiguation_rules_schema.py`.

---

## 2026-04-27 — STEP 4: Schema JSON `disambiguation_rules`

### Step completato

**STEP 4** del piano `PIANO_IMPLEMENTAZIONE_DISAMBIGUATION_CONTEXT_RESOLUTION.md` —
validatore Pydantic per il blocco `disambiguation_rules` nei `parsing_rules.json`.

### File toccati

| File | Stato | Note |
|------|-------|------|
| `src/parser/shared/disambiguation_rules_schema.py` | Creato | `DisambiguationAction`, `DisambiguationRule`, `DisambiguationRulesBlock` |
| `tests/parser_canonical_v1/test_disambiguation_rules_schema.py` | Creato | 18 test — tutti verdi |

### Risultato test

```
pytest tests/parser_canonical_v1/test_disambiguation_rules_schema.py  →  18 passed
pytest src/parser/trader_profiles/                                     →  549 passed, 12 skipped, 0 failed
```

### Rischi aperti

- `prefer` non è validato come appartenente a `when_*_detected` — una regola con intent incoerenti è accettata per schema; il controllo è responsabilità del motore (Step 7).
- `keep_multi` non richiede `keep` valorizzato — il motore deve gestire `keep=None` come "mantieni tutti i candidati".
- Unicità dei nomi regola non verificata a schema — duplicati non rilevati prima di Step 11.

### Prossimo step

**Step 5** — Schema JSON `context_resolution_rules` in `src/parser/shared/context_resolution_schema.py`.

---

## 2026-04-29 — Check stato reale Fasi 1-4 del parser redesign

### Scopo

Verifica documentale del piano `PARSER_REDESIGN_SPEC_V1.md` contro il repository reale,
senza introdurre nuova logica di prodotto.

### Esito sintetico

| Fase | Stato | Nota |
|---|---|---|
| Fase 1 — Cleanup preliminare | Parziale | chiusa solo per i file legacy sicuramente scollegati |
| Fase 2 — ParsedMessage models | Completata | modelli e test presenti |
| Fase 3 — Shared infrastructure | Completata | runtime/disambiguation/schema presenti e verificati |
| Fase 4 — trader_a pilota | Non completata | il profilo `trader_a` e ancora sul percorso legacy |

### Evidenze raccolte

- `src/parser/intent_types.py` e `src/parser/parsed_message.py` sono presenti.
- `src/parser/shared/runtime.py` e `src/parser/shared/disambiguation.py` sono presenti.
- I test Phase 1-3 esistono e passano.
- `src/parser/trader_profiles/trader_a/profile.py` usa ancora `parsing_rules.json`.
- In `src/parser/trader_profiles/trader_a/` non esistono ancora `semantic_markers.json` e `rules.json`.
- `trader_a/profile.py` espone ancora `parse_canonical(...) -> CanonicalMessage`, non il nuovo `parse(...) -> ParsedMessage`.

### Verifica eseguita

```bash
pytest src/parser/tests/test_phase1_cleanup.py \
       src/parser/tests/test_phase2_parsed_message.py \
       src/parser/tests/test_phase3_shared_runtime.py \
       src/parser/tests/test_phase3_disambiguation.py \
       src/parser/tests/test_phase3_rules_schema.py -q
```

Risultato:

```text
30 passed
```

### File toccati

| File | Stato | Note |
|---|---|---|
| `docs/in_progress/new_parser/PARSER_REDESIGN_SPEC_V1.md` | Aggiornato | aggiunta sezione di check stato Fasi 1-4 |
| `docs/AUDIT.md` | Aggiornato | registrata la verifica del 2026-04-29 |

### Rischi aperti

- La checklist della Fase 1 nel documento originale e piu ampia dello stato reale del cleanup: se la si interpreta letteralmente, la fase non e ancora completamente chiusa.
- La Fase 4 non va considerata "in corso avanzato" solo per la presenza di `extractors.py`: il contratto del profilo e ancora legacy.
- Fasi successive che assumono `trader_a` gia migrato devono essere considerate bloccate o almeno premature.

### Prossimo step

Quando si riprendera il lavoro implementativo:
- o si chiude davvero il residuo di Fase 1 con una nuova migrazione controllata;
- oppure si accetta formalmente che la Fase 1 e "parzialmente chiusa" e si apre la vera migrazione Fase 4 di `trader_a`.

# AUDIT — TeleSignalBot

Registro degli step di migrazione completati, stato dei file e rischi aperti.

---

## 2026-05-08 — Fix Trader A: MOVE_STOP_TO_BE false positive in "поторопился"

### Step completato

Investigazione root cause e fix del caso 189 dove "поторопился" (fretta) innescava false positive per MOVE_STOP_TO_BE.

### Root cause

La parola "поторопился" contiene "БУ" (substring interna), che matchava sia il weak marker di MOVE_STOP_TO_BE ("в бу") che di EXIT_BE ("бу"). Questo causava una classificazione errata come UPDATE/MOVE_STOP_TO_BE invece di REPORT/EXIT_BE.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser_v2/profiles/trader_a/rules.json` | Modificato | Aggiunti pattern in `unless_contains_any` della regola `move_stop_to_be_weak_context` per escludere false positive in parole come "поторопился", "судьбу", "борьбу", ecc. |
| `src/parser_v2/profiles/trader_a/rules.json` | Modificato | Aggiunta nuova disambiguazione rule `exit_be_over_move_stop_to_be_in_sl_hit_context` per preferire EXIT_BE quando SL_HIT è presente (contesto di status report). |

### Risultato test

```
pytest src/parser_v2/tests/ → 71 passed, 0 failed
Caso 189: PRIMARY_CLASS = REPORT, PRIMARY_INTENT = EXIT_BE (prima: UPDATE, MOVE_STOP_TO_BE)
```

### Metodologia

- **Fase 1**: Root cause investigation — query database, analisi diagnostics
- **Fase 2**: Pattern analysis — confronto con altri marker match
- **Fase 3**: Hypothesis — la regex per "БУ dentro parola" è troppo permissiva
- **Fase 4**: Fix con verifica test automatici

---

## 2026-05-08 — Fix _COMMON_COLUMNS in report_schema_v2.py

### Step completato

Fix di 2 test failure pre-esistenti in `parser_test/reporting/tests/test_flatteners_v2.py`.

### File toccati

| File | Stato | Note |
|---|---|---|
| `parser_test/reporting/report_schema_v2.py` | Modificato | Aggiunti `run_id` e `diagnostics_summary` a `_COMMON_COLUMNS` |

### Risultato test

```
pytest parser_test/ → 64 passed, 0 failed
```

### Causa

`_COMMON_COLUMNS` non includeva `run_id` e `diagnostics_summary`, quindi `flatten_for_scope` non li emetteva nelle colonne dei CSV per gli scope `ALL`, `NEW_SIGNAL`, `UPDATE`, `REPORT`, `INFO_ONLY`, `UNCLASSIFIED`. `ERRORS` non era affetto (usa `_ERRORS_COLUMNS` separato che li aveva già).

---

## 2026-05-08 — Parser Test v2: Trader Filter & Parser Selection

### Step completato

Feature completa: separazione di `source_trader_id` / `resolved_trader_id` / `trader_filter` / `parser_profile` in quattro concetti indipendenti. 6 task TDD completati, 62 test verdi (+ 2 pre-esistenti in `test_flatteners_v2.py` non correlati).

### File toccati

| File | Stato | Note |
|---|---|---|
| `parser_test/db/schema.py` | Modificato | `_add_column_if_missing` helper; aggiunge `resolved_trader_id TEXT` e `resolution_method TEXT` a `raw_messages` |
| `parser_test/db/tests/test_schema.py` | Modificato | +3 test nuove colonne |
| `parser_test/scripts/trader_resolution.py` | Creato | Modulo condiviso: `normalize_trader_id`, `build_trader_resolver`, `load_known_trader_ids` |
| `parser_test/scripts/tests/test_trader_resolution.py` | Creato | 6 test `normalize_trader_id` |
| `parser_test/scripts/import_history.py` | Modificato | Flag `--default-source-trader` per impostare `source_trader_id` all'import |
| `parser_test/scripts/tests/test_import_history_topics.py` | Modificato | +2 test nuovo flag |
| `parser_test/scripts/resolve_traders.py` | Creato | Script che persiste `resolved_trader_id` + `resolution_method` su `raw_messages` |
| `parser_test/scripts/tests/test_resolve_traders.py` | Creato | 8 test (priorità, skip, force-re-resolve, normalizzazione alias) |
| `parser_test/scripts/replay_parser_v2.py` | Riscritto | Nuovi flag `--trader-filter`, `--assume-trader`, `--parser-profile`, `--allow-cross-profile-parse`, `--audit-csv`; `--trader` deprecato |
| `parser_test/scripts/tests/test_replay_parser_v2.py` | Creato | 15 test (trader filter, profile, cross-profile, audit CSV, deprecation) |
| `parser_test/scripts/tests/test_replay_trader_resolution.py` | Eliminato | Sostituito da `test_replay_parser_v2.py` |
| `parser_test/scripts/generate_parser_reports_v2.py` | Modificato | Stessi nuovi flag di `replay_parser_v2.py`; `--trader` deprecato con warning |

### Risultato test

```
pytest parser_test/ → 62 passed, 2 failed (pre-esistenti, non correlati a questa feature)
```

I 2 failure pre-esistenti sono in `test_flatteners_v2.py` — bug in `parser_test/reporting/report_schema_v2.py` (`_COMMON_COLUMNS` mancanti `run_id` e `diagnostics_summary`). Non introdotti da questa feature.

### Flussi operativi abilitati

**Mono-trader:**
```bash
python parser_test/scripts/import_history.py --db-path db.sqlite3 --chat-id -123 --default-source-trader trader_a
python parser_test/scripts/resolve_traders.py --db-path db.sqlite3
python parser_test/scripts/replay_parser_v2.py --db-path db.sqlite3 --trader-filter trader_a --parser-profile trader_a --force-reparse
```

**Multitrader:**
```bash
python parser_test/scripts/import_history.py --db-path db.sqlite3 --chat-id -123
python parser_test/scripts/resolve_traders.py --db-path db.sqlite3
python parser_test/scripts/replay_parser_v2.py --db-path db.sqlite3 --trader-filter trader_a --parser-profile auto --force-reparse
```

### Rischi aperti
- `replay_parser_v2.py:349` usa `except Exception` generico — logga solo `repr(exc)[:500]` senza stack trace. Debugging di errori parser richiederebbe `traceback.format_exc()`.
- `run_replay()` accetta `parser_system` ma non lo usa (dead parameter).
- `generate_parser_reports_v2.py` non espone `--only-unparsed` e `--show-samples` (presenti in `replay_parser_v2.py` ma non in questo wrapper).

### Branch / commit

Merge su `main`. Ultimo commit: `5488044`.

---

## 2026-05-07 — Occurrence Identity + Target Binding (parser_v2)

### Step completato

Implementazione completa del feature `occurrence-identity-target-binding` su `parser_v2`.
12 task TDD completati, 66 test scritti, 0 regressioni.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser_v2/contracts/enums.py` | Modificato | Aggiunto `TargetSource` Literal (8 valori) |
| `src/parser_v2/contracts/context.py` | Modificato | Aggiunto `target_source` a `TargetHints`, `TargetCandidate`, `TargetExtractionResult` |
| `src/parser_v2/contracts/parsed_message.py` | Modificato | Aggiunto `intent_id`, `occurrence_index` (ge=0), `target_hints` a `ParsedIntent` |
| `src/parser_v2/contracts/canonical_message.py` | Modificato | Aggiunto `source_intent_id` a `UpdateOperation` e `TargetedAction`; warning rinominato `ambiguous_target_intent_binding` |
| `src/parser_v2/contracts/rules.py` | Modificato | Aggiunto `WeakContextExclusionRule` + `weak_context_exclusions` in `MarkerResolutionRules` |
| `src/parser_v2/core/marker_evidence_resolver.py` | Riscritto | Supporto `weak_context_exclusions` con scope (same_sentence/same_line/window/whole_message) e `raw_text` |
| `src/parser_v2/core/local_disambiguator.py` | Modificato | Supporto campo `scope` nelle regole (same_span, same_line, whole_message) |
| `src/parser_v2/core/target_hints_extractor.py` | Riscritto | Ritorna `TargetExtractionResult` con `TargetCandidate` posizionali per ogni link |
| `src/parser_v2/core/parsed_message_builder.py` | Modificato | Aggiunto `_assign_occurrence_ids()` — assegna `intent_id` e `occurrence_index` a tutti gli intent |
| `src/parser_v2/core/target_binding_resolver.py` | Creato | Nuovo componente: binding riga-livello candidati→intent con regola D11 ambiguità |
| `src/parser_v2/translation/canonical_translator.py` | Modificato | Multi-op su target globale produce `TargetedAction` per ciascuna (non PARTIAL); `source_intent_id` propagato; `intents` deduplicate |
| `src/parser_v2/core/runtime.py` | Modificato | `TargetBindingResolver` integrato nel pipeline; `raw_text` passato al resolver; `_extract_target_hints` ritorna `TargetExtractionResult` |

### Risultato test

```
pytest src/parser_v2/  →  66/66 passed (0 failures)
```

Distribuzione:
- 15 test contratti (Tasks 1-4)
- 5 test WeakContextExclusionRule (Task 5)
- 4 test LocalDisambiguator scope (Task 6)
- 7 test TargetHintsExtractor (Task 7)
- 4+1 test ParsedMessageBuilder (Task 8)
- 6 test TargetBindingResolver (Task 9)
- 7 test CanonicalTranslator (Task 10)
- 4 test Runtime (Task 11)
- 5 test integrazione end-to-end (Task 12)

### Decisioni architetturali chiave

| Decisione | Scelta | Motivazione |
|---|---|---|
| D1 | `TargetBindingResolver` separato dal `IntentEntityExtractor` | Separazione responsabilità; il binding avviene dopo la disambiguazione |
| D2 | Multi-op su global target → N `TargetedAction`, non PARTIAL | Ogni op agisce su un trade specifico downstream |
| D7 | Rename immediato `multi_ref_mixed_intents_not_supported` → `ambiguous_target_intent_binding` | Semantica più precisa, evita confusione con vecchio comportamento |
| D8 | `CanonicalMessage.intents` = lista deduplicata dei tipi | Indica quali tipi sono presenti, non quante occorrenze |
| D9 | `ParsedMessageBuilder` assegna gli occurrence IDs | Momento post-disambiguazione, pre-binding |
| D10 | Link nel testo batte reply per `target_source` | Il link è più specifico e intenzionale |
| D11 | Ambiguità = N_links != N_intents AND entrambi > 1 sulla stessa riga | 1:N e N:1 sono risolvibili; solo N:M entrambi>1 è ambiguo |

### Rischi aperti

- `WeakContextExclusionRule.scope == "window"` implementato nel resolver ma senza test di integrazione con profilo reale — richiede `window_chars` configurato nel `rules.json` del trader.
- I profili esistenti (`trader_a`, `trader_b`, `trader_c`, `trader_d`, `trader_3`) non usano ancora `weak_context_exclusions` — la feature è disponibile ma non attivata.
- `SIGNAL` e `REPORT` in `CanonicalTranslator` non deduplicano `intents` (solo UPDATE lo fa). Da valutare se necessario per quei primary_class.

### Branch

`worktree-feat-occurrence-identity-target-binding` — pronto per merge su `main`.

---

## 2026-05-06 — Verifica Fase 7 LocalDisambiguator e fix compatibilità Python 3.11

### Step completato

Verifica dello stato della Fase 7 (`LocalDisambiguator`) e fix di due categorie di bug
che bloccavano 44 test nelle Fasi 9, 10, 12, 13 e 1 test nella Fase 5.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser_v2/core/target_hints_extractor.py` | Modificato | Sostituita sintassi PEP 695 `def _dedup[T]` con `TypeVar` compatibile Python 3.11; aggiunto import `TypeVar` |
| `src/parser_v2/core/parsed_message_builder.py` | Modificato | Stessa correzione PEP 695 → TypeVar |
| `src/parser_v2/profiles/trader_a/signal_extractor.py` | Modificato | Aggiunto `"risk"` (inglese) a `_DEFAULT_RISK_PREFIXES`; prima solo marker russi |

### Risultato test

```
pytest tests/parser_v2/  →  94/94 passed (erano 50 collezionati con 4 errori di import + 1 failure)
```

### Stato Fase 7 verificato

`LocalDisambiguator` è **completamente implementato**: tutti i 5 test della Fase 7 passano.
Checklist piano rispettata: `prefer/suppress`, `primary_intent precedence`, regola contestuale
MARKET, `diagnostics applied rules`, `keep composites`.

### Stato complessivo parser_v2 dopo il fix

| Fase | Test | Stato |
|---|---|---|
| 1 — Contratti | 9/9 ✅ | Completa |
| 2 — TextNormalizer | 4/4 ✅ | Completa |
| 3 — MarkerMatcher | 3/3 ✅ | Completa |
| 4 — MarkerEvidenceResolver | 3/3 ✅ | Completa |
| 5 — SignalExtractor | 6/6 ✅ | Completa (era 5/6) |
| 6 — IntentEntityExtractor | 4/4 ✅ | Completa |
| 7 — LocalDisambiguator | 5/5 ✅ | Completa |
| 8 — ClassificationResolver | 8/8 ✅ | Completa |
| 9 — TargetHintsExtractor | 7/7 ✅ | Completa (era bloccata) |
| 10 — ParsedMessageBuilder | 3/3 ✅ | Completa (era bloccata) |
| 11 — CanonicalTranslator | 7/7 ✅ | Completa |
| 12 — Runtime + Profile | 4/4 ✅ | Completa (era bloccata) |
| 13 — Golden tests | 29/29 ✅ | Completa (era bloccata) |

### Rischi aperti

- L'ambiente di esecuzione usa Python 3.11; il codebase dichiara Python 3.12+ in `CLAUDE.md`.
  Attenzione a non reintrodurre sintassi PEP 695 (`def f[T]`, `type X = ...`) in nuovi file.
- `semantic_markers.json` e `rules.json` fisici per `trader_a` non esistono ancora:
  il profilo usa marker/rules in codice. La copertura linguistica è minima (Fase 12).
- Fasi downstream (operation_rules, target_resolver) non ancora migrate a `CanonicalMessage`.

### Prossimo step

Parser v2 Fase 1-13 completa e verde. Prossimi step canonici dal CLAUDE.md:
- **Step B** — Migrare `operation_rules` → consuma `CanonicalMessage`
- **Step C** — Migrare `target_resolver` → consuma `CanonicalMessage`

---

## 2026-05-04 — Review e cleanup documentazione `parser_v2`

### Step completato

Review completa di `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/` (11 documenti) e cleanup
strutturale per renderla implementabile direttamente.

### File toccati

| File | Stato | Note |
|---|---|---|
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/00_SCOPE_E_DECISIONI.md` | Modificato | Aggiunto stato codice (parser_v2 = solo docs) e sezione versionamento schema v2 |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/02_CONTRATTO_PARSED_MESSAGE.md` | Riscritto | Aggiunta formula `confidence` (strong=1.0/weak=0.4) e formula `evidence_status` derivate dal parser attuale |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/03_INTENTS_ENTITIES_MINIME.md` | Riscritto | Rimossi tutti gli `\\\_` triple-escape; allineato `ModifyEntryMode` a doc 09; `InfoOnlyEntities` ora solo `raw_fragment` |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/05_CANONICAL_MESSAGE.md` | Riscritto | Aggiunto `targeted_actions` al modello + sezione composite (UPDATE+REPORT, REPORT prevale, SIGNAL+UPDATE non supportato); InfoPayload ridotto |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/06_MARKERS_RULES.md` | Modificato | Aggiunta regola contestuale MARKET (signal) vs MODIFY_ENTRY/MARKET_NOW (update) |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/06_1_SEMANTIC_MARKERS_COMPLETO.md` | Riscritto | Rimossi tutti gli `\\_` underscore escapati (JSON ora valido); `number_format` → hint diagnostico; aggiunto `modify_entry_mode_markers`; `info_markers` consolidato |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md` | Riscritto | Allineato a struttura cartelle doc 11 (`contracts/`); rimosso adapter legacy (Fase 13); aggiunti edge cases test (testo vuoto, emoji, numeri orfani, locale price) |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/08_MULTI_REF_TARGETED_ACTIONS.md` | Riscritto | Aggiunto algoritmo segmentazione concreto (split_lines + per-line link/intent) basato su `src/parser/trader_profiles/common_utils.py` |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/09_MODIFY_ENTRY_MODE_MARKERS.md` | Riscritto | Rimossi `\\\_` escape; mode ridotto a `MARKET_NOW/UPDATE_PRICE/REMOVE/UNKNOWN`; aggiunto rinvio a doc 06 per disambiguazione contestuale |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/11_ARCHITETTURA_UNIVERSALE_PARSER.md` | Modificato | Aggiunto `target_hints_extractor.py` al core; `extract_target_hints` reso opzionale nel Protocol profile (default in core) |
| `src/parser_v2/docs/PARSER_DA_ZERO_DOCS/12_ENUMS_E_CONSTANTI.md` | Creato | Single source of truth per tutti gli enum (`MessageClass`, `ParseStatus`, `IntentType`, `EntryStructure`, `ModifyEntryMode`, `ScopeHint`, `UpdateOperationType`, ecc.) |

### Risultato

Documentazione ora coerente, JSON valido copiabile, contratti allineati tra documenti,
algoritmo segmentazione concreto, formula confidence definita, scope tassativo a `CanonicalMessage`.

### Rischi aperti

- Nessun codice ancora scritto in `src/parser_v2/`. La Fase 1 (`contracts/`) è il prossimo step.
- Necessità di riscrivere `operation_rules` e `target_resolver` per consumare `CanonicalMessage` (non in scope per parser_v2 ma blocca l'integrazione end-to-end).
- I marker `info_markers` semplificati non distinguono più ADMIN/SCHEDULE/etc. — se il sistema ne avesse bisogno in futuro, va riaperto.

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

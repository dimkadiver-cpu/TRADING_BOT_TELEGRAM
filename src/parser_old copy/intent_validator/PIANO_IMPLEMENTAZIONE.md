# Piano di implementazione — Intent Validator Multilivello

**Basato su:** PRD_INTENT_VALIDATION_MULTILAYER_AND_MULTI_TARGET v1.1  
**Data:** 2026-04-30  
**Stato:** in corso

---

## Come usare questa checklist

- Ogni task è atomico e testabile prima di procedere al successivo.
- Il prefisso `[BLOCCANTE]` indica che la fase successiva dipende da questo task.
- Segnare `[x]` quando il task è completato e tutti i test passano.
- Non iniziare una fase se la precedente ha task `[BLOCCANTE]` non completati.

---

## Fase 1 — Disambiguation conservativa

**Priorità:** ALTA — regressione attiva  
**Stima:** 1-2 giorni  
**Dipendenze:** nessuna

**Problema:** `apply_disambiguation_rules()` rimuove candidati dalla lista invece di marcarli `INVALID`. Informazione persa ad ogni parse.

**Decisione architetturale (approvata):** La disambiguation opera su `IntentResult[]` invece di `IntentCandidate[]`. Questo elimina la conversione post-disambiguation e semplifica il flusso.

### Task

- [ ] **1.1** Leggere `src/parser/shared/disambiguation_engine.py` e mappare tutti i punti in cui i candidati vengono rimossi (righe 77-88).
- [ ] **1.2** `[BLOCCANTE]` Refactorare `apply_disambiguation_rules()` per accettare `list[IntentResult]` invece di `list[IntentCandidate]`.
  - Per action `prefer`: intent in `over[]` → `status = INVALID`, `invalid_reason = "suppressed_by_rule:<rule_name>"`
  - Per action `suppress`: intent soppressi → `status = INVALID`, `invalid_reason = "suppressed_by_rule:<rule_name>"`
  - Per action `keep_multi`: nessuna modifica
  - Intent con `status` già `INVALID` non devono essere riprocessati
- [ ] **1.3** Aggiungere ai `diagnostics` del `ParsedMessage`:
  ```json
  {
    "disambiguation": {
      "applied_rules": ["prefer_move_stop_to_be_over_move_stop"],
      "suppressed_intents": ["MOVE_STOP"]
    }
  }
  ```
- [ ] **1.4** Aggiornare tutti i caller di `apply_disambiguation_rules()` nei profili trader (cercare con `grep -r "apply_disambiguation_rules"`).
- [ ] **1.5** Scrivere test `test_disambiguation_conservative.py`:
  - `test_prefer_marks_suppressed_intent_invalid` — MOVE_STOP_TO_BE + MOVE_STOP → MOVE_STOP `INVALID: suppressed_by_rule:prefer_move_stop_to_be_over_move_stop`
  - `test_suppress_marks_intent_invalid_with_reason` — CLOSE_FULL + CLOSE_PARTIAL → CLOSE_FULL `INVALID`
  - `test_keep_multi_keeps_both_intents` — TP_HIT + MOVE_STOP_TO_BE → entrambi `CANDIDATE` (non toccati)
  - `test_sl_hit_suppresses_close_full_without_explicit_close` — SL_HIT + CLOSE_FULL senza "закрываю" → CLOSE_FULL `INVALID`
  - `test_suppressed_intent_visible_in_diagnostics` — applied_rules e suppressed_intents presenti
- [ ] **1.6** Verificare che `DisambiguationResult` restituisca anche gli intent `INVALID` (non solo i `detected_intents` attivi).
- [ ] **1.7** Eseguire tutti i test esistenti dei profili (`pytest src/parser/trader_profiles/`). Nessuna regressione.

### Acceptance criteria

```text
✓ Intent soppressi restano nella lista come INVALID con invalid_reason.
✓ Il translator riceve solo CONFIRMED (non cambia comportamento downstream).
✓ diagnostics.disambiguation.applied_rules popolato.
✓ Tutti i test profili passano.
```

---

## Fase 2 — Entity validation

**Priorità:** ALTA  
**Stima:** 1-2 giorni  
**Dipendenze:** nessuna (parallela alla Fase 1)

**Problema:** `validator.py` non verifica se l'intent ha le entità minime. `MOVE_STOP` senza prezzo viene confermato.

### Task

- [ ] **2.1** Aggiungere a `validation_rules.json` i campi `requires_any_entity` e `requires_all_entities` per MOVE_STOP e CLOSE_PARTIAL (vedi sezione 10 del PRD per il JSON completo).
- [ ] **2.2** Estendere `_load_rules()` in `validator.py` per leggere `requires_any_entity` e `requires_all_entities` dal JSON.
- [ ] **2.3** `[BLOCCANTE]` Implementare `_validate_entity_requirements(intent: IntentResult, rule: dict) -> str | None`:
  - Accede a `intent.entities.to_dict()` (già disponibile via `IntentEntities.to_dict()`)
  - `requires_any_entity: [a, b]` → almeno uno tra `a`, `b` deve essere non-None nel dict
  - `requires_all_entities: [a, b]` → tutti devono essere non-None
  - Restituisce `None` se OK, `"missing_required_entity:a|b"` se fallisce
- [ ] **2.4** Chiamare `_validate_entity_requirements()` in `_validate_intent()` prima della history validation. Se fallisce → `INVALID` con l'invalid_reason restituito.
- [ ] **2.5** Scrivere test `test_entity_validation.py`:
  - `test_move_stop_missing_entity_invalid` — entities vuote → `INVALID: missing_required_entity:new_stop_price|stop_to_tp_level`
  - `test_move_stop_with_stop_to_tp_level_ok` — stop_to_tp_level valorizzato → entity check passa
  - `test_move_stop_with_new_stop_price_ok` — new_stop_price valorizzato → entity check passa
  - `test_close_partial_missing_fraction_invalid` — entities vuote → `INVALID`
  - `test_close_partial_with_fraction_ok` — fraction valorizzata → entity check passa
  - `test_move_stop_to_be_no_entity_ok` — MoveStopToBEEntities vuote → non invalidato per entity
  - `test_close_full_no_entity_requirement` — nessun campo richiesto → entity check passa

### Acceptance criteria

```text
✓ MOVE_STOP senza new_stop_price e stop_to_tp_level → INVALID: missing_required_entity
✓ MOVE_STOP con almeno uno → prosegue alla history validation
✓ CLOSE_PARTIAL senza fraction/close_price → INVALID
✓ MOVE_STOP_TO_BE senza entità → non invalidato
✓ Intents senza requires_*_entity in rules → non invalidati per entity
```

---

## Fase 3 — Target normalization e `requires_target`

**Priorità:** ALTA  
**Stima:** 2-3 giorni  
**Dipendenze:** Fase 1 completata (usa IntentResult)

**Problema:** REPLY e TELEGRAM_LINK ignorati silenziosamente. UPDATE senza target viene confermato.

### Task

- [ ] **3.1** Estendere `_extract_message_refs(targeting)` in `validator.py`:
  - `REPLY` → `int(ref.value)` (già message_id)
  - `TELEGRAM_LINK` → estrarre message_id dall'URL usando `_LINK_ID_RE` già definita in `resolution_unit.py` (importare o duplicare)
  - `EXPLICIT_ID` → `int(ref.value)`
  - `MESSAGE_ID` → già supportato, invariato
  - `GLOBAL_SCOPE`, `SELECTOR` → restituire lista vuota + flag scope speciale separato
- [ ] **3.2** Aggiungere `requires_target` a tutti i rules in `validation_rules.json` (vedi sezione 10 del PRD). `REPORT_FINAL_RESULT` → `requires_target: false`.
- [ ] **3.3** `[BLOCCANTE]` Implementare `_check_requires_target(intent, rule, refs, scope_kind) -> str | None`:
  - Se `rule.get("requires_target", True)` è True
  - E refs è vuota
  - E scope_kind non è `"GLOBAL_SCOPE"` o `"SELECTOR"`
  - → restituisce `"missing_target"`
- [ ] **3.4** Chiamare `_check_requires_target()` in `_validate_intent()` dopo la entity validation e prima della history validation. Se fallisce → `INVALID: missing_target`.
- [ ] **3.5** Verificare che `GLOBAL_SCOPE` venga confermato senza history check (scope globale non richiede refs singoli).
- [ ] **3.6** Scrivere test `test_target_validation.py`:
  - `test_update_without_target_invalid` — UPDATE con targeting None → `INVALID: missing_target`
  - `test_reply_target_normalized_and_validated` — targeting con REPLY → ref normalizzato, history check eseguito
  - `test_telegram_link_target_normalized_and_validated` — targeting con TELEGRAM_LINK `https://t.me/c/123/2110` → ref 2110, history check eseguito
  - `test_explicit_id_target_normalized` — EXPLICIT_ID valorizzato → normalizzato
  - `test_global_scope_bypasses_target_check` — targeting GLOBAL_SCOPE → non INVALID per missing_target
  - `test_report_final_result_no_target_required` — REPORT_FINAL_RESULT senza target → non INVALID per missing_target
  - `test_signal_intent_no_target_required` — intent categoria SIGNAL → bypass check

### Acceptance criteria

```text
✓ UPDATE senza target → INVALID: missing_target
✓ REPLY normalizzato a int → history validation eseguita
✓ TELEGRAM_LINK normalizzato → history validation eseguita
✓ GLOBAL_SCOPE → non richiede refs singoli
✓ REPORT_FINAL_RESULT → requires_target: false
```

---

## Fase 6 — `validation_rules.json` copertura completa

**Priorità:** MEDIA — eseguibile in parallelo con Fase 3  
**Stima:** 0.5 giorni  
**Dipendenze:** Fase 2 completata (per requires_any_entity)

**Problema:** regole mancanti per CLOSE_PARTIAL, CANCEL_PENDING, INVALIDATE_SETUP, REPORT_FINAL_RESULT.

### Task

- [ ] **6.1** Sostituire `validation_rules.json` con la versione completa della sezione 10 del PRD.
  - Aggiungere: `CLOSE_PARTIAL`, `CANCEL_PENDING`, `INVALIDATE_SETUP`, `REPORT_FINAL_RESULT`
  - Aggiornare: `MOVE_STOP` con `requires_any_entity` e `invalid_reason` aggiornato
  - Aggiungere `requires_target` a tutte le regole esistenti
- [ ] **6.2** Verificare che il loader `_load_rules()` non ignori i nuovi campi.
- [ ] **6.3** Test di smoke: eseguire `pytest src/parser/intent_validator/` — tutti i test passano.

### Acceptance criteria

```text
✓ validation_rules.json ha 11 regole (tutte gli intent operativi + REPORT_FINAL_RESULT)
✓ CLOSE_PARTIAL e MOVE_STOP hanno requires_any_entity
✓ Tutti i test intent_validator passano
```

---

## Fase 4 — History validation multi-ref

**Priorità:** MEDIA  
**Stima:** 1 giorno  
**Dipendenze:** Fase 3 completata (normalizzazione refs)

**Problema:** dopo la Fase 3 REPLY/TELEGRAM_LINK sono interi — la history validation già funziona per interi, ma servono test espliciti e verifica coverage.

### Task

- [ ] **4.1** Verificare che `SQLiteHistoryProvider.get_signal_lifecycle()` accetti tutti i tipi di message_id normalizzati dalla Fase 3.
- [ ] **4.2** Scrivere test `test_history_validation_multi_ref.py`:
  - `test_tp_hit_without_new_signal_invalid` — history vuota → `INVALID: no_open_signal`
  - `test_tp_hit_after_closed_signal_invalid` — history con CLOSE_FULL → `INVALID`
  - `test_move_stop_to_be_on_open_signal_confirmed` — history con NEW_SIGNAL → `CONFIRMED`
  - `test_exit_be_without_stop_moved_invalid` — history con NEW_SIGNAL ma senza MOVE_STOP → `INVALID`
  - `test_multi_target_partial_valid_confirmed` — target 2110 valido + 2111 invalido → `CONFIRMED: valid_refs=[2110], invalid_refs=[2111], invalid_reason="some_targets_invalid:no_open_signal"`
  - `test_multi_target_all_invalid` — tutti i target invalidi → `INVALID: valid_refs=[], invalid_refs=[2110, 2111]`
  - `test_multi_target_all_valid` — tutti i target validi → `CONFIRMED: invalid_refs=[]`
- [ ] **4.3** Verificare che `invalid_reason` per multi-target parziale sia `"some_targets_invalid:<original_reason>"`.

### Acceptance criteria

```text
✓ Multi-target parzialmente valido → CONFIRMED + valid_refs/invalid_refs separati
✓ Tutti invalidi → INVALID
✓ invalid_reason nel formato corretto
```

---

## Fase 5 — MIXED_TARGETED e sicurezza downstream

**Priorità:** MEDIA  
**Stima:** 2 giorni  
**Dipendenze:** Fase 4 completata

**Problema:** messaggi con più target + più intent senza mapping chiaro potrebbero applicare azioni errate.

### Task

- [ ] **5.1** Aggiungere `SINGLE_TARGET` e `MIXED_TARGETED` a `ResolutionUnit` in `resolution_unit.py`:
  ```python
  ResolutionUnit = Literal["SINGLE_TARGET", "MESSAGE_WIDE", "TARGET_ITEM_WIDE", "MIXED_TARGETED"]
  ```
- [ ] **5.2** Aggiornare `decide_resolution_unit()`:
  - `n_refs <= 1` → `SINGLE_TARGET`
  - `n_refs > 1` + firme tutte uguali → `MESSAGE_WIDE`
  - `n_refs > 1` + firme diverse + mapping per riga → `TARGET_ITEM_WIDE`
  - `n_refs > 1` + più intent operativi + nessun mapping per riga → `MIXED_TARGETED`
- [ ] **5.3** `[BLOCCANTE]` Aggiungere blocco di sicurezza nel validator o nel router: se `targeting_mode == MIXED_TARGETED`:
  - `parse_status = PARTIAL`
  - `warnings` += `["ambiguous_multi_target_mapping"]`
  - Non produrre azioni downstream
- [ ] **5.4** Aggiungere `targeting_analysis` ai `diagnostics` del `ParsedMessage` per tutti i casi:
  ```python
  parsed.diagnostics["targeting_analysis"] = {
      "mode": resolution_unit,
      "target_count": len(refs),
      "target_refs": refs,
      "mapping_confidence": confidence_value,
  }
  ```
  Dove `mapping_confidence`: 1.0 per MESSAGE_WIDE, 0.9+ per TARGET_ITEM_WIDE univoco, <0.5 per MIXED_TARGETED.
- [ ] **5.5** Estendere `_line_signature()` per ricevere i marker attivi del profilo (evitare hardcoding russo/inglese). Opzione semplice: aggiungere parametro `extra_tokens: dict[str, str]`.
- [ ] **5.6** Scrivere test `test_multi_target_classification.py`:
  - `test_single_ref_is_single_target` — 1 link → SINGLE_TARGET
  - `test_message_wide_three_links_same_action` — 3 link + "стоп в бу" → MESSAGE_WIDE
  - `test_target_item_wide_three_lines_three_actions` — 3 righe link+azione diverse → TARGET_ITEM_WIDE
  - `test_mixed_targeted_goes_partial` — 3 target + 2 azioni senza mapping → MIXED_TARGETED + PARTIAL + warning
  - `test_diagnostics_targeting_analysis_present` — targeting_analysis in diagnostics per tutti i casi
  - `test_mixed_targeted_not_passed_downstream` — MIXED_TARGETED → nessuna TargetedAction nel CanonicalMessage

### Acceptance criteria

```text
✓ MIXED_TARGETED → parse_status PARTIAL + warning ambiguous_multi_target_mapping
✓ Nessuna azione downstream prodotta per MIXED_TARGETED
✓ diagnostics.targeting_analysis presente per tutti i casi
✓ SINGLE_TARGET, MESSAGE_WIDE, TARGET_ITEM_WIDE invariati
```

---

## Fase 7 — Report debug

**Priorità:** BASSA  
**Stima:** 2-3 giorni  
**Dipendenze:** Fasi 1-5 completate

**Problema:** nessun report debug per il nuovo parser. Il report esistente legge `parse_results` (legacy).

### Task

- [ ] **7.1** Creare directory `parser_test/reporting_new_parser/`.
- [ ] **7.2** Creare `schema.py` — dataclass per le righe dei CSV:
  - `MessageRow` — una riga per messaggio
  - `IntentRow` — una riga per intent
  - `TargetValidationRow` — una riga per target per intent
  - `WarningRow` — una riga per warning
- [ ] **7.3** Creare `flatten.py` — funzioni per appiattire `ParsedMessage` nelle row:
  - `flatten_message(parsed: ParsedMessage) -> MessageRow`
  - `flatten_intents(parsed: ParsedMessage) -> list[IntentRow]`
  - `flatten_target_validation(parsed: ParsedMessage) -> list[TargetValidationRow]`
- [ ] **7.4** Creare `export.py` — scrittura CSV e JSONL:
  - Separatore `|` (standard progetto)
  - Encoding `UTF-8-sig`
  - Output: `messages.csv`, `intents.csv`, `target_validation.csv`, `warnings.csv`, `raw_debug.jsonl`
- [ ] **7.5** Creare `parser_test/scripts/generate_new_parser_debug_report.py`:
  - Legge `parse_results_v1` dal DB
  - Deserializza ogni `parsed_json` in `ParsedMessage`
  - Chiama flatten + export
  - Supporta filtro per `trader_id` e intervallo date
- [ ] **7.6** Verificare che i campi `suppressed_by_rule` (Fase 1) e `targeting_analysis` (Fase 5) siano visibili nel report.
- [ ] **7.7** Test minimali:
  - `test_flatten_message_basic`
  - `test_flatten_intents_includes_invalid`
  - `test_export_csv_correct_separator`

### Acceptance criteria

```text
✓ Per ogni intent: status, valid_refs, invalid_refs, invalid_reason, raw_fragment.
✓ Intent INVALID con suppressed_by_rule visibili nel report.
✓ Target validation rows per ogni ref.
✓ CSV con separatore | e encoding UTF-8-sig.
```

---

## Riepilogo dipendenze

```
Fase 1 (Disambiguation) ──────────────────────────────┐
Fase 2 (Entity validation) ─────────────┐             │
                                         ↓             ↓
Fase 6 (validation_rules.json) ←── Fase 2    Fase 3 (Target normalization)
                                                       │
                                                       ↓
                                             Fase 4 (History multi-ref)
                                                       │
                                                       ↓
                                             Fase 5 (MIXED_TARGETED)
                                                       │
                                                       ↓
                                             Fase 7 (Report debug)
```

Fase 1 e Fase 2 sono **parallele** e indipendenti. Fase 6 può partire non appena Fase 2 è completata.

---

## Stato avanzamento

| Fase | Descrizione | Stato | Note |
|---|---|---|---|
| 1 | Disambiguation conservativa | ⬜ non iniziata | priorità ALTA |
| 2 | Entity validation | ⬜ non iniziata | priorità ALTA |
| 3 | Target normalization + requires_target | ⬜ non iniziata | priorità ALTA |
| 6 | validation_rules.json completo | ⬜ non iniziata | parallela a Fase 3 |
| 4 | History validation multi-ref | ⬜ non iniziata | dopo Fase 3 |
| 5 | MIXED_TARGETED + sicurezza | ⬜ non iniziata | dopo Fase 4 |
| 7 | Report debug | ⬜ non iniziata | ultimo |

Aggiornare `⬜ non iniziata` → `🔄 in corso` → `✅ completata` a ogni avanzamento.

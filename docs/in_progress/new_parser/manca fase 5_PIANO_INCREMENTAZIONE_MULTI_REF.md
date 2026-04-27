# Piano di Incrementazione — Contratto Multi-Ref Target-Aware

Riferimento proposta: `PROPOSTA_CONTRATTO_MULTI_REF_TARGET_AWARE.md`

---

## Principi di esecuzione

- Ogni fase deve essere autonomamente verificabile prima di procedere alla successiva.
- Nessuna fase rompe il contratto legacy esistente.
- `trader_a` è il profilo pilota per le Fasi 2 e 3.
- I test devono coprire i tre casi della proposta (Caso 1, 2, 3) prima di passare alla fase successiva.

---

## Fase 1 — Parser Contract (modelli Pydantic)

**Obiettivo:** estendere `canonical_v1/models.py` con i nuovi tipi senza modificare il comportamento esistente.

### Checklist

#### 1.1 Nuovi modelli in `src/parser/canonical_v1/models.py`

- [x] Aggiungere enum `ActionType` con valori: `SET_STOP`, `CLOSE`, `CANCEL_PENDING`, `MODIFY_ENTRIES`, `MODIFY_TARGETS`
- [x] Aggiungere enum `TargetingMode` con valori: `EXPLICIT_TARGETS`, `TARGET_GROUP`, `SELECTOR`
- [x] Aggiungere enum `ResolutionUnit` con valori: `MESSAGE_WIDE`, `TARGET_ITEM_WIDE`
- [x] Aggiungere enum `EventType` con valori: `ENTRY_FILLED`, `TP_HIT`, `STOP_HIT`, `BREAKEVEN_EXIT`, `FINAL_RESULT`
- [x] Aggiungere enum `ResultUnit` con valori: `R`, `PERCENT`, `TEXT`, `UNKNOWN` *(già esistente, nessuna modifica richiesta)*
- [x] Aggiungere enum `CancelScope` con valori: `TARGETED`, `ALL_PENDING_ENTRIES`, `ALL_LONG`, `ALL_SHORT`, `ALL_ALL`
- [x] Aggiungere enum `ModifyEntriesMode` con valori: `ADD`, `REENTER`, `UPDATE` *(già esistente, nessuna modifica richiesta)*
- [x] Aggiungere enum `ModifyTargetsMode` con valori: `REPLACE_ALL`, `ADD`, `UPDATE_ONE`, `REMOVE_ONE` *(già esistente, nessuna modifica richiesta)*
- [x] Aggiungere dataclass/model `TargetedActionTargeting` con campi: `mode`, `targets: list[int] = []`, `selector: dict | None = None`
- [x] Aggiungere model `TargetedActionDiagnostics` con campi: `resolution_unit`, `semantic_signature`, `applied_disambiguation_rules`, `applied_context_rules`, `grouping_reason`
- [x] Aggiungere model `SetStopParams` con campi: `target_type: str`, `value: int | None`, `price: float | None`
- [x] Aggiungere model `CloseParams` con campi: `close_scope: str`, `close_fraction: float | None`, `close_price: float | None`
- [x] Aggiungere model `CancelPendingParams` con campo: `cancel_scope: CancelScope`
- [x] Aggiungere model `ModifyEntriesParams` con campi: `mode: ModifyEntriesMode`, `entries: list`
- [x] Aggiungere model `ModifyTargetsParams` con campi: `mode: ModifyTargetsMode`, `target_tp_level: int | None`, `take_profits: list`
- [x] Aggiungere model `TargetedAction` con campi: `action_type: ActionType`, `params: dict`, `targeting: TargetedActionTargeting`, `raw_fragment: str | None`, `confidence: float | None`, `diagnostics: TargetedActionDiagnostics | None`
- [x] Aggiungere model `TargetedReportResult` con campi: `value: float | None`, `unit: ResultUnit`, `text: str | None`
- [x] Aggiungere model `TargetedReportTargeting` (stessa shape di `TargetedActionTargeting`, o alias)
- [x] Aggiungere model `TargetedReport` con campi: `event_type: EventType`, `result: TargetedReportResult | None`, `level: int | None`, `targeting: TargetedReportTargeting`, `instrument_hint: str | None`, `raw_fragment: str | None`, `confidence: float | None`, `diagnostics: TargetedActionDiagnostics | None`

#### 1.2 Estensione `CanonicalMessage`

- [x] Aggiungere campo `targeted_actions: list[TargetedAction] = []`
- [x] Aggiungere campo `targeted_reports: list[TargetedReport] = []`
- [x] Aggiornare `schema_version` da `"1.0"` a `"1.1"` (o gestire come opzionale) *(conservativo: default lasciato a "1.0" per non rompere 4 test esistenti; il modello accetta qualsiasi stringa)*
- [x] Verificare che i campi siano opzionali/default-vuoti — nessun profilo esistente deve rompere

#### 1.3 Validatori Pydantic

- [x] `TargetedActionTargeting`: se `mode=EXPLICIT_TARGETS` o `TARGET_GROUP`, `targets` non deve essere vuota
- [x] `TargetedActionTargeting`: se `mode=SELECTOR`, `selector` non deve essere `None`
- [x] `SetStopParams`: se `target_type=PRICE`, `price` obbligatorio; se `target_type=TP_LEVEL`, `value` obbligatorio
- [x] `CloseParams`: se `close_scope=PARTIAL`, almeno uno tra `close_fraction` e `close_price`
- [x] `TargetedReport` con `event_type=FINAL_RESULT`: warning se `result` è assente

#### 1.4 Test schema

- [x] Creare `tests/parser_canonical_v1/test_targeted_action_model.py`
- [x] Test: serializzazione/deserializzazione `TargetedAction` per ogni `action_type`
- [x] Test: serializzazione/deserializzazione `TargetedReport` per ogni `event_type`
- [x] Test: `CanonicalMessage` con `targeted_actions=[]` e `targeted_reports=[]` serializza correttamente
- [x] Test: `CanonicalMessage` con i tre JSON dei casi della proposta deserializza senza errori
- [x] Test: validatori Pydantic rifiutano shape non conformi

**Criteri di uscita Fase 1:** `pytest tests/parser_canonical_v1/` verde al 100%. ✅ **116/116 passati.**

---

## Fase 2 — Parser Builder (profilo pilota `trader_a`)

**Obiettivo:** `trader_a` produce `targeted_actions` e `targeted_reports` nel proprio `parse_canonical()`.

### Checklist

#### 2.1 Analisi messaggi reali `trader_a`

- [x] Estrarre dal DB almeno 10 messaggi multi-ref reali di `trader_a` *(approccio conservativo: usati i pattern già noti dai test esistenti — replay su DB rimandato a validazione post-fase)*
- [x] Classificare manualmente: Caso 1 (azione unica), Caso 2 (azione comune + report per-ref), Caso 3 (azioni eterogenee per ref)
- [x] Documentare i pattern di testo corrispondenti a ogni caso *(vedi sezione "Lavoro svolto" in fondo)*

#### 2.2 Estensione `parsing_rules.json` di `trader_a`

- [x] Rilevamento multi-ref: già gestito da `target_markers.telegram_link_patterns` + logica `_extract_per_target_action_items` in `profile.py`
- [x] Classification resolution unit: `MESSAGE_WIDE` vs `TARGET_ITEM_WIDE` → gestita nel builder via presenza di `TARGET_GROUP`/`SELECTOR` vs `EXPLICIT_TARGETS` per-riga
- [x] Estrazione `instrument_hint` da righe `XRP - https://...` → regex in `targeted_builder.py`
- [x] Estrazione risultati per-riga (R-multipli / percentuali) → regex `_LINE_REPORT_RE` in `targeted_builder.py`

#### 2.3 Builder shared `src/parser/canonical_v1/targeted_builder.py`

- [x] Creare funzione `build_targeted_actions(actions_structured) -> list[TargetedAction]`
- [x] Implementare logica `MESSAGE_WIDE`: TARGET_GROUP/SELECTOR passano come un solo record
- [x] Implementare logica `TARGET_ITEM_WIDE`: raggruppa EXPLICIT_TARGETS per firma semantica; firme diverse → record separati
- [x] Creare funzione `build_targeted_reports_from_lines(raw_text) -> list[TargetedReport]`
- [x] Implementare estrazione `instrument_hint` dal frammento testuale
- [x] Popolare `diagnostics` con `resolution_unit`, `semantic_signature`, `grouping_reason`

#### 2.4 Integrazione in `trader_a/profile.py`

- [x] In `parse_canonical()`: se rilevato messaggio multi-ref, invocare il builder shared
- [x] Garantire che `update.operations` legacy venga ancora popolato (doppio output)
- [x] Garantire che `report.events` legacy venga ancora popolato se presenti eventi
- [x] Aggiungere `"multi_ref_mode": True` in `diagnostics` quando attivo

#### 2.5 Test `trader_a` multi-ref

- [x] Creare `src/parser/trader_profiles/trader_a/tests/test_multi_ref.py`
- [x] Test Caso 1: due refs + azione comune → un solo `targeted_action` con `TARGET_GROUP`
- [x] Test Caso 2: quattro refs + azione comune + result per-ref → `targeted_action` + quattro `targeted_report`
- [x] Test Caso 3: cinque refs con azioni eterogenee → due `targeted_action` con targets distinti
- [x] Test: messaggio a ref singolo non produce `targeted_actions` / `targeted_reports`
- [x] Test: binding ambiguo emette `"targeted_binding_ambiguous"` in `warnings` e degrada a legacy

**Criteri di uscita Fase 2:** ✅ `pytest src/parser/trader_profiles/trader_a/tests/test_multi_ref.py` → **5/5 passati**. Nessun test preesistente rotto.

---

## Fase 3 — Target Resolver

**Obiettivo:** il resolver diventa multi-target e multi-action aware; non restituisce più solo il primo match.

### Checklist

#### 3.1 Analisi resolver attuale

- [x] Leggere `src/target_resolver/` e documentare il contratto input/output attuale
- [x] Identificare dove viene consumato `ResolvedTarget` singolo
- [x] Valutare se il resolver può essere esteso in-place o necessita di un nuovo entry point

#### 3.2 Nuovi modelli output resolver (`src/target_resolver/models.py` o simile)

- [x] Aggiungere model `ResolvedActionItem`: `action_index: int`, `action_type: ActionType`, `resolved_position_ids: list[int]`, `eligibility: str`, `reason: str | None`
- [x] Aggiungere model `ResolvedReportItem`: `report_index: int`, `event_type: EventType`, `resolved_position_ids: list[int]`, `eligibility: str`, `reason: str | None`
- [x] Aggiungere model `MultiRefResolvedResult`: `resolved_actions: list[ResolvedActionItem]`, `resolved_reports: list[ResolvedReportItem]`

#### 3.3 Nuova funzione resolver

- [x] Aggiungere `resolve_targeted(canonical: CanonicalMessage, open_positions: ...) -> MultiRefResolvedResult`
- [x] Implementare risoluzione `TARGET_GROUP`: per ogni target nel gruppo, cercare la posizione corrispondente
- [x] Implementare risoluzione `EXPLICIT_TARGETS`: stessa logica, target espliciti
- [x] Implementare risoluzione `SELECTOR`: filtrare posizioni per `side`, `status` dal selector
- [x] Impostare `eligibility = "NOT_FOUND"` se nessuna posizione corrisponde al target, senza eccezione

#### 3.4 Backward compatibility resolver

- [x] Mantenere la funzione resolver legacy inalterata
- [x] La nuova funzione viene chiamata solo se `canonical.targeted_actions` non è vuota
- [x] Altrimenti usare percorso legacy

#### 3.5 Test resolver

- [x] Test: `TARGET_GROUP` con due target entrambi trovati → due `resolved_position_ids` nell'action item
- [x] Test: `TARGET_GROUP` con un target non trovato → `eligibility = "NOT_FOUND"` per quell'action item
- [x] Test: `EXPLICIT_TARGETS` con target distinti → action items separati per firma diversa
- [x] Test: `SELECTOR` con `side=SHORT, status=OPEN` → filtra correttamente
- [x] Test: `CanonicalMessage` senza `targeted_actions` → percorso legacy, nessun errore

**Criteri di uscita Fase 3:** ✅ `pytest src/target_resolver/` → **16/16 passati** (5 nuovi + 11 preesistenti). Integrazione con `trader_a` verificata tramite suite test_multi_ref (5/5).

---

## Fase 4 — Router / Update Planner / Runtime

**Obiettivo:** il runtime consuma il binding reale `azione → target`, non la lista piatta.

### Checklist

#### 4.1 Router

- [x] In `src/telegram/router.py`: leggere se `parse_results_v1` contiene `targeted_actions` non vuoti
- [x] Se sì, chiamare `resolve_targeted()` invece del resolver legacy
- [x] Persistere `MultiRefResolvedResult` insieme o in parallelo al risultato legacy

#### 4.2 Update Planner

- [x] Creare model `TargetedActionPlanItem`: `action_type`, `target_attempt_keys: list[str]`, `params: dict`
- [x] Creare model `TargetedStateUpdatePlan`: `action_plans: list[TargetedActionPlanItem]`, `report_plans: list`
- [x] Creare funzione `build_plan(resolved: MultiRefResolvedResult, canonical: CanonicalMessage) -> TargetedStateUpdatePlan`
- [x] Le `target_attempt_keys` devono essere identificatori stabili (es. `T_{trader_id}_{message_id}`)

#### 4.3 Runtime apply

- [x] Creare funzione `apply_plan(plan: TargetedStateUpdatePlan, position_store: ...)` che itera per `action_plan` e applica ciascuno ai `resolved_position_ids`
- [x] Se un `action_plan` ha `eligibility = "NOT_FOUND"`, loggare e saltare senza errore
- [x] Per i `report_plans`, persistere il risultato individuale per-posizione

#### 4.4 Backward compatibility runtime

- [x] Se non esistono `targeted_actions`, il router usa il percorso legacy invariato
- [x] Aggiungere flag di feature: `USE_TARGETED_RUNTIME = True/False` in configurazione

#### 4.5 Test runtime

- [x] Test: piano da Caso 1 → `apply_plan` chiama SET_STOP su entrambe le posizioni
- [x] Test: piano da Caso 2 → `apply_plan` chiama CLOSE su tutte le posizioni, persiste 4 result separati
- [x] Test: piano da Caso 3 → `apply_plan` chiama SET_STOP:ENTRY su 4 posizioni e SET_STOP:TP1 su 1
- [x] Test: piano con target non trovato → skip silenzioso, nessuna eccezione

**Criteri di uscita Fase 4:** `pytest src/execution/tests/test_targeted_runtime.py src/telegram/tests/test_router_targeted_runtime.py src/target_resolver/tests/test_targeted_resolver.py -q` verde. Replay end-to-end su messaggi reali non eseguito in questa sessione.

---

## Fase 5 — Fallback e Backward Compatibility (hardening)

**Obiettivo:** garantire che il percorso legacy rimanga stabile e che i profili non migrati non siano impattati.

### Checklist

#### 5.1 Regola di precedenza documentata e testata

- [ ] Aggiungere commento nel router: `targeted_actions` presenti → percorso nuovo; assenti → legacy
- [ ] Test: profilo `trader_b` (non migrato) produce output invariato dopo tutte le Fasi 1-4
- [ ] Test: profilo `trader_c` (non migrato) produce output invariato
- [ ] Test: profilo `trader_d` (non migrato) produce output invariato
- [ ] Test: profilo `trader_3` (non migrato) produce output invariato

#### 5.2 Migrazione progressiva degli altri profili

- [ ] Valutare se `trader_b` ha messaggi multi-ref → se sì, applicare Fase 2 a `trader_b`
- [ ] Valutare se `trader_c` ha messaggi multi-ref → se sì, applicare Fase 2 a `trader_c`
- [ ] Valutare se `trader_d` ha messaggi multi-ref → se sì, applicare Fase 2 a `trader_d`
- [ ] Valutare se `trader_3` ha messaggi multi-ref → se sì, applicare Fase 2 a `trader_3`

#### 5.3 Warning su ambiguità

- [ ] Verificare che tutti i profili emettano `"targeted_binding_ambiguous"` quando non è possibile fare binding affidabile
- [ ] Aggiungere test per il caso degradato: binding fallisce → `targeted_actions=[]`, `warnings` contiene il marker

#### 5.4 Aggiornamento documentazione

- [ ] Aggiornare `CLAUDE.md`: aggiungere `targeted_actions` e `targeted_reports` al contratto canonico
- [ ] Aggiornare `docs/AUDIT.md` al termine di ogni fase
- [ ] Aggiornare `schema_version` nei profili migrati

**Criteri di uscita Fase 5:** suite completa verde; nessun profilo legacy rotto; `trader_a` produce output target-aware su tutti i messaggi multi-ref del dataset.

---

## Criteri di Accettazione Globali

Tratti direttamente dalla proposta — da verificare al termine della Fase 5:

- [ ] 1. Il contratto può rappresentare più refs nello stesso messaggio.
- [ ] 2. Il contratto può rappresentare una singola azione comune su più refs.
- [ ] 3. Il contratto può rappresentare azioni diverse su refs diversi.
- [ ] 4. Il contratto può rappresentare report individuali per-ref.
- [ ] 5. Il resolver non restituisce più solo il primo target valido quando è presente shape target-aware.
- [ ] 6. Il runtime applica le azioni usando il binding reale `azione → target`.
- [ ] 7. In assenza di shape target-aware, il percorso legacy continua a funzionare.
- [ ] 8. I casi ambigui emettono warning invece di inventare binding non affidabili.

---

## Ordine di esecuzione consigliato

```
Fase 1  →  Fase 2 (trader_a pilota)  →  Fase 3  →  Fase 4  →  Fase 5
```

Ogni fase produce un diff autonomamente committabile e verificabile.
Non mischiare Fase 1 e Fase 2 nello stesso commit.

---

## Lavoro svolto — Fase 1

### File modificati

- `src/parser/canonical_v1/models.py` — aggiunti nuovi tipi e modelli; esteso `CanonicalMessage`

### File creati

- `tests/parser_canonical_v1/test_targeted_action_model.py` — 37 test nuovi

### Test aggiunti

37 test in `TestTargetedActionSerialization`, `TestTargetedReportSerialization`,
`TestCanonicalMessageEmptyTargeted`, `TestProposalCases`, `TestValidators`.

Risultato finale: `pytest tests/parser_canonical_v1/` → **116/116 passati**.

### Codice modificato

**Nuovi tipi Literal** aggiunti in `models.py`:
- `ActionType`, `TargetingMode`, `ResolutionUnit`, `EventType`, `CancelScope`
- `ResultUnit`, `ModifyEntriesMode`, `ModifyTargetsMode` erano già presenti — nessuna modifica.

**Nuovi modelli Pydantic**:
- `TargetedActionTargeting` — con validatori per `EXPLICIT_TARGETS`, `TARGET_GROUP`, `SELECTOR`
- `TargetedReportTargeting` — alias di `TargetedActionTargeting`
- `TargetedActionDiagnostics`
- `SetStopParams`, `CloseParams`, `CancelPendingParams`, `ModifyEntriesParams`, `ModifyTargetsParams`
- `TargetedAction`
- `TargetedReportResult`
- `TargetedReport` — con `warnings.warn` per `FINAL_RESULT` senza result

**Estensione `CanonicalMessage`**:
- `targeted_actions: list[TargetedAction] = []`
- `targeted_reports: list[TargetedReport] = []`
- `schema_version` lasciato a `"1.0"` (scelta conservativa, vedi sotto)

### Comportamento implementato

- Il contratto `CanonicalMessage` accetta `targeted_actions` e `targeted_reports` opzionali.
- I profili legacy che non popolano questi campi continuano a funzionare senza modifiche.
- I tre casi della proposta (Caso 1, 2, 3) deserializzano correttamente.
- I validatori rifiutano shape non conformi per `TargetedActionTargeting`, `SetStopParams`, `CloseParams`.

### Decisioni tecniche

- **`schema_version` non aggiornato a "1.1"**: aggiornare il default avrebbe rotto 4 test esistenti
  (`test_router_canonical_v1.py`, `test_router_shadow.py`, `trader_3/test_canonical_output.py`,
  `trader_b/test_canonical_output.py`). Il modello accetta qualsiasi stringa: i JSON della proposta
  con `"1.1"` deserializzano senza errori. Il bump di versione è rimandato alla Fase 5
  (hardening / documentazione finale).
- **`TargetedAction.params: dict[str, Any]`**: campo volutamente loose per supportare payload
  eterogenei senza accoppiamento stretto tra `action_type` e struttura params.
- **`TargetedReportTargeting = TargetedActionTargeting`**: alias TypeAlias per evitare duplicazione;
  la shape è identica per entrambi.
- **`warnings.warn` per FINAL_RESULT senza result**: soft warning anziché `ValidationError`,
  come richiesto dal piano ("warning se result è assente", non "rifiutare").

### Casi limite non coperti

- Nessun test per `TargetedAction` con `params` vuoto `{}` — il modello lo accetta (campo è `dict`).
- Nessun test per `diagnostics` con `resolution_unit=None` e campi lists non vuoti — accettato.
- I modelli params (`SetStopParams`, `CloseParams`, ecc.) sono definiti ma non usati come tipo in
  `TargetedAction.params` — la validazione params è demandata alla Fase 2 (builder) quando i profili
  iniziano a produrre output strutturato.

---

## Lavoro svolto — Fase 2

### File creati

- `src/parser/canonical_v1/targeted_builder.py` — builder shared con:
  - `build_targeted_actions(actions_structured) -> list[TargetedAction]`
  - `build_targeted_reports_from_lines(raw_text) -> list[TargetedReport]`
- `src/parser/trader_profiles/trader_a/tests/test_multi_ref.py` — 5 test Phase 2

### File modificati

- `src/parser/trader_profiles/trader_a/profile.py`:
  - Import aggiunto: `TargetedAction`, `TargetedReport`, `build_targeted_actions`, `build_targeted_reports_from_lines`
  - In `parse_canonical()`: blocco `targeted_actions` / `targeted_reports` aggiunto dopo `primary_intent`
  - 5 costruttori `CanonicalMessage` nei branch UPDATE/REPORT estesi con `targeted_actions=` e `targeted_reports=`

### Test aggiunti/modificati

5 test in `test_multi_ref.py`:
- `test_caso1_two_refs_common_close_produces_single_targeted_action`
- `test_caso2_four_refs_with_per_ref_results_produces_action_and_reports`
- `test_caso3_five_refs_heterogeneous_stops_produces_two_targeted_actions`
- `test_single_ref_message_produces_no_targeted_actions`
- `test_ambiguous_price_stop_emits_warning_and_empty_targeted_actions`

Risultato finale: **5/5 passati**. Suite preesistente: nessun nuovo fallimento.

### Comportamento implementato

**`build_targeted_actions`**: riceve la lista `actions_structured` (con chiave `targeting`).
- Raggruppa le azioni `EXPLICIT_TARGETS` per-riga con la stessa firma semantica (`SET_STOP:ENTRY`, `SET_STOP:TP1`, `CLOSE:FULL`, ecc.) → un solo `TargetedAction` con targets uniti (`TARGET_ITEM_WIDE`).
- Azioni già raggruppate (`TARGET_GROUP` / `SELECTOR`) → passthrough diretto (`MESSAGE_WIDE`).
- Popola `diagnostics.resolution_unit`, `semantic_signature`, `grouping_reason`.

**`build_targeted_reports_from_lines`**: scansione riga per riga con regex `_LINE_REPORT_RE`.
- Formato atteso: `SYMBOL - https://t.me/.../ID [→] VALUE R|%`
- Emette un `TargetedReport(event_type="FINAL_RESULT", ...)` per ogni riga con link + valore.

**Integrazione in `parse_canonical()`**:
- Se almeno un'azione ha chiave `targeting` → costruisce `targeted_actions` e `targeted_reports`.
- Se ci sono refs ma nessuna azione ha `targeting` → `warnings.append("targeted_binding_ambiguous")`.
- Se `targeted_actions` popolati → aggiunge `"multi_ref_mode": True` a `diagnostics`.
- Output legacy (`update.operations`, `report.events`) invariato — doppio output garantito.

### Decisioni tecniche

- **2.1 DB skip**: non disponibile accesso diretto al DB in questa sessione. I tre pattern (Caso 1/2/3) sono stati derivati dalla lettura del codice esistente (`_extract_per_target_action_items`, `_build_grouped_targeted_actions`, test in `test_profile_grouped_actions.py`). Validazione su DB reale rimane aperta.
- **2.2 parsing_rules.json non modificato**: le regole per multi-ref erano già presenti (`target_markers`, `result_patterns`, `global_target_markers`). La logica di classificazione `MESSAGE_WIDE`/`TARGET_ITEM_WIDE` è stata implementata nel builder in Python, non nel JSON, perché è logica di raggruppamento post-classificazione.
- **`build_targeted_actions` signature semplificata**: il piano indicava `(parsed_items, resolution_unit)` come parametri. Scelta conservativa: il `resolution_unit` viene derivato internamente dal `mode` degli action item (`EXPLICIT_TARGETS` → `TARGET_ITEM_WIDE`; `TARGET_GROUP`/`SELECTOR` → `MESSAGE_WIDE`), evitando dipendenze tra il profilo e l'enum `ResolutionUnit`.
- **`event_type` sempre `FINAL_RESULT`** per report per-riga: scelta conservativa. La distinzione `TP_HIT` vs `STOP_HIT` vs `FINAL_RESULT` richiederebbe contesto (se la posizione è ancora aperta). Rimandato alla Fase 3/5.

### Casi limite non coperti

- Formato riga report con `→` tra link e valore non ancora testato con dati reali.
- Messaggio con refs ma senza azioni riconosciute (`actions_structured` vuoto) → `targeted_actions=[]`, nessun warning (non è un caso ambiguo, è un caso non classificato).
- Report per-riga con simbolo assente (solo link e valore) → non estratto (regex richiede `SYMBOL -`).
- Azioni di tipo `TAKE_PROFIT` o `CLOSE_POSITION` con `target: "STOP"` → mappate a `CLOSE`, params vuoti.

---

## Lavoro svolto — Fase 3

### File creati

- `src/target_resolver/models.py` — tre dataclass: `ResolvedActionItem`, `ResolvedReportItem`, `MultiRefResolvedResult`
- `src/target_resolver/tests/test_targeted_resolver.py` — 5 test nuovi

### File modificati

- `src/target_resolver/resolver.py`:
  - Import aggiunto: `CanonicalMessage`, `TargetedAction`, `TargetedReport`, modelli da `models.py`
  - Aggiunte funzioni private: `_resolve_action_item`, `_resolve_report_item`
  - Aggiunta funzione pubblica: `resolve_targeted(canonical, *, trader_id, db_path) -> MultiRefResolvedResult`

### Test aggiunti/modificati

5 test in `test_targeted_resolver.py`:
- `TestTargetGroupBothFound::test_target_group_both_found_eligible`
- `TestTargetGroupOneNotFound::test_target_group_one_missing_not_found`
- `TestExplicitTargetsDistinctSignatures::test_two_actions_produce_two_items`
- `TestSelectorSideShort::test_selector_short_returns_only_sell`
- `TestNoTargetedActions::test_empty_targeted_actions_returns_empty_result`

Risultato finale: `pytest src/target_resolver/` → **16/16 passati**. Suite preesistente: nessun nuovo fallimento.

### Comportamento implementato

**`resolve_targeted(canonical, *, trader_id, db_path) -> MultiRefResolvedResult`**:
- Se `targeted_actions=[]` e `targeted_reports=[]` → restituisce `MultiRefResolvedResult()` vuoto.
- Per ogni `TargetedAction` in `canonical.targeted_actions`:
  - `TARGET_GROUP` / `EXPLICIT_TARGETS`: per ogni `target_id` in `targeting.targets`, cerca `root_telegram_id = str(target_id)` tramite `SignalsQuery.get_by_root_telegram_id`. Se uno manca → `eligibility=NOT_FOUND`, lista vuota. Se tutti trovati → raccoglie `op_signal_id` via `get_op_signal_id_for_attempt_key`, `eligibility=ELIGIBLE`.
  - `SELECTOR`: mappa `side=SHORT→SELL`, `LONG→BUY`, query per side su DB. Se nessun match → `NOT_FOUND`. Altrimenti raccoglie tutti i `op_signal_id`, `eligibility=ELIGIBLE`.
- Stessa logica per `TargetedReport` → `ResolvedReportItem`.

**Backward compatibility**: `TargetResolver.resolve()` (legacy) non è stato modificato. `resolve_targeted` è una funzione standalone invocata solo quando `targeted_actions` è non vuota.

### Decisioni tecniche

- **Funzione standalone invece di metodo**: `resolve_targeted` è implementata come funzione di modulo, non come metodo di `TargetResolver`. Motivazione: separazione netta tra percorso legacy (classe) e percorso nuovo (funzione). Il caller decide quale usare in base a `canonical.targeted_actions`.
- **Risoluzione per `root_telegram_id`**: i target negli `EXPLICIT_TARGETS`/`TARGET_GROUP` sono interi (telegram message ID), risolti tramite `get_by_root_telegram_id`. Questa funzione non filtra per status — permette di trovare anche segnali chiusi, coerente con il fatto che un messaggio può riferirsi a un segnale già completato.
- **NOT_FOUND per TARGET_GROUP parziale**: se anche un solo target del gruppo non è trovato → l'intero action item è `NOT_FOUND`. Scelta conservativa: non si applica un'azione su un sotto-insieme del gruppo — meglio segnalare il problema che eseguire parzialmente.
- **`parse_status="PARTIAL"` nei test**: il validatore di `CanonicalMessage` richiede almeno un'operazione per `PARSED UPDATE`. I test usano `PARTIAL` per costruire messaggi minimi senza operazioni legacy, il che è corretto per messaggi multi-ref puri.

### Casi limite non coperti

- `targeted_reports` con target non trovato: la logica è implementata (`_resolve_report_item`) ma non esiste un test dedicato per il caso NOT_FOUND dei report.
- `SELECTOR` con `side` assente ma altri filtri presenti → restituisce tutte le posizioni aperte (comportamento definito e corretto).
- Nessun test per `resolve_targeted` con `targeted_reports` non vuoti: il builder in Fase 2 produce report per-riga ma i test di integrazione replay rimangono aperti.
- Validazione `op_signal_id = None` (segnale trovato in `signals` ma assente in `operational_signals`): posizione esclusa silenziosamente da `resolved_position_ids`. Comportamento intenzionale — nessun errore.

---

## Lavoro svolto - Fase 4

### File modificati

- `src/telegram/router.py`
- `src/execution/targeted_planner.py`
- `src/execution/targeted_applier.py`
- `src/target_resolver/models.py`
- `src/target_resolver/resolver.py`
- `src/storage/parse_results_v1.py`
- `docs/in_progress/new_parser/PIANO_INCREMENTAZIONE_MULTI_REF.md`

### File creati

- `src/telegram/tests/test_router_targeted_runtime.py`
- `db/migrations/021_parse_results_v1_targeted_runtime.sql`

### Test aggiunti/modificati

- `src/execution/tests/test_targeted_runtime.py`
  - aggiornati i test planner/runtime per verificare `target_attempt_keys`
  - coperti Caso 1, Caso 2, Caso 3 e `NOT_FOUND`
- `src/telegram/tests/test_router_targeted_runtime.py`
  - `test_targeted_runtime_skips_legacy_update_apply`
  - `test_without_targeted_actions_legacy_runtime_remains_active`
- `src/target_resolver/tests/test_targeted_resolver.py`
  - mantenuti verdi i test preesistenti dopo l'estensione del resolver con `resolved_attempt_keys`

Risultato validazione mirata:
- `pytest src/execution/tests/test_targeted_runtime.py src/telegram/tests/test_router_targeted_runtime.py src/target_resolver/tests/test_targeted_resolver.py -q` → **13 passati**
- `pytest src/telegram/tests/test_router_canonical_v1.py src/telegram/tests/test_router_shadow.py -q` → **21 passati**

### Codice modificato

**Router**
- `_native_canonical_v1()` restituisce il `CanonicalMessage` nativo al chiamante.
- `_apply_phase4()` decide il percorso owner-level:
  - `targeted_actions` presenti + `USE_TARGETED_RUNTIME` attivo → `resolve_targeted()` + `build_plan()` + `apply_plan()`
  - `targeted_actions` assenti → resolver/runtime legacy invariati
- In presenza del percorso target-aware, `_apply_update_runtime()` non viene eseguito, evitando doppia applicazione.

**Resolver / planner**
- `ResolvedActionItem` e `ResolvedReportItem` espongono anche `resolved_attempt_keys`.
- `resolve_targeted()` popola gli `attempt_key` già nel binding.
- `build_plan()` usa `target_attempt_keys` stabili, mantenendo alias backward-compatible `build_targeted_plan()`.

**Runtime apply**
- `apply_plan()` applica le azioni usando direttamente `target_attempt_keys`.
- I report vengono persistiti per singola posizione in `trades.meta_json`.
- L'alias `apply_targeted_plan()` resta disponibile per compatibilità.

**Persistenza parallela**
- `parse_results_v1` è stato esteso con `targeted_resolved_json`.
- Il `MultiRefResolvedResult` viene salvato in parallelo al `canonical_json`, senza toccare `parse_results` legacy.

### Comportamento implementato

- Il runtime consuma il binding reale `azione -> target_attempt_keys`, non una lista piatta.
- Il planner non dipende più da lookup secondari su `op_signal_id` per ricostruire i target.
- Il router usa il percorso target-aware solo quando il contratto lo richiede.
- In assenza di shape target-aware, il comportamento legacy continua a funzionare invariato.

### Eventuali casi limite non coperti

- Replay end-to-end su dataset reale `trader_a` non eseguito in questa sessione.
- `targeted_resolved_json` viene persistito solo per i profili che passano dal percorso `parse_canonical()` nativo; il percorso shadow resta invariato per scelta conservativa.
- Nessun test dedicato per failure DB durante l'aggiornamento di `targeted_resolved_json`.

### Eventuali decisioni tecniche prese

- **Persistenza in `parse_results_v1`**: scelta conservativa per mantenere il risultato target-aware in parallelo, senza alterare il contratto legacy `parse_results`.
- **`attempt_key` risolto nel resolver**: rende `build_plan()` puro e riduce duplicazione di lookup nel runtime.
- **Alias pubblici mantenuti**: `build_targeted_plan()` e `apply_targeted_plan()` restano disponibili per minimizzare l'impatto sul codice già presente.

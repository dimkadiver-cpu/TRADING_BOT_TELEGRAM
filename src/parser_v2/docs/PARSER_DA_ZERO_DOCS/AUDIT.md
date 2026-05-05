# AUDIT — parser_v2

**Ultima modifica**: 2026-05-05  
**Scope**: Confronto implementazione vs piano documentale (docs/PARSER_DA_ZERO_DOCS/ fasi 1-13)  
**Valutazione finale**: ✅ 97% conforme — Fase 6 chiusa, architettura JSON-driven completata

---

## Tavola di conformità per fase

| Fase | Componente | File | Stato | Conformità |
|------|-----------|------|--------|------------|
| 1 | Contratti / Enum | `contracts/enums.py`, `contracts/canonical_message.py`, `contracts/parsed_message.py` | ✅ | 100% |
| 2 | TextNormalizer | `core/text_normalizer.py` | ✅ | 100% |
| 3 | MarkerMatcher | `core/marker_matcher.py` | ✅ | 100% |
| 4 | MarkerEvidenceResolver | `core/marker_evidence_resolver.py` | ✅ | 100% |
| 5 | SignalExtractor | `profiles/trader_a/signal_extractor.py` | ✅ | 95% |
| 6 | IntentEntityExtractor | `profiles/trader_a/intent_entity_extractor.py` | ✅ | 100% |
| 7 | LocalDisambiguator | `core/local_disambiguator.py` | ⚠️ | 85% |
| 8 | ClassificationResolver | `core/classification_resolver.py` | ✅ | 95% |
| 9 | TargetHintsExtractor | `core/target_hints_extractor.py` | ✅ | 95% |
| 10 | ParsedMessageBuilder | `core/parsed_message_builder.py` | ✅ | 95% |
| 11 | CanonicalTranslator | `translation/canonical_translator.py` | ⚠️ | 90% |
| 12 | Runtime + TraderAProfile | `core/runtime.py`, `profiles/trader_a/profile.py` | ✅ | 100% |
| 13 | Test suite | `tests/parser_v2/` | ⚠️ | 65% |

---

## Changelog modifiche post-audit iniziale

### 2026-05-05 — Refactoring IntentEntityExtractor (Fase 6 chiusa)

**Problema**: `IntentEntityExtractor` ignorava l'`evidence` già prodotta da `MarkerMatcher` e
faceva il proprio rilevamento degli intent via pattern regex hardcoded. Duplicazione di logica:
il JSON in `semantic_markers.json` era la source of truth per `MarkerMatcher` ma non per
l'extractor, che aveva patterns propri (più poveri).

**File modificati**:
- `profiles/trader_a/intent_entity_extractor.py` — refactoring completo
- `profiles/trader_a/profile.py` — riga 61: passa `evidence` all'extractor
- `profiles/trader_a/semantic_markers.json` — marker aggiunti per ADD_ENTRY, MODIFY_ENTRY,
  INFO_ONLY (erano vuoti) + marker inglesi per MOVE_STOP_TO_BE e CLOSE_FULL
- `tests/parser_v2/test_intent_entity_extractor_phase6.py` — aggiornato a pipeline completa
- `tests/parser_v2/test_runtime_profile_phase12.py` — corretta assertion `matched_markers`

**Cosa è cambiato nell'architettura**:

| Prima | Dopo |
|-------|------|
| `extract(normalized)` — ignora evidence | `extract(normalized, evidence)` — consuma evidence |
| `_INTENT_PATTERNS`: 16 regex hardcoded per rilevamento | rimosso |
| 18 costanti Cyrilliche `_CYR_*` | rimosse |
| Rilevamento + entity extraction accoppiati | Rilevamento: JSON → MarkerMatcher; Entity extraction: `_ENTITY_BUILDERS` |

**Nuove componenti**:
- `_ENTITY_BUILDERS: dict[str, EntityBuilder]` — mappa `IntentType → builder`, solo estrazione
  entità (prezzi, livelli, percentuali); zero rilevamento
- `_deduplicate_by_span()` — rimuove intent sovrapposti dopo entity building:
  - weak dentro strong (cross-intent): es. `SL_HIT/weak:"стоп"` dentro `MOVE_STOP_TO_BE/strong:"стоп в бу"`
  - stesso tipo che si sovrappone: es. due match `TP_HIT` su span contigui, mantiene il primo
- Logica strong-INFO suppression: se presente un marker forte di tipo `info` (es. admin),
  tutti i marker di intent deboli vengono scartati

**Impatto sui test**: 93/94 passano. Il test fallito (`test_risk_hint_is_optional_and_extracted_when_present`) è pre-esistente e riguarda `SignalExtractor` (non toccato).

**Invariante garantita**: aggiungere una frase a `semantic_markers.json` → si propaga
automaticamente al rilevamento senza toccare codice Python.

---

## Decisioni architetturali approvate — stato applicazione

| Decisione | Dove documentata | Applicata |
|-----------|-----------------|-----------|
| UPDATE/PARTIAL senza payload → warning multi_ref_mixed | GAP A1 | ✅ |
| Precedenza unica SIGNAL > UPDATE > REPORT > INFO | GAP A2 | ✅ |
| Separazione `ModifyEntryMode` vs `ModifyEntriesOperationKind` | DOC 12 | ✅ |
| `INVALIDATE_SETUP` con `reason_text` | GAP A4 | ✅ |
| `REPORT_RESULT` in `report.result`, non in `events` | scelta finale | ✅ |
| `ParsedIntent.status = EvidenceStatus` (non bool) | GAP A6 | ✅ |
| No mix `update.operations` + `targeted_actions` | GAP A7 | ⚠️ da testare |
| `line_index`, `span_start`, `span_end` in `ParsedIntent` | GAP A8 | ✅ campi presenti |
| Rilevamento intent via JSON (non hardcoded) | design core | ✅ chiuso |

---

## Gap per fase — stato attuale

### Fase 5 — SignalExtractor (95%)
- **Gap**: struttura `RANGE` non implementata (`2110-2120` formato zona di entrata)
- **Impatto**: messaggi con zona di entrata ricevono `TWO_STEP` invece di `RANGE`
- **Priorità**: media — raro nel dataset Trader A
- **Gap noto**: `risk_hint` non estratto in alcuni formati inglesi (test pre-esistente fallito)

### Fase 6 — IntentEntityExtractor (100%) ✅ chiuso
- ~~Pattern hardcoded nel codice~~ → rimosso
- Architettura JSON-driven completa: `MarkerMatcher` rileva, `_ENTITY_BUILDERS` estrae
- `_deduplicate_by_span` gestisce overlap cross-intent e stesso-tipo
- Strong-INFO suppression gestisce contesti admin/schedule

### Fase 7 — LocalDisambiguator (85%)
- **Gap**: docstring mancanti, nessun test unitario diretto
- **Impatto**: regole prefer/suppress difficili da verificare isolatamente
- **Priorità**: media

### Fase 11 — CanonicalTranslator (90%)
- **Gap**: grouping `targeted_actions` per firma semantica ha TODO nel codice
- **Comportamento attuale**: fallback conservativo → `ParseStatus.PARTIAL` + warning
- **Impatto**: messaggi multi-ref con intento omogeneo non collassano in `targeted_actions` strutturate
- **Priorità**: alta se il dataset contiene molti multi-ref

### Fase 13 — Test suite (65%)
- **Gap**: copertura parziale; i 13 golden case della fase non sono tutti verificati
- **Stato attuale**: 93/94 test passano; test_signal_extractor pre-esistente fallito (risk_hint)
- **Impatto**: regressioni non rilevabili automaticamente
- **Priorità**: alta prima del merge in produzione

---

## Punti di forza

1. **Rilevamento 100% JSON-driven** — `semantic_markers.json` è ora SSoT completa per tutti gli
   intent; aggiungere una frase al JSON si propaga senza toccare codice
2. **Separazione netta rilevamento / estrazione** — `MarkerMatcher` rileva, `_ENTITY_BUILDERS` estrae;
   le due responsabilità non si sovrappongono più
3. **Separazione core / profilo** — il runtime è universale; Trader B/C richiedono solo un nuovo `Profile`
4. **Schema Pydantic con `@model_validator`** — contratto enforced a costruzione
5. **Diagnostica ricca** — `ParsedMessage` porta `matched_markers`, `suppressed_markers`, `applied_rules`
6. **Span tracking** — `MarkerMatch` e `ParsedIntent` portano `span_start`/`span_end`
7. **Deduplicazione span** — overlap cross-intent e stesso-tipo gestiti in `_deduplicate_by_span`

---

## Rischi aperti

| Rischio | Severità | Mitigazione attuale |
|---------|----------|---------------------|
| Multi-ref grouping incompleto (Fase 11) | Media | Fallback PARTIAL + warning, non errore |
| `risk_hint` non estratto in alcuni formati | Bassa | Test pre-esistente, segnalato |
| Confidence signal fissa (1.0 / 0.6) | Bassa | Conservativa, non produce falsi positivi |
| Test coverage 65% (Fase 13) | Alta | Blocca deploy in produzione |
| `LocalDisambiguator` senza test unitari | Media | Coperto solo via integration test runtime |
| `cross_intent_suppression` nel resolver non copre span-containment | Bassa | Gestita in `_deduplicate_by_span` nell'extractor |

---

## Raccomandazioni per completamento

### Alta priorità (prima di integrazione router)
1. Completare i 13 golden case della Fase 13 — ogni caso deve avere asserzione su `ParsedMessage` e `CanonicalMessage`
2. Aggiungere almeno 2 test unitari per `LocalDisambiguator` (verify prefer/suppress rules)
3. Verificare round-trip `ParsedMessage → CanonicalMessage` per tutti i `primary_class`
4. Correggere `risk_hint` extraction in `SignalExtractor` per formato `"risk N%"`

### Media priorità (iterazione successiva)
5. Implementare `RANGE` entry structure in `SignalExtractor`
6. Completare multi-ref grouping in `CanonicalTranslator`
7. Aggiungere regola `cross_intent_suppression` nel resolver per span-containment
   (attualmente gestita nell'extractor come workaround)

### Bassa priorità (backlog)
8. Field-level confidence scoring per signal (ora usa 1.0 / 0.6 fisso)
9. Docstring per `LocalDisambiguator`
10. Valutare `InstructionUnit` se multi-ref misto diventa frequente nei dati reali

---

## Compatibilità con CLAUDE.md

- Nessuna modifica a `src/parser/` — ✅
- Nessun import da layer legacy — ✅
- Pydantic v2 ovunque — ✅
- Type hints e `from __future__ import annotations` — ✅
- Niente dict raw nel contratto canonico — ✅

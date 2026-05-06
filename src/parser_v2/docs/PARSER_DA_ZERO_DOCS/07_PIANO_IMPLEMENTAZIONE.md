# Piano di implementazione

## Obiettivo

Riscrivere il parser Trader A da zero fino a `CanonicalMessage`.

**Scope tassativo**: il parser termina a `CanonicalMessage`. Niente di più.

Fuori scope (verranno **riscritti** in seguito, non riusati dal vecchio sistema):

```text
TargetResolver
ApplicabilityValidator
ExecutionPlanner
ExecutionApplier
DB lifecycle validation
operation_rules
target_resolver
backtesting integration
```

> ⚠️ Nessun adapter `CanonicalMessage → TraderParseResult` legacy. Il vecchio `operation_rules` e `target_resolver` saranno riscritti per consumare `CanonicalMessage` direttamente.

---

## Struttura cartelle target

Riferimento canonico: [11_ARCHITETTURA_UNIVERSALE_PARSER.md](11_ARCHITETTURA_UNIVERSALE_PARSER.md).

```text
src/parser_v2/
  contracts/
    parsed_message.py
    canonical_message.py
    intents.py
    entities.py
    markers.py
    rules.py
    context.py
    enums.py             # IntentType, MessageClass, ParseStatus, ScopeHint, ...

  core/
    text_normalizer.py
    marker_matcher.py
    marker_evidence_resolver.py
    local_disambiguator.py
    classification_resolver.py
    parsed_message_builder.py
    target_hints_extractor.py    # default universale
    runtime.py

  translation/
    canonical_translator.py
    operation_builder.py
    report_builder.py

  profiles/
    base.py              # Protocol TraderParserProfile
    registry.py

    trader_a/
      profile.py
      semantic_markers.json
      rules.json
      signal_extractor.py
      intent_entity_extractor.py

  tests/
    unit/
    integration/
    fixtures/
```

Stato aggiornato dopo fasa 1: `src/parser_v2/` contiene `docs/` e i contratti Pydantic iniziali in `contracts/`. Il parser corrente vive in `src/parser/` e sarà sostituito (non migrato) man mano.

---

# Fase 1 — Contratti (`contracts/`)

Creare i modelli Pydantic divisi in file separati:

```text
contracts/enums.py            → IntentType, IntentCategory, MessageClass,
                                 ParseStatus, EntryStructure, EntryType,
                                 EvidenceStatus, ModifyEntryMode, ScopeHint
contracts/parsed_message.py   → ParsedMessage, ParsedIntent, SignalDraft
contracts/canonical_message.py→ CanonicalMessage, SignalPayload, UpdatePayload,
                                 ReportPayload, InfoPayload, UpdateOperation,
                                 ReportEvent, TargetedAction
contracts/entities.py         → IntentEntities (tutte le sottoclassi)
contracts/markers.py          → MarkerMatch, MarkerEvidence, NormalizedText
contracts/rules.py            → SemanticMarkers, ParserRules
contracts/context.py          → ParserContext, RawContext, TargetHints
```

> 📖 Tutti gli enum hanno **single source of truth** in [12_ENUMS_E_CONSTANTI.md](12_ENUMS_E_CONSTANTI.md).

Checklist:

```text
[x] eliminare nomi legacy U_* nei modelli nuovi
[x] usare primary_class: SIGNAL/UPDATE/REPORT/INFO
[x] usare parse_status: PARSED/PARTIAL/UNCLASSIFIED/ERROR
[x] usare evidence_status, non validation_status
[x] non includere campi DB/runtime
[x] schema_version="parsed_message_v2" / "canonical_message_v2"
```

## Lavoro svolto - fasa 1

### File modificati

```text
src/parser_v2/__init__.py
src/parser_v2/contracts/__init__.py
src/parser_v2/contracts/enums.py
src/parser_v2/contracts/parsed_message.py
src/parser_v2/contracts/canonical_message.py
src/parser_v2/contracts/entities.py
src/parser_v2/contracts/markers.py
src/parser_v2/contracts/rules.py
src/parser_v2/contracts/context.py
tests/parser_v2/test_contracts_phase1.py
src/parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md
```

### Comportamento implementato

```text
- Contratti Pydantic separati per enum, contesto, marker, rules, entità, ParsedMessage e CanonicalMessage.
- Enum canonici senza prefissi legacy U_* e con MessageClass, ParseStatus, EvidenceStatus, IntentType, ScopeHint, operation types e schema versions da 12_ENUMS_E_CONSTANTI.md.
- ParsedMessage usa primary_class, parse_status, evidence_status, target_hints, raw_context, warnings e diagnostics; non contiene validation_status, target risolti o campi DB/runtime.
- ParsedIntent include il ponte minimo pre-InstructionUnit: line_index, span_start, span_end.
- CanonicalMessage usa schema_version="canonical_message_v2" e valida la coerenza top-level tra primary_class e payload business.
- UpdateOperation consente un solo payload coerente con op_type, incluso INVALIDATE_SETUP con payload minimo reason_text.
- REPORT_RESULT resta intent canonico ma vive in ReportPayload.result, non in ReportPayload.events.
- UPDATE/PARTIAL senza operazioni/targeted_actions è ammesso solo con warning multi_ref_mixed_intents_not_supported.
```

### Eventuali casi limite non coperti

```text
- Non sono coperti parser runtime, normalizzazione testo, marker matching, evidence resolver, disambiguazione, estrazione segnale/intenti o traduzione ParsedMessage -> CanonicalMessage: appartengono alle fasi successive.
- Non è stata implementata associazione reale link/riga/intent; sono stati solo aggiunti i campi contrattuali minimi sugli intent.
- La validazione di completezza semantica del segnale oltre alla shape Pydantic resta responsabilità delle fasi di extractor/classification.
```

### Decisioni tecniche prese

```text
- I modelli nuovi restano isolati da src/parser/ legacy: nessun adapter verso TraderParseResult.
- ModifyEntryMode (intent-level) e ModifyEntriesOperationKind (operation-level) sono separati per non confondere ADD_ENTRY/REENTER con i mode di MODIFY_ENTRY.
- TargetHints rimane hint non risolto: non include position_id, order_id, lifecycle_state o validazioni DB.
- I test della fasa 1 sono schema-level e vivono in tests/parser_v2/test_contracts_phase1.py.
```

---

# Fase 2 — `TextNormalizer`

File: `core/text_normalizer.py`

Checklist:

```text
[x] lowercase
[x] ё -> е
[x] normalize dash (– — − -> -)
[x] collapse spaces
[x] preserve raw_text
[x] split lines (drop empty, strip whitespace)
```

Test:

```text
"Стоп в БУ"      -> "стоп в бу"
"стоп — в бу"    -> "стоп - в бу"
"line1\n\nline2" -> ["line1", "line2"]
""               -> ([], normalized="")
```

## Lavoro svolto - Fasa 2

### File modificati

```text
src/parser_v2/core/__init__.py
src/parser_v2/core/text_normalizer.py
tests/parser_v2/test_text_normalizer_phase2.py
src/parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md
```

### Comportamento implementato

```text
- TextNormalizer.normalize(text) restituisce il contratto NormalizedText.
- raw_text viene conservato identico all'input.
- normalized_text viene normalizzato con lowercase, ё -> е, dash unicode – — − -> -, trim e collasso degli spazi orizzontali.
- lines contiene solo righe non vuote, strip e normalizzate con le stesse regole.
```

### Eventuali casi limite non coperti

```text
- Non sono stati coperti MarkerMatcher, span, marker strong/weak o classificazione: appartengono alle fasi successive.
- Non sono stati normalizzati altri caratteri unicode oltre a quelli richiesti esplicitamente dalla Fasa 2.
```

### Decisioni tecniche prese

```text
- Per preservare una relazione semplice tra normalized_text e lines, le righe non vuote normalizzate vengono unite con "\n" in normalized_text.
- Il collasso degli spazi riguarda gli spazi orizzontali dentro ciascuna riga, non converte newline in spazi.
```

---

# Fase 3 — `MarkerMatcher` con span

File: `core/marker_matcher.py`

Output:

```python
MarkerMatch(
    name="MOVE_STOP_TO_BE",
    kind="intent",
    strength="strong",
    marker="стоп в бу",
    start=0,
    end=9
)
```

Checklist:

```text
[x] match strong
[x] match weak
[x] include start/end nel testo normalizzato
[x] support multiple occurrences
[x] no suppression nel matcher puro
```

## Lavoro svolto - Fasa 3

### File modificati

```text
src/parser_v2/core/marker_matcher.py
tests/parser_v2/test_marker_matcher_phase3.py
src/parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md
```

### Comportamento implementato

```text
- MarkerMatcher.match(normalized, markers) legge SemanticMarkers e produce una lista di MarkerMatch.
- Il matcher copre marker strong e weak.
- Ogni match include name, kind, strength, marker, start ed end calcolati su normalized.normalized_text.
- Le occorrenze multiple dello stesso marker vengono emesse tutte.
- Il matcher e' puro: non sopprime weak dentro strong, non applica cross-intent suppression e non usa rules.json.
- I match vengono restituiti ordinati per posizione nel testo normalizzato, preservando l'ordine di definizione per i pareggi sullo stesso span.
```

### Eventuali casi limite non coperti

```text
- Non sono stati implementati MarkerEvidenceResolver, suppression, diagnostics suppressed_markers o regole cross-intent: appartengono alla Fasa 4.
- Non sono state aggiunte normalizzazioni dei marker dentro MarkerMatcher: il matcher assume che i marker passati siano gia' coerenti con normalized.normalized_text.
- Non e' stato implementato un kind per ignore_markers, perche' MarkerKind non lo prevede e la Fasa 3 richiede solo il matcher puro dei marker semantici.
```

### Decisioni tecniche prese

```text
- I test usano stringhe Unicode esplicite per i casi cirillici, evitando ambiguita' dovute al mojibake presente in alcune parti dei documenti.
- La ricerca e' letterale tramite str.find, senza regex: sufficiente per i marker dichiarativi della Fasa 3 e piu' semplice da verificare sugli span.
- Le occorrenze sovrapposte sono tecnicamente rilevabili avanzando di un carattere dopo ogni match; nessuna suppression viene anticipata.
```

---

# Fase 4 — `MarkerEvidenceResolver`

File: `core/marker_evidence_resolver.py`

Checklist:

```text
[x] suppress weak inside strong same intent
[x] suppress cross-intent weak via rules.json
[x] produce suppressed_markers diagnostics
[x] produce clean MarkerEvidence
```

Test obbligatorio:

```text
Input: "стоп в бу"
Atteso: solo MOVE_STOP_TO_BE strong
        (NON deve emettere EXIT_BE weak su "бу")
```

## Lavoro svolto - Fasa 4

### File modificati

```text
src/parser_v2/core/marker_evidence_resolver.py
tests/parser_v2/test_marker_evidence_resolver_phase4.py
src/parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md
```

### Comportamento implementato

```text
- MarkerEvidenceResolver.resolve(matches, rules) restituisce MarkerEvidenceResolution con evidence pulita, suppressed_markers e diagnostics.
- I weak marker intent contenuti nello span di uno strong marker dello stesso intent vengono soppressi quando suppress_weak_inside_strong_same_intent=true.
- Le regole marker_resolution.cross_intent_suppression di ParserRules sopprimono weak marker di altri intent quando sono contenuti nello span dello strong marker configurato.
- Il caso obbligatorio "стоп в бу" mantiene solo MOVE_STOP_TO_BE strong e sopprime EXIT_BE weak su "бу".
- diagnostics espone suppressed_markers in formato leggibile e applied_marker_rules con le regole effettivamente applicate.
```

### Eventuali casi limite non coperti

```text
- Non sono stati implementati LocalDisambiguator, primary_intent precedence, category scoring o classificazione: appartengono alle fasi successive.
- Non sono gestiti ignore_markers, perche' la Fasa 4 richiede solo evidence resolution sui MarkerMatch gia' emessi.
- Non viene ancora costruito ParsedMessage.diagnostics completo con matched_markers/applied_disambiguation_rules: appartiene alla Fasa 10.
```

### Decisioni tecniche prese

```text
- La cross-intent suppression e' locale allo span dello strong marker che la giustifica; un weak marker non sovrapposto nello stesso messaggio viene mantenuto.
- Il risultato del resolver usa un piccolo container MarkerEvidenceResolution nel modulo core, evitando modifiche ai contratti Pydantic della Fasa 1.
- Le evidence restituite in result.evidence sono solo non soppresse; i marker soppressi restano disponibili separatamente in result.suppressed_markers per diagnostica.
```

---

# Fase 5 — `SignalExtractor` (profile-specific)

File: `profiles/trader_a/signal_extractor.py`

Checklist:

```text
[x] extract symbol
[x] extract side
[x] extract entries (con role PRIMARY/AVERAGING)
[x] extract entry_structure
[x] extract stop_loss
[x] extract take_profits
[x] extract risk_hint opzionale
[x] compute missing_fields
[x] compute completeness
```

Regola:

```text
COMPLETE = symbol + side + entries + stop_loss + take_profits
```

---

## Lavoro svolto - FASA 5

### File modificati

```text
src/parser_v2/profiles/__init__.py
src/parser_v2/profiles/trader_a/__init__.py
src/parser_v2/profiles/trader_a/signal_extractor.py
tests/parser_v2/test_signal_extractor_phase5.py
src/parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md
```

### Comportamento implementato

```text
- SignalExtractor.extract(normalized) restituisce SignalDraft oppure None.
- Estrae symbol con suffissi crypto espliciti e fallback conservativo per hashtag bare (#LINK -> LINKUSDT).
- Estrae side LONG/SHORT da marker inglesi e russi minimi.
- Estrae entry primaria e averaging con role PRIMARY/AVERAGING, is_optional coerente e entry_type MARKET/LIMIT.
- Calcola entry_structure ONE_SHOT, TWO_STEP o LADDER in base al numero di entry estratte.
- Estrae stop_loss, take_profits sequenziati e risk_hint opzionale singolo/range.
- Calcola missing_fields su symbol, side, entries, stop_loss e take_profits.
- completeness e' COMPLETE solo quando symbol + side + entries + stop_loss + take_profits sono presenti.
```

### Eventuali casi limite non coperti

```text
- Non sono stati implementati IntentEntityExtractor, LocalDisambiguator, ClassificationResolver, TargetHintsExtractor, ParsedMessageBuilder, CanonicalTranslator o runtime: appartengono alle fasi successive.
- Non viene ancora usato semantic_markers.json come sorgente dati del SignalExtractor; per la FASA 5 sono stati codificati solo i marker minimi necessari e verificati dai test.
- La struttura RANGE non e' ancora estratta da range price tipo "entry 62000-61000"; l'estrattore copre ONE_SHOT/TWO_STEP/LADDER.
- La copertura linguistica russa e' minima: side, entry, averaging, stop e risk; varianti piu' ricche restano da espandere con fixture/golden nelle fasi successive.
```

### Decisioni tecniche prese

```text
- L'extractor lavora su NormalizedText ma usa raw_text per conservare Price.raw originale.
- Testo con solo symbol/side senza entries, stop_loss o take_profits restituisce None, per non anticipare classificazione o trasformare target hint in signal.
- Il parser dei prezzi usa una euristica locale per virgola/punto/spazi, sufficiente per i casi FASA 5 senza modificare il contratto Price.
- Nessun adapter legacy verso TraderParseResult e nessuna dipendenza dal parser in src/parser/.
```

---

# Fase 6 — `IntentEntityExtractor` (profile-specific)

File: `profiles/trader_a/intent_entity_extractor.py`

Intents da estrarre:

```text
[x] MOVE_STOP_TO_BE
[x] MOVE_STOP
[x] CLOSE_FULL
[x] CLOSE_PARTIAL
[x] CANCEL_PENDING
[x] INVALIDATE_SETUP
[x] REENTER
[x] ADD_ENTRY
[x] MODIFY_ENTRY
[x] MODIFY_TARGETS
[x] ENTRY_FILLED
[x] TP_HIT
[x] SL_HIT
[x] EXIT_BE
[x] REPORT_RESULT
[x] INFO_ONLY
```

Non estrarre:

```text
[x] pnl
[x] R
[x] percent profit
[x] currency result
```

## Lavoro svolto - FASA 6

### File modificati

```text
src/parser_v2/profiles/trader_a/intent_entity_extractor.py
tests/parser_v2/test_intent_entity_extractor_phase6.py
src/parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md
```

### Comportamento implementato

```text
- IntentEntityExtractor.extract(normalized) restituisce list[ParsedIntent].
- Estrae i 16 intenti canonici della FASA 6 con categoria da INTENT_CATEGORY_BY_TYPE.
- Ogni ParsedIntent include confidence=1.0, raw_fragment, span_start/span_end e MarkerEvidence strong minimale.
- Popola le entita' minime dei contratti per MOVE_STOP, CLOSE_PARTIAL, CANCEL_PENDING, REENTER, ADD_ENTRY, MODIFY_ENTRY, MODIFY_TARGETS, ENTRY_FILLED, TP_HIT, SL_HIT, EXIT_BE, REPORT_RESULT e INFO_ONLY.
- MOVE_STOP supporta stop verso TP level e stop verso prezzo.
- MODIFY_ENTRY supporta i mode MARKET_NOW, UPDATE_PRICE e REMOVE con EntryLeg coerenti dove presenti.
- REPORT_RESULT conserva solo raw_summary: PnL, R, percent profit e currency result non vengono estratti come campi strutturati.
```

### Eventuali casi limite non coperti

```text
- La copertura marker e' volutamente minima e guidata dai test FASA 6; non replica tutto semantic_markers.json.
- Non sono implementati LocalDisambiguator, primary_intent precedence, classificazione, warnings, target_hints, ParsedMessageBuilder, CanonicalTranslator o runtime: appartengono alle fasi successive.
- Non viene ancora usato semantic_markers.json come sorgente dati runtime per l'IntentEntityExtractor.
- Le metriche di risultato trading vengono ignorate come entita' strutturate; restano solo nel raw_summary di REPORT_RESULT quando il messaggio e' riconosciuto come report.
```

### Decisioni tecniche prese

```text
- L'API resta parallela a SignalExtractor: input NormalizedText, output contract-level senza side effect.
- L'estrazione usa marker/pattern locali conservativi per evitare nuove dipendenze o refactor del matcher/evidence resolver.
- Il pattern generico "move stop" non matcha "move stop to be", per evitare doppia estrazione locale senza anticipare le regole di disambiguazione della FASA 7.
- Nessun adapter legacy verso TraderParseResult e nessuna dipendenza da src/parser/.
```

---

# Fase 7 — `LocalDisambiguator`

File: `core/local_disambiguator.py`

Checklist:

```text
[x] prefer/suppress rules
[x] primary_intent precedence
[x] regola contestuale: signal_payload presente ⇒ MARKET marker = entry_type
[x] diagnostics applied rules
[x] keep compatible composites
```

## Lavoro svolto - FASA 7

### File modificati

```text
src/parser_v2/core/local_disambiguator.py
tests/parser_v2/test_local_disambiguator_phase7.py
src/parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md
```

### Comportamento implementato

```text
- LocalDisambiguator.resolve(intents, rules, signal=None, normalized=None) restituisce LocalDisambiguationResult.
- Applica regole dichiarative locali prefer/over e suppress da ParserRules.disambiguation.
- Calcola primary_intent usando ParserRules.primary_intent_precedence, senza modificare primary_class o parse_status.
- Mantiene intenti compositi compatibili quando nessuna regola li sopprime esplicitamente.
- Applica la regola contestuale MARKET: se signal e' presente, un MODIFY_ENTRY/MARKET_NOW generato dallo stesso marker viene soppresso e il marker resta interpretato come entry_type MARKET del signal.
- Produce diagnostics con applied_disambiguation_rules e suppressed_intents.
```

### Eventuali casi limite non coperti

```text
- Non sono implementati ClassificationResolver, ParsedMessageBuilder, TargetHintsExtractor, CanonicalTranslator o runtime: appartengono alle fasi successive.
- La regola contestuale MARKET non modifica direttamente SignalDraft: assume che SignalExtractor abbia gia' assegnato entry_type=MARKET.
- Non sono stati introdotti warning utente; la FASA 7 espone solo diagnostics locali, mentre warnings e parse_status saranno composti nelle fasi successive.
- Non sono stati implementati controlli DB, target/lifecycle o validazione di applicabilita', esplicitamente fuori scope parser.
```

### Decisioni tecniche prese

```text
- L'API mantiene compatibilita' con resolve(intents, rules) e aggiunge signal/normalized come keyword opzionali per supportare la regola contestuale senza anticipare il runtime.
- Le regole restano dict dichiarativi dentro ParserRules.disambiguation, coerenti con il contratto esistente della FASA 1.
- prefer rimuove solo gli intenti elencati in over, non tutti gli intenti concorrenti, per preservare compositi compatibili come TP_HIT + MOVE_STOP_TO_BE.
- primary_intent viene calcolato nel risultato della disambiguazione ma la classificazione messaggio resta fuori scope FASA 7.
```

---

# Fase 8 — `ClassificationResolver`

File: `core/classification_resolver.py`

Regola:

```python
if signal is not None:
    primary_class = "SIGNAL"
elif update_intents:
    primary_class = "UPDATE"
elif report_intents:
    primary_class = "REPORT"
elif info_intents:
    primary_class = "INFO"
else:
    primary_class = "INFO"
    parse_status = "UNCLASSIFIED"
```

Nota runtime: quando la risoluzione marker lascia un `info` valido, il flusso si ferma prima di `extract_signal()` e `extract_intent_entities()`. `info` non è un fallback tardivo, è una guardia iniziale.

Checklist:

```text
[x] signal partial stays SIGNAL/PARTIAL
[x] update without target stays UPDATE + warning
[x] report does not become update
[x] info marker creates INFO/PARSED
[x] empty/no markers → INFO/UNCLASSIFIED
```

## Lavoro svolto - FASA 8

### File modificati

```text
src/parser_v2/core/classification_resolver.py
tests/parser_v2/test_classification_resolver_phase8.py
src/parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md
```

### Comportamento implementato

```text
- ClassificationResolver.resolve(signal, intents, target_hints=None) restituisce ClassificationResult con primary_class, parse_status e warnings.
- Se signal e' presente, primary_class=SIGNAL domina sugli intenti; signal COMPLETE senza missing_fields produce PARSED, altrimenti PARTIAL.
- Se non c'e' signal e sono presenti intenti UPDATE, primary_class=UPDATE e parse_status=PARSED.
- UPDATE senza target hint resta UPDATE/PARSED e aggiunge warning update_without_target_hint.
- TargetHints con reply, link, message id, explicit id, symbol o scope_hint diverso da UNKNOWN evita la warning update_without_target_hint.
- Se non ci sono UPDATE, gli intenti REPORT producono REPORT/PARSED.
- Se un marker `info` valido è presente, il runtime chiude il flusso su INFO prima di estrarre signal o intent.
- Se non ci sono UPDATE o REPORT, INFO_ONLY produce INFO/PARSED.
- Nessun signal e nessun intent utile produce INFO/UNCLASSIFIED.
```

### Eventuali casi limite non coperti

```text
- Non sono stati implementati TargetHintsExtractor, ParsedMessageBuilder, CanonicalTranslator o runtime: appartengono alle fasi successive.
- Non vengono ancora generate warnings per SIGNAL + UPDATE nello stesso messaggio; il resolver applica solo la precedenza SIGNAL richiesta dalla FASA 8.
- Non viene calcolata confidence o evidence_status; la composizione completa di ParsedMessage resta responsabilita' della FASA 10.
```

### Decisioni tecniche prese

```text
- Il resolver usa INTENT_CATEGORY_BY_TYPE come fonte canonica per distinguere UPDATE/REPORT/INFO, evitando euristiche su prefissi testuali.
- update_without_target_hint e' una warning parser-level: non implica validazione DB, lifecycle o applicabilita' operativa.
- Un signal e' PARSED solo con completeness=COMPLETE e missing_fields vuoto; in caso di incoerenza conservativa resta PARTIAL.
- L'output e' una dataclass locale minimale per non anticipare ParsedMessageBuilder.
```

---

# Fase 9 — `TargetHintsExtractor` (universale, default)

File: `core/target_hints_extractor.py`

Estrae:

```text
- reply_to_message_id (da context)
- telegram_links via regex (t.me/c/N/M, t.me/<channel>/M)
- telegram_message_ids (parsed dai link)
- explicit_ids ("signal id 123", "id сигнала 456")
- symbols (token che matchano target_hint_markers.symbol)
- scope_hint (ALL_LONG, ALL_SHORT, ALL_POSITIONS, ALL_OPEN, ALL_REMAINING)
```

Logica universale per tutti i trader. Override nel profilo solo se serve regex/comportamento custom.

Checklist:

```text
[x] regex telegram link uniforme
[x] dedup ID/link
[x] scope_hint da target_hint_markers
[x] tutti i campi opzionali
```

## Lavoro svolto - FASA 9

### File modificati

```text
src/parser_v2/contracts/rules.py
src/parser_v2/core/target_hints_extractor.py
tests/parser_v2/test_target_hints_extractor_phase9.py
src/parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md
```

### Comportamento implementato

```text
- TargetHintsExtractor.extract(normalized, context, markers) restituisce TargetHints con campi opzionali/default.
- Estrae reply_to_message_id da ParserContext, con fallback su RawContext.reply_to_message_id.
- Estrae link Telegram nei formati t.me/c/N/M e t.me/<channel>/M, con http/https opzionale.
- Estrae telegram_message_ids dal segmento finale numerico dei link.
- Deduplica link, message id, explicit id e symbol preservando l'ordine di prima occorrenza.
- Estrae explicit_ids dai formati "signal id 123", "signal id: 123", "сигнал id 123" e "id сигнала 456".
- Estrae symbols dai token che matchano target_hint_markers["symbol"] o target_hint_markers["SYMBOL"].
- Estrae scope_hint dagli scope canonici presenti in target_hint_markers, preferendo marker strong rispetto a weak.
```

### Eventuali casi limite non coperti

```text
- Non sono stati implementati target per riga, targeted_actions o gestione multi-ref: appartengono alle fasi successive.
- Non sono stati supportati formati Telegram diversi da t.me/c/N/M e t.me/<channel>/M.
- Non e' stata aggiunta logica custom per profilo: FASA 9 richiede solo il default universale.
- Non viene risolto alcun target reale, position_id, order_id o lifecycle state.
```

### Decisioni tecniche prese

```text
- target_hint_markers e' stato allargato a dict[str, MarkerSet] per supportare le chiavi tecniche documentate "symbol", "explicit_id" e "telegram_link" oltre agli scope canonici.
- Il risultato dei symbol viene normalizzato in uppercase e senza hashtag iniziale.
- Gli explicit id restano stringhe, coerenti con il contratto TargetHints.explicit_ids.
- In presenza di piu' scope hint, vince il primo marker strong trovato per posizione; i weak vengono considerati solo se non ci sono strong.
```

---

# Fase 10 — `ParsedMessageBuilder`

File: `core/parsed_message_builder.py`

Checklist:

```text
[x] build ParsedMessage
[x] include diagnostics (matched_markers, suppressed_markers, applied_rules)
[x] include warnings
[x] include raw_context
[x] include target_hints
[x] no DB validation
```

## Lavoro svolto - FASA 10

### File modificati

```text
src/parser_v2/core/parsed_message_builder.py
tests/parser_v2/test_parsed_message_builder_phase10.py
src/parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md
```

### Comportamento implementato

```text
- ParsedMessageBuilder.build(...) costruisce ParsedMessage usando i contratti gia' introdotti.
- La classificazione viene delegata a ClassificationResolver, quindi SIGNAL/UPDATE/REPORT/INFO e parse_status restano coerenti con la FASA 8.
- Include signal, intents finali, primary_intent, target_hints, warnings, raw_context e diagnostics.
- Unisce warning prodotti dalla classificazione e warning passate dal chiamante, deduplicandole in ordine.
- Compone diagnostics con matched_markers, suppressed_markers, applied_marker_rules, applied_disambiguation_rules, applied_rules, category_scores e, se presente un signal, contatori/missing_fields del segnale.
- Costruisce RawContext da ParserContext quando non e' gia' presente, oppure preserva RawContext esistente riempiendo normalized_text se manca.
- Calcola confidence diagnostica da evidence strong/weak degli intenti; per signal usa score conservativo 1.0 se COMPLETE, 0.6 se INCOMPLETE.
- Calcola evidence_status come LOW_CONFIDENCE sotto 0.5 o solo weak evidence, AMBIGUOUS per piu' intenti senza primary_intent, altrimenti RESOLVED.
- Non esegue validazione DB, target resolution, lifecycle validation, applicability validation o traduzione CanonicalMessage.
```

### Eventuali casi limite non coperti

```text
- Non e' stato implementato UniversalParserRuntime: l'orchestrazione end-to-end appartiene alla FASA 12.
- Non e' stato implementato CanonicalTranslator: la traduzione ParsedMessage -> CanonicalMessage appartiene alla FASA 11.
- Non sono gestiti targeted_actions, multi-ref per riga o warning multi-ref: appartengono alle fasi successive.
- La confidence del signal non usa ancora marker field-level per categoria SIGNAL, perche' il SignalExtractor corrente restituisce solo SignalDraft e non evidence field-level strutturata.
```

### Decisioni tecniche prese

```text
- Il builder resta universale e non dipende da Trader A o dal parser legacy in src/parser/.
- La soluzione piu' conservativa per confidence del signal e' diagnostica: COMPLETE=1.0, INCOMPLETE=0.6, senza usarla come criterio operativo.
- La diagnostica serializza marker/evidence in stringhe stabili "NAME/strength:marker@start:end", coerenti con le fasi precedenti.
- RawContext non viene sovrascritto quando arriva gia' dal ParserContext; vengono solo riempiti normalized_text e extracted_links se assenti.
```

---

# Fase 11 — `CanonicalTranslator`

File: `translation/canonical_translator.py`

Checklist:

```text
[x] SIGNAL -> SignalPayload
[x] MOVE_STOP_TO_BE -> SET_STOP ENTRY
[x] MOVE_STOP -> SET_STOP PRICE/TP_LEVEL
[x] CLOSE_FULL -> CLOSE FULL
[x] CLOSE_PARTIAL -> CLOSE PARTIAL
[x] CANCEL_PENDING -> CANCEL_PENDING
[x] INVALIDATE_SETUP -> INVALIDATE_SETUP
[x] MODIFY_ENTRY -> MODIFY_ENTRIES (con mode)
[x] ADD_ENTRY -> MODIFY_ENTRIES mode=ADD (operation builder)
[x] REENTER -> MODIFY_ENTRIES mode=REENTER
[x] MODIFY_TARGETS -> MODIFY_TARGETS
[x] REPORT intents -> minimal ReportPayload
[x] INFO -> InfoPayload (raw_fragment only)
[x] multi-ref -> targeted_actions (vedi doc 08)
```

## Lavoro svolto - FASA 11

### File modificati

```text
src/parser_v2/translation/__init__.py
src/parser_v2/translation/canonical_translator.py
tests/parser_v2/test_canonical_translator_phase11.py
src/parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md
```

### Comportamento implementato

```text
- CanonicalTranslator.translate(parsed) converte ParsedMessage in CanonicalMessage senza usare runtime, DB, target resolver o parser legacy.
- SIGNAL viene tradotto in SignalPayload e non produce update/report/targeted_actions.
- Gli intent UPDATE vengono tradotti in UpdateOperation coerenti con op_type: SET_STOP, CLOSE, CANCEL_PENDING, INVALIDATE_SETUP, MODIFY_ENTRIES e MODIFY_TARGETS.
- MOVE_STOP_TO_BE produce SET_STOP target_type=ENTRY.
- MOVE_STOP produce SET_STOP target_type=PRICE quando e' presente new_stop_price e target_type=TP_LEVEL quando e' presente stop_to_tp_level.
- CLOSE_FULL e CLOSE_PARTIAL producono CLOSE con close_scope FULL/PARTIAL e preservano prezzo/fraction quando presenti.
- ADD_ENTRY, REENTER e MODIFY_ENTRY producono MODIFY_ENTRIES con kind ADD/REENTER o il mode estratto dall'intent.
- MODIFY_TARGETS converte i Price estratti in TakeProfit sequenziati nel payload canonical.
- Gli intent REPORT producono ReportPayload minimale con events ENTRY_FILLED/TP_HIT/SL_HIT/EXIT_BE e REPORT_RESULT in report.result.
- UPDATE + REPORT mantiene primary_class=UPDATE e conserva il report come payload secondario.
- INFO produce InfoPayload con raw_fragment dall'entita' INFO_ONLY o dal testo raw.
- Quando target_hints contiene link/message id espliciti o selector globali, le operation omogenee vengono raggruppate in targeted_actions e update.operations resta vuoto.
- Multi-ref con piu' firme operative diverse resta conservativamente PARTIAL con warning multi_ref_mixed_intents_not_supported e senza targeted_actions.
```

### Eventuali casi limite non coperti

```text
- Non e' stato implementato UniversalParserRuntime ne' il profilo Trader A: appartengono alla FASA 12.
- Non e' stata introdotta InstructionUnit: doc 08 la rimanda esplicitamente.
- Non viene risolta alcuna associazione DB/position/order/lifecycle: resta fuori scope parser.
- La segmentazione per riga completa descritta in doc 08 non e' anticipata; la FASA 11 usa i TargetHints gia' presenti nel ParsedMessage e raggruppa solo azioni omogenee.
- MOVE_STOP senza prezzo o TP level non puo' costruire un SET_STOP valido; il traduttore non inventa un target operativo.
```

### Decisioni tecniche prese

```text
- Il traduttore e' universale e non dipende da src/parser/ legacy.
- Per i multi-ref misti e' stata scelta la soluzione piu' conservativa del doc 08: warning e PARTIAL, senza inventare mapping ref->intent.
- TargetedAction.params viene derivato dal payload canonical dell'operation con exclude_none=True, mantenendo una firma stabile per il grouping.
- Signal + update intents non produce update payload; gli update intent vengono esclusi dal canonical SIGNAL e viene aggiunta warning update_intents_dropped_in_signal_message.
- Se un UPDATE non produce alcuna operation valida e non e' un multi-ref misto supportato come PARTIAL, il traduttore restituisce parse_status=ERROR con warning canonical_translation_without_update_operation.
```

---

# Fase 12 — `UniversalParserRuntime` + Profile

File: `core/runtime.py`, `profiles/trader_a/profile.py`

Il runtime universale orchestra le fasi 2→11. Il profilo Trader A fornisce solo i pezzi specifici.

Metodo entry-point unico:

```python
def parse(text: str, context: ParserContext, profile: TraderParserProfile) -> CanonicalMessage:
    ...
```

Non creare:

```text
parse_message legacy
parse_canonical separato
```

Checklist:

```text
[x] UniversalParserRuntime orchestra le fasi 2->11
[x] entry-point unico parse(text, context, profile)
[x] TraderAProfile collega marker, rules, SignalExtractor e IntentEntityExtractor
[x] TargetHintsExtractor resta default universale nel runtime
[x] nessun parse_message legacy
[x] nessun parse_canonical separato
```

## Lavoro svolto - FASA 12

### File modificati

```text
src/parser_v2/core/runtime.py
src/parser_v2/profiles/trader_a/profile.py
tests/parser_v2/test_runtime_profile_phase12.py
src/parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md
```

### Comportamento implementato

```text
- UniversalParserRuntime.parse(text, context, profile) orchestra normalizzazione, marker matching, evidence resolution, signal extraction, intent extraction, disambiguazione, target hints, ParsedMessageBuilder e CanonicalTranslator.
- Aggiunto entry-point module-level parse(text, context, profile) con lo stesso contratto, senza creare parse_message legacy o parse_canonical separato.
- TraderAProfile espone trader_code="trader_a", load_markers(), load_rules(), extract_signal() ed extract_intent_entities().
- TraderAProfile collega gli extractor Trader A gia' implementati nelle fasi 5 e 6.
- Il runtime usa TargetHintsExtractor universale salvo eventuale override opzionale del profilo.
- Diagnostics del CanonicalMessage conserva matched_markers, suppressed_markers, applied marker rules, applied disambiguation rules e suppressed_intents.
```

### Eventuali casi limite non coperti

```text
- Non e' stata implementata la golden suite completa: appartiene alla FASA 13.
- Non sono stati creati semantic_markers.json e rules.json fisici; il profilo usa marker/rules minimi in codice per restare conservativo e non introdurre nuovi artefatti fuori dalla FASA 12.
- Non sono stati implementati registry profili, adapter legacy, TargetResolver, ApplicabilityValidator, ExecutionPlanner, ExecutionApplier, DB lifecycle validation o integrazione backtesting.
- L'associazione multi-ref per riga resta limitata a quanto gia' supportato dalla FASA 11 tramite TargetHints e CanonicalTranslator.
```

### Decisioni tecniche prese

```text
- Il Protocol TraderParserProfile e' definito in core/runtime.py per evitare un nuovo modulo base.py non richiesto esplicitamente dalla FASA 12.
- Il profilo ignora context/evidence negli extractor perche' gli extractor attuali lavorano gia' su NormalizedText; la firma resta compatibile con il contratto architetturale.
- Le istanze core del runtime sono iniettabili dal costruttore per testabilita', ma il percorso standard usa i componenti di default.
- I marker/rules Trader A sono volutamente minimali e coerenti con gli extractor esistenti, senza anticipare l'espansione della copertura prevista dalla FASA 13.
```

---

# Fase 13 — Test suite

## Test minimi (golden path)

```text
[x] segnale completo (5 campi) -> SIGNAL/PARSED
[x] segnale parziale senza TP -> SIGNAL/PARTIAL, missing_fields=["take_profits"]
[x] segnale parziale senza symbol -> SIGNAL/PARTIAL
[x] "стоп в бу" -> UPDATE/MOVE_STOP_TO_BE only (no EXIT_BE)
[x] "закрылся в бу" -> REPORT/EXIT_BE
[x] "первый тейк взяли" -> REPORT/TP_HIT level=1
[x] "выбило по стопу" -> REPORT/SL_HIT
[x] "закрываю по текущим" -> UPDATE/CLOSE_FULL
[x] "фикс 50%" -> UPDATE/CLOSE_PARTIAL fraction=0.5
[x] "убираем лимитки" -> UPDATE/CANCEL_PENDING
[x] "итог по сделке" -> REPORT/REPORT_RESULT
[x] "обзор рынка" -> INFO/INFO_ONLY
[x] "asdfgh" (testo ignoto) -> INFO/UNCLASSIFIED
```

## Test compositi

```text
[x] "первый тейк взяли, стоп в бу" -> UPDATE/MOVE_STOP_TO_BE + report.events=[TP_HIT]
[x] "выбило по стопу, всем закрываю" -> REPORT/SL_HIT + warning close_full_redundant_with_sl_hit
```

## Test multi-ref

```text
[x] 2 link + stesso intent per riga -> targeted_actions con telegram_message_ids=[a, b]
[x] 3 link + comando comune in fondo "прикроем" -> targeted_actions CLOSE FULL
[x] selector globale "зафиксировать все шорты" -> targeted_actions con scope_hint=ALL_SHORT
[x] link + intenti diversi per riga -> PARTIAL + warning multi_ref_mixed_intents_not_supported
```

## Edge cases

```text
[x] testo vuoto "" -> INFO/UNCLASSIFIED
[x] solo whitespace "   \n  " -> INFO/UNCLASSIFIED
[x] solo emoji "🚀🔥" -> INFO/UNCLASSIFIED
[x] solo numeri "2114" -> INFO/UNCLASSIFIED (no field marker → no signal)
[x] solo simbolo "ETHUSDT" -> INFO/UNCLASSIFIED (no side, no struttura)
[x] testo molto lungo (>5000 char) con poco senso -> INFO/UNCLASSIFIED, no crash
[x] caratteri non latini misti (emoji, cinese) -> non rompere il matcher
[x] marker weak + ignore_marker -> no intent emesso
[x] segnale + marker UPDATE nello stesso messaggio -> SIGNAL prevale, warning update_intents_dropped_in_signal_message
[x] reply_to_message_id senza testo (solo emoji) + comando in messaggio replied → caso non applicabile (parser vede solo testo+context)
[x] price con thousands separator e diversi locale: "90 000,5" / "90,000.5" / "90.000,5" -> Price.value uguale = 90000.5
```

## Test schema validation

```text
[x] CanonicalMessage SIGNAL con update non None -> ValidationError
[x] CanonicalMessage UPDATE/PARSED con update.operations vuoto E targeted_actions vuoto -> ValidationError
[x] CanonicalMessage UPDATE/PARTIAL con update.operations vuoto E targeted_actions vuoto -> consentito solo con warning multi_ref_mixed_intents_not_supported
[x] CanonicalMessage REPORT con report=None -> ValidationError
[x] CanonicalMessage INFO con signal/update/report/targeted_actions popolati -> ValidationError
```

## Lavoro svolto - fasa 13

### File modificati

```text
src/parser_v2/core/runtime.py
src/parser_v2/profiles/trader_a/profile.py
src/parser_v2/profiles/trader_a/intent_entity_extractor.py
tests/parser_v2/test_runtime_golden_phase13.py
src/parser_v2/docs/PARSER_DA_ZERO_DOCS/07_PIANO_IMPLEMENTAZIONE.md
```

### Comportamento implementato

```text
- Aggiunta golden suite end-to-end sul runtime universale con TraderAProfile.
- Coperti segnali completi e parziali, update, report, info e testo ignoto.
- Coperti compositi TP_HIT + MOVE_STOP_TO_BE e SL_HIT + CLOSE_FULL ridondante.
- Coperti multi-ref supportati: stesso intent su piu' link, comando comune in coda e selector globale ALL_SHORT.
- Coperto multi-ref misto non supportato come UPDATE/PARTIAL con warning multi_ref_mixed_intents_not_supported.
- Coperti edge case: vuoto, whitespace, emoji, numeri, solo simbolo, testo lungo, Unicode misto, weak marker con ignore marker, segnale con update marker e price locale.
- Coperti i casi di schema validation CanonicalMessage richiesti dalla fasa.
- Aggiunti marker CLOSE_FULL minimi per "всем закрываю", "прикроем" e "зафиксировать".
- Aggiunta warning parser-level close_full_redundant_with_sl_hit quando CLOSE_FULL viene soppresso per SL_HIT.
```

### Eventuali casi limite non coperti

```text
- Non e' stata implementata InstructionUnit o segmentazione completa ref->intent: resta fuori scope e rimandata dal doc 08.
- Il caso marker weak + ignore_marker verifica che non venga emesso intent; non introduce ancora diagnostics dedicate per ignore_markers.
- I multi-ref coperti restano quelli conservativi gia' supportabili con TargetHints e targeted_actions.
```

### Decisioni tecniche prese

```text
- Per il composito TP_HIT + MOVE_STOP_TO_BE la classe resta UPDATE e l'operation e' MOVE_STOP_TO_BE; il primary_intent conserva la precedence esistente dei report piu' rischiosi.
- La warning close_full_redundant_with_sl_hit viene derivata dal nome della regola di disambiguazione applicata, evitando nuova logica duplicata nel traduttore.
- I marker CLOSE_FULL aggiunti sono limitati ai casi richiesti dalla golden suite e gia' presenti nel documento marker completo.
- La validazione e' stata eseguita su tutta tests/parser_v2, senza toccare parser legacy, adapter, resolver target o DB runtime.
```

---

# Sequenza consigliata

```text
1. Contratti (Fase 1) — bloccare contratti prima di qualsiasi logica
2. TextNormalizer (Fase 2)
3. MarkerMatcher + EvidenceResolver (Fasi 3-4)
4. SignalExtractor (Fase 5)
5. IntentEntityExtractor (Fase 6)
6. LocalDisambiguator + ClassificationResolver (Fasi 7-8)
7. TargetHintsExtractor (Fase 9)
8. ParsedMessageBuilder (Fase 10)
9. CanonicalTranslator (Fase 11)
10. Runtime + Profile Trader A (Fase 12)
11. Tests (Fase 13)
```

La priorità è eliminare ambiguità strong/weak e doppia fonte di verità.

---

# Cosa NON va in questo piano

```text
- adapter CanonicalMessage → legacy TraderParseResult (non serve, riscriviamo a valle)
- migrazione operation_rules / target_resolver (saranno riscritti ex-novo in seguito)
- backtesting integration (separato)
- DB schema migration per parse_results_v2 (separato, non blocca il parser)
```

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

Lo stato attuale del codice è **`src/parser_v2/` contiene solo `docs/`** — nessun modulo Python ancora scritto. Il parser corrente vive in `src/parser/` e sarà sostituito (non migrato) man mano.

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
[ ] eliminare nomi legacy U_* nei modelli nuovi
[ ] usare primary_class: SIGNAL/UPDATE/REPORT/INFO
[ ] usare parse_status: PARSED/PARTIAL/UNCLASSIFIED/ERROR
[ ] usare evidence_status, non validation_status
[ ] non includere campi DB/runtime
[ ] schema_version="parsed_message_v2" / "canonical_message_v2"
```

---

# Fase 2 — `TextNormalizer`

File: `core/text_normalizer.py`

Checklist:

```text
[ ] lowercase
[ ] ё -> е
[ ] normalize dash (– — − -> -)
[ ] collapse spaces
[ ] preserve raw_text
[ ] split lines (drop empty, strip whitespace)
```

Test:

```text
"Стоп в БУ"      -> "стоп в бу"
"стоп — в бу"    -> "стоп - в бу"
"line1\n\nline2" -> ["line1", "line2"]
""               -> ([], normalized="")
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
[ ] match strong
[ ] match weak
[ ] include start/end nel testo normalizzato
[ ] support multiple occurrences
[ ] no suppression nel matcher puro
```

---

# Fase 4 — `MarkerEvidenceResolver`

File: `core/marker_evidence_resolver.py`

Checklist:

```text
[ ] suppress weak inside strong same intent
[ ] suppress cross-intent weak via rules.json
[ ] produce suppressed_markers diagnostics
[ ] produce clean MarkerEvidence
```

Test obbligatorio:

```text
Input: "стоп в бу"
Atteso: solo MOVE_STOP_TO_BE strong
        (NON deve emettere EXIT_BE weak su "бу")
```

---

# Fase 5 — `SignalExtractor` (profile-specific)

File: `profiles/trader_a/signal_extractor.py`

Checklist:

```text
[ ] extract symbol
[ ] extract side
[ ] extract entries (con role PRIMARY/AVERAGING)
[ ] extract entry_structure
[ ] extract stop_loss
[ ] extract take_profits
[ ] extract risk_hint opzionale
[ ] compute missing_fields
[ ] compute completeness
```

Regola:

```text
COMPLETE = symbol + side + entries + stop_loss + take_profits
```

---

# Fase 6 — `IntentEntityExtractor` (profile-specific)

File: `profiles/trader_a/intent_entity_extractor.py`

Intents da estrarre:

```text
[ ] MOVE_STOP_TO_BE
[ ] MOVE_STOP
[ ] CLOSE_FULL
[ ] CLOSE_PARTIAL
[ ] CANCEL_PENDING
[ ] INVALIDATE_SETUP
[ ] REENTER
[ ] ADD_ENTRY
[ ] MODIFY_ENTRY
[ ] MODIFY_TARGETS
[ ] ENTRY_FILLED
[ ] TP_HIT
[ ] SL_HIT
[ ] EXIT_BE
[ ] REPORT_RESULT
[ ] INFO_ONLY
```

Non estrarre:

```text
[ ] pnl
[ ] R
[ ] percent profit
[ ] currency result
```

---

# Fase 7 — `LocalDisambiguator`

File: `core/local_disambiguator.py`

Checklist:

```text
[ ] prefer/suppress rules
[ ] primary_intent precedence
[ ] regola contestuale: signal_payload presente ⇒ MARKET marker = entry_type
[ ] diagnostics applied rules
[ ] keep compatible composites
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

Checklist:

```text
[ ] signal partial stays SIGNAL/PARTIAL
[ ] update without target stays UPDATE + warning
[ ] report does not become update
[ ] info marker creates INFO/PARSED
[ ] empty/no markers → INFO/UNCLASSIFIED
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
[ ] regex telegram link uniforme
[ ] dedup ID/link
[ ] scope_hint da target_hint_markers
[ ] tutti i campi opzionali
```

---

# Fase 10 — `ParsedMessageBuilder`

File: `core/parsed_message_builder.py`

Checklist:

```text
[ ] build ParsedMessage
[ ] include diagnostics (matched_markers, suppressed_markers, applied_rules)
[ ] include warnings
[ ] include raw_context
[ ] include target_hints
[ ] no DB validation
```

---

# Fase 11 — `CanonicalTranslator`

File: `translation/canonical_translator.py`

Checklist:

```text
[ ] SIGNAL -> SignalPayload
[ ] MOVE_STOP_TO_BE -> SET_STOP ENTRY
[ ] MOVE_STOP -> SET_STOP PRICE/TP_LEVEL
[ ] CLOSE_FULL -> CLOSE FULL
[ ] CLOSE_PARTIAL -> CLOSE PARTIAL
[ ] CANCEL_PENDING -> CANCEL_PENDING
[ ] INVALIDATE_SETUP -> INVALIDATE_SETUP
[ ] MODIFY_ENTRY -> MODIFY_ENTRIES (con mode)
[ ] ADD_ENTRY -> MODIFY_ENTRIES mode=ADD (operation builder)
[ ] REENTER -> MODIFY_ENTRIES mode=REENTER
[ ] MODIFY_TARGETS -> MODIFY_TARGETS
[ ] REPORT intents -> minimal ReportPayload
[ ] INFO -> InfoPayload (raw_fragment only)
[ ] multi-ref -> targeted_actions (vedi doc 08)
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

---

# Fase 13 — Test suite

## Test minimi (golden path)

```text
[ ] segnale completo (5 campi) -> SIGNAL/PARSED
[ ] segnale parziale senza TP -> SIGNAL/PARTIAL, missing_fields=["take_profits"]
[ ] segnale parziale senza symbol -> SIGNAL/PARTIAL
[ ] "стоп в бу" -> UPDATE/MOVE_STOP_TO_BE only (no EXIT_BE)
[ ] "закрылся в бу" -> REPORT/EXIT_BE
[ ] "первый тейк взяли" -> REPORT/TP_HIT level=1
[ ] "выбило по стопу" -> REPORT/SL_HIT
[ ] "закрываю по текущим" -> UPDATE/CLOSE_FULL
[ ] "фикс 50%" -> UPDATE/CLOSE_PARTIAL fraction=0.5
[ ] "убираем лимитки" -> UPDATE/CANCEL_PENDING
[ ] "итог по сделке" -> REPORT/REPORT_RESULT
[ ] "обзор рынка" -> INFO/INFO_ONLY
[ ] "asdfgh" (testo ignoto) -> INFO/UNCLASSIFIED
```

## Test compositi

```text
[ ] "первый тейк взяли, стоп в бу" -> UPDATE/MOVE_STOP_TO_BE + report.events=[TP_HIT]
[ ] "выбило по стопу, всем закрываю" -> REPORT/SL_HIT + warning close_full_redundant_with_sl_hit
```

## Test multi-ref

```text
[ ] 2 link + stesso intent per riga -> targeted_actions con telegram_message_ids=[a, b]
[ ] 3 link + comando comune in fondo "прикроем" -> targeted_actions CLOSE FULL
[ ] selector globale "зафиксировать все шорты" -> targeted_actions con scope_hint=ALL_SHORT
[ ] link + intenti diversi per riga -> PARTIAL + warning multi_ref_mixed_intents_not_supported
```

## Edge cases

```text
[ ] testo vuoto "" -> INFO/UNCLASSIFIED
[ ] solo whitespace "   \n  " -> INFO/UNCLASSIFIED
[ ] solo emoji "🚀🔥" -> INFO/UNCLASSIFIED
[ ] solo numeri "2114" -> INFO/UNCLASSIFIED (no field marker → no signal)
[ ] solo simbolo "ETHUSDT" -> INFO/UNCLASSIFIED (no side, no struttura)
[ ] testo molto lungo (>5000 char) con poco senso -> INFO/UNCLASSIFIED, no crash
[ ] caratteri non latini misti (emoji, cinese) -> non rompere il matcher
[ ] marker weak + ignore_marker -> no intent emesso
[ ] segnale + marker UPDATE nello stesso messaggio -> SIGNAL prevale, warning update_intents_dropped_in_signal_message
[ ] reply_to_message_id senza testo (solo emoji) + comando in messaggio replied → caso non applicabile (parser vede solo testo+context)
[ ] price con thousands separator e diversi locale: "90 000,5" / "90,000.5" / "90.000,5" -> Price.value uguale = 90000.5
```

## Test schema validation

```text
[ ] CanonicalMessage SIGNAL con update non None -> ValidationError
[ ] CanonicalMessage UPDATE/PARSED con update.operations vuoto E targeted_actions vuoto -> ValidationError
[ ] CanonicalMessage UPDATE/PARTIAL con update.operations vuoto E targeted_actions vuoto -> consentito solo con warning multi_ref_mixed_intents_not_supported
[ ] CanonicalMessage REPORT con report=None -> ValidationError
[ ] CanonicalMessage INFO con signal/update/report/targeted_actions popolati -> ValidationError
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

# 11 — Architettura universale del parser

## Scopo

Questo documento definisce come progettare il nuovo parser in modo universale, così da poter aggiungere altri trader senza riscrivere ogni volta tutta la pipeline.

Obiettivo:

```text
Un solo runtime comune
+
profili trader-specifici
```

Il parser deve arrivare fino a:

```text
Raw Telegram message
↓
ParsedMessage
↓
CanonicalMessage
```

Restano fuori:

```text
TargetResolver
ApplicabilityValidator
ExecutionPlanner
ExecutionApplier
DB lifecycle validation
```

---

## Decisione principale

Il nuovo parser non deve essere:

```text
TraderAParser
TraderBParser
TraderCParser
```

ognuno con pipeline interna propria.

Deve essere:

```text
UniversalParserRuntime
+
TraderProfile
```

Dove:

```text
UniversalParserRuntime = pipeline comune
TraderProfile          = vocabolario + estrazione specifica
```

---

## Struttura proposta

```text
src/parser_v2/
  core/
    text_normalizer.py
    marker_matcher.py
    marker_evidence_resolver.py
    local_disambiguator.py
    classification_resolver.py
    target_hints_extractor.py
    parsed_message_builder.py
    runtime.py

  contracts/
    parsed_message.py
    canonical_message.py
    intents.py
    entities.py
    markers.py
    rules.py
    context.py

  translation/
    canonical_translator.py
    operation_builder.py
    report_builder.py

  profiles/
    base.py
    registry.py

    trader_a/
      profile.py
      semantic_markers.json
      rules.json
      signal_extractor.py
      intent_entity_extractor.py

    trader_b/
      profile.py
      semantic_markers.json
      rules.json
      signal_extractor.py
      intent_entity_extractor.py
```

---

## Core universale

Il core è identico per tutti i trader.

Componenti:

```text
TextNormalizer
MarkerMatcher
MarkerEvidenceResolver
LocalDisambiguator
ClassificationResolver
ParsedMessageBuilder
CanonicalTranslator
```

Questi moduli non devono sapere se il messaggio viene da Trader A, Trader B o Trader C.

---

# 1. `TextNormalizer`

## Responsabilità

```text
- lowercase
- ё -> е
- normalizzazione dash
- collapse spazi
- split lines
- conservazione raw_text
```

## Output

```python
NormalizedText(
    raw_text=...,
    normalized_text=...,
    lines=[...]
)
```

## Universale

Sì.  
Non deve essere specifico per trader.

Eventuali eccezioni linguistiche possono essere configurate, ma non codificate nel profilo.

---

# 2. `MarkerMatcher`

## Responsabilità

Trova marker da:

```text
semantic_markers.json
```

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

## Universale

Sì.  
Il matcher non deve sapere cosa significa il marker. Deve solo trovare span testuali.

---

# 3. `MarkerEvidenceResolver`

## Responsabilità

Pulisce marker forti/deboli.

Regole:

```text
- weak dentro strong stesso intent -> soppresso
- cross-intent suppression da rules.json
- strong domina weak
- marker ridondanti eliminati prima degli IntentResult
```

## Universale

Sì.  
Le regole specifiche stanno in `rules.json`.

---

# 4. `LocalDisambiguator`

## Responsabilità

Risolve conflitti locali tra intenti già rilevati.

Esempi:

```text
MOVE_STOP_TO_BE + MOVE_STOP -> MOVE_STOP_TO_BE
CANCEL_PENDING + INVALIDATE_SETUP -> dipende da rules.json
CLOSE_PARTIAL + CLOSE_FULL -> CLOSE_PARTIAL
```

## Universale

Sì.  
La logica di applicazione è comune. Le regole sono per profilo.

---

# 5. `ClassificationResolver`

## Responsabilità

Decide:

```text
SIGNAL
UPDATE
REPORT
INFO
```

Ordine:

```text
1. signal payload presente
2. update intents presenti
3. report intents presenti
4. info marker valido presente → short-circuit
5. fallback INFO / UNCLASSIFIED
```

## Universale

Sì.

---

# 6. `ParsedMessageBuilder`

## Responsabilità

Costruisce il contratto interno:

```python
ParsedMessage
```

Include:

```text
primary_class
parse_status
signal
intents
primary_intent
target_hints
warnings
diagnostics
raw_context
```

## Universale

Sì.

---

# 7. `CanonicalTranslator`

## Responsabilità

Traduce:

```text
ParsedMessage -> CanonicalMessage
```

Mapping comune:

```text
MOVE_STOP_TO_BE -> SET_STOP ENTRY
MOVE_STOP       -> SET_STOP PRICE / TP_LEVEL
CLOSE_FULL      -> CLOSE FULL
CLOSE_PARTIAL   -> CLOSE PARTIAL
CANCEL_PENDING  -> CANCEL_PENDING
MODIFY_ENTRY    -> MODIFY_ENTRIES
MODIFY_TARGETS  -> MODIFY_TARGETS
INVALIDATE_SETUP -> INVALIDATE_SETUP
```

## Universale

Sì.

Il translator non deve contenere logica specifica per Trader A.

---

## Contratti comuni

I contratti devono essere uguali per tutti i trader.

```text
ParsedMessage
CanonicalMessage
ParsedIntent
SignalDraft
SignalPayload
UpdatePayload
ReportPayload
TargetHints
TargetedAction
EntryLeg
Price
```

Ogni trader deve produrre questi stessi contratti.

---

## Profilo trader-specific

Un profilo trader deve contenere solo ciò che cambia da trader a trader.

Componenti:

```text
semantic_markers.json
rules.json
signal_extractor.py
intent_entity_extractor.py
profile.py
```

---

# `semantic_markers.json`

Contiene vocabolario.

Esempi:

```text
- marker per intenti
- marker per campi segnale
- marker per side LONG/SHORT
- marker per entry type MARKET/LIMIT
- marker info
- marker target hints
```

Non deve contenere logica.

---

# `rules.json`

Contiene logica dichiarativa.

Esempi:

```text
- marker_resolution
- cross_intent_suppression
- disambiguation
- primary_intent_precedence
- mode precedence
```

Non deve contenere marker lessicali, salvo condizioni testuali molto specifiche.

---

# `signal_extractor.py`

Responsabilità:

```text
estrarre SignalDraft / SignalPayload
```

Specifico per trader perché ogni trader scrive setup in modo diverso.

Esempi differenze:

```text
Trader A: A/B, вход, усреднение
Trader B: COIN, Direction, ENTRY, TARGETS, STOP
Trader C: formato russo breve
```

---

# `intent_entity_extractor.py`

Responsabilità:

```text
trasformare marker risolti in ParsedIntent con entità
```

Specifico per trader perché le frasi operative possono essere diverse.

Esempi:

```text
стоп в бу -> MOVE_STOP_TO_BE
по текущим -> MODIFY_ENTRY / MARKET_NOW
лимитки убираем -> CANCEL_PENDING
```

---

# `profile.py`

Deve solo collegare i pezzi specifici.

```python
class TraderAProfile(TraderParserProfile):
    trader_code = "trader_a"

    def load_markers(self) -> SemanticMarkers:
        ...

    def load_rules(self) -> ParserRules:
        ...

    def extract_signal(
        self,
        text: NormalizedText,
        context: ParserContext,
        evidence: list[MarkerEvidence],
    ) -> SignalDraft | None:
        ...

    def extract_intent_entities(
        self,
        text: NormalizedText,
        context: ParserContext,
        evidence: list[MarkerEvidence],
    ) -> list[ParsedIntent]:
        ...
```

Non deve contenere:

```text
- normalizzazione globale
- marker matcher
- canonical translator
- DB validation
- execution logic
```

---

## Interfaccia profilo

Contratto consigliato:

```python
class TraderParserProfile(Protocol):
    trader_code: str

    def load_markers(self) -> SemanticMarkers:
        ...

    def load_rules(self) -> ParserRules:
        ...

    def extract_signal(
        self,
        text: NormalizedText,
        context: ParserContext,
        evidence: list[MarkerEvidence],
    ) -> SignalDraft | None:
        ...

    def extract_intent_entities(
        self,
        text: NormalizedText,
        context: ParserContext,
        evidence: list[MarkerEvidence],
    ) -> list[ParsedIntent]:
        ...

    # extract_target_hints è OPZIONALE. Default in core/target_hints_extractor.py.
    # Override solo se il trader ha pattern di link/ID custom.
    def extract_target_hints(
        self,
        text: NormalizedText,
        context: ParserContext,
        markers: SemanticMarkers,
    ) -> TargetHints | None:
        ...
```

### `extract_target_hints` è universale per default

Telegram link, reply_to_message_id, scope_hint da `target_hint_markers` sono pattern comuni a tutti i trader. La logica vive in `core/target_hints_extractor.py` e viene chiamata direttamente dal runtime, **non dal profilo**.

Il profilo può sovrascrivere `extract_target_hints` solo se ha pattern custom (es. ID interno proprietario, formato esplicit ID diverso).

---

## Runtime universale

```python
class UniversalParserRuntime:
    def parse(
        self,
        text: str,
        context: ParserContext,
        profile: TraderParserProfile,
    ) -> CanonicalMessage:
        normalized = TextNormalizer.normalize(text)

        marker_matches = MarkerMatcher.match(
            normalized,
            profile.load_markers(),
        )

        evidence = MarkerEvidenceResolver.resolve(
            marker_matches,
            profile.load_rules(),
        )

        if has_info_marker(evidence):
            return build_info_message(...)

        signal = profile.extract_signal(
            normalized,
            context,
            evidence,
        )

        intents = profile.extract_intent_entities(
            normalized,
            context,
            evidence,
        )

        intents = LocalDisambiguator.resolve(
            intents,
            profile.load_rules(),
        )

        # target_hints estratti dal core con il vocabolario del profilo.
        # Il profilo può fornire un override se serve.
        target_hints_fn = (
            profile.extract_target_hints
            if hasattr(profile, "extract_target_hints")
            else TargetHintsExtractor.extract
        )
        target_hints = target_hints_fn(
            normalized,
            context,
            profile.load_markers(),
        )

        parsed = ParsedMessageBuilder.build(
            profile=profile.trader_code,
            signal=signal,
            intents=intents,
            target_hints=target_hints,
            context=context,
            diagnostics={
                "matched_markers": marker_matches,
                "resolved_evidence": evidence,
            },
        )

        return CanonicalTranslator.translate(parsed)
```

---

## Registry

Serve un registry per scegliere il profilo.

```python
class ParserProfileRegistry:
    def register(self, trader_code: str, profile: TraderParserProfile) -> None:
        ...

    def get(self, trader_code: str) -> TraderParserProfile:
        ...
```

Esempio:

```python
registry.register("trader_a", TraderAProfile())
registry.register("trader_b", TraderBProfile())
registry.register("trader_c", TraderCProfile())
```

Uso:

```python
profile = registry.get(trader_code)
canonical = runtime.parse(text, context, profile)
```

---

## Come aggiungere un nuovo trader

Per aggiungere `trader_b`:

```text
1. creare profiles/trader_b/
2. scrivere semantic_markers.json
3. scrivere rules.json
4. implementare signal_extractor.py
5. implementare intent_entity_extractor.py
6. creare profile.py
7. registrare TraderBProfile
8. aggiungere test
```

Non devi modificare:

```text
core/
contracts/
translation/
```

Se per aggiungere un trader devi modificare il core, il core è troppo specifico.

---

## Test comuni

Ogni profilo deve passare test comuni.

```text
test_info_unclassified
test_update_without_target
test_signal_complete
test_signal_partial
test_marker_weak_inside_strong
test_canonical_schema_valid
```

---

## Test specifici per trader

Ogni trader ha test propri.

Esempio Trader A:

```text
test_trader_a_signal_ab_entries
test_trader_a_stop_to_be
test_trader_a_market_now
test_trader_a_cancel_pending
test_trader_a_multi_ref_same_command
```

Esempio Trader B:

```text
test_trader_b_coin_direction_entry_targets_stop
test_trader_b_leverage_format
```

---

## Cosa deve restare universale

```text
ParsedMessage
CanonicalMessage
IntentType canonici
EntryLeg
Price
TargetHints
TargetedAction
ClassificationResolver
CanonicalTranslator
MarkerMatcher
MarkerEvidenceResolver
```

---

## Cosa può cambiare per trader

```text
marker lessicali
formato segnale
regex di estrazione
regole di disambiguazione
marker di entry/stop/tp
lingua e abbreviazioni
```

---

## Errore da evitare

Non creare profili così:

```python
class TraderAProfileParser:
    def parse_message(...):
        normalize()
        match()
        extract()
        classify()
        translate()
        validate()
```

Perché poi Trader B duplicherà tutto.

La forma corretta è:

```python
runtime.parse(text, context, profile)
```

---

## Differenza tra parser universale e profilo

```text
Parser universale:
  Come si parsifica

Profilo trader:
  Cosa significa il linguaggio di quel trader
```

---

## Regola architetturale

Il core può chiamare metodi del profilo.

Il profilo non deve chiamare il runtime operativo.

Il parser non deve chiamare DB.

Il translator non deve richiedere validazione DB.

---

## Decisione finale

Il nuovo parser deve essere progettato così:

```text
UniversalParserRuntime
+
TraderParserProfile
```

Con questa separazione:

```text
Core comune:
  pipeline, marker matching, disambiguazione, classificazione, canonicalizzazione

Profilo trader:
  marker, rules, signal extractor, intent entity extractor
```

Questa architettura permette di costruire altri parser senza duplicare logica e senza riportare il progetto nello stato ibrido attuale.

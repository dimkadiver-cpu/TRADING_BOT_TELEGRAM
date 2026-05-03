# Piano di implementazione — Nuova logica di classificazione parser

## 0. Obiettivo

Rifare la logica di classificazione del parser in modo che la classe finale del messaggio non dipenda più dai soli `classification_markers`.

La nuova classificazione deve derivare da:

```text
struttura estratta + intenti validati + contesto/targeting + marker come evidenza secondaria
```

Regola centrale:

```text
SIGNAL  → nasce dalla struttura del segnale
UPDATE  → nasce da intenti operativi validi
REPORT  → nasce da intenti di esito/stato validi
INFO    → fallback informativo
UNCLASSIFIED → fallback neutro
```

Inoltre si semplifica la tassonomia report:

```text
REPORT_FINAL_RESULT
REPORT_PARTIAL_RESULT
```

diventano:

```text
REPORT_RESULT
```

con attributi opzionali:

```text
result_scope = FINAL | PARTIAL | UNKNOWN
```

---

## 1. Problema attuale

### 1.1 `RulesEngine.classify()` è troppo autoritativo

Oggi `RulesEngine.classify()` assegna `NEW_SIGNAL`, `UPDATE`, `INFO` usando marker testuali strong/weak.

Questo è fragile perché marker come:

```text
entry
вход
sl:
tp:
```

possono comparire sia in nuovi segnali sia in update/report.

Esempio problematico:

```text
вход исполнен
```

Questo significa più probabilmente:

```text
ENTRY_FILLED / REPORT
```

ma contiene anche `вход`, quindi può spingere verso `NEW_SIGNAL`.

### 1.2 `NEW_SIGNAL` non deve nascere da marker

`NEW_SIGNAL` è una classe strutturale.

Deve essere deciso da campi estratti:

```text
symbol + side + entry + stop_loss + take_profit
```

Non da parole come `entry`, `вход`, `tp`, `sl`.

### 1.3 La confidence attuale è debole

Se un marker strong dà score `1.0`, la confidence diventa già massima.

Questo non misura realmente l’affidabilità della classificazione.

### 1.4 Le `classification_rules` configurate non sono centrali

Nel profilo esistono regole strutturali, ma la classificazione runtime attuale non le usa come fonte primaria.

La configurazione quindi suggerisce una logica più avanzata di quella realmente applicata.

---

## 2. Decisione architetturale

### 2.1 Separare marker evidence da classificazione finale

`RulesEngine.classify()` non deve più decidere direttamente la classe finale.

Deve diventare un produttore di evidenze:

```text
ClassEvidence
```

Esempio:

```python
ClassEvidence(
    scores={
        "new_signal": 0.4,
        "update": 1.0,
        "info_only": 0.0,
    },
    matched_markers=[
        {"category": "new_signal", "marker": "вход", "strength": "strong"},
        {"category": "update", "marker": "вход исполнен", "strength": "strong"},
    ],
)
```

La decisione finale viene fatta da un nuovo componente:

```text
ClassificationResolver
```

---

## 2.2 Contratto finale: nessun `message_type`

Il nuovo piano non deve introdurre `message_type` nel risultato finale.

Contratto finale da mantenere:

```text
primary_class
parse_status
intents
targeted_actions
targeted_reports
diagnostics
```

Uso corretto:

```text
Nuovo segnale completo:
primary_class = SIGNAL
parse_status = PARSED

Nuovo segnale incompleto:
primary_class = SIGNAL
parse_status = PARTIAL
diagnostics.missing_fields = [...]

Update:
primary_class = UPDATE
parse_status = PARSED

Report:
primary_class = REPORT
parse_status = PARSED

Info/non classificato:
primary_class = INFO
parse_status = PARSED oppure UNCLASSIFIED
```

`message_type` può restare solo nel vecchio `ClassificationResult` di `RulesEngine.classify()` come compatibilità interna, ma non deve comparire nel `ParsedMessage`, nel `CanonicalMessage` o nel nuovo output del resolver.

---

## 3. Nuovo flusso target

```text
TraderAProfileParser.parse()
    ↓
SharedParserRuntime.parse()
    ↓
RulesEngine.detect_class_evidence(text)
    ↓
rules.detect_intents_with_evidence(text)
    ↓
TraderAExtractors.extract(text, ...)
    ↓
_build_intent_results(...)
    ↓
HistoryBackedIntentValidator.validate(...)
    ↓
ProfileRulesDisambiguationEngine.apply(...)
    ↓
ClassificationResolver.resolve(...)
    ↓
IntentTranslator.translate(...)
    ↓
CanonicalMessage
```

Nota: se nel codice attuale alcuni componenti non sono ancora collegati realmente al runtime, il piano li integra in modo progressivo.

---

## 4. Responsabilità dei componenti

## 4.1 `RulesEngine`

### Responsabilità corretta

Deve produrre evidenze grezze:

```text
marker match
intent marker evidence
combination evidence
blacklist evidence
```

### Non deve fare

Non deve decidere da solo:

```text
primary_class
CanonicalMessage.message_type finale
SIGNAL/UPDATE/REPORT
```

### Output desiderato

```python
@dataclass
class ClassEvidence:
    scores: dict[str, float]
    matched_markers: list[MarkerMatch]
    winning_hint: str | None
    confidence_hint: float
    reasons: list[str]
```

---

## 4.2 `TraderAExtractors`

### Responsabilità corretta

Estrarre dati strutturali e intenti candidati:

```text
signal_candidate
symbol
side
entry
stop_loss
take_profit
raw_intents
target_refs
reply_refs
links
numbers
levels
```

### Output minimo richiesto

```python
ExtractionResult:
    signal: SignalCandidate | None
    fields_present: set[str]
    raw_intents: list[RawIntent]
    entities: dict
    targeting: TargetingInfo | None
```

---

## 4.3 `IntentResultBuilder`

### Responsabilità corretta

Costruire `IntentResult` partendo principalmente dagli intenti estratti dall’extractor.

I match del rules engine servono a:

```text
aggiungere evidence
aumentare/diminuire strength
spiegare il perché
```

ma non devono creare intenti operativi da soli se non c’è estrazione coerente.

Eccezione ammessa: intenti semplici puramente testuali e non ambigui, per esempio:

```text
CANCEL_PENDING
MOVE_STOP_TO_BE
```

ma solo se hanno marker strong e non confliggono con struttura segnale.

---

## 4.4 `HistoryBackedIntentValidator`

### Responsabilità corretta

Validare intenti in base a:

```text
reply/link target
messaggio target
symbol coerente
stato precedente noto
presenza di segnale collegato
```

### Esempi

```text
MOVE_STOP_TO_BE senza target ma con reply valido → valido
MOVE_STOP_TO_BE senza target e senza contesto → ambiguo o invalido
TP_HIT con target message coerente → report valido
CLOSE_FULL su segnale non trovato → valido solo se policy consente global selector
```

---

## 4.5 `ProfileRulesDisambiguationEngine`

### Responsabilità corretta

Applicare regole di profilo:

```text
soppressione
precedenza
incompatibilità
risoluzione conflitti
```

Esempio:

```text
MOVE_STOP_TO_BE batte MOVE_STOP se non c’è livello numerico esplicito.
CLOSE_FULL batte CLOSE_PARTIAL se entrambi rilevati ma esiste marker forte di full close.
REPORT_RESULT non deve sopprimere UPDATE se il messaggio contiene anche azione operativa concreta.
```

---

## 4.6 `ClassificationResolver`

Nuovo componente centrale.

### Responsabilità

Decidere:

```text
primary_class
parse_status
classification_confidence
classification_reasons
```

partendo da:

```text
signal completeness
validated intents
report intents
targeting/context
marker evidence
```

### Input

```python
@dataclass
class ClassificationInput:
    text: str
    signal: SignalCandidate | None
    signal_completeness: float
    fields_present: set[str]
    intents: list[IntentResult]
    class_evidence: ClassEvidence
    targeting: TargetingInfo | None
    history_context: HistoryContext | None
```

### Output

```python
@dataclass
class ResolvedClassification:
    primary_class: str          # SIGNAL | UPDATE | REPORT | INFO
    parse_status: str          # PARSED | PARTIAL | UNCLASSIFIED | ERROR
    confidence: float
    reasons: list[str]
    warnings: list[str]
    diagnostics: dict
```

Nota: `message_type` non viene introdotto nel contratto finale. Rimane solo come eventuale campo legacy/intermedio di `RulesEngine.classify()`, se mantenuto per compatibilità.

---

## 5. Regole decisionali finali

## 5.1 Classe `SIGNAL`

### Regola

```text
Se esiste signal_candidate completo:
    primary_class = SIGNAL
    parse_status = PARSED
```

### Completezza minima

Per Trader A:

```text
symbol
side
entry
stop_loss
almeno 1 take_profit
```

### Setup incompleto

Se esistono almeno:

```text
symbol + side + entry
```

ma mancano stop o target:

```text
primary_class = SIGNAL
parse_status = PARTIAL
diagnostics.missing_fields = [...]
```

oppure, se si vuole mantenere lo schema più semplice:

```text
primary_class = SIGNAL
parse_status = PARTIAL
diagnostics.missing_fields = [...]
```

Decisione consigliata:

```text
Non introdurre SETUP_INCOMPLETE come message_type.
Usare:
- primary_class = SIGNAL
- parse_status = PARTIAL
- diagnostics.missing_fields = [...]
```

Se serve un intent esplicito, usare un intent/flag interno tipo `CREATE_SIGNAL` con `signal_completeness < 1.0`, non un nuovo campo `message_type`.

---

## 5.2 Classe `UPDATE`

### Regola

```text
Se esistono intenti operativi validati:
    primary_class = UPDATE
    parse_status = PARSED
```

### Intenti operativi

```text
MOVE_STOP
MOVE_STOP_TO_BE
CLOSE_FULL
CLOSE_PARTIAL
CANCEL_PENDING
REMOVE_PENDING_ENTRY
MODIFY_ENTRY
MODIFY_TARGETS
INVALIDATE_SETUP
ADD_ENTRY
REENTER
```

### Nota

`UPDATE` non deve essere deciso da marker generici.

Deve nascere da intenti validi.

---

## 5.3 Classe `REPORT`

### Regola

```text
Se esistono solo intenti report validati:
    primary_class = REPORT
    parse_status = PARSED
```

### Intenti report

```text
ENTRY_FILLED
TP_HIT
SL_HIT
EXIT_BE
REPORT_RESULT
```

### Caso misto UPDATE + REPORT

Se un messaggio contiene sia update operativo sia report:

```text
primary_class = UPDATE
secondary_class = REPORT
```

Oppure, se il modello non prevede `secondary_class`:

```text
primary_class = UPDATE
message_type = UPDATE
reports = [...]
```

Decisione consigliata:

```text
UPDATE batte REPORT se c’è almeno una azione operativa state-changing.
REPORT resta dentro targeted_reports.
```

---

## 5.4 Classe `INFO`

### Regola

```text
Se non c’è signal
e non ci sono intenti validi
e ci sono marker info_only:
    primary_class = INFO
    parse_status = PARSED
```

### Nota

`INFO` è fallback, non una classe competitiva forte.

---

## 5.5 Classe `INFO` con `parse_status=UNCLASSIFIED`

### Regola

```text
Se non c’è struttura
e non ci sono intenti validi
e non ci sono marker info solidi:
    primary_class = INFO
    parse_status = UNCLASSIFIED
```

---

## 6. Semplificazione report: `REPORT_RESULT`

## 6.1 Rimozione delle varianti

Rimuovere progressivamente:

```text
REPORT_FINAL_RESULT
REPORT_PARTIAL_RESULT
```

Sostituire con:

```text
REPORT_RESULT
```

---

## 6.2 Nuovi attributi

Aggiungere attributi opzionali:

```python
result_scope: Literal["FINAL", "PARTIAL", "UNKNOWN"]
result_status: Literal["TP", "SL", "BE", "PROFIT", "LOSS", "UNKNOWN"]
result_value: float | None
result_currency: str | None
result_percent: float | None
```

---

## 6.3 Esempi

### Esempio 1

```text
закрыли часть +30%
```

Output:

```json
{
  "intent": "REPORT_RESULT",
  "result_scope": "PARTIAL",
  "result_status": "PROFIT",
  "result_percent": 30
}
```

### Esempio 2

```text
сделка закрыта в плюс
```

Output:

```json
{
  "intent": "REPORT_RESULT",
  "result_scope": "FINAL",
  "result_status": "PROFIT"
}
```

### Esempio 3

```text
итог +120$
```

Output:

```json
{
  "intent": "REPORT_RESULT",
  "result_scope": "UNKNOWN",
  "result_status": "PROFIT",
  "result_value": 120,
  "result_currency": "USD"
}
```

---

## 7. Modifiche ai file

## 7.1 `src/parser/rules_engine.py`

### Modifiche

- Aggiungere `detect_class_evidence(text)`.
- Mantenere `classify(text)` solo per compatibilità.
- Spostare la logica di scoring marker dentro `detect_class_evidence`.
- Restituire marker match dettagliati.
- Non usare più `classify()` come fonte finale della classe.

### Checklist

- [ ] Creare dataclass `MarkerMatch`.
- [ ] Creare dataclass `ClassEvidence`.
- [ ] Implementare `detect_class_evidence(text)`.
- [ ] Fare in modo che `classify(text)` chiami `detect_class_evidence(text)` e produca vecchio output compatibile.
- [ ] Aggiungere test su marker forti/deboli.
- [ ] Aggiungere test su pareggio `new_signal` vs `update`.
- [ ] Aggiungere test su `вход исполнен`.

---

## 7.2 `src/parser/shared/runtime.py`

### Modifiche

- Sostituire uso diretto di `classification.message_type` per `primary_class`.
- Chiamare `RulesEngine.detect_class_evidence(text)`.
- Chiamare `ClassificationResolver.resolve(...)`.
- Usare il risultato del resolver per costruire `ParsedMessage`.
- Non fare più:

```python
classification.message_type == "NEW_SIGNAL" → SIGNAL
```

### Checklist

- [ ] Inserire `class_evidence = rules.detect_class_evidence(text)`.
- [ ] Passare `signal`, `intents`, `class_evidence`, `targeting` al resolver.
- [ ] Rimuovere o ridurre `_select_primary_class(...)`.
- [ ] Rimuovere dipendenza autoritativa da `classification.message_type`.
- [ ] Mantenere compatibilità con vecchi campi `event_type/message_subtype` se ancora richiesti.
- [ ] Aggiungere diagnostica `classification_reasons`.
- [ ] Aggiungere diagnostica `class_evidence`.

---

## 7.3 Nuovo file: `src/parser/shared/classification_resolver.py`

### Responsabilità

Contenere tutta la decisione finale sulla classe.

### Struttura proposta

```python
class ClassificationResolver:
    def resolve(self, input: ClassificationInput) -> ResolvedClassification:
        ...
```

### Checklist

- [ ] Creare `ClassificationInput`.
- [ ] Creare `ResolvedClassification`.
- [ ] Implementare regola `SIGNAL`.
- [ ] Implementare regola `UPDATE`.
- [ ] Implementare regola `REPORT`.
- [ ] Implementare regola `INFO`.
- [ ] Implementare fallback `INFO` con `parse_status=UNCLASSIFIED`.
- [ ] Gestire caso misto `UPDATE + REPORT`.
- [ ] Gestire `SETUP_INCOMPLETE`.
- [ ] Restituire `reasons`.
- [ ] Restituire `warnings`.

---

## 7.4 `src/parser/shared/intent_taxonomy.py` oppure file equivalente

### Modifiche

Centralizzare la classificazione degli intenti:

```python
UPDATE_INTENTS = {
    "MOVE_STOP",
    "MOVE_STOP_TO_BE",
    "CLOSE_FULL",
    "CLOSE_PARTIAL",
    "CANCEL_PENDING",
    "REMOVE_PENDING_ENTRY",
    "MODIFY_ENTRY",
    "MODIFY_TARGETS",
    "INVALIDATE_SETUP",
    "ADD_ENTRY",
    "REENTER",
}

REPORT_INTENTS = {
    "ENTRY_FILLED",
    "TP_HIT",
    "SL_HIT",
    "EXIT_BE",
    "REPORT_RESULT",
}
```

### Checklist

- [ ] Creare o aggiornare file tassonomia intenti.
- [ ] Rimuovere riferimenti diretti sparsi a `REPORT_FINAL_RESULT`.
- [ ] Rimuovere riferimenti diretti sparsi a `REPORT_PARTIAL_RESULT`.
- [ ] Aggiungere helper `is_update_intent(intent_name)`.
- [ ] Aggiungere helper `is_report_intent(intent_name)`.
- [ ] Aggiungere helper `is_state_changing_intent(intent_name)`.

---

## 7.5 `src/parser/trader_profiles/trader_a/semantic_markers.json`

### Modifiche

Ripulire `classification_markers.new_signal`.

Marker come questi non devono essere marker strong di classe:

```text
entry
вход
sl:
tp:
```

Devono stare nei field markers o negli extractor.

### Checklist

- [ ] Rimuovere marker generici da `classification_markers.new_signal.strong`.
- [ ] Tenere solo marker realmente distintivi di nuovo segnale.
- [ ] Spostare `entry/вход/sl/tp` in sezione field/extraction se necessario.
- [ ] Aggiungere marker per `REPORT_RESULT`.
- [ ] Rimuovere marker dedicati a `REPORT_FINAL_RESULT`.
- [ ] Rimuovere marker dedicati a `REPORT_PARTIAL_RESULT`.
- [ ] Aggiornare marker `TP_HIT`, `SL_HIT`, `EXIT_BE` se necessario.

---

## 7.6 `src/parser/trader_profiles/trader_a/rules.json`

### Modifiche

Aggiornare le regole di disambiguazione e precedenza.

### Nuove regole consigliate

```json
{
  "classification_rules": [
    {
      "name": "complete_signal_structure",
      "when_all_fields_present": ["symbol", "side", "entry", "stop_loss", "take_profit"],
      "then": "NEW_SIGNAL",
      "confidence": 0.95
    },
    {
      "name": "incomplete_signal_structure",
      "when_all_fields_present": ["symbol", "side", "entry"],
      "when_any_field_missing": ["stop_loss", "take_profit"],
      "then": "SETUP_INCOMPLETE",
      "confidence": 0.65
    }
  ]
}
```

### Checklist

- [ ] Aggiornare `classification_rules` per struttura segnale.
- [ ] Aggiornare `primary_intent_precedence`.
- [ ] Sostituire `REPORT_FINAL_RESULT` e `REPORT_PARTIAL_RESULT` con `REPORT_RESULT`.
- [ ] Aggiungere regole per `result_scope`.
- [ ] Aggiungere regole per `result_status`.
- [ ] Aggiungere regola conflitto `UPDATE` batte `REPORT` se state-changing.
- [ ] Aggiungere regola conflitto `SIGNAL` batte marker update solo se struttura completa.

---

## 7.7 `src/parser/normalization.py` oppure translator equivalente

### Modifiche

- Accettare `REPORT_RESULT`.
- Tradurre `REPORT_RESULT` in `targeted_reports`.
- Popolare `result_scope`, `result_status`, eventuali valori numerici.
- Non generare più `REPORT_FINAL_RESULT` / `REPORT_PARTIAL_RESULT`.

### Checklist

- [ ] Aggiornare mapping intent → canonical action.
- [ ] Aggiungere `REPORT_RESULT`.
- [ ] Rimuovere mapping obsoleto finale/parziale.
- [ ] Aggiungere normalizzazione `result_scope`.
- [ ] Aggiungere normalizzazione `result_status`.
- [ ] Verificare compatibilità con `parse_results`.
- [ ] Verificare compatibilità con downstream/backtesting.

---

## 7.8 Modelli Pydantic / dataclass in `src/parser/models`

### Modifiche

Aggiungere modello report semplificato:

```python
class ReportResultPayload(BaseModel):
    result_scope: Literal["FINAL", "PARTIAL", "UNKNOWN"] = "UNKNOWN"
    result_status: Literal["TP", "SL", "BE", "PROFIT", "LOSS", "UNKNOWN"] = "UNKNOWN"
    result_value: float | None = None
    result_currency: str | None = None
    result_percent: float | None = None
```

### Checklist

- [ ] Aggiungere payload `ReportResultPayload`.
- [ ] Collegare payload a `IntentResult.entities` o campo equivalente.
- [ ] Validare enum.
- [ ] Aggiungere serializzazione JSON.
- [ ] Aggiungere test modello.

---

## 8. Ordine di implementazione consigliato

## Fase 1 — Preparazione tassonomia

Obiettivo: rendere chiara la distinzione tra intenti update e report.

Checklist:

- [ ] Creare o aggiornare `intent_taxonomy.py`.
- [ ] Introdurre `REPORT_RESULT`.
- [ ] Mappare temporaneamente i vecchi intenti:
  - [ ] `REPORT_FINAL_RESULT → REPORT_RESULT + result_scope=FINAL`
  - [ ] `REPORT_PARTIAL_RESULT → REPORT_RESULT + result_scope=PARTIAL`
- [ ] Aggiungere test di compatibilità.

---

## Fase 2 — Class evidence

Obiettivo: degradare `RulesEngine.classify()` da decisore a produttore di evidenza.

Checklist:

- [ ] Implementare `ClassEvidence`.
- [ ] Implementare `MarkerMatch`.
- [ ] Implementare `detect_class_evidence`.
- [ ] Mantenere `classify()` per compatibilità.
- [ ] Aggiungere test per:
  - [ ] solo marker new signal
  - [ ] solo marker update
  - [ ] marker misti
  - [ ] pareggio
  - [ ] blacklist
  - [ ] info only

---

## Fase 3 — ClassificationResolver

Obiettivo: creare il componente che decide davvero.

Checklist:

- [ ] Creare `classification_resolver.py`.
- [ ] Implementare `ClassificationInput`.
- [ ] Implementare `ResolvedClassification`.
- [ ] Implementare priorità:
  - [ ] signal completo
  - [ ] update state-changing
  - [ ] report-only
  - [ ] setup incompleto
  - [ ] info
  - [ ] unclassified
- [ ] Aggiungere `reasons`.
- [ ] Aggiungere `warnings`.
- [ ] Aggiungere test unitari isolati.

---

## Fase 4 — Integrazione runtime

Obiettivo: collegare il resolver al runtime.

Checklist:

- [ ] Sostituire `rules.classify(text)` con `rules.detect_class_evidence(text)`.
- [ ] Costruire `ClassificationInput`.
- [ ] Chiamare `ClassificationResolver.resolve(...)`.
- [ ] Usare `resolved.primary_class`.
- [ ] Non introdurre `message_type` nel contratto finale.
- [ ] Salvare `resolved.reasons` in diagnostics.
- [ ] Salvare `class_evidence` in diagnostics.
- [ ] Rimuovere decisione `message_type == NEW_SIGNAL → SIGNAL`.

---

## Fase 5 — Pulizia Trader A markers/rules

Obiettivo: ridurre falsi positivi.

Checklist:

- [ ] Ripulire `classification_markers.new_signal`.
- [ ] Spostare marker generici in field markers.
- [ ] Aggiornare `intent_markers`.
- [ ] Aggiungere marker `REPORT_RESULT`.
- [ ] Aggiornare `rules.json` con regole strutturali.
- [ ] Aggiornare disambiguation rules.
- [ ] Aggiornare precedence rules.
- [ ] Testare messaggi reali Trader A.

---

## Fase 6 — Translator e canonical output

Obiettivo: produrre CanonicalMessage coerente.

Checklist:

- [ ] Aggiornare `IntentTranslator`.
- [ ] Mappare `REPORT_RESULT`.
- [ ] Eliminare output finale/parziale separato.
- [ ] Popolare `targeted_reports`.
- [ ] Popolare `targeted_actions`.
- [ ] Gestire caso misto update/report.
- [ ] Verificare `primary_class`.
- [ ] Verificare `message_type`.

---

## Fase 7 — Backward compatibility

Obiettivo: non rompere i consumatori esistenti.

Checklist:

- [ ] Mantenere alias legacy:
  - [ ] `REPORT_FINAL_RESULT`
  - [ ] `REPORT_PARTIAL_RESULT`
- [ ] Convertire internamente verso `REPORT_RESULT`.
- [ ] Aggiungere campo diagnostics:
  - [ ] `legacy_intent_mapped`
  - [ ] `legacy_intent_original`
- [ ] Aggiornare eventuali report parser.
- [ ] Aggiornare eventuali test snapshot.
- [ ] Verificare DB `parse_results`.
- [ ] Verificare `operational_signals`.

---

## 9. Test richiesti

## 9.1 Test unitari `RulesEngine`

### Casi

- [ ] Marker strong update.
- [ ] Marker weak info.
- [ ] Marker misti update/new_signal.
- [ ] Marker generico `вход`.
- [ ] Marker specifico `вход исполнен`.
- [ ] Nessun marker.
- [ ] Blacklist.

---

## 9.2 Test unitari `ClassificationResolver`

### Casi SIGNAL

- [ ] `symbol + side + entry + stop + tp` → `SIGNAL / PARSED`.
- [ ] `symbol + side + entry` senza stop/tp → `SIGNAL / PARTIAL`.
- [ ] solo `entry` → non `SIGNAL`.

### Casi UPDATE

- [ ] `MOVE_STOP_TO_BE` validato → `UPDATE`.
- [ ] `MOVE_STOP` con livello → `UPDATE`.
- [ ] `CLOSE_FULL` → `UPDATE`.
- [ ] `CANCEL_PENDING` → `UPDATE`.

### Casi REPORT

- [ ] `ENTRY_FILLED` → `REPORT`.
- [ ] `TP_HIT` → `REPORT`.
- [ ] `SL_HIT` → `REPORT`.
- [ ] `EXIT_BE` → `REPORT`.
- [ ] `REPORT_RESULT` → `REPORT`.

### Casi misti

- [ ] `TP1 hit, move SL to BE` → `UPDATE` con report incluso.
- [ ] `entry filled, move stop to BE` → `UPDATE` con report incluso.
- [ ] `close half + profit` → probabilmente `UPDATE`, report payload incluso.

### Casi fallback

- [ ] info marker senza intenti → `INFO`.
- [ ] testo rumoroso → `INFO` con `parse_status=UNCLASSIFIED`.

---

## 9.3 Test integrazione Trader A

Usare messaggi reali o fixture.

Checklist:

- [ ] Nuovo segnale completo.
- [ ] Segnale incompleto.
- [ ] Update stop a BE.
- [ ] Update stop con livello.
- [ ] Close full.
- [ ] Close partial.
- [ ] Cancel pending.
- [ ] Entry filled.
- [ ] TP hit.
- [ ] SL hit.
- [ ] Exit BE.
- [ ] Report profit/loss generico.
- [ ] Messaggio con reply multipli.
- [ ] Messaggio con link multipli.
- [ ] Messaggio misto update + report.
- [ ] Messaggio solo informativo.
- [ ] Messaggio ambiguo.

---

## 10. Esempi di comportamento atteso

## 10.1 Nuovo segnale

Input:

```text
BTCUSDT LONG
Entry: 65000
SL: 64000
TP1: 66000
TP2: 67000
```

Output atteso:

```json
{
  "primary_class": "SIGNAL",
  "parse_status": "PARSED",
  "intents": ["CREATE_SIGNAL"]
}
```

---

## 10.2 Entry filled

Input:

```text
вход исполнен
```

Output atteso:

```json
{
  "primary_class": "REPORT",
  "parse_status": "PARSED",
  "intents": ["ENTRY_FILLED"]
}
```

Non deve diventare `SIGNAL`.

---

## 10.3 Stop a BE

Input:

```text
стоп в бу
```

Output atteso:

```json
{
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "intents": ["MOVE_STOP_TO_BE"]
}
```

---

## 10.4 TP hit + stop a BE

Input:

```text
TP1 hit, stop to BE
```

Output atteso:

```json
{
  "primary_class": "UPDATE",
  "parse_status": "PARSED",
  "intents": ["TP_HIT", "MOVE_STOP_TO_BE"],
  "targeted_reports": [
    {"intent": "TP_HIT"}
  ],
  "targeted_actions": [
    {"intent": "MOVE_STOP_TO_BE"}
  ]
}
```

---

## 10.5 Risultato finale

Input:

```text
Сделка закрыта +120$
```

Output atteso:

```json
{
  "primary_class": "REPORT",
  "parse_status": "PARSED",
  "intents": ["REPORT_RESULT"],
  "targeted_reports": [
    {
      "intent": "REPORT_RESULT",
      "result_scope": "FINAL",
      "result_status": "PROFIT",
      "result_value": 120,
      "result_currency": "USD"
    }
  ]
}
```

---

## 11. Migrazione da vecchi intenti report

## 11.1 Mapping legacy

```text
REPORT_FINAL_RESULT   → REPORT_RESULT + result_scope=FINAL
REPORT_PARTIAL_RESULT → REPORT_RESULT + result_scope=PARTIAL
```

## 11.2 Regola

Il parser può ancora riconoscere i vecchi nomi in input/config per un periodo transitorio, ma il canonical output deve usare solo:

```text
REPORT_RESULT
```

## 11.3 Checklist

- [ ] Aggiungere mapping legacy.
- [ ] Aggiornare semantic markers.
- [ ] Aggiornare rules.
- [ ] Aggiornare translator.
- [ ] Aggiornare test.
- [ ] Aggiornare documentazione.
- [ ] Rimuovere vecchi nomi solo dopo stabilizzazione.

---

## 12. Criteri di accettazione

La modifica è accettabile solo se:

- [ ] `RulesEngine.classify()` non decide più il `primary_class` finale.
- [ ] Un messaggio con solo `entry/вход` non diventa automaticamente `SIGNAL`.
- [ ] Un nuovo segnale completo viene classificato `SIGNAL`.
- [ ] Un update operativo viene classificato `UPDATE`.
- [ ] Un report puro viene classificato `REPORT`.
- [ ] Un messaggio misto update/report mantiene entrambi gli output.
- [ ] `REPORT_FINAL_RESULT` e `REPORT_PARTIAL_RESULT` non compaiono più nel canonical output finale.
- [ ] `REPORT_RESULT` supporta `result_scope`.
- [ ] I vecchi intenti report sono gestiti come alias legacy.
- [ ] Le decisioni del resolver sono tracciate in diagnostics.
- [ ] I test Trader A passano su fixture reali.

---

## 13. Rischi

## 13.1 Rischio: rompere compatibilità downstream

Se moduli successivi si aspettano `REPORT_FINAL_RESULT` o `REPORT_PARTIAL_RESULT`, la semplificazione può rompere reporting/backtesting.

Mitigazione:

```text
alias legacy in input
canonical output nuovo
compat mapping per downstream
diagnostics per transizione
```

---

## 13.2 Rischio: extractor non abbastanza strutturato

Se `TraderAExtractors.extract()` non restituisce abbastanza campi per calcolare `signal_completeness`, il resolver non può decidere bene.

Mitigazione:

```text
aggiungere fields_present
aggiungere signal_completeness
aggiungere missing_fields
```

---

## 13.3 Rischio: troppi casi misti

Messaggi tipo:

```text
TP1 preso, stop in pari, chiudere metà
```

contengono report + update.

Mitigazione:

```text
primary_class = UPDATE se contiene almeno una azione state-changing
report resta in targeted_reports
```

---

## 13.4 Rischio: marker inutilizzabili

Se i marker restano troppo generici, continueranno a sporcare evidence.

Mitigazione:

```text
classification_markers più stretti
field_markers separati
intent_markers più specifici
```

---

## 14. Checklist finale compatta

## Codice

- [ ] `RulesEngine.detect_class_evidence()`
- [ ] `ClassEvidence`
- [ ] `MarkerMatch`
- [ ] `ClassificationResolver`
- [ ] `ClassificationInput`
- [ ] `ResolvedClassification`
- [ ] `intent_taxonomy.py`
- [ ] `REPORT_RESULT`
- [ ] legacy mapping report
- [ ] runtime integration
- [ ] translator integration
- [ ] diagnostics

## Config

- [ ] pulizia `classification_markers`
- [ ] aggiornamento `intent_markers`
- [ ] aggiornamento `rules.json`
- [ ] nuove regole strutturali
- [ ] nuove regole report result
- [ ] nuove regole conflitti update/report

## Test

- [ ] unit `RulesEngine`
- [ ] unit `ClassificationResolver`
- [ ] unit `REPORT_RESULT`
- [ ] integration Trader A
- [ ] regression vecchi esempi
- [ ] fixture messaggi reali
- [ ] test messaggi misti
- [ ] test reply/link multipli

## Documentazione

- [ ] aggiornare spec parser redesign
- [ ] aggiornare doc runtime flow
- [ ] documentare nuova tassonomia intenti
- [ ] documentare `REPORT_RESULT`
- [ ] documentare diagnostics
- [ ] documentare compat legacy

---

## 15. Prompt operativo per Codex

```text
Agisci come TDD Mentor & Developer.

Contesto:
Sto rifacendo la classificazione del nuovo parser in TRADING_BOT_TELEGRAM.
La classificazione finale non deve più dipendere direttamente da RulesEngine.classify().
RulesEngine deve produrre solo evidenze marker/class hints.
La decisione finale deve essere fatta da un nuovo ClassificationResolver usando:
- struttura estratta del segnale
- intenti validati
- targeting/storia
- marker evidence come supporto secondario

Obiettivi:
1. Aggiungi ClassEvidence e MarkerMatch in src/parser/rules_engine.py o modulo condiviso.
2. Implementa RulesEngine.detect_class_evidence(text).
3. Mantieni RulesEngine.classify(text) per compatibilità, ma non usarlo più per decidere primary_class.
4. Crea src/parser/shared/classification_resolver.py con:
   - ClassificationInput
   - ResolvedClassification
   - ClassificationResolver.resolve(...)
5. Integra il resolver in src/parser/shared/runtime.py.
6. Rimuovi la logica:
   message_type == "NEW_SIGNAL" -> primary_class "SIGNAL"
7. SIGNAL deve nascere solo da signal candidato/completo.
8. UPDATE deve nascere da intenti operativi validi.
9. REPORT deve nascere da intenti report validi.
10. Semplifica REPORT_FINAL_RESULT e REPORT_PARTIAL_RESULT in REPORT_RESULT.
11. Aggiungi mapping legacy:
    REPORT_FINAL_RESULT -> REPORT_RESULT + result_scope=FINAL
    REPORT_PARTIAL_RESULT -> REPORT_RESULT + result_scope=PARTIAL
12. Aggiorna tests con casi:
    - nuovo segnale completo
    - ingresso eseguito / вход исполнен
    - stop a BE
    - TP hit
    - TP hit + stop to BE
    - report risultato finale
    - testo ambiguo

Metodo:
- Prima scrivi/aggiorna i test.
- Poi implementa il minimo codice necessario.
- Mantieni compatibilità dove possibile.
- Alla fine aggiorna la documentazione del parser.
- Riporta file modificati, test eseguiti e casi ancora aperti.
```

# PRD 2.b — Parser Pipeline Integration in runtime_v2

**Data:** 2026-05-13
**Stato:** draft
**Deriva da:** `docs/Raggionamento/documento_madre_riprogettazione_trading_bot_telegram_v_0_1.md` v0.2, Fase C
**Precondizione:** PRD 2.a chiuso — parser_v2 verificato su dati reali, zero errori di schema
**Sblocca:** PRD 03 (Operation Rules Engine V2)

---

## 1. Scopo

Integrare `parser_v2` come parser ufficiale del runtime live. Questo PRD costruisce il modulo `src/runtime_v2/parser_pipeline/` che consuma il `ParserDispatchCandidate` prodotto da PRD 01 e produce un `CanonicalMessage` persistito in `canonical_messages`.

**Flusso target:**
```
ParserDispatchCandidate          ← output PRD 01
        ↓
ParserPipelineProcessor          ← nuovo in runtime_v2/parser_pipeline/
        ↓
UniversalParserRuntime.parse()   ← parser_v2 invariato
        ↓
CanonicalMessage                 ← schema_version = "canonical_message_v2"
        ↓
canonical_messages (DB)          ← nuova tabella
        ↓
CanonicalParseResult             ← output verso PRD 03
```

**Fuori scope:**
- Operation rules → PRD 03
- Modifica a `UniversalParserRuntime` o ai profili
- Listener, intake, trader resolution
- Lifecycle o execution
- Migrazione `MessageRouter` legacy — resta baseline separata

---

## 2. Struttura package

```
src/runtime_v2/
    parser_pipeline/
        __init__.py
        models.py        — CanonicalParseResult, ParserJobStatus
        processor.py     — ParserPipelineProcessor
    persistence/
        raw_messages.py          — esistente (PRD 01)
        processing_jobs.py       — esistente (PRD 01)
        canonical_messages.py    — nuovo
```

---

## 3. Contratti

### 3.1 Input — `ParserDispatchCandidate`

Definito in PRD 01, `src/runtime_v2/trader_resolution/models.py`:

```python
class ParserDispatchCandidate:
    raw_message: RawMessageEnvelope
    resolved_trader: ResolvedTraderContext
    parser_profile: str
    parser_context: ParserContext   # da src.parser_v2.contracts.context
```

### 3.2 Output — `CanonicalParseResult`

```python
class CanonicalParseResult:
    raw_message_id: int
    canonical_message_id: int       # PK della riga in canonical_messages
    parser_profile: str
    primary_class: MessageClass
    parse_status: ParseStatus
    canonical_message: CanonicalMessage
    warnings: list[str]
    parsed_at: datetime
```

Questo oggetto è il contratto di ingresso per PRD 03 (operation rules). Non contiene decisioni operative.

### 3.3 `ParserJobStatus`

```python
class ParserJobStatus:
    raw_message_id: int
    status: Literal["parsed", "failed", "skipped"]
    reason: str | None
    canonical_message_id: int | None
```

---

## 4. `ParserPipelineProcessor` — responsabilità

### Fa

- Riceve `ParserDispatchCandidate`
- Recupera il profilo da `parser_v2/profiles/registry.py` via `parser_profile`
- Estrae `raw_text` da `raw_message.raw_text`
- Chiama `UniversalParserRuntime.parse(text, context, profile)`
- Persiste `CanonicalMessage` in `canonical_messages` via `CanonicalMessageRepository`
- Restituisce `CanonicalParseResult`

**Nota:** `raw_messages.processing_status` NON viene toccato dal parser pipeline. PRD 01 lo imposta a `done` al termine dell'intake. Lo stato del parsing è tracciato esclusivamente in `canonical_messages` tramite `parsed_at` e `parse_status`.

### Non fa

- Non interpreta il contenuto del `CanonicalMessage`
- Non applica policy o regole operative
- Non scrive in `parser_results_v2` (DB del harness `parser_test`, non del live)
- Non importa `src.telegram.router`
- Non importa `src/parser/` legacy

### Gestione errori

| Caso | Comportamento |
|---|---|
| `raw_text` è None | `CanonicalMessage` con `parse_status=UNCLASSIFIED`, `primary_class=INFO`, persistito con warning `empty_text` |
| `UniversalParserRuntime` lancia eccezione non-Pydantic | riga `canonical_messages` NON creata, log, `ParserJobStatus(status="failed")` |
| Pydantic `ValidationError` nell'output del profilo | bug del profilo — riga NOT creata, log con stack trace completo, `ParserJobStatus(status="failed")` |
| Errore di persistenza `canonical_messages` | eccezione propagata, `ParserJobStatus(status="failed")` |

**Nota:** in tutti i casi di failure, `raw_messages.processing_status` resta `done` (set da PRD 01). Il failure del parser è tracciato solo tramite assenza di riga in `canonical_messages` e log — non modifica lo stato dell'intake.

**Regola chiave:** il parser non deve mai produrre un output che viola lo schema Pydantic. Un `ValidationError` in output è un bug del profilo, non un caso normale.

---

## 5. Persistenza — tabella `canonical_messages`

### 5.1 Principio

`canonical_messages` appartiene al **parser bounded context**. Non ha foreign key verso tabelle operative. È separabile fisicamente in `parser_db` in futuro senza riscrivere il dominio.

### 5.2 Schema SQL

```sql
CREATE TABLE canonical_messages (
    canonical_message_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_message_id        INTEGER NOT NULL,
    run_context           TEXT    NOT NULL DEFAULT 'live',
    parser_profile        TEXT    NOT NULL,
    schema_version        TEXT    NOT NULL,
    primary_class         TEXT    NOT NULL,
    parse_status          TEXT    NOT NULL,
    primary_intent        TEXT,
    confidence            REAL    NOT NULL,
    canonical_json        TEXT    NOT NULL,
    warnings_json         TEXT    NOT NULL DEFAULT '[]',
    diagnostics_json      TEXT    NOT NULL DEFAULT '{}',
    parsed_at             TEXT    NOT NULL,
    UNIQUE(raw_message_id, run_context)
)
```

**Indici:**
```sql
CREATE INDEX idx_canonical_messages_raw        ON canonical_messages(raw_message_id);
CREATE INDEX idx_canonical_messages_class      ON canonical_messages(primary_class, parse_status);
CREATE INDEX idx_canonical_messages_profile    ON canonical_messages(parser_profile);
CREATE INDEX idx_canonical_messages_parsed_at  ON canonical_messages(parsed_at);
```

### 5.3 Note di design

- `run_context` distingue `live` da eventuali re-parse futuri (`reparse_YYYYMMDD`). Garantisce idempotenza: un raw message non produce due righe `live`.
- `canonical_json` è il `CanonicalMessage.model_dump_json()` completo — source of truth per i layer downstream.
- `warnings_json` e `diagnostics_json` sono colonne separate per query di debug senza deserializzare il JSON completo.
- Nessuna FK verso `raw_messages` per separabilità fisica futura — solo `raw_message_id` come reference logica.

### 5.4 Migrazione

```
db/migrations/024_runtime_v2_canonical_messages.sql
```

Additiva. Non modifica tabelle esistenti.

### 5.5 `CanonicalMessageRepository`

```python
class CanonicalMessageRepository:
    def save(
        self,
        raw_message_id: int,
        canonical: CanonicalMessage,
        run_context: str = "live",
    ) -> int:
        # restituisce canonical_message_id
        ...

    def get_by_raw_message_id(
        self,
        raw_message_id: int,
        run_context: str = "live",
    ) -> CanonicalMessage | None:
        ...
```

---

## 6. Boundary con parser_test

`parser_test` usa `parser_results_v2` come store dei risultati replay. Il live usa `canonical_messages`. Sono tabelle separate con scopi diversi:

| | `parser_results_v2` | `canonical_messages` |
|---|---|---|
| Contesto | harness replay / test | runtime live |
| Popolato da | `replay_parser_v2.py` | `ParserPipelineProcessor` |
| Idempotenza | `UNIQUE(run_id, raw_message_id)` | `UNIQUE(raw_message_id, run_context)` |
| Comparabilità | ✅ stesso `UniversalParserRuntime` | ✅ stesso `UniversalParserRuntime` |

**Regola:** stesso `UniversalParserRuntime` e stessi profili per live e replay — il risultato deve essere confrontabile a parità di testo e contesto.

---

## 7. Acceptance criteria

PRD 2.b è done quando:

1. `ParserPipelineProcessor` riceve un `ParserDispatchCandidate` valido e produce un `CanonicalParseResult` con `CanonicalMessage` schema-valid.

2. Il `CanonicalMessage` viene persistito in `canonical_messages` con `run_context=live`. Un secondo ingest dello stesso `raw_message_id` non crea una seconda riga (idempotenza via `UNIQUE`).

3. Un messaggio non eseguibile (`INFO`, `PARTIAL`, `UNCLASSIFIED`) produce un `CanonicalMessage` schema-valid e viene persistito — non va in `failed`.

4. Un'eccezione interna del profilo produce `ParserJobStatus(status="failed")` con log, senza creare riga in `canonical_messages` e senza propagare crash all'intake. `raw_messages.processing_status` resta `done`.

5. Nessun modulo in `runtime_v2/parser_pipeline/` importa `src.telegram.router`, `src.parser` legacy, o `canonical_v1`.

6. Migration `024_runtime_v2_canonical_messages.sql` applicabile su DB vuoto e su DB con dati PRD 01 esistenti senza errori.

7. Test di accettazione che dimostrano la slice completa end-to-end:
```
RawIngestItem
      ↓
RuntimeV2IntakeProcessor        (PRD 01)
      ↓
ParserDispatchCandidate
      ↓
ParserPipelineProcessor         (PRD 2.b)
      ↓
CanonicalMessage in canonical_messages
```
su almeno: un SIGNAL completo, un UPDATE con target hints, un INFO, un messaggio con `parse_status=PARTIAL`.

**Segnale primario:** la slice end-to-end da raw message a `canonical_messages` funziona senza toccare il router legacy.

**Cosa sblocca:** PRD 03 (Operation Rules Engine V2) ha un `CanonicalParseResult` come contratto di ingresso.

---

## 8. File da creare / modificare

| File | Tipo |
|---|---|
| `src/runtime_v2/parser_pipeline/__init__.py` | Nuovo |
| `src/runtime_v2/parser_pipeline/models.py` | Nuovo |
| `src/runtime_v2/parser_pipeline/processor.py` | Nuovo |
| `src/runtime_v2/persistence/canonical_messages.py` | Nuovo |
| `db/migrations/024_runtime_v2_canonical_messages.sql` | Nuovo |
| `tests/runtime_v2/test_parser_pipeline_processor.py` | Nuovo |
| `tests/runtime_v2/test_canonical_message_repository.py` | Nuovo |
| `tests/runtime_v2/test_acceptance.py` | Esteso (slice end-to-end PRD 01 + 2.b) |
| `docs/runtime_v2/parser_pipeline/funzionalita.md` | Nuovo |

**Non toccati:** `src/parser_v2/` (invariato), `src/telegram/router.py`, `src/parser/` legacy.

---

## 9. Rischi

| Rischio | Mitigazione |
|---|---|
| `raw_text=None` su messaggi media-only che passano intake | Gestione esplicita nel processor → UNCLASSIFIED INFO, non crash |
| Re-parse di un messaggio già parsato | `UNIQUE(raw_message_id, run_context)` + `run_context` versioned per re-parse espliciti |
| Divergenza live/replay su stesso testo | Stesso `UniversalParserRuntime` garantisce parità — comparare con `parser_test` su stesso `raw_message_id` |
| Python 3.11 compat (no PEP 695) | Evitare `TypeVar` con sintassi 3.12 — regola già in AUDIT.md |

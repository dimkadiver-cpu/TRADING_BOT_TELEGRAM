# parser_pipeline — Funzionalità

## Responsabilità

Il package `parser_pipeline` consuma il `ParserDispatchCandidate` prodotto dall'intake (PRD 01), chiama `UniversalParserRuntime`, e persiste il `CanonicalMessage` risultante in `canonical_messages`.

Non interpreta il contenuto del messaggio. Non applica regole operative. Non modifica `raw_messages.processing_status`.

## Componenti

### `models.py`

#### `CanonicalParseResult`

Output di un parsing riuscito. Contratto di ingresso per PRD 03 (Operation Rules Engine V2).

```python
class CanonicalParseResult(BaseModel):
    raw_message_id: int
    canonical_message_id: int       # PK in canonical_messages
    parser_profile: str
    primary_class: MessageClass     # "SIGNAL" | "UPDATE" | "REPORT" | "INFO"
    parse_status: ParseStatus       # "PARSED" | "PARTIAL" | "UNCLASSIFIED"
    canonical_message: CanonicalMessage
    warnings: list[str]
    parsed_at: datetime
```

#### `ParserJobStatus`

Restituito quando il parsing fallisce (profilo sconosciuto, eccezione runtime, errore persistenza). Nessuna riga viene creata in `canonical_messages`.

```python
class ParserJobStatus(BaseModel):
    raw_message_id: int
    status: Literal["parsed", "failed", "skipped"]
    reason: str | None              # "unknown_parser_profile" | "parser_runtime_error" | "persistence_error"
    canonical_message_id: int | None
```

---

### `processor.py`

#### `ParserPipelineProcessor`

**Costruttore:** `ParserPipelineProcessor(*, canonical_repo: CanonicalMessageRepository, runtime: UniversalParserRuntime | None = None)`

Il `runtime` è opzionale — se omesso viene creato automaticamente (`UniversalParserRuntime()`). Utile per iniettare un mock nei test.

**Metodo principale:**

```python
def process(
    self,
    candidate: ParserDispatchCandidate,
    run_context: str = "live",
) -> CanonicalParseResult | ParserJobStatus:
```

**Flusso interno:**

```
candidate.parser_profile
      ↓
get_parser_v2_profile(profile)          → KeyError → ParserJobStatus(failed, unknown_parser_profile)
      ↓
UniversalParserRuntime.parse(text, ctx, profile)
                                        → Exception → ParserJobStatus(failed, parser_runtime_error)
      ↓
CanonicalMessageRepository.save(...)    → Exception → ParserJobStatus(failed, persistence_error)
      ↓
CanonicalParseResult
```

**Gestione `raw_text=None`:** sostituito con `""` prima della chiamata al runtime. Il parser produce un `CanonicalMessage` con `parse_status=UNCLASSIFIED` — non è un failure.

## Boundary con parser_test

| | `parser_results_v2` | `canonical_messages` |
|---|---|---|
| Contesto | harness replay / test | runtime live |
| Popolato da | `replay_parser_v2.py` | `ParserPipelineProcessor` |
| Idempotenza | `UNIQUE(run_id, raw_message_id)` | `UNIQUE(raw_message_id, run_context)` |
| Runtime usato | `UniversalParserRuntime` | `UniversalParserRuntime` |

Stesso `UniversalParserRuntime` e stessi profili — i risultati sono confrontabili a parità di testo e contesto.

## Struttura CanonicalMessage UPDATE (per PRD 03)

Quando `primary_class == "UPDATE"`, il `CanonicalMessage` contiene:

```python
target_action_groups: list[TargetActionGroup]
```

Ogni `TargetActionGroup` raggruppa:
- `targeting: TargetHints` — chi è il target (telegram_message_ids, reply_to_message_id, scope_hint, …)
- `secondary_targeting: TargetHints | None` — reply degradato (quando sono presenti link espliciti + reply)
- `actions: list[ActionItem]` — azioni da eseguire su quel target

Ogni `ActionItem` ha:
- `action_type: UpdateOperationType` — `"SET_STOP" | "CLOSE" | "CANCEL_PENDING" | "MODIFY_ENTRIES" | "MODIFY_TARGETS" | "INVALIDATE_SETUP"`
- payload tipizzato nel campo corrispondente (`set_stop`, `close`, ecc.)
- `source_intent`, `source_intent_id`, `confidence`, `raw_fragment`

Pattern di accesso per PRD 03:

```python
for group in result.canonical_message.target_action_groups:
    targets = resolver.resolve(group.targeting)      # → lista posizioni
    for position in targets:
        for action in group.actions:
            executor.try_execute(action, position)
```

## Cosa sblocca

`CanonicalParseResult` è il contratto di ingresso per PRD 03 (Operation Rules Engine V2).

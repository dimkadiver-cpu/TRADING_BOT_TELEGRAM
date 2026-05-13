# parser_pipeline — Istruzioni d'uso

## Prerequisiti

1. Migration 024 applicata (`canonical_messages` esiste nel DB)
2. `parser_v2` profili registrati in `src/parser_v2/profiles/registry.py` per il trader in uso

## Uso base

```python
from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
from src.runtime_v2.parser_pipeline.models import CanonicalParseResult, ParserJobStatus
from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

repo = CanonicalMessageRepository(db_path="db/live.db")
processor = ParserPipelineProcessor(canonical_repo=repo)

# candidate è un ParserDispatchCandidate prodotto dall'intake (PRD 01)
result = processor.process(candidate)

if isinstance(result, CanonicalParseResult):
    print(result.primary_class)       # "SIGNAL", "UPDATE", "REPORT", "INFO"
    print(result.parse_status)        # "PARSED", "PARTIAL", "UNCLASSIFIED"
    print(result.canonical_message_id)
    # → passa result a PRD 03 (Operation Rules)
elif isinstance(result, ParserJobStatus):
    print(result.status)   # "failed"
    print(result.reason)   # "unknown_parser_profile" | "parser_runtime_error" | "persistence_error"
    # → log e skip, raw_message.processing_status resta "done"
```

## Slice end-to-end (PRD 01 + PRD 2.b)

```python
from src.runtime_v2.intake.processor import RuntimeV2IntakeProcessor
from src.runtime_v2.parser_pipeline.processor import ParserPipelineProcessor
from src.runtime_v2.persistence.raw_messages import RawMessageRepository
from src.runtime_v2.persistence.canonical_messages import CanonicalMessageRepository

raw_repo = RawMessageRepository(db_path="db/live.db")
canonical_repo = CanonicalMessageRepository(db_path="db/live.db")

intake = RuntimeV2IntakeProcessor(repo=raw_repo, ...)
parser = ParserPipelineProcessor(canonical_repo=canonical_repo)

# Messaggio in arrivo dal listener
dispatch_candidate = intake.process(raw_ingest_item)
if dispatch_candidate is not None:
    result = parser.process(dispatch_candidate)
```

## Idempotenza

Se lo stesso `raw_message_id` viene processato due volte con lo stesso `run_context`, la seconda chiamata restituisce lo stesso `canonical_message_id` senza creare una seconda riga.

Per un re-parse esplicito usare un `run_context` diverso:

```python
result = processor.process(candidate, run_context="reparse_20260513")
```

## Test

```bash
pytest tests/runtime_v2/test_parser_pipeline_processor.py -v
pytest tests/runtime_v2/test_acceptance.py -v -k "prd2b"
```

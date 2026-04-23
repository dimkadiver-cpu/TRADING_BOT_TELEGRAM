# Parser - Contratti di Output

## 1. Contratto legacy: `TraderParseResult`

Formato dataclass (non Pydantic) usato dal flusso operativo corrente.

Campi principali:

- `message_type`
- `intents`
- `entities`
- `target_refs`
- `reported_results`
- `warnings`
- `confidence`
- campi semantici aggiuntivi: `primary_intent`, `actions_structured`, `target_scope`, `linking`, `diagnostics`.

Uso:

- validazione coerenza (`src/validation/coherence.py`);
- serializzazione nel JSON di `parse_results.parse_result_normalized_json`;
- input di phase4 per operation rules / resolver / runtime updates.

## 2. Contratto canonical v1: `CanonicalMessage`

Definito in `src/parser/canonical_v1/models.py` (Pydantic, `extra="forbid"`).

Top-level chiave:

- `primary_class`: `SIGNAL|UPDATE|REPORT|INFO`
- `parse_status`: `PARSED|PARTIAL|UNCLASSIFIED|ERROR`
- `confidence`
- `intents`, `primary_intent`
- `targeting`
- payload business: `signal`, `update`, `report`
- `warnings`, `diagnostics`, `raw_context`.

Regole forti modello:

- `SIGNAL` richiede `signal` e vieta `update/report`;
- `UPDATE` richiede `update`;
- `REPORT` richiede `report`;
- cardinalita entry validate (`ONE_SHOT`, `TWO_STEP`, `RANGE`, `LADDER`);
- operazioni update validate (`SET_STOP`, `CLOSE`, `CANCEL_PENDING`, `MODIFY_ENTRIES`, `MODIFY_TARGETS`).

## 3. Adapter chain legacy -> v1

Pipeline tecnica:

```text
TraderParseResult
  -> adapt_legacy_parse_result_to_event_envelope(...)
  -> TraderEventEnvelopeV1
  -> normalize(...)
  -> CanonicalMessage
```

Mappature rilevanti:

- intents update/report convertiti in operation/event canonici;
- target refs legacy convertiti in `Targeting.refs`;
- entities legacy convertite in `SignalPayload`, `UpdatePayload`, `ReportPayload`;
- warning adapter per casi orfani (es. intent con dati insufficienti).

## 4. Coesistenza contratti

Situazione attuale:

- il contratto legacy e quello decisionale nel runtime;
- il contratto v1 e persistito in shadow/native su `parse_results_v1`;
- la convergenza completa richiede migrazione dei consumer downstream da legacy a v1.

## 5. Vincoli e attenzione operativa

- cambiare shape `entities` legacy puo rompere validation/phase4 anche con test parser verdi;
- cambiare mapping adapter/normalizer puo alterare `parse_results_v1` senza impatto immediato su runtime;
- servono test su entrambi i livelli quando si cambia logica parser.


# Parser - Integrazione Router e Storage

## 1. Ingresso parser nel Router

In `MessageRouter._route_inner(...)`:

1. blacklist check;
2. risoluzione trader effettivo;
3. eligibility;
4. recupero reply text;
5. costruzione `ParserContext`;
6. `profile.parse_message(...)`.

Il parser non viene invocato se:

- trader non risolto (va in review queue);
- canale/topic inattivo;
- parser profilo non registrato (record `SKIPPED`).

## 2. Validazione coerenza

Subito dopo `parse_message`, il Router esegue:

- `validate(result)` da `src/validation/coherence.py`.

Output validazione:

- `VALID`
- `INFO_ONLY`
- `STRUCTURAL_ERROR`

Solo `VALID` puo attivare phase4 (se engine/store sono cablati).

## 3. Persistenza `parse_results` (legacy)

Costruzione record con `_build_parse_result_record(...)`:

- serializza `message_type`, `intents`, `entities`, `target_refs`, `actions_structured`, `warnings`, `confidence`;
- aggiunge payload di validazione (`validation_status/errors/warnings`);
- salva tutto in `parse_result_normalized_json`.

`is_executable` e vero solo per `NEW_SIGNAL` completo.

## 4. Persistenza `parse_results_v1` (canonical)

Due modalita:

- `_native_canonical_v1(...)`: se il profilo implementa `parse_canonical`;
- `_shadow_normalize(...)`: altrimenti usa normalizer legacy->v1.

Caratteristica chiave:

- non blocca il path legacy;
- in caso errore salva `normalizer_error` e continua.

## 5. Schema DB v1

Tabella `parse_results_v1` (migrazione `020_parse_results_v1.sql`):

- `raw_message_id` (unique);
- `trader_id`;
- `primary_class`;
- `parse_status`;
- `confidence`;
- `canonical_json`;
- `normalizer_error`;
- `created_at`.

## 6. Passaggio a layer downstream

Se validazione `VALID`:

- `OperationRulesEngine.apply(...)`;
- `TargetResolver.resolve(...)`;
- persistenza `signals` / `operational_signals`;
- per UPDATE eligible: update planner + update applier runtime.

Questo significa che eventuali regressioni parser possono propagarsi rapidamente fino a stato trade runtime.


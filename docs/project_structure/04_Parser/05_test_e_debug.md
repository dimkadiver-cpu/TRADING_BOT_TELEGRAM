# Parser - Test e Debug Operativo

## 1. Suite test principali

### Rules engine

- `src/parser/tests/test_rules_engine.py`
- copre: loading regole, merge shared vocab, classify, intent detection, blacklist, properties.

### Modelli canonical v1

- `tests/parser_canonical_v1/test_canonical_v1_schema.py`
- copre: casi positivi/negativi del contratto Pydantic v1.

### Normalizer legacy -> v1

- `tests/parser_canonical_v1/test_normalizer.py`
- copre mapping signal/update/report, targeting, warning e parse_status.

### Profili trader

- `src/parser/trader_profiles/<trader>/tests/*`
- copre: real cases, canonical output, regole per profilo, casi golden/smoke.

## 2. Tool replay e report parser

Directory: `parser_test/`

Script utili:

- `parser_test/scripts/replay_parser.py`
- `parser_test/scripts/watch_parser.py`
- `parser_test/scripts/generate_parser_reports.py`
- `parser_test/scripts/audit_canonical_v1.py`

Output tipici:

- CSV classificazione per trader;
- audit canonical v1 (`parser_test/reports/canonical_v1_audit/*`).

## 3. Strategia minima di validazione dopo modifiche parser

1. test unit mirati (modulo toccato);
2. test profilo trader coinvolto;
3. test normalizer/canonical se si tocca mapping o schema;
4. replay parser sul trader impattato;
5. se cambia output operativo: verificare anche validation/router.

## 4. Segnali di regressione frequenti

- `message_type` cambia classe attesa;
- intents mancanti o extra;
- shape `entities` incompatibile con coherence/operation rules;
- canonical v1 con `normalizer_error` crescente;
- UPDATE non targettato che diventa azionabile per errore.

## 5. Nota su robustezza ambientale

I test parser possono risultare verdi anche con drift su layer esterni.  
Per modifiche non banali, validare almeno un percorso integrato con Router.


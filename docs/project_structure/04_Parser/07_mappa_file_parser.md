# Parser - Mappa File e Responsabilita

## 1. Nucleo parser

- `src/parser/rules_engine.py`: motore marker-based di classificazione/intents.
- `src/parser/text_utils.py`: utility testo condivise.
- `src/parser/canonical_schema.py`: loader schema intent da CSV (source-of-truth esterna).
- `src/parser/intent_action_map.py`: mapping intents -> azioni runtime.

## 2. Contratti dati

- `src/parser/trader_profiles/base.py`: `ParserContext` e `TraderParseResult`.
- `src/parser/event_envelope_v1.py`: envelope intermedio parser-side.
- `src/parser/canonical_v1/models.py`: contratto CanonicalMessage v1.

## 3. Adapter e normalizzazione

- `src/parser/adapters/legacy_to_event_envelope_v1.py`: mapping legacy -> envelope.
- `src/parser/canonical_v1/normalizer.py`: envelope -> canonical v1.

## 4. Profili trader

- `src/parser/trader_profiles/registry.py`: lookup parser da trader id.
- `src/parser/trader_profiles/common_utils.py`: utility comuni profili.
- `src/parser/trader_profiles/trader_a/*`: parser dedicato TA (logica piu ampia).
- `src/parser/trader_profiles/trader_b/*`: parser base per TB e base class per TC/TD/T3.
- `src/parser/trader_profiles/trader_c/*`: specializzazioni TC.
- `src/parser/trader_profiles/trader_d/*`: specializzazioni TD.
- `src/parser/trader_profiles/trader_3/*`: specializzazioni T3.

## 5. Test parser

- `src/parser/tests/test_rules_engine.py`: unit test engine.
- `src/parser/models/tests/*`: test modelli legacy parser.
- `src/parser/canonical_v1/tests/test_legacy_event_envelope_adapter.py`: test adapter envelope.
- `src/parser/trader_profiles/*/tests/*`: test per trader.
- `tests/parser_canonical_v1/*`: suite canonical v1 cross-profile.

## 6. Integrazione esterna al package parser

- `src/telegram/router.py`: orchestration parser + storage + phase4.
- `src/validation/coherence.py`: gate di coerenza output parser.
- `src/storage/parse_results.py`: persistenza legacy.
- `src/storage/parse_results_v1.py`: persistenza canonical v1.
- `db/migrations/020_parse_results_v1.sql`: DDL storage v1.


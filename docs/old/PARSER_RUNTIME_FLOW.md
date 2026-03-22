# PARSER RUNTIME FLOW (ROUND 2)

## Entry point runtime
1. Telegram listener riceve il messaggio: `src/telegram/listener.py`.
2. Costruisce `ParserInput` e chiama `MinimalParserPipeline.parse(...)`.
3. Il risultato viene persistito in `parse_results` tramite `ParseResultStore`.

## Dispatcher mode
`src/parser/dispatcher.py` decide quale output usare:
- `regex_only`: usa parser regex.
- `llm_only`: tenta LLM; se non disponibile, fallback controllato a regex con metadata.
- `hybrid_auto`: regex prima, LLM solo se regex e debole.

`selection_reason` resta in `selection_metadata` (non in warning testuali).

## Trader A canonico unico
Nel runtime esiste un solo trader canonico per Trader A:
- canonical trader id: `trader_a`
- alias supportati: `TA`, `A`, `trader_a`
- punto unico di risoluzione alias: `src/parser/trader_profiles/registry.py` (`canonicalize_trader_code`)

Dopo la canonicalizzazione, il parser usa sempre lo stesso profilo:
- `src/parser/trader_profiles/trader_a/profile.py`

`ta_profile.py` non guida piu il runtime principale: resta solo come shim di compatibilita API legacy.

### Refinement semantico condiviso
In `pipeline` esiste una fase comune post-normalizzazione:
- `NEW_SIGNAL` ha sempre intent esplicito `NS_CREATE_SIGNAL`.
- per `NEW_SIGNAL`, `entities` include almeno campi core derivati dal normalized quando mancanti:
  - `symbol`, `side`, `entry`, `stop_loss`, `take_profits`
  - `averaging` solo se diverso da `entry_main`.
- `NS_CREATE_SIGNAL` viene rimosso fuori da `NEW_SIGNAL`.

### Entity Policy per profilo
La normalizzazione condivisa legge `entity_policy` da `src/parser/trader_profiles/<trader>/parsing_rules.json`.

Campi supportati:
- `market_type`: `SPOT` | `PERPETUAL` | `DECLARED`
- `entry_order_type`: `MARKET` | `LIMIT` | `DECLARED`

Policy runtime:
- `DECLARED` usa il valore estratto dal profilo se presente.
- Se il profilo non dichiara il valore, resta attivo il fallback gia esistente del parser.
- La policy viene applicata in `src/parser/normalization.py`, quindi vale in modo uniforme per tutti i profili attivi.

## Policy runtime su status/warning/completeness/linkage
- `parse_status` persistito = `normalized.status` reale.
- warning finali = merge ordinato unico tra warning upstream e warning di validazione.
- `completeness` e semantico:
  - `INCOMPLETE` per `SETUP_INCOMPLETE` e `UNCLASSIFIED`
  - `COMPLETE` per gli altri message_type.
- `linkage_status` conserva il fatto oggettivo (`LINKED`/`UNLINKED`) anche su `INFO_ONLY`.

## Policy intent/action adottata
- `NS_*`: intent di creazione setup (attualmente `NS_CREATE_SIGNAL`).
- `U_*`: intent di update operativo o report.
- `ACT_*`: azioni operative derivate da intent (mapping in `src/parser/intent_action_map.py`).

Nota: `NS_CREATE_SIGNAL` e semantico, non e una action operativa.

## Piano Entrata Canonico (Trader A)
Per `NEW_SIGNAL` il parser espone un piano operativo canonico:
- `entries` (source of truth operativo) con item:
  - `sequence`, `role` (`PRIMARY`/`AVERAGING`), `order_type` (`MARKET`/`LIMIT`/`UNKNOWN`)
  - `price`, `raw_label`, `source_style`, `is_optional`
- campi sintetici:
  - `entry_plan_type`: `SINGLE_MARKET` | `SINGLE_LIMIT` | `MARKET_WITH_LIMIT_AVERAGING` | `LIMIT_WITH_LIMIT_AVERAGING` | `UNKNOWN`
  - `entry_structure`: `SINGLE` | `TWO_STEP` | `UNKNOWN`
  - `has_averaging_plan`: boolean

Mapping operativo:
- `A/B` e `Entry + Averaging` convergono nello stesso modello:
  - entry 1 = `PRIMARY`
  - entry 2 = `AVERAGING` (sempre `LIMIT`)
- la forma sorgente resta tracciata in `raw_label` e `source_style`.
- policy prudente: se la prima entry non dichiara esplicitamente market/limit, viene trattata come `LIMIT` per Trader A.

## Moduli attivi vs legacy/non integrati
Attivi nel flow runtime principale:
- `src/parser/pipeline.py`
- `src/parser/dispatcher.py`
- `src/parser/normalization.py`
- `src/parser/trader_profiles/trader_a/profile.py`
- `src/storage/parse_results.py`

Presenti nel repository ma non nel flow runtime principale:
- `src/parser/entity_extractor.py`
- `src/parser/intent_classifier.py`
- `src/parser/rules_engine.py`
- `src/parser/prefix_normalizer.py`
- `src/parser/scoring.py` / `src/parser/models.py` (catena legacy separata)

In questo round non e stato fatto cleanup distruttivo dei moduli legacy.


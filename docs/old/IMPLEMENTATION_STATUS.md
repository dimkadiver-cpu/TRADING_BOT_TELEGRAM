# IMPLEMENTATION STATUS

## Scopo

Questo file distingue in modo rapido tra comportamento gia implementato e design target.

## Stato corrente (2026-03-14)

### Implementato nel runtime attuale

- listener Telegram live con Telethon
- persistenza `raw_messages`
- risoluzione trader effettivo
- eligibility con strong link detection
- parser minimo `regex_only` / `llm_only` / `hybrid_auto`
- normalizzazione su `parse_result_normalized_json` con semantica primaria:
  - `message_type`
  - `intents`
  - `actions`
- persistenza `parse_results`
- mapping canonico `intent -> action`
- profilo trader-specific canonico per Trader A (`trader_a`) con alias `TA`/`A`
- planner update minimale (`build_update_plan`)
- applier update minimale (`apply_update_plan`)
- test parser dedicati:
  - `parser_test/tests`
  - `src/parser/trader_profiles/trader_a/tests`
  - `src/execution/test_update_*.py`

### Presente nel repository ma non attivo end-to-end

- `src/parser/intent_classifier.py` (utility legacy/ausiliarie, non asse principale)
- `src/parser/models.py` (strutture legacy ancora presenti)
- tabelle legacy `signals`, `events`, `warnings`, `trades`

Questi elementi esistono nel codice o nelle migration ma non sono il flusso operativo principale del listener corrente.

### Target design non ancora implementato

- state machine operativa
- risk gate / sizing
- execution planner runtime completo (orchestrazione end-to-end)
- bot comandi Telegram operativi
- adapter exchange Bybit
- reconciliation runtime
- tabelle `update_matches`, `trade_state_events`, `resolution_logs`

## Regola pratica

Se un documento descrive planner, lifecycle, risk, bot o exchange, va letto come specifica target salvo indicazione contraria nel codice.

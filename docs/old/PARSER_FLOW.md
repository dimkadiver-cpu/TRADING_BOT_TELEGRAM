# PARSER FLOW

## Scope
The parser converts Telegram raw messages into a common normalized contract and persists it in `parse_results.parse_result_normalized_json`.

Legacy fields remain stored for backward compatibility.

## Parser mode vs parser used
- `parser_mode`: configured system mode (`regex_only`, `llm_only`, `hybrid_auto`)
- `parser_used`: parser that produced the final selected output (`regex` or `llm`)

## LLM provider
- `llm_provider`: backend vendor used by LLM adapter (`openai` or `gemini`)
- `llm_provider` is independent from `parser_mode`
- both providers produce the same normalized parser contract

## Dispatcher behavior
The pipeline delegates parser selection to a dispatcher.

1. `regex_only`
- execute regex parser only
- output has `parser_used=regex`

2. `llm_only`
- execute LLM adapter only
- output has `parser_used=llm`
- if LLM is not configured, failure is explicit and handled by pipeline warning/fallback policy

3. `hybrid_auto`
- run regex first
- evaluate quality with conservative fallback rules
- if regex is weak, attempt LLM
- if LLM is unavailable/fails, fallback to regex
- `selection_metadata` stores reason and fallback info

## LLM backend config
Global env config:
- `LLM_ENABLED=true|false`
- `LLM_PROVIDER=openai|gemini`
- `LLM_MODEL=<model_name>`
- `LLM_TIMEOUT_MS=<milliseconds>`

OpenAI credentials:
- `OPENAI_API_KEY=<secret>`
- optional `OPENAI_BASE_URL=<url>`

Gemini credentials:
- `GEMINI_API_KEY=<secret>`
- optional `GEMINI_BASE_URL=<url>`

Suggested low-latency Gemini model:
- `gemini-2.5-flash`

Trader-specific overrides in `src/parser/trader_profiles/<trader>/parsing_rules.json`:
- `parser_mode`
- `llm_provider`
- `llm_model`
- `entity_policy.market_type`
- `entity_policy.entry_order_type`

`entity_policy` is applied in the shared normalization layer:
- `market_type`: `SPOT` | `PERPETUAL` | `DECLARED`
- `entry_order_type`: `MARKET` | `LIMIT` | `DECLARED`
- `DECLARED` means: use the profile-declared entity when present, otherwise keep the current fallback behaviour.

Example:
```json
{
  "parser_mode": "hybrid_auto",
  "llm_provider": "gemini",
  "llm_model": "gemini-2.5-flash",
  "entity_policy": {
    "market_type": "DECLARED",
    "entry_order_type": "DECLARED"
  }
}
```

## LLM output contract
The adapter requires JSON-only output (no free text) aligned to the normalized contract.
If provider-native strict schema is unavailable, local tolerant parsing/coercion and validator warnings are applied.
If still unusable, a controlled error is raised for dispatcher fallback.

## Shared contract
Regex and LLM outputs must converge to the same normalized fields (message type, symbol, direction, entries, SL/TP, actions, refs, results, notes, entities, warnings).

## Validation
Validation is non-blocking:
- adds `validation_warnings`
- does not stop persistence

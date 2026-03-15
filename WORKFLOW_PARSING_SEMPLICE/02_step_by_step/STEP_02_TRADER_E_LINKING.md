# Step 2 - Trader, Eligibility e Linking

## Cosa succede

Dopo il salvataggio raw, il sistema decide:

1. quale trader ha scritto il messaggio
2. se il messaggio è adatto al parsing operativo
3. se esiste un link forte a un messaggio precedente (reply/link Telegram)

## File principali

- `src/telegram/effective_trader.py`
- `src/telegram/trader_mapping.py`
- `src/telegram/eligibility.py`
- `config/trader_aliases.json`
- `config/telegram_source_map.json`

## Risultato di questo step

Il parser riceve metadati chiari:

- `resolved_trader_id`
- `eligibility_status`
- `linkage_method`
- `linkage_reference_id` (se presente)

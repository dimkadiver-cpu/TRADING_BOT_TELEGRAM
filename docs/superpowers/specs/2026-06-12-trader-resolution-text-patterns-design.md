# Trader Resolution Text Patterns Design

## Goal

Rimuovere l'hardcode del topic da `src/telegram/pattern_extractors.py` e rendere la risoluzione trader via pattern testuali configurabile per topic multi-trader.

## Contract

In `config/channels.yaml`, un topic multi-trader puo' dichiarare:

```yaml
resolution:
  mode: default
  pattern_group: multi_strategy_ru
  max_depth: 5
  aliases: {}
```

Semantica:

- `mode: default` usa il flusso standard `aliases -> text_patterns -> reply_chain -> links`
- `mode: patterns_only` usa solo `text_patterns`
- `pattern_group` e' opzionale in generale, ma obbligatorio quando `mode: patterns_only`
- `max_depth` continua a controllare solo la reply chain

## Pattern Catalog

I pattern vivono in `config/text_patterns.yaml`:

```yaml
groups:
  multi_strategy_ru:
    patterns:
      - trader_id: rsi_intraday
        all_of: ["RSI(2) Коннора", "интрадей"]
      - trader_id: rsi_swing
        all_of: ["RSI(2) Коннора", "свинг"]
      - trader_id: sma_intraday
        all_of: ["Кросс SMA 21/55", "интрадей"]
```

Regole:

- match su testo normalizzato leggero
- se piu' pattern dello stesso gruppo matchano insieme -> ambiguo
- se `pattern_group` e' referenziato ma il gruppo non esiste -> fail-fast in startup/config load

## Implementation Notes

- `ChannelConfigResolver` espone `resolution_mode` e `pattern_group`
- `TraderResolver` usa il gruppo pattern configurato invece di `topic_id == 4180`
- `pattern_extractors.py` smette di contenere la matrice topic->if hardcoded e diventa loader/matcher di `text_patterns.yaml`
- `startup_check.validator` valida i riferimenti a `pattern_group`

## Acceptance

- Un topic multi-trader puo' risolvere `sma_intraday` senza hardcode sul topic
- Il flusso `default` resta invariato per alias, reply-chain e links
- Un gruppo pattern mancante blocca la config
- I test coprono match positivo, ambiguo e fallback


### Preparazione DB di backtest (da fare dopo Batch 1)

```bash
# 1. Scarica messaggi storici da Telegram
python parser_test/scripts/import_history.py \
  --db-path db/backtest.sqlite3 \
  --chat-id <CHAT_ID> \
  --from-date 2025-06-01 --to-date 2026-03-01

# 2. Parsa i messaggi
python parser_test/scripts/replay_parser.py \
  --db-path db/backtest.sqlite3

# 3. Applica operation rules (Step 18b)
python parser_test/scripts/replay_operation_rules.py \
  --db-path db/backtest.sqlite3 --rules-dir config
```

### Step 18a — OHLCV (da fare prima del test end-to-end reale)

```bash
cd freqtrade
freqtrade download-data --config user_data/config.json \
  --pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT \
  --timeframe 5m --days 365 --exchange bybit --trading-mode futures
```

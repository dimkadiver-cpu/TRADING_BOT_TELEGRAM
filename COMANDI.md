# Comandi principali — TeleSignalBot

## Avvio

```powershell
# Prima volta (applica migrazioni DB)
python main.py --migrate; python main.py

# Avvio normale
python main.py
```

## Monitoraggio

```powershell
# Log in tempo reale
Get-Content logs\bot.log -Wait -Tail 50

# Stato DB (messaggi per status e tipo)
python check_db.py

# Esporta CSV con i risultati del parsing
python export_live_reports.py

# Audit rapido sync bot <-> freqtrade sui trade aperti/pending
.\.venv\Scripts\python.exe scripts\audit_live_sync.py

# Audit rapido su un solo simbolo
.\.venv\Scripts\python.exe scripts\audit_live_sync.py --symbol BTCUSDT

# Ispeziona un singolo attempt_key tra bot DB e freqtrade DB
.\.venv\Scripts\python.exe scripts\inspect_attempt.py --attempt-key T_xxx

# Ispeziona l'ultimo trade registrato
.\.venv\Scripts\python.exe scripts\inspect_attempt.py --latest-trade

# Stato TP per tutti i trade OPEN
.\.venv\Scripts\python.exe scripts\tp_status.py
```

I CSV vengono salvati in `parser_test/reports/`.

## Test

```powershell
# Smoke suite — verifica rapida (216 test, ~6s)
.venv/Scripts/python.exe -m pytest src/parser/models/tests/ src/parser/tests/ src/telegram/tests/ src/validation/tests/ -q

# Full suite — tutti i profili trader
.venv/Scripts/python.exe -m pytest src/parser/trader_profiles/ parser_test/tests/ -q
```

## Configurazione

| File | Cosa configura |
|---|---|
| `config/channels.yaml` | Canali Telegram, blacklist, finestra recovery |
| `config/telegram_source_map.json` | Mapping chat multi-trader |
| `.env` | API keys Telegram, path DB |
| `freqtrade/user_data/config.json` | Bridge Freqtrade, incluso `execution.market_dispatch_interval_s` |

Il bot rilegge `channels.yaml` automaticamente senza restart (hot reload).

## Reset DB

```powershell
# Backup + reset
copy db\tele_signal_bot.sqlite3 db\backup_YYYYMMDD.sqlite3
del db\tele_signal_bot.sqlite3
python main.py --migrate; python main.py
```

## Canali attivi

| Canale | chat_id | Tipo | Trader |
|---|---|---|---|
| PifSignal | -1003171748254 | multi-trader | trader_a, trader_b, trader_c, trader_d, trader_3 |

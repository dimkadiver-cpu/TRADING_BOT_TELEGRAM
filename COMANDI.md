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

# COMANDI

Comandi operativi rapidi per il setup attuale del progetto.

## Convenzioni

- Repo root: `C:\TeleSignalBot`
- Venv bot: `C:\TeleSignalBot\.venv`
- Venv freqtrade: `C:\TeleSignalBot\.venv-freqtrade`
- Config freqtrade locale: `C:\TeleSignalBot\freqtrade\user_data\config.json`
- Pairlist dinamica: `C:\TeleSignalBot\freqtrade\user_data\dynamic_pairs.json`
- DB bot: `C:\TeleSignalBot\.local\tele_signalbot.sqlite3`

## Avvio listener Telegram

Da `C:\TeleSignalBot`:

```powershell
.\.venv\Scripts\Activate.ps1
python .\main.py
```

## Avvio freqtrade in dry-run

Da `C:\TeleSignalBot\freqtrade`:

```powershell
$env:PYTHONPATH = "C:\TeleSignalBot"
C:\TeleSignalBot\.venv-freqtrade\Scripts\freqtrade.exe show-config -c .\user_data\config.json
C:\TeleSignalBot\.venv-freqtrade\Scripts\freqtrade.exe trade -c .\user_data\config.json --strategy SignalBridgeStrategy --dry-run
```

Se sei fuori cartella `freqtrade`, usa il path assoluto:

```powershell
$env:PYTHONPATH = "C:\TeleSignalBot"
C:\TeleSignalBot\.venv-freqtrade\Scripts\freqtrade.exe trade -c C:\TeleSignalBot\freqtrade\user_data\config.json --strategy SignalBridgeStrategy --dry-run
```

## Comando unico stack

Da `C:\TeleSignalBot`:

```powershell
.\scripts\start_phase5_stack.ps1 -MonitorSeconds 300
```

Cosa fa:

- valida import `src` nel venv `freqtrade`
- esegue `show-config`
- avvia listener e `freqtrade` in background
- controlla che i due processi restino vivi
- prova FreqUI su `http://127.0.0.1:8080`
- monitora il DB e `dynamic_pairs.json`
- segnala se il flusso arriva fino a `signals` / `operational_signals` / `trades`

File creati/aggiornati dal launcher:

- log listener: `C:\TeleSignalBot\.local\runtime\listener_runtime.log`
- log freqtrade: `C:\TeleSignalBot\.local\runtime\freqtrade_runtime.log`
- pid listener: `C:\TeleSignalBot\.local\runtime\listener.pid`
- pid freqtrade: `C:\TeleSignalBot\.local\runtime\freqtrade.pid`

Varianti utili:

```powershell
.\scripts\start_phase5_stack.ps1 -NoMonitor
.\scripts\start_phase5_stack.ps1 -MonitorSeconds 600
```

## FreqUI

URL:

- `http://127.0.0.1:8080`
- `http://127.0.0.1:8080/docs`

Nota:

- evitare la pagina Pairlist se compaiono errori lato API su `pairlists`
- il bridge ordini resta comunque operativo

## Bot Telegram freqtrade

Stato attuale del config locale:

- in `C:\TeleSignalBot\freqtrade\user_data\config.json` il blocco `telegram` esiste
- se `telegram.enabled` resta `false`, il bot Telegram di `freqtrade` non parte

Campi minimi da configurare:

```json
"telegram": {
  "enabled": true,
  "token": "YOUR_BOT_TOKEN",
  "chat_id": "YOUR_CHAT_ID",
  "allow_custom_messages": true
}
```

Controllo rapido del blocco `telegram`:

```powershell
Get-Content C:\TeleSignalBot\freqtrade\user_data\config.json | Select-String "telegram|enabled|token|chat_id|allow_custom_messages" -Context 0,2
```

Comandi principali del bot Telegram `freqtrade`:

- `/status`
- `/status table`
- `/profit`
- `/balance`
- `/stopbuy`
- `/reload_config`
- `/logs`
- `/forceexit <trade-id>`
- `/forcelong <pair>` e `/forceshort <pair>` solo se `force_entry_enable` e attivo

Note:

- il bot Telegram di `freqtrade` serve per controllo operativo, non per ricevere i segnali del bridge
- il bridge `listener -> DB -> freqtrade` non dipende da questo bot
- in gruppi Telegram conviene limitare l'accesso con `authorized_users`

## Controllo pairlist dinamica

```powershell
Get-Content C:\TeleSignalBot\freqtrade\user_data\dynamic_pairs.json
```

## Test principali

Da `C:\TeleSignalBot`:

```powershell
.\.venv\Scripts\python.exe -m pytest src\execution\tests -q
.\.venv\Scripts\python.exe -m pytest src\telegram\tests\test_router_phase4.py -q
.\.venv\Scripts\python.exe -m pytest src\operation_rules\tests\test_engine.py -q
.\.venv\Scripts\python.exe -m pytest src\telegram\tests\test_router_integration.py -q
.\.venv\Scripts\python.exe -m pytest src\execution\tests\test_dynamic_pairlist.py -q
```

## Query DB rapide

Ultimi raw messages:

```powershell
@'
import sqlite3
conn = sqlite3.connect(r"C:\TeleSignalBot\.local\tele_signalbot.sqlite3")
for row in conn.execute("select raw_message_id, source_chat_id, telegram_message_id, processing_status from raw_messages order by raw_message_id desc limit 20"):
    print(row)
'@ | C:\TeleSignalBot\.venv\Scripts\python.exe -
```

Ultimi segnali:

```powershell
@'
import sqlite3
conn = sqlite3.connect(r"C:\TeleSignalBot\.local\tele_signalbot.sqlite3")
for row in conn.execute("select signal_id, attempt_key, trader_id, symbol, side, status, created_at from signals order by signal_id desc limit 20"):
    print(row)
'@ | C:\TeleSignalBot\.venv\Scripts\python.exe -
```

Ultimi operational signals:

```powershell
@'
import sqlite3
conn = sqlite3.connect(r"C:\TeleSignalBot\.local\tele_signalbot.sqlite3")
for row in conn.execute("select op_signal_id, attempt_key, trader_id, message_type, is_blocked, block_reason, target_eligibility from operational_signals order by op_signal_id desc limit 20"):
    print(row)
'@ | C:\TeleSignalBot\.venv\Scripts\python.exe -
```

Trade, ordini, eventi:

```powershell
@'
import sqlite3
conn = sqlite3.connect(r"C:\TeleSignalBot\.local\tele_signalbot.sqlite3")
print("TRADES")
for row in conn.execute("select trade_id, attempt_key, symbol, state, close_reason, opened_at, closed_at from trades order by trade_id desc limit 20"):
    print(row)
print("ORDERS")
for row in conn.execute("select order_pk, attempt_key, purpose, status, qty, price, trigger_price from orders order by order_pk desc limit 20"):
    print(row)
print("EVENTS")
for row in conn.execute("select event_id, attempt_key, event_type, created_at from events order by event_id desc limit 30"):
    print(row)
'@ | C:\TeleSignalBot\.venv\Scripts\python.exe -
```

Inspector end-to-end di un caso singolo:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\inspect_attempt.py --latest-signal
```

Oppure:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\inspect_attempt.py --latest-trade
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\inspect_attempt.py --symbol BTCUSDT
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\inspect_attempt.py --attempt-key <attempt_key>
```

Checklist atteso vs osservato:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\verify_attempt_expectation.py --latest-signal --expect entry_filled
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\verify_attempt_expectation.py --latest-signal --expect move_stop
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\verify_attempt_expectation.py --latest-signal --expect tp1
```

Suite automatica injection + verify:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\run_dryrun_suite.py --scenario-dir C:\TeleSignalBot\scripts\trader_a_scenarios --reset
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\run_dryrun_suite.py --scenario-dir C:\TeleSignalBot\scripts\trader_a_scenarios --files u01_move_stop_to_be.json u06_close_partial_50.json --reset
```

## Config locale

Aprire il config freqtrade:

```powershell
notepad C:\TeleSignalBot\freqtrade\user_data\config.json
```

Controllare solo i campi `api_server`:

```powershell
Get-Content C:\TeleSignalBot\freqtrade\user_data\config.json | Select-String "api_server|jwt_secret_key|ws_token|username|password|fiat_display_currency" -Context 0,2
```

## Problemi comuni

Errore config non trovata:

```text
Config file \"user_data\\config.json\" not found
```

Fix:

- entrare in `C:\TeleSignalBot\freqtrade`
- oppure usare il path assoluto del config

Errore CoinGecko `429`:

- dipende da `fiat_display_currency`
- non blocca il bridge
- per evitarlo, impostare `fiat_display_currency` a stringa vuota nel config

Errore `number_assets not specified` su `RemotePairList`:

- il `RemotePairList` del `freqtrade` locale richiede `number_assets`
- aggiungere il campo nel blocco `pairlists` del config

Esempio:

```json
{
  "method": "RemotePairList",
  "mode": "whitelist",
  "processing_mode": "append",
  "pairlist_url": "file:///user_data/dynamic_pairs.json",
  "number_assets": 200,
  "refresh_period": 10,
  "keep_pairlist_on_failure": true
}
```

Warning JWT troppo corta:

- usare `jwt_secret_key` e `ws_token` locali di almeno 32 caratteri

## Stop processi

Se devi fermare i processi, usa `Ctrl+C` nei terminali dove girano listener e freqtrade.

# Injection README

Guida rapida per iniettare messaggi Telegram finti nel DB del bot e testarli in locale senza aspettare messaggi reali.

## Obiettivo

Lo script [inject_fake_messages.py](C:/TeleSignalBot/scripts/inject_fake_messages.py) inserisce righe in `raw_messages` e poi usa il `MessageRouter` reale del progetto.

Quindi il flusso testato e' questo:

1. `raw_messages`
2. `parse_results`
3. `operational_signals`
4. `signals`
5. bridge Freqtrade in `dry-run`

Questo permette di verificare il comportamento del bot quasi come se il messaggio arrivasse davvero da Telegram.

## File utili

- Script: [inject_fake_messages.py](C:/TeleSignalBot/scripts/inject_fake_messages.py)
- Inspector caso runtime: [inspect_attempt.py](C:/TeleSignalBot/scripts/inspect_attempt.py)
- Template scenari: [injection_scenarios.template.json](C:/TeleSignalBot/scripts/injection_scenarios.template.json)
- Template bridge/freqtrade: [injection_bridge_cases.template.json](C:/TeleSignalBot/scripts/injection_bridge_cases.template.json)
- DB bot live: [tele_signal_bot.sqlite3](C:/TeleSignalBot/db/tele_signal_bot.sqlite3)
- DB dry-run Freqtrade: [tradesv3.dryrun.sqlite](C:/TeleSignalBot/freqtrade/tradesv3.dryrun.sqlite)

## Requisiti

Usa il Python del venv del progetto:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe
```

Non usare il `python` di sistema se mancano dipendenze come `pydantic`.

## Uso base

### 1. Iniettare un solo messaggio

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\inject_fake_messages.py `
  --chat-id -100111 `
  --trader trader_a `
  --text "BTCUSDT long entry 100 sl 90 tp1 110"
```

Lo script stampa un JSON con:

- stato del `raw_message`
- `parse_result`
- eventuale `signal`
- eventuale `operational_signal`
- stato `injection` con `duplicate_raw_message=true` se hai rilanciato lo stesso `chat_id + telegram_message_id`

## Uso con scenario file

### 2. Duplicare il template

Copia [injection_scenarios.template.json](C:/TeleSignalBot/scripts/injection_scenarios.template.json) e sostituisci i placeholder:

- `SYMBOL_PLACEHOLDER`
- `ENTRY_1`
- `ENTRY_2`
- `STOP_LOSS`
- `TP_1`
- `TP_2`
- `NEW_STOP`

I valori sono volutamente liberi, cosi' puoi adattarli al chart reale che stai guardando.

### 3. Eseguire lo scenario

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\inject_fake_messages.py `
  --chat-id -100111 `
  --trader trader_a `
  --scenario-file C:\TeleSignalBot\scripts\injection_scenarios.template.json
```

## Campi supportati nel file scenario

Ogni item JSON puo' contenere:

- `name`: etichetta umana del caso
- `chat_id`: chat sintetica
- `trader` o `trader_id`: trader da forzare
- `telegram_message_id`: id Telegram sintetico
- `reply_to_message_id`: collega un UPDATE al messaggio precedente
- `text` oppure `raw_text`: testo del messaggio
- `message_ts`: timestamp opzionale ISO
- `source_chat_title`: nome fittizio del canale
- `source_trader_id`: override opzionale
- `acquisition_mode`: default `injected`

## Come simulare un UPDATE

Per simulare un update devi usare `reply_to_message_id` verso il `telegram_message_id` del segnale iniziale.

Esempio:

```json
[
  {
    "telegram_message_id": 1001,
    "chat_id": "-100111",
    "trader": "trader_a",
    "text": "BTCUSDT long entry 100 sl 90 tp1 110"
  },
  {
    "telegram_message_id": 1002,
    "reply_to_message_id": 1001,
    "chat_id": "-100111",
    "trader": "trader_a",
    "text": "tp1 hit"
  }
]
```

## Test isolato vs test live sul bot

### Test isolato su DB separato

Consigliato per provare parser e routing senza sporcare il DB runtime:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\inject_fake_messages.py `
  --db-path C:\TeleSignalBot\.test_tmp\inject_demo.sqlite3 `
  --chat-id -100111 `
  --trader trader_a `
  --text "BTCUSDT long entry 100 sl 90 tp1 110" `
  --no-dynamic-pairlist
```

### Test sul DB reale del bot

Se vuoi che Freqtrade `dry-run` lo veda davvero, devi scrivere nel DB bridge reale:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\inject_fake_messages.py `
  --db-path C:\TeleSignalBot\db\tele_signal_bot.sqlite3 `
  --chat-id -100111 `
  --trader trader_a `
  --scenario-file C:\TeleSignalBot\scripts\my_scenario.json
```

## Test con Freqtrade dry-run

Per verificare l'end-to-end:

1. avvia Freqtrade in `dry-run`
2. inietta il messaggio o lo scenario nel DB del bot
3. controlla se compaiono righe in:
   - [tele_signal_bot.sqlite3](C:/TeleSignalBot/db/tele_signal_bot.sqlite3)
   - [tradesv3.dryrun.sqlite](C:/TeleSignalBot/freqtrade/tradesv3.dryrun.sqlite)
4. osserva FreqUI / log / DB

### Ispezione rapida del caso appena generato

Dopo l'iniezione puoi leggere un caso completo con un comando unico:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\inspect_attempt.py --latest-signal
```

Oppure per un caso preciso:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\inspect_attempt.py `
  --attempt-key T_-100111_1001_trader_a
```

Lo script mostra in un colpo solo:

- `signal` e `operational_signals`
- `trade` e `position`
- ordini nel DB del bot (`ENTRY`, `SL`, `TP`, `EXIT`)
- timeline `events`
- `warnings`
- ordini presenti nel DB dry-run di Freqtrade associati allo stesso `attempt_key`

Questo e il modo piu veloce per confrontare quello che vedi in FreqUI con la fonte di verita del bridge.

### Verifica atteso vs osservato

Per i casi tipici del dry-run puoi usare anche una checklist automatica:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\verify_attempt_expectation.py `
  --latest-signal `
  --expect move_stop
```

Aspettative supportate:

- `entry_filled`
- `move_stop`
- `tp1`
- `close_partial`
- `cancel_pending`
- `close_full`

Lo script stampa un riepilogo `PASS/FAIL` con i controlli principali sul DB del bot e sul DB dry-run di Freqtrade.

### Suite automatica di casi

Se vuoi lanciare piu' casi in sequenza senza fare i passaggi a mano, usa il runner:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\run_dryrun_suite.py `
  --scenario-dir C:\TeleSignalBot\scripts\trader_a_scenarios `
  --reset `
  --report-json C:\TeleSignalBot\.test_tmp\dryrun_suite_report.json
```

Cosa fa:

- opzionalmente resetta il DB del bot e il DB dry-run di Freqtrade
- risolve da solo le dipendenze `reply_to_message_id` tra i file scenario
- inietta i casi nell'ordine corretto
- aspetta il runtime del bridge / dry-run
- esegue i check `PASS/FAIL` sui casi che hanno una aspettativa nota

Esempio: un update come `u01_move_stop_to_be.json` porta dentro automaticamente anche il suo segnale padre `s02_limit_single_long.json`.

Per eseguire solo alcuni file:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\run_dryrun_suite.py `
  --scenario-dir C:\TeleSignalBot\scripts\trader_a_scenarios `
  --files u01_move_stop_to_be.json u06_close_partial_50.json `
  --reset
```

Aspettative mappate automaticamente al momento:

- `u01_move_stop_to_be` -> `move_stop`
- `u02_move_stop_to_level` -> `move_stop`
- `u03_tp1_hit_stop_to_be` -> `tp1`
- `u04_tp2_hit_close_rest` -> `close_full`
- `u05_stop_hit` -> `close_full`
- `u06_close_partial_50` -> `close_partial`
- `u07_close_full` -> `close_full`
- `u08_cancel_pending` -> `cancel_pending`
- `u10_mark_filled` -> `entry_filled`

I file senza aspettativa mappata vengono comunque iniettati, ma marcati come `INFO no verification mapped`.

## Template rapido bridge/freqtrade

Se vuoi provare solo i 3 casi principali del bridge, usa [injection_bridge_cases.template.json](C:/TeleSignalBot/scripts/injection_bridge_cases.template.json).

Placeholder da cambiare:

- `TRADER_ID_PLACEHOLDER`
- `SYMBOL_PLACEHOLDER`
- `ENTRY_1`
- `ENTRY_2`
- `STOP_LOSS`
- `TP_1`
- `TP_2`

Include solo questi casi:

- `LIMIT` singolo
- `LIMIT` multiplo
- `MARKET + LIMIT successivi`

## Casi tipici da provare

- `LIMIT` singolo
- multi-entry `LIMIT`
- `MARKET` iniziale + `LIMIT` successivi
- `move stop`
- `move stop to BE`
- `close partial`
- `close full`
- `cancel pending`
- `tp hit`
- `stop hit`

## Note importanti

- Il testo deve essere compatibile con il parser del trader scelto.
- Se il testo non e' riconosciuto, potresti ottenere `UNCLASSIFIED` o nessuna riga in `signals`.
- Il trader viene forzato da `--trader`, quindi puoi provare diversi parser senza dipendere dalla source mapping reale di Telegram.
- Se non vuoi che venga toccata la pairlist dinamica, usa `--no-dynamic-pairlist`.
- Se rilanci lo stesso scenario con lo stesso `telegram_message_id`, lo script ora segnala il duplicato e non riprocessa il vecchio `raw_message`. Per reiniettare davvero, cambia `telegram_message_id` oppure fai reset del DB.

## Suggerimento pratico

Per ogni trader che vuoi testare spesso, conviene mantenere un file scenario dedicato, ad esempio:

- `scripts/scenario_trader_a.json`
- `scripts/scenario_trader_3.json`
- `scripts/scenario_xlm_runtime.json`

Cosi' puoi rilanciare gli stessi casi molto velocemente mentre osservi il comportamento del bridge in `dry-run`.

---

## Suite scenari trader_a

Directory dedicata: `scripts/trader_a_scenarios/`

Un file JSON per caso, un messaggio per file. I testi usano il formato reale del trader (russo/inglese, dash notation `—`, emoji). Simboli e prezzi sono placeholder da sostituire prima di iniettare.

### Placeholder da sostituire

| Placeholder | Descrizione |
|-------------|-------------|
| `SYMBOL` | Es. `BTCUSDT` |
| `ENTRY_PRICE` | Prezzo entry limit |
| `ENTRY_1`, `ENTRY_2` | Due livelli per averaging |
| `STOP_PRICE` | Stop loss |
| `TP1_PRICE`, `TP2_PRICE`, `TP3_PRICE` | Take profit levels |
| `NEW_STOP_PRICE` | Stop spostato (u02) |
| `NEW_TP1`, `NEW_TP2`, `NEW_TP3` | Teyks aggiornati (u09) |
| `FILL_PRICE` | Prezzo di esecuzione entry (u10) |

### NEW SIGNAL (5 file, ID 7001–7005)

| File | ID | Tipo | Parser attende |
|------|----|------|----------------|
| `s01_market_long.json` | 7001 | MARKET LONG | NEW_SIGNAL |
| `s02_limit_single_long.json` | 7002 | LIMIT single LONG | NEW_SIGNAL |
| `s03_limit_averaging_long.json` | 7003 | LIMIT 2 entry (usredn.) | NEW_SIGNAL |
| `s04_market_short.json` | 7004 | MARKET SHORT (emoji+dash) | NEW_SIGNAL |
| `s05_setup_incomplete.json` | 7005 | LONG, тейки позже | SETUP_INCOMPLETE |

### UPDATE (11 file, ID 7101–7111)

| File | ID | reply_to | Intent atteso |
|------|----|----------|---------------|
| `u01_move_stop_to_be.json` | 7101 | 7002 | U_MOVE_STOP_TO_BE |
| `u02_move_stop_to_level.json` | 7102 | 7002 | U_MOVE_STOP |
| `u03_tp1_hit_stop_to_be.json` | 7103 | 7002 | U_TP_HIT + U_MOVE_STOP_TO_BE |
| `u04_tp2_hit_close_rest.json` | 7104 | 7002 | U_TP_HIT + U_CLOSE_FULL |
| `u05_stop_hit.json` | 7105 | 7002 | U_STOP_HIT |
| `u06_close_partial_50.json` | 7106 | 7003 | U_CLOSE_PARTIAL |
| `u07_close_full.json` | 7107 | 7001 | U_CLOSE_FULL |
| `u08_cancel_pending.json` | 7108 | 7002 | U_CANCEL_PENDING_ORDERS |
| `u09_update_take_profits.json` | 7109 | 7001 | U_UPDATE_TAKE_PROFITS |
| `u10_mark_filled.json` | 7110 | 7002 | U_MARK_FILLED |
| `u11_result_report.json` | 7111 | 7002 | U_REPORT_FINAL_RESULT |

### INFO_ONLY (1 file, ID 7201)

| File | ID | Intent atteso |
|------|----|---------------|
| `i01_info_admin.json` | 7201 | INFO_ONLY |

### Reset DB prima di ogni sessione

> **Obbligatorio.** Senza reset, i segnali PENDING rimasti bloccano le injection successive per `max_concurrent_same_symbol`.

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\reset_live_db.py
```

### Come iniettare

> Gli UPDATE devono essere iniettati **dopo** il segnale genitore, altrimenti `reply_to_message_id` non trova la riga nel DB.

```powershell
# Singolo file
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\inject_fake_messages.py `
  --scenario-file C:\TeleSignalBot\scripts\trader_a_scenarios\s02_limit_single_long.json

# Tutti i NEW SIGNAL in ordine
foreach ($f in Get-ChildItem C:\TeleSignalBot\scripts\trader_a_scenarios\s0*.json) {
  C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\inject_fake_messages.py `
    --scenario-file $f.FullName
}

# Un UPDATE dopo aver iniettato il segnale genitore
C:\TeleSignalBot\.venv\Scripts\python.exe C:\TeleSignalBot\scripts\inject_fake_messages.py `
  --scenario-file C:\TeleSignalBot\scripts\trader_a_scenarios\u01_move_stop_to_be.json
```

# Fase 5 - Istruzioni complete di completamento

## Scopo

Questo documento descrive i passi operativi necessari per chiudere davvero la Fase 5
del progetto: bridge execution via freqtrade, dry-run controllato, verifica end-to-end,
monitoraggio minimo e aggiornamento finale della documentazione.

Va usato insieme a:

- `docs/PRD_FASE_5.md`
- `docs/FREQTRADE_CONFIG.md`
- `docs/FREQTRADE_RUNBOOK.md`
- `docs/COMANDI.md`
- `docs/AUDIT.md`

## Stato attuale del repo

Alla data di questo documento, il repository risulta in questo stato:

- Step 16 implementato: cleanup stub legacy + `freqtrade_normalizer.py` + strategy bridge.
- Step 17 implementato: stoploss, exit full, callback writer base.
- Step 18 implementato: update intents principali (`U_MOVE_STOP`, `U_CLOSE_FULL`,
  `U_CLOSE_PARTIAL`, `U_CANCEL_PENDING`).
- Step 19 implementato: config locale, bootstrap e runtime `freqtrade` reale in `dry_run`.
- Step 20 implementato: `channels.yaml`, runbook operativo e FreqUI funzionante.

La fase non e ancora chiusa operativamente perche manca ancora l'osservazione
end-to-end di un messaggio Telegram reale nello stesso ambiente di runtime.

## Stato verificato il 2026-03-27

Nota pairlist dinamica:

- il bot puo auto-popolare `freqtrade/user_data/dynamic_pairs.json` quando arriva un `NEW_SIGNAL` valido
- `freqtrade` puo leggerla via `RemotePairList` senza dover conoscere prima tutti i simboli


Nel workspace corrente sono stati osservati con successo nel runtime `freqtrade` reale in `dry_run`:

- `NEW_SIGNAL`: fill entry e callback DB su `signals`, `trades`, `orders`, `positions`, `events`
- `U_MOVE_STOP`: aggiornamento stop nel DB bot e riallineamento dello `stoploss_on_exchange` nel DB `freqtrade`
- `U_CLOSE_FULL`: uscita completa con `POSITION_CLOSED`
- `U_CLOSE_PARTIAL`: riduzione posizione con `adjust_trade_position()` e `PARTIAL_CLOSE_FILLED`
- `U_CANCEL_PENDING`: ordine `ENTRY` `open` cancellato per timeout dopo UPDATE mirato, senza fill

Stato operativo aggiuntivo osservato:

- FreqUI/API server locale funzionante su `http://127.0.0.1:8080`
- pairlist dinamica auto-popolata dal bot tramite `freqtrade/user_data/dynamic_pairs.json`

Limiti residui osservati:

- listener Telegram live non ancora osservato end-to-end con un messaggio reale nello stesso workspace
- la pagina Pairlist della FreqUI puo generare errori lato API in avvio; il bridge ordini resta comunque operativo
- se `fiat_display_currency` e valorizzato, la UI puo interrogare CoinGecko per la conversione fiat e ricevere `429`


## Obiettivo di chiusura

La Fase 5 puo dirsi completata solo quando sono veri tutti i punti seguenti:

1. Il bridge freqtrade gira nel suo venv dedicato e carica `SignalBridgeStrategy`.
2. Il listener gira e scrive sul DB reale condiviso.
3. Un segnale `PENDING` viene visto dalla strategy e porta ad apertura trade in dry-run.
4. I callback aggiornano correttamente `signals`, `trades`, `orders`, `positions`, `events`.
5. Almeno i flussi `NEW_SIGNAL`, `U_MOVE_STOP`, `U_CLOSE_FULL` sono osservati in esecuzione.
6. La documentazione finale viene aggiornata con esito, limiti residui e comandi reali.

## Criteri di chiusura

### Chiusura tecnica

La chiusura tecnica richiede:

- test locali del bridge verdi
- strategy caricabile nel runtime freqtrade reale
- config locale valida
- DB condiviso raggiungibile da bot e freqtrade

### Chiusura operativa

La chiusura operativa richiede anche:

- almeno uno smoke test dry-run end-to-end riuscito
- verifica manuale su DB e FreqUI
- conferma che il canale reale configurato produca la catena completa

La Fase 5 si considera davvero chiusa solo al raggiungimento della chiusura operativa.

## Prerequisiti

Prima di iniziare:

1. Assicurati che il working tree sia sotto controllo e che le modifiche Fase 5 siano note.
2. Verifica che il DB usato per il test non sia un DB live di produzione.
3. Assicurati che `config/channels.yaml` e `config/telegram_source_map.json` siano coerenti.
4. Assicurati di avere credenziali locali per Bybit paper o ambiente dry-run equivalente.
5. Assicurati di avere una macchina dove possano girare contemporaneamente:
   - bot principale
   - freqtrade
   - eventuale FreqUI

## Step 1 - Verifica baseline del repo

Esegui i test minimi che devono restare verdi prima di ogni collaudo operativo:

```powershell
.\.venv\Scripts\python.exe -m pytest .\src\execution\tests -q
.\.venv\Scripts\python.exe -m pytest .\src\telegram\tests -q
```

Esito atteso:

- `src/execution/tests` verde
- in `src/telegram/tests` e tollerata la failure preesistente su
  `test_catchup_skips_channel_with_no_last_id`, ma non devono comparire nuove regressioni
  legate alla Fase 5

Se compare una nuova regressione, fermarsi qui e risolverla prima del collaudo.

## Step 2 - Preparare il venv freqtrade

Creare un ambiente separato da quello del bot:

```powershell
python -m venv .venv-freqtrade
.\.venv-freqtrade\Scripts\python.exe -m pip install --upgrade pip
.\.venv-freqtrade\Scripts\python.exe -m pip install "freqtrade>=2024.0"
```

Verifica minima:

```powershell
.\.venv-freqtrade\Scripts\python.exe -c "import freqtrade.strategy; print('ok')"
```

Esito atteso:

- il comando stampa `ok`

Se fallisce, la Fase 5 non puo essere chiusa nel runtime reale.

## Step 3 - Creare la configurazione locale freqtrade

Partire dal template gia presente:

```powershell
Copy-Item .\freqtrade\user_data\config.template.json .\freqtrade\user_data\config.json
```

Poi aggiornare localmente almeno questi campi in `freqtrade/user_data/config.json`:

- `exchange.key`
- `exchange.secret`
- `bot_db_path`
- `te_signal_bot_db_path`
- `api_server.jwt_secret_key`
- `api_server.ws_token`
- `api_server.username`
- `api_server.password`
- `telegram.enabled`
- `telegram.token`
- `telegram.chat_id`

Regole obbligatorie:

- non committare `config.json`
- non salvare segreti nei documenti del repo
- mantenere `dry_run: true` fino a chiusura ufficiale della fase
- mantenere `trading_mode: "futures"` e `defaultType: "swap"` se si usa il mapping pair
  `BTC/USDT:USDT`

## Step 4 - Verificare il DB condiviso

Scegliere un path DB unico, stabile e condiviso tra i due processi.

Esempio:

```powershell
$env:TELESIGNALBOT_DB_PATH = "C:\TeleSignalBot\.local\tele_signalbot.sqlite3"
```

Verifiche:

1. Il bot scrive sul DB scelto.
2. freqtrade legge lo stesso DB.
3. Non ci sono copie duplicate del DB in cartelle temporanee.

Controllo rapido:

```powershell
.\.venv\Scripts\python.exe -c "import os; print(os.getenv('TELESIGNALBOT_DB_PATH'))"
.\.venv-freqtrade\Scripts\python.exe -c "import os; print(os.getenv('TELESIGNALBOT_DB_PATH'))"
```

I due output devono puntare allo stesso file.

## Step 5 - Verificare la configurazione canali

Controllare:

- `config/channels.yaml`
- `config/telegram_source_map.json`

Nel repo attuale il canale reale previsto e `PifSignal`, multi-trader.

Checklist:

1. `chat_id` corretto in `channels.yaml`
2. `active: true`
3. `trader_id: null` per canale multi-trader
4. chat id presente in `multi_trader_chat_ids` in `telegram_source_map.json`
5. parser profile esistenti per:
   - `trader_a`
   - `trader_b`
   - `trader_c`
   - `trader_d`
   - `trader_3`

Se uno dei trader attesi manca o non ha regole caricabili, il collaudo Step 20 non e valido.

## Step 6 - Validare la config freqtrade

Eseguire:

```powershell
cd freqtrade
$env:PYTHONPATH = "C:\TeleSignalBot"
..\.venv-freqtrade\Scripts\freqtrade.exe show-config -c .\user_data\config.json
```

Esito atteso:

- config valida
- strategy path risolto
- nessun errore su exchange, whitelist o chiavi richieste

Se il comando non trova la strategy, verificare:

- `strategy = "SignalBridgeStrategy"`
- la strategy vive gia nella posizione standard `user_data/strategies`
- `PYTHONPATH` deve puntare a `C:\TeleSignalBot` per permettere l''import di `src.*`
- cwd corretto in `C:\TeleSignalBot\freqtrade`

## Step 7 - Verificare che la strategy si carichi

Eseguire:

```powershell
cd freqtrade
$env:PYTHONPATH = "C:\TeleSignalBot"
..\.venv-freqtrade\Scripts\freqtrade.exe trade -c .\user_data\config.json --strategy SignalBridgeStrategy --dry-run
```

Controlli immediati attesi nel log:

- strategy `SignalBridgeStrategy` caricata
- nessun errore di import da `src.execution.freqtrade_normalizer`
- nessun errore su `bot_db_path` o `TELESIGNALBOT_DB_PATH`
- FreqUI in ascolto sulla porta configurata

Se la strategy non parte, la Fase 5 non puo essere considerata completata.

## Step 8 - Avviare il listener del bot

In una seconda shell:

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

Controlli minimi:

- il listener carica `config/channels.yaml`
- il canale `PifSignal` risulta attivo
- non ci sono errori immediati su parse config o recovery

## Step 9 - Eseguire uno smoke test dry-run controllato

Questo e il test minimo obbligatorio per chiudere la fase.

Sono accettabili due modalita:

### Modalita A - Segnale reale controllato

Usare un messaggio reale dal canale configurato e verificare la catena completa:

1. il messaggio entra in `raw_messages`
2. viene creato `parse_results`
3. viene creato `signals.status = 'PENDING'`
4. viene creato `operational_signals`
5. freqtrade apre il trade in dry-run
6. il callback porta il segnale a `ACTIVE`

### Modalita B - Fixture realistico sul DB

Accettabile solo se il runtime Telegram reale non e disponibile in quel momento.

In questo caso bisogna comunque osservare:

1. strategy attiva nel processo freqtrade reale
2. lettura di un segnale `PENDING` dal DB condiviso
3. apertura trade in dry-run
4. callback di fill con aggiornamento DB

Per chiudere davvero la fase e preferibile la Modalita A.

## Step 10 - Verifiche DB obbligatorie dopo l'entry

Dopo la prima apertura in dry-run eseguire controlli SQL sul DB condiviso.

### Segnali

```sql
SELECT attempt_key, trader_id, symbol, side, status, created_at, updated_at
FROM signals
ORDER BY created_at DESC
LIMIT 20;
```

Esito atteso:

- il segnale usato per il test passa da `PENDING` a `ACTIVE`

### Operational signals

```sql
SELECT op_signal_id, attempt_key, trader_id, message_type, is_blocked, position_size_usdt, leverage
FROM operational_signals
ORDER BY op_signal_id DESC
LIMIT 20;
```

Esito atteso:

- esiste la riga `NEW_SIGNAL` coerente con il `attempt_key`
- `is_blocked = 0`

### Trades

```sql
SELECT trade_id, attempt_key, symbol, state, close_reason, opened_at, closed_at
FROM trades
ORDER BY trade_id DESC
LIMIT 20;
```

Esito atteso:

- esiste un trade `OPEN` per lo stesso `attempt_key`

### Orders

```sql
SELECT order_pk, attempt_key, purpose, status, qty, price, trigger_price
FROM orders
ORDER BY order_pk DESC
LIMIT 20;
```

Esito atteso:

- presente almeno un ordine `ENTRY`
- presenti gli ordini protettivi attesi, se previsti dal context

### Positions

```sql
SELECT env, symbol, side, size, entry_price, mark_price, leverage, realized_pnl
FROM positions
ORDER BY updated_at DESC
LIMIT 20;
```

Esito atteso:

- posizione aperta con `size > 0`

### Events

```sql
SELECT event_id, attempt_key, event_type, created_at
FROM events
ORDER BY event_id DESC
LIMIT 30;
```

Esito atteso:

- presente almeno `ENTRY_FILLED`

## Step 11 - Verificare gli UPDATE minimi obbligatori

La Fase 5 non e chiusa se non vengono provati almeno i principali update execution-side.

### Caso 1 - U_MOVE_STOP

Obiettivo:

- verificare che il nuovo stop venga letto dalla strategy
- verificare che il comportamento sia coerente con `custom_stoploss()`

Evidenza minima richiesta:

- update presente nel DB
- nessun errore runtime in freqtrade
- stoploss relativo ricalcolato secondo il nuovo livello

### Caso 2 - U_CLOSE_FULL

Obiettivo:

- verificare che la strategy emetta il segnale di uscita
- verificare aggiornamento trade e position nel DB

Evidenza minima richiesta:

- trade passa a `CLOSED`
- `positions.size = 0`
- evento `POSITION_CLOSED` o equivalente presente

### Caso 3 - U_CLOSE_PARTIAL

Obiettivo:

- verificare riduzione parziale posizione tramite `adjust_trade_position()`
- verificare persist della frazione chiusa nel DB

Evidenza minima richiesta:

- trade ancora `OPEN` se resta qty residua
- `trades.meta_json` contiene `last_partial_exit_update_id`
- evento `PARTIAL_CLOSE_FILLED` presente

### Caso 4 - U_CANCEL_PENDING

Obiettivo:

- verificare che una entry non ancora fillata venga rifiutata o fatta scadere

Evidenza minima richiesta:

- il segnale non viene aperto
- nessun trade nuovo per quel `attempt_key`
- nessuna incoerenza tra signal status e stato ordini

## Step 12 - Verifica FreqUI e bot Telegram freqtrade

Nota workspace 2026-03-27:

- il bridge runtime e stato validato, ma la FreqUI locale non parte finche non viene risolta l'incompatibilita `api_server`/`FastAPI`/`Starlette`

Controlli minimi:

1. FreqUI raggiungibile sulla porta configurata
2. bot online
3. trade dry-run visibile
4. pair del test presente tra quelle monitorate

Se il bot Telegram freqtrade e abilitato, verificare almeno:

- `/status`
- `/status table`
- `/stopbuy`

Se disponibile nel setup locale, verificare anche:

- `/forceexit <trade-id>`

## Step 13 - Gestione problemi ricorrenti

### Pair non eseguibile

Sintomo:

- nessuna entry, anche se il segnale e `PENDING`

Controlli:

- pair normalizzata dal bridge
- `pair_whitelist` in config
- `trading_mode` e `defaultType`

### Strategy non caricata

Sintomo:

- freqtrade parte ma non importa `SignalBridgeStrategy`

Controlli:

- path strategy
- cwd
- installazione freqtrade
- import path del progetto

### DB lock

Sintomo:

- errori `SQLITE_BUSY` o `database is locked`

Controlli:

- un solo DB condiviso
- niente viewer esterni in write lock
- callback writer con retry attivo

### Signal resta PENDING

Sintomo:

- strategy attiva ma nessuna entry

Controlli:

- `is_blocked = 0`
- pair mappabile
- `stake_amount` valorizzato
- `leverage` valorizzato
- signal ancora `PENDING`
- nessun `U_CANCEL_PENDING` targettizzato

## Step 14 - Raccolta evidenze di chiusura

Prima di dichiarare completata la fase, raccogliere:

1. output dei test bridge
2. output `freqtrade show-config`
3. conferma che la strategy si carica
4. evidenza SQL di:
   - `signals`
   - `trades`
   - `orders`
   - `positions`
   - `events`
5. esito di almeno uno smoke test dry-run end-to-end
6. esito di almeno i casi `U_MOVE_STOP` e `U_CLOSE_FULL`

Se manca una di queste evidenze, non segnare la fase come chiusa.

## Step 15 - Aggiornamenti documentali obbligatori

Al termine del collaudo aggiornare:

1. `docs/AUDIT.md`
   - segnare la Fase 5 come completata solo se esiste evidenza end-to-end reale
   - altrimenti segnare "implementata ma non validata live"

2. `docs/PRD_FASE_5.md`
   - togliere `BOZZA` solo dopo chiusura operativa
   - allineare la parte partial exit all'implementazione reale se usa
     `adjust_trade_position()` invece di `custom_exit()`

3. `docs/FREQTRADE_RUNBOOK.md`
   - aggiungere i comandi reali usati
   - aggiungere i risultati osservati
   - aggiungere eventuali workaround emersi durante il collaudo

## Definition of Done

La Fase 5 e chiusa quando tutte le caselle seguenti sono vere:

- [x] `src/execution/tests` verdi
- [x] runtime freqtrade reale installato e funzionante
- [x] `freqtrade/user_data/config.json` locale valido
- [x] strategy `SignalBridgeStrategy` caricata da freqtrade
- [ ] bot listener attivo sullo stesso DB
- [x] almeno un trade dry-run aperto da un segnale del bot
- [x] callback DB osservati correttamente
- [x] verifica DB completata su `signals`, `trades`, `orders`, `positions`, `events`
- [x] `U_MOVE_STOP` verificato
- [x] `U_CLOSE_FULL` verificato
- [x] `U_CLOSE_PARTIAL` verificato oppure limite documentato
- [x] `U_CANCEL_PENDING` verificato oppure limite documentato
- [x] `docs/AUDIT.md` aggiornato
- [x] `docs/PRD_FASE_5.md` allineato allo stato reale
- [x] `docs/FREQTRADE_RUNBOOK.md` aggiornato con evidenze finali

## Nota finale

Finche manca il collaudo end-to-end nel processo freqtrade reale, la Fase 5 non deve
essere marcata come "completata", ma solo come "implementata e pronta per validazione".


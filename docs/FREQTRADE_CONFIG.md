# Configurazione freqtrade — bootstrap Step 19

Questo step introduce un template sicuro in `freqtrade/user_data/config.template.json`.
Il file reale da usare in ambiente paper/live è `freqtrade/user_data/config.json` e va creato localmente copiando il template.

## Regole di sicurezza

- Non committare mai chiavi Bybit, token Telegram o password FreqUI.
- `freqtrade/user_data/config.json` è ignorato da git.
- Se preferisci, tieni i segreti in un file locale separato come `freqtrade/user_data/config.local.json` e fondili fuori repo prima dell'avvio.

## Come il bridge trova il DB del bot

- La strategy legge `bot_db_path` oppure `te_signal_bot_db_path` dal config freqtrade.
- In alternativa legge la variabile ambiente `TELESIGNALBOT_DB_PATH`.
- Il path deve puntare al DB SQLite usato dal bot principale.

## Prerequisiti Fase 6

Per usare la modalita exchange-backed della Fase 6:

- `execution.protective_orders_mode = "exchange_manager"`
- il runtime deve iniettare `exchange_order_manager` nella `SignalBridgeStrategy`
- opzionale: `execution.reconciliation_watchdog_interval_s` per il watchdog periodico leggero

Regole operative obbligatorie in `exchange_manager`:

- la strategy non deve essere owner di `SL` o `TP`
- non devono esistere ordini protettivi paralleli fuori dal manager
- evitare configurazioni che creano un secondo `stoploss_on_exchange` sullo stesso trade

Esempio Windows:

```powershell
$env:TELESIGNALBOT_DB_PATH = "C:\TeleSignalBot\.local\tele_signalbot.sqlite3"
```

## Pair format / market type

- La pairlist puo auto-popolarsi tramite `RemotePairList` locale leggendo `freqtrade/user_data/dynamic_pairs.json`.
- Il bot aggiorna quel file automaticamente quando nasce un `NEW_SIGNAL` valido e mappabile.

- Il normalizer del bridge produce pair in formato futures USDT perpetual: `BTC/USDT:USDT`.
- Per mantenere coerente questo mapping il template imposta:
  - `trading_mode = "futures"`
  - `exchange.ccxt_config.options.defaultType = "swap"`
  - `exchange.ccxt_async_config.options.defaultType = "swap"`
- Se il market type reale non è `swap`, la pair canonica può risultare non mappabile o non presente in whitelist.

## Checklist bootstrap

1. Crea un venv dedicato a freqtrade.
2. Installa freqtrade nel suo venv:

```powershell
python -m venv .venv-freqtrade
.venv-freqtrade\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install "freqtrade>=2024.0"
```

3. Copia il template:

```powershell
Copy-Item freqtrade\user_data\config.template.json freqtrade\user_data\config.json
```

4. Sostituisci localmente:
   - credenziali exchange
   - token/chat Telegram
   - `jwt_secret_key`, `ws_token`, `username`, `password`
   - `bot_db_path` / `te_signal_bot_db_path`

5. Valida il config:

```powershell
cd freqtrade
$env:PYTHONPATH = "C:\TeleSignalBot"
..\.venv-freqtrade\Scripts\freqtrade.exe show-config -c .\user_data\config.json
```

6. Avvia in dry-run:

```powershell
cd freqtrade
$env:PYTHONPATH = "C:\TeleSignalBot"
..\.venv-freqtrade\Scripts\freqtrade.exe trade -c .\user_data\config.json --strategy SignalBridgeStrategy --dry-run
```

## Stato del workspace

- `.venv-freqtrade` esiste ed esegue `freqtrade 2026.2`.
- FreqUI locale e stata riattivata con il pin `starlette<1.0.0`.
- Il bridge dry-run e gia stato validato su `NEW_SIGNAL`, `U_MOVE_STOP`, `U_CLOSE_FULL`, `U_CLOSE_PARTIAL`, `U_CANCEL_PENDING`.
- La Fase 6 e validata in dry-run avanzato anche su `SL`/`TP` exchange-backed, `TP fill` e `bootstrap_sync_open_trades()`.
- La pairlist puo auto-popolarsi tramite `freqtrade/user_data/dynamic_pairs.json`.

## Contratto runtime — cosa è supportato

Questa tabella riassume il contratto definitivo tra `operation_rules` e il runtime freqtrade (aggiornato dopo Fase 6 allineamento Step A–E).

| Funzionalità | Stato | Dove |
|---|---|---|
| `position_size_usdt` → size position | ✓ Attivo | `custom_stake_amount()` |
| `leverage` → leva | ✓ Attivo | `leverage()` |
| `tp_handling` → ladder TP | ✓ Attivo | `order_filled_callback()` + `ExchangeOrderManager` |
| `entry_prices` E1 come prezzo LIMIT | ✓ Attivo (`first_in_plan`) | `custom_entry_price()` |
| `order_type` reale per ogni entry | ✓ Preservato | router `_build_entry_json()` → `entry_json` |
| `entry_split` → pesi multi-entry | ✗ Non attivo | persistito in DB per audit, non usato a runtime |
| `EntryPricePolicy` → fill entro tolleranza | ✓ Attivo | `confirm_trade_entry()` + `ENTRY_PRICE_REJECTED` event |
| `price_sanity` → gate statico sui prezzi del segnale | ✓ Attivo (parse-time) | Gate 9 in `engine.py`, prima della creazione del segnale |
| `price_corrections` → correzione prezzi runtime | ✗ Non supportato | dichiarato `PRICE_CORRECTIONS_NOT_SUPPORTED = True`; `price_corrections_json = NULL` |
| `auto_apply_intents` → filtro intent UPDATE | ✓ Attivo | `allowed_update_directives` property |
| `log_only_intents` | ✗ Non attivo a runtime | persistito per audit, non consumato |
| `machine_event.rules` → regole event-driven | ✗ Non supportato | dichiarato `MACHINE_EVENT_RULES_NOT_SUPPORTED = True`; fallback permissivo |

### Note sul contratto

**`price_sanity` vs `EntryPricePolicy`** — sono due gate indipendenti con scope diverso:
- `price_sanity` (Gate 9, `engine.py`) è un gate **parse-time**: valida i prezzi del segnale dal testo del messaggio contro range statici in config YAML. Non vede il prezzo di fill.
- `EntryPricePolicy` (`freqtrade_normalizer.py`) è il gate **runtime**: valida il prezzo proposto da freqtrade contro `entry_prices` del segnale in `confirm_trade_entry()`. Se il fill è fuori tolleranza, l'ingresso viene rigettato e viene scritto un evento `ENTRY_PRICE_REJECTED`.

**`entry_split`** — il piano multi-entry calcolato da `operation_rules` (pesi E1/E2/E3) è persisted in DB per audit ma non produce ordini multipli a runtime. La policy attiva è `first_in_plan`: si usa E1 come prezzo LIMIT unico.

**`price_corrections`** — il campo è presente nel modello `EffectiveRules` per compatibilità futura ma non viene letto a runtime. Il sentinel `PRICE_CORRECTIONS_NOT_SUPPORTED = True` in `freqtrade_normalizer.py` lo dichiara esplicitamente.

## Smoke test paper/dry

Target operativo:

1. Inserire nel DB un fixture realistico o un segnale reale controllato con `signals.status='PENDING'`.
2. Verificare che la pair sia presente in `pair_whitelist`.
3. Avviare freqtrade in dry-run.
4. Controllare in FreqUI che appaia il trade.
5. Verificare nel DB:
   - `signals` passa a `ACTIVE`
   - `trades` contiene il trade aperto
   - `orders` contiene entry + SL/TP
   - `events` registra `ENTRY_FILLED`

Limite attuale del workspace:

- lo smoke test del bridge `freqtrade` e gia stato eseguito con successo nel venv dedicato `.venv-freqtrade`
- resta ancora da osservare solo un messaggio Telegram reale entrare nel listener e arrivare fino a `freqtrade`

## Nota importante su working directory e import path

- La config e pensata per essere eseguita da `C:\TeleSignalBot\freqtrade`.
- La strategy vive nella posizione standard `user_data/strategies`, quindi `strategy_path` non serve nel config.
- La strategy importa moduli del progetto da `src.*`, quindi prima di avviare freqtrade bisogna esportare:

```powershell
$env:PYTHONPATH = "C:\TeleSignalBot"
```

## Troubleshooting minimo

### Pair non presente in whitelist

- Sintomo: nessuna entry in dry-run anche se il segnale è `PENDING`.
- Controllo: confronta `metadata['pair']` / pair normalizzata con la whitelist runtime.
- Fix: nel setup attuale la whitelist puo essere estesa automaticamente tramite `RemotePairList` e `dynamic_pairs.json`.

### Symbol non mappabile

- Sintomo: il signal resta non eseguibile.
- Controllo: il normalizer attuale si aspetta simboli canonici tipo `BTCUSDT`.
- Fix: correggi il simbolo a monte o aggiorna la whitelist/market type per mantenere il formato `BASE/USDT:USDT`.

### DB lock / SQLITE_BUSY

- Sintomo: callback o letture freqtrade falliscono a intermittenza.
- Mitigazione già implementata: retry nel callback writer.
- Operativamente:
  - usa un solo DB condiviso stabile, non copie temporanee
  - evita tool esterni che tengono aperto il DB in write lock
  - se il lock persiste, abbassa la concorrenza dei processi che scrivono sul DB

### `exchange_order_manager` non iniettato

- Sintomo: il trade entra ma i protettivi exchange-backed non vengono creati.
- Evidenza DB: warning `exchange_manager_missing` o evento `PROTECTIVE_ORDER_MANAGER_MISSING`.
- Fix: il runtime execution-side deve assegnare `strategy.exchange_order_manager = <manager>`.

### Reconciliation ricrea i protettivi al restart

- Sintomo: al riavvio compaiono nuovi client order id tipo `:R2`, `:R3`.
- Significato: il bootstrap ha visto un mismatch sicuro tra DB e backend exchange-backed e ha fatto remediation conservativa.
- Verifica: controllare `events` con `RECONCILIATION_COMPLETED` e l'assenza di warning ambigui.

### FreqUI / API server non parte

- Sintomo: crash all'avvio con errore tipo `FastAPI` senza `add_event_handler`.
- Causa osservata nel workspace 2026-03-27: venv freqtrade con `starlette 1.0.0`, incompatibile con il webserver usato da `freqtrade 2026.2`.
- Fix applicato localmente: nel venv `.venv-freqtrade` eseguire `python -m pip install "starlette<1.0.0"`.
- Verifica: `python -c "from fastapi import FastAPI; print(hasattr(FastAPI(), 'add_event_handler'))"` deve stampare `True`.


### Errori CoinGecko / fiat conversion

- Sintomo: log `429` da CoinGecko in `freqtrade.rpc.fiat_convert`.
- Causa: `fiat_display_currency` attivo, usato solo per mostrare valori fiat nella UI.
- Impatto: non blocca ordini, listener o bridge.
- Fix semplice: impostare `fiat_display_currency` a stringa vuota se non serve la conversione fiat.

### Warning su JWT secret troppo corta

- Sintomo: `InsecureKeyLengthWarning` nel log API server.
- Causa: `api_server.jwt_secret_key` o `api_server.ws_token` troppo corti o lasciati con placeholder.
- Impatto: non blocca il bridge, ma il setup e incompleto.
- Fix: usare stringhe casuali locali di almeno 32 caratteri.

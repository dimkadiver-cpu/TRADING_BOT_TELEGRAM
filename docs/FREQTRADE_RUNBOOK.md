# Runbook operativo minimo - Step 20

Questo runbook copre la catena minima:

`listener -> DB SQLite -> SignalBridgeStrategy -> freqtrade -> callback DB`

Per i comandi pronti da copiare, vedi anche `docs/COMANDI.md`.

## Canale attivo

- `PifSignal` (`chat_id: -1003171748254`)
- Tipo: multi-trader
- Trader attesi sul canale: `trader_a`, `trader_b`, `trader_c`, `trader_d`, `trader_3`

## Verifica prerequisiti

Nota pairlist dinamica:

- il bot mantiene automaticamente `freqtrade/user_data/dynamic_pairs.json`
- `freqtrade` la rilegge tramite `RemotePairList` e aggiunge le nuove pair alla whitelist runtime


Prima del run:

1. `config/channels.yaml` contiene il canale attivo corretto.
2. `config/telegram_source_map.json` marca il canale come multi-trader.
3. Parser profile disponibile per ogni trader atteso.
4. Operation rules caricabili per ogni trader atteso.
5. `freqtrade/user_data/config.json` esiste localmente e punta al DB corretto.
6. Il venv freqtrade dedicato contiene il modulo reale `freqtrade.strategy`.
7. Se si usa Fase 6, `execution.protective_orders_mode` e impostato a `exchange_manager`.
8. Il runtime ha iniettato `exchange_order_manager` nella strategy.

## Avvio listener

Nel venv del bot:

```powershell
.venv\Scripts\Activate.ps1
python main.py
```

Controlli minimi:

- il listener carica `config/channels.yaml`
- il chat id `-1003171748254` risulta attivo
- non compaiono errori di parse sul file config

## Avvio freqtrade

Nel venv dedicato freqtrade:

```powershell
.venv-freqtrade\Scripts\Activate.ps1
cd freqtrade
$env:PYTHONPATH = "C:\TeleSignalBot"
..\.venv-freqtrade\Scripts\freqtrade.exe show-config -c .\user_data\config.json
..\.venv-freqtrade\Scripts\freqtrade.exe trade -c .\user_data\config.json --strategy SignalBridgeStrategy --dry-run
```

Controlli minimi:

- config valida
- strategy `SignalBridgeStrategy` caricata
- nessun errore su `bot_db_path` / `TELESIGNALBOT_DB_PATH`
- FreqUI in ascolto su `http://127.0.0.1:8080`

## Verifica FreqUI

Apri:

- `http://127.0.0.1:8080`

Controlli:

- bot online
- nessun errore strategy/exchange
- trade dry-run visibili
- pair del segnale presente tra quelle monitorate
- evitare la pagina Pairlist della UI durante il collaudo se compaiono errori lato API su `pairlists`

## Verifica bot Telegram freqtrade

Se abilitato nel config locale, controlla che il bot risponda.

Comandi base da usare:

- stato bot: `/status`
- posizioni aperte tabellari: `/status table`
- stop nuove entry: `/stopbuy`
- close forzata di una posizione, se abilitata dal bot: `/forceexit <trade-id>`

Nota:

- questi comandi sono allineati alla documentazione ufficiale freqtrade 2024.10:
  [Telegram Usage](https://docs.freqtrade.io/en/2024.10/telegram-usage/)
- `stop entry` in freqtrade è normalmente il comando `/stopbuy`

## Check DB essenziali

### Ultimi raw messages ingestiti

```sql
SELECT raw_message_id, source_chat_id, telegram_message_id, processing_status
FROM raw_messages
ORDER BY raw_message_id DESC
LIMIT 20;
```

### Ultimi parse results

```sql
SELECT parse_result_id, resolved_trader_id, message_type, eligibility_status, parse_status
FROM parse_results
ORDER BY parse_result_id DESC
LIMIT 20;
```

### Segnali creati

```sql
SELECT attempt_key, trader_id, symbol, side, status, created_at
FROM signals
ORDER BY created_at DESC
LIMIT 20;
```

### Operational signals

```sql
SELECT op_signal_id, attempt_key, trader_id, message_type, is_blocked, position_size_usdt, leverage
FROM operational_signals
ORDER BY op_signal_id DESC
LIMIT 20;
```

### Trade / ordini / eventi

```sql
SELECT trade_id, attempt_key, symbol, state, close_reason, opened_at, closed_at
FROM trades
ORDER BY trade_id DESC
LIMIT 20;
```

```sql
SELECT order_pk, attempt_key, purpose, status, qty, price, trigger_price
FROM orders
ORDER BY order_pk DESC
LIMIT 20;
```

Per Fase 6 aggiungere:

```sql
SELECT order_pk, attempt_key, purpose, idx, client_order_id, exchange_order_id,
       status, qty, price, trigger_price, last_exchange_sync_at
FROM orders
WHERE attempt_key = '<attempt_key>'
  AND purpose IN ('SL', 'TP')
ORDER BY order_pk;
```

```sql
SELECT event_id, attempt_key, event_type, created_at
FROM events
ORDER BY event_id DESC
LIMIT 30;
```

## Smoke test controllato

Sequenza attesa:

1. Un messaggio reale entra dal canale `PifSignal`.
2. Il listener lo salva in `raw_messages`.
3. Il router risolve il trader e crea `parse_results`.
4. Phase 4 crea `signals.status='PENDING'` + `operational_signals`.
5. La strategy freqtrade legge il DB e genera il segnale di entry.
6. freqtrade apre il trade in dry-run.
7. I callback aggiornano `signals`, `trades`, `orders`, `positions`, `events`.

## Esito del workspace corrente

Verifica locale completata:

- `config/channels.yaml` presente e leggibile
- parser profile presenti per `trader_a`, `trader_b`, `trader_c`, `trader_d`, `trader_3`
- operation rules caricabili per tutti i trader attesi tramite fallback globale o file specifico
- `freqtrade 2026.2` installato in `.venv-freqtrade` e strategy caricata realmente
- bridge dry-run validato con fixture DB condiviso per `NEW_SIGNAL`, `U_MOVE_STOP`, `U_CLOSE_FULL`, `U_CLOSE_PARTIAL`, `U_CANCEL_PENDING`
- pairlist dinamica auto-popolata e verificata su `dynamic_pairs.json`

Evidenze runtime osservate il 2026-03-27:

- `NEW_SIGNAL`: `signals.status` passa a `ACTIVE`, vengono creati `trades`, `orders`, `positions`, `events`
- `U_MOVE_STOP`: il nuovo stop si propaga fino al nuovo ordine `stoploss_on_exchange` nel DB `freqtrade`
- `U_CLOSE_FULL`: il trade va a `CLOSED`, `positions.size = 0`, evento `POSITION_CLOSED` presente
- `U_CLOSE_PARTIAL`: riduzione parziale con `adjust_trade_position()`, evento `PARTIAL_CLOSE_FILLED`, trade ancora `OPEN` se resta size residua
- `U_CANCEL_PENDING`: ordine `ENTRY` `open` cancellato per timeout dopo UPDATE mirato, senza fill
- FreqUI/API server: riavvio riuscito, `Uvicorn running on http://127.0.0.1:8080`, endpoint `/` e `/docs` entrambi `200`

Evidenze Fase 6 osservate il 2026-03-28 in dry-run avanzato exchange-backed:

- entry fill -> `SL` reale + ladder `TP` reale sul backend runtime
- ogni protettivo con `exchange_order_id` persistito nel DB
- `U_MOVE_STOP` applicato al vero `SL` con replace conservativo
- `TP1` fillato con rebuild del ladder residuo coerente con `positions.size`
- restart con `bootstrap_sync_open_trades()` riuscito
- nessun doppio owner `strategy`/`manager`
- nessun ordine aperto duplicato sul backend runtime

Limiti attuali:

- non c'e un runtime Telegram live configurato in questo workspace
- la FreqUI locale e stata riattivata nel workspace dopo il pin `starlette<1.0.0` nel venv `.venv-freqtrade`
- resta non validato solo il listener Telegram live nello stesso ambiente
- se `fiat_display_currency` resta attivo, la UI puo loggare errori `429` da CoinGecko per la sola conversione fiat
- in avvio la pagina Pairlist della UI puo generare errori lato API senza bloccare il bridge ordini
- i contatori e marker `Long entries` / `Long exit` della UI riflettono i segnali dataframe della strategy, non sempre i fill reali del bridge; vedi `docs/FIX_FREQUI_MARKERS.md`

## Contratto runtime — garanzie e non-supporto

Riepilogo del contratto di allineamento (aggiornato dopo Fase 6 Step E).

### Garanzie attive

- Il prezzo di ingresso LIMIT è sempre E1 dal segnale (`custom_entry_price`, policy `first_in_plan`).
- Un fill proposto da freqtrade fuori dalla tolleranza configurata viene rigettato hard in `confirm_trade_entry()` e viene scritto un evento `ENTRY_PRICE_REJECTED`.
- Il gate `price_sanity` blocca (o avvisa) segnali con prezzi fuori range statico già a parse-time, prima della creazione del segnale in DB.
- Gli intent UPDATE non in `auto_apply_intents` non producono exit/close automatici.

### Non supportato (dichiarato esplicitamente)

- **`price_corrections`**: il campo è nel modello config ma `PRICE_CORRECTIONS_NOT_SUPPORTED = True` nel normalizer. `price_corrections_json = NULL` nel DB. Nessun aggiustamento di prezzo applicato a runtime.
- **`machine_event.rules`**: `MACHINE_EVENT_RULES_NOT_SUPPORTED = True`. Le regole event-driven (es. `TP_EXECUTED -> MOVE_STOP_TO_BE`) non sono eseguite. Il runtime cade in permissivo (applica tutto) quando la modalità è `machine_event`.
- **`entry_split` multi-entry reale**: i pesi E1/E2/E3 sono nel DB per audit ma non producono ordini multipli. Il runtime usa solo E1.
- **`log_only_intents`**: il campo è nel DB snapshot ma non ha effetto separato a runtime.

## Rischi residui

- Canale multi-trader: gli update corti possono ancora dipendere dalla reply-chain o dal tag nel testo.
- Whitelist freqtrade: una pair correttamente normalizzata può restare ineseguibile se non inclusa nel config locale.
- Accesso concorrente al DB: il retry sui callback esiste, ma un writer esterno aggressivo può comunque aumentare la latenza operativa.

## Troubleshooting minimo Fase 6

### Protettivi duplicati

- Sintomo: sul backend compaiono due `SL` o una ladder `TP` duplicata.
- Controllo: verificare che il flag sia `exchange_manager` e che la strategy non stia emulando `TP`/`SL`.
- Azione: controllare `protective_orders_mode`, iniezione del manager e disabilitare ownership parallela.

### Reconciliation ambigua

- Sintomo: il trade resta aperto ma compaiono warning senza remediation automatica.
- Controllo: tabella `warnings`, codice `exchange_reconciliation_ambiguous`.
- Azione: non forzare cancellazioni manuali cieche; confrontare `orders` DB, `events` e ordini venue prima di intervenire.

### Protettivi mancanti dopo restart

- Sintomo: posizione aperta ma nessun `SL`/`TP` aperto.
- Controllo: verificare che il backend runtime risponda a `fetch_open_orders()` e `fetch_position()`.
- Azione: eseguire bootstrap sync e controllare l'evento `RECONCILIATION_COMPLETED`.

### Chart FreqUI fuorviante

- Sintomo: la UI mostra `Long entries: 0` o `Long exit: 0` anche con trade fillati o partial exit registrati nel DB.
- Controllo: distinguere tra marker strategy sul dataframe e fill reali nelle tabelle `orders`, `events` e `trades`.
- Azione: verificare prima il DB; per il piano di fix e i prompt operativi vedi `docs/FIX_FREQUI_MARKERS.md` e `docs/FIX_FREQUI_MARKERS_AGENTE.md`.


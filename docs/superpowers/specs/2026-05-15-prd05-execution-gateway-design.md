# PRD 05 - Execution Gateway neutro + Hummingbot API Paper Adapter

**Data:** 2026-05-15  
**Stato:** design draft approvato in brainstorming  
**Deriva da:** PRD-04 Lifecycle Entry Gate + Executor-Neutral Command Outbox  
**Ambito:** Execution Gateway neutro, command worker, event sync polling, config account/executor, Hummingbot API paper adapter MVP  
**Fuori ambito MVP:** live trading reale, websocket/MQTT event source, reconciliation completa, allocation ledger multi-chain same-side, UI/dashboard, secret management avanzato

---

## 1. Scopo

PRD-05 collega il lifecycle stateful a un executor reale o simulato senza accoppiare il dominio interno a Hummingbot.

PRD-04 decide cosa va fatto e scrive comandi neutri in `ops_execution_commands`. PRD-05 legge quei comandi, li traduce verso un adapter concreto, aggiorna lo stato del comando e normalizza gli eventi di ritorno in `ops_exchange_events`.

```
PRD-04 Lifecycle
  -> ops_execution_commands PENDING
  -> ExecutionCommandWorker
  -> ExecutionGateway
  -> ExecutionAdapter
  -> Hummingbot API paper
       -> raw orders oppure V2 Executors
  -> update ops_execution_commands
  -> ExchangeEventSyncWorker
  -> ops_exchange_events NEW
  -> PRD-04 LifecycleEventWorker
```

Regola guida:

```
Lifecycle decide.
Execution Gateway traduce e invia.
Adapter parla con l'executor concreto.
Exchange resta la verita finale.
```

---

## 2. Principi di design

- PRD-05 non e un PRD Hummingbot. E un Execution Gateway neutro con Hummingbot API paper come primo adapter concreto.
- Ogni executor futuro richiede un nuovo adapter e config, ma non deve richiedere modifiche a parser, PRD-03, PRD-04 o al contratto DB canonico.
- Hummingbot CLI/config non e il canale tecnico del bot. Il canale tecnico MVP e Hummingbot API REST/Python client.
- Hummingbot V2 Executors sono una capability dell'adapter Hummingbot, non il modello interno del sistema.
- Tutti i comandi PRD-04 vengono accettati dal gateway. Se un adapter non supporta una capability richiesta, il gateway deve registrare una decisione esplicita invece di improvvisare.
- Nessun live trading e abilitabile nel MVP.

---

## 3. Responsabilita

### 3.1 Lifecycle PRD-04

Responsabilita gia esistenti:

- crea `ops_execution_commands`;
- decide lifecycle, risk, capacity e management plan;
- non importa Hummingbot;
- non conosce API executor;
- consuma `ops_exchange_events` tramite `LifecycleEventWorker`.

### 3.2 ExecutionCommandWorker

Nuovo worker PRD-05.

Responsabilita:

- legge `ops_execution_commands` in stato `PENDING`;
- applica retry/idempotenza;
- chiama `ExecutionGateway`;
- non decide policy trading;
- non aggiorna direttamente `ops_trade_chains`.

Query base:

```sql
SELECT *
FROM ops_execution_commands
WHERE status = 'PENDING'
ORDER BY created_at
LIMIT 100;
```

### 3.3 ExecutionGateway

Responsabilita:

- valida comando neutro e payload;
- carica `config/execution.yaml`;
- risolve `account_id -> adapter/execution_account_id/connector`;
- controlla capability adapter;
- genera o valida `client_order_id`;
- invia il comando all'adapter;
- aggiorna `ops_execution_commands.status` e result payload;
- registra eventi tecnici normalizzati quando opportuno.

### 3.4 ExecutionAdapter

Interfaccia stabile per executor concreti.

Responsabilita:

- traduce comando neutro verso API concreta;
- restituisce result strutturato;
- espone capability;
- non decide risk, TP distribution, lifecycle o policy trader.

### 3.5 HummingbotApiPaperAdapter

Primo adapter concreto.

Responsabilita:

- usa Hummingbot API REST/Python client;
- lavora in paper mode;
- supporta `raw_order_mode` come default;
- puo usare Hummingbot V2 Executors in `executor_mode` quando compatibile e abilitato;
- non abilita live trading.

### 3.6 ExchangeEventSyncWorker

Nuovo worker PRD-05.

Responsabilita:

- sincronizza ordini, trade/fill, posizioni ed executor status da Hummingbot API;
- MVP tramite polling REST;
- normalizza in `ops_exchange_events`;
- non aggiorna direttamente lifecycle.

---

## 4. Package structure

```
src/runtime_v2/execution_gateway/
    __init__.py
    models.py                    <- command/result/event/config Pydantic models
    config_loader.py             <- ExecutionConfigLoader
    adapters.py                  <- ExecutionAdapter ABC, AdapterCapabilities
    gateway.py                   <- ExecutionGateway
    command_worker.py            <- ExecutionCommandWorker
    event_sync.py                <- ExchangeEventSyncWorker + event source port
    repositories.py              <- ExecutionCommandRepository extensions if needed
    client_order_id.py           <- deterministic client_order_id builder/parser
    adapters/
        __init__.py
        fake.py                  <- fake adapter for tests
        hummingbot_api_paper.py  <- Hummingbot API paper adapter

config/
    execution.yaml

tests/runtime_v2/execution_gateway/
    test_config_loader.py
    test_client_order_id.py
    test_gateway.py
    test_command_worker.py
    test_event_sync.py
    test_hummingbot_api_paper_adapter.py
    test_integration.py
```

---

## 5. Config account/executor

PRD-05 usa config globale per account/executor, non override per trader.

Il trader influenza segnali, rischio e management plan nei layer precedenti. PRD-05 decide come eseguire tecnicamente un comando su un account/executor.

File:

```text
config/execution.yaml
```

Esempio:

```yaml
execution:
  default_adapter: hummingbot_api_paper

  account_routing:
    default:
      adapter: hummingbot_api_paper
      execution_account_id: bybit_paper_main

    acc_trader_a:
      adapter: hummingbot_bybit_paper_a
      execution_account_id: bybit_paper_a

  adapters:
    hummingbot_api_paper:
      type: hummingbot_api
      mode: paper
      base_url: http://localhost:8000
      account_id: main_paper
      connector: bybit_perpetual_paper_trade
      execution_account_id: bybit_paper_main

      execution_mode: raw_order
      allow_executor_mode_if_supported: true
      preferred_executor_type: position_executor

      capabilities:
        place_entry: true
        protective_stop_native: true
        take_profit_native: true
        bracket_order: false
        move_stop: true
        close_partial: true
        close_full: true
        executor_position: true

      protection:
        stop_loss_mode: exchange_native_required
        take_profit_mode: wait_for_position

      take_profit:
        placement_policy: wait_for_position
        allow_bracket_if_supported: true
        allow_expected_size_preplacement: false
        min_order_policy: review
        residual_policy: assign_to_last_tp

      position_management:
        same_symbol_same_side_policy: block
        same_symbol_opposite_side_policy: allow_if_hedge_mode
        require_client_order_id_correlation: true

      live_safety:
        allow_live_trading: false
        allow_executor_managed_stop: false
        allow_expected_size_take_profit: false
```

Routing rule:

```
PRD-04 assegna account_id.
PRD-05 risolve account_id -> adapter/execution_account_id/connector.
```

Supporto account:

- account exchange unico: piu `account_id` interni possono puntare allo stesso adapter;
- account separati: ogni `account_id` punta a un adapter diverso;
- stesso exchange con credenziali diverse: adapter/config entry diverse;
- exchange diversi: adapter diversi con connector diverso;
- il trader non sceglie direttamente l'exchange in PRD-05.

---

## 6. Command types supportati

PRD-05 deve accettare tutti i command type prodotti da PRD-04:

```
PLACE_ENTRY
PLACE_PROTECTIVE_STOP
PLACE_TAKE_PROFIT
MOVE_STOP_TO_BREAKEVEN
MOVE_STOP
CANCEL_PENDING_ENTRY
CLOSE_PARTIAL
CLOSE_FULL
```

Regole:

- il gateway valida tutti i command type;
- l'adapter prova la traduzione solo se dichiara capability adeguata;
- capability mancante prima dell'invio -> `REVIEW_REQUIRED`;
- errore tecnico dopo tentativo -> `FAILED`;
- nessun fallback silenzioso;
- nessuna approssimazione rischiosa, specialmente per ordini protettivi.

---

## 7. SL, TP multipli e volume

### 7.1 Stop loss

Policy MVP:

```
PLACE_PROTECTIVE_STOP richiede protective_stop_native=true.
```

Se non disponibile:

- comando `REVIEW_REQUIRED`;
- reason `protective_stop_native_required`;
- nessuna conversione automatica in executor-managed stop.

Rationale: se lo stop vive solo nel bot e Hummingbot/API si ferma, la posizione puo restare senza protezione reale.

### 7.2 Take profit multipli

I TP multipli sono un piano di uscita a quote.

PRD-05 non decide quanto chiudere. La distribuzione arriva dal payload neutro generato da PRD-04.

Esempio payload:

```json
{
  "symbol": "BTC-USDT",
  "side": "LONG",
  "tp_sequence": 1,
  "price": 68000,
  "close_pct": 40,
  "reduce_only": true
}
```

Regole:

- ogni TP e un ordine/comando distinto con `tp_sequence`;
- tutti i TP devono essere `reduce_only`;
- somma quote per chain <= 100%;
- quantita reale calcolata su filled qty quando disponibile;
- ultimo TP assorbe residui di arrotondamento;
- TP sotto `min_order_size` -> `REVIEW_REQUIRED`, salvo futura policy esplicita di merge;
- expected-size preplacement disabilitato di default.

### 7.3 TP placement policy

Default:

```
take_profit.placement_policy = wait_for_position
```

Comportamento:

- `PLACE_TAKE_PROFIT` resta `WAITING_POSITION` finche non arriva fill reale;
- dopo `ENTRY_FILLED`, `ExchangeEventSyncWorker` normalizza l'evento;
- `LifecycleEventWorker` aggiorna stato lifecycle;
- PRD-05 puo eseguire i TP usando quantita reale confermata.

Ottimizzazione:

- se `bracket_order=true` e `allow_bracket_if_supported=true`, l'adapter puo usare bracket/OCO nativo;
- se `executor_position=true` e chain compatibile, l'adapter puo usare Hummingbot V2 `PositionExecutor`.

Solo paper/test:

- `allow_expected_size_preplacement=true` puo piazzare TP su expected size;
- non ammesso in live safety MVP.

---

## 8. Hummingbot V2 Executors

Hummingbot V2 Executors sono utili per workflow finiti come entry/exit/hedge. Il `PositionExecutor` puo gestire posizione con stop loss, take profit, time limit e trailing stop.

PRD-05 li tratta come capability adapter.

Modalita:

```
raw_order_mode
  PLACE_ENTRY -> ordine diretto
  PLACE_PROTECTIVE_STOP -> ordine stop nativo
  PLACE_TAKE_PROFIT -> ordine reduce-only
  MOVE_STOP -> cancel/replace

executor_mode
  crea Hummingbot V2 Executor compatibile, es. PositionExecutor
  sincronizza stato executor -> ops_exchange_events
```

Regole:

- default `raw_order_mode`;
- `executor_mode` ammesso solo se config e capability lo consentono;
- il DB salva sempre eventi normalizzati nostri;
- lifecycle non dipende da Hummingbot executor internals.

---

## 9. Posizioni multiple

La gestione deve restare centrata su `trade_chain_id`.

```
Una posizione operativa interna = una ops_trade_chains.
Ogni ordine/comando/evento deve essere ricondotto a trade_chain_id.
```

L'exchange spesso aggrega per account/symbol/side. Per evitare ambiguita, PRD-05 impone client order id strutturati:

```text
tsb:<trade_chain_id>:<command_id>:<role>:<sequence>
```

Esempi:

```text
tsb:42:1001:entry:1
tsb:42:1002:sl:1
tsb:42:1003:tp:1
tsb:42:1004:tp:2
```

Policy MVP:

```yaml
position_management:
  same_symbol_same_side_policy: block
  same_symbol_opposite_side_policy: allow_if_hedge_mode
  require_client_order_id_correlation: true
```

Regole:

- piu posizioni su simboli diversi: ok;
- stesso symbol e side opposto: ok solo se hedge mode/account/adapter lo supportano;
- stesso symbol e stesso side: default `block`;
- se un comando creerebbe ambiguita non supportata, va in `REVIEW_REQUIRED`;
- allocation ledger per piu chain same-side aggregate e fuori scope MVP.

---

## 10. Stati comando

PRD-05 estende gli stati gestiti da `ops_execution_commands`:

```
PENDING
SENT
ACK
WAITING_POSITION
DONE
FAILED
REVIEW_REQUIRED
CANCELLED
```

Significato:

| Stato | Significato |
|---|---|
| `PENDING` | Comando creato da PRD-04, non ancora inviato |
| `SENT` | Richiesta inviata all'adapter/API |
| `ACK` | Hummingbot/exchange ha accettato comando o creato ordine/executor |
| `WAITING_POSITION` | Comando dipende da fill reale, tipico TP multipli |
| `DONE` | Comando completato o evento finale confermato |
| `FAILED` | Errore tecnico o rifiuto adapter/API/exchange |
| `REVIEW_REQUIRED` | Gateway non deve provare a eseguire per capability/config/stato non sicuro |
| `CANCELLED` | Comando annullato da lifecycle o sostituito |

Regole:

- capability mancante prima dell'invio -> `REVIEW_REQUIRED`;
- errore API temporaneo -> retry, poi `FAILED`;
- ordine rigettato da exchange -> `FAILED` + `ORDER_REJECTED`;
- comando gia inviato con stesso `idempotency_key`/`client_order_id` -> recupera stato, non reinvia;
- TP prima del fill -> `WAITING_POSITION`;
- posizione same-side bloccata da policy -> `REVIEW_REQUIRED`.

---

## 11. Eventi normalizzati

Eventi minimi in `ops_exchange_events`:

```
COMMAND_ACK
COMMAND_REJECTED
ORDER_CREATED
ORDER_FILLED
ORDER_PARTIALLY_FILLED
ORDER_CANCELLED
ORDER_REJECTED
POSITION_OPENED
POSITION_UPDATED
POSITION_CLOSED
STOP_FILLED
TAKE_PROFIT_FILLED
EXECUTOR_CREATED
EXECUTOR_COMPLETED
EXECUTOR_FAILED
SYNC_WARNING
```

Ogni evento deve includere nel payload, quando disponibile:

- `trade_chain_id`;
- `command_id`;
- `adapter`;
- `execution_account_id`;
- `connector`;
- `client_order_id`;
- `adapter_order_id`;
- `exchange_order_id`;
- `executor_id`;
- `symbol`;
- `side`;
- `role`;
- `sequence`;
- `filled_qty`;
- `price`;
- `reason`.

Idempotency key preferita:

```text
adapter + execution_account_id + exchange_order_id + event_type + fill_id
```

Fallback:

```text
adapter + command_id + event_type + payload_hash
```

---

## 12. Event sync

MVP usa polling Hummingbot API.

Rationale:

- la doc Hummingbot API espone endpoint REST per ordini, trade/fill e posizioni;
- Hummingbot ha componenti real-time interni, ma non si assume un WebSocket/MQTT pubblico stabile come consumer esterno PRD-05;
- polling e piu semplice da testare e sufficiente per paper mode.

Interfaccia:

```python
class ExchangeEventSourcePort:
    def fetch_order_updates(...) -> list[AdapterEvent]: ...
    def fetch_trade_updates(...) -> list[AdapterEvent]: ...
    def fetch_position_updates(...) -> list[AdapterEvent]: ...
    def fetch_executor_updates(...) -> list[AdapterEvent]: ...
```

Implementazione MVP:

```text
HummingbotApiPollingEventSource
```

Estensioni future:

```text
HummingbotMqttEventSource
WebSocketEventSource
```

---

## 13. DB e migrazioni

PRD-05 puo richiedere una migration su `ops_execution_commands` per aggiungere campi utili all'esecuzione:

```sql
ALTER TABLE ops_execution_commands ADD COLUMN adapter TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN execution_account_id TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN client_order_id TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN result_payload_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE ops_execution_commands ADD COLUMN sent_at TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN acknowledged_at TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN completed_at TEXT;
ALTER TABLE ops_execution_commands ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ops_execution_commands ADD COLUMN next_retry_at TEXT;
```

Indici:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_oec_client_order_id
    ON ops_execution_commands(client_order_id)
    WHERE client_order_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_oec_status_retry
    ON ops_execution_commands(status, next_retry_at);
```

Nota: la forma finale della migration va allineata al repository prima dell'implementazione.

---

## 14. Error handling e retry

Classi di errore:

| Classe | Stato | Esempi |
|---|---|---|
| Config/capability non sicura | `REVIEW_REQUIRED` | capability mancante, account routing assente, live mode vietato |
| Errore temporaneo API | retry -> `FAILED` | timeout, 5xx, connessione API non disponibile |
| Rifiuto executor/exchange | `FAILED` | ordine rigettato, parametri invalidi |
| Stato non coerente | `REVIEW_REQUIRED` | posizione non trovata, qty non calcolabile, same-side bloccato |
| Duplicato idempotente | recupero stato | stesso `client_order_id` gia visto |

Retry policy MVP:

- max retry configurabile;
- backoff semplice;
- nessun retry per errori di capability/config;
- ogni retry aumenta `retry_count`;
- errori finali devono lasciare reason in `result_payload_json`.

---

## 15. Acceptance contract

Done significa:

```
PRD-05 consuma comandi neutri PRD-04,
li instrada a un adapter configurabile,
supporta Hummingbot API paper,
sincronizza eventi reali/simulati via polling,
e produce eventi normalizzati consumabili dal LifecycleEventWorker.
```

Criteri pass/fail:

1. Tutti i command type PRD-04 sono accettati dal gateway.
2. Nessun comando Hummingbot-specific entra nel contratto canonico.
3. `account_id` viene risolto tramite `account_routing`.
4. Exchange/connector e account esecutivo sono configurabili.
5. Ogni comando inviato ha `client_order_id` tracciabile.
6. Doppio run non reinvia lo stesso comando.
7. Capability mancante produce `REVIEW_REQUIRED`.
8. Errore tecnico adapter produce retry e poi `FAILED`.
9. SL protettivo richiede capability nativa.
10. TP multipli default `WAITING_POSITION` finche non esiste fill reale.
11. Residuo arrotondamenti TP viene assegnato all'ultimo TP.
12. Posizione same symbol/same side e bloccata di default.
13. Hedge e ammesso solo se account/adapter lo supportano.
14. Hummingbot API paper adapter non abilita live trading.
15. ExchangeEventSyncWorker produce `ops_exchange_events` idempotenti.
16. Hummingbot V2 Executor mode e opzionale e capability-driven.
17. Lifecycle PRD-04 non importa execution gateway adapter concreti.

---

## 16. Test minimi

### Unit

- `ExecutionConfigLoader` valida config completa.
- Account routing: default, account specifico, account mancante.
- Capability checks per tutti i command type.
- `client_order_id` builder/parser round-trip.
- Policy same symbol/same side -> block.
- TP residual assignment su ultimo TP.
- TP sotto min order -> review.
- Live mode vietato in MVP.

### Integration con fake adapter

- `PLACE_ENTRY` passa `PENDING -> SENT -> ACK`.
- Capability mancante -> `REVIEW_REQUIRED`.
- Adapter timeout -> retry -> `FAILED`.
- Doppio worker run non reinvia comando.
- Fake fill -> `ops_exchange_events ORDER_FILLED`.
- TP multipli restano `WAITING_POSITION` prima del fill.
- Dopo fill reale, TP produce ordini reduce-only con qty coerenti.

### Hummingbot API paper gated

Questi test girano solo con env var esplicita, per esempio:

```text
RUN_HUMMINGBOT_API_TESTS=1
HUMMINGBOT_API_URL=http://localhost:8000
```

Copertura:

- health/API disponibile;
- adapter legge capability/config;
- crea ordine paper o executor compatibile;
- legge ordini/trades/posizioni tramite polling;
- normalizza almeno un evento.

---

## 17. Traccia estensioni future

Questi punti sono fuori scope MVP, ma PRD-05 lascia una traccia esplicita.

### 17.1 Live trading reale

Richiede:

- config `mode: live` con opt-in esplicito;
- kill switch operativo;
- review gate per primo ordine live;
- secret management robusto;
- reconciliation minima attiva;
- test su exchange testnet;
- audit di risk e protection order prima di abilitare size reale.

### 17.2 WebSocket/MQTT event source

Richiede:

- contratto evento esterno stabile;
- idempotenza equivalente al polling;
- reconnect/backfill;
- fallback polling;
- test per out-of-order e duplicate events.

### 17.3 Reconciliation completa

Richiede:

- confronto periodico DB interno vs Hummingbot state vs exchange state;
- detection di ordini mancanti, fill non visti, posizioni manualmente modificate;
- eventi `RECONCILIATION_WARNING` / `RECONCILIATION_FIX_REQUIRED`;
- policy per riparazioni automatiche vs review.

### 17.4 Allocation ledger per piu chain same-side aggregate

Richiede:

- ledger interno di quote per `account_id + symbol + side`;
- ripartizione fill/PnL/fees tra chain;
- gestione partial close proporzionale o per-chain;
- riconciliazione con posizione exchange aggregata;
- test estesi su fill parziali e chiusure manuali.

### 17.5 UI/dashboard

Richiede:

- viste command queue, adapter status, open positions, sync warnings;
- azioni manuali protette;
- audit trail leggibile;
- permessi/admin.

### 17.6 Secret management avanzato

Richiede:

- secret references invece di valori in YAML;
- supporto vault/keyring/env provider;
- redaction log;
- rotazione credenziali;
- policy per impedire leak in test, fixture e report.

---

## 18. Decisioni e verifiche prima dell'implementazione

Decisioni fissate:

1. La migration PRD-05 si chiamera `029_ops_execution_gateway.sql`.
2. `REVIEW_REQUIRED` e uno stato esplicito di `ops_execution_commands`, non un `FAILED` con reason.

Verifiche da fare durante il piano implementativo:

1. Forma finale del payload neutro per ogni command type, leggendo i payload effettivi prodotti da PRD-04.
2. Endpoint Hummingbot API esatti da usare per raw order mode e per executor mode.

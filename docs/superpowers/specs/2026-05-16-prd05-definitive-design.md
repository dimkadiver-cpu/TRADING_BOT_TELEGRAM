# PRD-05 — Execution Gateway: Design Definitivo

**Data:** 2026-05-16
**Stato:** approvato
**Deriva da:** PRD-05 draft (2026-05-15) + review dettagliata con gap analysis
**Ambito:** Execution Gateway neutro, HummingbotApiPaperAdapter, ExchangeEventSyncWorker, FillBridge

---

## 1. Scopo e principi

PRD-05 collega il lifecycle stateful (PRD-04) a un executor reale o simulato senza accoppiare il dominio interno a Hummingbot.

```
Lifecycle decide.
Execution Gateway traduce e invia.
Adapter parla con l'executor concreto.
Exchange è la verità finale.
```

PRD-04 scrive comandi neutri in `ops_execution_commands`. PRD-05 li legge, li traduce verso un adapter concreto, aggiorna lo stato del comando e normalizza gli eventi di ritorno in `ops_exchange_events`.

```
PRD-04 Lifecycle
  → ops_execution_commands PENDING
  → ExecutionCommandWorker
  → ExecutionGateway
  → ExecutionAdapter
  → Hummingbot API paper
  → update ops_execution_commands
  → ExchangeEventSyncWorker
  → ops_exchange_events NEW
  → PRD-04 LifecycleEventWorker (invariato)
```

---

## 2. Decisioni di design fissate

| # | Decisione |
|---|---|
| D1 | `CommandStatus` in `lifecycle/models.py` viene esteso con `WAITING_POSITION` e `REVIEW_REQUIRED` — unico source of truth |
| D2 | Il vocabolario eventi in `ops_exchange_events` resta PRD-04 nativo (`ENTRY_FILLED`, `TP_FILLED`, `SL_FILLED`) — il sync worker normalizza internamente |
| D3 | `WAITING_POSITION → PENDING` è triggerato da `ExecutionCommandWorker` via query su chain in stato `OPEN` — zero modifiche a PRD-04 event processor |
| D4 | `executor_mode` è fuori scope MVP — l'adapter dichiara `executor_position: false`, nessun codice executor da scrivere |
| D5 | Entry execution mode è configurabile: `a_sequential`, `b_entry_stop_then_tp` (default), `c_bracket` |
| D6 | TP multipli usano sempre `reduce_only=true` come ordini separati in tutti e tre i mode |
| D7 | `c_bracket` con TP multipli fa downgrade automatico a `b` con warning in `result_payload_json` |
| D8 | Leva configurata sull'adapter — non influenza il calcolo qty che arriva già da PRD-04 |
| D9 | MVP: polling smart su ordini attivi; Upgrade: FillBridge script dentro Hummingbot |
| D10 | PRD-04 non importa nulla di execution_gateway — isolamento garantito |

---

## 3. Architettura e responsabilità

### Flusso completo

```
ops_execution_commands (PENDING / WAITING_POSITION)
          ↓
ExecutionCommandWorker
  query 1: status=PENDING
  query 2: status=SENT, next_retry_at <= now()
  query 3: status=WAITING_POSITION JOIN chain WHERE lifecycle_state=OPEN
          ↓
ExecutionGateway
  - valida payload neutro
  - risolve account_id → adapter via execution.yaml
  - verifica capability adapter
  - controlla same-symbol/same-side policy
  - imposta leverage sul connector (prima invio)
  - genera client_order_id: tsb:<chain_id>:<cmd_id>:<role>:<seq>
  - aggiorna status comando
          ↓
HummingbotApiPaperAdapter
  - raw_order_mode (MVP)
  - executor_mode: fuori scope MVP
          ↓
Hummingbot paper trading (localhost:8000)
          ↓
ExchangeEventSyncWorker
  [MVP]     polling smart su ordini attivi in ops_execution_commands
  [Upgrade] FillBridge script dentro Hummingbot (event bus interno)
          ↓
ops_exchange_events (ENTRY_FILLED / TP_FILLED / SL_FILLED)
          ↓
PRD-04 LifecycleEventWorker (invariato)
```

### Responsabilità per componente

| Componente | Fa | Non fa |
|---|---|---|
| `ExecutionCommandWorker` | legge PENDING/retry/WAITING_POSITION, gestisce retry | decide policy trading |
| `ExecutionGateway` | valida, risolve config, capability check, leverage, client_order_id | conosce Hummingbot API |
| `HummingbotApiPaperAdapter` | traduce comandi neutri in REST Hummingbot | decide lifecycle, risk, TP distribution |
| `ExchangeEventSyncWorker` | correla fill a trade_chain via client_order_id, normalizza eventi | aggiorna lifecycle direttamente |

---

## 4. Entry execution mode

Configurabile per adapter in `execution.yaml`:

```yaml
entry_execution:
  mode: b_entry_stop_then_tp
  # a_sequential         → entry → fill → stop + tutti i TP
  # b_entry_stop_then_tp → entry + stop subito, TP dopo fill (default)
  # c_bracket            → entry + stop + TP1 OCO, TP2..N dopo fill
```

### Sequencing per mode

```
MODE A — sequential
  1. PLACE_ENTRY → SENT → ACK
  2. [ENTRY_FILLED]
  3. PLACE_PROTECTIVE_STOP → SENT (qty reale)
  4. PLACE_TAKE_PROFIT x N → SENT (qty reale, reduce_only)

MODE B — default MVP
  1. PLACE_ENTRY → SENT → ACK
  2. PLACE_PROTECTIVE_STOP → SENT subito (qty attesa, reduce_only=true)
  3. [ENTRY_FILLED]
  4. PLACE_TAKE_PROFIT x N → SENT (qty reale, reduce_only)

MODE C — bracket
  1. PLACE_ENTRY + STOP + TP1 → OCO nativo → SENT
  2. [ENTRY_FILLED]
  3. PLACE_TAKE_PROFIT TP2..N → SENT (qty reale, reduce_only)
  * downgrade automatico a B se TP multipli + warning in result_payload_json
```

### Timing ordini per mode

```
                    | stop timing   | TP1 timing     | TP2..N timing
--------------------|---------------|----------------|---------------
a_sequential        | dopo fill     | dopo fill      | dopo fill
b_entry_stop_then_tp| subito        | dopo fill      | dopo fill
c_bracket           | subito (OCO)  | subito (OCO)   | dopo fill
```

---

## 5. Gestione ordini — command type per command type

### Payload neutro (contratto PRD-04 → PRD-05)

```python
PLACE_ENTRY:
  symbol: str, side: str
  entry_type: "LIMIT" | "MARKET"
  price: float | None          # None se MARKET
  qty: float                   # calcolato da risk engine PRD-04
  sequence: int                # 1..N per averaging/ladder

PLACE_PROTECTIVE_STOP:
  symbol: str, side: str
  stop_price: float
  qty: float                   # qty attesa (risk engine)
  reduce_only: true

PLACE_TAKE_PROFIT:
  symbol: str, side: str
  tp_sequence: int             # 1..N
  price: float
  close_pct: float             # % della posizione da chiudere
  reduce_only: true

MOVE_STOP_TO_BREAKEVEN:
  symbol: str, side: str
  target_price: float          # entry_avg_price
  be_buffer_pct: float         # 0.0 = esatto breakeven

MOVE_STOP:
  symbol: str, side: str
  new_stop_price: float

CANCEL_PENDING_ENTRY:
  symbol: str, side: str
  # gateway risolve client_order_id dall'entry command della chain

CLOSE_PARTIAL:
  symbol: str, side: str
  close_pct: float             # gateway chiede qty corrente a Hummingbot

CLOSE_FULL:
  symbol: str, side: str      # gateway chiede qty corrente a Hummingbot
```

### Mapping verso Hummingbot API

```python
PLACE_ENTRY (LIMIT):
  POST /trading/orders
  { order_type: LIMIT, trade_type: BUY|SELL,
    price: ..., amount: qty, position_action: OPEN }

PLACE_ENTRY (MARKET):
  POST /trading/orders
  { order_type: MARKET, trade_type: BUY|SELL,
    amount: qty, position_action: OPEN }

PLACE_PROTECTIVE_STOP:
  POST /trading/orders
  { order_type: STOP_LOSS, trade_type: SELL|BUY,
    price: stop_price, amount: qty,
    position_action: CLOSE, reduce_only: true }

PLACE_TAKE_PROFIT:
  POST /trading/orders
  { order_type: LIMIT, trade_type: SELL|BUY,
    price: tp_price, amount: qty_calcolata,
    position_action: CLOSE, reduce_only: true }

MOVE_STOP / MOVE_STOP_TO_BREAKEVEN:
  POST /trading/.../cancel  (stop esistente)
  POST /trading/orders      (nuovo stop)

CANCEL_PENDING_ENTRY:
  POST /trading/{account}/{connector}/orders/{client_order_id}/cancel

CLOSE_PARTIAL / CLOSE_FULL:
  GET  /trading/positions   (qty corrente)
  POST /trading/orders      (MARKET, reduce_only, qty calcolata)

LEVERAGE (setup iniziale):
  POST /trading/leverage
  { connector: ..., trading_pair: ..., leverage: N }
```

### Qty per TP multipli — calcolo e residuo

```python
total = filled_qty   # da ENTRY_FILLED event

tp_qtys = []
allocated = 0.0
for i, tp in enumerate(sorted_tps):
    if i == len(sorted_tps) - 1:
        qty = round(total - allocated, qty_precision)  # ultimo assorbe residuo
    else:
        qty = round(total * tp.close_pct / 100, qty_precision)
        allocated += qty
    if qty < min_order_size:
        → REVIEW_REQUIRED, reason: tp_qty_below_min
    tp_qtys.append(qty)
```

---

## 6. Account routing

```yaml
account_routing:
  default:
    adapter: hummingbot_api_paper
    execution_account_id: bybit_paper_main

  acc_trader_a:               # override per trader specifico
    adapter: hummingbot_api_paper
    execution_account_id: bybit_paper_trader_a
```

Risoluzione:

```python
def resolve_adapter(self, account_id: str) -> AdapterConfig:
    routing = self.config.account_routing
    return routing.get(account_id) or routing["default"]
```

Supporta:
- **Tutti su main**: solo `default` nel config
- **Ogni fonte su account proprio**: un entry per ogni `acc_*`
- **Misto**: `default` + override per trader dedicati
- **Exchange diversi**: adapter diversi con connector diverso

---

## 7. CommandStatus — state machine

### Estensione lifecycle/models.py

```python
CommandStatus = Literal[
    "PENDING",           # creato da PRD-04, non ancora inviato
    "SENT",              # richiesta inviata all'adapter
    "ACK",               # exchange ha accettato l'ordine
    "WAITING_POSITION",  # attende fill reale (TP multipli prima di entry fill)
    "DONE",              # ordine completato / fill confermato
    "FAILED",            # errore terminale
    "REVIEW_REQUIRED",   # richiede intervento manuale
    "CANCELLED",         # annullato da lifecycle o sostituito
]
```

### Transizioni

```
PENDING ──────────────► SENT ──────────► ACK ──────────► DONE
   │                      │
   │                 [errore API]
   │                      │
   │              retry_count < max → next_retry_at → torna SENT
   │              retry_count >= max → FAILED
   │
   ├──► WAITING_POSITION → (chain OPEN) → PENDING → SENT
   │
   ├──► REVIEW_REQUIRED  (nessun retry)
   │
   └──► CANCELLED
```

### Classi di errore

| Situazione | Stato | Retry |
|---|---|---|
| Capability mancante | `REVIEW_REQUIRED` | no |
| Account routing assente | `REVIEW_REQUIRED` | no |
| Same-side bloccato | `REVIEW_REQUIRED` | no |
| Qty non calcolabile | `REVIEW_REQUIRED` | no |
| Live mode vietato | `REVIEW_REQUIRED` | no |
| Timeout API / 5xx | `SENT` + `next_retry_at` | sì, max configurabile |
| Rifiuto exchange (4xx) | `FAILED` | no |
| Retry esauriti | `FAILED` | no |
| Idempotente (già inviato) | recupera stato esistente | no |

### Retry policy

```python
MAX_RETRY = 3                    # configurabile per adapter
BACKOFF = [30, 90, 300]          # secondi

# Query retry (seconda query del worker)
SELECT * FROM ops_execution_commands
WHERE status = 'SENT'
  AND next_retry_at IS NOT NULL
  AND next_retry_at <= datetime('now')
ORDER BY next_retry_at
LIMIT 100
```

### WAITING_POSITION → PENDING (terza query del worker)

```sql
SELECT c.* FROM ops_execution_commands c
JOIN ops_trade_chains t ON c.trade_chain_id = t.trade_chain_id
WHERE c.status = 'WAITING_POSITION'
  AND t.lifecycle_state = 'OPEN'
ORDER BY c.created_at
LIMIT 100
```

Zero modifiche a PRD-04.

---

## 8. ExchangeEventSyncWorker

### Responsabilità

Correla i raw events di Hummingbot ai `trade_chain_id` interni e li normalizza nel vocabolario PRD-04.

La correlazione avviene tramite `client_order_id`:

```
tsb:42:1001:entry:1
    ↑   ↑     ↑    ↑
  chain cmd  role  seq

→ trade_chain_id = 42
→ role = entry → ENTRY_FILLED
→ role = sl    → SL_FILLED
→ role = tp    → TP_FILLED (con is_final calcolato)
```

### MVP — Polling smart su ordini attivi

```python
def run_once(self):
    # Solo ordini in volo — non sweep globale
    active = self._cmd_repo.get_sent_or_ack()

    for cmd in active:
        coid = parse_client_order_id(cmd.client_order_id)
        raw = self._adapter.get_order_status(coid)

        if raw.is_filled:
            event = self._normalize(coid, raw)
            self._event_repo.save(event)  # INSERT OR IGNORE
```

### Normalizzazione

```python
def _normalize(self, coid, raw) -> ExchangeEvent:
    if coid.role == "entry":
        event_type = "ENTRY_FILLED"
        payload = {"fill_price": raw.average_price,
                   "filled_qty": raw.filled_qty}

    elif coid.role == "sl":
        event_type = "SL_FILLED"
        payload = {"fill_price": raw.average_price}

    elif coid.role == "tp":
        remaining = self._cmd_repo.count_active_tps(coid.trade_chain_id)
        is_final = remaining == 1
        event_type = "TP_FILLED"
        payload = {"tp_level": coid.sequence,
                   "is_final": is_final,
                   "fill_price": raw.average_price,
                   "filled_qty": raw.filled_qty}

    return ExchangeEvent(
        trade_chain_id=coid.trade_chain_id,
        event_type=event_type,
        payload_json=json.dumps(payload),
        idempotency_key=f"{event_type}:{coid.trade_chain_id}:{raw.exchange_order_id}",
    )
```

### Upgrade — FillBridge script dentro Hummingbot

Un file Python (~60 righe) che gira come Script V2 dentro Hummingbot:

```python
# hummingbot_scripts/fill_bridge.py
class FillBridge(ScriptStrategyBase):
    def on_order_filled(self, event: OrderFilledEvent):
        if not event.client_order_id.startswith("tsb:"):
            return  # ignora ordini non nostri

        conn = sqlite3.connect(OPS_DB_PATH)
        conn.execute("""
            INSERT OR IGNORE INTO ops_exchange_events
            (trade_chain_id, event_type, payload_json,
             processing_status, idempotency_key, received_at)
            VALUES (?, ?, ?, 'NEW', ?, ?)
        """, (
            parse_chain_id(event.client_order_id),
            map_role_to_event_type(event.client_order_id),
            json.dumps({"fill_price": float(event.price),
                        "filled_qty": float(event.amount),
                        "exchange_order_id": event.exchange_order_id}),
            f"fill:{event.exchange_order_id}",
            datetime.utcnow().isoformat(),
        ))
        conn.commit()
```

Zero latency. Drop-in: non modifica gateway né DB.

Il polling smart rimane attivo come fallback anche con FillBridge.

### Confronto strategie sync

```
                  Latenza    Complessità    Infrastruttura extra
Polling smart     1-5s       bassa          nessuna (MVP)
FillBridge        <100ms     bassa          script dentro Hummingbot
MQTT broker       <100ms     alta           broker separato (futuro)
```

---

## 9. client_order_id

```
tsb:<trade_chain_id>:<command_id>:<role>:<sequence>

Esempi:
  tsb:42:1001:entry:1
  tsb:42:1002:sl:1
  tsb:42:1003:tp:1
  tsb:42:1004:tp:2
  tsb:42:1005:tp:3
```

Ruoli validi: `entry`, `sl`, `tp`

Il `client_order_id` è deterministico — garantisce idempotenza su doppio run.

---

## 10. DB Migration — `029_ops_execution_gateway.sql`

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

CREATE UNIQUE INDEX IF NOT EXISTS idx_oec_client_order_id
    ON ops_execution_commands(client_order_id)
    WHERE client_order_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_oec_retry
    ON ops_execution_commands(status, next_retry_at)
    WHERE status = 'SENT' AND next_retry_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_oec_waiting
    ON ops_execution_commands(status)
    WHERE status = 'WAITING_POSITION';
```

### result_payload_json schema

```json
{
  "adapter_order_id": "hb-12345",
  "exchange_order_id": "bybit-99999",
  "fill_price": 50050.0,
  "filled_qty": 0.02,
  "error": null,
  "reason": null,
  "warnings": []
}
```

---

## 11. execution.yaml — config completo

```yaml
execution:
  default_adapter: hummingbot_api_paper

  account_routing:
    default:
      adapter: hummingbot_api_paper
      execution_account_id: bybit_paper_main

    # Esempio account dedicato per trader specifico:
    # acc_trader_a:
    #   adapter: hummingbot_api_paper
    #   execution_account_id: bybit_paper_trader_a

  adapters:
    hummingbot_api_paper:
      type: hummingbot_api
      mode: paper
      base_url: http://localhost:8000
      connector: bybit_perpetual_paper_trade
      leverage: 1

      entry_execution:
        mode: b_entry_stop_then_tp
        # a_sequential         → entry → fill → stop + tutti i TP
        # b_entry_stop_then_tp → entry + stop subito, TP dopo fill (default)
        # c_bracket            → entry + stop + TP1 OCO, TP2..N dopo fill

      retry:
        max_attempts: 3
        backoff_seconds: [30, 90, 300]

      capabilities:
        place_entry: true
        protective_stop_native: true
        take_profit_native: true
        bracket_order: false
        move_stop: true
        close_partial: true
        close_full: true
        executor_position: false         # post-MVP: richiede Strategy V2 custom

      take_profit:
        min_order_policy: review         # REVIEW_REQUIRED se qty < min_order_size
        residual_policy: assign_to_last_tp

      position_management:
        same_symbol_same_side_policy: block
        same_symbol_opposite_side_policy: allow_if_hedge_mode
        require_client_order_id_correlation: true

      live_safety:
        allow_live_trading: false
```

---

## 12. Package structure

```
src/runtime_v2/execution_gateway/
    __init__.py
    models.py                  ← NeutralCommandPayload, AdapterResult,
                                  AdapterCapabilities, ExecutionConfig
    config_loader.py           ← ExecutionConfigLoader
    client_order_id.py         ← builder + parser round-trip
    gateway.py                 ← ExecutionGateway
    command_worker.py          ← ExecutionCommandWorker (3 query)
    event_sync.py              ← ExchangeEventSyncWorker
    repositories.py            ← estensione ExecutionCommandRepository
    adapters/
        __init__.py
        base.py                ← ExecutionAdapter ABC
        fake.py                ← FakeAdapter per test
        hummingbot_api_paper.py

config/
    execution.yaml

hummingbot_scripts/
    fill_bridge.py             ← upgrade opzionale, zero-infra

tests/runtime_v2/execution_gateway/
    test_config_loader.py
    test_client_order_id.py
    test_gateway.py
    test_command_worker.py
    test_event_sync.py
    test_hummingbot_adapter.py   # gated: RUN_HUMMINGBOT_API_TESTS=1
    test_integration.py
```

---

## 13. Acceptance criteria completi

```
 1. Tutti i command type PRD-04 sono accettati dal gateway.
 2. Nessun import Hummingbot nel lifecycle package.
 3. account_id viene risolto tramite account_routing.
 4. Exchange/connector e account esecutivo sono configurabili.
 5. Ogni comando inviato ha client_order_id tracciabile e deterministico.
 6. Doppio run non reinvia lo stesso comando (idempotenza via client_order_id).
 7. Capability mancante produce REVIEW_REQUIRED.
 8. Errore tecnico adapter produce retry poi FAILED.
 9. SL protettivo richiede protective_stop_native=true.
10. TP multipli default WAITING_POSITION finché non esiste fill reale.
11. Residuo arrotondamenti TP assegnato all'ultimo TP.
12. TP sotto min_order_size → REVIEW_REQUIRED.
13. Posizione same symbol/same side bloccata di default.
14. Hedge ammesso solo se account/adapter lo supportano.
15. HummingbotApiPaperAdapter non abilita live trading.
16. ExchangeEventSyncWorker produce ops_exchange_events idempotenti.
17. Hummingbot V2 executor_mode è fuori scope MVP (capability=false).
18. Entry execution mode configurabile (a/b/c) per adapter.
19. c_bracket con TP multipli fa downgrade a b con warning.
20. ExchangeEventSyncWorker fa polling solo su ordini attivi (SENT/ACK).
21. FillBridge script sostituisce polling senza modifiche al gateway.
22. Leverage impostato sul connector prima del primo ordine.
23. WAITING_POSITION riattivato da ExecutionCommandWorker via query
    su chain OPEN — zero modifiche a PRD-04.
24. CommandStatus in lifecycle/models.py include WAITING_POSITION
    e REVIEW_REQUIRED come unico source of truth.
```

---

## 14. Estensioni future (fuori scope MVP)

| Estensione | Prerequisiti |
|---|---|
| executor_mode | Strategy V2 custom dentro Hummingbot + canale comandi |
| MQTT event source | broker setup, reconnect/backfill, fallback polling |
| Live trading | kill switch, secret management, reconciliation, testnet audit |
| Reconciliation completa | confronto periodico DB vs Hummingbot vs exchange |
| Allocation ledger same-side | ledger quote per account+symbol+side, fill proporzionale |

---

## 15. Verifiche da fare durante il piano implementativo

1. Leggere payload effettivi prodotti da PRD-04 in `ops_execution_commands.payload_json` e verificare alignment con §5.
2. Verificare endpoint REST Hummingbot esatti (Swagger UI a `http://localhost:8000/docs`) per raw order mode.
3. Verificare se Bybit paper trade supporta `bracket_order` nativo prima di implementare mode C.
4. Allineare numero migration (`029`) alla sequenza effettiva delle migration esistenti.

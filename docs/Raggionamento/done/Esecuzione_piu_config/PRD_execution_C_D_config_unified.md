# PRD — Esecuzione C/D e separazione configurazione (documento unificato)

**File:** `PRD_execution_C_D_config_unified.md`  
**Data:** 2026-05-21  
**Ambito:** `runtime_v2` — lifecycle / execution gateway / CCXT Bybit adapter / configuration model  
**Supera:**
- `PRD_runtime_v2_execution_modes_C_D_simple.md`
- `PRD_allegato_config_leverage_hedge_execution_minimal.md`

**Dipende da:**
- `PRD_runtime_v2_passaggio_hummingbot_a_ccxt_lifecycle_rev2.md` — contratto lifecycle base
- `PRD_allegato_order_modes_tp_parziali_reconciliation_ccxt.md` — supera solo la sezione gestione TP parziali (ora position-level in D); i quattro assi di reconciliation e il modello eventi restano validi

---

# 1. Decisioni fondamentali

## 1.1 Regola di separazione configurazione

```text
operation_config.yaml
  = fonte di verità operativa
  = cosa è permesso fare
  → leverage, hedge_mode, risk limits, sizing, position policy, trader policy

execution.yaml
  = configurazione tecnica minimale dell'adapter
  = come parlare con l'exchange
  → adapter type, API env vars, demo/live, C/D strategy, trigger_by, websocket, retry, live safety
```

Regola finale:

```text
Nessuna policy di trading in execution.yaml.
Nessun dettaglio tecnico API in operation_config.yaml.
```

## 1.2 Modalità di esecuzione

Deprecate:

```text
a_sequential          → deprecated
b_entry_stop_then_tp  → deprecated
```

Attive:

```text
C_SIMPLE_ATTACHED   → 1 entry + 1 SL + 1 TP, tutto attached alla entry
D_POSITION_TPSL     → tutti gli altri casi, TP/SL via /v5/position/trading-stop
```

Default:

```text
D_POSITION_TPSL
```

C abilitabile come ottimizzazione per il caso semplice tramite `strategy.simple_attached_enabled: true`.

---

# 2. Separazione configurazione

## 2.1 `operation_config.yaml`

```yaml
account:
  id: main
  capital_base_usdt: 1000.0
  max_leverage: 5
  max_capital_at_risk_pct: 10.0
  hard_max_per_signal_risk_pct: 2.0

defaults:
  risk:
    mode: risk_pct_of_capital
    risk_pct_of_capital: 1.0
    leverage: 5
    max_capital_at_risk_per_trader_pct: 5.0
    max_concurrent_trades: 5
    max_concurrent_same_symbol: 1

  position_policy:
    mode: one_way   # one_way | hedge
```

Override per trader specifico (`config/traders/trader_a.yaml`):

```yaml
risk:
  leverage: 3   # non può superare account.max_leverage
```

## 2.2 `execution.yaml` minimale

```yaml
execution:
  default_adapter: bybit_demo

  account_routing:
    default:
      adapter: bybit_demo
      execution_account_id: main

  adapters:
    bybit_demo:
      type: ccxt_bybit
      mode: demo
      connector: bybit

      api_key_env: BYBIT_API_KEY_BYBIT_DEMO
      api_secret_env: BYBIT_API_SECRET_BYBIT_DEMO

      strategy:
        default_mode: D_POSITION_TPSL
        simple_attached_enabled: true
        trigger_by: MarkPrice
        one_tp_mode: FULL
        multi_tp_mode: PARTIAL

      websocket:
        enabled: true
        poll_fallback_enabled: true
        poll_fallback_period_seconds: 60

      retry:
        max_attempts: 3
        backoff_seconds: [30, 90, 300]

      live_safety:
        allow_live_trading: false
```

Campi rimossi rispetto alla versione legacy:

```text
leverage              → ora in operation_config.yaml
hedge_mode            → ora in operation_config.yaml
entry_execution       → sostituito da strategy.default_mode
capabilities          → rimosso
take_profit           → rimosso
position_management   → rimosso
```

## 2.3 Startup validation

All'avvio il sistema deve rilevare campi deprecati in `execution.yaml`:

```text
leverage, hedge_mode, entry_execution, capabilities, take_profit, position_management
```

Comportamento:
- fase transitoria: log warning
- dopo migrazione completa: errore hard al boot

## 2.4 Flusso del dato `leverage` e `hedge_mode`

```text
operation_config.yaml
    risk.leverage = 5
    position_policy.mode = one_way
        ↓
OperationConfigLoader → EffectiveEnrichmentConfig
        ↓
RiskCapacityEngine
    valida: risk.leverage <= account.max_leverage
    include leverage nel risk_snapshot
        ↓
LifecycleEntryGate
    include nel command payload:
        leverage      = 5
        hedge_mode    = false
        position_idx  = 0   (calcolato dall'adapter)
        ↓
ExecutionGateway
    legge leverage da payload["leverage"]
    NON legge adapter_cfg.leverage
        ↓
CcxtBybitAdapter
    adapter.set_leverage(symbol, leverage, account_id)
    resolve_position_idx(side, hedge_mode) → position_idx
```

Chiave di caching leverage:

```python
leverage_key = f"{account}:{symbol}:{position_idx}:{leverage}"
```

Motivazione: trader diversi possono usare leve diverse sullo stesso simbolo.

## 2.5 Calcolo `position_idx`

```python
def resolve_position_idx(side: str, hedge_mode: bool) -> int:
    if not hedge_mode:
        return 0
    return 1 if side == "LONG" else 2
```

| Mode | LONG | SHORT |
|---|---|---|
| one_way | 0 | 0 |
| hedge | 1 | 2 |

## 2.6 Validazione leverage

```text
risk.leverage (trader) <= risk.leverage (defaults) <= account.max_leverage
```

Se fallisce:

```text
Decision(passed=False, reason="risk_leverage_exceeds_account_max_leverage")
→ segnale BLOCKED o REVIEW_REQUIRED
```

---

# 3. Decision matrix C vs D

Il lifecycle determina la modalità prima di generare i comandi:

| Caso | Modalità |
|---|---|
| 1 entry + 1 SL + 1 TP + `simple_attached_enabled=true` | `C_SIMPLE_ATTACHED` |
| 1 entry + 1 SL + 1 TP + `simple_attached_enabled=false` | `D_POSITION_TPSL_FULL` |
| 1 entry + 1 SL + multi-TP | `D_POSITION_TPSL_PARTIAL` |
| multi-entry + 1 TP | `D_POSITION_TPSL_FULL` |
| multi-entry + multi-TP | `D_POSITION_TPSL_PARTIAL` |
| hedge mode | `D_POSITION_TPSL_FULL/PARTIAL` con `positionIdx` corretto |

---

# 4. C_SIMPLE_ATTACHED

## 4.1 Definizione

Caso esclusivo:

```text
1 entry (MARKET o LIMIT)
1 stop loss
1 take profit
```

TP e SL vengono passati direttamente nella `create_order`.

## 4.2 Casi NON supportati in C

```text
multi-entry
multi-TP
ladder / range
add entry
re-entry
TP parziali
UPDATE pre-fill (vedi sezione 4.5)
```

## 4.3 Comandi generati

```text
PLACE_ENTRY_WITH_ATTACHED_TPSL   → PENDING
```

Nessun `PLACE_PROTECTIVE_STOP` o `PLACE_TAKE_PROFIT` separati.

## 4.4 Payload command

```json
{
  "execution_strategy": "C_SIMPLE_ATTACHED",
  "symbol": "BTC/USDT:USDT",
  "side": "LONG",
  "entry_type": "LIMIT",
  "price": 65000,
  "qty": 0.01,
  "leverage": 5,
  "hedge_mode": false,
  "position_idx": 0,
  "attached_tpsl": {
    "mode": "FULL",
    "take_profit": 70000,
    "stop_loss": 63000,
    "tp_trigger_by": "MarkPrice",
    "sl_trigger_by": "MarkPrice"
  }
}
```

## 4.5 Chiamata Bybit

```python
exchange.create_order(
    symbol="BTC/USDT:USDT",
    type="limit",
    side="buy",
    amount=0.01,
    price=65000,
    params={
        "category": "linear",
        "positionIdx": 0,
        "takeProfit": "70000",
        "stopLoss": "63000",
        "tpTriggerBy": "MarkPrice",
        "slTriggerBy": "MarkPrice",
        "tpslMode": "Full",
        "tpOrderType": "Market",
        "slOrderType": "Market",
        "orderLinkId": "<client_order_id>"
    }
)
```

## 4.6 Lifecycle eventi attesi

```text
PLACE_ENTRY_WITH_ATTACHED_TPSL → ACK
  ↓
ENTRY_FILLED
  ↓
TP_FILLED  oppure  SL_FILLED
  → chain CLOSED
```

## 4.7 UPDATE su chain C

### Entry ancora PENDING

Il TP/SL è attached all'ordine, non alla posizione. `/v5/position/trading-stop` non è applicabile.

Regola:

```text
Qualsiasi UPDATE (move SL, cancel, modifica TP) che arriva
con entry ancora PENDING → REVIEW_REQUIRED automatico.
```

### Entry già FILLED

Dopo il fill, Bybit converte il TP/SL attached in TP/SL position-level.
Da quel momento C si comporta come D:

```text
MOVE_POSITION_STOP    → funziona
CANCEL_POSITION_TPSL  → funziona
```

## 4.8 Raccomandazione per fonti con UPDATE frequenti pre-fill

Se un trader invia spesso UPDATE prima del fill delle entry, disabilitare C globalmente in `execution.yaml`:

```yaml
strategy:
  simple_attached_enabled: false   # forza sempre D mode per tutti i trader
```

Il controllo per-trader è una feature futura: per ora `simple_attached_enabled` è un'impostazione globale dell'adapter.

---

# 5. D_POSITION_TPSL

## 5.1 Definizione

Tutti i casi non coperti da C. Entry come ordini normali, TP/SL tramite:

```text
/v5/position/trading-stop
```

TP e SL sono attributi della posizione Bybit, non ordini distinti con `client_order_id` proprio.

## 5.2 D Full — 1 TP

### Quando usare

```text
1 entry + 1 SL + 1 TP  (con C disabilitata)
multi-entry + 1 SL + 1 TP
```

### Comandi generati alla creazione chain

```text
PLACE_ENTRY leg1..N          → PENDING
SET_POSITION_TPSL_FULL       → WAITING_POSITION
```

### Flusso

```text
ENTRY_FILLED (primo fill)
  → chain OPEN
  → SET_POSITION_TPSL_FULL: WAITING_POSITION → PENDING → inviato
  ↓
adapter chiama /v5/position/trading-stop tpslMode=Full
  ↓
TP_FILLED  oppure  SL_FILLED
  → chain CLOSED
```

### Payload SET_POSITION_TPSL_FULL

```json
{
  "execution_strategy": "D_POSITION_TPSL",
  "symbol": "BTCUSDT",
  "side": "LONG",
  "leverage": 5,
  "hedge_mode": false,
  "position_idx": 0,
  "take_profit": 70000,
  "stop_loss": 63000,
  "tp_trigger_by": "MarkPrice",
  "sl_trigger_by": "MarkPrice"
}
```

### Chiamata Bybit

```python
exchange.private_post_v5_position_trading_stop({
    "category": "linear",
    "symbol": "BTCUSDT",
    "positionIdx": 0,
    "tpslMode": "Full",
    "takeProfit": "70000",
    "stopLoss": "63000",
    "tpTriggerBy": "MarkPrice",
    "slTriggerBy": "MarkPrice",
    "tpOrderType": "Market",
    "slOrderType": "Market"
})
```

---

## 5.3 D Partial — multi-TP

### Quando usare

```text
1 entry + 1 SL + multi-TP
multi-entry + 1 SL + multi-TP
```

### Comandi generati

```text
PLACE_ENTRY leg1..N                  → PENDING
SET_POSITION_TPSL_PARTIAL tp1        → WAITING_POSITION
SET_POSITION_TPSL_PARTIAL tp2        → WAITING_POSITION
...
```

Ogni blocco Partial ha il vincolo Bybit:

```text
tp_size == sl_size
```

### Calcolo size

Input: `open_position_qty` realmente fillata, non la quantità teorica pianificata.

```text
tp_qty_i = open_position_qty * close_pct_i / 100

TP1..TP(n-1) = qty arrotondata verso il basso
TPn = open_position_qty - somma(TP1..TP(n-1))   ← assorbe residuo rounding
```

Se un TP produce `qty < min_order_size` → policy configurata:

```text
min_order_policy: review | merge_into_previous | assign_to_last_tp
```

### Payload SET_POSITION_TPSL_PARTIAL

```json
{
  "execution_strategy": "D_POSITION_TPSL",
  "symbol": "BTCUSDT",
  "side": "LONG",
  "position_idx": 0,
  "tp_sequence": 1,
  "take_profit": 67000,
  "stop_loss": 63000,
  "tp_size": 0.01,
  "sl_size": 0.01,
  "tp_order_type": "Limit",
  "tp_limit_price": 67000,
  "tp_trigger_by": "MarkPrice",
  "sl_trigger_by": "MarkPrice"
}
```

### Chiamata Bybit

```python
exchange.private_post_v5_position_trading_stop({
    "category": "linear",
    "symbol": "BTCUSDT",
    "positionIdx": 0,
    "tpslMode": "Partial",
    "takeProfit": "67000",
    "stopLoss": "63000",
    "tpSize": "0.01",
    "slSize": "0.01",
    "tpOrderType": "Limit",
    "slOrderType": "Market",
    "tpLimitPrice": "67000",
    "tpTriggerBy": "MarkPrice",
    "slTriggerBy": "MarkPrice"
})
```

---

## 5.4 Correlazione fill in D mode

In D mode i TP/SL non hanno un `client_order_id` proprio: sono creati internamente da Bybit quando il prezzo tocca il livello configurato.

Il fill arriva via `watchMyTrades` con:

```text
info.stopOrderType = "TakeProfit" | "StopLoss"
info.reduceOnly    = true
```

### Strategia di correlazione

```text
fill da watchMyTrades con stopOrderType=TakeProfit
  → cerca chain in stato OPEN / PARTIALLY_CLOSED
    con symbol + side corrispondenti
    con SET_POSITION_TPSL in stato ACK
  → in D Partial: verifica fill_price ≈ uno dei TP registrati nel payload command
  → attribuisce il fill a quella chain
```

### Idempotency key

Invariata rispetto al contratto CCXT PRD:

```text
<event_type>:<trade_chain_id>:<exchange_trade_id>
```

### Caso ambiguo — due chain OPEN sullo stesso symbol+side

In `one_way` mode Bybit non ammette due posizioni opposte: due chain OPEN sullo stesso symbol+side è una condizione `REVIEW_REQUIRED` da bloccare prima dell'esecuzione.

In `hedge` mode il `position_idx` distingue LONG (1) da SHORT (2) — nessuna ambiguità.

---

## 5.5 Move SL in D mode

### Move a livello specifico o a BE

Singola chiamata che sovrascrive solo `stopLoss`, senza toccare i TP già impostati:

**Comando:** `MOVE_POSITION_STOP`

```json
{
  "execution_strategy": "D_POSITION_TPSL",
  "symbol": "BTCUSDT",
  "side": "LONG",
  "position_idx": 0,
  "new_stop_loss": 65000
}
```

```python
exchange.private_post_v5_position_trading_stop({
    "category": "linear",
    "symbol": "BTCUSDT",
    "positionIdx": 0,
    "stopLoss": "65000"
    # takeProfit non passato → Bybit non modifica i TP esistenti
})
```

Evento di conferma: `STOP_MOVED_CONFIRMED`

Per **move to breakeven**:

```text
new_stop_loss = entry_avg_price
```

Il `be_protection_status` passa a `PROTECTED` solo dopo `STOP_MOVED_CONFIRMED`, non prima.

---

## 5.6 SYNC_PROTECTIVE_ORDERS in D mode

Necessario dopo ogni evento che cambia `open_position_qty`:

```text
ENTRY_FILLED aggiuntivo (multi-entry)
TP_FILLED parziale
CLOSE_PARTIAL_FILLED
SL_FILLED parziale
```

I blocchi Partial già impostati su Bybit non si aggiornano incrementalmente: vanno cancellati e reimpostati con le nuove size.

### Flusso

```text
SYNC_PROTECTIVE_ORDERS
  → legge open_position_qty attuale
  → legge TP registrati (prezzi + percentuali originali)
  → chiama trading-stop con takeProfit=0 stopLoss=0  (cancella protezioni)
  → ricalcola size TP/SL su open_position_qty corrente
  → re-imposta blocchi Partial con nuove size
  → evento: PROTECTIVE_ORDERS_SYNCED
```

### Rischio noto — finestra senza protezione

Tra la cancellazione e il re-set esiste una breve finestra in cui la posizione non è protetta. Mitigazione:

```text
- WebSocket attivo per rilevare movimenti di prezzo durante la finestra
- Retry immediato se il re-set fallisce
- Log esplicito della finestra per audit
```

---

## 5.7 Annullamento entry averaging (CANCEL_PENDING_ENTRY)

### Principio fondamentale

`SET_POSITION_TPSL` in D mode viene sempre dimensionato su `open_position_qty` reale (filled), non sulla quantità teorica pianificata. Quindi:

```text
cancellare entry pending che non hanno ancora fillato
  → open_position_qty invariata
  → trading-stop già corretto per la size attuale
  → NO SYNC necessario
```

### Scenario: chain OPEN, entry averaging ancora PENDING

```text
Entry 1 fillata → open_position_qty = qty_entry1
  → SET_POSITION_TPSL inviato con size = open_position_qty
Entry 2, Entry 3 → PENDING
↓
Telegram UPDATE: cancella entries pendenti
  → CANCEL_PENDING_ENTRY
  → cancella Entry 2 e Entry 3 (ordini limit normali)
  → open_position_qty invariata
  → trading-stop già corretto
  → chain resta OPEN
  → PENDING_ENTRY_CANCELLED_CONFIRMED { position_already_open: true }
```

### Scenario: nessuna entry ancora fillata

```text
CANCEL_PENDING_ENTRY
  → cancella tutti i PLACE_ENTRY PENDING
  → SET_POSITION_TPSL (ancora WAITING_POSITION) → CANCELLED
  → chain → CANCELLED
```

### Scenario: entry parzialmente fillata prima del cancel

```text
Entry 1 parzialmente fillata → open_position_qty = qty_parziale
  → SET_POSITION_TPSL inviato con size = qty_parziale
↓
CANCEL_PENDING_ENTRY
  → cancella Entry 2, Entry 3
  → cancella residuo non fillato di Entry 1
  → open_position_qty = solo il fill già avvenuto
  → trading-stop già corretto per quella size
  → chain resta OPEN
```

---

# 6. Comandi lifecycle

## 6.1 Comandi attivi

```text
PLACE_ENTRY
PLACE_ENTRY_WITH_ATTACHED_TPSL
SET_POSITION_TPSL_FULL
SET_POSITION_TPSL_PARTIAL
MOVE_POSITION_STOP
CANCEL_POSITION_TPSL
SYNC_PROTECTIVE_ORDERS
CANCEL_PENDING_ENTRY
CLOSE_PARTIAL
CLOSE_FULL
```

## 6.2 Comandi deprecati come percorso primario

```text
PLACE_PROTECTIVE_STOP    → non usato in C/D (mantenuto per compatibilità test esistenti)
PLACE_TAKE_PROFIT        → non usato in C/D (mantenuto per compatibilità test esistenti)
```

## 6.3 Event types (invariati dal CCXT PRD)

```text
ENTRY_FILLED
TP_FILLED
SL_FILLED
CLOSE_PARTIAL_FILLED
CLOSE_FULL_FILLED
STOP_MOVED_CONFIRMED
PENDING_ENTRY_CANCELLED_CONFIRMED
PROTECTIVE_ORDERS_SYNCED
ORDER_REJECTED
ORDER_CANCELLED
```

---

# 7. Config model — modifiche codice

## 7.1 `AdapterConfig`

Rimuovere da `src/runtime_v2/execution_gateway/models.py`:

```python
leverage: float
hedge_mode: bool
entry_execution: EntryExecutionConfig
capabilities: AdapterCapabilities
take_profit: TakeProfitConfig
position_management: PositionManagementConfig
```

Aggiungere:

```python
class ExecutionStrategyConfig(BaseModel):
    default_mode: Literal["C_SIMPLE_ATTACHED", "D_POSITION_TPSL"] = "D_POSITION_TPSL"
    simple_attached_enabled: bool = True
    trigger_by: Literal["MarkPrice", "LastPrice", "IndexPrice"] = "MarkPrice"
    one_tp_mode: Literal["FULL"] = "FULL"
    multi_tp_mode: Literal["PARTIAL"] = "PARTIAL"

class AdapterConfig(BaseModel):
    type: str
    mode: str
    connector: str
    api_key_env: str | None = None
    api_secret_env: str | None = None
    strategy: ExecutionStrategyConfig = Field(default_factory=ExecutionStrategyConfig)
    websocket: WebsocketConfig
    retry: RetryConfig
    live_safety: LiveSafetyConfig
```

## 7.2 `factory.py`

```python
api_key = os.environ.get(cfg.api_key_env or "") if cfg.api_key_env else ""
api_secret = os.environ.get(cfg.api_secret_env or "") if cfg.api_secret_env else ""
```

---

# 8. Piano di migrazione

## Step 1 — Config models

- Modificare `AdapterConfig` (rimuovi legacy, aggiungi `strategy` e `api_key_env`)
- Aggiornare `factory.py` per env interpolation
- Aggiungere startup validation su campi deprecati in `execution.yaml`

## Step 2 — `execution.yaml` e `operation_config.yaml`

- Rimuovere `leverage` e `hedge_mode` da `execution.yaml`
- Verificare presenza di `risk.leverage` e `position_policy` in `operation_config.yaml`

## Step 3 — RiskCapacityEngine

- Implementare `risk.leverage <= account.max_leverage`
- Output `risk_snapshot` include `leverage`

## Step 4 — Command payload

- Aggiungere a tutti i comandi: `leverage`, `hedge_mode`, `position_idx`, `execution_strategy`
- `ExecutionGateway` legge `leverage` da `payload["leverage"]`

## Step 5 — Lifecycle decision matrix

- Implementare selezione C vs D nel lifecycle gate
- Aggiungere gestione `REVIEW_REQUIRED` per UPDATE su chain C con entry PENDING

## Step 6 — Nuovi comandi lifecycle

- `PLACE_ENTRY_WITH_ATTACHED_TPSL`
- `SET_POSITION_TPSL_FULL`
- `SET_POSITION_TPSL_PARTIAL`
- `MOVE_POSITION_STOP`
- `SYNC_PROTECTIVE_ORDERS`

## Step 7 — Adapter Bybit

- Handler `PLACE_ENTRY_WITH_ATTACHED_TPSL` → `create_order` con attached TP/SL
- Handler `SET_POSITION_TPSL_FULL` → `/v5/position/trading-stop` Full
- Handler `SET_POSITION_TPSL_PARTIAL` → `/v5/position/trading-stop` Partial
- Handler `MOVE_POSITION_STOP` → `/v5/position/trading-stop` solo `stopLoss`
- Handler `SYNC_PROTECTIVE_ORDERS` → cancel + re-set Partial
- Correlazione fill D mode in `CcxtBybitWsEventWorker`

## Step 8 — Test

Nuovi test (vedi sezione 9.3).

---

# 9. Acceptance criteria

## 9.1 Config

- `execution.yaml` non contiene `leverage`, `hedge_mode`, `entry_execution`, `capabilities`
- `operation_config.yaml` è la fonte unica di `risk.leverage` e `position_policy`
- Nessun componente legge `adapter_cfg.leverage` o `adapter_cfg.hedge_mode`
- Startup validation rileva e segnala campi deprecati in `execution.yaml`

## 9.2 Runtime

- `risk.leverage > account.max_leverage` → segnale `BLOCKED` con motivo esplicito
- `hedge_mode=true` con adapter/account non-hedge → `REVIEW_REQUIRED`
- Leva impostata su Bybit deriva da `payload["leverage"]`, non da `adapter_cfg`
- `position_idx` calcolato correttamente da `resolve_position_idx(side, hedge_mode)`
- LONG hedge → `positionIdx=1`; SHORT hedge → `positionIdx=2`; one_way → `positionIdx=0`

## 9.3 Esecuzione C

- 1 entry + 1 TP + 1 SL + `simple_attached_enabled=true` → `PLACE_ENTRY_WITH_ATTACHED_TPSL`
- UPDATE su chain C con entry PENDING → `REVIEW_REQUIRED`
- UPDATE su chain C con entry FILLED → `MOVE_POSITION_STOP` funziona correttamente
- Nessun `PLACE_PROTECTIVE_STOP` o `PLACE_TAKE_PROFIT` generati in C

## 9.4 Esecuzione D

- Multi-entry genera `PLACE_ENTRY` + `SET_POSITION_TPSL` in `WAITING_POSITION`
- `SET_POSITION_TPSL` rilasciato al primo `ENTRY_FILLED`, dimensionato su `open_position_qty` reale
- Multi-TP genera blocchi `SET_POSITION_TPSL_PARTIAL` con `tp_size == sl_size`
- Ultimo TP assorbe residuo rounding
- `SYNC_PROTECTIVE_ORDERS` eseguito dopo ogni fill che cambia `open_position_qty`
- `MOVE_POSITION_STOP` sovrascrive solo `stopLoss` senza toccare TP esistenti
- `be_protection_status=PROTECTED` solo dopo `STOP_MOVED_CONFIRMED`
- Fill D mode correlati per `symbol + side + stopOrderType + exchange_trade_id`
- `TP_FILLED` idempotente per `exchange_trade_id`

## 9.5 Cancel averaging

- `CANCEL_PENDING_ENTRY` con chain `OPEN` cancella solo entry PENDING, non tocca trading-stop
- `CANCEL_PENDING_ENTRY` con chain `WAITING_ENTRY` (nessun fill) → chain `CANCELLED`
- `PENDING_ENTRY_CANCELLED_CONFIRMED` include `position_already_open: true/false`

## 9.6 Reconciliation (invariata dal CCXT PRD)

- I quattro assi di reconciliation restano validi: command/order, fill, position, protective orders
- Fill D mode persi ricostruiti da `fetchMyTrades` con `stopOrderType` come discriminante
- Protective orders incoerenti generano `SYNC_PROTECTIVE_ORDERS` o `REVIEW_REQUIRED`

---

# 10. Sintesi

```text
operation_config.yaml      decide cosa fare
  leverage, hedge, rischio, sizing, policy trader

execution.yaml             decide come farlo
  adapter, API env, demo/live, C/D strategy, websocket, retry

C_SIMPLE_ATTACHED          1 entry + 1 SL + 1 TP
  → create_order con attached TP/SL
  → UPDATE pre-fill → REVIEW_REQUIRED
  → UPDATE post-fill → come D

D_POSITION_TPSL            tutto il resto
  → entry normali + /v5/position/trading-stop
  → Full per 1 TP, Partial per multi-TP
  → MOVE_POSITION_STOP sovrascrive solo stopLoss
  → SYNC ricalcola size su open_position_qty reale
  → fill correlati per symbol+side+stopOrderType+exchange_trade_id
```

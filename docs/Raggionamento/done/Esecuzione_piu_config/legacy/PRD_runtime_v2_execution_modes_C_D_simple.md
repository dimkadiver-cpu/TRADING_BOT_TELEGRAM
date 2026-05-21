# PRD — Runtime V2 Execution Modes: semplificazione da A/B/C a C/D

**File:** `PRD_runtime_v2_execution_modes_C_D_simple.md`  
**Ambito:** `runtime_v2` — lifecycle / execution gateway / CCXT Bybit adapter / `execution.yaml`  
**Obiettivo:** semplificare le modalità di esecuzione iniziale, deprecando A/B e sostituendo la C ibrida attuale con due strategie esplicite:

```text
C_SIMPLE_ATTACHED
D_POSITION_TPSL
```

---

# 1. Problema attuale

Nel progetto esistono tre modalità concettuali:

```text
a_sequential
b_entry_stop_then_tp
c_native_attached_tpsl
```

## 1.1 Mode A — `a_sequential`

Flusso:

```text
1. piazza entry
2. aspetta fill entry
3. piazza SL
4. piazza TP
```

Limite:

```text
Dopo il fill della entry può esistere una finestra temporale senza SL.
```

## 1.2 Mode B — `b_entry_stop_then_tp`

Flusso:

```text
1. piazza entry
2. piazza subito SL come conditional reduce-only
3. aspetta fill entry
4. piazza TP
```

Limite:

```text
Lo SL non è position-level Bybit trading-stop.
È un conditional market order reduceOnly separato.
```

Questo genera ambiguità perché il sistema lo chiama “protective stop native”, ma tecnicamente non usa `/v5/position/trading-stop`.

## 1.3 Mode C attuale — `c_native_attached_tpsl`

Flusso attuale:

```text
1. piazza entry con attached TP/SL
2. non genera PLACE_PROTECTIVE_STOP separato
3. se ci sono più TP:
   - ultimo TP attached alla entry
   - TP intermedi restano ordini separati dopo fill
```

Limite:

```text
È una modalità ibrida.
Non è né fully attached né fully position-level.
```

---

# 2. Decisione

Deprecare le modalità:

```text
a_sequential
b_entry_stop_then_tp
```

Sostituire la C attuale con una C più ristretta e introdurre D:

```text
C_SIMPLE_ATTACHED
D_POSITION_TPSL
```

| Nuova modalità | Scopo |
|---|---|
| `C_SIMPLE_ATTACHED` | 1 entry + 1 SL + 1 TP, tutto attached alla entry |
| `D_POSITION_TPSL` | tutti i casi non semplici: multi-entry, multi-TP, hedge, add-entry, partial fill |

---

# 3. Modalità C — `C_SIMPLE_ATTACHED`

## 3.1 Definizione

La modalità C serve solo per il caso semplice:

```text
1 entry
1 stop loss
1 take profit
```

TP e SL vengono passati direttamente nella `create_order` della entry.

## 3.2 Casi supportati

```text
- 1 entry MARKET + 1 SL + 1 TP
- 1 entry LIMIT + 1 SL + 1 TP
```

## 3.3 Casi NON supportati

```text
- più entry
- più TP
- ladder/range entry
- add entry
- re-entry dentro la stessa posizione
- TP parziali
```

Hedge mode può essere supportato solo se la chain espone già `positionIdx` corretto.

## 3.4 Flusso

```text
SIGNAL_ACCEPTED
  ↓
PLACE_ENTRY_WITH_ATTACHED_TPSL
  ↓
Bybit create_order:
  - entry order
  - takeProfit
  - stopLoss
  - tpslMode
  - tpTriggerBy
  - slTriggerBy
  ↓
Exchange events:
  - ENTRY_FILLED
  - TP_FILLED oppure SL_FILLED
```

## 3.5 Parametri Bybit

Esempio logico:

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

## 3.6 Stato comandi

In C il lifecycle non deve generare comandi separati:

```text
PLACE_PROTECTIVE_STOP
PLACE_TAKE_PROFIT
```

per il caso semplice.

Deve generare un solo comando:

```text
PLACE_ENTRY_WITH_ATTACHED_TPSL
```

oppure mantenere `PLACE_ENTRY` ma con payload esplicito:

```json
{
  "execution_mode": "C_SIMPLE_ATTACHED",
  "symbol": "BTC/USDT:USDT",
  "side": "LONG",
  "entry_type": "LIMIT",
  "price": 65000,
  "qty": 0.01,
  "attached_tpsl": {
    "mode": "FULL",
    "take_profit": 70000,
    "stop_loss": 63000,
    "tp_trigger_by": "MarkPrice",
    "sl_trigger_by": "MarkPrice"
  }
}
```

---

# 4. Modalità D — `D_POSITION_TPSL`

## 4.1 Definizione

La modalità D usa ordini entry normali e poi imposta TP/SL tramite Bybit position-level trading-stop:

```text
/v5/position/trading-stop
```

Qui TP/SL non sono attached al singolo ordine entry, ma alla posizione Bybit.

## 4.2 Casi supportati

```text
- 1 entry + 1 SL + 1 TP
- 1 entry + 1 SL + più TP
- più entry + 1 SL + 1 TP
- più entry + 1 SL + più TP
- market + limit mixed legs
- range/ladder
- add entry
- re-entry
- partial fill
- hedge mode long/short con positionIdx corretto
```

## 4.3 Flusso generale

```text
SIGNAL_ACCEPTED
  ↓
PLACE_ENTRY leg 1
PLACE_ENTRY leg 2...
  ↓
ENTRY_FILLED / PARTIAL_FILL
  ↓
posizione esiste su Bybit
  ↓
SET_POSITION_TPSL_FULL oppure SET_POSITION_TPSL_PARTIAL
  ↓
Exchange events:
  - TP_FILLED
  - SL_FILLED
  - POSITION_CLOSED
```

---

# 5. D Full — 1 TP + 1 SL

## 5.1 Quando usare

```text
- 1 entry + 1 SL + 1 TP
- più entry + 1 SL + 1 TP
```

## 5.2 Comando runtime

```text
SET_POSITION_TPSL_FULL
```

Payload:

```json
{
  "symbol": "BTCUSDT",
  "side": "LONG",
  "position_idx": 0,
  "take_profit": 70000,
  "stop_loss": 63000,
  "tp_trigger_by": "MarkPrice",
  "sl_trigger_by": "MarkPrice"
}
```

## 5.3 Chiamata Bybit

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

# 6. D Partial — più TP

## 6.1 Quando usare

```text
- 1 entry + più TP
- più entry + più TP
```

## 6.2 Concetto

Bybit Partial lavora per blocchi:

```text
TP1 size X + SL size X
TP2 size Y + SL size Y
TP3 size Z + SL size Z
```

Quindi il sistema deve tradurre:

```text
1 SL globale + più TP
```

in una serie di blocchi parziali:

```text
Partial block 1:
  tpSize = size TP1
  slSize = size TP1

Partial block 2:
  tpSize = size TP2
  slSize = size TP2
```

## 6.3 Comando runtime

```text
SET_POSITION_TPSL_PARTIAL
```

Payload per singolo TP:

```json
{
  "symbol": "BTCUSDT",
  "side": "LONG",
  "position_idx": 0,
  "tp_sequence": 1,
  "take_profit": 67000,
  "stop_loss": 63000,
  "tp_size": 0.01,
  "sl_size": 0.01,
  "tp_order_type": "Limit",
  "sl_order_type": "Market",
  "tp_limit_price": 67000,
  "tp_trigger_by": "MarkPrice",
  "sl_trigger_by": "MarkPrice"
}
```

## 6.4 Chiamata Bybit

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

## 6.5 Regola importante

In Bybit Partial:

```text
tpSize deve essere uguale a slSize
```

Quindi se il sistema vuole tre TP parziali, deve creare tre blocchi parziali coerenti.

---

# 7. Hedge mode

## 7.1 Regola

In hedge mode ogni chain deve distinguere:

```text
symbol
side
positionIdx
```

Mapping:

```text
one-way:
  positionIdx = 0

hedge LONG:
  positionIdx = 1

hedge SHORT:
  positionIdx = 2
```

## 7.2 Esempio LONG hedge

```json
{
  "symbol": "BTCUSDT",
  "side": "LONG",
  "position_idx": 1
}
```

## 7.3 Esempio SHORT hedge

```json
{
  "symbol": "BTCUSDT",
  "side": "SHORT",
  "position_idx": 2
}
```

---

# 8. Nuova matrice casi

| Caso | Modalità |
|---|---|
| 1 entry market/limit + 1 SL + 1 TP | `C_SIMPLE_ATTACHED` oppure `D_POSITION_TPSL_FULL` |
| 1 entry market/limit + 1 SL + più TP | `D_POSITION_TPSL_PARTIAL` |
| più entry + 1 SL + 1 TP | `D_POSITION_TPSL_FULL` |
| più entry + 1 SL + più TP | `D_POSITION_TPSL_PARTIAL` |
| hedge long/short | `D_POSITION_TPSL_FULL/PARTIAL` con `positionIdx` |

## 8.1 Default consigliato

Per ridurre casi speciali:

```text
default = D_POSITION_TPSL
```

C può essere abilitata solo come ottimizzazione per il caso più semplice.

---

# 9. Nuovo `execution.yaml` semplice

## 9.1 Obiettivo

Rimuovere configurazioni ambigue:

```text
entry_execution.mode
protective_stop_native
take_profit_native
bracket_order
```

Sostituirle con una strategia chiara.

## 9.2 Proposta YAML

```yaml
execution:
  default_adapter: bybit_demo

  account_routing:
    default:
      adapter: bybit_demo
      execution_account_id: master_account

  adapters:
    bybit_demo:
      type: ccxt_bybit
      mode: demo
      connector: bybit
      testnet: true
      api_key: "${BYBIT_API_KEY_BYBIT_DEMO}"
      leverage: 10
      hedge_mode: false

      strategy:
        default_mode: D_POSITION_TPSL

        simple_attached:
          enabled: true
          mode: C_SIMPLE_ATTACHED
          use_when:
            single_entry: true
            single_take_profit: true
            single_stop_loss: true

        position_tpsl:
          enabled: true
          mode: D_POSITION_TPSL
          one_tp_mode: FULL
          multi_tp_mode: PARTIAL
          trigger_by:
            take_profit: MarkPrice
            stop_loss: MarkPrice
          full:
            tp_order_type: Market
            sl_order_type: Market
          partial:
            tp_order_type: Limit
            sl_order_type: Market
            residual_policy: assign_to_last_tp
            min_order_policy: review

      websocket:
        enabled: true
        poll_fallback_enabled: true
        poll_fallback_period_seconds: 60

      retry:
        max_attempts: 3
        backoff_seconds: [30, 90, 300]

      live_safety:
        allow_live_trading: false

    bybit_paper:
      type: ccxt_bybit
      mode: paper
      connector: bybit
      testnet: true
      api_key: ""
      leverage: 10
      hedge_mode: false

      strategy:
        default_mode: D_POSITION_TPSL
        simple_attached:
          enabled: false
          mode: C_SIMPLE_ATTACHED
        position_tpsl:
          enabled: true
          mode: D_POSITION_TPSL
          one_tp_mode: FULL
          multi_tp_mode: PARTIAL
          trigger_by:
            take_profit: MarkPrice
            stop_loss: MarkPrice

      websocket:
        enabled: false
        poll_fallback_enabled: true
        poll_fallback_period_seconds: 60

      retry:
        max_attempts: 3
        backoff_seconds: [30, 90, 300]

      live_safety:
        allow_live_trading: false
```

---

# 10. Modifiche richieste al codice

## 10.1 Config model

Sostituire:

```python
class EntryExecutionConfig(BaseModel):
    mode: str = "b_entry_stop_then_tp"
```

con modelli espliciti:

```python
from typing import Literal
from pydantic import BaseModel, Field

ExecutionStrategyMode = Literal[
    "C_SIMPLE_ATTACHED",
    "D_POSITION_TPSL",
]

class StrategyTriggerByConfig(BaseModel):
    take_profit: Literal["MarkPrice", "LastPrice", "IndexPrice"] = "MarkPrice"
    stop_loss: Literal["MarkPrice", "LastPrice", "IndexPrice"] = "MarkPrice"

class SimpleAttachedConfig(BaseModel):
    enabled: bool = True
    mode: Literal["C_SIMPLE_ATTACHED"] = "C_SIMPLE_ATTACHED"

class PositionTpslFullConfig(BaseModel):
    tp_order_type: Literal["Market"] = "Market"
    sl_order_type: Literal["Market"] = "Market"

class PositionTpslPartialConfig(BaseModel):
    tp_order_type: Literal["Market", "Limit"] = "Limit"
    sl_order_type: Literal["Market", "Limit"] = "Market"
    residual_policy: Literal["assign_to_last_tp", "review"] = "assign_to_last_tp"
    min_order_policy: Literal["review", "merge_into_last_tp"] = "review"

class PositionTpslConfig(BaseModel):
    enabled: bool = True
    mode: Literal["D_POSITION_TPSL"] = "D_POSITION_TPSL"
    one_tp_mode: Literal["FULL"] = "FULL"
    multi_tp_mode: Literal["PARTIAL"] = "PARTIAL"
    trigger_by: StrategyTriggerByConfig = Field(default_factory=StrategyTriggerByConfig)
    full: PositionTpslFullConfig = Field(default_factory=PositionTpslFullConfig)
    partial: PositionTpslPartialConfig = Field(default_factory=PositionTpslPartialConfig)

class ExecutionStrategyConfig(BaseModel):
    default_mode: ExecutionStrategyMode = "D_POSITION_TPSL"
    simple_attached: SimpleAttachedConfig = Field(default_factory=SimpleAttachedConfig)
    position_tpsl: PositionTpslConfig = Field(default_factory=PositionTpslConfig)
```

Nel `AdapterConfig`:

```python
strategy: ExecutionStrategyConfig = Field(default_factory=ExecutionStrategyConfig)
```

Rimuovere o deprecare:

```python
entry_execution: EntryExecutionConfig
take_profit: TakeProfitConfig
capabilities: AdapterCapabilities
```

---

# 11. Lifecycle command types

Deprecare come comandi primari:

```text
PLACE_PROTECTIVE_STOP
PLACE_TAKE_PROFIT
```

Nuovi comandi:

```text
PLACE_ENTRY
PLACE_ENTRY_WITH_ATTACHED_TPSL
SET_POSITION_TPSL_FULL
SET_POSITION_TPSL_PARTIAL
MOVE_POSITION_STOP
CANCEL_POSITION_TPSL
```

## 11.1 Regole di generazione comandi

### Caso C

Se:

```text
single entry
single TP
single SL
strategy.simple_attached.enabled = true
```

allora:

```text
PLACE_ENTRY_WITH_ATTACHED_TPSL
```

### Caso D Full

Se:

```text
TP count = 1
```

allora:

```text
PLACE_ENTRY leg1...
WAIT_POSITION
SET_POSITION_TPSL_FULL
```

### Caso D Partial

Se:

```text
TP count > 1
```

allora:

```text
PLACE_ENTRY leg1...
WAIT_POSITION
SET_POSITION_TPSL_PARTIAL tp1
SET_POSITION_TPSL_PARTIAL tp2
...
```

---

# 12. Adapter Bybit

## 12.1 `PLACE_ENTRY_WITH_ATTACHED_TPSL`

Deve chiamare:

```python
exchange.create_order(..., params={
    "takeProfit": ...,
    "stopLoss": ...,
    "tpslMode": "Full",
    "tpOrderType": "Market",
    "slOrderType": "Market",
    ...
})
```

## 12.2 `SET_POSITION_TPSL_FULL`

Deve chiamare:

```python
exchange.private_post_v5_position_trading_stop({
    "category": "linear",
    "symbol": bybit_symbol,
    "positionIdx": position_idx,
    "tpslMode": "Full",
    "takeProfit": str(tp_price),
    "stopLoss": str(sl_price),
    "tpTriggerBy": tp_trigger_by,
    "slTriggerBy": sl_trigger_by,
    "tpOrderType": "Market",
    "slOrderType": "Market",
})
```

## 12.3 `SET_POSITION_TPSL_PARTIAL`

Deve chiamare:

```python
exchange.private_post_v5_position_trading_stop({
    "category": "linear",
    "symbol": bybit_symbol,
    "positionIdx": position_idx,
    "tpslMode": "Partial",
    "takeProfit": str(tp_price),
    "stopLoss": str(sl_price),
    "tpSize": str(tp_size),
    "slSize": str(tp_size),
    "tpOrderType": tp_order_type,
    "slOrderType": sl_order_type,
    "tpLimitPrice": str(tp_limit_price),
    "tpTriggerBy": tp_trigger_by,
    "slTriggerBy": sl_trigger_by,
})
```

---

# 13. DB e audit

## 13.1 `ops_trade_chains`

Aggiungere o usare campi:

```text
execution_mode
position_idx
tpsl_strategy
```

Esempi:

```text
execution_mode = C_SIMPLE_ATTACHED
execution_mode = D_POSITION_TPSL
tpsl_strategy = FULL
tpsl_strategy = PARTIAL
```

## 13.2 `ops_execution_commands`

I comandi `SET_POSITION_TPSL_FULL/PARTIAL` devono avere payload completo per audit.

## 13.3 `ops_exchange_events`

Gli eventi Bybit vanno normalizzati in:

```text
ENTRY_FILLED
TP_FILLED
SL_FILLED
POSITION_TPSL_UPDATED
POSITION_TPSL_CANCELLED
```

---

# 14. Migrazione

## 14.1 Deprecare Mode A

```text
a_sequential → deprecated
```

Motivo:

```text
troppo simile a D, ma senza usare position-level trading-stop.
```

## 14.2 Deprecare Mode B

```text
b_entry_stop_then_tp → deprecated
```

Motivo:

```text
usa SL conditional reduce-only separato, non position-level trading-stop.
```

## 14.3 Sostituire Mode C attuale

```text
c_native_attached_tpsl → replaced
```

Nuova divisione:

```text
C_SIMPLE_ATTACHED = solo caso semplice
D_POSITION_TPSL = tutti gli altri casi
```

---

# 15. Acceptance criteria

## 15.1 Config

- `execution.yaml` non contiene più `entry_execution.mode`.
- `execution.yaml` non contiene più `b_entry_stop_then_tp`.
- `strategy.default_mode` è validato con Literal/Enum.
- Valori errati in YAML falliscono all’avvio.

## 15.2 Lifecycle

- Caso 1 entry + 1 TP + 1 SL genera `PLACE_ENTRY_WITH_ATTACHED_TPSL` se C è abilitata.
- Caso multi-TP genera `SET_POSITION_TPSL_PARTIAL`.
- Caso multi-entry + 1 TP genera `SET_POSITION_TPSL_FULL`.
- Caso hedge imposta `positionIdx=1` per LONG e `positionIdx=2` per SHORT.

## 15.3 Adapter

- Non usa più `PLACE_PROTECTIVE_STOP` come conditional reduce-only per il flusso principale.
- Implementa `private_post_v5_position_trading_stop`.
- Supporta `Full`.
- Supporta `Partial`.
- Usa `MarkPrice` come default trigger.

## 15.4 Reconciliation

- Il sistema riconcilia posizione reale prima di inviare `SET_POSITION_TPSL_FULL/PARTIAL`.
- Se non esiste posizione, il comando resta `WAITING_POSITION`.
- Se la posizione è chiusa, TP/SL residui vengono marcati come non applicabili o riconciliati come chiusi/cancellati.
- Nessun TP/SL viene creato senza `positionIdx` corretto in hedge mode.

---

# 16. Sintesi finale

Nuova architettura proposta:

```text
C_SIMPLE_ATTACHED
  Solo:
    - 1 entry
    - 1 SL
    - 1 TP
  Usa:
    - create_order con takeProfit + stopLoss

D_POSITION_TPSL
  Tutto il resto:
    - multi-entry
    - multi-TP
    - hedge
    - partial fill
    - add entry
  Usa:
    - entry orders normali
    - /v5/position/trading-stop Full o Partial
```

Regola finale:

```text
C = attached semplice
D = position-level trading-stop
A/B = deprecated
```

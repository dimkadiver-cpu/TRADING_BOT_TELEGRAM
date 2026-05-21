# PRD Allegato — Separazione responsabilità tra `operation_config.yaml` ed `execution.yaml`

**File:** `PRD_allegato_config_leverage_hedge_execution_minimal.md`  
**Ambito:** `runtime_v2` — configuration model / signal enrichment / lifecycle / execution gateway / CCXT Bybit adapter  
**Documento collegato:** `PRD_runtime_v2_execution_modes_C_D_simple.md`  
**Scopo:** eliminare la sovrapposizione tra configurazione operativa e configurazione tecnica dell’exchange, rendendo `execution.yaml` minimale.

---

# 1. Problema

Nel sistema attuale alcuni parametri compaiono sia in:

```text
config/operation_config.yaml
```

sia in:

```text
config/execution.yaml
```

I casi principali sono:

```text
leverage
hedge_mode
```

Questa duplicazione crea ambiguità:

```text
- quale file decide la leva effettiva?
- quale file decide se una fonte può aprire posizioni hedge?
- quale valore vince se i due file divergono?
```

Esempio incoerente:

```yaml
# operation_config.yaml
account:
  max_leverage: 5

defaults:
  risk:
    leverage: 1
```

```yaml
# execution.yaml
leverage: 10
```

Questa configurazione permette tre interpretazioni incompatibili:

```text
- max leverage ammessa = 5
- leverage risk dichiarata = 1
- leverage impostata su exchange = 10
```

Il sistema deve eliminare questa ambiguità.

---

# 2. Decisione principale

## 2.1 Fonte di verità

```text
operation_config.yaml = fonte di verità operativa
execution.yaml = configurazione tecnica minima dell’adapter
```

## 2.2 Campi da spostare definitivamente in `operation_config.yaml`

```text
leverage
hedge_mode
risk limits
position policy
trader/source policy
```

## 2.3 Campi che devono restare in `execution.yaml`

```text
adapter type
exchange connector
demo/testnet/live mode
API env variable names
routing account → adapter
technical TP/SL execution strategy C/D
trigger price source
websocket
retry
live safety gate
```

---

# 3. Nuova semantica di `leverage`

## 3.1 Significato

`leverage` in `operation_config.yaml` indica la **leva operativa da usare per aprire la posizione**.

Questa leva serve a:

```text
- aprire più posizioni con capitale limitato;
- calcolare/verificare l’esposizione;
- impostare la leva sull’exchange prima dell’ordine;
- vincolare il rischio dentro i limiti account/trader.
```

## 3.2 Regola

```text
risk.leverage <= account.max_leverage
```

Se la regola fallisce:

```text
- il segnale non deve arrivare all’execution gateway;
- deve andare in REVIEW o BLOCK;
- il motivo deve essere esplicito.
```

Esempio motivo:

```text
risk_leverage_exceeds_account_max_leverage
```

## 3.3 Config proposta

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
    risk_usdt_fixed: 10.0
    capital_base_mode: static_config
    capital_base_usdt: 1000.0
    leverage: 5
    use_trader_risk_hint: false
    max_capital_at_risk_per_trader_pct: 5.0
    max_concurrent_trades: 5
    max_concurrent_same_symbol: 1
```

## 3.4 Override per trader

Ogni trader può sovrascrivere `risk.leverage`, ma non può superare `account.max_leverage`.

Esempio:

```yaml
# config/traders/trader_a.yaml
risk:
  leverage: 3
```

```yaml
# config/traders/trader_b.yaml
risk:
  leverage: 5
```

Non valido:

```yaml
# config/traders/trader_c.yaml
risk:
  leverage: 10
```

se:

```yaml
account:
  max_leverage: 5
```

---

# 4. Nuova semantica di `hedge_mode`

## 4.1 Significato

`hedge_mode` in `operation_config.yaml` indica se una fonte/trader può aprire posizioni opposte sullo stesso simbolo.

Non è un dettaglio tecnico dell’adapter.

È una policy operativa della fonte di segnale.

```text
hedge_mode: false
  → non accetta LONG e SHORT simultanei sullo stesso simbolo per quella fonte/trader

hedge_mode: true
  → accetta LONG e SHORT simultanei, se l’exchange/account è compatibile
```

## 4.2 Config attuale compatibile

Per ora si può mantenere:

```yaml
defaults:
  hedge_mode: false
```

## 4.3 Config futura consigliata

Più chiara:

```yaml
defaults:
  position_policy:
    mode: one_way   # one_way | hedge
```

Mapping:

```text
hedge_mode: false → position_policy.mode: one_way
hedge_mode: true  → position_policy.mode: hedge
```

## 4.4 Regola su Bybit

Quando la policy è `hedge`, l’execution deve usare `positionIdx` corretto:

```text
LONG  → positionIdx = 1
SHORT → positionIdx = 2
```

Quando la policy è `one_way`:

```text
positionIdx = 0
```

---

# 5. `execution.yaml` minimale

## 5.1 Campi da rimuovere

Da eliminare da `execution.yaml`:

```yaml
leverage: ...
hedge_mode: ...
entry_execution: ...
capabilities: ...
take_profit: ...
position_management: ...
```

Motivo:

```text
- leverage è policy operativa/risk;
- hedge_mode è policy operativa della fonte;
- entry_execution A/B/C viene sostituito dalle nuove strategie C/D;
- capabilities sono troppo generiche e spesso fuorvianti;
- take_profit/residual policy appartiene alla strategia position_tpsl o al management plan;
- position_management appartiene al lifecycle/risk, non all’adapter.
```

## 5.2 Nuovo file minimale proposto

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

## 5.3 Nota su `api_key_env` / `api_secret_env`

La versione attuale del loader non interpola automaticamente variabili ambiente nel valore `api_key`.

Quindi servono modifiche a:

```text
src/runtime_v2/execution_gateway/models.py
src/runtime_v2/execution_gateway/adapters/factory.py
```

Nuovo modello:

```python
class AdapterConfig(BaseModel):
    type: str
    mode: str
    connector: str
    api_key_env: str | None = None
    api_secret_env: str | None = None
    strategy: ExecutionStrategyConfig
    websocket: WebsocketConfig
    retry: RetryConfig
    live_safety: LiveSafetyConfig
```

Factory:

```python
api_key = os.environ.get(cfg.api_key_env or "") if cfg.api_key_env else ""
api_secret = os.environ.get(cfg.api_secret_env or "") if cfg.api_secret_env else ""
```

---

# 6. Nuovo flusso dati

## 6.1 Enrichment

`OperationConfigLoader` produce `EffectiveEnrichmentConfig` con:

```text
risk.leverage
hedge_mode
account_id
management_plan
signal_policy
```

## 6.2 RiskCapacityEngine

`RiskCapacityEngine` deve:

```text
1. validare risk.leverage <= account.max_leverage;
2. restituire decision.leverage;
3. includere leverage nel risk_snapshot;
4. bloccare/revisionare se la leva non è valida.
```

Output atteso:

```json
{
  "passed": true,
  "size_usdt": 250.0,
  "leverage": 5,
  "risk_snapshot": {
    "capital": 1000.0,
    "risk_amount": 10.0,
    "entry_price": 65000.0,
    "sl_price": 63000.0,
    "size_usdt": 250.0,
    "leverage": 5
  }
}
```

## 6.3 LifecycleEntryGate

Quando genera i comandi di execution, deve includere:

```json
{
  "symbol": "BTC/USDT:USDT",
  "side": "LONG",
  "entry_type": "LIMIT",
  "price": 65000,
  "qty": 0.01,
  "leverage": 5,
  "hedge_mode": false
}
```

Nel caso C/D deve includere anche:

```json
{
  "execution_strategy": "D_POSITION_TPSL",
  "position_idx": 0
}
```

oppure lasciare che l’adapter calcoli `position_idx` da `hedge_mode + side`.

## 6.4 ExecutionGateway

Il gateway non deve più leggere:

```python
adapter_cfg.leverage
```

Deve leggere:

```python
payload["leverage"]
```

e chiamare:

```python
adapter.set_leverage(symbol, leverage, execution_account_id)
```

La chiave di caching deve includere almeno:

```text
execution_account_id
symbol
side/positionIdx se hedge
leverage
```

Esempio:

```python
leverage_key = f"{account}:{symbol}:{position_idx}:{leverage}"
```

Motivo: se trader diversi usano leve diverse, non basta più una chiave solo `account:symbol`.

## 6.5 Adapter Bybit

L’adapter non deve più avere `hedge_mode` come proprietà globale configurata da `execution.yaml`.

Deve ricevere dal payload:

```text
hedge_mode
side
```

e calcolare:

```python
def resolve_position_idx(side: str, hedge_mode: bool) -> int:
    if not hedge_mode:
        return 0
    return 1 if side == "LONG" else 2
```

Questo valore deve essere usato in:

```text
- create_order params
- trading-stop Full
- trading-stop Partial
- move stop
- cancel/update TP/SL
```

---

# 7. Impatto sul modello C/D

## 7.1 C_SIMPLE_ATTACHED

Il comando `PLACE_ENTRY_WITH_ATTACHED_TPSL` deve ricevere da operation/lifecycle:

```json
{
  "leverage": 5,
  "hedge_mode": false,
  "position_idx": 0,
  "attached_tpsl": {
    "take_profit": 70000,
    "stop_loss": 63000
  }
}
```

L’adapter:

```text
1. imposta leverage se necessario;
2. crea entry con attached TP/SL;
3. usa positionIdx corretto.
```

## 7.2 D_POSITION_TPSL

I comandi `SET_POSITION_TPSL_FULL/PARTIAL` devono ricevere:

```json
{
  "symbol": "BTCUSDT",
  "side": "LONG",
  "leverage": 5,
  "hedge_mode": false,
  "position_idx": 0,
  "take_profit": 70000,
  "stop_loss": 63000
}
```

L’adapter:

```text
1. verifica/costruisce positionIdx;
2. chiama /v5/position/trading-stop;
3. non usa hedge_mode globale da config.
```

---

# 8. Validazioni obbligatorie

## 8.1 Startup validation

All’avvio:

```text
- execution.yaml non deve contenere leverage;
- execution.yaml non deve contenere hedge_mode;
- execution.yaml non deve contenere entry_execution;
- execution.yaml non deve contenere capabilities legacy.
```

Se presenti, opzionalmente:

```text
- warning in fase transitoria;
- errore hard dopo migrazione.
```

## 8.2 Signal validation

Per ogni segnale:

```text
risk.leverage <= account.max_leverage
```

Se falso:

```text
BLOCK / REVIEW
```

## 8.3 Hedge validation

Se:

```text
hedge_mode = true
```

allora il sistema deve verificare:

```text
- adapter supporta hedge;
- account/exchange è configurato in hedge mode;
- positionIdx viene passato correttamente.
```

Se non verificabile:

```text
REVIEW_REQUIRED
```

## 8.4 Execution payload validation

Ogni comando entry o TP/SL deve contenere:

```text
symbol
side
leverage
hedge_mode oppure position_idx
```

---

# 9. Migrazione

## Step 1 — Modelli config

Modificare `ExecutionConfig`.

Rimuovere:

```text
leverage
hedge_mode
entry_execution
capabilities
take_profit
position_management
```

Aggiungere:

```text
api_key_env
api_secret_env
strategy
```

## Step 2 — `operation_config.yaml`

Mantenere o rafforzare:

```yaml
risk:
  leverage: ...
```

Mantenere temporaneamente:

```yaml
hedge_mode: false
```

Poi migrare a:

```yaml
position_policy:
  mode: one_way
```

## Step 3 — Risk validation

Implementare:

```text
risk.leverage <= account.max_leverage
```

## Step 4 — Command payload

Aggiungere a tutti i comandi execution:

```text
leverage
hedge_mode / position_idx
execution_strategy
```

## Step 5 — Gateway

Rimuovere uso di `adapter_cfg.leverage`.

## Step 6 — Adapter

Rimuovere `hedge_mode` globale dall’adapter.

## Step 7 — Test

Aggiungere test:

```text
- execution.yaml minimal viene caricato
- execution.yaml con leverage fallisce o produce warning
- execution.yaml con hedge_mode fallisce o produce warning
- risk.leverage > account.max_leverage blocca segnale
- PLACE_ENTRY contiene leverage da operation_config
- hedge_mode true produce positionIdx 1/2
- hedge_mode false produce positionIdx 0
```

---

# 10. Acceptance criteria

## Config

- `execution.yaml` minimale non contiene `leverage`.
- `execution.yaml` minimale non contiene `hedge_mode`.
- `operation_config.yaml` contiene `risk.leverage`.
- `operation_config.yaml` contiene `hedge_mode` o `position_policy.mode`.

## Runtime

- La leva impostata su Bybit deriva dal payload comando.
- Il payload comando deriva da `operation_config.yaml`.
- Nessun componente legge più `adapter_cfg.leverage`.
- Nessun componente legge più `adapter_cfg.hedge_mode`.

## Safety

- Se `risk.leverage > account.max_leverage`, il segnale viene bloccato o mandato in review.
- Se `hedge_mode=true` ma l’adapter/account non supporta hedge, il segnale viene mandato in review.
- In hedge mode, `positionIdx` è sempre valorizzato correttamente.

## Execution

- `C_SIMPLE_ATTACHED` usa `positionIdx` corretto.
- `D_POSITION_TPSL_FULL` usa `positionIdx` corretto.
- `D_POSITION_TPSL_PARTIAL` usa `positionIdx` corretto.
- WebSocket/reconciliation resta configurata solo da `execution.yaml`.

---

# 11. Sintesi

Nuova responsabilità dei file:

```text
operation_config.yaml
  decide cosa è permesso fare:
    - leva
    - hedge
    - rischio
    - sizing
    - gestione posizione
    - regole trader/fonte

execution.yaml
  decide come parlare con l’exchange:
    - adapter
    - API env vars
    - demo/live
    - C/D strategy
    - trigger_by
    - websocket
    - retry
    - live safety
```

Regola finale:

```text
Nessuna policy di trading deve stare in execution.yaml.
Nessun dettaglio tecnico API deve stare in operation_config.yaml.
```

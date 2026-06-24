# Gestione Account Exchange — Istruzioni operative

Questo documento spiega come creare, configurare e assegnare account exchange (Bybit e futuri exchange)
ai trader/fonti del bot. Leggi tutto prima di toccare i file di configurazione.

---

## Indice

1. [Architettura — come funziona il routing](#1-architettura)
2. [File coinvolti](#2-file-coinvolti)
3. [Modalità di assegnazione account](#3-modalità-di-assegnazione-account)
4. [Scenario A — Account singolo condiviso (default)](#4-scenario-a--account-singolo-condiviso)
5. [Scenario B — Un account per trader (subaccount)](#5-scenario-b--un-account-per-trader-subaccount)
6. [Scenario C — Più exchange diversi](#6-scenario-c--più-exchange-diversi)
7. [Come aggiungere un nuovo account exchange step-by-step](#7-come-aggiungere-un-nuovo-account-step-by-step)
8. [Come assegnare un account a una fonte/canale](#8-come-assegnare-un-account-a-una-fonte--canale)
9. [File .env — dove mettere le API key](#9-file-env--dove-mettere-le-api-key)
10. [Gate di sicurezza live trading](#10-gate-di-sicurezza-live-trading)
11. [Riferimento valori supportati](#11-riferimento-valori-supportati)
12. [Checklist rapida](#12-checklist-rapida)

---

## 1. Architettura

Il sistema usa tre livelli distinti per collegare un segnale Telegram a un account exchange:

```
canale Telegram
      │
      │  channels.yaml → trader_id
      ▼
config/traders/<trader_id>.yaml
      │
      │  account.id  (es. "main", "sub_trader_a")
      ▼
config/operation_config.yaml
      │
      │  account_mode → quale blocco account leggere
      ▼
config/execution.yaml
      │
      │  account_routing.<id> → adapter + execution_account_id
      ▼
adapter (ccxt_bybit)
      │
      │  api_key_env / api_secret_env → legge da .env
      ▼
Exchange (Bybit demo / paper / live)
```

**Separazione concettuale:**
- `trader_id` — chi ha mandato il segnale (es. `trader_a`)
- `account_id` — account logico usato per l'esecuzione (es. `"main"`, `"sub_trader_a"`)
- `execution_account_id` — identificatore passato all'adapter/gateway (può coincidere con account_id)
- `adapter` — istanza CCXT configurata con le proprie credenziali (es. `bybit_demo`, `bybit_live`)

---

## 2. File coinvolti

| File | Responsabilità |
|------|---------------|
| `.env` | API key e secret — **mai in git** |
| `config/execution.yaml` | Adapter exchange, routing account logico → adapter |
| `config/operation_config.yaml` | Modalità account (`single` / `per_trader_subaccount`), account globale |
| `config/traders/<id>.yaml` | Override account per singolo trader (solo in `per_trader_subaccount`) |
| `config/channels.yaml` | Mapping canale Telegram → `trader_id` |

---

## 3. Modalità di assegnazione account

Controllata da `account_mode` in `config/operation_config.yaml`:

### `account_mode: single`
Tutti i trader condividono un unico account. I limiti (capitale, leva, rischio) si applicano
globalmente. È la modalità predefinita e la più semplice.

### `account_mode: per_trader_subaccount`
Ogni trader può avere il proprio account/subaccount con credenziali, capitale e limiti separati.
Se un trader non ha un blocco `account` nel suo file yaml, eredita l'account globale.

---

## 4. Scenario A — Account singolo condiviso

Configurazione minima, tutto su Bybit demo con un unico account.

### `.env`
```bash
BYBIT_API_KEY_BYBIT_DEMO=xxxxxxxxxxxx
BYBIT_API_SECRET_BYBIT_DEMO=yyyyyyyyyyyy
```

### `config/operation_config.yaml`
```yaml
account_mode: single

account:
  id: "main"
  capital_base_usdt: 10000.0
  max_leverage: 5
  max_capital_at_risk_pct: 100.0
  hard_max_per_signal_risk_pct: 2.0
```

### `config/execution.yaml`
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
      adjust_for_time_difference: true
      recv_window_ms: 10000
      time_sync_on_startup: true
      strategy:
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

I file `config/traders/*.yaml` non hanno bisogno del blocco `account` — lo ereditano dal globale.

---

## 5. Scenario B — Un account per trader (subaccount)

Ogni trader usa un subaccount Bybit separato con credenziali proprie.

### `.env`
```bash
# Account principale / fallback
BYBIT_API_KEY_MAIN=xxxxxxxxxxxx
BYBIT_API_SECRET_MAIN=yyyyyyyyyyyy

# Subaccount trader_a
BYBIT_API_KEY_TRADER_A=aaaaaaaaaa
BYBIT_API_SECRET_TRADER_A=bbbbbbbbbb

# Subaccount trader_b
BYBIT_API_KEY_TRADER_B=cccccccccc
BYBIT_API_SECRET_TRADER_B=dddddddddd
```

### `config/operation_config.yaml`
```yaml
account_mode: per_trader_subaccount

# Account globale usato come fallback per trader senza blocco account
account:
  id: "main"
  capital_base_usdt: 5000.0
  max_leverage: 5
  max_capital_at_risk_pct: 100.0
  hard_max_per_signal_risk_pct: 2.0
```

### `config/traders/trader_a.yaml`
```yaml
enabled: true
gate_mode: block

account:
  id: "sub_trader_a"          # account logico dedicato
  capital_base_usdt: 3000.0
  max_leverage: 7
  max_capital_at_risk_pct: 80.0
  hard_max_per_signal_risk_pct: 1.5

risk:
  risk_pct_of_capital: 1.0
  max_concurrent_trades: 5
```

### `config/traders/trader_b.yaml`
```yaml
enabled: true
gate_mode: block

account:
  id: "sub_trader_b"
  capital_base_usdt: 2000.0
  max_leverage: 5
  max_capital_at_risk_pct: 100.0
  hard_max_per_signal_risk_pct: 2.0

risk:
  risk_pct_of_capital: 2.0
  max_concurrent_trades: 3
```

### `config/execution.yaml`
```yaml
execution:
  default_adapter: bybit_main

  account_routing:
    # Fallback globale
    default:
      adapter: bybit_main
      execution_account_id: main

    # Routing dedicato per trader_a
    sub_trader_a:
      adapter: bybit_trader_a
      execution_account_id: sub_trader_a

    # Routing dedicato per trader_b
    sub_trader_b:
      adapter: bybit_trader_b
      execution_account_id: sub_trader_b

  adapters:
    bybit_main:
      type: ccxt_bybit
      mode: demo
      connector: bybit
      api_key_env: BYBIT_API_KEY_MAIN
      api_secret_env: BYBIT_API_SECRET_MAIN
      adjust_for_time_difference: true
      recv_window_ms: 10000
      time_sync_on_startup: true
      strategy:
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

    bybit_trader_a:
      type: ccxt_bybit
      mode: demo
      connector: bybit
      api_key_env: BYBIT_API_KEY_TRADER_A
      api_secret_env: BYBIT_API_SECRET_TRADER_A
      adjust_for_time_difference: true
      recv_window_ms: 10000
      time_sync_on_startup: true
      strategy:
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

    bybit_trader_b:
      type: ccxt_bybit
      mode: demo
      connector: bybit
      api_key_env: BYBIT_API_KEY_TRADER_B
      api_secret_env: BYBIT_API_SECRET_TRADER_B
      adjust_for_time_difference: true
      recv_window_ms: 10000
      time_sync_on_startup: true
      strategy:
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

---

## 6. Scenario C — Più exchange diversi

Esempio con trader_a su Bybit demo e trader_b su Bybit live (exchange diversi o ambienti diversi).

### `.env`
```bash
BYBIT_API_KEY_DEMO=xxxxxxxxxxxx
BYBIT_API_SECRET_DEMO=yyyyyyyyyyyy

BYBIT_API_KEY_LIVE=aaaaaaaaaa
BYBIT_API_SECRET_LIVE=bbbbbbbbbb
TSB_ALLOW_LIVE_TRADING=YES_I_UNDERSTAND   # obbligatorio per mode: live
```

### `config/execution.yaml`
```yaml
execution:
  default_adapter: bybit_demo

  account_routing:
    default:
      adapter: bybit_demo
      execution_account_id: main

    account_live:
      adapter: bybit_live
      execution_account_id: live_main

  adapters:
    bybit_demo:
      type: ccxt_bybit
      mode: demo
      connector: bybit
      api_key_env: BYBIT_API_KEY_DEMO
      api_secret_env: BYBIT_API_SECRET_DEMO
      adjust_for_time_difference: true
      recv_window_ms: 10000
      time_sync_on_startup: true
      strategy:
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

    bybit_live:
      type: ccxt_bybit
      mode: live                          # demo | paper | live
      connector: bybit
      api_key_env: BYBIT_API_KEY_LIVE
      api_secret_env: BYBIT_API_SECRET_LIVE
      adjust_for_time_difference: true
      recv_window_ms: 10000
      time_sync_on_startup: true
      strategy:
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
        allow_live_trading: true          # DEVE essere true per mode: live
```

### `config/operation_config.yaml`
```yaml
account_mode: per_trader_subaccount

account:
  id: "main"
  capital_base_usdt: 5000.0
  max_leverage: 5
  max_capital_at_risk_pct: 100.0
  hard_max_per_signal_risk_pct: 2.0
```

### `config/traders/trader_b.yaml`
```yaml
enabled: true
gate_mode: block

account:
  id: "account_live"           # ← punta al routing bybit_live
  capital_base_usdt: 8000.0
  max_leverage: 3
  max_capital_at_risk_pct: 50.0
  hard_max_per_signal_risk_pct: 1.0
```

---

## 7. Come aggiungere un nuovo account step-by-step

### Step 1 — Ottieni le credenziali dall'exchange

Per Bybit: Panel → Account → API Management → Create API Key.
Annota API Key e Secret (il Secret viene mostrato solo una volta).

### Step 2 — Scegli un nome per la env var

Convenzione consigliata: `BYBIT_API_KEY_<NOME>` / `BYBIT_API_SECRET_<NOME>`
dove `<NOME>` è maiuscolo e descrittivo (es. `TRADER_A`, `LIVE_MAIN`, `DEMO_TEST`).

### Step 3 — Aggiungi le credenziali al file `.env`

```bash
# Crea il file se non esiste (nella root del progetto)
BYBIT_API_KEY_NUOVO=la_tua_key
BYBIT_API_SECRET_NUOVO=il_tuo_secret
```

Il file `.env` non deve mai essere committato in git.
Verifica che `.gitignore` contenga la riga `.env`.

### Step 4 — Aggiungi l'adapter in `config/execution.yaml`

```yaml
adapters:
  # ... adapter esistenti ...

  bybit_nuovo:
    type: ccxt_bybit
    mode: demo              # demo | paper | live
    connector: bybit
    api_key_env: BYBIT_API_KEY_NUOVO
    api_secret_env: BYBIT_API_SECRET_NUOVO
    adjust_for_time_difference: true
    recv_window_ms: 10000
    time_sync_on_startup: true
    strategy:
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
      allow_live_trading: false   # true solo per mode: live + env TSB_ALLOW_LIVE_TRADING
```

### Step 5 — Aggiungi il routing in `config/execution.yaml`

```yaml
account_routing:
  # ... routing esistenti ...

  account_nuovo:
    adapter: bybit_nuovo
    execution_account_id: account_nuovo
```

### Step 6 — Assegna l'account a un trader

In `config/operation_config.yaml` imposta `account_mode: per_trader_subaccount`,
poi nel file del trader target (`config/traders/<id>.yaml`):

```yaml
account:
  id: "account_nuovo"      # deve corrispondere alla chiave in account_routing
  capital_base_usdt: 5000.0
  max_leverage: 5
  max_capital_at_risk_pct: 100.0
  hard_max_per_signal_risk_pct: 2.0
```

### Step 7 — Registra il trader in `operation_config.yaml`

```yaml
registered_traders:
  - trader_esistente
  - nuovo_trader        # aggiungilo qui se è un trader nuovo
```

Solo i trader in `registered_traders` ricevono configurazione effettiva dal loader.

---

## 8. Come assegnare un account a una fonte / canale

L'assegnazione canale → trader avviene in `config/channels.yaml`.
L'assegnazione trader → account avviene in `config/traders/<id>.yaml` (in modalità `per_trader_subaccount`).

### Esempio completo

```yaml
# config/channels.yaml
channels:
  - chat_id: -1003722628653
    topic_id: 3
    label: "Canale_TraderA"
    active: true
    trader_id: trader_a        # ← questo trader userà l'account definito in trader_a.yaml
    parser_profile: trader_a
    blacklist: []

  - chat_id: -1003722628653
    topic_id: 4
    label: "Canale_TraderB"
    active: true
    trader_id: trader_b        # ← questo trader userà l'account definito in trader_b.yaml
    parser_profile: trader_b
    blacklist: []
```

```yaml
# config/traders/trader_a.yaml  (account_mode: per_trader_subaccount)
account:
  id: "sub_trader_a"
  capital_base_usdt: 3000.0

# config/traders/trader_b.yaml
account:
  id: "sub_trader_b"
  capital_base_usdt: 2000.0
```

```yaml
# config/execution.yaml
account_routing:
  sub_trader_a:
    adapter: bybit_trader_a
    execution_account_id: sub_trader_a
  sub_trader_b:
    adapter: bybit_trader_b
    execution_account_id: sub_trader_b
```

### Canali multi-trader (risoluzione dinamica)

Se un canale contiene messaggi di più trader identificati da tag/alias,
imposta `trader_id: null` e configura `resolution.aliases` in `channels.yaml`.
In questo caso ogni trader coinvolto deve avere il proprio account configurato
nei rispettivi file `config/traders/<id>.yaml`.

---

## 9. File .env — dove mettere le API key

### Posizione
Nella root del progetto: `/home/user/TRADING_BOT_TELEGRAM/.env`

### Struttura completa di esempio
```bash
# ─── Telegram ────────────────────────────────────────────
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
TELEGRAM_PHONE=+391234567890

# ─── Bybit Demo ──────────────────────────────────────────
BYBIT_API_KEY_BYBIT_DEMO=xxxxxxxxxxxxxxxxxxxxx
BYBIT_API_SECRET_BYBIT_DEMO=yyyyyyyyyyyyyyyyyyyyy

# ─── Bybit Subaccount Trader A ───────────────────────────
BYBIT_API_KEY_TRADER_A=aaaaaaaaaaaaaaaaaaaa
BYBIT_API_SECRET_TRADER_A=bbbbbbbbbbbbbbbbbbbb

# ─── Bybit Subaccount Trader B ───────────────────────────
BYBIT_API_KEY_TRADER_B=cccccccccccccccccccc
BYBIT_API_SECRET_TRADER_B=dddddddddddddddddddd

# ─── Bybit Live (sblocco manuale obbligatorio) ────────────
BYBIT_API_KEY_LIVE=eeeeeeeeeeeeeeeeeeee
BYBIT_API_SECRET_LIVE=ffffffffffffffffffff
TSB_ALLOW_LIVE_TRADING=YES_I_UNDERSTAND
```

### Regole
- Il file `.env` non deve mai essere committato in git.
- I nomi delle env var in `.env` devono corrispondere esattamente ai valori
  `api_key_env` / `api_secret_env` in `config/execution.yaml`.
- Se una env var è assente o vuota al momento dell'avvio, l'adapter viene
  inizializzato con stringa vuota — le chiamate all'exchange falliranno con 401.

---

## 10. Gate di sicurezza live trading

Il bot ha un doppio gate per il live trading. Entrambe le condizioni devono essere vere:

| Gate | Dove si configura | Valore richiesto |
|------|-------------------|-----------------|
| Gate 1 — config | `execution.yaml` → `live_safety.allow_live_trading` | `true` |
| Gate 2 — env | `.env` | `TSB_ALLOW_LIVE_TRADING=YES_I_UNDERSTAND` |

Se uno dei due gate manca, il gateway rifiuta l'esecuzione live e logga un errore.
Per demo e paper il gate live non è richiesto (resta `allow_live_trading: false`).

---

## 11. Riferimento valori supportati

### `account_mode`
| Valore | Comportamento |
|--------|--------------|
| `single` | Unico account globale per tutti i trader |
| `per_trader_subaccount` | Ogni trader può avere account dedicato |

### `mode` adapter
| Valore | Exchange Bybit | Note |
|--------|---------------|------|
| `demo` | Bybit Demo (testnet separato) | API key demo da Bybit Panel |
| `paper` | Bybit Testnet normale | Richiede testnet attivato |
| `live` | Bybit Mainnet reale | Richiede doppio gate sicurezza |

### `type` adapter
| Valore | Descrizione |
|--------|-------------|
| `ccxt_bybit` | Unico adapter implementato. Usa la libreria CCXT per Bybit. |

### `trigger_by` strategy
| Valore | Descrizione |
|--------|-------------|
| `MarkPrice` | Trigger TP/SL su Mark Price (raccomandato per futures) |
| `LastPrice` | Trigger su Last Price |
| `IndexPrice` | Trigger su Index Price |

### `capital_base_mode` risk
| Valore | Fonte capitale per il sizing |
|--------|------------------------------|
| `static_config` | Usa `capital_base_usdt` dal file config |
| `live_equity` | Legge equity reale dall'exchange in tempo reale |

---

## 12. Checklist rapida

Quando aggiungi o modifichi un account, verifica:

- [ ] Credenziali aggiunte in `.env` con nome univoco
- [ ] Adapter aggiunto in `config/execution.yaml` → `adapters`
- [ ] Routing aggiunto in `config/execution.yaml` → `account_routing`
- [ ] `account.id` nel trader yaml corrisponde alla chiave in `account_routing`
- [ ] `account_mode: per_trader_subaccount` in `operation_config.yaml` (se usi account per-trader)
- [ ] Trader presente in `registered_traders` in `operation_config.yaml`
- [ ] Per live: `allow_live_trading: true` nell'adapter E `TSB_ALLOW_LIVE_TRADING=YES_I_UNDERSTAND` in `.env`
- [ ] `.env` non è in git (verifica `.gitignore`)

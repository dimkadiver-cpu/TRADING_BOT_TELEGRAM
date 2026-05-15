# Promemoria — Revisione concettuale `new_operation_tempate.yaml`

## Contesto

Il file:

```text
config/new_operation_tempate.yaml
```

può funzionare come **base concettuale** per definire le policy operative del sistema.

L'idea generale è corretta: usare un file YAML per configurare regole di rischio, correzioni del segnale, gestione TP/SL, gestione update Telegram, pending order e lifecycle.

Però il file non deve diventare un unico blocco confuso gestito da un solo `OperationRulesEngine` monolitico.

La regola principale è:

```text
Un solo file YAML può andare bene.
Ma il runtime deve separare le responsabilità.
```

---

# 1. Giudizio sintetico

## Va bene come concetto

Il file contiene già sezioni utili:

```text
global_hard_caps
registered_traders
global_defaults
risk_mode
entry_split
price_corrections
tp
sl
updates
pending
```

Queste sezioni coprono buona parte delle esigenze operative:

- rischio;
- entry market/limit;
- split entry;
- correzioni prezzi;
- numero di TP da usare;
- distribuzione chiusure sui TP;
- gestione SL;
- update Telegram ammessi;
- timeout ordini pending;
- regole per ordini non fillati.

## Difetto principale

Il file mescola livelli diversi:

```text
risk
signal correction
telegram update admission
lifecycle rules
execution behavior
pending order management
```

Questo va corretto strutturalmente.

---

# 2. Principio architetturale

Il file YAML può restare unico, ma deve essere letto e diviso in modelli interni separati:

```text
OperationConfig
├── RiskPolicy
├── SignalPolicy
├── UpdateAdmissionPolicy
├── LifecyclePolicy
├── PendingPolicy
├── ExecutionPolicy
└── ReconciliationPolicy
```

Non deve esistere un motore generico che decide tutto.

Ogni blocco deve essere usato dal layer corretto.

---

# 3. Dove applicare le sezioni

| Sezione YAML | Layer che la usa |
|---|---|
| `global_hard_caps` | RiskEngine / safety guard |
| `risk` | RiskEngine |
| `signal_policy` | SignalPolicyEngine |
| `entry_split` | SignalPolicyEngine |
| `price_corrections` | SignalPolicyEngine + exchange metadata |
| `tp` | SignalPolicyEngine / LifecycleManager |
| `sl` iniziale | SignalPolicyEngine |
| `update_admission` | UpdateAdmissionPolicy |
| `lifecycle` | LifecycleManager |
| `pending` | LifecycleManager / TimeoutWorker |
| `execution` | ExecutionAdapter |
| `reconciliation` | ReconciliationWorker |

---

# 4. Struttura consigliata

## Schema concettuale

```yaml
global_hard_caps:
  max_capital_at_risk_pct: 10.0
  hard_max_per_signal_risk_pct: 2.0
  max_leverage: 5
  allow_unprotected_positions: false

registered_traders:
  - trader_a
  - trader_b
  - trader_c
  - trader_d
  - trader_3

global_defaults:
  enabled: true
  gate_mode: block

  risk:
    mode: risk_pct_of_capital
    risk_pct_of_capital: 1.0
    risk_usdt_fixed: 10.0
    capital_base_mode: static_config
    capital_base_usdt: 1000.0
    leverage: 1
    max_capital_at_risk_per_trader_pct: 5.0
    max_concurrent_same_symbol: 1

  signal_policy:
    market_execution:
      mode: tolerance
      tolerance_pct: 0.5
      range_tolerance_pct: 0.2

    entry_split:
      mode: by_entry_structure
      max_entries_to_use: 3
      on_extra_entries: ignore_tail
      on_missing_weight: fallback_equal

    price_corrections:
      enabled: false
      methods:
        round_to_tick:
          enabled: true
        clamp_to_exchange_precision:
          enabled: true
        reject_out_of_symbol_range:
          enabled: true

    tp:
      use_tp_count: null
      close_distribution:
        mode: table
        table:
          1: [100]
          2: [50, 50]
          3: [30, 30, 40]

    sl:
      use_original_sl: true
      require_sl: true
      reject_if_missing: true

  update_admission:
    move_stop_price:
      enabled: true

    move_stop_to_be:
      enabled: false

    move_stop_to_tp_level:
      enabled: false

    close_partial:
      enabled: true

    close_full:
      enabled: true

    cancel_pending:
      enabled: true

    add_entry:
      enabled: true

    modify_targets:
      enabled: false

  lifecycle:
    break_even:
      enabled: true
      trigger: tp2
      price_mode: avg_entry_price

    pending:
      cancel_pending_on_timeout: true
      pending_timeout_hours: 24
      chain_timeout_hours: 168
      cancel_averaging_pending_after: null
      cancel_unfilled_pending_after: null

  execution:
    protective_sl:
      required: true
      mode: exchange_native_first
      fallback: bot_managed

  reconciliation:
    enabled: true
    interval_seconds: 30
    on_db_exchange_mismatch: create_recovery_event
```

---

# 5. Correzioni importanti da fare

## 5.1 Separare `MOVE_STOP` da `MOVE_STOP_TO_BE`

Nel file attuale la sezione update è troppo generica.

Esempio problematico:

```yaml
updates:
  apply_move_stop: true
```

Questo non basta, perché `MOVE_STOP` può significare cose diverse:

```text
MOVE_STOP con prezzo esplicito
MOVE_STOP_TO_BE
MOVE_STOP a TP1
MOVE_STOP a TP2
MOVE_STOP implicito da testo tipo "бу"
```

Serve distinguere:

```yaml
update_admission:
  move_stop_price:
    enabled: true

  move_stop_to_be:
    enabled: false

  move_stop_to_tp_level:
    enabled: false
```

Così puoi bloccare gli update Telegram che spostano SL a BE, ma accettare uno stop esplicito con prezzo.

---

## 5.2 Spostare `be_trigger` fuori da `sl`

Se nel file esiste una logica tipo:

```yaml
sl:
  use_original_sl: true
  be_trigger: tp2
```

concettualmente è sbagliata.

`be_trigger` non riguarda lo SL iniziale. Riguarda la gestione della posizione dopo fill/TP.

Corretto:

```yaml
lifecycle:
  break_even:
    enabled: true
    trigger: tp2
    price_mode: avg_entry_price
```

Lo SL iniziale deve restare in:

```yaml
signal_policy:
  sl:
    use_original_sl: true
    require_sl: true
    reject_if_missing: true
```

---

## 5.3 Spostare `market_execution` fuori da `global_hard_caps`

`market_execution` non è un hard cap.

Gli hard cap devono contenere solo vincoli non superabili:

```yaml
global_hard_caps:
  max_capital_at_risk_pct: 10.0
  hard_max_per_signal_risk_pct: 2.0
  max_leverage: 5
  allow_unprotected_positions: false
```

`market_execution` deve stare in:

```yaml
signal_policy:
  market_execution:
    mode: tolerance
```

oppure in:

```yaml
execution:
  market_execution:
    mode: tolerance
```

---

## 5.4 Rendere `entry_split` più robusto

`entry_split` è utile, ma deve gestire casi sporchi.

Esempi:

```text
parser trova 4 entry
policy ne usa massimo 3

parser trova range
policy usa solo endpoints

parser trova market + limit averaging
policy non permette averaging
```

Campi consigliati:

```yaml
entry_split:
  max_entries_to_use: 3
  on_extra_entries: ignore_tail      # ignore_tail | reject | normalize
  on_missing_weight: fallback_equal
```

---

## 5.5 Rendere `price_corrections` esplicito

Evitare nomi ambigui tipo:

```yaml
method: number_theory
```

Meglio usare metodi operativi e testabili:

```yaml
price_corrections:
  enabled: false
  methods:
    round_to_tick:
      enabled: true
    clamp_to_exchange_precision:
      enabled: true
    reject_out_of_symbol_range:
      enabled: true
```

Ogni correzione deve essere auditabile.

---

## 5.6 Aggiungere policy per SL protettivo

Per trading reale serve esplicitare se lo SL deve stare sull'exchange o può essere solo bot-managed.

Aggiungere:

```yaml
execution:
  protective_sl:
    required: true
    mode: exchange_native_first
    fallback: bot_managed
```

Significato:

```text
exchange_native_first = prova a mettere SL nativo exchange
bot_managed = fallback gestito dal bot se nativo non disponibile
required = non lasciare posizione senza protezione
```

---

## 5.7 Aggiungere idempotenza comandi

Con ordini reali, crash e retry sono inevitabili.

Aggiungere:

```yaml
commands:
  idempotency:
    use_client_order_id: true
    duplicate_command_policy: ignore_if_same_payload
```

Serve per evitare doppio ordine in caso di retry.

---

## 5.8 Aggiungere reconciliation

Il DB operativo può divergere dallo stato exchange.

Aggiungere:

```yaml
reconciliation:
  enabled: true
  interval_seconds: 30
  on_db_exchange_mismatch: create_recovery_event
```

La reconciliation non deve essere il ciclo normale di lifecycle. Deve essere un controllo di sicurezza.

---

# 6. Cosa deve restare auditabile

Ogni modifica fatta dalla policy deve essere registrata.

Salvare sempre:

```text
original_signal_payload
policy_adjusted_payload
policy_decisions
policy_warnings
policy_version
```

Esempio:

```text
Il trader manda 6 TP.
La policy ne usa 3.
```

Salvare:

```text
targets_original = [TP1, TP2, TP3, TP4, TP5, TP6]
targets_used = [TP1, TP2, TP3]
targets_ignored = [TP4, TP5, TP6]
policy_reason = MAX_TARGETS_LIMIT
```

---

# 7. Flusso corretto per NEW_SIGNAL

```text
Telegram NEW_SIGNAL
↓
Parser V2
↓
Canonical Event
↓
SignalPolicyEngine
    - normalizza entry
    - limita TP
    - valida SL iniziale
    - applica correzioni prezzo
↓
RiskEngine
    - calcola size
    - valida esposizione
    - valida rischio
↓
OperationalBridge
↓
LifecycleManager
↓
ExecutionAdapter
↓
Hummingbot / Exchange
```

---

# 8. Flusso corretto per UPDATE Telegram

```text
Telegram UPDATE
↓
Parser V2
↓
Canonical Update Event
↓
UpdateAdmissionPolicy
    - accetta/blocca update
↓
se ammesso:
    LifecycleManager
    ↓
    ExecutionAdapter
    ↓
    Hummingbot / Exchange

se bloccato:
    audit only
```

Esempio:

```text
Telegram:
"stop in BE"

Parser:
MOVE_STOP_TO_BE

UpdateAdmissionPolicy:
move_stop_to_be.enabled = false

Output:
BLOCKED
reason = TELEGRAM_BE_DISABLED
```

---

# 9. Flusso corretto per eventi exchange

```text
Exchange/Hummingbot event
↓
ExchangeEventSync
↓
Ops DB
↓
LifecycleManager
↓
eventuale ExecutionCommand
↓
ExecutionAdapter
↓
Hummingbot / Exchange
```

Esempio:

```text
TP2_FILLED
↓
LifecycleManager
↓
rule: break_even.trigger = tp2
↓
command: REPLACE_SL_AT_BREAKEVEN
```

---

# 10. Regola importante sui commenti del file

Se nel file è scritto che il trader può sempre inviare uno stop esplicito, BE o riferimento TP, il commento va corretto.

Formula sbagliata:

```text
Il trader può sempre inviare SET_STOP.
```

Formula corretta:

```text
Il parser può estrarre SET_STOP.
L'esecuzione reale dipende da UpdateAdmissionPolicy.
Solo dopo il LifecycleManager genera un comando operativo.
```

Questa distinzione è fondamentale.

---

# 11. Rischio se non si corregge

Se il file resta troppo monolitico, i rischi sono:

```text
- parser che inizia a decidere execution
- risk engine che corregge semantica del segnale
- lifecycle manager che interpreta testo Telegram
- execution adapter che decide regole operative
- update Telegram eseguiti senza filtro fine
- difficile testare singole regole
- difficile fare backtest coerente
- difficile spiegare perché un ordine è stato eseguito
```

---

# 12. Raccomandazione finale

Il file `new_operation_tempate.yaml` può andare come base, ma va rifinito prima dell'implementazione.

Azioni consigliate:

```text
1. Mantieni un solo file YAML.
2. Dividi le sezioni per responsabilità.
3. Crea modelli interni separati:
   RiskPolicy
   SignalPolicy
   UpdateAdmissionPolicy
   LifecyclePolicy
   ExecutionPolicy
4. Separa MOVE_STOP da MOVE_STOP_TO_BE.
5. Sposta be_trigger dentro lifecycle.break_even.
6. Sposta market_execution fuori da global_hard_caps.
7. Aggiungi protective_sl.
8. Aggiungi idempotency.
9. Aggiungi reconciliation.
10. Rendi tutte le correzioni auditabili.
```

Sintesi:

```text
YAML unico: sì.
Motore unico che decide tutto: no.
Policy separate e testabili: sì.
```

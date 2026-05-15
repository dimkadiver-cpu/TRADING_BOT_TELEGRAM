# Promemoria — Separazione tra DB Parser e DB Operativo

## Obiettivo

Separare nettamente due domini:

```text
DB Parser
    conserva ciò che è arrivato da Telegram
    e come è stato interpretato

DB Operativo
    conserva ciò che il sistema ha deciso di fare
    e come sta gestendo ordini/posizioni/lifecycle
```

Regola principale:

```text
Il DB Parser conserva ciò che il trader ha detto.
Il DB Operativo conserva ciò che il sistema ha deciso di fare.
```

---

# 1. DB Parser

## Scopo

Il DB Parser serve per audit, sviluppo parser, replay, diagnostica e ricostruzione del messaggio originale.

Risponde a domande tipo:

```text
Che messaggio è arrivato?
Da quale canale/topic?
Da quale trader?
Come è stato parsato?
Che intent sono stati estratti?
Il parser era sicuro o ambiguo?
Quale evento canonico ha prodotto?
```

## Contenuto

Tabelle/aree consigliate:

```text
raw_messages
parser_results
canonical_events
message_refs
parser_diagnostics
trader_resolution
processing_status
```

## Cosa contiene

Esempio:

```text
Messaggio Telegram:
"BTC long entry 65000 TP 66000 67000 SL 64000"

DB Parser:
message_type = NEW_SIGNAL
intent = CREATE_SIGNAL
symbol = BTC-USDT
side = LONG
entry = 65000
targets = [66000, 67000]
stop_loss = 64000
parse_status = OK
diagnostics = [...]
```

## Cosa NON deve contenere

Il DB Parser non deve decidere:

```text
- se il segnale verrà eseguito;
- quale size usare;
- quanti TP usare davvero;
- se modificare SL;
- se accettare update futuri;
- se piazzare ordini;
- se chiudere una posizione;
- se spostare SL a BE.
```

Il parser produce dati interpretati, non decisioni operative.

---

# 2. DB Operativo

## Scopo

Il DB Operativo serve per esecuzione reale/simulata, lifecycle, ordini, posizioni, rischio, audit operativo e reconciliation con exchange.

Risponde a domande tipo:

```text
Questo segnale è stato accettato?
Quali regole sono state applicate?
Che versione corretta del segnale uso?
Quale size è stata calcolata?
Che ordini sono stati creati?
La posizione è aperta?
TP1 è stato colpito?
SL è stato spostato a BE?
Quali update Telegram sono stati accettati o bloccati?
Lo stato DB coincide con l'exchange?
```

## Contenuto

Tabelle/aree consigliate:

```text
ops_trade_chains
ops_policy_decisions
ops_update_admission_events
ops_orders
ops_fills
ops_positions
ops_exchange_events
ops_lifecycle_events
ops_execution_commands
ops_warnings
ops_reconciliation_logs
```

---

# 3. Cosa entra nel DB Operativo

Il DB Operativo conserva il segnale dopo il passaggio da:

```text
Parser output
↓
SignalPolicyEngine
↓
RiskEngine
↓
OperationalBridge
↓
LifecycleManager
```

Quindi conserva:

```text
- versione originale importata dal canonical_event;
- versione corretta dalle policy;
- decisioni di policy;
- decisioni di rischio;
- trade_chain;
- ordini;
- fill;
- posizione;
- eventi exchange;
- eventi lifecycle;
- comandi inviati a Hummingbot/exchange;
- decisioni sugli update Telegram.
```

---

# 4. Differenza chiave

## DB Parser

```text
targets_original = [TP1, TP2, TP3, TP4, TP5]
```

## DB Operativo

```text
targets_used = [TP1, TP2, TP3]
targets_ignored = [TP4, TP5]
policy_reason = MAX_TP_COUNT_3
policy_version = v1
```

Il DB Parser dice cosa ha scritto il trader.  
Il DB Operativo dice cosa il sistema userà davvero.

---

# 5. Gestione update Telegram

Gli update Telegram seguono questo flusso:

```text
Telegram UPDATE
↓
Parser V2
↓
Canonical Update Event nel DB Parser
↓
UpdateAdmissionPolicy
↓
DB Operativo:
    ACCEPTED / BLOCKED / IGNORED
↓
se ACCEPTED:
    LifecycleManager
    ↓
    ExecutionCommand
```

## Esempio: update bloccato

```text
Telegram:
"stop in BE"
```

DB Parser:

```text
message_type = UPDATE
intent = MOVE_STOP_TO_BE
ref = trade_chain_id / signal reference
```

Policy:

```text
move_stop_to_be.enabled = false
```

DB Operativo:

```text
update_intent = MOVE_STOP_TO_BE
decision = BLOCKED
reason = TELEGRAM_BE_DISABLED
execution_command = null
```

## Esempio: update accettato

```text
Telegram:
"close full"
```

DB Parser:

```text
message_type = UPDATE
intent = CLOSE_FULL
```

DB Operativo:

```text
update_intent = CLOSE_FULL
decision = ACCEPTED
lifecycle_event = CLOSE_FULL_REQUESTED
execution_command = CLOSE_POSITION
```

---

# 6. Dove stanno le policy

Le policy statiche stanno nella config, per esempio:

```yaml
update_admission:
  move_stop_to_be:
    enabled: false

  move_stop_price:
    enabled: true

  close_full:
    enabled: true

  close_partial:
    enabled: true

  cancel_pending:
    enabled: true

  modify_targets:
    enabled: false
```

Nel DB Operativo non serve salvare solo la config astratta.

Serve salvare la decisione concreta presa per ogni evento reale:

```text
source_canonical_event_id
trade_chain_id
update_intent
decision
reason
policy_version
created_at
```

---

# 7. NEW_SIGNAL — flusso completo

```text
Telegram NEW_SIGNAL
↓
DB Parser:
    raw_message
    parser_result
    canonical_event
↓
SignalPolicyEngine:
    corregge/filtra entry, TP, SL
↓
RiskEngine:
    calcola size e valida rischio
↓
DB Operativo:
    trade_chain
    policy_decisions
    risk_decision
    execution_command
↓
ExecutionAdapter:
    Hummingbot / Exchange
↓
ExchangeEventSync:
    aggiorna ordini/fill/posizione
```

## Esempio

DB Parser:

```text
symbol = BTC-USDT
side = LONG
entry = 65000
targets = [66000, 67000, 68000]
stop_loss = 64000
```

DB Operativo:

```text
trade_chain_id = 10
symbol = BTC-USDT
side = LONG
entry_used = 65000
targets_used = [66000, 67000]
targets_ignored = [68000]
stop_loss_used = 64000
risk_pct = 1%
position_size = 0.02 BTC
lifecycle_state = WAITING_ENTRY_FILL
```

---

# 8. UPDATE — flusso completo

```text
Telegram UPDATE
↓
DB Parser:
    canonical update event
↓
UpdateAdmissionPolicy:
    decide ACCEPTED / BLOCKED / IGNORED
↓
DB Operativo:
    update_admission_event
↓
se ACCEPTED:
    LifecycleManager
    ↓
    execution_command
```

---

# 9. Evento Exchange — flusso completo

```text
Exchange / Hummingbot
↓
ExchangeEventSync
↓
DB Operativo:
    ops_exchange_events
    ops_orders
    ops_fills
    ops_positions
↓
LifecycleManager
↓
eventuale comando successivo
```

Esempio:

```text
TP2_FILLED
↓
LifecycleManager legge policy:
    move SL to BE after TP2
↓
execution_command:
    REPLACE_SL_AT_BREAKEVEN
```

Questo non passa dal DB Parser, perché non è un messaggio Telegram. È un evento operativo reale.

---

# 10. Regole di separazione

## Regola 1

```text
Il parser non scrive ordini.
```

Il parser scrive solo:

```text
raw_messages
parser_results
canonical_events
diagnostics
```

## Regola 2

```text
Il DB Operativo non deve perdere il riferimento al DB Parser.
```

Ogni trade_chain deve poter risalire a:

```text
source_message_id
source_canonical_event_id
source_trader_id
source_chat_id
source_topic_id
```

## Regola 3

```text
Le correzioni non devono sovrascrivere l'originale.
```

Salvare sempre:

```text
original_payload
policy_adjusted_payload
policy_decisions
policy_version
```

## Regola 4

```text
Gli update Telegram bloccati restano salvati.
```

Anche se non vengono eseguiti, servono per audit.

## Regola 5

```text
L'exchange è la fonte finale della verità operativa.
```

Il DB Operativo deve poter fare reconciliation.

---

# 11. Schema mentale finale

```text
parser_db
    raw_messages
    parser_results
    canonical_events
    diagnostics

        ↓ bridge

ops_db
    imported_canonical_events
    policy_decisions
    risk_decisions
    trade_chains
    update_admission_events
    lifecycle_events
    execution_commands
    orders
    fills
    positions
    exchange_events
    reconciliation_logs
```

---

# 12. Sintesi finale

## DB Parser

```text
Serve a sapere cosa è stato detto e come è stato interpretato.
```

Contiene:

```text
messaggi Telegram
parse result
canonical events
diagnostica parser
riferimenti messaggi
risoluzione trader
```

## DB Operativo

```text
Serve a sapere cosa il sistema ha deciso e cosa sta succedendo realmente.
```

Contiene:

```text
segnale corretto dalle policy
decisioni di rischio
trade chain
ordini
fill
posizioni
eventi exchange
decisioni lifecycle
decisioni sugli update Telegram
comandi operativi
```

Formula finale:

```text
DB Parser = verità testuale/canonica del segnale.
DB Operativo = verità decisionale e operativa del sistema.
```

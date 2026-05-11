# Promemoria — Dove applicare regole, correzioni e gestione operativa

## Obiettivo

Separare correttamente le responsabilità tra parser, policy, rischio, lifecycle ed execution.

Regola base:

```text
Parser = capisce il messaggio
Policy = decide cosa è ammesso/corretto
RiskEngine = decide se e quanto tradare
LifecycleManager = gestisce la vita della posizione
ExecutionAdapter = esegue tecnicamente su Hummingbot/exchange
```

---

## Flusso generale

```text
Telegram message
↓
Parser V2
↓
Canonical Event
↓
SignalPolicyEngine / UpdateAdmissionPolicy
↓
RiskEngine
↓
OperationalBridge
↓
LifecycleManager
↓
ExecutionAdapter
↓
Hummingbot / Exchange
↓
ExchangeEventSync
↓
Ops DB
```

---

# 1. Parser V2

## Responsabilità

Il parser deve solo trasformare testo Telegram in evento canonico.

Deve estrarre:

- tipo messaggio: `NEW_SIGNAL`, `UPDATE`, `INFO_ONLY`, ecc.
- intent: `CREATE_SIGNAL`, `MOVE_STOP`, `MOVE_STOP_TO_BE`, `CLOSE_FULL`, ecc.
- entità: symbol, side, entry, TP, SL, leverage, riferimenti, quantità, ecc.
- diagnostica: ambiguità, dati mancanti, fallback usati, confidence.

## Non deve fare

Il parser non deve:

- decidere se eseguire il segnale;
- calcolare size;
- correggere TP/SL secondo policy operative;
- decidere se accettare update Telegram;
- spostare SL a BE;
- applicare regole di rischio;
- inviare ordini.

---

# 2. SignalPolicyEngine

## Quando si usa

Si usa sui `NEW_SIGNAL`, dopo il parser e prima del rischio.

## Responsabilità

Applica correzioni e policy sul contenuto del segnale.

Esempi:

- limitare il numero di TP usati;
- ignorare TP extra;
- normalizzare entry;
- normalizzare SL;
- correggere prezzi secondo regole definite;
- applicare default;
- bloccare segnali incompleti;
- scartare segnali senza SL;
- scartare segnali con struttura non accettata;
- applicare regole specifiche per trader.

## Esempio

```text
Input parser:
targets = [TP1, TP2, TP3, TP4, TP5]

Policy:
max_targets = 3

Output:
targets_used = [TP1, TP2, TP3]
targets_ignored = [TP4, TP5]
```

## Nota

Le correzioni devono essere auditabili.

Conservare sempre:

```text
original_signal_payload
policy_adjusted_payload
policy_decisions
policy_warnings
```

---

# 3. UpdateAdmissionPolicy

## Quando si usa

Si usa sugli update provenienti da Telegram.

Serve a decidere se un update Telegram può modificare realmente la posizione.

## Responsabilità

Accettare, bloccare o degradare update Telegram.

Esempi:

- bloccare `MOVE_STOP_TO_BE`;
- accettare `CLOSE_FULL`;
- accettare `CLOSE_PARTIAL`;
- accettare `CANCEL_PENDING`;
- bloccare `MODIFY_TARGETS`;
- accettare solo update con riferimento chiaro;
- accettare solo update da trader ammessi;
- bloccare update ambigui;
- bloccare update su chain già chiuse.

## Esempio config

```yaml
telegram_updates:
  MOVE_STOP_TO_BE:
    enabled: false

  MOVE_STOP:
    enabled: true

  CLOSE_FULL:
    enabled: true

  CLOSE_PARTIAL:
    enabled: true

  CANCEL_PENDING:
    enabled: true

  MODIFY_TARGETS:
    enabled: false
```

## Esempio operativo

```text
Telegram update:
"stop in BE"

Parser:
intent = MOVE_STOP_TO_BE

UpdateAdmissionPolicy:
MOVE_STOP_TO_BE.enabled = false

Output:
event_status = BLOCKED
reason = TELEGRAM_BE_DISABLED
```

L'evento resta salvato per audit, ma non viene eseguito.

---

# 4. RiskEngine

## Quando si usa

Dopo `SignalPolicyEngine`, prima di creare ordini reali.

## Responsabilità

Gestisce rischio e calcolo posizione.

Esempi:

- calcolo size;
- rischio percentuale per trade;
- rischio massimo giornaliero;
- rischio massimo per trader;
- rischio massimo per simbolo;
- max posizioni aperte;
- max esposizione totale;
- controllo distanza SL;
- controllo RR minimo;
- controllo min/max leverage;
- controllo min order size;
- validazione precisione quantità/prezzo.

## Esempio

```text
Equity = 1000 USDT
Risk = 1%
Entry = 65000
SL = 64000

risk_amount = 10 USDT
distance_to_stop = 1.54%
notional = risk_amount / 0.0154
```

## Output possibile

```text
APPROVED
REJECTED_BY_RISK
SIZE_ADJUSTED
LEVERAGE_ADJUSTED
```

---

# 5. OperationalBridge

## Responsabilità

Trasforma eventi canonici ammessi in eventi operativi.

Esempio:

```text
Canonical NEW_SIGNAL
↓
validated signal
↓
trade_chain
↓
initial execution command
```

Oppure:

```text
Canonical UPDATE
↓
admitted update
↓
lifecycle_event
```

## Regola importante

Il parser non deve scrivere direttamente ordini.

Solo `OperationalBridge` / `LifecycleManager` possono trasformare eventi ammessi in comandi operativi.

---

# 6. LifecycleManager

## Quando si usa

Dopo la creazione della `trade_chain` e durante tutta la vita della posizione.

Reagisce a:

- eventi exchange;
- fill;
- cancellazioni;
- timeout;
- update Telegram ammessi;
- price-level events;
- reconciliation events.

## Responsabilità

Gestisce la posizione nel tempo.

Esempi:

- se TP1 viene colpito, chiudi parziale;
- se TP2 viene colpito, sposta SL a BE;
- se entry limit non viene fillata entro X tempo, cancella;
- se prezzo raggiunge TP prima dell'entry pending, cancella entry;
- se posizione viene chiusa, cancella ordini residui;
- se SL manca, genera warning o ricrea SL;
- se update Telegram `CLOSE_FULL` è ammesso, chiude posizione;
- se update Telegram `CANCEL_PENDING` è ammesso, cancella ordini pendenti.

## Esempio

```text
Exchange event:
TP2_FILLED

Lifecycle rule:
MOVE_SL_TO_BE_AFTER_TP2 = true

Output:
execution_command = REPLACE_SL_AT_BREAKEVEN
```

## Non deve fare

Il `LifecycleManager` non deve riparsare testo Telegram.

Lavora solo su eventi già normalizzati.

---

# 7. ExecutionAdapter

## Responsabilità

Traduce comandi operativi in chiamate tecniche verso Hummingbot/exchange.

Esempi:

- piazza ordine;
- cancella ordine;
- modifica ordine;
- chiude posizione;
- imposta leverage;
- legge stato ordine;
- legge stato posizione;
- crea/usa executor Hummingbot.

## Non deve decidere

Non deve decidere:

- se un update Telegram è ammesso;
- quanti TP usare;
- se SL deve andare a BE dopo TP2;
- se il rischio è accettabile;
- se un segnale è valido.

Riceve comandi già decisi.

---

# 8. ExchangeEventSync

## Responsabilità

Legge eventi da Hummingbot/exchange e li normalizza nel DB operativo.

Esempi di eventi:

```text
ORDER_FILLED
ORDER_PARTIALLY_FILLED
ORDER_CANCELLED
ORDER_REJECTED
POSITION_UPDATED
POSITION_CLOSED
PRICE_LEVEL_TOUCHED
```

## Regola

L'exchange è la fonte finale della verità.

```text
Exchange = stato reale
Hummingbot = canale operativo/monitor
Ops DB = copia auditabile
LifecycleManager = decision maker
```

---

# 9. Dove applicare ogni regola

| Regola / Correzione | Dove applicarla |
|---|---|
| Estrarre entry, TP, SL dal testo | `Parser V2` |
| Limitare numero TP | `SignalPolicyEngine` |
| Correggere prezzi iniziali | `SignalPolicyEngine` |
| Arrotondare secondo precisione exchange | `SignalPolicyEngine` + metadata exchange |
| Bloccare segnale senza SL | `SignalPolicyEngine` |
| Calcolare size | `RiskEngine` |
| Validare rischio massimo | `RiskEngine` |
| Bloccare update Telegram `MOVE_STOP_TO_BE` | `UpdateAdmissionPolicy` |
| Accettare update `CLOSE_FULL` | `UpdateAdmissionPolicy` |
| Accettare update `CANCEL_PENDING` | `UpdateAdmissionPolicy` |
| TP2 hit → SL a BE | `LifecycleManager` |
| Entry pending timeout → cancel | `LifecycleManager` |
| Prezzo tocca TP prima di entry → cancel pending | `LifecycleManager` |
| Invio ordine a exchange | `ExecutionAdapter` |
| Aggiornamento fill/posizione da exchange | `ExchangeEventSync` |

---

# 10. Flussi pratici

## NEW_SIGNAL

```text
Telegram NEW_SIGNAL
↓
Parser V2
↓
SignalPolicyEngine
    - corregge/filtra TP
    - valida SL iniziale
    - normalizza entry
↓
RiskEngine
    - calcola size
    - valida esposizione
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

## UPDATE Telegram

```text
Telegram UPDATE
↓
Parser V2
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

---

## Evento Exchange

```text
Exchange / Hummingbot event
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
```

---

# 11. DB operativo: concetto minimo

Tabelle principali:

```text
ops_trade_chains
ops_orders
ops_fills
ops_positions
ops_exchange_events
ops_lifecycle_events
ops_execution_commands
ops_warnings
```

## Nota su ordini

Non usare due tabelle separate `ordini_attivi` e `ordini_eseguiti`.

Usare:

```text
ops_orders = tutti gli ordini
ops_fills = esecuzioni/fill reali
```

Gli ordini attivi/eseguiti si ottengono con view/query:

```sql
SELECT *
FROM ops_orders
WHERE status IN ('NEW', 'OPEN', 'PARTIALLY_FILLED');
```

```sql
SELECT *
FROM ops_orders
WHERE status = 'FILLED';
```

---

# 12. Worker lifecycle

Il worker non deve scansionare tutto il DB.

Deve consumare eventi nuovi:

```sql
SELECT *
FROM ops_exchange_events
WHERE processing_status = 'NEW'
ORDER BY received_at
LIMIT 100;
```

Poi:

```text
1. carica solo la trade_chain coinvolta
2. applica regole
3. genera execution_command
4. marca evento come DONE
```

Per timeout usa query mirate:

```sql
SELECT *
FROM ops_trade_chains
WHERE lifecycle_state = 'WAITING_ENTRY_FILL'
  AND entry_timeout_at <= CURRENT_TIMESTAMP;
```

---

# 13. Regola architetturale principale

```text
Regole sul contenuto del segnale
→ SignalPolicyEngine

Regole su quali update Telegram accettare
→ UpdateAdmissionPolicy

Regole economiche/rischio
→ RiskEngine

Regole sulla posizione già attiva
→ LifecycleManager

Comandi tecnici verso exchange/Hummingbot
→ ExecutionAdapter

Stato reale exchange
→ ExchangeEventSync
```

---

# 14. Punto critico

Non mettere le regole operative nel parser.

Errore da evitare:

```text
Parser riceve "stop in BE"
↓
parser decide di spostare SL
↓
parser genera ordine
```

Corretto:

```text
Parser riceve "stop in BE"
↓
produce canonical intent MOVE_STOP_TO_BE
↓
UpdateAdmissionPolicy decide se ammesso
↓
LifecycleManager decide azione concreta
↓
ExecutionAdapter esegue
```

---

# 15. Sintesi finale

La separazione robusta è:

```text
Parser V2
    capisce il messaggio

SignalPolicyEngine
    corregge e normalizza il segnale iniziale

UpdateAdmissionPolicy
    decide quali update Telegram possono agire

RiskEngine
    calcola size e valida rischio

LifecycleManager
    gestisce posizione, TP, SL, BE, timeout, cancel

ExecutionAdapter
    invia comandi tecnici

ExchangeEventSync
    aggiorna DB con eventi reali
```

Questa struttura mantiene auditabilità, controllo e possibilità di cambiare Hummingbot/exchange senza riscrivere parser e policy.

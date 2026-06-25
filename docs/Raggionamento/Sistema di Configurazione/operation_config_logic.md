# `operation_config.yaml` — logica effettiva, precedenze e stato di implementazione

**Repository:** `dimkadiver-cpu/TRADING_BOT_TELEGRAM`  
**File analizzato:** `config/operation_config.yaml`  
**Snapshot del file:** SHA `5aac51c94fe081d76edf87a42aca79ae57213687`  
**Analisi del runtime:** loader, enrichment, risk gate, lifecycle e gestione eventi.  
**Verifica:** 25 giugno 2026.

> Questo documento non descrive solo i commenti YAML. Distingue:
>
> - **Attivo**: il valore viene letto e influenza un percorso runtime verificato.
> - **Parzialmente attivo**: viene caricato ma agisce solo in una parte del flusso, oppure con limiti.
> - **Inattivo / non propagato**: il campo esiste nel YAML o nei modelli ma non modifica il comportamento reale corrente.
>
> Un campo presente nella configurazione **non deve essere considerato una garanzia di comportamento** finché non è classificato come attivo.

---

# 1. Modello operativo

```text
config/operation_config.yaml
        │
        ├── loader YAML
        │     ├── controllo trader registrato
        │     ├── merge default + override trader
        │     ├── account globale o account trader
        │     └── snapshot policy nel messaggio enriched
        │
        ▼
Signal Enrichment
        │
        ├── blacklist
        ├── struttura entry ammessa
        ├── SL obbligatorio
        ├── TP trimming
        ├── split/range/riordino entry
        └── admission update
        │
        ▼
Lifecycle Entry Gate
        │
        ├── controlli simbolo
        ├── risk sizing e limiti
        ├── piano ordini
        └── timeout pending
        │
        ▼
Execution Gateway / Exchange
        │
        ▼
Lifecycle Event Processor
        │
        ├── fill entry
        ├── TP
        ├── SL
        ├── BE
        └── cancel automatico averaging
```

Il file non viene usato come configurazione “live” in ogni passaggio.  
Per ogni messaggio ammesso, l’enrichment salva un **`policy_snapshot`** completo nel record enriched. Il lifecycle successivo dovrebbe quindi basarsi sullo snapshot associato a quel messaggio, non sul file YAML modificato dopo.

---

# 2. Precedenza e merge delle configurazioni

## 2.1 Regola generale

Per un trader registrato:

```text
effective config =
deep_merge(defaults, config/traders/<trader_id>.yaml senza account)
```

La fusione è ricorsiva per i dizionari:

```text
base:
  risk:
    leverage: 10
    risk_pct_of_capital: 0.5

override:
  risk:
    leverage: 20

risultato:
  risk:
    leverage: 20
    risk_pct_of_capital: 0.5
```

Le liste non vengono concatenate: l’override sostituisce l’intera lista.

## 2.2 `account_mode`

| Valore | Risoluzione account |
|---|---|
| `single` | usa sempre il blocco top-level `account` |
| qualunque altro valore | prova a usare `config/traders/<id>.yaml → account`; se assente usa il globale |

### Problema di validazione

Il runtime non valida formalmente `account_mode`.

```python
if account_mode == "single":
    global account
else:
    trader account fallback globale
```

Quindi un errore come:

```yaml
account_mode: per_trader_subacount
```

non genera un blocco: viene trattato implicitamente come `per_trader_subaccount`.

**Stato:** parzialmente sicuro. Il valore supportato è commentato nel YAML, ma non è imposto dal loader.

## 2.3 Override `account`: non è deep merge

Per `account_mode: per_trader_subaccount`:

```text
effective_account =
trader.account se esiste
altrimenti global account
```

Il blocco `account` trader sostituisce l’intero blocco globale, non solo le chiavi specificate.

Esempio rischioso:

```yaml
# config/traders/trader_x.yaml
account:
  id: "sub_1"
```

Il runtime non eredita automaticamente:

```yaml
capital_base_usdt: 10000
max_leverage: 25
max_capital_at_risk_pct: 100
hard_max_per_signal_risk_pct: 2
```

e usa invece i default interni del modello:

```text
capital_base_usdt = 1000
max_leverage = 10
max_capital_at_risk_pct = 10
hard_max_per_signal_risk_pct = 2
```

**Conclusione:** un override trader di `account` deve contenere tutti i campi necessari; non è sicuro definirne uno parziale.

## 2.4 Registrazione trader

Un trader non presente in `registered_traders` riceve:

```text
enrichment_decision = BLOCK
reason_code = trader_not_registered
```

Un trader presente nella lista ma senza file `config/traders/<id>.yaml` usa i `defaults`.

## 2.5 Hot reload

Il loader controlla solo la modifica di:

```text
config/operation_config.yaml
```

Non controlla il timestamp dei file:

```text
config/traders/<trader_id>.yaml
```

Quindi:

```text
modifica solo trader_x.yaml
    → non viene ricaricata automaticamente

modifica operation_config.yaml
    → il loader ricarica anche gli override trader al passaggio successivo
```

Per rendere effettiva una modifica solo al file trader, serve riavvio oppure una modifica del file globale.

## 2.6 Versione policy e audit

Il messaggio enriched salva:

- `policy_snapshot`: configurazione effettiva del trader;
- `policy_version`: hash SHA-256.

Il problema è che il processor calcola `policy_version` senza passare il trader:

```python
policy_version = self._config.get_policy_version()
```

Quindi l’hash è costruito sul YAML globale e **non include gli override trader**, mentre lo snapshot sì.

Conseguenza:

```text
stesso policy_version
≠ stessa configurazione effettiva
```

per trader diversi o per cambiamenti nel loro file override.

Per audit e debugging, lo snapshot resta la fonte affidabile; `policy_version` non è una chiave affidabile dell’effettiva policy trader-specifica.

---

# 3. Snapshot della configurazione corrente

## 3.1 Profilo attuale globale

```yaml
global_safety.allow_unprotected_positions: false
account_mode: per_trader_subaccount
defaults.enabled: true
defaults.gate_mode: block
defaults.hedge_mode: true
```

## 3.2 Policy segnale

```yaml
accepted_entry_structures:
  - ONE_SHOT
  - TWO_STEP
  - RANGE
  - LADDER

market_execution:
  mode: tolerance
  tolerance_pct: 0.5
  range_tolerance_pct: 0.2

tp.use_tp_count: null
sl.require_sl: true
sl.use_original_sl: true
```

## 3.3 Management plan

```yaml
be_trigger: tp1
be_fee_correction_enabled: true
be_fee_fallback_profile: null

close_distribution:
  mode: table
  1: [100]
  2: [50, 50]
  3: [30, 30, 40]
  4: [25, 25, 25, 25]
  5: [20, 20, 20, 20, 20]
  6: [20, 20, 20, 20, 10, 10]

cancel_pending_by_engine: true
cancel_pending_on_timeout: true
pending_timeout_hours: 24
cancel_averaging_pending_after: tp1
cancel_unfilled_pending_after: null
risk_freed_by_be: true
protective_sl_mode: exchange_native_first
market_convert_mode: cancel_subsequent
```

## 3.4 Risk

```yaml
mode: risk_pct_of_capital
risk_pct_of_capital: 0.5
capital_base_mode: static_config
capital_base_usdt: 10000
leverage: 10
use_trader_risk_hint: true
risk_hint_range_mode: min_value
max_capital_at_risk_per_trader_pct: 100
max_concurrent_trades: 50
max_concurrent_same_symbol: 1
```

---

# 4. Stato sintetico di tutti i campi

| Blocco | Campo | Stato | Nota |
|---|---|---|---|
| `global_safety` | `allow_unprotected_positions` | **Inattivo** | non raggiunge l’effective config e non ha consumer runtime |
| root | `account_mode` | Attivo, senza validazione | qualunque valore diverso da `single` entra nel ramo per-trader |
| root | `account.id` | Attivo | seleziona account logico/exchange |
| root | `account.max_leverage` | Attivo | hard cap sul leverage configurato nel risk profile |
| root | `account.capital_base_usdt` | Inattivo nel risk sizing | il sizing usa `risk.capital_base_usdt` |
| root | `account.max_capital_at_risk_pct` | Inattivo nel risk gate | il risk gate usa `risk.max_capital_at_risk_per_trader_pct` |
| root | `account.hard_max_per_signal_risk_pct` | Inattivo | nessun consumer runtime trovato |
| root | `registered_traders` | Attivo | blocca trader non registrati |
| root | `symbol_blacklist` | Attivo | blocco pre-enrichment |
| `defaults` | `enabled` | Attivo | blocca l’intero profilo trader |
| `defaults` | `gate_mode` | Parzialmente attivo | modifica solo gli update disabilitati; non i signal gate |
| `defaults` | `hedge_mode` | Attivo | determina `position_idx` e consente long/short separati |
| `signal_policy` | `accepted_entry_structures` | Attivo | blocca strutture non ammesse |
| `signal_policy` | `market_execution.*` | **Inattivo** | nessun consumer runtime trovato |
| `signal_policy` | `entry_split.*` | Attivo | pesi, derivazione range, riordino |
| `signal_policy` | `tp.use_tp_count` | Attivo ma limitato | taglia solo TP eccedenti |
| `signal_policy` | `sl.require_sl` | Attivo | blocca segnali senza SL |
| `signal_policy` | `sl.use_original_sl` | **Inattivo** | nessun consumer runtime trovato |
| `signal_policy` | `price_corrections.*` | **Inattivo** | nessun consumer runtime trovato |
| `signal_policy` | `price_sanity.*` | Attivo ma parziale | controlla solo i TP |
| `update_admission` | tutti | Attivo | un’azione disabilitata blocca/review l’intero update |
| `management_plan` | `be_trigger` | Attivo | move SL a BE su TP definito |
| `management_plan` | `be_fee_correction_*` | Configurato ma effettivamente non operativo | fallback profile `null` forza BE puro |
| `management_plan` | `close_distribution` | Attivo | fallback equal per conteggi non presenti nella tabella |
| `management_plan` | `cancel_pending_by_engine` | Attivo | abilita il cancel automatico averaging |
| `management_plan` | `cancel_pending_on_timeout` | Attivo | crea timeout per chain waiting |
| `management_plan` | `cancel_averaging_pending_after` | Attivo | implementato su TP fill |
| `management_plan` | `cancel_unfilled_pending_after` | **Non implementato** | caricato ma mai consumato |
| `management_plan` | `risk_freed_by_be` | **Inattivo come toggle** | il risk gate libera comunque il rischio se `PROTECTED` |
| `management_plan` | `protective_sl_mode` | **Inattivo** | execution style scelto da flag runtime, non dal campo |
| `management_plan` | `market_convert_mode` | Non propagato | loader lo scarta e il modello usa il default |
| `risk` | blocco intero | Attivo | sizing, concorrenza, cap rischio, leverage |
| `risk` | `max_concurrent_same_symbol` | Attivo con limite ulteriore | impedisce sempre anche lo stesso symbol+side |

---

# 5. Safety e account

## 5.1 `global_safety.allow_unprotected_positions`

Configurazione:

```yaml
global_safety:
  allow_unprotected_positions: false
```

Semantica dichiarata nel commento:

```text
false → non ammettere posizioni senza SL protettivo
```

### Stato reale

Il loader non inserisce `global_safety` in `EffectiveEnrichmentConfig`.  
Nessun percorso runtime analizzato legge questo campo.

La protezione effettiva oggi deriva da:

```yaml
defaults.signal_policy.sl.require_sl: true
```

e dal risk gate, che rifiuta comunque un segnale senza stop valido perché non può calcolare la size.

**Conclusione:** modificare `allow_unprotected_positions` oggi non cambia il comportamento.

---

## 5.2 Account logico

Configurazione globale:

```yaml
account:
  id: "demo_1"
  capital_base_usdt: 10000
  max_leverage: 25
  max_capital_at_risk_pct: 100
  hard_max_per_signal_risk_pct: 2
```

### Campi realmente usati

| Campo | Uso verificato |
|---|---|
| `id` | seleziona account logico per snapshot, market data e comandi exchange |
| `max_leverage` | rifiuta il trade se `risk.leverage > account.max_leverage` |
| `capital_base_usdt` | non entra nel risk sizing corrente |
| `max_capital_at_risk_pct` | non entra nel risk gate corrente |
| `hard_max_per_signal_risk_pct` | non viene applicato dal risk gate corrente |

### Conseguenza

La configurazione corrente contiene due cap di rischio concettualmente simili:

```yaml
account.max_capital_at_risk_pct: 100
risk.max_capital_at_risk_per_trader_pct: 100
```

Il runtime verifica solo il secondo.  
Il primo può indurre a ritenere attivo un limite che, nel percorso analizzato, non viene applicato.

---

# 6. Signal admission

## 6.1 Ordine dei controlli

Per un messaggio `SIGNAL`:

```text
1. trader registrato e attivo
2. blacklist globale
3. blacklist trader
4. entry structure accettata
5. stop loss obbligatorio
6. trim TP
7. split/derivazione/riordino entry
8. price sanity, se attiva
9. PASS al lifecycle
```

Qualunque fallimento nei punti 2–5 produce:

```text
enrichment_decision = BLOCK
```

e non arriva al lifecycle.

## 6.2 Blacklist simboli

```yaml
symbol_blacklist:
  global: []
  per_trader: {}
```

Il confronto normalizza il simbolo e poi usa:

```text
raw_candidate == raw_symbol
oppure
raw_symbol.startswith(raw_candidate)
```

Esempio:

```text
candidate = BTC
symbol = BTCUSDT
→ bloccato
```

Questo è utile se si vuole bloccare tutte le quote di una coin, ma può essere più ampio del previsto.

## 6.3 `accepted_entry_structures`

Valori attuali:

```yaml
ONE_SHOT
TWO_STEP
RANGE
LADDER
```

Effetto:

```text
signal.entry_structure ∉ elenco
    → BLOCK unsupported_entry_structure
```

Non passa per `gate_mode: warn`: le strutture non ammesse vengono sempre bloccate.

## 6.4 Stop obbligatorio

```yaml
sl:
  require_sl: true
```

Effetto:

```text
SL assente oppure SL senza prezzo
    → BLOCK missing_stop_loss
```

Il risk engine ripete il vincolo perché senza SL non può calcolare rischio e size.

## 6.5 `use_original_sl`

```yaml
sl:
  use_original_sl: true
```

Il valore viene caricato nel modello, ma non è consultato nell’enrichment, nel risk gate né nel lifecycle analizzato.

**Stato reale:** lo SL originale viene già mantenuto perché non esiste una logica configurata che lo sostituisce. Il flag non controlla nulla.

---

# 7. MARKET execution policy

Configurazione:

```yaml
market_execution:
  mode: tolerance
  tolerance_pct: 0.5
  range_tolerance_pct: 0.2
```

Semantica prevista:

| Modalità | Intenzione |
|---|---|
| `tolerance` | consentire market solo entro uno scostamento massimo |
| `free` | consentire market senza tolleranza |

### Stato reale

I valori vengono validati nel modello e salvati nello snapshot, ma non risultano consumati nel codice runtime analizzato.

Quindi oggi:

```text
mode: tolerance
tolerance_pct: 0.5
range_tolerance_pct: 0.2
```

non dimostrano l’esistenza di un gate che confronti prezzo corrente, entry segnale e scostamento massimo.

**Rischio:** un operatore può credere che sia attivo un limite di slippage/admission market che non è applicato.

---

# 8. Entry policy e split

## 8.1 Tabelle correnti

### LIMIT

```yaml
single:
  E1: 100%

range:
  split_mode: midpoint
  E1: 50%
  E2: 50%

averaging:
  E1: 70%
  E2: 30%

ladder:
  E1: 50%
  E2: 30%
  E3: 20%
```

### MARKET

```yaml
single:
  E1: 100%

averaging:
  E1: 70%
  E2: 30%
```

## 8.2 Mappa struttura → blocco pesi

| Tipo entry | Struttura | Configurazione usata |
|---|---|---|
| LIMIT | `ONE_SHOT` | `LIMIT.single` |
| LIMIT | `RANGE` | `LIMIT.range` |
| LIMIT | `TWO_STEP` | `LIMIT.averaging` |
| LIMIT | `LADDER` | `LIMIT.ladder` |
| MARKET | `TWO_STEP` | `MARKET.averaging` |
| MARKET | tutto il resto | `MARKET.single` |

`MARKET.range` è vietato: il loader rifiuta qualsiasi configurazione che lo definisca.

## 8.3 Normalizzazione pesi

Se la somma è diversa da 1 con tolleranza 0,001:

```text
peso_effettivo_i = peso_i / somma_pesi
```

Esempio:

```yaml
weights: {E1: 70, E2: 30}
```

diventa:

```text
E1 = 0.7
E2 = 0.3
```

### Lacune di validazione

Non viene imposto che:

- tutti i pesi siano non negativi;
- la somma sia maggiore di zero;
- ci sia un peso per ogni leg parsata;
- non ci siano chiavi extra.

Esempio pericoloso:

```yaml
weights: {E1: 1, E2: -1}
```

ha somma zero, non viene normalizzato e può propagare pesi non validi.

## 8.4 RANGE

### `endpoints`

Mantiene i due estremi, converte la struttura in `TWO_STEP` e ordina per lato:

| Side | Sequenza risultante |
|---|---|
| LONG | prezzo più alto → prezzo più basso |
| SHORT | prezzo più basso → prezzo più alto |

### `firstpoint`, `lastpoint`, `midpoint`

Converte il range in una sola entry:

```text
RANGE
    → ONE_SHOT
```

e assegna:

```text
weight = 1.0
```

Per `midpoint`:

```text
midpoint = round((min_price + max_price) / 2, 8)
```

### Configurazione attuale

```yaml
split_mode: midpoint
```

Quindi un segnale RANGE LIMIT viene ridotto a una sola entry al punto medio.  
I pesi `E1: 50%, E2: 50%` del blocco range non hanno effetto finale perché la derivazione `midpoint` sostituisce il risultato con una sola leg al 100%.

## 8.5 Riordino LIMIT per side

Dopo lo split, le entry LIMIT vengono riordinate:

```text
LONG  → dalla più alta alla più bassa
SHORT → dalla più bassa alla più alta
```

Il runtime salva anche la traccia del riordino nel payload enriched.

Questo è corretto per identificare la prima entry e le averaging successive, ma i pesi restano associati alle leg prima del riordino. L’ordine semantico `E1/E2/E3` va quindi interpretato sul piano normalizzato, non sulla scrittura originale del trader.

---

# 9. Take profits

## 9.1 `use_tp_count`

```yaml
tp:
  use_tp_count: null
```

Regola:

```text
null
    → usa tutti i TP parsati

intero N
    → se i TP parsati sono più di N:
          mantiene solo i primi N
```

Non forza N take profits quando il segnale ne contiene meno.

Esempio:

```text
use_tp_count = 3
segnale con 5 TP → conserva TP1–TP3
segnale con 2 TP → conserva 2 TP, non crea TP3
```

## 9.2 Close distribution

Configurazione attuale:

| Numero TP | Distribuzione |
|---:|---|
| 1 | 100% |
| 2 | 50% / 50% |
| 3 | 30% / 30% / 40% |
| 4 | 25% / 25% / 25% / 25% |
| 5 | 20% × 5 |
| 6 | 20% / 20% / 20% / 20% / 10% / 10% |

Runtime:

```text
se mode = table e il numero TP è presente:
    usa la tabella

altrimenti:
    usa distribuzione equal
```

Quindi un segnale con 7 TP, dato `use_tp_count: null`, diventa automaticamente:

```text
7 × 14,2857%
```

non una distribuzione esplicita a 7 target.

## 9.3 Protezione TP/SL su exchange

Nel piano di esecuzione:

- con 1 TP viene usato un TP/SL position-level full;
- con più TP vengono creati TP/SL parziali per quantità;
- l’ultimo TP riceve la quantità residua, per compensare arrotondamenti.

---

# 10. Price sanity e price corrections

## 10.1 `price_sanity`

Configurazione:

```yaml
price_sanity:
  enabled: false
  symbol_ranges: {}
```

Quando attiva, il processor controlla solo i prezzi dei take profit:

```text
per ogni TP:
    min_price ≤ TP ≤ max_price
```

Non controlla:

- entry;
- stop loss;
- rapporto long/short coerente;
- mark price corrente;
- distanza entry–SL;
- tick size exchange.

**Stato:** attivo ma molto parziale.

## 10.2 `price_corrections`

Configurazione:

```yaml
price_corrections:
  enabled: false
  round_to_tick: false
  clamp_to_exchange_precision: false
```

I valori vengono caricati e salvati, ma non risultano usati dal runtime analizzato.

Questo è particolarmente rilevante per `RANGE.midpoint`, che arrotonda a 8 decimali e non al tick size reale dell’exchange.

---

# 11. Update admission

## 11.1 Configurazione attuale

| Intent | Stato |
|---|---|
| `MOVE_STOP` | consentito |
| `MOVE_STOP_TO_BE` | consentito |
| `CLOSE_FULL` | consentito |
| `CLOSE_PARTIAL` | consentito |
| `CANCEL_PENDING` | consentito |
| `ADD_ENTRY` | bloccato |
| `REENTER` | bloccato |
| `MODIFY_ENTRY` | consentito |
| `MODIFY_TARGETS` | bloccato |
| `INVALIDATE_SETUP` | consentito |

## 11.2 Regola

Per ogni azione nell’update:

```text
azione ammessa
    → continua

azione non ammessa
    → interrompe subito l’intero update
```

Con `gate_mode: block`:

```text
BLOCK action_type_disabled:<intent>
```

Con `gate_mode: warn`:

```text
REVIEW action_type_warned:<intent>
```

Non esiste applicazione parziale a livello di enrichment.

Esempio:

```text
messaggio:
- MOVE_STOP_TO_BE
- MODIFY_TARGETS

MODIFY_TARGETS = false
gate_mode = block

risultato:
→ tutto il messaggio viene BLOCK
→ MOVE_STOP_TO_BE non viene eseguito
```

## 11.3 Limite di `gate_mode`

`gate_mode` influenza solo questo caso: azioni update disabilitate.

Non converte in REVIEW:

- simboli blacklist;
- struttura entry non supportata;
- SL assente;
- trader non registrato;
- trader disabilitato.

---

# 12. Management plan

## 12.1 Breakeven trigger

Configurazione:

```yaml
be_trigger: tp1
```

Logica:

```text
TP1 fillato
    → crea comando MOVE_STOP_TO_BREAKEVEN
```

Il trigger è valutato sui fill exchange, non sul semplice raggiungimento di prezzo.

## 12.2 BE fee-aware: stato corrente

Configurazione:

```yaml
be_fee_correction_enabled: true
be_fee_fallback_profile: null
```

Il resolver fa:

```text
se fee correction disabilitata:
    stop = entry_avg_price

se open_position_qty <= 0 oppure fallback_profile mancante:
    stop = entry_avg_price
```

Con il valore attuale `null`, non viene trovata una fallback profile.  
Il risultato effettivo è quindi:

```text
BE stop = average entry price
```

anche se `be_fee_correction_enabled: true`.

Per rendere possibile il calcolo fee-aware esiste almeno il profilo:

```yaml
be_fee_fallback_profile: bybit_linear
```

ma la sua opportunità dipende da execution style e fee reali. Il documento non raccomanda di impostarlo alla cieca; constata solo che con `null` la correzione fee non viene applicata.

## 12.3 Auto-cancel averaging dopo TP

Configurazione:

```yaml
cancel_pending_by_engine: true
cancel_averaging_pending_after: tp1
```

Logica al fill non finale di TP1:

```text
esistono leg averaging pending?
    │
    ├─ no → nessun cancel
    │
    └─ sì
        → CANCEL_PENDING_ENTRY
        → reason = auto_cancel_averaging
```

Se TP1 è anche trigger BE, il runtime differisce il BE finché non riceve conferma della cancellazione delle averaging leg. Lo scopo è evitare di calcolare il BE su una posizione che potrebbe ancora essere mediata.

## 12.4 Timeout pending

Configurazione:

```yaml
cancel_pending_on_timeout: true
pending_timeout_hours: 24
```

Alla creazione della chain:

```text
timeout_at = now_utc + 24 ore
```

Il campo è creato solo se `cancel_pending_on_timeout` è true.

Questo timeout è distinto da `cancel_averaging_pending_after`:

| Meccanismo | Trigger |
|---|---|
| timeout pending | tempo trascorso senza risoluzione delle entry |
| auto-cancel averaging | TP specifico fillato |

## 12.5 `cancel_unfilled_pending_after`

Configurazione:

```yaml
cancel_unfilled_pending_after: null
```

Il campo viene:

- dichiarato nel YAML;
- validato dal modello;
- copiato nel management plan.

Ma il lifecycle event processor non lo consulta.  
Il commento nel file dice esplicitamente “da implementare”.

**Stato:** non implementato. Cambiarlo in `tp1` o `tp2` non produce il comportamento desiderato finché non esiste il consumer runtime.

## 12.6 `risk_freed_by_be`

Configurazione:

```yaml
risk_freed_by_be: true
```

Il risk gate libera il capitale di rischio per le chain in stato:

```text
be_protection_status == PROTECTED
```

senza consultare `risk_freed_by_be`.

Quindi oggi:

```yaml
risk_freed_by_be: false
```

non impedirebbe la liberazione del rischio dopo BE confermato.

**Stato:** il comportamento esiste, ma il toggle non governa il comportamento.

## 12.7 `protective_sl_mode`

Configurazione:

```yaml
protective_sl_mode: exchange_native_first
```

Il campo viene caricato nel management plan, ma l’execution style reale viene scelto tramite:

```text
simple_attached_enabled
```

che imposta:

```text
UNIFIED_PLAN
oppure
D_POSITION_TPSL
```

Il campo `protective_sl_mode` non viene letto in questa decisione.

**Stato:** inattivo.

## 12.8 `market_convert_mode`

Configurazione:

```yaml
market_convert_mode: cancel_subsequent
```

Il modello supporta:

```text
cancel_subsequent
keep_subsequent
```

La funzione è usata dal lifecycle per la conversione “entry market now”.  
Ma nel loader, quando costruisce `ManagementPlanConfig`, il campo non viene passato.

Risultato:

```text
valore nel YAML
    → perso nel loader
    → modello usa sempre il default cancel_subsequent
```

Quindi anche impostando:

```yaml
market_convert_mode: keep_subsequent
```

la policy effettiva resta `cancel_subsequent`.

**Stato:** funzionalità implementata, ma configurazione non propagata.

---

# 13. Risk model

## 13.1 Formula base

Con configurazione attuale:

```yaml
mode: risk_pct_of_capital
risk_pct_of_capital: 0.5
capital_base_mode: static_config
capital_base_usdt: 10000
```

il rischio teorico per trade è:

```text
risk_amount = 10.000 × 0,5 / 100 = 50 USDT
```

## 13.2 Modalità

| Mode | Formula |
|---|---|
| `risk_pct_of_capital` | `capital × risk_pct_of_capital / 100` |
| `risk_usdt_fixed` | `risk_usdt_fixed` |

## 13.3 Fonte del capitale

| `capital_base_mode` | Fonte |
|---|---|
| `static_config` | `risk.capital_base_usdt` |
| `live_equity` | snapshot equity dell’account exchange |

Con `live_equity`, se non c’è account snapshot, il trade viene rifiutato:

```text
missing_account_snapshot_for_live_equity
```

## 13.4 Trader risk hint

Configurazione:

```yaml
use_trader_risk_hint: true
risk_hint_range_mode: min_value
```

La hint viene applicata solo se:

- `mode = risk_pct_of_capital`;
- la hint è parsata;
- il rischio indicato dal trader è inferiore al rischio configurato.

La hint è quindi **reduce-only**.

Esempio:

```text
rischio bot = 0,5%
rischio trader = 0,3%
→ rischio effettivo = 0,3%

rischio trader = 1%
→ rischio effettivo = 0,5%
```

Per una hint range:

```text
0,25%–0,75%
```

con `min_value`:

```text
rischio effettivo hint = 0,25%
```

## 13.5 Sizing limit

Per limit entry:

```text
risk_distance = |entry_price − stop_price|

size_usdt =
risk_amount / risk_distance × entry_price
```

Per ogni leg:

```text
leg_risk = total_risk × weight
leg_qty = leg_risk / |leg_price − stop_price|
```

Per MARKET senza mark price disponibile:

```text
qty_mode = deferred_market
```

cioè la quantità non è ancora calcolabile e sarà determinata in un passaggio successivo.

## 13.6 Limiti di concorrenza

Ordine dei guard:

```text
1. max_concurrent_trades
2. max_concurrent_same_symbol
3. duplicate_position stesso symbol + stesso side
4. SL e entry validi
5. capital at risk
6. max leverage account
```

Configurazione attuale:

```yaml
max_concurrent_trades: 50
max_concurrent_same_symbol: 1
hedge_mode: true
```

### Implicazione

Con `max_concurrent_same_symbol: 1`:

```text
BTCUSDT LONG aperto
→ BTCUSDT SHORT viene rifiutato
```

anche con `hedge_mode: true`.

`hedge_mode: true` consente l’uso di position index separati sul lato exchange, ma il cap per simbolo impedisce di fatto due chain sullo stesso pair.

## 13.7 Capital-at-risk

Il runtime calcola:

```text
current_open_risk =
somma risk_amount delle chain aperte
escluse quelle con BE_PROTECTED
```

e applica:

```text
current_open_risk + new_risk_amount
≤ capital × max_capital_at_risk_per_trader_pct / 100
```

Con la configurazione attuale:

```text
10.000 × 100% = 10.000 USDT
```

Questo limite è molto alto rispetto al singolo rischio configurato; il controllo reale più restrittivo è quindi la concorrenza, salvo override trader.

## 13.8 Leverage

```yaml
risk.leverage: 10
account.max_leverage: 25
```

Risultato:

```text
10 ≤ 25
→ consentito
```

Il risk engine non usa `account.hard_max_per_signal_risk_pct`.

---

# 14. Interazioni e contraddizioni della configurazione attuale

## 14.1 RANGE midpoint contro pesi range

```yaml
LIMIT.range:
  split_mode: midpoint
  weights: {E1: 0.50, E2: 0.50}
```

La policy dichiara una distribuzione a due leg, ma `midpoint` converte il range in una sola entry al 100%.

Quindi i pesi range sono attualmente ridondanti per tutti i segnali RANGE.

## 14.2 Hedge mode contro same-symbol cap

```yaml
hedge_mode: true
max_concurrent_same_symbol: 1
```

A livello exchange il sistema sa costruire `position_idx` LONG e SHORT separati.  
A livello risk gate, però, due chain sullo stesso simbolo non possono coesistere.

Il hedge mode non è “operativamente sfruttabile” sullo stesso pair finché il cap resta 1.

## 14.3 Fee-aware BE dichiarato ma non attivo

```yaml
be_fee_correction_enabled: true
be_fee_fallback_profile: null
```

La seconda riga neutralizza la prima nel resolver attuale.  
Il risultato è un BE nominale sull’average entry, non un BE netto fee-aware.

## 14.4 `risk_freed_by_be` appare configurabile ma non lo è

```yaml
risk_freed_by_be: true
```

Non è il flag a governare la liberazione rischio; la liberazione avviene sempre per stato `PROTECTED`.

## 14.5 Cap account e cap risk separati

Sono presenti almeno tre concetti simili:

```text
account.max_capital_at_risk_pct
account.hard_max_per_signal_risk_pct
risk.max_capital_at_risk_per_trader_pct
```

Nel percorso risk verificato, è applicato solo il terzo.  
Questa duplicazione aumenta il rischio di modificare il campo sbagliato.

---

# 15. Configurazioni inattive o difettose da correggere

## Priorità alta

1. **Propagare `market_convert_mode` nel loader**  
   Oggi viene ignorato e usa sempre `cancel_subsequent`.

2. **Rendere effettivo o rimuovere `global_safety.allow_unprotected_positions`**  
   Il campo può creare una falsa sicurezza.

3. **Rendere effettivo o rimuovere `risk_freed_by_be`**  
   Il comportamento è hardcoded e non è controllato dal flag.

4. **Correggere `policy_version`**  
   Va calcolata con `get_policy_version(trader_id)` o su `policy_snapshot`.

5. **Ricaricare i file `config/traders/*.yaml`**  
   Altrimenti gli override non sono realmente hot-reload.

6. **Decidere il contratto dell’account override**  
   Opzioni sane:
   - deep merge global account + trader account; oppure
   - validare che ogni account trader sia completo.

## Priorità media

7. **Attivare o rimuovere `market_execution`**  
   La tolleranza market dichiarata non viene applicata.

8. **Attivare o rimuovere `sl.use_original_sl`**.

9. **Attivare o rimuovere `price_corrections`**  
   Serve soprattutto prima dell’invio exchange.

10. **Completare `cancel_unfilled_pending_after`**.

11. **Rendere `price_sanity` coerente con il nome**  
    Dovrebbe verificare almeno entry, SL e TP, non solo TP.

12. **Validare i pesi entry**  
    Somma positiva, valori non negativi, copertura delle leg attese.

## Priorità di design

13. **Separare i cap account dai cap trader**  
    Un campo dovrebbe avere un consumer e una semantica unica.

14. **Rendere esplicito il rapporto hedge mode / same-symbol cap**  
    Il sistema deve dichiarare se vuole consentire hedge contemporaneo sullo stesso pair.

---

# 16. Macchina a stati config-driven

```text
NEW SIGNAL
    │
    ├── trader registrato?
    ├── profilo enabled?
    ├── symbol blacklist?
    ├── entry structure ammessa?
    ├── SL richiesto?
    ├── TP trim?
    ├── split / range derivation?
    └── price sanity?
            │
            ▼
ENRICHED PASS
    │
    ├── symbol esiste su exchange?
    ├── risk capacity?
    ├── leverage <= account max?
    ├── concurrent limits?
    └── timeout pending?
            │
            ▼
WAITING ENTRY
    │
    ├── entry fill
    ├── timeout pending
    ├── update Telegram ammesso?
    └── cancel pending
            │
            ▼
OPEN / PARTIALLY CLOSED
    │
    ├── TP1 fill
    │     ├── cancel averaging?
    │     └── BE trigger?
    │
    ├── TP successivi
    ├── SL
    ├── close manuale
    └── update accepted
            │
            ▼
CLOSED
```

---

# 17. Fonti di codice analizzate

- [`config/operation_config.yaml`](https://github.com/dimkadiver-cpu/TRADING_BOT_TELEGRAM/blob/main/config/operation_config.yaml)
- [`src/runtime_v2/signal_enrichment/config_loader.py`](https://github.com/dimkadiver-cpu/TRADING_BOT_TELEGRAM/blob/main/src/runtime_v2/signal_enrichment/config_loader.py)
- [`src/runtime_v2/signal_enrichment/models.py`](https://github.com/dimkadiver-cpu/TRADING_BOT_TELEGRAM/blob/main/src/runtime_v2/signal_enrichment/models.py)
- [`src/runtime_v2/signal_enrichment/processor.py`](https://github.com/dimkadiver-cpu/TRADING_BOT_TELEGRAM/blob/main/src/runtime_v2/signal_enrichment/processor.py)
- [`src/runtime_v2/lifecycle/risk_capacity.py`](https://github.com/dimkadiver-cpu/TRADING_BOT_TELEGRAM/blob/main/src/runtime_v2/lifecycle/risk_capacity.py)
- [`src/runtime_v2/lifecycle/entry_gate.py`](https://github.com/dimkadiver-cpu/TRADING_BOT_TELEGRAM/blob/main/src/runtime_v2/lifecycle/entry_gate.py)
- [`src/runtime_v2/lifecycle/event_processor.py`](https://github.com/dimkadiver-cpu/TRADING_BOT_TELEGRAM/blob/main/src/runtime_v2/lifecycle/event_processor.py)
- [`src/runtime_v2/lifecycle/be_move_resolver.py`](https://github.com/dimkadiver-cpu/TRADING_BOT_TELEGRAM/blob/main/src/runtime_v2/lifecycle/be_move_resolver.py)

---

# 18. Sintesi conclusiva

Il file `operation_config.yaml` è già la sorgente principale di policy del runtime V2, ma contiene tre categorie diverse di campi:

```text
A. policy operative reali
   - blacklist
   - strutture entry
   - split entry
   - stop obbligatorio
   - TP count
   - update admission
   - risk sizing
   - limiti concorrenza
   - BE trigger
   - auto-cancel averaging

B. policy parziali
   - price sanity solo TP
   - gate_mode solo update disabilitati
   - close distribution con fallback equal
   - fee-aware BE senza fallback profile

C. configurazioni inattive/non propagate
   - global_safety.allow_unprotected_positions
   - account capital/risk cap non usati dal risk gate
   - hard_max_per_signal_risk_pct
   - market_execution
   - sl.use_original_sl
   - price_corrections
   - cancel_unfilled_pending_after
   - risk_freed_by_be come toggle
   - protective_sl_mode
   - market_convert_mode nel loader
```

La priorità non è aggiungere altri campi, ma rendere non ambigua la catena:

```text
YAML field
    → validated effective policy
    → persisted snapshot
    → runtime consumer
    → exchange command
    → lifecycle/audit event
```

Ogni campo senza consumer verificabile va eliminato, implementato, oppure marcato chiaramente come “reserved / not active”.

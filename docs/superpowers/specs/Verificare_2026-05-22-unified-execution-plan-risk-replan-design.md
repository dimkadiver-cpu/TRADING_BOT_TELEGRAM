# Unified Execution Plan With Risk Replan Design

Date: 2026-05-22
Status: Draft — rivisto post-analisi punto per punto
Scope: runtime V2 lifecycle + execution gateway behavior for entry/TP/SL planning and entry-changing updates

## 1. Goal

Unificare la gestione dei casi:

- Caso 1a: `1 LIMIT + 1 TP`
- Caso 1b: `1 LIMIT + N TP`
- Caso 2a: `N LIMIT + 1 TP`
- Caso 2b: `N LIMIT + N TP`
- Caso 3a: `1 MARKET + 1 TP`
- Caso 3b: `1 MARKET + N TP`
- Caso 4a: `1 MARKET + N LIMIT + 1 TP`
- Caso 4b: `1 MARKET + N LIMIT + N TP`

con un solo modello interno, mantenendo una prima implementazione ottimizzata per Bybit ma senza codificare la logica di prodotto direttamente nell'adapter.

## 2. Decisioni bloccate

### 2.1 Strategia generale

Adottare un modello interno comune con prima implementazione Bybit-first:

- un solo `ExecutionPlan` interno;
- stesse regole di prodotto per tutti i casi;
- traduzione exchange-specifica demandata a una policy di routing/protection;
- supporto esplicito fin da ora per update che cambiano le entry.

### 2.2 Regola TP comune

Quando il segnale ha `1 TP`:

- ogni leg viene inviata con `SL + final TP attached`;
- non esiste rebuild TP post-fill.

Quando il segnale ha `N TP`:

- ogni leg viene inviata con `SL + ultimo TP attached`;
- dopo ogni `ENTRY_FILLED`, il sistema ricalcola solo i `TP intermedi`;
- i `TP intermedi` vengono emessi sulla `filled_entry_qty` cumulata;
- l'ultimo TP non viene rigenerato, perché resta attached alle singole leg.

### 2.3 Regola update entry

Gli update che modificano le entry non sono patch locali. Il sistema deve:

- ricostruire il `target ExecutionPlan` completo della chain;
- confrontarlo con lo stato reale già esistente (via `plan_state_json`);
- applicare un diff di azioni consentite.

### 2.4 Regola rischio

Il sistema non ragiona in qty fisse. Ragiona in rischio:

- `risk_total_target`
- `risk_already_realized`
- `risk_remaining`
- nuova allocazione del rischio sulle leg non fillate

A ogni replan:

- le leg già `FILLED` restano storia acquisita;
- il rischio residuo viene ridistribuito sulle leg non fillate;
- le qty vengono ricalcolate dal nuovo budget residuo e dalla distanza dallo SL.

## 3. Non-goals

Fuori da questa spec:

- redesign generale dell'event model;
- supporto completo multi-exchange;
- migrazione storage/schema non necessaria al comportamento target;
- refactor cosmetici non legati al flusso entry/protection/replan.

## 4. ExecutionPlan comune

Ogni segnale e ogni update rilevante devono convergere in un unico oggetto logico.

`take_profits[]` è input di costruzione (proveniente dal segnale canonico) e non
fa parte del piano persistito. L'`ExecutionPlanBuilder` lo separa in `final_tp`
e `intermediate_tps[]` al momento della build. Il piano persistito contiene solo
la proiezione derivata.

```text
ExecutionPlan
  plan_version
  symbol
  side
  stop_loss
  final_tp
  intermediate_tps[]
  entry_legs[]
  protection_policy
  rebuild_policy
  risk_policy
```

### 4.1 Entry leg

Ogni `entry_leg` contiene almeno:

- `leg_id`
- `sequence`
- `entry_type`: `MARKET | LIMIT`
- `price | null`
- `weight`
- `risk_budget`
- `qty_mode`: `fixed | deferred_market`
  - `fixed` — qty calcolata a signal time, nota al submit
  - `deferred_market` — qty calcolabile solo al fill (MARKET senza prezzo corrente disponibile)

### 4.2 Policy

`protection_policy`:

- `TPSL_ATTACHED_FIRST_LEG` — SL + ultimo TP attached in FULL mode solo su E1
  (sequence=1). Tutte le leg successive usano `PLACE_ENTRY` senza attached TPSL.
  Garantito sicuro dalla struttura dei prezzi: E2..N non possono fillare prima di E1.

`rebuild_policy`:

- `NONE` — 1 TP, nessun TP intermedio
- `ON_EACH_ENTRY_FILL` — N TP, TP intermedi a position-level partial dopo ogni fill

`risk_policy`:

- `REBALANCE_REMAINING_RISK_ON_REPLAN`

## 5. Routing logico dei casi

I casi non devono più vivere come builder separati di business logic. Devono diventare solo una proiezione del piano comune.

La `protection_policy` è sempre `TPSL_ATTACHED_FIRST_LEG` per tutti gli 8 casi.
La distinzione rilevante è solo sul `rebuild_policy`.

### 5.1 Single TP

- `protection_policy = TPSL_ATTACHED_FIRST_LEG`
- `rebuild_policy = NONE`

Copre:

- `1 LIMIT + 1 TP`
- `N LIMIT + 1 TP`
- `1 MARKET + 1 TP`
- `1 MARKET + N LIMIT + 1 TP`

### 5.2 Multi TP

- `protection_policy = TPSL_ATTACHED_FIRST_LEG`
- `rebuild_policy = ON_EACH_ENTRY_FILL`

Copre:

- `1 LIMIT + N TP`
- `N LIMIT + N TP`
- `1 MARKET + N TP`
- `1 MARKET + N LIMIT + N TP`

## 6. Regola operativa unica

### 6.1 Submit iniziale

```
leg sequence=1 → PLACE_ENTRY_WITH_ATTACHED_TPSL  tpsl_mode=FULL
                  attached: SL + ultimo TP (chiude full position)

leg sequence>1 → PLACE_ENTRY  (nessun attached TPSL)
```

Valido per tutti gli 8 casi, indipendentemente dal numero di TP.
Il `tpsl_mode=FULL` non richiede `tp_qty` esplicita — chiude l'intera posizione quando colpito.
Le leg E2..N non rischiano di fillare prima di E1 per la struttura dei prezzi della sequenza.

### 6.2 Fill di entry

A ogni `ENTRY_FILLED`:

- aggiornare `filled_entry_qty` e `open_position_qty`;
- aggiornare `risk_already_realized` con `fill_qty * abs(fill_price - sl_price)`;
- aggiornare `plan_state_json` — status della leg → `FILLED`;
- se `rebuild_policy == ON_EACH_ENTRY_FILL` (tp_count > 1):
  - ricalcolare i TP intermedi su `filled_entry_qty` cumulata;
  - emettere `SET_POSITION_TPSL_PARTIAL` per ogni TP intermedio;
  - supersedere i TP intermedi precedenti;
- non toccare l'ultimo TP attached su E1 (già position-level FULL).

### 6.3 Break-even e move stop

Le azioni di stop management devono basarsi sulla protezione reale della posizione,
non sul tipo entry originario.

Per i flow attached Bybit:

- `MOVE_STOP_TO_BREAKEVEN` e `MOVE_STOP` usano `trading_stop_move_sl` (routing position-level);
- non dipendono da ipotesi legacy di ordine SL standalone.

**Assunzione Bybit non verificata:** `trading_stop_move_sl` modifica solo lo SL e
lascia il TP attached intatto. Da verificare su testnet prima del deploy.
Se falsa: dopo ogni BE move il TP deve essere ripiazzato esplicitamente →
aggiungere `rebuild_tp_after_stop_move` al diff engine.

## 7. Architettura del rischio

### 7.1 Principio

La qty e' sempre derivata. Il dato primario e' il budget di rischio.

Questa formula è già implementata in `RiskCapacityEngine` al momento del segnale.
Il replan la riapplica sulle leg non fillate usando il `risk_remaining` aggiornato.

Formula generale:

```text
qty = risk_budget / abs(entry_reference_price - stop_loss)
```

`entry_reference_price`:

- per `LIMIT`: il prezzo limite della leg;
- per `MARKET`: il prezzo di riferimento corrente al submit.

### 7.2 Stato di rischio richiesto

Per ogni chain servono questi concetti:

- `risk_total_target` — budget totale definito a signal time
- `risk_already_realized` — somma di `fill_qty * abs(fill_price - sl_price)` per ogni fill reale
- `risk_remaining` — `risk_total_target - risk_already_realized`
- `replan_allocations` — distribuzione del `risk_remaining` sulle leg non fillate dopo un replan

### 7.3 Esempio pratico

Segnale `LONG`, `SL=95`, `risk_total_target=100`.

Entry iniziali:

- `E1 MARKET`, peso `50%`
- `E2 LIMIT 98`, peso `50%`

Prezzo market corrente: `100`

Budget iniziale:

- `E1`: `50`
- `E2`: `50`

Qty:

- `E1 MARKET`: `50 / (100 - 95) = 10`
- `E2 LIMIT 98`: `50 / (98 - 95) = 16.67`

Se filla solo `E1` a 100 (no slippage):

- `risk_already_realized = 10 * (100 - 95) = 50`
- `risk_remaining = 50`

Se poi arriva update "E1 entra ora" con prezzo corrente `101` e E2 rimane LIMIT:

- il rischio residuo da allocare a E2 resta `50`;
- nuova qty E2 non cambia (è LIMIT, il prezzo è fisso);
- non serve replan su E2.

Se invece l'update converte E1 (ancora pending) in MARKET a `101`:

- il vecchio limit E1 viene cancellato;
- nuova qty E1 = `50 / (101 - 95) = 8.33`;
- E2 mantiene il suo budget originale (policy `keep_remaining`).

Il budget resta coerente. La qty cambia perché cambia il contesto di esecuzione.

### 7.4 Slippage e budget exhaustion

`risk_already_realized` usa il `fill_price` reale, non il prezzo pianificato.

Per le leg `LIMIT`, il fill avviene a prezzo uguale o migliore del limite →
il rischio realizzato è uguale o inferiore al budget. Il caso negativo non può verificarsi.

Per le leg `MARKET`, il fill può avvenire a prezzo peggiore del riferimento →
il rischio realizzato può superare il budget.

Gestione del `risk_remaining` dopo fill MARKET con slippage:

- `risk_remaining > 0` ma < budget pianificato per le leg residue → le leg residue
  vengono ricreate con qty ridotta: `risk_remaining_per_leg / abs(leg_price - sl)`;
- `risk_remaining == 0` → tutte le leg pending vengono cancellate
  (evento `RISK_BUDGET_EXHAUSTED`);
- `risk_remaining < 0` → budget già superato per slippage → tutte le leg pending
  vengono cancellate (evento `RISK_BUDGET_EXHAUSTED`).

Se la qty ricalcolata risulta inferiore al `min_order_size` dell'exchange → la leg
viene cancellata con warning `qty_below_min_order_size`, non viene inviato un
ordine invalido.

## 8. Update che cambiano le entry

### 8.1 Scenari coperti

Questa spec copre solo i seguenti casi di entry-changing update:

- **Caso A** — `ONE_SHOT LIMIT → MARKET`: la singola leg pending viene convertita
  a mercato (update "entra ora");
- **Caso B** — `TWO_STEP`: E1 ancora pending viene convertita a MARKET, E2 resta
  LIMIT (con policy `keep_remaining` o `cancel_and_consolidate`).

Altri scenari (aggiunta leg, rimozione leg secondaria, modifica prezzo LIMIT) sono
descritti nelle azioni consentite ma non sono casi primari di questa spec.

### 8.2 Modello

Ogni update rilevante genera un nuovo `target ExecutionPlan`.

Il runtime confronta:

- `plan_state_json` — stato runtime corrente del piano (leg status, client_order_id per leg)
- `target plan` — piano ricostruito dall'update

e produce un diff applicativo. Il diff engine non interroga `ops_execution_commands`
per decisioni di business — usa esclusivamente `plan_state_json`.

`plan_state_json` viene aggiornato nella stessa transazione di ogni cambio di stato
(fill, cancel, replan), garantendo coerenza atomica con `ops_execution_commands`.

Struttura `plan_state_json`:

```json
{
  "plan_version": 1,
  "legs": [
    {
      "leg_id": "leg_1",
      "sequence": 1,
      "entry_type": "LIMIT",
      "price": 98.0,
      "risk_budget": 50.0,
      "qty": 16.67,
      "qty_mode": "fixed",
      "status": "PENDING",
      "client_order_id": "place_entry_attached:42:leg1"
    }
  ]
}
```

### 8.3 Azioni consentite

- `keep_entry_leg` — la leg resta invariata
- `cancel_pending_entry` — cancella una leg pending
- `add_entry_leg` — aggiunge una nuova leg al piano
- `replace_entry_leg` — `cancel + recreate` con nuovo tipo/prezzo/qty (es. LIMIT → MARKET)
- `rebuild_intermediate_tps` — ricalcola TP intermedi sulla `filled_entry_qty` reale
- `move_stop` — cambia il prezzo dello SL
- `update_position_protection` — aggiorna `tp_size` / `sl_size` degli ordini protettivi
  sulla posizione aperta dopo un replan che cambia le qty residue. Triggered da: replan
  con qty allocation changes su posizione già parzialmente aperta. Distinto da `move_stop`
  (che cambia il prezzo) e da `rebuild_intermediate_tps` (che è post-fill, non post-replan).

### 8.4 Regole

- una leg già `FILLED` non viene modificata come entry;
- una leg `PENDING` può essere cancellata e sostituita;
- `LIMIT -> MARKET` non è un edit exchange-side: è `cancel + recreate`;
- le qty delle leg non fillate vengono sempre ricalcolate dal `risk_remaining` reale;
- i TP intermedi vengono sempre ricalcolati dalla `filled_entry_qty` reale;
- nuovo prezzo leg uguale allo SL → reject (`zero_risk_distance`);
- nuovo prezzo leg oltre lo SL (LONG: prezzo < SL, SHORT: prezzo > SL) → reject esplicito.

### 8.5 Policy di consolidamento per LIMIT → MARKET

Quando E1 viene convertita da LIMIT a MARKET, le leg successive possono essere gestite
con due strategie configurabili a livello account o trade chain:

- `keep_remaining` — E2..N restano LIMIT con il loro budget originale; E1 viene ricreata
  a MARKET con `leg_risk_E1 / abs(market_price - sl)`;
- `cancel_and_consolidate` — E2..N vengono cancellate; E1 prende `risk_total_target * 100%`
  → `risk_total_target / abs(market_price - sl)`.

Default: `keep_remaining`.

## 9. Implicazioni Bybit-first

### 9.1 Mapping desiderato

Per Bybit:

- leg `sequence=1` → `PLACE_ENTRY_WITH_ATTACHED_TPSL` con `tpsl_mode=FULL`
  (SL + ultimo TP, chiude full position);
- leg `sequence>1` → ordine semplice senza attached TPSL;
- TP intermedi → `SET_POSITION_TPSL_PARTIAL` (position-level partial) dopo ogni fill;
- move stop / BE → `trading_stop_move_sl` (routing compatibile con protezione attached).

### 9.2 Semplificazione rispetto al design precedente

Il passaggio a `tpsl_mode=FULL` su E1 per tutti i casi elimina il gap noto tra
`tp_qty_ratio` (lifecycle) e `tp_qty` (adapter): `FULL` mode non richiede `tp_qty`
esplicita, quindi il problema non si pone. Nei casi `MARKET deferred`, E1 va a mercato
con `FULL` attached — la qty non è necessaria per impostare il TP a livello posizione.

## 10. Refactor target

### 10.1 Componenti target

- `ExecutionPlanBuilder`
- `EntryCommandFactory`
- `PostFillProtectionRebuilder`
- `ProtectionRoutingPolicy`
- `ExecutionPlanDiffEngine`

### 10.2 Responsabilita'

`ExecutionPlanBuilder`:

- riceve segnale canonico + risk snapshot;
- separa `take_profits[]` in `final_tp` e `intermediate_tps[]`;
- costruisce il piano canonico con `qty_mode`, `risk_budget` per leg;
- produce `plan_state_json` iniziale con tutte le leg in status `PENDING`.

`EntryCommandFactory`:

- leg `sequence=1` → `PLACE_ENTRY_WITH_ATTACHED_TPSL` con `tpsl_mode=FULL`;
- leg `sequence>1` → `PLACE_ENTRY` senza attached TPSL;
- regola uniforme per tutti gli 8 casi — nessun branch su `tp_count` o `entry_count`.

`PostFillProtectionRebuilder`:

- genera i TP intermedi dopo i fill quando `tp_count > 1`;
- triggered da evento `ENTRY_FILLED` se `rebuild_policy == ON_EACH_ENTRY_FILL`.

`ProtectionRoutingPolicy`:

- traduce il piano comune nel linguaggio concreto dell'exchange;
- incapsula le differenze Bybit attached vs standalone.

`ExecutionPlanDiffEngine`:

- riceve `plan_state_json` corrente e `target plan`;
- decide `keep / cancel / add / replace / rebuild / update_protection`;
- non interroga il DB direttamente — lavora su oggetti tipizzati.

## 11. Migrazione raccomandata

### Fase 1

Introdurre `ExecutionPlan` come oggetto interno e `plan_state_json` su `TradeChain`.

Il calcolo del rischio non cambia — `RiskCapacityEngine` già implementa la formula
corretta. La Fase 1 non modifica il comportamento esterno: i builder C/D esistenti
continuano a funzionare, `plan_state_json` viene popolato in parallelo come shadow.

### Fase 2

Far derivare i mode correnti dal piano comune. I mode diventano etichette diagnostiche
inferite automaticamente dalle proprietà del piano:

- 1 leg + 1 TP + `SINGLE_TP_ATTACHED_PER_LEG` → `C_SIMPLE_ATTACHED`
- 1 leg + N TP + `FINAL_TP_ATTACHED_PER_LEG` → `C_MULTI_TP`
- N leg + 1 TP + `SINGLE_TP_ATTACHED_PER_LEG` → `D_MULTI_ENTRY_1TP`
- N leg + N TP + `FINAL_TP_ATTACHED_PER_LEG` → `D_MULTI_ENTRY_MULTI_TP`

Strategia di transizione: **sostituzione diretta**.

1. Implementare `EntryCommandFactory` con test di parità: stesso input → stesso
   payload `ExecutionCommand` tra vecchio builder e nuovo percorso, per tutti gli 8 casi.
2. Quando i test di parità passano su tutti i casi, rimuovere i builder separati
   (`_build_c_commands`, `_build_c_multi_tp_commands`, `_build_d_multi_entry_1tp_commands`,
   `_build_d_multi_entry_multi_tp_commands`) in un unico commit.
3. Il routing in `entry_gate.py` basato su `entry_count / tp_count / simple_attached_enabled`
   viene rimosso — il piano decide tutto.
4. `execution_mode` resta sul `TradeChain` come label diagnostico per i chain già aperti,
   ma non guida più la scelta del builder.

### Fase 3

Spostare il post-fill rebuild e il routing protections su `PostFillProtectionRebuilder`
e `ProtectionRoutingPolicy` come componenti comuni.

### Fase 4

Aggiungere `ExecutionPlanDiffEngine` per gli update entry-changing (scenari A e B).
Aggiungere gestione `risk_remaining` e `RISK_BUDGET_EXHAUSTED`.

### Fase 5

Rimuovere i mode correnti come driver di business logic. Restano solo come label
diagnostici nel `plan_state_json`.

## 12. Acceptance criteria

Il design e' corretto se:

1. tutti gli 8 casi di input convergono nello stesso modello `ExecutionPlan`;
2. il runtime applica una sola regola TP per `1 TP` e una sola per `N TP`;
3. gli update entry-changing usano `replan completo + diff`, non patch locali;
4. la qty delle leg non fillate e' sempre derivata dal `risk_remaining` ribilanciato;
5. il caso `MARKET + N LIMIT + N TP` funziona con la stessa architettura dei casi piu' semplici;
6. il mapping Bybit per market deferred supporta anche la size del final TP attached;
7. BE move su posizione con TP attached non altera il TP esistente
   (test su Bybit testnet obbligatorio prima del deploy in produzione).

## 13. Open implementation notes

- Il design richiede copertura test separata per:
  - piano iniziale per tutti gli 8 casi;
  - fill progressivi con aggiornamento `risk_already_realized`;
  - slippage MARKET che porta `risk_remaining` a zero o negativo;
  - update `LIMIT → MARKET` con policy `keep_remaining` e `cancel_and_consolidate`;
  - aggiunta/rimozione averaging;
  - rebuild TP intermedi post-fill;
  - BE move dopo protezioni attached (testnet Bybit obbligatorio).
- Le qty non devono essere persistite come unica verita' di business del piano;
  la verita' e' il `risk_budget` per leg e lo stato reale dei fill.
- `plan_state_json` è la fonte autoritativa per il diff engine — non `ops_execution_commands`.
- La migrazione deve preservare backward compatibility operativa finché i vecchi mode esistono ancora.

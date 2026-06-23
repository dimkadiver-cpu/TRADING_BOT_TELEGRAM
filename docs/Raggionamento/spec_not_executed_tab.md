# Spec — Dashboard tab `Not executed`

## 1. Obiettivo

Sostituire il significato attuale del tab `Blocked`.

Il tab non deve essere una lista generica di errori tecnici, review e comandi execution falliti.

Deve rispondere a una domanda precisa:

> Quali segnali non hanno prodotto alcuna entry effettivamente accettata o fillata dall'exchange, e perché?

Nome UI richiesto:

```text
🚫 Not executed
```

Il vecchio nome `Blocked` è deprecato.

---

## 2. Principio di classificazione

Ogni segnale deve appartenere a una sola vista di outcome principale.

```text
Signal received
│
├─ Rejected before entry order exists
│  └─ Not executed
│
├─ Accepted, but entry submission failed permanently
│  └─ Not executed
│
├─ Entry order acknowledged by exchange, not filled
│  └─ Active · WAITING_ENTRY
│
├─ Entry partially filled
│  └─ Active · PARTIALLY_FILLED
│
├─ Entry filled / position open
│  └─ Active · OPEN / PARTIALLY_CLOSED
│
└─ Position closed
   └─ Closed
```

### Regola anti-duplicazione

Un record non può apparire contemporaneamente in:

- `Not executed`
- `Active`
- `Closed`

`Not executed` contiene soltanto segnali o chain per cui non esiste nessuna entry effettivamente accettata dall'exchange, fillata o trasformata in posizione.

---

## 3. Scope del tab

### 3.1 Incluso

Il tab include due categorie.

#### A. Rejected before chain execution

Segnale non eseguito perché rifiutato prima dell'invio dell'entry all'exchange.

Esempi:

```text
SIGNAL_REJECTED
POLICY_REJECTED
RISK_REJECTED
MANUAL_REJECTED
SIGNAL_VALIDATION_REJECTED
```

Motivi tipici:

```text
missing_symbol_or_side
no_entry_legs
missing_stop_loss_for_risk_calc
unknown_symbol
control_mode:new_entries_paused
risk_limit_exceeded
policy_rejected
manual_rejected
```

Questi record possono non avere `trade_chain_id`.

#### B. Accepted but entry not executed

Chain creata, ma nessuna entry è stata accettata o fillata dall'exchange.

Esempi:

```text
ENTRY_COMMAND_FAILED
ENTRY_RETRY_EXHAUSTED
ENTRY_SUBMISSION_REJECTED
ENTRY_BLOCKED_BEFORE_SUBMISSION
```

L'errore deve riguardare esclusivamente il percorso entry iniziale.

Command type ammessi:

```text
PLACE_ENTRY
PLACE_ENTRY_LIMIT
PLACE_ENTRY_MARKET
```

L'implementazione deve usare i command type reali definiti dal runtime. Se sono presenti alias o varianti, devono essere normalizzati in una categoria unica `ENTRY_SUBMISSION`.

### 3.2 Escluso

Non devono entrare nel tab:

```text
WAITING_ENTRY
PARTIALLY_FILLED
OPEN
PARTIALLY_CLOSED
CLOSED
POSITION_CLOSED
```

Sono già coperti da `Active` o `Closed`.

Sono inoltre esclusi gli errori successivi alla creazione/accettazione dell'entry:

```text
MOVE_STOP failed
MOVE_STOP_TO_BREAKEVEN failed
REBUILD_PARTIAL_TPS failed
CLOSE_FULL failed
CLOSE_PARTIAL failed
protective-order reconciliation failed
position synchronization failed
```

Questi devono alimentare una vista separata:

```text
⚠️ Operational issues
```

---

## 4. Stato entry: requisito necessario

L'attuale lifecycle state `WAITING_ENTRY` non è sufficiente per decidere se una chain è realmente attiva.

Una chain può essere in `WAITING_ENTRY` perché:

1. è stata creata ma l'entry non è ancora stata inviata;
2. l'entry è stata inviata ed è pending sull'exchange;
3. l'entry command è fallito;
4. il retry è ancora in corso.

Serve una rappresentazione esplicita dell'outcome entry.

### 4.1 Campo logico richiesto

Il modello deve poter derivare o memorizzare uno stato entry equivalente a:

```text
NOT_SUBMITTED
SUBMISSION_PENDING
ACKNOWLEDGED
PARTIALLY_FILLED
FILLED
FAILED_FINAL
CANCELLED_UNFILLED
```

### 4.2 Semantica

| Entry status | Vista |
|---|---|
| `NOT_SUBMITTED` con rifiuto finale | Not executed |
| `SUBMISSION_PENDING` | Active solo se command realmente queued/in retry; preferibilmente stato separato `ENTRY_SUBMIT_PENDING` |
| `ACKNOWLEDGED` | Active · WAITING_ENTRY |
| `PARTIALLY_FILLED` | Active · PARTIALLY_FILLED |
| `FILLED` | Active / Closed |
| `FAILED_FINAL` | Not executed |
| `CANCELLED_UNFILLED` dopo ordine acknowledged | Closed oppure `Expired / Cancelled`; non Not executed |

### 4.3 Regola minima per Active

`Active · WAITING_ENTRY` deve significare:

> L'exchange ha accettato almeno un ordine entry aperto.

Non deve significare semplicemente:

> La chain è stata creata.

---

## 5. Data model consigliato

### 5.1 Outcome record

Per garantire copertura anche dei segnali respinti senza chain, introdurre una tabella o una proiezione dedicata.

Nome suggerito:

```text
ops_signal_execution_outcomes
```

Schema minimo:

```sql
CREATE TABLE ops_signal_execution_outcomes (
    outcome_id              INTEGER PRIMARY KEY,
    canonical_message_id    INTEGER,
    raw_message_id          INTEGER,
    trade_chain_id          INTEGER NULL,

    account_id              TEXT,
    trader_id               TEXT,
    symbol                  TEXT NULL,
    side                    TEXT NULL,

    outcome_kind            TEXT NOT NULL,
    phase                   TEXT NOT NULL,
    reason_code             TEXT NOT NULL,
    reason_detail           TEXT NULL,

    source_event_type       TEXT NOT NULL,
    source_command_id       INTEGER NULL,
    source_command_type     TEXT NULL,

    occurred_at             TEXT NOT NULL,
    resolved_at             TEXT NULL,
    resolution_type         TEXT NULL,

    created_at              TEXT NOT NULL
);
```

Valori ammessi:

```text
outcome_kind:
- SIGNAL_REJECTED
- ENTRY_NOT_EXECUTED

phase:
- VALIDATION
- POLICY
- RISK
- MANUAL_REVIEW
- ENTRY_SUBMISSION
```

### 5.2 Alternative minima senza nuova tabella

Se non si vuole introdurre subito una nuova tabella:

- leggere `SIGNAL_REJECTED` dagli eventi lifecycle anche senza chain;
- leggere `REVIEW_REQUIRED` solo se avviene prima dell'entry submission;
- leggere `FAILED` solo per command type entry;
- verificare assenza di entry acknowledged/fill/position.

Questa soluzione è transitoria e meno affidabile perché richiede inferenze da tabelle diverse.

---

## 6. Query di inclusione

### 6.1 Segnali rifiutati

Includere gli eventi `SIGNAL_REJECTED` con reason definitiva.

Pseudo-query:

```sql
SELECT
    le.source_id AS signal_reference,
    NULL AS trade_chain_id,
    reason,
    created_at
FROM ops_lifecycle_events le
WHERE le.event_type = 'SIGNAL_REJECTED';
```

I metadati `account_id`, `trader_id`, `symbol`, `side` devono essere recuperati dal contesto del canonical/enriched message o salvati direttamente nel payload/outcome record.

### 6.2 Chain entry non eseguite

Includere una chain solo quando tutte le condizioni sono vere:

```text
1. esiste un fallimento definitivo relativo a entry submission;
2. non esiste un entry order acknowledged/open sull'exchange;
3. filled_entry_qty = 0;
4. open_position_qty = 0;
5. chain non è terminale con posizione eseguita.
```

Pseudo-query:

```sql
SELECT DISTINCT
    t.trade_chain_id,
    t.account_id,
    t.trader_id,
    t.symbol,
    t.side,
    ec.command_type,
    ec.payload_json,
    ec.updated_at
FROM ops_trade_chains t
JOIN ops_execution_commands ec
    ON ec.trade_chain_id = t.trade_chain_id
WHERE ec.status = 'FAILED'
  AND ec.command_type IN (
      'PLACE_ENTRY',
      'PLACE_ENTRY_LIMIT',
      'PLACE_ENTRY_MARKET'
  )
  AND COALESCE(t.filled_entry_qty, 0) = 0
  AND COALESCE(t.open_position_qty, 0) = 0
  AND t.lifecycle_state NOT IN (
      'OPEN',
      'PARTIALLY_FILLED',
      'PARTIALLY_CLOSED',
      'CLOSED',
      'POSITION_CLOSED'
  );
```

La query definitiva deve usare gli effettivi command type e stati del repository.

---

## 7. UI target

### 7.1 Vista globale

```text
🚫 Not executed — All accounts
─────────────────────────────────────
Total: 3   Page: 1/1   Updated: 14:32:05
Order: Latest first
─────────────────────────────────────
#S-104 · ETH/USDT · LONG
demo_1 · trader_a
REJECTED · Risk
Reason: risk_limit_exceeded
At: 23 Jun 14:11
Details: /signal_104
─────────────────────────────────────
#22 · SOL/USDT · SHORT
demo_2 · trader_b
NOT EXECUTED · Entry submission
Reason: insufficient_margin
Command: PLACE_ENTRY
At: 23 Jun 14:18
Details: /trade_22
```

### 7.2 Account scope

```text
🚫 Not executed — demo_1
─────────────────────────────────────
Total: 2   Page: 1/1   Updated: 14:32:05
─────────────────────────────────────
#S-104 · ETH/USDT · LONG
trader_a
REJECTED · Policy
Reason: control_mode:new_entries_paused
At: 23 Jun 14:11
Details: /signal_104
```

### 7.3 Trader scope

```text
🚫 Not executed — demo_1 · trader_a
─────────────────────────────────────
Total: 1   Page: 1/1   Updated: 14:32:05
─────────────────────────────────────
#22 · SOL/USDT · SHORT
NOT EXECUTED · Entry submission
Reason: insufficient_margin
Command: PLACE_ENTRY
At: 23 Jun 14:18
Details: /trade_22
```

---

## 8. Campi obbligatori per ogni riga

| Campo | Obbligatorio | Note |
|---|---:|---|
| riferimento | Sì | `#trade_id` se chain esiste; `#S-id` se signal-only |
| symbol / side | Quando disponibili | `—` se rifiutato prima del parsing completo |
| account / trader | Sì nel globale | Derivati dal messaggio/config |
| outcome | Sì | `REJECTED` oppure `NOT EXECUTED` |
| phase | Sì | `Validation`, `Policy`, `Risk`, `Manual review`, `Entry submission` |
| reason | Sì | reason code normalizzato |
| timestamp | Sì | timestamp dell'outcome, non update generico |
| details action | Sì | `/trade_id` o `/signal_id` |

Non mostrare `Blocked:` come label. Usare `At:`.

---

## 9. Filtri

La tab `Not executed` deve supportare:

```text
Account → Trader → Outcome → Phase → Side
```

Valori:

```text
Outcome:
- All
- Rejected
- Entry not executed

Phase:
- Validation
- Policy
- Risk
- Manual review
- Entry submission

Side:
- All
- LONG
- SHORT
```

Regole già fissate:

- `Trader` selezionabile solo dopo `Account`;
- cambio account cancella trader;
- selettore trader mostra solo trader dell'account selezionato;
- `Reset all` cancella ogni filtro;
- i filtri devono essere locali alla vista, esclusi `Account` e `Trader` che sono scope condiviso.

---

## 10. Operational issues: fuori scope ma necessario

Gli errori dopo che l'entry è stata accettata o fillata non devono contaminare `Not executed`.

Devono essere gestiti da una vista distinta:

```text
⚠️ Operational issues
```

Esempi:

```text
SL update failed
TP rebuild failed
close command failed
missing protective stop
exchange reconciliation mismatch
position sync stale
```

Un trade può apparire in `Active` e in `Operational issues`, perché qui non si tratta di una classificazione finale dell'outcome segnale ma di un alert operativo attivo.

---

## 11. Migrazione dal tab attuale

### 11.1 Rinominare

```text
blocked → not_executed
bloccati → not_executed
```

### 11.2 Rimuovere dalla query attuale

Rimuovere l'inclusione generica:

```sql
ec.status = 'FAILED'
```

senza filtrare command phase/type.

### 11.3 Conservare solo review pre-entry

`REVIEW_REQUIRED` entra in `Not executed` soltanto se:

```text
filled_entry_qty = 0
AND open_position_qty = 0
AND nessuna entry acknowledged
AND review phase è precedente o relativa alla entry submission
```

Review su update di una posizione già aperta va in `Operational issues`.

### 11.4 Ordinamento

Ordinamento unico:

```text
occurred_at DESC
```

Non usare `trade_chain_id` come criterio di ordinamento.

---

## 12. Criteri di accettazione

### AC-01 — Rejection senza chain

Dato un segnale respinto per policy prima della creazione chain:

```text
- compare in Not executed;
- non compare in Active;
- mostra reason e timestamp;
- apre il dettaglio segnale.
```

### AC-02 — Entry command fallito

Dato un segnale accettato, una chain creata e `PLACE_ENTRY` fallito definitivamente senza fill:

```text
- compare in Not executed;
- non compare in Active;
- mostra command type e reason.
```

### AC-03 — Ordine entry acknowledged ma non fillato

Dato `PLACE_ENTRY` accepted dall'exchange e ordine ancora aperto:

```text
- compare in Active · WAITING_ENTRY;
- non compare in Not executed.
```

### AC-04 — Entry parzialmente fillata

Dato `filled_entry_qty > 0`:

```text
- compare in Active · PARTIALLY_FILLED;
- non compare in Not executed.
```

### AC-05 — Fallimento SL dopo entry fillata

Dato trade `OPEN` e `MOVE_STOP` fallito:

```text
- non compare in Not executed;
- compare in Operational issues.
```

### AC-06 — Errore storico risolto

Dato `PLACE_ENTRY` fallito, poi retry accepted o fillato:

```text
- non compare più in Not executed;
- compare in Active o Closed secondo stato corrente.
```

### AC-07 — Una sola outcome primaria

Dato uno stesso segnale con più eventi intermedi:

```text
- la tab mostra un solo record outcome corrente;
- non crea righe duplicate;
- conserva gli eventi completi nel dettaglio.
```

### AC-08 — Scope globale

Nel dashboard globale:

```text
- ogni record mostra Account e Trader;
- Account → Trader filtra correttamente;
- il titolo riflette il filtro effettivo.
```

---

## 13. Test richiesti

### Query

- `SIGNAL_REJECTED` senza `trade_chain_id`;
- `REVIEW_REQUIRED` pre-entry;
- `REVIEW_REQUIRED` dopo position open: escluso;
- `PLACE_ENTRY` failed senza fill: incluso;
- `MOVE_STOP` failed con trade open: escluso;
- entry acknowledged waiting: escluso;
- partial fill: escluso;
- retry fallito poi successful: escluso;
- due errori per stessa chain: una sola riga outcome;
- sorting per `occurred_at DESC`;
- account/trader/side/outcome/phase filters.

### Formatter

- signal-only reference `#S-...`;
- chain reference `#...`;
- globale mostra account e trader;
- account scope non ripete account;
- empty state: `No non-executed signals.`;
- reason mancante: mostra `Reason: unavailable`, non `—`.

---

## 14. Decisioni finali

1. `Waiting entry` è un trade attivo solo dopo acknowledge exchange.
2. `Not executed` significa nessuna entry exchange accepted/fillata.
3. Errori operativi post-entry sono una categoria separata.
4. I segnali respinti senza chain devono essere persistiti e visibili.
5. La query non può basarsi esclusivamente su `ops_trade_chains`.
6. Il tab deve mostrare outcome business, non rumore tecnico storico.

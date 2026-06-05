# Multi-Update Summary Design

Data: 2026-06-05
Scope: redesign del `MULTI_CHAIN_SUMMARY` nel control plane runtime v2 per casi di multi-update trader, con focus su summary autosufficiente, linking coerente e gestione speciale dei casi `CLOSE_FULL`.

## Contesto

Oggi il sistema runtime v2 gestisce gli update Telegram con due percorsi distinti:

- sintesi per-chain in `src/runtime_v2/lifecycle/entry_gate.py` tramite `_write_update_clean_log()`
- summary multi-chain in `src/runtime_v2/lifecycle/entry_gate.py` tramite `_write_multi_chain_summary()`
- projection event-driven dei lifecycle event terminali e exchange-driven in `src/runtime_v2/control_plane/outbox_writer.py`

Questo produce una asimmetria strutturale:

- il summary multi-chain conosce solo stato minimale per chain (`DONE/PARTIAL/SKIPPED/REVIEW`)
- non possiede un modello ricco del risultato per-chain
- non distingue bene tra operazione richiesta e risultato effettivo
- non ha una regola di linking coerente tra `SIGNAL_ACCEPTED`, `UPDATE_DONE` e `POSITION_CLOSED`
- nel caso `CLOSE_FULL` il summary arriva troppo presto rispetto ai link finali realmente utili

## Obiettivo

Rendere `MULTI_CHAIN_SUMMARY` il messaggio principale di lettura per gli update multi-chain.

Done significa:

- il summary è autosufficiente per gli update non terminali
- il summary mostra il risultato effettivo per ogni chain
- il linking per-chain è coerente e predicibile
- i casi `CLOSE_FULL` usano il link finale a `POSITION_CLOSED`
- il summary `CLOSE_FULL` viene ritardato finché i link finali sono risolvibili

## Acceptance Contract

### Criterio principale

Leggendo solo il `MULTI_CHAIN_SUMMARY`, l’utente deve capire cosa è successo su ogni chain senza dover aprire altri messaggi, eccetto il caso `CLOSE_FULL`, dove il link finale serve per il dettaglio di chiusura.

### Pass/Fail Criteria

1. Per update multi-chain non-`CLOSE_FULL`, ogni chain `DONE/PARTIAL/SKIPPED/REVIEW/ERROR` è leggibile direttamente nel summary.
2. Per update multi-chain con `CLOSE_FULL`, ogni chain usa il link finale `POSITION_CLOSED` e il summary non ripete i dettagli già propri del report finale.
3. Il summary non espone raw event names interni come contenuto utente.
4. Il summary non viene emesso troppo presto nei casi `CLOSE_FULL`.
5. Il formato testuale dei mockup approvati resta il riferimento di prodotto.

### Segnali secondari

- test formatter
- test normalizer per-chain
- test integrazione lifecycle/control plane
- verifica delle regole di linking

## Casi in scope

### 1. Update multi-target specifici

- `MOVE_SL_TO_BE`
- `SET_SL_TO_LEVEL` o prezzo/livello esplicito
- `CANCEL_PENDING`
- `CLOSE_FULL`
- combinazioni come `MOVE_SL_TO_BE + CANCEL_PENDING`

### 2. Update multi-target specifici con comando distinto per singolo riferimento

Esempio:

- un messaggio contiene più riferimenti Telegram
- ogni riferimento riceve una specifica istruzione stop diversa

### 3. Update con scope globali

- `ALL_POSITIONS`
- `ALL_OPEN`
- `ALL_REMAINING`

con le stesse famiglie operative:

- `MOVE_SL_TO_BE`
- `SET_SL_TO_LEVEL`
- `CANCEL_PENDING`
- `CLOSE_FULL`
- combinazioni miste

## Decisioni di prodotto validate

### 1. Il summary è autosufficiente

Il `MULTI_CHAIN_SUMMARY` non è più una tabella-indice minimale. È il messaggio principale per leggere il risultato dell’update multi-chain.

### 2. Posizione del link

Per ogni chain il link va subito sotto la riga header della chain.

Esempio non-`CLOSE_FULL`:

```text
#8 BTC LONG — DONE
https://t.me/c/.../470
Entry_2: 61,192.03 → cancelled
SL: 66,400 → 68,500 BE
```

### 3. Regola di linking per-chain

- update che non contengono `CLOSE_FULL`:
  - il link per-chain punta al `SIGNAL_ACCEPTED` root della chain
- update che contengono `CLOSE_FULL`:
  - il link per-chain punta al `POSITION_CLOSED` finale

### 4. Ritardo del summary `CLOSE_FULL`

Se l’update contiene `CLOSE_FULL`, il summary non deve essere emesso finché il link finale `POSITION_CLOSED` non è risolvibile.

### 5. Casi `CLOSE_FULL`: niente duplicazione inutile

Nel summary `CLOSE_FULL` non va ripetuto:

```text
Applied:
Position: open → closed 100%
Close reason: MANUAL_CLOSE
```

perché queste informazioni appartengono già al report `POSITION_CLOSED`.

## Opzioni considerate

### Opzione A — tenere il summary come writer separato ma più ricco

Il summary continua a essere costruito in `entry_gate`, ma con una struttura per-chain molto più ricca.

Pro:

- diff più piccolo
- basso rischio sulle proiezioni event-driven esistenti

Contro:

- l’asimmetria architetturale resta
- la logica update continua a vivere lontana dal projection path

### Opzione B — spostare il summary nel projection model event-driven

Il lifecycle persiste solo dati strutturati; `outbox_writer` costruisce anche il summary multi-chain.

Pro:

- modello più pulito
- un solo owner della semantica notifica

Contro:

- refactor più ampio
- costo alto per il task attuale

### Opzione C — modello ibrido a due fasi

Il lifecycle produce un summary intermedio ricco; un secondo passaggio lo finalizza quando i link richiesti sono risolvibili.

Pro:

- risolve bene il problema dei link finali `CLOSE_FULL`
- non richiede un refactor totale del pipeline
- mantiene basso il rischio di regressione

Contro:

- introduce uno stato intermedio in più

### Raccomandazione

Usare l’Opzione C.

È il compromesso migliore tra correttezza funzionale, rischio e superficie di cambiamento.

## Design del summary

## Struttura generale

Il summary deve avere:

- header complessivo update
- sezione `Operations requested`
- blocchi per-chain
- footer aggregato
- `Source`
- link al messaggio update trader sorgente

### Caso non-`CLOSE_FULL`

Formato di riferimento:

```text
✅ UPDATE APPLICATO — 3/4 full, 1 partial
- - - - - - - - - - - - - - - - - - - - - - - -
Operations requested:
▪️ CANCEL_PENDING
▪️ MOVE_SL_TO_BE
- - - - - - - - - - - - - - - - - - - - - - - -
#6 WLD LONG — DONE
https://t.me/c/3897279123/468
Entry_2: 61,192.03 → cancelled
Entry_3: 60,192.03 → cancelled
SL: 66,400 → 68,500 BE
- - - - - - - - - - - - - - - - - - - - - - - -
#7 ICNT LONG — PARTIAL
https://t.me/c/3897279123/469
Entry_2: SKIPPED — no pending averaging order
SL: 66,400 → 68,500 BE
- - - - - - - - - - - - - - - - - - - - - - - -
#8 BTC LONG — DONE
https://t.me/c/3897279123/470
Entry_2: 61,192.03 → cancelled
SL: 66,400 → 68,500 BE
- - - - - - - - - - - - - - - - - - - - - - - -
#1 INCT LONG — DONE
https://t.me/c/3897279123/466
Entry_2: 61,192.03 → cancelled
SL: 66,400 → 68,500 BE
- - - - - - - - - - - - - - - - - - - - - - - -
Done: 3 | Partial: 1 | Skipped: 1 | Error: 0
- - - - - - - - - - - - - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3927267771/365
```

### Caso `CLOSE_FULL`

Formato di riferimento:

```text
✅ UPDATE APPLICATO — 4/4 chain
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
Operation requested:
▪️ Close full
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
#6 WLD LONG — DONE
https://t.me/c/3897279123/468
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
#7 ICNT LONG — DONE
https://t.me/c/3897279123/469
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
#8 BTC LONG — DONE
https://t.me/c/3897279123/470
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
#1 INCT LONG — DONE
https://t.me/c/3897279123/466
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
Done: 4 | Skipped: 0 | Error: 0
- - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
Source: trader_update
https://t.me/c/3927267771/365
```

## Modello dati target

Il summary non dovrebbe più dipendere solo da inferenza tardiva su `accepted/noop/review`.

Serve un outcome per-chain normalizzato.

### Struttura concettuale

```text
SummaryOutcome
- canonical_message_id
- source
- update_source_link
- requested_operations[]
- contains_close_full
- chains[]

ChainOutcome
- trade_chain_id
- symbol
- side
- status
- display_lines[]
- link_mode
- resolved_link
```

### `status`

Valori ammessi:

- `DONE`
- `PARTIAL`
- `SKIPPED`
- `REVIEW`
- `ERROR`

### `link_mode`

Valori ammessi:

- `signal_root`
- `final_close`

## Flusso dati

### 1. Processing update

Il gate continua a produrre risultati per-chain, ma questi risultati devono essere normalizzati in una struttura summary-friendly.

Ogni chain outcome deve contenere:

- stato finale del summary
- linee display già orientate all’utente
- regola di link attesa

### 2. Emissione immediata per update non-`CLOSE_FULL`

Se nessuna operazione richiesta è `CLOSE_FULL`, il summary può essere scritto subito.

### 3. Emissione ritardata per update `CLOSE_FULL`

Se almeno una operazione richiesta è `CLOSE_FULL`, il summary entra in stato logico `pending_final_links`.

Viene emesso solo quando:

- i `POSITION_CLOSED` richiesti sono stati materializzati
- i link finali per le chain rilevanti sono risolvibili

### 4. Messaggi per-chain

I messaggi per-chain possono continuare a esistere, ma non sono più la fonte primaria per comprendere l’update multi-chain.

## Rendering per stato

### DONE non-`CLOSE_FULL`

Mostra:

- header chain
- link root
- linee di cambiamento effettive

Per gli update stop:

- `MOVE_STOP_BE` rende la semantica di breakeven
- `MOVE_STOP` non-BE rende anche una riga `Reference`

Esempio:

```text
SL: 66,400 → 68,500
Reference: TP_1
```

oppure:

```text
SL: 66,400 → 67,950
Reference: Price
```

### DONE `CLOSE_FULL`

Mostra:

- header chain
- link finale `POSITION_CLOSED`

Non mostra:

- blocco `Applied`
- `Position open → closed 100%`
- `Close reason`

### PARTIAL

Mostra:

- header chain
- link
- linee applicate
- linee skipped o rejected leggibili

### SKIPPED

Mostra:

- header chain
- link
- motivo utente leggibile

### REVIEW

Mostra:

- header chain
- link quando disponibile
- motivo review leggibile

### ERROR

Mostra:

- header chain
- link quando disponibile
- motivo operativo breve

Mai stack trace o dettagli interni grezzi.

## Policy testo utente

Il summary non deve esporre raw event names come testo finale.

Esempi ammessi:

- `Entry_2: SKIPPED — no pending averaging order`
- `SL: SKIPPED — already at breakeven`
- `Review required — ambiguous update target`
- `Error — close command submitted but final close report missing`

Per i move stop:

- `MOVE_STOP_BE` è il ramo dedicato al breakeven
- `MOVE_STOP` non-BE può mostrare solo:
  - `Reference: TP_n`
  - `Reference: Price`

Non serve un fallback `Custom`, perché se parser/lifecycle non estraggono un livello TP o un prezzo specifico, il caso degrada già nel modello operativo a `MOVE_STOP_BE`.

Esempi non desiderati:

- `NOOP_NOT_PENDING`
- `NOOP_ALREADY_PROTECTED_BE`
- `REVIEW_REQUIRED`

Questi possono restare nel livello interno, non nel formatter utente.

## Impatti architetturali

### Owner layer

L’owner corretto del redesign è il path update nel lifecycle/control-plane boundary.

File coinvolti in prima analisi:

- `src/runtime_v2/lifecycle/entry_gate.py`
- `src/runtime_v2/control_plane/formatters/clean_log.py`
- eventuale tracking o repository notification/outbox se serve persistere lo stato intermedio

### Nota sul refactor

Questo redesign non richiede nel primo passo di spostare tutto il sistema update dentro `outbox_writer`.

È un redesign focalizzato, non una re-architettura totale del notification pipeline.

## Strategia di test

### Unit test normalizer per-chain

Copertura minima:

- `CANCEL_PENDING`
- `MOVE_SL_TO_BE`
- `CLOSE_FULL`
- combinazioni multiple sulla stessa chain

### Unit test formatter summary

Copertura minima:

- non-`CLOSE_FULL` autosufficiente
- `CLOSE_FULL` compatto
- `PARTIAL`
- `SKIPPED`
- `ERROR`

### Integration test lifecycle/control plane

Copertura minima:

- update multi-chain non terminale emesso subito
- update multi-chain con `CLOSE_FULL` ritardato fino a link finale risolvibile
- niente duplicazione del blocco `Applied: Position open → closed 100%`

### Regression test linking

Copertura minima:

- root `SIGNAL_ACCEPTED` per non-`CLOSE_FULL`
- `POSITION_CLOSED` per `CLOSE_FULL`

## Rischi e follow-up

### Rischi

- introdurre stato intermedio senza persistenza robusta del waiting condition
- dipendere da segnali terminali `POSITION_CLOSED` che arrivano in ritardo o non arrivano
- mantenere incoerenze tra per-chain logs e summary se le due sintesi non condividono lo stesso modello outcome

### Follow-up probabili

- definire dove persistere il summary intermedio `pending_final_links`
- definire timeout/degradazione se un `POSITION_CLOSED` atteso non diventa mai risolvibile
- valutare in task separato l’unificazione più ampia tra sintesi update e projection event-driven

## Out of scope

- redesign completo di tutti i formatter CLEAN_LOG
- refactor totale del projection engine
- modifica dei report `POSITION_CLOSED`, `TP_FILLED_FINAL`, `SL_FILLED`, `BE_EXIT` oltre i punti strettamente necessari alla coerenza link/report

## Suggested Commit Message

`docs: specify autosufficient multi-update summary design`

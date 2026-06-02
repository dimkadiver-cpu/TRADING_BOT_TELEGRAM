# Notification Redesign Gap Closure

**Data**: 2026-06-03  
**Stato**: Draft approvato a livello di design

---

## Obiettivo

Chiudere i gap residui del redesign notifiche del 2026-06-02 senza riaprire l'architettura:

- arricchire `MODIFY_ENTRIES` con diff concreti `old_price -> new_price`;
- rendere `UPDATE_PARTIAL` leggibile con modifiche applicate concrete e rifiuti separati;
- rendere `UPDATE_REJECTED` minimale ma utile con `reason` e azioni rifiutate tecniche.

Il lavoro deve restare confinato alla sintesi payload update e al rendering Telegram delle notifiche CLEAN_LOG.

---

## Contesto

La review dell'implementazione del redesign `2026-06-02-notification-redesign.md` ha mostrato tre gap:

1. `MODIFY_ENTRIES` emette `TELEGRAM_UPDATE_ACCEPTED` con solo `{"action": "MODIFY_ENTRIES"}` e quindi `_write_update_clean_log()` non può popolare `changed`.
2. `UPDATE_PARTIAL` non mostra le modifiche concrete applicate, anche se il synthesis le calcola.
3. `UPDATE_REJECTED` è troppo povero: il formatter mostra solo `reason`, mentre il synthesis non espone chiaramente le azioni rifiutate.

Il resto del redesign già introdotto resta valido e fuori scope: delay rimossi per TP/UPDATE, `MULTI_CHAIN_SUMMARY`, link enrichment nel dispatcher, rimozione `AggregationWorker`.

---

## Approccio scelto

Approccio mirato e aderente alla spec esistente:

- nessuna ridefinizione globale del contratto update;
- nessun cambio a dispatcher, outbox, delay o multi-chain summary;
- completamento del payload di `MODIFY_ENTRIES`;
- allineamento dei formatter `UPDATE_PARTIAL` e `UPDATE_REJECTED` ai dati già sintetizzati.

Questo approccio è preferito perché chiude i gap reali con il minimo cambiamento coerente.

---

## Requisiti funzionali

### 1. `MODIFY_ENTRIES` deve produrre `changed_entries`

In `lifecycle/entry_gate.py`, `_apply_modify_entries()` deve arricchire il payload di `TELEGRAM_UPDATE_ACCEPTED` con:

```python
{
    "action": "MODIFY_ENTRIES",
    "changed_entries": [
        {"sequence": 2, "old_price": 92500.0, "new_price": 93100.0},
    ],
}
```

Regole:

- includere solo le leg per cui esiste una modifica concreta di prezzo;
- non includere leg cancellate o convertite a market in questo campo;
- non dedurre questi valori nel formatter: la sorgente canonica è il payload lifecycle.

### 2. `UPDATE_PARTIAL` deve mostrare modifiche applicate e rifiuti

Quando una chain produce sia azioni accettate sia noop/reject tecnici:

- `_write_update_clean_log()` continua a sintetizzare `changed` dai `TELEGRAM_UPDATE_ACCEPTED`;
- il payload `UPDATE_PARTIAL` deve contenere sia `changed` sia `rejected_actions`;
- il formatter deve mostrare:
  - prima le modifiche applicate concrete;
  - poi la lista `Rejected:` con i nomi tecnici (`NOOP_*`).

### 3. `UPDATE_REJECTED` deve restare minimale

Per il caso totalmente respinto:

- il payload deve includere `reason` quando ricavabile;
- il payload deve includere `rejected_actions` con i nomi tecnici;
- il formatter deve mostrare:
  - `Reason: ...` se presente;
  - `Rejected:` con i nomi tecnici;
- non mostrare diff tentati `old -> new`.

### 4. Nomi tecnici preservati

I rifiuti devono restare tecnici, ad esempio:

- `NOOP_ALREADY_PROTECTED_BE`
- `NOOP_NOT_PENDING`
- `NOOP_ALREADY_CLOSED`

Non è prevista alcuna traduzione in etichette user-friendly in questa fase.

---

## Modifiche tecniche

### `src/runtime_v2/lifecycle/entry_gate.py`

#### `_apply_modify_entries()`

Deve costruire `changed_entries` durante l'elaborazione dei `diff_actions`.

Fonte dati:

- stato corrente da `plan_state_json`;
- modifiche individuate dal diff engine / dalle replace-update actions già calcolate.

Comportamento:

- per ogni leg con update prezzo, aggiungere `{sequence, old_price, new_price}`;
- se il prezzo non è determinabile in modo affidabile, non inventare il dato;
- in quel caso l'evento resta valido ma senza `changed_entries`.

#### `_write_update_clean_log()`

Deve essere allineato così:

- `UPDATE_DONE`: invariato, ma usa anche `changed_entries` di `MODIFY_ENTRIES`;
- `UPDATE_PARTIAL`: mantiene `changed` e `rejected_actions`;
- `UPDATE_REJECTED`: popola `rejected_actions` e un `reason` compatto se disponibile.

Per `reason`:

- usare il primo motivo disponibile da noop/review event, senza aggiungere nuova tassonomia;
- se nessun motivo è ricavabile, omettere `reason`.

### `src/runtime_v2/control_plane/formatters/clean_log.py`

#### `_update_partial()`

Deve mostrare:

- header `UPDATE PARTIAL`;
- sezione modifiche concrete da `changed`, con lo stesso formato di `_update_done()` quando disponibile;
- sezione `Rejected:` con i valori di `rejected_actions`.

#### `_update_rejected()`

Deve mostrare:

- header `UPDATE REJECTED`;
- `Reason: ...` se presente;
- sezione `Rejected:` con i valori di `rejected_actions`.

### File esplicitamente fuori scope

- `src/runtime_v2/control_plane/notification_dispatcher.py`
- `src/runtime_v2/control_plane/outbox_writer.py`
- `MULTI_CHAIN_SUMMARY`
- delay di invio
- path TP / SL / close fills

---

## Error handling

- Se `old_price` o `new_price` non sono ricostruibili in modo affidabile, il sistema non deve generare placeholder o valori presunti.
- L'assenza di `changed_entries` per un singolo evento non deve rompere la sintesi `UPDATE_*`; produce solo un messaggio meno ricco.
- Il formatter deve tollerare payload parziali: se `changed` manca, deve comunque mostrare `Rejected:` o `Reason:` quando presenti.

---

## Testing

Validazione minima richiesta:

1. test su `_apply_modify_entries()` che verifica `changed_entries` per una modifica prezzo reale;
2. test su `_write_update_clean_log()` o flow equivalente che verifica `UPDATE_PARTIAL` con `changed` + `rejected_actions`;
3. test formatter per `UPDATE_REJECTED` con `reason` + `rejected_actions`;
4. rerun della suite mirata notifiche/runtime già usata in review.

Suite target prevista:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest \
  tests\runtime_v2\control_plane\test_outbox_writer.py \
  tests\runtime_v2\control_plane\test_clean_log_formatter.py \
  tests\runtime_v2\control_plane\test_dispatcher.py \
  tests\runtime_v2\lifecycle\test_entry_gate.py -q
```

---

## Acceptance criteria

1. Un update `MODIFY_ENTRIES` con cambio prezzo mostra `Entry_N: old -> new` nel messaggio sintetizzato.
2. Un `UPDATE_PARTIAL` mostra sia le modifiche applicate concrete sia la lista `Rejected:` con i codici tecnici.
3. Un `UPDATE_REJECTED` mostra `Reason:` quando disponibile e la lista `Rejected:` con i codici tecnici.
4. Nessun cambiamento di comportamento su delay TP/UPDATE, `MULTI_CHAIN_SUMMARY` o dispatcher link enrichment.

---

## Note operative

- Questo documento estende la spec `docs/superpowers/specs/2026-06-02-notification-redesign.md` a livello di chiusura gap, senza sostituirla.
- Non viene introdotto alcun nuovo migration, schema DB o nuovo event type.
- Non viene eseguito alcun commit in questa fase senza richiesta esplicita dell'utente.

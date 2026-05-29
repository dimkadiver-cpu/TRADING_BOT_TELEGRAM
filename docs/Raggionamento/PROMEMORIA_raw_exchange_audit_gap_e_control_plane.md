# Promemoria - Gap audit raw exchange e impatto sul Control Plane

## Contesto

Durante la verifica di due chain in:

```text
C:\TeleSignalBot\db\ops.sqlite3
```

e emerso un gap specifico tra:

```text
exchange_raw_events
```

e:

```text
ops_exchange_events
ops_lifecycle_events
ops_trade_chains
```

Il caso osservato riguarda fill molto rapidi su entry bot-generated.

---

## Problema osservato

Su una chain reale:

```text
- il fill entry e presente e processato correttamente in ops_exchange_events
- la chain viene aperta correttamente
- il lifecycle continua correttamente
```

ma i raw event immediati associati allo stesso ordine possono restare:

```text
classified_event_type = UNKNOWN
trade_chain_id = NULL
forwarded_to_lifecycle = 0
```

Quindi:

```text
runtime operativo corretto
audit raw incompleto o sporco
```

---

## Diagnosi

Il comportamento deriva da due fattori distinti.

### 1. Race temporale su `known_order_link_ids`

Il classifier del watcher exchange usa una mappa dinamica:

```text
order_link_id -> (trade_chain_id, role, sequence)
```

per correlare un raw fill a una chain.

Nel caso di market fill molto rapidi:

```text
mark_sent(command)
e
arrivo del raw watch_my_trades
```

possono avvenire quasi nello stesso istante.

Se il batch del watcher legge la mappa un attimo prima che il `client_order_id`
sia visibile come `SENT/DONE`, il raw viene classificato come `UNKNOWN`.

### 2. `watch_orders` non e una fonte affidabile per inferire fill entry

Nel codice attuale, `watch_orders` viene usato soprattutto per:

```text
- Cancelled
- stati tecnici di supporto
```

mentre stati tipo:

```text
open
closed
```

senza una regola esplicita restano `UNKNOWN`.

Questo e coerente col design attuale:

```text
watch_orders = audit tecnico
watch_my_trades / reconciliation = eventi operativi veri
```

---

## Effetto reale sul sistema

### Cosa NON si rompe

Il gap non implica perdita del fatto operativo.

Se il raw classifier non correla il fill in tempo, il sistema puo comunque
recuperarlo tramite:

```text
ExchangeEventSyncWorker / reconciliation command-based
```

e scrivere comunque il fatto corretto in:

```text
ops_exchange_events
```

Da li il lifecycle aggiorna correttamente:

```text
- ops_trade_chains
- ops_lifecycle_events
- ops_execution_commands
```

### Cosa resta rotto

Resta sporca la tracciabilita raw.

In pratica:

```text
ops_exchange_events dice la verita operativa
exchange_raw_events non sempre racconta bene come ci si e arrivati
```

---

## Impatto sul futuro Control Plane

La domanda corretta non e:

```text
esiste un gap raw?
```

ma:

```text
quale tabella usera il sistema notifiche come source of truth?
```

### Se il design segue il PRD attuale

Il PRD del Control Plane dice chiaramente che:

```text
TECH_LOG deriva dal logger tecnico
CLEAN_LOG deriva da domain events
COMMANDS mostra snapshot operative
```

Quindi:

```text
CLEAN_LOG non deve leggere exchange_raw_events come fonte primaria
```

La source of truth corretta per notifiche e snapshot operative deve essere:

```text
- ops_trade_chains
- ops_lifecycle_events
- ops_exchange_events
- futuro ops_notification_outbox
```

Se il sistema viene costruito cosi:

```text
il gap raw non ha impatto funzionale diretto su CLEAN_LOG e COMMANDS
```

### Se invece il sistema notifiche legge i raw

Se qualcuno in futuro prova a derivare notifiche da:

```text
exchange_raw_events.classified_event_type
```

allora il gap diventa pericoloso.

Rischi:

```text
- missing notification su fill rapidissimi
- notifiche duplicate tra raw e reconciliation
- timeline sporca
- falsi sospetti di evento perso
- rumore in audit e debug
```

Conclusione architetturale:

```text
exchange_raw_events non deve diventare la source of truth del Control Plane
```

Deve restare:

```text
audit / forensics / supporto diagnostico
```

---

## Proposta di fix raccomandata

Obiettivo:

```text
pulire il raw audit
senza spostare la source of truth
senza duplicare eventi lifecycle
```

### Strategia

Introdurre una fase di:

```text
raw correlation backfill
```

per raw `UNKNOWN` recenti con `order_link_id` valorizzato.

### Flusso proposto

1.

```text
Il watcher salva il raw anche se al primo passaggio e UNKNOWN
```

2.

```text
Un piccolo worker o step post-send rilegge i raw UNKNOWN recenti
```

3.

```text
Se order_link_id ora e risolvibile:
```

aggiorna i campi audit:

```text
- classified_event_type
- classified_source
- trade_chain_id
- tp_level
```

4.

```text
Non reinserire l'evento operativo se ops_exchange_events lo contiene gia
```

Quindi:

```text
backfill audit, non replay di business logic
```

---

## Perche questa soluzione e preferibile

### Vantaggio 1

Non tocca il comportamento operativo sano.

### Vantaggio 2

Non forza `exchange_raw_events` a diventare la verita primaria.

### Vantaggio 3

Riduce il rumore investigativo sui casi veloci.

### Vantaggio 4

Mantiene separati:

```text
fact operativo
vs
traccia raw
```

### Vantaggio 5

Si allinea bene al futuro `ops_notification_outbox`.

---

## Estensione utile opzionale

Quando un evento viene scritto in `ops_exchange_events` via reconciliation,
e utile marcare esplicitamente la provenienza nel payload.

Esempio:

```json
{
  "fill_price": 73222.0,
  "filled_qty": 0.148,
  "command_id": 6,
  "source_mode": "reconciliation",
  "matched_client_order_id": "tsb:2:6:entry:1:..."
}
```

Questo non sostituisce il backfill raw, ma aiuta a distinguere:

```text
evento catturato live dal watcher
evento recuperato dal reconciliation path
```

---

## Regola architetturale da preservare

Per il futuro sistema notifiche:

```text
NON leggere exchange_raw_events come fonte primaria delle notifiche business
```

Regola corretta:

```text
CLEAN_LOG / COMMANDS / outbox
    leggono eventi dominio e stato operativo consolidato

TECH_LOG / audit / forensics
    possono leggere raw e log tecnici
```

Formula pratica:

```text
raw exchange events = evidenza tecnica
ops_exchange_events = fatto operativo normalizzato
ops_lifecycle_events = decisione di dominio
ops_trade_chains = stato finale della chain
```

---

## Acceptance criteria del fix

Il gap puo dirsi chiuso quando:

1.

```text
Un fill rapidissimo puo ancora essere recuperato via reconciliation
```

2.

```text
Il raw corrispondente non resta permanentemente UNKNOWN se il link ordine e noto
```

3.

```text
Il backfill non genera duplicati in ops_exchange_events
```

4.

```text
Il lifecycle non viene rieseguito inutilmente
```

5.

```text
Il Control Plane puo continuare a basarsi su domain events consolidati
```

---

## Priorita

Priorita suggerita:

```text
alta:
    non usare exchange_raw_events come source of truth delle notifiche

media:
    introdurre raw correlation backfill

bassa:
    migliorare watch_orders come fonte classificante primaria
```

---

## Sintesi finale

Il gap osservato non e principalmente un bug di trading.

E un gap di audit e tracciabilita:

```text
l'operazione viene eseguita e consolidata correttamente
ma il raw stream non sempre riflette bene la correlazione iniziale
```

Per il futuro Control Plane questo e accettabile solo a una condizione:

```text
le notifiche devono derivare da eventi dominio consolidati
non dai raw event exchange
```

La soluzione raccomandata e:

```text
backfill dei raw UNKNOWN con order_link_id noto
senza trasformare il raw nel motore della logica notifiche
```

# Svuotare un topic Telegram senza eliminarlo

## Obiettivo

Creare un comando da usare **dentro un forum topic**:

```text
/clear_topic
```

Quando un utente autorizzato invia il comando, il sistema deve:

1. capire qual è il topic corrente;
2. recuperare tutti i messaggi di quel solo topic;
3. cancellarli;
4. lasciare il topic esistente e riutilizzabile.

Non deve creare job, record nel DB, audit, report finale o messaggi di conferma.

Dopo l'operazione il topic deve risultare vuoto, salvo il messaggio tecnico iniziale del topic che Telegram non consente di eliminare.

---

## Cosa non usare

Non usare:

```text
deleteForumTopic
```

perché elimina anche il topic.

---

## Requisiti

- Il comando deve funzionare solo nei forum topic, non nella chat generale.
- Solo utenti autorizzati possono eseguirlo.
- L'identità che cancella i messaggi deve essere amministratrice del supergruppo e avere il permesso di eliminare messaggi.
- Il sistema non deve toccare messaggi di altri topic.

---

## Flusso minimo

```text
/clear_topic inviato dentro un topic
    ↓
Leggi chat_id e topic_id dal messaggio del comando
    ↓
Verifica che il mittente sia autorizzato
    ↓
Telethon recupera tutti i messaggi appartenenti a quel topic
    ↓
Esclude il messaggio iniziale del topic
    ↓
Aggiunge anche il messaggio /clear_topic alla lista da eliminare
    ↓
Cancella tutti gli ID a gruppi di massimo 100
    ↓
Fine: nessun messaggio finale nel topic
```

---

## Identificatori da usare

Dal messaggio che contiene `/clear_topic` servono:

```python
chat_id = message.chat_id
topic_id = message.reply_to.reply_to_top_id
command_message_id = message.id
```

Nel caso della Bot API, `topic_id` corrisponde normalmente a:

```python
message.message_thread_id
```

Il perimetro della pulizia è sempre:

```text
chat_id + topic_id
```

Mai usare il titolo del topic o il testo del messaggio per decidere quali messaggi cancellare.

---

## Recupero messaggi con Telethon

Per trovare anche messaggi vecchi che il sistema non ha mai salvato, usare Telethon/MTProto per leggere il thread del topic tramite il suo `top_msg_id`.

Concetto:

```python
messages = await list_messages_in_topic(
    chat_id=chat_id,
    top_msg_id=topic_id,
)
```

Ogni messaggio del topic ha lo stesso riferimento al thread (`reply_to_top_id == topic_id`).

Il messaggio iniziale del topic non va cancellato:

```python
message.id == topic_id
```

Va escluso dalla lista.

---

## Cancellazione

Raccogli tutti gli ID eliminabili, incluso il comando `/clear_topic`, e cancellali a blocchi di massimo 100:

```python
message_ids = [
    msg.id
    for msg in messages
    if msg.id != topic_id
]

for batch in chunks(message_ids, 100):
    await delete_messages(chat_id, batch)
```

La cancellazione può essere eseguita:

- direttamente tramite Telethon, con l'account amministratore; oppure
- tramite `deleteMessages` della Bot API, se il bot ha `can_delete_messages`.

Non inviare alcun messaggio di successo alla fine, altrimenti il topic non rimane vuoto.

---

## Comportamento in caso di errore

- Se il comando è fuori da un topic: non fare nulla.
- Se l'utente non è autorizzato: non fare nulla oppure cancellare solo il comando.
- Se alcuni messaggi sono già stati rimossi: ignorarli e continuare.
- Se Telegram limita temporaneamente le richieste: attendere il tempo richiesto e riprendere la cancellazione.
- Se arriva un secondo `/clear_topic` mentre è già in corso una pulizia sullo stesso topic: ignorarlo.

Per evitare doppie esecuzioni basta un lock in memoria basato su:

```text
chat_id + topic_id
```

Non serve salvarlo nel database.

---

## Risultato

```text
Prima:

Topic clean_log · trader_a
├─ messaggio iniziale del topic       ← resta
├─ log 1                              ← eliminato
├─ log 2                              ← eliminato
├─ dashboard                          ← eliminato
└─ /clear_topic                       ← eliminato

Dopo:

Topic clean_log · trader_a
└─ messaggio iniziale del topic
```

Il topic resta disponibile per ricevere nuovi messaggi.

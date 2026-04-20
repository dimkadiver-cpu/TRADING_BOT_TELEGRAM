# Listener Layer — Documentazione tecnica

Questo documento descrive il layer di acquisizione Telegram: dalla ricezione di un messaggio grezzo fino alla sua messa in coda per il parser.

---

## Indice

1. [Panoramica del flusso](#1-panoramica-del-flusso)
2. [Configurazione canali](#2-configurazione-canali)
3. [Filtro scope: chat e topic](#3-filtro-scope-chat-e-topic)
4. [Estrazione topic ID](#4-estrazione-topic-id)
5. [Ingestion — persistenza raw](#5-ingestion--persistenza-raw)
6. [Coda asincrona e worker](#6-coda-asincrona-e-worker)
7. [Router — decisioni di routing](#7-router--decisioni-di-routing)
8. [Recovery al restart](#8-recovery-al-restart)
9. [Hot reload configurazione](#9-hot-reload-configurazione)
10. [Log chiave](#10-log-chiave)
11. [Tabella componenti e file](#11-tabella-componenti-e-file)

---

## 1. Panoramica del flusso

```
Telegram (Telethon)
       │
       │ evento NewMessage
       ▼
TelegramListener._handle_new_message()
       │
       ├─ extract_message_topic_id()       estrae topic_id dal messaggio
       ├─ _is_allowed_message()            filtra per chat + topic + active
       ├─ _is_media_only()                 scarta messaggi solo-media senza testo
       │
       ▼
_ingest_and_enqueue()
       │
       ├─ _is_blacklisted()                controlla blacklist_global + scope-matched
       ├─ ingestion.ingest()               persiste in raw_messages (dedup automatico)
       │
       ▼
asyncio.Queue[QueueItem]
       │
       ▼
run_worker() → _process_item() → MessageRouter.route()
       │
       ▼
Parser + Storage (parse_results, signals, operational_signals)
```

Al restart, prima di attivare il listener live, viene eseguita la recovery:

```
run_recovery()
  ├─ _reenqueue_stale()          messaggi bloccati in pending/processing
  └─ _catchup_from_telegram()    messaggi arrivati mentre il processo era spento
```

---

## 2. Configurazione canali

File: `config/channels.yaml`

Il listener rileva modifiche a questo file e ricarica la config **senza restart** (hot reload).

### Struttura minima

```yaml
recovery:
  max_hours: 7          # finestra temporale per il catchup al restart

blacklist_global:
  - "#admin"            # tag/frasi filtrate su tutti i canali
  - "#pinned"

channels:
  - chat_id: -1001234567
    label: "MioCanale"
    active: true
    trader_id: trader_a
    blacklist: []       # blacklist specifica per questo scope
```

### Con forum topic (più trader nello stesso forum)

```yaml
channels:
  - chat_id: -1003722628653
    topic_id: 3
    label: "Forum_TraderA"
    active: true
    trader_id: trader_a
    blacklist: ["rumore_specifico"]

  - chat_id: -1003722628653
    topic_id: 4
    label: "Forum_TraderB"
    active: true
    trader_id: trader_b
    blacklist: []
```

### Campi

| Campo | Tipo | Default | Descrizione |
|---|---|---|---|
| `chat_id` | int | obbligatorio | ID Telegram del gruppo/forum (negativo) |
| `topic_id` | int \| null | `null` | Topic del forum. `null` = forum intero o gruppo normale |
| `label` | string | `""` | Nome leggibile, solo per log/debug |
| `active` | bool | `true` | Se `false`, i messaggi vengono marcati `done` senza parsing |
| `trader_id` | string \| null | `null` | Trader assegnato. Se `null`, il router lo cerca nel testo |
| `blacklist` | lista string | `[]` | Parole/frasi filtrate **solo su questo scope** |

### Validazione al caricamento

- `topic_id` deve essere intero positivo (≥ 1) se specificato
- Non possono esistere due entry con la stessa coppia `(chat_id, topic_id)`
- `topic_id = 1` è valido: corrisponde al **General topic** di Telegram

---

## 3. Filtro scope: chat e topic

### Regola di matching

Per ogni messaggio in arrivo, il sistema cerca l'entry più specifica:

```
topic-specific (chat_id + topic_id esatto)  →  ha priorità
     ↓ se assente
forum-wide (chat_id + topic_id=null)         →  fallback
     ↓ se assente
nessun match                                 →  messaggio scartato
```

Implementazione: `ChannelsConfig.match_entry(chat_id, topic_id)` in `src/telegram/channel_config.py`.

### Casi concreti

| Config | Messaggio | Risultato |
|---|---|---|
| entry topic-3 | messaggio topic-3 | match → entry topic-3 |
| entry topic-3 + entry forum-wide | messaggio topic-3 | match → entry topic-3 (ha priorità) |
| entry topic-3 + entry forum-wide | messaggio topic-99 | match → entry forum-wide (fallback) |
| solo entry topic-3 | messaggio topic-4 | nessun match → scartato |
| solo entry forum-wide | messaggio senza topic | match → entry forum-wide |
| config vuota (nessuna entry) | qualsiasi | **open mode**: tutti ammessi |

### Open mode

Se `channels` è completamente vuoto nel YAML, il listener accetta messaggi da qualsiasi chat. Utile in fase di debug o test.

### Canale inattivo (`active: false`)

Un messaggio che matcha una entry con `active: false` viene marcato `done` immediatamente, senza parsing né inserimento in parse_results.

---

## 4. Estrazione topic ID

Funzione: `extract_message_topic_id(message)` in `src/telegram/topic_utils.py`.

Telethon rappresenta i topic nel campo `message.reply_to`:

```python
# Messaggio in un topic named (es. topic 3)
message.reply_to.forum_topic == True
message.reply_to.reply_to_top_id == 3        → topic_id = 3

# Messaggio nel General topic
message.reply_to.forum_topic == True
message.reply_to.reply_to_top_id == None     → topic_id = 1

# Messaggio in un gruppo normale (nessun forum topic)
message.reply_to == None
  oppure
message.reply_to.forum_topic != True         → topic_id = None
```

**Attenzione**: il check su `forum_topic` usa l'identità (`is not True`) e non la truthiness, per evitare falsi positivi con oggetti MagicMock nei test.

### Semantica dei valori

| Valore | Significato |
|---|---|
| `None` | Gruppo normale o canale, nessun forum topic |
| `1` | General topic del forum (primo topic di default) |
| `> 1` | Topic named del forum, identificato dal suo ID |

---

## 5. Ingestion — persistenza raw

Una volta superati i filtri, il messaggio viene persistito in `raw_messages` tramite `RawMessageIngestionService`.

### Deduplicazione

La chiave di dedup è `(source_chat_id, telegram_message_id)`. Un messaggio già presente viene silenziosamente ignorato (`saved=False`). Questo garantisce che il catchup e il listener live non inseriscano duplicati.

### Acquisition status

| Valore | Quando |
|---|---|
| `ACQUIRED_ELIGIBLE` | Messaggio normale, passa tutti i filtri |
| `BLACKLISTED` | Testo matcha una blacklist; viene salvato ma non parsato |

### Colonne chiave di `raw_messages`

| Colonna | Contenuto |
|---|---|
| `source_chat_id` | ID chat (stringa) |
| `telegram_message_id` | ID messaggio Telegram |
| `source_topic_id` | Topic forum (`null` per chat normali) |
| `raw_text` | Testo del messaggio |
| `processing_status` | `pending` → `processing` → `done` / `failed` / `blacklisted` / `review` |
| `source_trader_id` | Trader esplicito se noto al momento dell'ingestion (di solito `null`) |

---

## 6. Coda asincrona e worker

Dopo l'ingestion, il messaggio viene inserito in una `asyncio.Queue[QueueItem]`.

### QueueItem

```python
@dataclass
class QueueItem:
    raw_message_id: int           # FK in raw_messages
    source_chat_id: str
    telegram_message_id: int
    raw_text: str
    source_trader_id: str | None
    reply_to_message_id: int | None
    acquisition_mode: str         # "live" | "catchup"
    source_topic_id: int | None   # propagato per tutto il pipeline
```

### Worker

`run_worker()` legge dalla coda in loop e chiama `MessageRouter.route(item)` per ogni item. Se il routing solleva un'eccezione non gestita, il messaggio viene marcato `failed` e il worker continua.

Il listener e il worker girano in due task asyncio separati: il listener non si blocca in attesa del parsing.

---

## 7. Router — decisioni di routing

`MessageRouter.route()` in `src/telegram/router.py` gestisce tutto il ciclo di vita di un messaggio dalla coda fino al parser.

### Sequenza di decisioni

```
1. Aggiorna processing_status → "processing"

2. is_blacklisted_text(config, text, chat_id, topic_id)
   → se True: status = "blacklisted", return

3. _resolve_trader(item)
   a. cerca tag trader nel testo (EffectiveTraderResolver)
   b. se non trovato: usa entry matchata da match_entry(chat_id, topic_id)
   → se non risolto: status = "review", inserisce in review_queue, return

4. _is_inactive_channel(chat_id, topic_id)
   → se True: status = "done", return (scope inattivo, non parsare)

5. get_profile_parser(trader_id)
   → se None: salva parse_result come SKIPPED, status = "done", return

6. ParserContext + profile_parser.parse_message()
   → salva parse_result, aggiorna status = "done"

7. (opzionale) OperationRulesEngine + TargetResolver
   → salva signals / operational_signals se validation = VALID
```

### Blacklist

La regola applicata è: **`blacklist_global` + `blacklist_scope_matchato`**.

Il "scope matchato" è l'entry restituita da `match_entry(chat_id, topic_id)`: se il messaggio viene da topic-3 e c'è un'entry specifica per topic-3, si usa la blacklist di quella entry. Non c'è merge implicito con la blacklist dell'entry forum-wide dello stesso chat.

### Risoluzione trader

```
EffectiveTraderResolver (testo + catena reply)
          ↓ se fallisce
match_entry(chat_id, topic_id).trader_id
          ↓ se null
→ unresolved_trader → review_queue
```

In un forum con topic multipli (es. topic-3 → trader_a, topic-4 → trader_b), ogni messaggio ottiene il trader corretto perché `match_entry` restituisce l'entry giusta per quel topic.

---

## 8. Recovery al restart

Al restart viene chiamata `run_recovery(client)` prima di attivare il listener live.

### Stale messages (`_reenqueue_stale`)

Recupera da `raw_messages` i record con `processing_status IN ('pending', 'processing')`: messaggi che erano stati acquisiti ma il processo si è interrotto prima che il worker finisse. Vengono reinseriti nella coda con `acquisition_mode="catchup"` e `source_topic_id` preservato.

### Catchup da Telegram (`_catchup_from_telegram`)

Recupera da Telegram i messaggi arrivati mentre il processo era spento.

**Checkpoint topic-aware**: per ogni entry attiva, il sistema chiede `get_last_telegram_message_id(chat_id, topic_id)` — il massimo `telegram_message_id` già visto per quello scope specifico. Se un forum ha due topic configurati, si calcola il minimo dei due checkpoint e si fa una sola chiamata `get_messages(chat_id, min_id=..., limit=200)`. I messaggi recuperati vengono poi filtrati per scope.

```
Forum -1001: topic-3 last_id=50, topic-4 last_id=100
→ get_messages(-1001, min_id=50, limit=200)
→ filtra: topic-3 → ingest, topic-4 → ingest, topic-99 → scarta
```

La finestra temporale è configurabile (`recovery.max_hours` in channels.yaml): messaggi più vecchi del cutoff vengono ignorati anche se superano il checkpoint.

**Schema legacy**: se `raw_messages` non ha ancora la colonna `source_topic_id` (prima della migration 018), il checkpoint usa solo `chat_id` senza topic.

---

## 9. Hot reload configurazione

`ChannelConfigWatcher` monitora `channels.yaml` in polling ogni 100ms (default). Quando rileva una modifica (`mtime` o `size` cambiati), ricarica il file e chiama `on_reload(new_config)`.

Il listener espone `update_config(new_config)` che aggiorna internamente sia `self._config` che il config del router. Non è necessario riavviare il processo per:
- aggiungere/rimuovere canali
- cambiare `active`, `trader_id`, `blacklist`
- aggiungere nuovi topic a un forum già monitorato

---

## 10. Log chiave

I log usano il formato `chiave=valore` per facilitare parsing e grep.

| Evento | Log |
|---|---|
| Messaggio solo-media scartato | `media_only_skipped \| chat=... topic=... msg_id=...` |
| Messaggio blacklistato | `blacklisted \| chat=... topic=... telegram_message_id=... raw_message_id=...` |
| Duplicato saltato | `duplicate skipped \| chat=... topic=... msg_id=...` |
| Ingestione riuscita | `raw acquired \| chat=... topic=... msg_id=... mode=... raw_message_id=...` |
| Trader non risolto | `trader_unresolved \| chat_id=... telegram_message_id=... method=...` |
| Scope inattivo | `trader_inactive \| trader_id=... chat_id=... topic_id=...` |
| Recovery stale | `recovery: re-enqueuing N stale messages` |
| Recovery catchup | `recovery: N catchup messages \| chat=... since_id=...` |
| Config ricaricata | `channels.yaml reloaded \| channels=N active=M` |

---

## 11. Tabella componenti e file

| Componente | File | Responsabilità |
|---|---|---|
| Config loader + matching | `src/telegram/channel_config.py` | Parsing YAML, validazione, `match_entry` |
| Config watcher | `src/telegram/channel_config.py` | Hot reload via polling |
| Topic extraction | `src/telegram/topic_utils.py` | `extract_message_topic_id` |
| Listener | `src/telegram/listener.py` | Ricezione messaggi, filtro, enqueue, recovery |
| Ingestion service | `src/telegram/ingestion.py` | DTO → `raw_messages` con dedup |
| Queue item | `src/telegram/router.py` | `QueueItem` dataclass |
| Router | `src/telegram/router.py` | Blacklist, trader resolution, parsing, storage |
| Trader resolver | `src/telegram/effective_trader.py` | Ricerca trader da testo, reply chain, source |
| Processing status | `src/storage/processing_status.py` | Lifecycle `processing_status`, checkpoint topic-aware |
| Raw messages store | `src/storage/raw_messages.py` | Persistenza raw con `source_topic_id` |
| Signals store | `src/storage/signals_store.py` | Persistenza segnali operativi |
| Op signals store | `src/storage/operational_signals_store.py` | Persistenza segnali con risk + target |

### Migration DB rilevanti

| Migration | Contenuto |
|---|---|
| `006_raw_messages.sql` | Schema base `raw_messages` |
| `017_raw_message_media.sql` | Colonne media |
| `018_add_source_topic_id_to_raw_messages.sql` | `source_topic_id` + indice topic-aware |
| `019_add_source_topic_id_to_signals_and_op_signals.sql` | Provenance topic in signals + op_signals |

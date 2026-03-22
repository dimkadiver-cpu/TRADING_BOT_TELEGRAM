---
name: telegram-ingestion
description: Usa questa skill per lavorare su Listener e Router/Pre-parser — acquisizione messaggi Telegram, recovery, blacklist, trader resolution e routing verso il parser.
---

# Obiettivo

Gestire correttamente l'ingresso dei messaggi Telegram nel sistema mantenendo separati: acquisizione, recovery, blacklist, risoluzione trader e routing parser.

# Quando usarla

- quando si modifica `src/telegram/listener.py`
- quando si lavora sul Router / Pre-parser
- quando si migliora trader resolution
- quando si tocca blacklist o filtro canali
- quando si analizzano problemi di unresolved trader

# Flusso attuale

```
1. Telethon NewMessage event
2. media check → skip se media senza caption testuale
3. salva in raw_messages (processing_status: pending)
4. asyncio.Queue.put(raw_message_id)
5. worker: asyncio.Queue.get()
6. Router: blacklist check
7. Router: risoluzione trader effettivo
8. Router: filtro trader attivi/inattivi
9. Router: costruisce ParserContext
10. Parser: profilo specifico trader
11. aggiorna processing_status: done | failed | blacklisted | review
```

# File chiave

```
src/telegram/listener.py        → orchestrazione, asyncio event loop
src/telegram/ingestion.py       → persistenza raw_messages
src/telegram/effective_trader.py → risoluzione trader
src/telegram/eligibility.py     → eligibility e strong link
src/telegram/router.py          → [DA IMPLEMENTARE] pre-parser routing
config/channels.yaml            → config canali, blacklist, trader attivi
```

# Config canali — channels.yaml

```yaml
recovery:
  max_hours: 4

blacklist_global:
  - "#admin"
  - "#info"

channels:
  - chat_id: -1001234567890
    label: "nome_canale"
    active: true
    trader_id: "trader_a"       # null se multi-trader
    blacklist:
      - "#weekly"
```

Hot reload: `watchdog` monitora `channels.yaml`. Il listener rilegge la lista canali su modifica senza restart.

# Recovery al restart

```
per ogni chat_id attivo in channels.yaml:
  1. leggi last_message_id da raw_messages
  2. fetch messaggi da last_message_id in poi (Telethon get_messages)
  3. filtra per finestra temporale (default: max_hours da config)
  4. processa in ordine cronologico con acquisition_mode: catchup
  5. riprendi processing dei messaggi in stato "processing" rimasti bloccati
  6. mettiti in ascolto live
```

# processing_status — valori

```
pending      → salvato, in attesa di processing
processing   → worker lo sta processando
done         → processato con successo
failed       → eccezione durante processing (loggata)
blacklisted  → filtrato da blacklist
review       → trader non risolto, in review_queue
```

# acquisition_mode

```
live     → messaggio ricevuto in real-time
catchup  → messaggio recuperato al restart
```

I layer successivi usano questo campo per decidere se eseguire ordini MARKET recuperati — i MARKET in catchup vengono scartati.

# Gestione media

```
solo media (foto, video, documento)  → skip, log "media_only_skipped"
media + caption testuale             → processa solo caption
solo testo                           → processa normalmente
```

# Router — responsabilità in ordine

## 1. Blacklist check
Controlla `raw_text` contro `blacklist_global` e `blacklist` del canale.
- match → `processing_status: blacklisted`, logga, stop

## 2. Risoluzione trader
Usa `effective_trader.py` con priorità:
1. alias nel testo (tag tipo `[trader#A]`)
2. reply al messaggio padre
3. mapping `chat_id → trader_id` da `channels.yaml`

Trader non risolto:
- `processing_status: review`
- inserisce in `review_queue` con `reason: unresolved_trader`
- stop

## 3. Filtro attivi/inattivi
Verifica che `resolved_trader_id` sia `active: true` in `channels.yaml`.
- trader inattivo → `processing_status: done`, logga, stop
- raw_messages preservato per replay futuro

## 4. Costruzione ParserContext
```python
ParserContext(
    trader_id           = resolved_trader_id,
    message_id          = telegram_message_id,
    reply_to_message_id = reply_to_message_id,
    channel_id          = source_chat_id,
    raw_text            = raw_text,
    reply_raw_text      = testo messaggio padre (da raw_messages se reply)
    extracted_links     = link Telegram nel testo
    hashtags            = hashtag nel testo
    acquisition_mode    = live | catchup
)
```

`reply_raw_text` viene recuperato dal Router — il parser non fa query al DB.

# asyncio.Queue — pattern

```python
# listener (producer)
await queue.put(raw_message_id)

# worker (consumer)
raw_message_id = await queue.get()
# processa
queue.task_done()
```

Al restart la queue è vuota. Il recovery dal DB riempie i `pending` rimasti.

# Logging errori

Ogni skip o fallimento viene loggato con:
```
timestamp | chat_id | telegram_message_id | status | reason | raw_text[:200]
```

Mai inghiottito silenziosamente con `except: pass`.

# Regole

- listener.py deve orchestrare, non contenere business logic
- la risoluzione trader avviene PRIMA del parsing
- niente parsing del testo grezzo dentro effective_trader o eligibility
- niente logica exchange in listener.py
- raw_messages viene scritto SEMPRE — anche per messaggi blacklistati
- il Router legge channels.yaml, non il DB, per la lista trader attivi

# Output richiesto

Quando usi questa skill, restituisci sempre:
- fase del flusso coinvolta
- file corretti da toccare
- impatto su trader resolution / blacklist / routing
- rischio di regressione su messaggi esistenti
- test o casi reali da verificare

# PRD Dettagliato — Listener Telegram

## Obiettivo

Acquisire messaggi in real-time da canali Telegram di terzi con latenza minima, recovery automatico al restart, e persistenza garantita di tutti i messaggi.

## Stato

Implementato. Il listener live esiste già e copre ingestione, `asyncio.Queue`, recovery, media-skip e hot reload della configurazione canali.

## Tecnologia

**Telethon** — unica opzione per canali di terzi. Sessione MTProto esistente e funzionante su account reale.

## Architettura interna

```
Telethon NewMessage event
      ↓
media check → skip con log se media senza caption testuale
      ↓
salva raw_messages (processing_status: pending)    ← persistenza garantita
      ↓
asyncio.Queue.put(raw_message_id)                  ← velocità
      ↓
worker: asyncio.Queue.get()                        ← latenza zero
      ↓
Router / Pre-parser
```

## Config canali — config/channels.yaml

```yaml
recovery:
  max_hours: 4                 # finestra temporale recovery al restart

blacklist_global:
  - "#admin"
  - "#info"
  - "#pinned"

channels:
  - chat_id: -1001234567890
    label: "trader_alpha"
    active: true
    trader_id: "trader_a"      # null se canale multi-trader
    blacklist:
      - "#weekly"

  - chat_id: -1009876543210
    label: "canale_multi"
    active: true
    trader_id: null
    blacklist: []
```

**Hot reload** — `watchdog` monitora `channels.yaml`. Il listener rilegge la lista canali su modifica file senza restart del processo.

## processing_status — valori

```
pending      → salvato, in attesa di processing
processing   → worker lo sta processando
done         → processato con successo
failed       → eccezione durante processing (loggata con dettagli)
blacklisted  → filtrato da blacklist
review       → trader non risolto, in review_queue
```

## acquisition_mode

```
live     → messaggio ricevuto in real-time
catchup  → messaggio recuperato al restart
```

I layer successivi usano questo campo per decidere se eseguire ordini MARKET recuperati — i MARKET in catchup vengono scartati.

## Recovery al restart

```
1. per ogni chat_id attivo in channels.yaml
2. leggi last_message_id da raw_messages per quel chat_id
3. fetch messaggi da last_message_id in poi (Telethon get_messages)
4. filtra per finestra temporale (default: max_hours da config)
5. processa in ordine cronologico con acquisition_mode: catchup
6. riprendi processing dei messaggi in stato "processing" rimasti bloccati
7. mettiti in ascolto live con NewMessage handler
```

## Gestione media

```
solo media (foto, video, documento)  → skip, log "media_only_skipped"
media + caption testuale             → processa solo testo caption
solo testo                           → processa normalmente
```

## asyncio.Queue — pattern

```python
# listener (producer)
await queue.put(raw_message_id)

# worker (consumer)
raw_message_id = await queue.get()
# processa
queue.task_done()
```

Al restart la queue è vuota. Il recovery dal DB riempie i `pending` rimasti prima di andare live.

## Sessione Telethon — note deployment

La sessione MTProto è legata all'IP in modo soft. Al primo avvio su server remoto Telegram potrebbe richiedere riautenticazione (OTP una tantum). Dopo la prima autenticazione sul server la sessione si stabilizza.

## Logging

Ogni evento rilevante viene loggato con:
```
timestamp | chat_id | telegram_message_id | status | reason | raw_text[:200]
```

Mai inghiottito silenziosamente. Log file: `logs/listener.log`

## File creati/modificati

```
src/telegram/listener.py         → implementato
config/channels.yaml             → creato
src/telegram/channel_config.py   → implementato hot reload su channels.yaml
```

## File da NON toccare

```
src/telegram/effective_trader.py  → risoluzione trader, stabile
src/telegram/eligibility.py       → eligibility, stabile
src/storage/raw_messages.py       → storage layer, non toccare
```

## Dipendenze

```
telethon>=1.34.0    # già installato
watchdog>=4.0       # già installato
aiosqlite>=0.20.0   # già installato
```

## Test richiesti

- test recovery: simula restart con messaggi pending nel DB
- test blacklist: verifica che messaggi blacklistati abbiano status `blacklisted`
- test hot reload: modifica channels.yaml, verifica che il listener aggiorni la lista
- test media skip: verifica log corretto per messaggi media-only

## Stato di verifica

Coperti nel repository da test dedicati in `src/telegram/tests/`, inclusi recovery, blacklist, media skip e hot reload config.

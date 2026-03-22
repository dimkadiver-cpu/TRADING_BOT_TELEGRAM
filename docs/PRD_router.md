# PRD Dettagliato — Router / Pre-parser

## Obiettivo

Layer tra Listener e Parser. Decide se e a quale profilo parser passare ogni messaggio. Non parsa, non interpreta — smista.

## Stato

Da implementare — Fase 3.

## Posizione nel flusso

```
raw_messages (processing_status: pending)
      ↓
Router / Pre-parser
      ↓
parse_results | review_queue
```

## Responsabilità in ordine

```
1. legge raw_messages con processing_status: pending
2. blacklist check
3. risoluzione trader
4. filtro attivi/inattivi
5. costruisce ParserContext
6. chiama profilo parser corretto
7. aggiorna processing_status
```

## Step 1 — Blacklist check

Controlla `raw_text` contro:
- `blacklist_global` da `channels.yaml`
- `blacklist` specifica del canale

```
match trovato:
  → processing_status: blacklisted
  → logga: chat_id | message_id | matched_pattern
  → STOP — non passa al parser
```

I messaggi blacklistati sono salvati in `raw_messages` — mai persi, sempre tracciabili per audit.

## Step 2 — Risoluzione trader

Usa `effective_trader.py` esistente con priorità:

```
1. alias/tag nel testo          (es. [trader#A])
2. reply al messaggio padre     → cerca trader del messaggio padre in raw_messages
3. mapping chat_id → trader_id  da channels.yaml
```

**Trader non risolto:**
```
→ processing_status: review
→ inserisce in review_queue con reason: unresolved_trader
→ STOP — non passa al parser
```

## Step 3 — Filtro attivi/inattivi

Verifica che `resolved_trader_id` abbia `active: true` in `channels.yaml`.

```
trader inattivo:
  → processing_status: done
  → logga: "trader_inactive: {trader_id}"
  → STOP
  → raw_messages preservato per replay futuro
```

Quando il trader viene riabilitato, il replay_parser.py può rielaborare tutti i suoi messaggi storici dal DB.

## Step 4 — Costruzione ParserContext

```python
ParserContext(
    trader_id           = resolved_trader_id,
    message_id          = telegram_message_id,
    reply_to_message_id = reply_to_message_id,
    channel_id          = source_chat_id,
    raw_text            = raw_text,
    reply_raw_text      = testo messaggio padre (recuperato dal DB se reply)
    extracted_links     = link Telegram estratti dal testo
    hashtags            = hashtag estratti dal testo
    acquisition_mode    = live | catchup
)
```

`reply_raw_text` viene recuperato dal Router tramite query a `raw_messages`. Il parser non fa mai query al DB.

## Step 5 — Chiamata parser e persistenza

```python
try:
    result = profile_parser.parse_message(text, context)
    parse_results_store.upsert(result)
    update_status(raw_message_id, "done")
except Exception as e:
    update_status(raw_message_id, "failed")
    log_error(raw_message_id, e, raw_text)
    # non blocca — continua con il messaggio successivo
```

## Review queue — schema DB

```sql
CREATE TABLE review_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_message_id  INTEGER NOT NULL REFERENCES raw_messages(raw_message_id),
    reason          TEXT NOT NULL,  -- unresolved_trader | parser_error | manual
    created_at      TEXT NOT NULL,
    resolved_at     TEXT,           -- NULL finché in attesa
    resolution      TEXT            -- skipped | processed | escalated
);
```

## processing_status — valori completi

```
pending      → salvato dal Listener, in attesa
processing   → Router lo sta processando
done         → processato con successo (parser chiamato e risultato salvato)
failed       → eccezione durante processing (loggata con dettagli)
blacklisted  → filtrato da blacklist (non processato)
review       → trader non risolto, in review_queue
```

## File da creare/modificare

```
src/telegram/router.py           → NUOVO — logica Router
src/storage/review_queue.py      → NUOVO — storage review_queue
```

## File da NON toccare

```
src/telegram/effective_trader.py → risoluzione trader, stabile
src/telegram/eligibility.py      → stabile
src/storage/raw_messages.py      → storage layer, non toccare
src/storage/parse_results.py     → storage layer, non toccare
```

## Dipendenze

Nessuna dipendenza nuova — usa componenti esistenti.

## Test richiesti

- test blacklist global: messaggio con pattern global viene bloccato
- test blacklist canale: messaggio con pattern specifico canale viene bloccato
- test trader non risolto: va in review_queue con reason corretto
- test trader inattivo: processing_status = done, raw preservato
- test reply_raw_text: ParserContext ha testo messaggio padre
- test eccezione parser: processing_status = failed, loggato, non blocca

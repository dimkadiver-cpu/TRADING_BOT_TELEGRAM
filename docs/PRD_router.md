# PRD Dettagliato — Router / Pre-parser

## Obiettivo

Layer tra Listener e Parser. Decide se e a quale profilo parser passare ogni messaggio. Non parsa, non interpreta — smista.

## Stato

Implementato in Step 10.

La risoluzione via reply è implementata in modo transitivo sulla catena delle reply, con depth limit e protezione da loop. Restano possibili casi limite applicativi nei canali multi-trader con contesto storico incompleto.

Per una fotografia aggiornata della fase, inclusi gap residui e criterio di chiusura operativa, vedere `docs/PHASE_3_ROUTER_STATUS.md`.

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
2. reply-chain ancestry         → risale i parent in raw_messages fino a trovare
                                  trader già risolto o alias nel testo del segnale
3. mapping chat_id → trader_id  da channels.yaml
```

Dettaglio robustezza richiesto per i canali multi-trader:
- non fermarsi al solo parent diretto
- cercare lungo la catena delle reply con profondità massima limitata
- usare come fonti forti, in ordine:
  - `source_trader_id` del parent
  - trader già risolto/persistito per il parent
  - alias/tag nel `raw_text` del parent
- fermarsi appena viene trovato un trader affidabile
- proteggersi da loop con `visited` set

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

Per i messaggi `UPDATE` brevi in canali multi-trader, il Router deve poter
costruire il contesto anche quando il trader è noto solo nel segnale originario
e non nel parent diretto.

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
src/telegram/effective_trader.py → estensione robusta della reply resolution
src/storage/...                  → accessor minimo per risalire la reply-chain
```

## File da NON toccare

```
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
- test reply-chain ancestry: child reply-to-reply eredita trader dal segnale originario
- test reply-chain max-depth: si ferma senza loop o scansioni infinite
- test reply-chain da alias parent: risolve trader da tag nel parent storico
- test eccezione parser: processing_status = failed, loggato, non blocca

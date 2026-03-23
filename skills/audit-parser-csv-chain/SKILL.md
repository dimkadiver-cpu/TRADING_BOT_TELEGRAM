---
name: audit-parser-csv-chain
description: Usa questa skill quando devi controllare risultati parser a partire da CSV o report parser_test, ricostruire la catena dei messaggi (reply, parent, root signal, comandi precedenti), individuare incongruenze tra testo, targeting e output parse, e produrre un report di audit ad alta precisione.
---

# Obiettivo

Fare audit parser partendo dai CSV, non dal codice. La skill deve verificare se il parse salvato o riportato è coerente con:
- testo raw del messaggio
- parent diretto
- eventuale catena `reply -> reply -> signal`
- scope globale implicito nel testo
- comandi precedenti rilevanti nello stesso thread

# Quando usarla

- quando un CSV mostra `message_type`, `intents`, `entities` o `actions` sospetti
- quando ci sono `UNRESOLVED`, `UNCLASSIFIED` o falsi positivi
- quando un update breve sembra corretto solo guardando la chat reale
- quando devi capire se il problema è parser, resolver, targeting o report

# Input attesi

- uno o piu CSV sotto `parser_test/reports/`
- opzionalmente uno o piu `raw_message_id`
- opzionalmente un trader target

# Fonti da usare

1. CSV sotto `parser_test/reports/`
2. DB test `parser_test/db/parser_test.sqlite3`
3. `raw_messages`
4. `parse_results`
5. parser attuale del trader, solo se serve verificare se il record DB è stale

# Workflow

1. Individua le righe problematiche nel CSV.
2. Per ogni riga estrai almeno:
   - `raw_message_id`
   - `telegram_message_id`
   - `message_type`
   - `intents`
   - `raw_text`
3. Ricostruisci la catena messaggi:
   - messaggio corrente
   - parent diretto
   - eventuali parent successivi fino al root signal
   - comandi precedenti collegati via reply o link
4. Confronta:
   - significato umano del testo
   - parse salvato in `parse_results`
   - parse attuale del profilo trader
5. Classifica il problema in una di queste categorie:
   - `stale_db_result`
   - `parser_classification_bug`
   - `parser_intent_bug`
   - `targeting_bug`
   - `trader_resolution_bug`
   - `report_export_bug`
   - `not_a_bug`

# Regole di ricostruzione chain

- partire sempre da `reply_to_message_id` se presente
- cercare il parent in `raw_messages` per stesso `source_chat_id`
- se il parent non basta, risalire altri hop
- se ci sono link `t.me/.../<id>`, considerarli target forti
- distinguere sempre:
  - segnale originario
  - comandi successivi
  - outcome finali

# Distinzioni importanti

- `stale_db_result`: il parser attuale e il DB non coincidono
- `targeting_bug`: gli intent sono giusti ma l'azione punta al target sbagliato
- `parser_intent_bug`: gli intent stessi sono sbagliati
- `not_a_bug`: il parse e coerente, il testo era ambiguo ma il risultato attuale e corretto

# Output richiesto

Restituisci un report con una sezione per messaggio:

```text
raw_message_id:
trader:
esito audit:
testo:
chain:
parse salvato:
parse attuale:
incongruenza:
causa probabile:
fix_area:
```

Chiudi sempre con un riepilogo aggregato:
- numero casi analizzati
- numero bug veri
- numero record stale
- numero casi da rivedere manualmente

# Regole

- non proporre fix di codice in questa skill
- non fermarti al parent diretto se il senso richiede reply-chain
- se il parser attuale differisce dal DB, segnalarlo esplicitamente
- se il caso dipende dal contesto chat, riportare la chain in chiaro

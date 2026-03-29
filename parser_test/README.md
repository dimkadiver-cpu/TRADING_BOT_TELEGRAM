# parser_test

Harness separato per rieseguire il parser sul database di test senza toccare il runtime live.

## Obiettivo

- usare un DB dedicato in `parser_test/db/parser_test.sqlite3`
- rileggere `raw_messages` storici
- rieseguire parser + trader resolution + eligibility
- aggiornare/inserire `parse_results`
- mostrare anche il parse normalizzato (`parse_result_normalized_json`)

## Setup rapido

1. Copia `parser_test/.env.example` in `parser_test/.env` e modifica se serve.
2. Importa storico Telegram nel DB test:

```bash
python parser_test/scripts/import_history.py --chat-id -1001234567890 --db-per-chat --limit 2000
```

3. Riesegui parser sui raw importati:

```bash
python parser_test/scripts/replay_parser.py --chat-id -1001234567890 --db-per-chat --only-unparsed --limit 200
```

## Opzioni principali

- `import_history.py`
- `--chat-id <id|@username|link>` (override di `PARSER_TEST_CHAT_ID`)
- `--topic-id <msg_id_topic>` importa solo un topic/thread del canale forum
- `--limit N`
- `--from-date <YYYY-MM-DD o ISO>`
- `--to-date <YYYY-MM-DD o ISO>`
- `--reverse`
- `--only-new`
- `--download-media` salva anche il media in `raw_messages.media_blob` come BLOB opzionale
- `--db-path <path-db-test>`
- `--db-name <nome-logico-db>`
- `--db-per-chat` crea `parser_test/db/parser_test__chat_<chat>.sqlite3`

- `replay_parser.py`
- `--db-path` path del DB test (default da `.env` oppure `parser_test/db/parser_test.sqlite3`)
- `--db-name` nome logico per riaprire un DB dedicato sotto `parser_test/db`
- `--db-per-chat` usa lo stesso naming automatico basato su `--chat-id`
- `--only-unparsed`
- `--limit N`
- `--chat-id <id>`
- `--trader <TRADER_ID>`
- `--from-date <YYYY-MM-DD o ISO>`
- `--to-date <YYYY-MM-DD o ISO>`
- `--show-normalized-samples N` (default 3, 0 disabilita la stampa esempi)

## Note

- Lo script applica le migration sul DB test, non sul DB live.
- Non avvia listener Telegram e non dipende da Telethon runtime.
- `import_history.py` scrive solo su `raw_messages` (nessun tocco a `parse_results`).
- Con `--download-media`, i media vengono salvati come `BLOB` in `raw_messages.media_blob`; molti editor SQLite li mostrano in modalita' `Image` se il contenuto e' una vera immagine.
- Risoluzione chat target: `--chat-id` > `PARSER_TEST_CHAT_ID`; se assenti entrambi lo script termina con errore esplicito.
- Se usi un link tipo `https://t.me/c/3531875065/175`, il `175` va passato come `--topic-id 175` e il canale come `--chat-id -1003531875065`.

## Comandi pratici

### Import storico in un DB separato per canale

```bash
python parser_test/scripts/import_history.py --chat-id -1001234567890 --db-per-chat --limit 2000
```

### Import solo un topic/thread del canale

```bash
python parser_test/scripts/import_history.py --chat-id -1003531875065 --topic-id 175 --db-per-chat
```

### Import storico con testo + immagine nel DB

```bash
python parser_test/scripts/import_history.py --chat-id -1001234567890 --db-per-chat --download-media
```

### Import solo un topic con testo + immagine nel DB

```bash
python parser_test/scripts/import_history.py --chat-id -1003531875065 --topic-id 175 --db-per-chat --download-media
```

### Import incrementale con download immagini

```bash
python parser_test/scripts/import_history.py --chat-id -1001234567890 --db-per-chat --only-new --download-media
```

### Import incrementale nello stesso DB del canale

```bash
python parser_test/scripts/import_history.py --chat-id -1001234567890 --db-per-chat --only-new
```

### Import con finestra temporale

```bash
python parser_test/scripts/import_history.py --chat-id -1001234567890 --db-per-chat --from-date 2026-03-01 --to-date 2026-03-29
```

### Replay parser sullo stesso DB separato

```bash
python parser_test/scripts/replay_parser.py --chat-id -1001234567890 --db-per-chat --only-unparsed --limit 500
```

### Replay parser filtrando un trader

```bash
python parser_test/scripts/replay_parser.py --chat-id -1001234567890 --db-per-chat --trader trader_a --only-unparsed
```

### Usare un nome DB personalizzato invece del chat id

```bash
python parser_test/scripts/import_history.py --chat-id -1001234567890 --db-name canale_marzo
python parser_test/scripts/replay_parser.py --db-name canale_marzo --only-unparsed
```

### Replay parser + refresh report CSV

```bash
python parser_test/scripts/generate_parser_reports.py --chat-id -1001234567890 --db-per-chat --trader trader_all
```

### Replay parser + report di un solo trader

```bash
python parser_test/scripts/generate_parser_reports.py --chat-id -1001234567890 --db-per-chat --trader trader_a
python parser_test/scripts/generate_parser_reports.py --chat-id -1001234567890 --db-per-chat --trader TA
```

### Solo esporta CSV da un DB gia' popolato

```bash
python parser_test/scripts/generate_parser_reports.py --db-name canale_marzo --trader trader_all --skip-replay
```

## Media nel DB

- Il download dei media e' opzionale: si attiva solo con `--download-media`.
- Se un messaggio contiene testo + immagine, il testo resta in `raw_text` e l'immagine viene salvata in `raw_messages.media_blob`.
- I campi aggiunti in `raw_messages` sono: `has_media`, `media_kind`, `media_mime_type`, `media_filename`, `media_blob`.
- `media_blob` e' un `BLOB` SQLite, quindi molti editor DB lo possono mostrare in `Edit Data Cell` con modalita' `Image`.
- Se non usi `--download-media`, i metadati del messaggio restano importati ma il binario dell'immagine non viene scaricato nel DB.
- Il parser attuale continua a lavorare soprattutto su `raw_text`; il salvataggio immagine serve a conservare il messaggio completo per analisi successive.

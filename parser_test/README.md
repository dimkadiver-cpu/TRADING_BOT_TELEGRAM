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
python parser_test/scripts/import_history.py --limit 2000
```

3. Riesegui parser sui raw importati:

```bash
python parser_test/scripts/replay_parser.py --only-unparsed --limit 200
```

## Opzioni principali

- `import_history.py`
- `--chat-id <id|@username|link>` (override di `PARSER_TEST_CHAT_ID`)
- `--limit N`
- `--from-date <YYYY-MM-DD o ISO>`
- `--to-date <YYYY-MM-DD o ISO>`
- `--reverse`
- `--only-new`
- `--db-path <path-db-test>`

- `replay_parser.py`
- `--db-path` path del DB test (default da `.env` oppure `parser_test/db/parser_test.sqlite3`)
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
- Risoluzione chat target: `--chat-id` > `PARSER_TEST_CHAT_ID`; se assenti entrambi lo script termina con errore esplicito.

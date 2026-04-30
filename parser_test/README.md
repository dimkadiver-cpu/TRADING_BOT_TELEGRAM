# parser_test

Harness separato per rieseguire il parser sul database di test senza toccare il runtime live.

## Obiettivo

- Usare un DB dedicato in `parser_test/db/` (uno per canale o un DB condiviso)
- Rileggere `raw_messages` storici importati da Telegram
- Rieseguire il parser v1 (`parsed_message_v1`) e salvare il risultato in `parsed_messages`
- Esportare report CSV per trader, divisi per tipo di messaggio

## Setup rapido

1. Copia `parser_test/.env.example` in `parser_test/.env` e modifica se serve.

2. Importa storico Telegram nel DB di test:

```bash
python parser_test/scripts/import_history.py --chat-id -1001234567890 --db-per-chat --limit 2000
```

3. Esegui il parser v1 sui raw importati:

```bash
python parser_test/scripts/replay_parser.py \
    --chat-id -1001234567890 --db-per-chat \
    --parser-system parsed_message \
    --only-unparsed
```

4. Esporta i report CSV:

```bash
python parser_test/scripts/generate_parser_reports.py \
    --chat-id -1001234567890 --db-per-chat \
    --parser-system parsed_message \
    --report-system v1 \
    --trader trader_all
```

I CSV vengono scritti in `parser_test/reports/<trader_id>_message_types_csv/`.

---

## Opzioni principali

### `import_history.py`

| Flag | Descrizione |
|---|---|
| `--chat-id <id\|@username\|link>` | Override di `PARSER_TEST_CHAT_ID` |
| `--topic-id <msg_id>` | Importa solo un topic/thread del canale forum |
| `--limit N` | Numero massimo di messaggi da importare |
| `--from-date <YYYY-MM-DD>` | Limite inferiore incluso |
| `--to-date <YYYY-MM-DD>` | Limite superiore incluso |
| `--only-new` | Importa solo messaggi non ancora presenti nel DB |
| `--reverse` | Importa dal più recente al più vecchio |
| `--download-media` | Salva anche i media in `raw_messages.media_blob` |
| `--db-path <path>` | Path esplicito del DB di test |
| `--db-name <nome>` | Nome logico del DB sotto `parser_test/db/` |
| `--db-per-chat` | Crea `parser_test/db/parser_test__chat_<chat>.sqlite3` |

### `replay_parser.py`

| Flag | Descrizione |
|---|---|
| `--parser-system parsed_message` | **Obbligatorio**: usa il parser v1 e scrive in `parsed_messages` |
| `--db-path <path>` | Path esplicito del DB di test |
| `--db-name <nome>` | Nome logico del DB |
| `--db-per-chat` | Usa lo stesso naming basato su `--chat-id` |
| `--only-unparsed` | Processa solo i raw senza risultato in `parsed_messages` |
| `--limit N` | Numero massimo di messaggi da processare |
| `--chat-id <id>` | Filtra per `raw_messages.source_chat_id` |
| `--trader <TRADER_ID>` | Filtra per trader (es. `trader_a`, `TA`) |
| `--from-date <YYYY-MM-DD>` | Limite inferiore incluso |
| `--to-date <YYYY-MM-DD>` | Limite superiore incluso |

### `generate_parser_reports.py`

| Flag | Descrizione |
|---|---|
| `--parser-system parsed_message` | **Obbligatorio**: replay con parser v1 prima dell'export |
| `--report-system v1` | **Obbligatorio**: esporta da `parsed_messages`, niente legacy |
| `--db-path <path>` | Path esplicito del DB di test |
| `--db-name <nome>` | Nome logico del DB |
| `--db-per-chat` | Usa naming automatico basato su `--chat-id` |
| `--trader <TRADER_ID>` | Filtra per trader (`trader_a`, `TA`, `trader_all`) |
| `--from-date <YYYY-MM-DD>` | Limite inferiore incluso |
| `--to-date <YYYY-MM-DD>` | Limite superiore incluso |
| `--limit N` | Numero massimo di messaggi nel replay |
| `--chat-id <id>` | Filtra per canale |

---

## Comandi pratici

### Import storico in un DB separato per canale

```bash
python parser_test/scripts/import_history.py \
    --chat-id -1001234567890 --db-per-chat --limit 2000
```

### Import solo un topic/thread del canale

```bash
python parser_test/scripts/import_history.py \
    --chat-id -1003531875065 --topic-id 175 --db-per-chat
```

### Import storico con download media

```bash
python parser_test/scripts/import_history.py \
    --chat-id -1001234567890 --db-per-chat --download-media
```

### Import incrementale (solo nuovi messaggi)

```bash
python parser_test/scripts/import_history.py \
    --chat-id -1001234567890 --db-per-chat --only-new
```

### Import con finestra temporale

```bash
python parser_test/scripts/import_history.py \
    --chat-id -1001234567890 --db-per-chat \
    --from-date 2026-03-01 --to-date 2026-03-29
```

### Replay parser v1 su DB separato per canale

```bash
python parser_test/scripts/replay_parser.py \
    --chat-id -1001234567890 --db-per-chat \
    --parser-system parsed_message \
    --only-unparsed
```

### Replay parser v1 filtrando un trader

```bash
python parser_test/scripts/replay_parser.py \
    --chat-id -1001234567890 --db-per-chat \
    --parser-system parsed_message \
    --trader trader_a --only-unparsed
```

### Replay con limite messaggi

```bash
python parser_test/scripts/replay_parser.py \
    --chat-id -1001234567890 --db-per-chat \
    --parser-system parsed_message \
    --only-unparsed --limit 500
```

### Usare un nome DB personalizzato

```bash
python parser_test/scripts/import_history.py \
    --chat-id -1001234567890 --db-name canale_marzo

python parser_test/scripts/replay_parser.py \
    --db-name canale_marzo \
    --parser-system parsed_message --only-unparsed
```

### Replay parser + export CSV (tutto in un comando)

```bash
python parser_test/scripts/generate_parser_reports.py \
    --chat-id -1001234567890 --db-per-chat \
    --parser-system parsed_message \
    --report-system v1 \
    --trader trader_all
```

### Replay + report di un solo trader

```bash
python parser_test/scripts/generate_parser_reports.py \
    --chat-id -1001234567890 --db-per-chat \
    --parser-system parsed_message \
    --report-system v1 \
    --trader trader_a
```

### Solo export CSV da un DB già popolato (skip replay)

```bash
python parser_test/scripts/generate_parser_reports.py \
    --db-name canale_marzo \
    --parser-system parsed_message \
    --report-system v1 \
    --trader trader_all \
    --skip-replay
```

---

## Artefatti CSV prodotti

Per ogni trader vengono scritti in `parser_test/reports/<trader_id>_message_types_csv/`:

| File | Contenuto |
|---|---|
| `trader_..._all_messages.csv` | Tutti i messaggi parsati |
| `trader_..._new_signal.csv` | `primary_class=SIGNAL` + `parse_status=PARSED` |
| `trader_..._update.csv` | `primary_class=UPDATE` |
| `trader_..._report.csv` | `primary_class=REPORT` |
| `trader_..._info_only.csv` | `primary_class=INFO` |
| `trader_..._setup_incomplete.csv` | `primary_class=SIGNAL` + `parse_status=PARTIAL` |
| `trader_..._unclassified.csv` | `parse_status=UNCLASSIFIED` |

### Colonne comuni a tutti i file

`raw_message_id` · `reply_to_message_id` · `raw_text` · `parse_status` · `primary_class`

### Colonne aggiuntive — UPDATE / REPORT / INFO / UNCLASSIFIED

`warnings_summary` · `primary_intent` · `intents_confirmed` · `intents_candidate` · `intents_invalid` · `intents_invalid_reason` · `target_scope_scope` · `target_refs` · `new_stop_level` · `close_scope` · `close_fraction` · `hit_target` · `fill_state` · `cancel_scope` · `reported_results`

### Colonne aggiuntive — NEW_SIGNAL / SETUP_INCOMPLETE

`symbol` · `direction` · `risk_hint_value` · `market_type` · `completeness` · `entry_plan_type` · `entry_structure` · `entry_count` · `entries_summary` · `stop_loss_price` · `tp_prices` · `signal_id`

---

## Note

- Lo script applica le migration sul DB di test, non sul DB live.
- Non avvia il listener Telegram e non dipende da Telethon runtime.
- `import_history.py` scrive solo su `raw_messages`; non tocca `parsed_messages`.
- Il parser v1 scrive in `parsed_messages` (migration `022_parsed_messages.sql`). La tabella viene creata automaticamente al primo replay.
- Con `--download-media`, i media vengono salvati come `BLOB` in `raw_messages.media_blob`; molti editor SQLite li mostrano in modalità `Image` se il contenuto è un'immagine.
- Risoluzione chat target: `--chat-id` > `PARSER_TEST_CHAT_ID`; se assenti entrambi lo script termina con errore esplicito.
- Se usi un link tipo `https://t.me/c/3531875065/175`, il `175` va passato come `--topic-id 175` e il canale come `--chat-id -1003531875065`.

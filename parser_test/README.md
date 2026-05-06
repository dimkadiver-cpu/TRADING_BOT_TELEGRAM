# parser_test — Ambiente di test e sviluppo per parser_v2

Ambiente autonomo per importare messaggi reali da Telegram, eseguire `src/parser_v2`
e produrre CSV leggibili per sviluppo e valutazione del parser.

---

## Setup

### 1. Dipendenze

```bash
pip install -r requirements.txt
```

### 2. File `.env`

Copia `.env.example` (se presente) oppure crea `parser_test/.env`:

```env
TELEGRAM_API_ID=<il tuo api_id>
TELEGRAM_API_HASH=<il tuo api_hash>
TELEGRAM_SESSION_NAME=parser_test
```

Le credenziali Telegram si ottengono da https://my.telegram.org.

---

## Flusso operativo

```
import_history.py   →   raw_messages nel DB
                              ↓
generate_parser_reports_v2.py (--force-reparse)
                              ↓
  replay_parser_v2   →   parser_results_v2
  report_export_v2   →   CSV in reports_v2/run_<id>/
```

---

## Comandi

### Import da Telegram

Scarica i messaggi di un canale/topic nel DB locale.

```bash
python parser_test/scripts/import_history.py ^
  --chat-id <CHAT_ID> ^
  --topic-id <TOPIC_ID> ^
  --db-name trader_a_topic ^
  --from-date 2026-04-01 ^
  --to-date 2026-05-01
```

| Argomento | Descrizione |
|-----------|-------------|
| `--chat-id` | ID numerico del canale Telegram |
| `--topic-id` | ID del topic (se il canale usa topics) |
| `--db-name` | Nome del DB locale (file in `db/parser_test__<name>.sqlite3`) |
| `--from-date` | Data inizio (`YYYY-MM-DD`) |
| `--to-date` | Data fine (`YYYY-MM-DD`) |
| `--limit` | Numero massimo messaggi |
| `--only-new` | Importa solo messaggi non ancora nel DB |
| `--download-media` | Scarica anche media (immagini, documenti) |

### Replay parser v2

Riesegue `src/parser_v2` su tutti i messaggi raw salvati.

```bash
python parser_test/scripts/replay_parser_v2.py ^
  --db-name trader_a_topic ^
  --trader trader_a ^
  --force-reparse
```

| Argomento | Descrizione |
|-----------|-------------|
| `--trader` | Profilo da usare (`trader_a`, `ta`, `a`) |
| `--from-date / --to-date` | Filtra per data messaggio |
| `--limit` | Processa solo N messaggi |
| `--only-unparsed` | Salta messaggi già parsati con successo |
| `--force-reparse` | Riprocessa anche se già presenti risultati |
| `--show-samples N` | Stampa N esempi di output a schermo |

### Genera CSV (da run esistente)

```bash
python parser_test/scripts/generate_parser_reports_v2.py ^
  --db-name trader_a_topic ^
  --run latest ^
  --trader trader_a ^
  --skip-replay
```

### Replay + CSV in un comando

```bash
python parser_test/scripts/generate_parser_reports_v2.py ^
  --db-name trader_a_topic ^
  --trader trader_a ^
  --force-reparse
```

### Watch mode (sviluppo attivo)

Monitora i file del profilo e rilancia automaticamente replay + CSV ad ogni modifica.

```bash
python parser_test/scripts/watch_parser.py ^
  --trader trader_a ^
  --db-name trader_a_topic
```

File monitorati: `semantic_markers.json`, `rules.json`, `profile.py`, `signal_extractor.py`,
`intent_entity_extractor.py` in `src/parser_v2/profiles/trader_a/`.

---

## Output CSV

I CSV vengono generati in:

```
parser_test/reports_v2/run_<run_id>/<trader>_message_types_csv/
  <trader>_all_messages.csv
  <trader>_new_signal.csv
  <trader>_update.csv
  <trader>_report.csv
  <trader>_info_only.csv
  <trader>_setup_incomplete.csv
  <trader>_unclassified.csv
  <trader>_errors.csv
```

### Scope CSV

| File | Contenuto |
|------|-----------|
| `all_messages` | Tutti i messaggi parsati con successo |
| `new_signal` | `primary_class=SIGNAL`, `parse_status=PARSED` |
| `setup_incomplete` | `primary_class=SIGNAL`, `parse_status=PARTIAL` |
| `update` | `primary_class=UPDATE` |
| `report` | `primary_class=REPORT` |
| `info_only` | `primary_class=INFO` |
| `unclassified` | `parse_status=UNCLASSIFIED` |
| `errors` | `error_status != OK` oppure `parse_status=ERROR` |

I valori lista nelle celle CSV usano `|` come separatore.
Encoding: UTF-8-sig (compatibile LibreOffice).

---

## Database

Il DB è un file SQLite in `parser_test/db/`. Ogni `--db-name` crea un file separato.

Tabelle principali:
- `raw_messages` — messaggi Telegram importati
- `parser_runs` — metadati di ogni run di replay
- `parser_results_v2` — risultati `CanonicalMessage` per ogni messaggio

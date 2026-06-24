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

## Concetti chiave

Quattro concetti distinti gestiti separatamente:

| Concetto | Significato |
|---|---|
| `source_trader_id` | Trader noto dalla sorgente/import (`--default-source-trader`) |
| `resolved_trader_id` | Trader effettivo usato dal replay; nei DB mono puo essere scritto gia in import, nei DB multi viene persistito da `resolve_traders.py` |
| `--trader-filter` | Quali messaggi includere nel replay (filtra per `resolved_trader_id`) |
| `--parser-profile` | Quale profilo usare per parsare |

---

## Flusso operativo

```
import_history.py          →   raw_messages nel DB
                                       ↓
resolve_traders.py         →   raw_messages.resolved_trader_id (persistito)
                                       ↓
replay_parser_v2.py        →   parser_results_v2
                                       ↓
report_export_v2.py        →   CSV in reports_v2/run_<id>/
```

`generate_parser_reports_v2.py` esegue replay + export in un solo comando.

---

## Comandi

### 1. Import da Telegram

Scarica i messaggi di un canale/topic nel DB locale.

```bash
python parser_test/scripts/import_history.py ^
  --chat-id <CHAT_ID> ^
  --topic-id <TOPIC_ID> ^
  --db-name trader_a_topic ^
  --from-date 2026-04-01 ^
  --to-date 2026-05-01
```

Per canali mono-trader, imposta subito il trader in import. Questo valorizza `source_trader_id` e anche `resolved_trader_id`:

```bash
python parser_test/scripts/import_history.py ^
  --chat-id <CHAT_ID> ^
  --db-name trader_a_topic ^
  --default-source-trader trader_a
```

python parser_test/scripts/import_history.py --chat-id -1001573488012 --db-name trader_crypto_ninjias --default-source-trader trader_crypto_ninjias

python parser_test/scripts/import_history.py --chat-id -1003722628653 --topic-id 3 --db-name trader_A_NOW --default-source-trader trader_a

 python parser_test/scripts/generate_parser_reports_v2.py --db-path "C:\TeleSignalBot\parser_test\db\parser_test__trader_crypto_ninjias.sqlite3" --trader-filter trader_crypto_ninjias  --parser-profile trader_crypto_ninjias --force-reparse
                   

"C:\TeleSignalBot\parser_test\db\parser_test__trader_b_now.sqlite3"


| Argomento | Descrizione |
|-----------|-------------|
| `--chat-id` | ID numerico del canale Telegram |
| `--topic-id` | ID del topic (se il canale usa topics) |
| `--db-name` | Nome del DB locale (file in `db/telegram__<name>.sqlite3`) |
| `--default-source-trader` | Imposta `source_trader_id` e `resolved_trader_id` per tutti i messaggi importati |
| `--from-date` | Data inizio (`YYYY-MM-DD`) |
| `--to-date` | Data fine (`YYYY-MM-DD`) |
| `--limit` | Numero massimo messaggi |
| `--only-new` | Importa solo messaggi non ancora nel DB |
| `--download-media` | Scarica anche media (immagini, documenti) |

---

### 2. Risoluzione trader

Risolve il trader effettivo per ogni messaggio e lo persiste in `raw_messages.resolved_trader_id`.
Nei DB multi-trader e il passaggio obbligatorio prima del replay. Nei DB mono-trader e opzionale se l'import ha gia valorizzato `resolved_trader_id`.

```bash
python parser_test/scripts/resolve_traders.py --db-path  "C:\TeleSignalBot\parser_test\db\parser_test__trader_a_topic.sqlite3"
  --db-name trader_a_topic
```
--db-path  "C:\TeleSignalBot\parser_test\db\parser_test__trader_a_topic.sqlite3"


Per canali dove non è possibile rilevare il trader automaticamente:

```bash
python parser_test/scripts/resolve_traders.py ^
  --db-name trader_a_topic ^
  --assume-trader trader_a
```

| Argomento | Descrizione |
|-----------|-------------|
| `--db-name / --db-path` | DB da aggiornare |
| `--assume-trader` | Trader di fallback se la risoluzione automatica fallisce |
| `--force-re-resolve` | Riprocessa anche i messaggi già risolti |

Output console:
```
[resolve] 1240 messaggi trovati
[resolve] 980 già risolti (skip)
[resolve] completato — source_trader_id: 200 | content_alias: 30 | assume_trader: 5 | unresolved: 15
```

---

### 3. Replay parser v2

Riesegue `src/parser_v2` sui messaggi raw salvati.

python parser_test/scripts/generate_parser_reports_v2.py --db-path "C:\TeleSignalBot\parser_test\db\parser_test__trader_a_topic.sqlite3" --trader-filter trader_a  --parser-profile trader_a --force-reparse

```bash
python parser_test/scripts/replay_parser_v2.py --db-path  "C:\TeleSignalBot\parser_test\db\parser_test__trader_a_topic.sqlite3"   --trader-filter trader_a --parser-profile trader_a --force-reparse 
  --db-name trader_a_topic ^
  --trader-filter trader_a ^
  --parser-profile trader_a ^
  --force-reparse
```

--db-path  "C:\TeleSignalBot\parser_test\db\parser_test__trader_a_topic.sqlite3"

Per parsare tutto il DB con profilo automatico (usa `resolved_trader_id` come profilo):

```bash
python parser_test/scripts/replay_parser_v2.py ^
  --db-name trader_a_topic ^
  --parser-profile auto ^
  --force-reparse
```

| Argomento | Descrizione |
|-----------|-------------|
| `--trader-filter` | Processa solo messaggi con `resolved_trader_id` uguale al valore indicato |
| `--assume-trader` | Trader di fallback se `resolved_trader_id` è NULL |
| `--parser-profile` | Profilo da usare: `auto` (usa `resolved_trader_id`) oppure nome esplicito (`trader_a`) |
| `--allow-cross-profile-parse` | Permette di parsare messaggi di un trader con il profilo di un altro |
| `--audit-csv` | Genera CSV audit con tutti gli stati inclusi gli skip |
| `--from-date / --to-date` | Filtra per data messaggio |
| `--limit` | Processa solo N messaggi |
| `--only-unparsed` | Salta messaggi già parsati con successo |
| `--force-reparse` | Riprocessa anche se già presenti risultati |
| `--show-samples N` | Stampa N esempi di output a schermo |
| `--trader` | **Deprecato** — usa `--trader-filter` |

**Stati tracciati a console:**

| Stato | Significato |
|---|---|
| `OK` / `PARTIAL` / `PARSED` | Parsato correttamente |
| `UNRESOLVED_TRADER` | `resolved_trader_id` non determinabile — skip |
| `SKIPPED_TRADER_FILTER` | `resolved_trader_id` != `--trader-filter` — skip |
| `SKIPPED_UNSUPPORTED_PARSER_PROFILE` | Profilo non registrato — skip |
| `PARSER_ERROR` | Eccezione nel parser — scritto in DB |

---

### 4. Genera CSV (da run esistente)

```bash
python parser_test/scripts/generate_parser_reports_v2.py ^
  --db-name trader_a_topic ^
  --run latest ^
  --trader-filter trader_a ^
  --skip-replay
```

### Replay + CSV in un comando

```bash
python parser_test/scripts/generate_parser_reports_v2.py ^
  --db-name trader_a_topic ^
  --trader-filter trader_a ^
  --parser-profile trader_a ^
  --force-reparse
```

Se non passi `--trader-filter`:
- il replay processa tutti i messaggi compatibili con i parametri scelti;
- l'export genera automaticamente un set CSV separato per ogni `trader_id` presente nel run.

Esempio: DB unico multitrader, profilo parser unico per tutti, report separati per trader:

```bash
python parser_test/scripts/generate_parser_reports_v2.py ^
  --db-name multi ^
  --parser-profile trader_prova ^
  --allow-cross-profile-parse ^
  --force-reparse
```

In questo caso:
- il replay usa `trader_prova` per tutti i messaggi compatibili;
- non serve `--trader-filter`;
- i CSV finali vengono comunque separati per `trader_id` (`trader_a`, `trader_b`, ecc.).

Accetta tutti gli stessi argomenti di `replay_parser_v2.py` più:

| Argomento | Descrizione |
|---|---|
| `--skip-replay` | Salta il replay, usa un run esistente |
| `--run` | `latest` oppure run_id numerico (usato con `--skip-replay`) |
| `--reports-dir` | Directory di output (default: `parser_test/reports_v2/`) |

---

### 5. Watch mode (sviluppo attivo)

Monitora i file del profilo e rilancia automaticamente replay + CSV ad ogni modifica.

```bash
python parser_test/scripts/watch_parser.py ^
  --trader trader_a ^
  --db-name trader_a_topic
```

File monitorati: `semantic_markers.json`, `rules.json`, `profile.py`, `signal_extractor.py`,
`intent_entity_extractor.py` in `src/parser_v2/profiles/trader_a/`.

---

## Flussi tipici

### Mono-trader (canale dedicato)

```bash
python parser_test/scripts/import_history.py --chat-id -123 --db-name trader_a --default-source-trader trader_a
python parser_test/scripts/resolve_traders.py --db-name trader_a
python parser_test/scripts/replay_parser_v2.py --db-name trader_a --trader-filter trader_a --parser-profile trader_a --force-reparse
```

### Multitrader (canale misto)

```bash
python parser_test/scripts/import_history.py --chat-id -123 --db-name multi
python parser_test/scripts/resolve_traders.py --db-name multi
python parser_test/scripts/replay_parser_v2.py --db-name multi --trader-filter trader_a --parser-profile trader_a --force-reparse
```

### Multitrader con profilo unico e report separati per trader

```bash
python parser_test/scripts/import_history.py --chat-id -123 --db-name multi
python parser_test/scripts/resolve_traders.py --db-name multi
python parser_test/scripts/generate_parser_reports_v2.py --db-name multi --parser-profile trader_prova --allow-cross-profile-parse --force-reparse
```

Usa questo flusso quando:
- il DB contiene più trader già risolti in `resolved_trader_id`;
- vuoi parsare tutto con un solo profilo parser;
- vuoi ottenere CSV distinti per ogni trader senza lanciare il comando più volte.

### Tutto il DB con profilo auto

```bash
python parser_test/scripts/replay_parser_v2.py --db-name multi --parser-profile auto --force-reparse
```

---

## Output CSV

Con `--trader-filter trader_a`, i CSV vengono generati in:

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

Senza `--trader-filter`, viene generata una cartella per ogni trader trovato nel run:

```
parser_test/reports_v2/run_<run_id>/trader_a_message_types_csv/
parser_test/reports_v2/run_<run_id>/trader_b_message_types_csv/
parser_test/reports_v2/run_<run_id>/trader_c_message_types_csv/
```

Esempio reale:

```
parser_test/reports_v2/run_42/trader_a_message_types_csv/trader_a_all_messages.csv
parser_test/reports_v2/run_42/trader_b_message_types_csv/trader_b_all_messages.csv
parser_test/reports_v2/run_42/trader_c_message_types_csv/trader_c_all_messages.csv
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

Con `--audit-csv` viene generato anche `audit_run_<run_id>.csv` con tutti gli stati inclusi gli skip.

I valori lista nelle celle CSV usano `|` come separatore.
Encoding: UTF-8-sig (compatibile LibreOffice).

---

## Database

Il DB è un file SQLite in `parser_test/db/`. Ogni `--db-name` crea un file separato.

Tabelle principali:

| Tabella | Contenuto |
|---|---|
| `raw_messages` | Messaggi Telegram importati. Colonne chiave: `source_trader_id`, `resolved_trader_id`, `resolution_method` |
| `parser_runs` | Metadati di ogni run di replay |
| `parser_results_v2` | Risultati `CanonicalMessage` per ogni messaggio |

`resolved_trader_id` è NULL finché `resolve_traders.py` non è stato eseguito.
`resolution_method` traccia come è stato risolto: `source_trader_id` | `content_alias` | `reply_chain` | `assume_trader` | `unresolved`.

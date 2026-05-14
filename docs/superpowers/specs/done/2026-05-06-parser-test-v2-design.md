# Design — parser_test v2 agganciato a src/parser_v2

**Data:** 2026-05-06
**Stato:** approvato

---

## 1. Obiettivo

Sostituire completamente il parser_test v1 (agganciato a `src/parser`) con una nuova versione autonoma agganciata a `src/parser_v2`. Il vecchio layer viene eliminato subito. Il nuovo ambiente permette di importare messaggi reali da Telegram, eseguire il parser v2, e produrre CSV leggibili per sviluppo e valutazione.

---

## 2. Decisioni chiave

| Decisione | Scelta |
|-----------|--------|
| Parser usato | solo `src/parser_v2` (`UniversalParserRuntime`) |
| DB parser_test | autonomo dal DB operativo — `CREATE TABLE IF NOT EXISTS` inline |
| Schema management | `parser_test/db/schema.py` centralizzato (Approccio A) |
| Vecchi moduli v1 | eliminati subito |
| Registry trader | dict factory estensibile, solo `trader_a` registrato ora |
| watch_parser.py | aggiornato per parser_v2, non eliminato |
| README | riscritto con istruzioni operative complete |

---

## 3. File eliminati

```
parser_test/scripts/replay_parser.py
parser_test/scripts/generate_parser_reports.py
parser_test/scripts/export_reports_csv.py
parser_test/scripts/audit_canonical_v1.py
parser_test/reporting/flatteners.py
parser_test/reporting/flatteners_v1.py
parser_test/reporting/report_schema.py
parser_test/reporting/report_schema_v1.py
parser_test/reporting/report_export.py
parser_test/reporting/report_export_v1.py
parser_test/reporting/canonical_v1_audit.py
```

Test obsoleti da eliminare:
```
parser_test/tests/test_report_export.py
parser_test/tests/test_parser_dispatcher_modes.py
parser_test/tests/test_canonical_schema_alignment.py
parser_test/tests/test_parse_result_normalized.py
parser_test/tests/test_ta_profile_refactor.py
parser_test/tests/test_pipeline_semantic_consistency.py
parser_test/scripts/tests/test_replay_parser_phase3.py
parser_test/scripts/tests/test_replay_parser_parsed_messages.py
parser_test/scripts/tests/test_generate_parser_reports.py
parser_test/scripts/tests/test_audit_canonical_v1.py
```

---

## 4. Struttura finale

```
parser_test/
├── db/
│   ├── __init__.py
│   ├── schema.py                      ← NUOVO
│   └── tests/
│       └── test_schema.py             ← NUOVO
├── scripts/
│   ├── db_paths.py                    ← invariato
│   ├── import_history.py              ← invariato
│   ├── replay_parser_v2.py            ← NUOVO
│   ├── generate_parser_reports_v2.py  ← NUOVO
│   └── watch_parser.py                ← aggiornato
├── reporting/
│   ├── __init__.py
│   ├── report_schema_v2.py            ← NUOVO
│   ├── flatteners_v2.py               ← NUOVO
│   └── report_export_v2.py            ← NUOVO
└── README.md                          ← riscritto

src/
├── parser_v2/profiles/
│   └── registry.py                    ← NUOVO
└── storage/
    ├── parser_runs.py                 ← NUOVO
    └── parser_results_v2.py           ← NUOVO
```

Test nuovi:
```
tests/parser_v2/test_registry.py
tests/storage/test_parser_runs.py
tests/storage/test_parser_results_v2.py
parser_test/db/tests/test_schema.py
```

---

## 5. Componenti

### 5.1 `src/parser_v2/profiles/registry.py`

Dict factory con alias normalizzati. Registra solo `trader_a` ora; aggiungere un trader futuro = una riga nel dict.

```python
_PROFILE_FACTORIES: dict[str, type] = {
    "trader_a": TraderAProfile,
    "ta": TraderAProfile,
}

def canonicalize_trader_v2(value: str | None) -> str | None
def get_parser_v2_profile(trader_id: str) -> TraderParserProfile  # raises KeyError se sconosciuto
def list_parser_v2_profiles() -> list[str]  # solo nomi canonici, senza alias
```

---

### 5.2 `parser_test/db/schema.py`

```python
def apply_parser_test_schema(conn: sqlite3.Connection) -> None
```

Crea con `CREATE TABLE IF NOT EXISTS`:
- `raw_messages` — fonte dati primaria (invariata)
- `parser_runs` — ciclo vita di ogni run
- `parser_results_v2` — risultati canonici

Più tutti gli indici definiti nel PRD. Idempotente: può essere chiamata più volte senza effetti collaterali.

---

### 5.3 `src/storage/parser_runs.py`

Riceve `sqlite3.Connection` dall'esterno (non apre DB).

```python
@dataclass(slots=True)
class ParserRunRecord:
    run_id: int
    started_at: str
    completed_at: str | None
    db_scope: str | None
    trader_filter: str | None
    parser_system: str
    parser_version: str | None
    force_reparse: bool
    notes: str | None

class ParserRunStore:
    def __init__(self, conn: sqlite3.Connection) -> None
    def create_run(...) -> int
    def complete_run(run_id: int) -> None
    def get_latest_run(...) -> ParserRunRecord | None
```

---

### 5.4 `src/storage/parser_results_v2.py`

Riceve `sqlite3.Connection` dall'esterno.

```python
@dataclass(slots=True)
class ParserResultV2Record:
    run_id: int
    raw_message_id: int
    trader_id: str | None
    parser_profile: str | None
    primary_class: str | None
    parse_status: str | None
    primary_intent: str | None
    confidence: float | None
    canonical_json: str | None
    warnings_json: str | None
    diagnostics_json: str | None
    error_status: str          # "OK" | "UNRESOLVED_TRADER" | "PARSER_ERROR"
    error_message: str | None
    created_at: str

class ParserResultV2Store:
    def __init__(self, conn: sqlite3.Connection) -> None
    def insert_result(record: ParserResultV2Record) -> None
    def fetch_by_run(run_id: int, trader: str | None = None) -> list[ParserResultV2Record]
    def fetch_latest_run_results(trader: str | None = None) -> list[ParserResultV2Record]
```

---

### 5.5 `parser_test/scripts/replay_parser_v2.py`

**CLI args:** `--db-path`, `--db-name`, `--db-per-chat`, `--chat-id`, `--trader`, `--from-date`, `--to-date`, `--limit`, `--only-unparsed`, `--force-reparse`, `--show-samples`

**Flusso:**
1. Apre connessione DB via `db_paths.py`
2. `apply_parser_test_schema(conn)`
3. `ParserRunStore.create_run()`
4. Legge `raw_messages` con filtri
5. Per ogni messaggio:
   - Risolve trader: `--trader` → `source_trader_id` → mapping → `UNRESOLVED_TRADER`
   - `get_parser_v2_profile(trader_id)`
   - Costruisce `ParserContext` + `RawContext`
   - `UniversalParserRuntime().parse(text, context, profile)` → `CanonicalMessage`
   - `ParserResultV2Store.insert_result(...)` — mai interrompe il run
6. `ParserRunStore.complete_run(run_id)`
7. Stampa summary: totale / parsed / partial / unclassified / error

**Risoluzione trader (ordine):**
1. `--trader` esplicito se fornito
2. `raw_messages.source_trader_id` se valorizzato
3. Mapping chat/topic → trader se configurato
4. `error_status = UNRESOLVED_TRADER` — il run continua

---

### 5.6 `parser_test/scripts/generate_parser_reports_v2.py`

**CLI args:** tutti quelli di replay + `--skip-replay`, `--run latest|<run_id>`, `--reports-dir`

Se `--skip-replay` non passato: esegue il replay inline (non subprocess).
Poi: `report_export_v2.export_all(conn, run_id, trader, reports_dir)`.
Output: lista file generati con conteggio righe per ognuno.

---

### 5.7 `parser_test/reporting/`

**`report_schema_v2.py`** — dizionario `scope → list[str]` con colonne comuni + colonne specifiche per scope (SIGNAL, UPDATE, REPORT, INFO, ERRORS). Source of truth per l'ordine delle colonne CSV.

**`flatteners_v2.py`** — `flatten_for_scope(scope, result_record, raw_record) -> dict`. Legge `canonical_json` via `json.loads` e naviga il dizionario — nessuna importazione di `CanonicalMessage` a runtime (evita dipendenza circular e overhead di validazione Pydantic sul path report).

**`report_export_v2.py`** — `export_all(conn, run_id, trader, reports_dir)`. Per ogni scope: filtra records, chiama flattener, scrive CSV con encoding `UTF-8-sig` e separatore `|` per le liste.

---

### 5.8 `parser_test/scripts/watch_parser.py` (aggiornato)

Monitora: `src/parser_v2/profiles/<trader>/semantic_markers.json`, `rules.json`, `profile.py`.
Al cambio rilancia: `generate_parser_reports_v2.py --trader <trader> --force-reparse`.
Debounce 2s invariato.

---

## 6. Scope CSV

| Scope | Filtro |
|-------|--------|
| ALL | `error_status = OK` |
| NEW_SIGNAL | `error_status = OK`, `primary_class = SIGNAL`, `parse_status = PARSED` |
| SETUP_INCOMPLETE | `error_status = OK`, `primary_class = SIGNAL`, `parse_status = PARTIAL` |
| UPDATE | `error_status = OK`, `primary_class = UPDATE` |
| REPORT | `error_status = OK`, `primary_class = REPORT` |
| INFO_ONLY | `error_status = OK`, `primary_class = INFO` |
| UNCLASSIFIED | `error_status = OK`, `parse_status = UNCLASSIFIED` |
| ERRORS | `error_status != OK` OR `parse_status = ERROR` |

Output: `reports_v2/run_<run_id>/<trader>_message_types_csv/<trader>_<scope_lower>.csv`

---

## 7. Error handling

| Errore | Comportamento |
|--------|---------------|
| Trader non risolvibile | `error_status=UNRESOLVED_TRADER`, run continua |
| `UniversalParserRuntime` lancia eccezione | `error_status=PARSER_ERROR`, run continua |
| Errore insert singolo risultato | log warning, run continua |
| DB non apribile / schema non applicabile | eccezione propagata, exit 1 |

---

## 8. Testing

| File | Cosa copre |
|------|-----------|
| `tests/parser_v2/test_registry.py` | alias trader, trader sconosciuto, list_profiles |
| `tests/storage/test_parser_runs.py` | create/complete/get_latest con DB in-memory |
| `tests/storage/test_parser_results_v2.py` | insert OK, insert error, fetch_by_run, fetch_latest |
| `parser_test/db/tests/test_schema.py` | apply_schema idempotente, tabelle presenti |

---

## 9. README.md (parser_test)

Sezioni:
1. Setup (dipendenze, `.env` con credenziali Telegram)
2. Import da Telegram — comando completo con tutti i flag
3. Replay parser v2 — comando con opzioni
4. Genera CSV — con e senza `--skip-replay`
5. Replay + CSV in un comando
6. Watch mode per sviluppo attivo
7. Struttura output `reports_v2/run_<id>/`
8. Descrizione scope CSV

---

## 10. Comandi attesi

```bash
# Import da Telegram
python parser_test/scripts/import_history.py ^
  --chat-id <CHAT_ID> --topic-id <TOPIC_ID> ^
  --db-name trader_a_topic ^
  --from-date 2026-04-01 --to-date 2026-05-01

# Replay parser v2
python parser_test/scripts/replay_parser_v2.py ^
  --db-name trader_a_topic --trader trader_a --force-reparse

# Solo CSV (da run esistente)
python parser_test/scripts/generate_parser_reports_v2.py ^
  --db-name trader_a_topic --run latest --trader trader_a --skip-replay

# Replay + CSV in un comando
python parser_test/scripts/generate_parser_reports_v2.py ^
  --db-name trader_a_topic --trader trader_a --force-reparse
```

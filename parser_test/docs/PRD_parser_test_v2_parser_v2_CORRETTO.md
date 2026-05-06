# PRD — `parser_test` Versione 2 agganciato a `src/parser_v2`

## 1. Obiettivo

Realizzare un ambiente `parser_test` dedicato al nuovo parser in `src/parser_v2`.

Lo scopo è:

```text
scaricare/importare messaggi reali da Telegram
  ↓
salvarli nel DB di test come raw_messages
  ↓
rieseguire src/parser_v2 sui messaggi storici
  ↓
salvare il risultato canonico del parser
  ↓
produrre CSV leggibili per sviluppo, debug e valutazione del parser
```

`parser_test` deve servire per sviluppare e valutare il parser su dati reali, senza usare il runtime operativo live.

---

## 2. Principi obbligatori

### 2.1 Il parser da usare è solo `src/parser_v2`

Il replay deve agganciarsi direttamente a:

```text
src/parser_v2/core/runtime.py
src/parser_v2/profiles/<trader>/
src/parser_v2/contracts/
```

Il runtime atteso è:

```python
from src.parser_v2.core.runtime import UniversalParserRuntime

runtime = UniversalParserRuntime()
canonical_message = runtime.parse(
    text=raw_text,
    context=context,
    profile=profile,
)
```

Il risultato atteso è un `CanonicalMessage`.

---

### 2.2 Il recupero Telegram deve riusare il layer esistente

La parte di acquisizione messaggi deve continuare a riusare:

```text
src/telegram/
src/telegram/ingestion.py
src/storage/raw_messages.py
```

Non bisogna riscrivere una seconda logica di import.

Il layer Telegram deve supportare:

```text
canale intero
topic specifico
range date
limite messaggi
solo messaggi nuovi
download immagini/media opzionale
```

I dati acquisiti devono finire in `raw_messages`.

---

### 2.3 Database semplice

Il database di test deve essere ridotto ai dati realmente utili:

```text
raw_messages
parser_runs
parser_results_v2
```

Il risultato parserizzato deve essere salvato come JSON canonico intero, più alcune colonne indicizzabili per query e report.

---

### 2.4 Report CSV derivati dal risultato canonico

I CSV devono essere prodotti leggendo `parser_results_v2.canonical_json`.

Non devono essere generati da output intermedi o strutture parallele.

---

## 3. Contratto dati del parser v2

Il CSV deve derivare dal contratto reale `CanonicalMessage`.

Struttura canonica attesa:

```text
CanonicalMessage
  schema_version
  parser_profile
  primary_class
  parse_status
  confidence
  primary_intent
  intents
  signal
  update
  report
  info
  targeted_actions
  target_hints
  warnings
  diagnostics
  raw_context
```

Valori principali:

```text
primary_class:
  SIGNAL
  UPDATE
  REPORT
  INFO

parse_status:
  PARSED
  PARTIAL
  UNCLASSIFIED
  ERROR
```

Nota importante:

```text
UNCLASSIFIED non è un primary_class.
UNCLASSIFIED è un parse_status.
```

---

## 4. Flusso generale

```text
Telegram
  ↓
import_history.py
  ↓
RawMessageIngestionService
  ↓
raw_messages
  ↓
replay_parser_v2.py
  ↓
UniversalParserRuntime
  ↓
CanonicalMessage
  ↓
parser_results_v2
  ↓
generate_parser_reports_v2.py
  ↓
CSV per trader / scope
```

---

## 5. Moduli da mantenere / creare

## 5.1 Moduli esistenti da riusare

### `parser_test/scripts/import_history.py`

Responsabilità:

```text
importare messaggi Telegram nel DB di test
```

Deve continuare a supportare:

```text
--chat-id
--topic-id
--limit
--from-date
--to-date
--only-new
--download-media
--db-path
--db-name
--db-per-chat
```

Non deve eseguire il parser.

---

### `src/telegram/ingestion.py`

Responsabilità:

```text
ricevere TelegramIncomingMessage
convertirlo in RawMessageRecord
salvarlo tramite RawMessageStore
```

Campi rilevanti:

```text
source_chat_id
source_chat_title
source_type
source_trader_id
telegram_message_id
reply_to_message_id
source_topic_id
raw_text
message_ts
has_media
media_kind
media_mime_type
media_filename
media_blob
```

---

### `src/storage/raw_messages.py`

Responsabilità:

```text
persistenza raw_messages
deduplicazione per source_chat_id + telegram_message_id
recupero messaggi raw per replay
```

---

## 5.2 Moduli nuovi da creare

### `src/parser_v2/profiles/registry.py`

Responsabilità:

```text
registrare i profili disponibili del parser_v2
normalizzare alias trader
restituire il profilo corretto per replay
```

API proposta:

```python
def canonicalize_trader_v2(value: str | None) -> str | None:
    ...

def get_parser_v2_profile(value: str):
    ...

def list_parser_v2_profiles() -> list[str]:
    ...
```

Esempio iniziale:

```python
from src.parser_v2.profiles.trader_a.profile import TraderAProfile

_PROFILE_FACTORIES = {
    "trader_a": TraderAProfile,
    "ta": TraderAProfile,
    "a": TraderAProfile,
}
```

---

### `src/storage/parser_runs.py`

Responsabilità:

```text
creare run parser
chiudere run parser
recuperare ultimo run
```

API proposta:

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
    def create_run(...) -> int:
        ...

    def complete_run(run_id: int) -> None:
        ...

    def get_latest_run(...) -> ParserRunRecord | None:
        ...
```

---

### `src/storage/parser_results_v2.py`

Responsabilità:

```text
salvare risultati CanonicalMessage
salvare errori per singolo messaggio
recuperare risultati per run/trader/scope
```

API proposta:

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
    error_status: str
    error_message: str | None
    created_at: str

class ParserResultV2Store:
    def insert_result(record: ParserResultV2Record) -> None:
        ...

    def fetch_by_run(run_id: int, trader: str | None = None) -> list[ParserResultV2Record]:
        ...

    def fetch_latest_run_results(trader: str | None = None) -> list[ParserResultV2Record]:
        ...
```

---

### `parser_test/scripts/replay_parser_v2.py`

Responsabilità:

```text
leggere raw_messages
risolvere trader/profilo parser_v2
costruire ParserContext v2
eseguire UniversalParserRuntime
salvare parser_results_v2
```

Argomenti CLI:

```text
--db-path
--db-name
--db-per-chat
--chat-id
--trader
--from-date
--to-date
--limit
--only-unparsed
--force-reparse
--show-samples
```

---

### `parser_test/reporting/report_schema_v2.py`

Responsabilità:

```text
definire colonne CSV per ogni scope
```

---

### `parser_test/reporting/flatteners_v2.py`

Responsabilità:

```text
convertire CanonicalMessage JSON in riga CSV piatta
```

---

### `parser_test/reporting/report_export_v2.py`

Responsabilità:

```text
leggere parser_results_v2
filtrare per scope
scrivere CSV
```

---

### `parser_test/scripts/generate_parser_reports_v2.py`

Responsabilità:

```text
opzionalmente eseguire replay_parser_v2
generare CSV
stampare riepilogo finale
```

Argomenti CLI:

```text
--db-path
--db-name
--db-per-chat
--run latest | <run_id>
--trader
--from-date
--to-date
--limit
--force-reparse
--reports-dir
--skip-replay
```

---

## 6. Schema DB proposto

## 6.1 `raw_messages`

La tabella `raw_messages` resta la fonte dati primaria.

Campi minimi richiesti:

```sql
raw_message_id INTEGER PRIMARY KEY AUTOINCREMENT,
source_chat_id TEXT NOT NULL,
source_chat_title TEXT,
source_type TEXT,
source_trader_id TEXT,
source_topic_id INTEGER,
telegram_message_id INTEGER NOT NULL,
reply_to_message_id INTEGER,
raw_text TEXT,
message_ts TEXT NOT NULL,
acquired_at TEXT NOT NULL,
acquisition_status TEXT,
has_media INTEGER DEFAULT 0,
media_kind TEXT,
media_mime_type TEXT,
media_filename TEXT,
media_blob BLOB,
UNIQUE(source_chat_id, telegram_message_id)
```

---

## 6.2 `parser_runs`

```sql
CREATE TABLE IF NOT EXISTS parser_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    db_scope TEXT,
    trader_filter TEXT,
    parser_system TEXT NOT NULL DEFAULT 'parser_v2',
    parser_version TEXT,
    force_reparse INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);
```

Indici:

```sql
CREATE INDEX IF NOT EXISTS idx_parser_runs_started_at
ON parser_runs(started_at);
```

---

## 6.3 `parser_results_v2`

```sql
CREATE TABLE IF NOT EXISTS parser_results_v2 (
    parser_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    raw_message_id INTEGER NOT NULL,

    trader_id TEXT,
    parser_profile TEXT,

    primary_class TEXT,
    parse_status TEXT,
    primary_intent TEXT,
    confidence REAL,

    canonical_json TEXT,
    warnings_json TEXT,
    diagnostics_json TEXT,

    error_status TEXT NOT NULL DEFAULT 'OK',
    error_message TEXT,

    created_at TEXT NOT NULL,

    UNIQUE(run_id, raw_message_id),
    FOREIGN KEY(run_id) REFERENCES parser_runs(run_id),
    FOREIGN KEY(raw_message_id) REFERENCES raw_messages(raw_message_id)
);
```

Indici:

```sql
CREATE INDEX IF NOT EXISTS idx_parser_results_v2_run
ON parser_results_v2(run_id);

CREATE INDEX IF NOT EXISTS idx_parser_results_v2_raw
ON parser_results_v2(raw_message_id);

CREATE INDEX IF NOT EXISTS idx_parser_results_v2_trader
ON parser_results_v2(trader_id);

CREATE INDEX IF NOT EXISTS idx_parser_results_v2_class_status
ON parser_results_v2(primary_class, parse_status);

CREATE INDEX IF NOT EXISTS idx_parser_results_v2_error
ON parser_results_v2(error_status);
```

---

## 7. Replay parser v2

## 7.1 Input dati

`replay_parser_v2.py` deve leggere da `raw_messages`.

Filtri supportati:

```text
chat_id
trader
from_date
to_date
limit
only_unparsed
```

---

## 7.2 Risoluzione trader

Ordine di risoluzione consigliato:

```text
1. --trader esplicito, se fornito
2. raw_messages.source_trader_id, se valorizzato
3. mapping chat/topic → trader, se configurato
4. errore controllato: trader unresolved
```

Se il trader non è risolvibile, il messaggio non deve bloccare il run.

Deve essere salvato in `parser_results_v2` con:

```text
error_status = UNRESOLVED_TRADER
error_message = spiegazione breve
```

---

## 7.3 Costruzione `ParserContext`

Per ogni raw message:

```python
from src.parser_v2.contracts.context import ParserContext, RawContext

context = ParserContext(
    message_id=raw.telegram_message_id,
    reply_to_message_id=raw.reply_to_message_id,
    source_chat_id=raw.source_chat_id,
    source_topic_id=raw.source_topic_id,
    raw_context=RawContext(
        raw_text=raw.raw_text or "",
        message_id=raw.telegram_message_id,
        reply_to_message_id=raw.reply_to_message_id,
        source_chat_id=raw.source_chat_id,
        source_topic_id=raw.source_topic_id,
        extracted_links=extract_telegram_links(raw.raw_text or ""),
        hashtags=extract_hashtags(raw.raw_text or ""),
    ),
)
```

---

## 7.4 Esecuzione parser

```python
runtime = UniversalParserRuntime()

canonical = runtime.parse(
    text=raw.raw_text or "",
    context=context,
    profile=profile,
)
```

---

## 7.5 Salvataggio risultato

Dal `CanonicalMessage` salvare:

```text
parser_profile      ← canonical.parser_profile
primary_class       ← canonical.primary_class
parse_status        ← canonical.parse_status
primary_intent      ← canonical.primary_intent
confidence          ← canonical.confidence
canonical_json      ← canonical.model_dump_json(exclude_none=True)
warnings_json       ← canonical.warnings
diagnostics_json    ← canonical.diagnostics
error_status        ← OK
```

In caso di errore di esecuzione replay/parser:

```text
canonical_json      ← NULL
error_status        ← PARSER_ERROR
error_message       ← exception breve
```

Il run deve continuare anche se un messaggio fallisce.

---

## 8. Produzione CSV v2

## 8.1 Directory output

Default:

```text
parser_test/reports_v2/
```

Struttura consigliata:

```text
parser_test/reports_v2/run_<run_id>/
  trader_a_message_types_csv/
    trader_a_all_messages.csv
    trader_a_new_signal.csv
    trader_a_update.csv
    trader_a_report.csv
    trader_a_info_only.csv
    trader_a_setup_incomplete.csv
    trader_a_unclassified.csv
    trader_a_errors.csv
```

---

## 8.2 Scope CSV

```text
ALL
NEW_SIGNAL
UPDATE
REPORT
INFO_ONLY
SETUP_INCOMPLETE
UNCLASSIFIED
ERRORS
```

Mappatura:

```text
ALL
  error_status = OK

NEW_SIGNAL
  error_status = OK
  primary_class = SIGNAL
  parse_status = PARSED

SETUP_INCOMPLETE
  error_status = OK
  primary_class = SIGNAL
  parse_status = PARTIAL

UPDATE
  error_status = OK
  primary_class = UPDATE

REPORT
  error_status = OK
  primary_class = REPORT

INFO_ONLY
  error_status = OK
  primary_class = INFO

UNCLASSIFIED
  error_status = OK
  parse_status = UNCLASSIFIED

ERRORS
  error_status != OK
  OR parse_status = ERROR
```

---

## 8.3 Colonne comuni

Queste colonne derivano da `raw_messages`, `parser_results_v2` e `CanonicalMessage`.

```text
run_id
raw_message_id
telegram_message_id
source_chat_id
source_topic_id
reply_to_message_id
message_ts
trader_id
parser_profile
schema_version
raw_text
primary_class
parse_status
primary_intent
intents
confidence
warnings
diagnostics_summary
```

Origine:

```text
run_id                  parser_results_v2.run_id
raw_message_id          raw_messages.raw_message_id
telegram_message_id     raw_messages.telegram_message_id
source_chat_id          raw_messages.source_chat_id
source_topic_id         raw_messages.source_topic_id
reply_to_message_id     raw_messages.reply_to_message_id
message_ts              raw_messages.message_ts
trader_id               parser_results_v2.trader_id
parser_profile          canonical.parser_profile
schema_version          canonical.schema_version
raw_text                canonical.raw_context.raw_text oppure raw_messages.raw_text
primary_class           canonical.primary_class
parse_status            canonical.parse_status
primary_intent          canonical.primary_intent
intents                 canonical.intents
confidence              canonical.confidence
warnings                canonical.warnings
diagnostics_summary     sintesi di canonical.diagnostics
```

---

## 8.4 Colonne SIGNAL

Il contratto reale del payload signal è:

```text
signal.symbol
signal.side
signal.entry_structure
signal.entries
signal.stop_loss
signal.take_profits
signal.risk_hint
signal.leverage_hint
signal.missing_fields
signal.completeness
```

Colonne CSV corrette:

```text
symbol
side
entry_structure
entries_count
entries_summary
stop_loss_price
take_profit_count
take_profit_prices
risk_hint_raw
risk_hint_value
risk_hint_min_value
risk_hint_max_value
leverage_hint
missing_fields
completeness
```

Regole di estrazione:

```text
symbol                  signal.symbol
side                    signal.side
entry_structure         signal.entry_structure
entries_count           len(signal.entries)
entries_summary         entry.sequence:entry.entry_type:entry.role@entry.price.value
stop_loss_price         signal.stop_loss.price.value
take_profit_count       len(signal.take_profits)
take_profit_prices      lista take_profit.price.value
risk_hint_raw           signal.risk_hint.raw
risk_hint_value         signal.risk_hint.value
risk_hint_min_value     signal.risk_hint.min_value
risk_hint_max_value     signal.risk_hint.max_value
leverage_hint           signal.leverage_hint
missing_fields          join(signal.missing_fields)
completeness            signal.completeness
```

Campi esclusi:

```text
market_type
```

Motivo:

```text
market_type non esiste nel contratto CanonicalMessage/SignalPayload.
Se serve, deve essere aggiunto al contratto oppure calcolato esternamente come campo derivato.
```

---

## 8.5 Colonne UPDATE

Il contratto reale update contiene:

```text
update.operations[]
targeted_actions[]
target_hints
```

Ogni `UpdateOperation` contiene:

```text
op_type
set_stop
close
cancel_pending
modify_entries
modify_targets
invalidate_setup
source_intent
confidence
raw_fragment
```

Colonne CSV corrette:

```text
operations_count
operations_summary
operation_types
source_intents
operation_confidences
operation_raw_fragments

target_scope_hint
target_reply_to_message_id
target_telegram_message_ids
target_telegram_links
target_explicit_ids
target_symbols

set_stop_target_type
set_stop_price
set_stop_tp_level

close_scope
close_fraction
close_price

cancel_scope_hint

modify_entries_kind
modify_entries_count
modify_entries_summary
modify_entries_entry_structure

modify_targets_mode
modify_targets_count
modify_targets_prices
modify_targets_target_tp_level

invalidate_reason_text

targeted_actions_count
targeted_actions_summary
```

Regole di estrazione:

```text
operations_count                len(update.operations)
operations_summary              sintesi leggibile di update.operations
operation_types                 join(op.op_type)
source_intents                  join(op.source_intent)
operation_confidences           join(op.confidence)
operation_raw_fragments         join(op.raw_fragment)

target_scope_hint               target_hints.scope_hint
target_reply_to_message_id      target_hints.reply_to_message_id
target_telegram_message_ids     join(target_hints.telegram_message_ids)
target_telegram_links           join(target_hints.telegram_links)
target_explicit_ids             join(target_hints.explicit_ids)
target_symbols                  join(target_hints.symbols)

set_stop_target_type            op.set_stop.target_type
set_stop_price                  op.set_stop.price.value
set_stop_tp_level               op.set_stop.tp_level

close_scope                     op.close.close_scope
close_fraction                  op.close.fraction
close_price                     op.close.close_price.value

cancel_scope_hint               op.cancel_pending.cancel_scope_hint

modify_entries_kind             op.modify_entries.kind
modify_entries_count            len(op.modify_entries.entries)
modify_entries_summary          sintesi entries
modify_entries_entry_structure  op.modify_entries.entry_structure

modify_targets_mode             op.modify_targets.mode
modify_targets_count            len(op.modify_targets.take_profits)
modify_targets_prices           join(tp.price.value)
modify_targets_target_tp_level  op.modify_targets.target_tp_level

invalidate_reason_text          op.invalidate_setup.reason_text

targeted_actions_count          len(targeted_actions)
targeted_actions_summary        sintesi targeted_actions
```

Nota importante:

```text
targeted_actions non va ignorato.
Nei messaggi con link/riferimenti multipli il translator può spostare operazioni da update.operations a targeted_actions.
```

---

## 8.6 Colonne REPORT

Il contratto reale report contiene:

```text
report.events[]
report.result
```

Ogni `ReportEvent` contiene:

```text
event_type
level
price
source_intent
raw_fragment
```

`ReportResult` contiene:

```text
raw_fragment
```

Colonne CSV corrette:

```text
report_events_count
report_events_summary
report_event_types
report_event_levels
report_event_prices
report_event_source_intents
report_event_raw_fragments
report_result_raw_fragment
hit_target
hit_price
```

Regole di estrazione:

```text
report_events_count             len(report.events)
report_events_summary           sintesi leggibile degli eventi
report_event_types              join(event.event_type)
report_event_levels             join(event.level)
report_event_prices             join(event.price.value)
report_event_source_intents     join(event.source_intent)
report_event_raw_fragments      join(event.raw_fragment)
report_result_raw_fragment      report.result.raw_fragment
```

Campi derivati:

```text
hit_target
hit_price
```

Regole campi derivati:

```text
hit_target:
  se event_type = TP_HIT e level presente → TP<level>
  se event_type = TP_HIT senza level → TP
  se event_type = SL_HIT → SL
  se event_type = EXIT_BE → BE
  se event_type = ENTRY_FILLED e level presente → ENTRY<level>
  se event_type = ENTRY_FILLED senza level → ENTRY

hit_price:
  primo event.price.value disponibile
```

Nota:

```text
REPORT_RESULT è un intent unico.
Non esistono REPORT_FINAL_RESULT e REPORT_PARTIAL_RESULT nel nuovo contratto.
```

---

## 8.7 Colonne INFO

Il contratto reale info contiene:

```text
info.raw_fragment
```

Colonne CSV corrette:

```text
info_raw_fragment
```

Regola di estrazione:

```text
info_raw_fragment = info.raw_fragment
```

---

## 8.8 Colonne ERRORS

Il CSV errors deve unire due casi:

```text
1. errore tecnico di replay/storage/profile resolution
2. errore semantico/canonico del parser con parse_status = ERROR
```

Colonne:

```text
run_id
raw_message_id
telegram_message_id
source_chat_id
source_topic_id
message_ts
trader_id
parser_profile
primary_class
parse_status
primary_intent
error_status
error_message
warnings
diagnostics_summary
raw_text
```

Regole:

```text
error_status != OK
OR parse_status = ERROR
```

---

## 8.9 Campi vietati o non canonici

Questi campi non devono comparire come colonne primarie canoniche:

```text
market_type
direction
target_refs
report_result
```

Correzioni:

```text
market_type      → rimuovere oppure dichiarare derivato esterno
direction        → usare side
target_refs      → usare target_hints.*
report_result    → usare report_result_raw_fragment
```

Questi campi possono esistere solo come colonne derivate:

```text
hit_target
hit_price
operations_summary
entries_summary
report_events_summary
targeted_actions_summary
diagnostics_summary
```

---

## 9. Comandi attesi

## 9.1 Import da Telegram

```bash
python parser_test/scripts/import_history.py ^
  --chat-id <CHAT_ID> ^
  --topic-id <TOPIC_ID> ^
  --db-name trader_a_topic ^
  --from-date 2026-04-01 ^
  --to-date 2026-05-01 ^
  --download-media
```

---

## 9.2 Replay parser v2

```bash
python parser_test/scripts/replay_parser_v2.py ^
  --db-name trader_a_topic ^
  --trader trader_a ^
  --force-reparse
```

---

## 9.3 Generazione CSV da ultimo run

```bash
python parser_test/scripts/generate_parser_reports_v2.py ^
  --db-name trader_a_topic ^
  --run latest ^
  --trader trader_a ^
  --skip-replay
```

---

## 9.4 Replay + CSV in un comando

```bash
python parser_test/scripts/generate_parser_reports_v2.py ^
  --db-name trader_a_topic ^
  --trader trader_a ^
  --force-reparse
```

---

## 10. Checklist implementazione

## Fase 1 — Registry profili parser_v2

- [ ] Creare `src/parser_v2/profiles/registry.py`.
- [ ] Registrare `TraderAProfile`.
- [ ] Implementare `canonicalize_trader_v2`.
- [ ] Implementare `get_parser_v2_profile`.
- [ ] Implementare `list_parser_v2_profiles`.
- [ ] Aggiungere test unitari per alias trader.

---

## Fase 2 — DB

- [ ] Creare migration per `parser_runs`.
- [ ] Creare migration per `parser_results_v2`.
- [ ] Aggiungere indici.
- [ ] Verificare che `raw_messages` abbia `source_topic_id`.
- [ ] Verificare che `raw_messages` abbia campi media opzionali.
- [ ] Non modificare il significato di `raw_messages`.

---

## Fase 3 — Storage

- [ ] Creare `src/storage/parser_runs.py`.
- [ ] Creare `src/storage/parser_results_v2.py`.
- [ ] Testare creazione run.
- [ ] Testare completamento run.
- [ ] Testare inserimento risultato OK.
- [ ] Testare inserimento risultato errore.
- [ ] Testare fetch per run.
- [ ] Testare fetch latest.

---

## Fase 4 — Replay

- [ ] Creare `parser_test/scripts/replay_parser_v2.py`.
- [ ] Leggere DB tramite `db_paths.py`.
- [ ] Applicare migration prima del replay.
- [ ] Leggere `raw_messages` con filtri.
- [ ] Risolvere trader.
- [ ] Costruire `ParserContext`.
- [ ] Eseguire `UniversalParserRuntime`.
- [ ] Salvare `CanonicalMessage`.
- [ ] Salvare errori senza interrompere il run.
- [ ] Stampare summary finale.

---

## Fase 5 — CSV

- [ ] Creare `report_schema_v2.py`.
- [ ] Creare `flatteners_v2.py`.
- [ ] Creare `report_export_v2.py`.
- [ ] Implementare scope `ALL`.
- [ ] Implementare scope `NEW_SIGNAL`.
- [ ] Implementare scope `UPDATE`.
- [ ] Implementare scope `REPORT`.
- [ ] Implementare scope `INFO_ONLY`.
- [ ] Implementare scope `SETUP_INCOMPLETE`.
- [ ] Implementare scope `UNCLASSIFIED`.
- [ ] Implementare scope `ERRORS`.
- [ ] Verificare che i campi CSV siano derivati da `CanonicalMessage`.
- [ ] Verificare che `market_type` non venga usato come campo canonico.
- [ ] Verificare che `side` venga usato al posto di `direction`.
- [ ] Verificare che `target_hints.*` venga usato al posto di `target_refs`.
- [ ] Verificare che `targeted_actions` venga esportato.

---

## Fase 6 — Comando unico

- [ ] Creare `generate_parser_reports_v2.py`.
- [ ] Supportare `--skip-replay`.
- [ ] Supportare `--run latest`.
- [ ] Supportare `--run <id>`.
- [ ] Supportare `--force-reparse`.
- [ ] Stampare file generati e numero righe.

---

## 11. Criteri di accettazione

Il lavoro è accettabile solo se:

- [ ] `import_history.py` importa messaggi reali nel DB.
- [ ] I messaggi topic hanno `source_topic_id` valorizzato.
- [ ] I media vengono salvati solo se `--download-media` è attivo.
- [ ] `replay_parser_v2.py` usa `src/parser_v2`.
- [ ] Il replay produce `CanonicalMessage`.
- [ ] Il replay salva in `parser_results_v2`.
- [ ] Ogni esecuzione crea un `parser_run`.
- [ ] Gli errori per singolo messaggio non interrompono il run.
- [ ] I CSV leggono da `parser_results_v2`.
- [ ] I CSV sono separati per trader e scope.
- [ ] Il comando unico replay + CSV funziona.
- [ ] Nessun modulo del vecchio parser è necessario per il nuovo flusso.
- [ ] Le colonne CSV coincidono con il contratto `CanonicalMessage`.
- [ ] Il CSV `UPDATE` esporta anche `targeted_actions`.
- [ ] Il CSV `INFO_ONLY` esporta `info_raw_fragment`.
- [ ] Il CSV `ERRORS` include sia `error_status != OK` sia `parse_status = ERROR`.

---

## 12. Rischi tecnici

### 12.1 Registry parser_v2 assente

Rischio:

```text
il replay non sa quale profilo istanziare
```

Mitigazione:

```text
creare subito src/parser_v2/profiles/registry.py
```

---

### 12.2 Context incompleto

Rischio:

```text
il parser non riesce a risolvere reply, topic o link
```

Mitigazione:

```text
costruire sempre ParserContext + RawContext completi
```

---

### 12.3 CSV troppo povero

Rischio:

```text
il report non permette di capire gli errori del parser
```

Mitigazione:

```text
includere sempre raw_text, warnings, diagnostics_summary e campi principali del payload
```

---

### 12.4 Targeted actions ignorate

Rischio:

```text
i messaggi con link multipli o scope globale sembrano vuoti nel CSV update
```

Mitigazione:

```text
esportare sempre targeted_actions_count e targeted_actions_summary
```

---

### 12.5 Nessuna valutazione automatica

Rischio:

```text
il CSV mostra l'output ma non dice se è corretto
```

Mitigazione futura:

```text
aggiungere expected_labels.csv
aggiungere parser_mismatches.csv
aggiungere accuracy_summary.csv
```

Questa parte non è obbligatoria per la prima versione.

---

## 13. Output finale atteso

Dopo implementazione, il ciclo operativo deve essere:

```bash
python parser_test/scripts/import_history.py ^
  --chat-id <CHAT_ID> ^
  --db-name trader_a_test ^
  --from-date 2026-04-01 ^
  --to-date 2026-05-01
```

```bash
python parser_test/scripts/generate_parser_reports_v2.py ^
  --db-name trader_a_test ^
  --trader trader_a ^
  --force-reparse
```

Output:

```text
parser_test/reports_v2/run_<run_id>/trader_a_message_types_csv/
  trader_a_all_messages.csv
  trader_a_new_signal.csv
  trader_a_update.csv
  trader_a_report.csv
  trader_a_info_only.csv
  trader_a_setup_incomplete.csv
  trader_a_unclassified.csv
  trader_a_errors.csv
```

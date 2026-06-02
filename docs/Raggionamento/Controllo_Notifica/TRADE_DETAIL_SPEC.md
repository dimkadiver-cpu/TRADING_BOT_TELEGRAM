# TRADE_DETAIL_SPEC - /trade #id compact/full con storia chain e link Telegram

Versione: 1.0
Stato: proposta implementativa

---

## 1. Obiettivo

Ridisegnare il comando Telegram Control Plane:

```text
/trade #id
```

in una vista operativa utile davvero per seguire la storia di una singola chain.

Il redesign introduce due modalita:

```text
/trade #id
  -> vista compact

/trade #id full
  -> vista full con timeline completa
```

Obiettivi concreti:

```text
- mostrare lo stato corrente della chain senza aprire il DB
- mostrare una mini-storia leggibile della chain
- mostrare il link al messaggio origine del segnale
- mostrare il link agli update trader derivati da Telegram quando disponibili
- non rompere il sistema attuale CLEAN_LOG
- non duplicare logica di dominio nel formatter Telegram
```

---

## 2. Principi di design

### 2.1 Distinzione tra snapshot e storia

`ops_trade_chains` resta la fonte di verita per lo stato corrente.

```text
ops_trade_chains
  -> snapshot corrente della chain

ops_lifecycle_events
  -> storia operativa della chain
```

Il comando `/trade` non deve tentare di ricostruire lo stato attuale solo dagli eventi.
Gli eventi servono a spiegare la storia, non a sostituire lo snapshot.

### 2.2 Distinzione tra segnale origine e update trader

I link Telegram vanno trattati come due categorie diverse:

```text
origin_signal_link
  -> messaggio che ha creato la chain

update links
  -> messaggi trader successivi che hanno modificato la chain
```

Un singolo campo generico `source` o `original_message_link` non basta piu.

### 2.3 Nessun impatto comportamentale su CLEAN_LOG

Le modifiche previste qui sono additive-only:

```text
- nuovi campi nel view model TradeDetail
- nuova vista full del comando /trade
- metadata extra nei payload dei lifecycle events trader-linked
```

Non vanno cambiati:

```text
- event_type esistenti
- naming dei notification_type CLEAN_LOG
- chiavi payload gia lette dal clean_log
- semantica attuale di SIGNAL_ACCEPTED / UPDATE_DONE / UPDATE_PARTIAL / UPDATE_REJECTED
```

---

## 3. Esperienza utente target

### 3.1 Vista compact

La vista compact deve rispondere in pochi secondi a queste domande:

```text
1. Dove si trova la chain adesso?
2. Cosa e gia successo?
3. Cosa resta da aspettare?
4. Da quale messaggio e partita?
5. Qual e l'ultimo update trader che l'ha toccata?
```

Template target:

```text
📌 TRADE #184
BTCUSDT | LONG | PARTIALLY_CLOSED
Trader: trader_a | Account: master_account

Now:
Avg entry: 63,235
Open qty: 0.10
SL: 63,500
Protection: protected

Plan:
Entries: all filled
TPs: TP1 hit | TP2 64,550 pending

Story:
09:12 Signal accepted
09:31 Entry updated after second fill
10:06 TP1 filled
10:18 Trader update applied

Next:
Watching TP2

Links:
Signal: https://t.me/c/.../411
Last update: https://t.me/c/.../428
Updates linked: 2
```

### 3.2 Vista full

La vista full deve funzionare come timeline auditabile, senza scadere nel raw dump.

Template target:

```text
📜 TRADE #184 FULL
BTCUSDT | LONG | trader_a | master_account

Timeline:
09:12 SIGNAL_ACCEPTED
link: https://t.me/c/.../411

09:31 ENTRY_UPDATED
source: exchange

10:18 TELEGRAM_UPDATE_ACCEPTED
source: trader update
link: https://t.me/c/.../428

10:42 BE_EXIT
source: exchange

Final:
Net PnL: +4.20 USDT
Fees: 1.80 USDT
Funding: 0.00 USDT
```

### 3.3 Regola di verbosity

```text
/trade #id
  -> sintetico, orientato all'operatore

/trade #id full
  -> piu completo, orientato a forensic/debug operativo
```

Non introdurre un terzo mode nel primo step.
`raw` resta opzionale e fuori scope per questa spec.

---

## 4. Sintassi comando

Comportamento richiesto:

```text
/trade 145
  -> compact

/trade #145
  -> compact

/trade 145 full
  -> full

/trade #145 full
  -> full
```

Usage ufficiale:

```text
Usage: /trade <chain_id> [full]
```

Argomenti invalidi:

```text
/trade
/trade foo
/trade 145 nope
```

devono restituire:

```text
REJECTED
reject_reason="invalid_arguments"
```

senza alterare la semantica di audit attuale.

---

## 5. Fonti dati

### 5.1 Stato corrente

Fonte primaria:

```text
ops_trade_chains
```

Campi gia disponibili:

```text
trade_chain_id
trader_id
account_id
symbol
side
lifecycle_state
entry_avg_price
current_stop_price
expected_stop_price
be_protection_status
management_plan_json
risk_snapshot_json
planned_entry_qty
filled_entry_qty
open_position_qty
closed_position_qty
execution_mode
plan_state_json
source_chat_id
telegram_message_id
cumulative_gross_pnl
cumulative_fees
cumulative_funding
allocated_margin
created_at
updated_at
```

Riferimenti:

- [001_ops_lifecycle_core.sql](/abs/path/C:/TeleSignalBot/db/ops_migrations/001_ops_lifecycle_core.sql:3)
- [003_ops_quantity_runtime.sql](/abs/path/C:/TeleSignalBot/db/ops_migrations/003_ops_quantity_runtime.sql:1)
- [004_ops_plan_state.sql](/abs/path/C:/TeleSignalBot/db/ops_migrations/004_ops_plan_state.sql:1)
- [005_ops_risk_tracking.sql](/abs/path/C:/TeleSignalBot/db/ops_migrations/005_ops_risk_tracking.sql:1)
- [009_ops_source_message_link.sql](/abs/path/C:/TeleSignalBot/db/ops_migrations/009_ops_source_message_link.sql:1)
- [010_ops_pnl_columns.sql](/abs/path/C:/TeleSignalBot/db/ops_migrations/010_ops_pnl_columns.sql:1)

### 5.2 Storia operativa

Fonte primaria:

```text
ops_lifecycle_events
```

Campi disponibili:

```text
event_id
trade_chain_id
event_type
source_type
source_id
previous_state
next_state
payload_json
idempotency_key
created_at
```

Uso richiesto:

```text
- costruzione mini-story per compact
- costruzione timeline completa per full
- derivazione close_reason se necessario
- derivazione latest_trader_message_link
- conteggio linked_update_count
```

### 5.3 Link segnale origine

Fonte:

```text
ops_trade_chains.source_chat_id
ops_trade_chains.telegram_message_id
```

Regola:

```python
if source_chat_id.startswith("-100"):
    link = f"https://t.me/c/{source_chat_id[4:]}/{telegram_message_id}"
```

Questo link e gia affidabile.

### 5.4 Link update trader

Situazione attuale:

```text
- il lifecycle update conosce il raw_message_id dell'update corrente
- _persist_update() costruisce gia un update_source_link
- quel link oggi viene usato per CLEAN_LOG
- non esiste pero una relazione forte e persistita per ogni lifecycle event update
```

Conclusione:

```text
per la vista /trade full i metadata update devono essere persistiti nei lifecycle event trader-linked
```

---

## 6. Data model del Control Plane

La dataclass `TradeDetail` in `status_queries.py` deve essere estesa.

### 6.1 TradeDetail

```python
@dataclass
class TradeDetail:
    chain_id: int
    symbol: str
    side: str
    trader_id: str
    account_id: str
    state: str

    opened_at: str | None
    updated_at: str | None
    closed_at: str | None

    entry_avg_price: float | None
    open_position_qty: float | None
    filled_entry_qty: float | None
    closed_position_qty: float | None

    current_stop_price: float | None
    expected_stop_price: float | None
    be_protection_status: str | None

    planned_entries: list[TradePlannedEntry]
    planned_targets: list[TradePlannedTarget]

    origin_signal_link: str | None
    latest_trader_message_link: str | None
    linked_update_count: int

    story_items: list[TradeStoryItem]
    timeline_items: list[TradeTimelineItem]

    next_action_label: str | None
    final_result: TradeFinalResult | None
```

### 6.2 TradePlannedEntry

```python
@dataclass
class TradePlannedEntry:
    sequence: int
    entry_type: str
    price: float | None
    status: str
    filled_price: float | None = None
```

### 6.3 TradePlannedTarget

```python
@dataclass
class TradePlannedTarget:
    level: int
    price: float | None
    status: str
```

### 6.4 TradeStoryItem

Da usare solo per la vista compact.

```python
@dataclass
class TradeStoryItem:
    at: str | None
    label: str
    kind: str
    source: str
    source_link: str | None
```

### 6.5 TradeTimelineItem

Da usare per la vista full.

```python
@dataclass
class TradeTimelineItem:
    event_id: int
    at: str | None
    event_type: str
    title: str
    source: str
    source_link: str | None
    summary_lines: list[str]
    status: str | None
```

### 6.6 TradeFinalResult

```python
@dataclass
class TradeFinalResult:
    close_reason: str | None
    roi_net_pct: float | None
    total_pnl_net: float | None
    gross_pnl: float | None
    fees: float | None
    funding: float | None
```

---

## 7. Mappatura campi

### 7.1 Mappatura snapshot

| Campo TradeDetail | Sorgente |
|---|---|
| `chain_id` | `ops_trade_chains.trade_chain_id` |
| `symbol` | `ops_trade_chains.symbol` |
| `side` | `ops_trade_chains.side` |
| `trader_id` | `ops_trade_chains.trader_id` |
| `account_id` | `ops_trade_chains.account_id` |
| `state` | `ops_trade_chains.lifecycle_state` |
| `opened_at` | `ops_trade_chains.created_at` |
| `updated_at` | `ops_trade_chains.updated_at` |
| `entry_avg_price` | `ops_trade_chains.entry_avg_price` |
| `open_position_qty` | `ops_trade_chains.open_position_qty` |
| `filled_entry_qty` | `ops_trade_chains.filled_entry_qty` |
| `closed_position_qty` | `ops_trade_chains.closed_position_qty` |
| `current_stop_price` | `ops_trade_chains.current_stop_price` |
| `expected_stop_price` | `ops_trade_chains.expected_stop_price` |
| `be_protection_status` | `ops_trade_chains.be_protection_status` |
| `origin_signal_link` | `source_chat_id` + `telegram_message_id` |

### 7.2 Mappatura final result

| Campo TradeFinalResult | Sorgente |
|---|---|
| `gross_pnl` | `cumulative_gross_pnl` |
| `fees` | `cumulative_fees` |
| `funding` | `cumulative_funding` |
| `total_pnl_net` | `gross_pnl - fees - funding` |
| `roi_net_pct` | `total_pnl_net / allocated_margin * 100` se `allocated_margin > 0` |
| `close_reason` | ultimo evento terminale oppure payload terminale |

Nota:

```text
nel comando /trade le fee vanno mostrate come costo positivo leggibile
nel clean_log oggi il final_result usa fee negative per convenzione di rendering
questa spec non cambia quella convenzione
```

### 7.3 Mappatura plan

Fonte primaria:

```text
plan_state_json
```

Fallback:

```text
management_plan_json
```

Regola:

```text
- non ricostruire il plan dal lifecycle se plan_state_json e gia sufficiente
- usare lifecycle solo per arricchire lo stato se il plan snapshot e incompleto
```

### 7.4 Mappatura story e timeline

Fonte:

```sql
SELECT event_id, event_type, source_type, source_id, previous_state, next_state, payload_json, created_at
FROM ops_lifecycle_events
WHERE trade_chain_id = ?
ORDER BY event_id ASC
```

Uso:

```text
story_items
  -> selezione e normalizzazione di pochi eventi ad alto segnale

timeline_items
  -> quasi tutti gli eventi rilevanti, in ordine cronologico
```

---

## 8. Mapping eventi -> linguaggio utente

### 8.1 Compact story

| event_type | Label compact |
|---|---|
| `SIGNAL_ACCEPTED` | `Signal accepted` |
| `ENTRY_FILLED` / `ENTRY_OPENED` | `Entry opened` |
| `ENTRY_UPDATED` | `Entry updated` |
| `TP_FILLED` | `TP{n} filled` |
| `TP_FILLED_FINAL` | `Final TP hit` |
| `SL_FILLED` | `Stop loss hit` |
| `BE_EXIT` | `BE exit` |
| `POSITION_CLOSED` | `Position closed` |
| `TELEGRAM_UPDATE_ACCEPTED` | `Trader update applied` |
| `REVIEW_REQUIRED` | `Review required` |
| `RECONCILIATION_WARNING` | `Reconciliation warning` |
| `RECONCILIATION_FIXED` | `Reconciliation fixed` |

Regole:

```text
- massimo 5-8 righe
- ignorare eventi troppo tecnici o ripetitivi
- se un update produce 3 eventini interni ravvicinati, compact deve collassarli
```

### 8.2 Full timeline

Per `full` la title puo essere piu vicina all'`event_type`.

Esempio:

```text
SIGNAL_ACCEPTED
ENTRY_UPDATED
TELEGRAM_UPDATE_ACCEPTED
TP_FILLED
BE_EXIT
POSITION_CLOSED
```

Il dettaglio entra in `summary_lines`.

---

## 9. Link Telegram

### 9.1 Campo `origin_signal_link`

Sempre calcolato da:

```text
ops_trade_chains.source_chat_id
ops_trade_chains.telegram_message_id
```

Questo e il messaggio che ha creato la chain.

### 9.2 Campo `latest_trader_message_link`

Definizione:

```text
ultimo lifecycle event della chain che deriva da un update trader e possiede metadata Telegram
```

Non deve puntare:

```text
- a eventi exchange
- a eventi runtime
- al segnale origine, se non ci sono stati update trader successivi
```

### 9.3 Campo `linked_update_count`

Definizione:

```text
numero di lifecycle event trader-linked con metadata Telegram validi
```

Se un singolo update genera piu eventi lifecycle, il conteggio puo essere:

```text
- per event
oppure
- per distinct raw_message_id
```

Scelta raccomandata:

```text
count distinct raw_message_id
```

perche corrisponde meglio al numero di messaggi Telegram trader.

### 9.4 Campo `source_link` per timeline item

Regola:

```text
eventi segnale origine
  -> origin_signal_link

eventi trader-linked
  -> link dal metadata evento

eventi exchange/runtime/worker
  -> None
```

---

## 10. Gap attuale e modifica richiesta

### 10.1 Gap attuale

Oggi il sistema conosce il link update corrente in `_persist_update()`, ma non persiste una relazione forte per ogni lifecycle event update.

Quindi:

```text
origin_signal_link
  -> gia robusto

latest_trader_message_link
  -> recuperabile solo indirettamente

timeline item link per update
  -> non affidabile in modo generale
```

### 10.2 Modifica richiesta

Per ogni `LifecycleEvent` nato da update trader, aggiungere metadata Telegram nel `payload_json`.

Formato minimo accettabile:

```json
{
  "raw_message_id": 123
}
```

Formato preferito:

```json
{
  "raw_message_id": 123,
  "source_chat_id": "-1001234567890",
  "telegram_message_id": 428
}
```

### 10.3 Motivazione

Questa scelta:

```text
- evita euristiche ex post
- evita join fragili parser DB -> ops DB al momento della query
- permette full timeline con link evento-per-evento
- resta compatibile con il clean_log attuale
```

---

## 11. Compatibilita con CLEAN_LOG

### 11.1 Stato attuale

Il sistema `clean_log` legge `ops_lifecycle_events.payload_json` e consuma solo alcune chiavi note.

Punti rilevanti:

- [outbox_writer.py](/abs/path/C:/TeleSignalBot/src/runtime_v2/control_plane/outbox_writer.py:162)
- [project_clean_log_for_chain()](/abs/path/C:/TeleSignalBot/src/runtime_v2/control_plane/outbox_writer.py:526)
- [_write_update_clean_log()](/abs/path/C:/TeleSignalBot/src/runtime_v2/lifecycle/entry_gate.py:79)

### 11.2 Regola di sicurezza

Le modifiche di questa spec non devono cambiare:

```text
- event_type esistenti
- source_type esistenti
- naming dei notification_type CLEAN_LOG
- chiavi payload gia lette dal clean_log
```

Le modifiche consentite:

```text
- aggiungere chiavi extra nei payload_json
- leggere quei metadata dal comando /trade
- estendere il formatter del comando /trade
```

### 11.3 Invarianti anti-regressione

Non toccare queste chiavi gia usate dal clean_log:

```text
action
reason
applied_actions
rejected_actions
changed_fields
source
source_message_link
tp_level
is_final
fill_price
closed_size
exec_fee
close_reason
```

Chiavi extra ammesse:

```text
raw_message_id
source_chat_id
telegram_message_id
```

Conclusione:

```text
la feature /trade compact/full non deve intaccare il clean_log
se la persistenza metadata resta additive-only
```

---

## 12. Next Action Logic

La vista compact deve avere un blocco `Next:` derivato da regole semplici.

Regole iniziali:

```text
WAITING_ENTRY + no fill
  -> Watching entry fill

OPEN/PARTIALLY_CLOSED + pending TP
  -> Waiting TP{n}

OPEN/PARTIALLY_CLOSED + BE protected
  -> Waiting TP or BE exit

REVIEW_REQUIRED
  -> Manual intervention needed

CLOSED/CANCELLED/EXPIRED
  -> Chain completed
```

Non introdurre logica predittiva complessa nel primo step.

---

## 13. Layout formatter

### 13.1 Compact

Sezioni:

```text
Header
Now
Plan
Story
Next
Links
```

Regole:

```text
- output breve
- niente dump JSON
- niente elenco lungo di eventi
- i link vanno in fondo
- mostrare Last update solo se esiste
- mostrare Updates linked solo se > 0
```

### 13.2 Full

Sezioni:

```text
Header
Timeline
Final
Links summary
```

Regole:

```text
- una entry timeline per evento rilevante
- ogni entry puo avere source e link
- usare summary_lines per dettagli numerici
- non mostrare campi DB nudi
```

---

## 14. File da toccare

### 14.1 `src/runtime_v2/control_plane/status_queries.py`

Da fare:

```text
- estendere dataclass TradeDetail
- aggiungere nuovi dataclass TradePlannedEntry, TradePlannedTarget, TradeStoryItem,
  TradeTimelineItem, TradeFinalResult
- espandere get_trade()
- leggere tutti gli ops_lifecycle_events della chain
- introdurre helper interni:
  _build_story_items(...)
  _build_timeline_items(...)
  _build_plan_entries(...)
  _build_plan_targets(...)
  _build_final_result(...)
  _build_next_action_label(...)
```

### 14.2 `src/runtime_v2/control_plane/formatters/trade_detail.py`

Da fare:

```text
- mantenere format_trade_detail(...) come compact
- aggiungere format_trade_detail_full(...)
- spostare qui solo rendering, non parsing JSON complesso
```

### 14.3 `src/runtime_v2/control_plane/telegram_bot.py`

Da fare:

```text
- estendere parsing /trade
- supportare [full]
- aggiornare usage
```

### 14.4 `src/runtime_v2/lifecycle/entry_gate.py`

Da fare:

```text
- nei lifecycle event trader-linked, aggiungere metadata Telegram al payload_json
- non cambiare event_type
- non cambiare chiavi payload usate dal clean_log
```

### 14.5 Test

Aree:

```text
tests/runtime_v2/control_plane/
tests/runtime_v2/lifecycle/
```

---

## 15. Strategia implementativa

Ordine raccomandato:

```text
1. estendere data model TradeDetail
2. implementare get_trade() ricco
3. aggiungere formatter compact/full
4. estendere router /trade [full]
5. persistire metadata Telegram negli update lifecycle
6. aggiungere test mirati
```

Motivo:

```text
la parte query/view model va resa solida prima del rendering
```

---

## 16. Query e pseudo-codice

### 16.1 Query chain

```sql
SELECT
  trade_chain_id,
  symbol,
  side,
  trader_id,
  account_id,
  lifecycle_state,
  entry_avg_price,
  current_stop_price,
  expected_stop_price,
  be_protection_status,
  management_plan_json,
  risk_snapshot_json,
  plan_state_json,
  source_chat_id,
  telegram_message_id,
  filled_entry_qty,
  open_position_qty,
  closed_position_qty,
  cumulative_gross_pnl,
  cumulative_fees,
  cumulative_funding,
  allocated_margin,
  created_at,
  updated_at
FROM ops_trade_chains
WHERE trade_chain_id = ?
```

### 16.2 Query timeline

```sql
SELECT
  event_id,
  event_type,
  source_type,
  source_id,
  previous_state,
  next_state,
  payload_json,
  created_at
FROM ops_lifecycle_events
WHERE trade_chain_id = ?
ORDER BY event_id ASC
```

### 16.3 Pseudo-codice get_trade

```python
row = load_chain(...)
if not row:
    return None

events = load_lifecycle_events(...)
origin_signal_link = build_signal_link(...)
planned_entries = build_plan_entries(row.plan_state_json, row.management_plan_json)
planned_targets = build_plan_targets(row.plan_state_json, row.management_plan_json)
timeline_items = build_timeline_items(events, origin_signal_link)
story_items = collapse_story_items(timeline_items)
latest_trader_message_link = find_latest_trader_link(timeline_items)
linked_update_count = count_distinct_trader_update_messages(timeline_items)
final_result = build_final_result(row, events)
closed_at = derive_closed_at(events, row.state)
next_action_label = build_next_action_label(row.state, planned_entries, planned_targets, row.be_protection_status)

return TradeDetail(...)
```

---

## 17. Test richiesti

### 17.1 Control plane

```text
- get_trade() ritorna origin_signal_link se source_chat_id+telegram_message_id esistono
- compact mostra Links: Signal
- compact mostra Last update quando esiste
- compact non mostra Last update quando assente
- full mostra timeline ordinata per event_id/tempo
- full mostra source link sugli eventi trader-linked
- full non inventa link per eventi exchange/runtime
- linked_update_count conta distinct raw_message_id
```

### 17.2 Lifecycle payload compatibility

```text
- aggiungere raw_message_id/source_chat_id/telegram_message_id ai payload update non rompe CLEAN_LOG
- UPDATE_DONE / UPDATE_PARTIAL / UPDATE_REJECTED continuano a renderizzare come prima
```

### 17.3 Argument parsing

```text
- /trade #145 -> compact
- /trade 145 -> compact
- /trade #145 full -> full
- /trade 145 full -> full
- argomento extra invalido -> rejected invalid_arguments
```

---

## 18. Fuori scope

Questa spec non include:

```text
- un comando /trade raw
- nuove tabelle dedicate per trade detail
- rewrite del CLEAN_LOG
- modifica della tassonomia lifecycle
- ricostruzione cross-DB complessa del reply thread Telegram
- aggiunta di pagination nella full timeline
```

---

## 19. Acceptance criteria

```text
1.  /trade #id mostra snapshot corrente, plan, mini-story, next, links.
2.  /trade #id full mostra timeline completa leggibile della chain.
3.  Il link del segnale origine e sempre mostrato quando source_chat_id e telegram_message_id esistono.
4.  Gli update trader mostrano un link quando il lifecycle event possiede metadata Telegram.
5.  latest_trader_message_link non punta mai a un evento exchange/runtime.
6.  linked_update_count conta i messaggi trader effettivamente collegati alla chain.
7.  La query di trade detail non dipende solo dagli ultimi 3 eventi.
8.  plan_state_json resta la fonte primaria per entries/targets pianificati.
9.  Le modifiche sono additive-only rispetto al CLEAN_LOG.
10. Nessun formatter /trade contiene logica di dominio pesante o SQL.
11. /trade <id> [full] mantiene audit e reject semantics attuali.
12. Il sistema attuale di notification CLEAN_LOG continua a funzionare senza regressioni.
```

---

## 20. Decisioni finali

Decisioni approvate da questa spec:

```text
- Mode default: compact
- Mode esteso: full
- Snapshot corrente da ops_trade_chains
- Storia da ops_lifecycle_events
- Link segnale origine da ops_trade_chains
- Link update trader da metadata persistiti nei lifecycle event payload
- Compatibilita CLEAN_LOG garantita con approccio additive-only
```

Commit message suggerito:

```text
add trade detail spec for compact/full chain history with telegram source links
```

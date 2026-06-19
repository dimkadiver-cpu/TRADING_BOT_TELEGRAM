# Design: Commands Scope, Stats, PnL, Dashboard
**Data:** 2026-06-19  
**Stato:** aggiornato post-revisione 2026-06-19 — pronto per piano di implementazione

---

## Contesto

Il sistema supporta più account exchange (`per_account`), più trader per account, e può girare su un singolo supergroup Telegram con topic distinti. I comandi attuali (`/status`, `/trades`, `/pnl`, ecc.) non filtrano per account né per trader — restituiscono dati aggregati di tutto il DB.

Questo design introduce:
1. **Scope resolution** — i comandi si scopano automaticamente in base al topic da cui vengono inviati
2. **Comandi read-only migliorati** — `/trades` con PnL snapshot, `/pnl` con realized PnL reale, `/stats` multi-periodo
3. **Comandi di emergenza con conferma** — `/close_all`, `/close`, `/cancel_all`
4. **Dashboard inline pinnable** — messaggio con keyboard inline aggiornato on-demand e a ogni cambio stato; 5 viste con paginazione
5. **Block-based rendering** — tutti i messaggi comandi usano lo stesso sistema `render_template` / `TEMPLATE_REGISTRY` di clean_log e tech_log

---

## 1. Scope Resolution

### Principio

Il `thread_id` del topic da cui arriva il comando identifica univocamente l'account (e opzionalmente il trader). Tutti i dati mostrati sono filtrati per quell'account.

### `QueryScope`

```python
@dataclass(frozen=True)
class QueryScope:
    account_id: str           # es. "demo_1"
    trader_ids: list[str] | None  # None = tutti i trader dell'account
```

### `ScopeResolver`

Costruito al boot dalla `ControlPlaneConfig`. Fa il reverse lookup:

```
thread_id 4   → QueryScope("demo_1", trader_ids=None)   # commands topic demo_1
thread_id 42  → QueryScope("demo_2", trader_ids=None)   # commands topic demo_2
```

Se `thread_id` non è riconosciuto → fallback su `default_account`, `trader_ids=None`.

Il `CommandRouter` chiama `scope_resolver.resolve(thread_id)` una volta per ogni comando e passa lo scope al service. Nessuna logica di scope altrove.

### Filtro SQL applicato

```sql
-- trader_ids=None: filtra solo per account
WHERE account_id = 'demo_1'

-- trader_ids=['trader_a','trader_b']: filtra per account + trader
WHERE account_id = 'demo_1' AND trader_id IN ('trader_a', 'trader_b')
```

### Argomento opzionale per filtrare ulteriormente

Tutti i comandi read-only accettano `[trader]` opzionale:

```
/trades trader_a    → scope.account_id="demo_1", trader_ids=["trader_a"]
/stats trader_b     → scope.account_id="demo_1", trader_ids=["trader_b"]
/pnl                → scope.account_id="demo_1", trader_ids=None (tutto l'account)
```

---

## 2. Comandi Read-Only

### `/trades [trader]`

Lista trade attivi con entry price e PnL da `ops_position_snapshots`.

**Output:**

```
📊 TRADES — demo_1 · trader_a
────────────────
Updated: 14:32:05  |  Snapshot: 18s fa

#5  📈 BTCUSDT  LONG   OPEN
    Entry: 63,500  SL: 62,800  BE: set
    Qty: 0.01  PnL: +12.40 USDT

#7  📉 ETHUSDT  SHORT  OPEN
    Entry: 2,140   SL: 2,180
    Qty: 0.5   PnL: -3.20 USDT

⚠️ Snapshot >120s — dati non aggiornati
────────────────
/trade #id  · /close <symbol>  · /cancel_all
```

**Dati:** `ops_trade_chains` (filtrato per scope) JOIN `ops_position_snapshots` per PnL unrealized.  
**Warning snapshot:** se `captured_at` del snapshot è >120s → riga `⚠️ Snapshot >120s — dati non aggiornati`.  
**Argomento:** `/trades trader_a` → filtra `trader_id` dentro l'account.

---

### `/pnl [trader]`

Combina snapshot account con PnL realizzato reale da `ops_trade_chains`.

**Output:**

```
💰 PNL — demo_1
────────────────
Account: master_account  |  14:32:05

Snapshot account:
  Equity:    10,432.50 USDT
  Balance:    9,100.00 USDT
  Margin:       820.00 USDT

Realizzato (trade chiusi):
  Gross PnL:   +234.80 USDT
  Fees:         -18.40 USDT
  Funding:       -2.10 USDT
  Netto:       +214.30 USDT

Posizioni aperte: 2  |  Waiting: 0
```

**Dati:**
- Snapshot account: `ops_account_snapshots` (ultimo per account_id)
- PnL realizzato: `SUM(cumulative_gross_pnl)`, `SUM(cumulative_fees)`, `SUM(cumulative_funding)` da `ops_trade_chains WHERE lifecycle_state='CLOSED'` filtrato per scope

---

### `/stats [trader]`

Statistiche storiche divise per 4 fasce temporali in un messaggio unico.

**Output:**

```
📈 STATS — demo_1
────────────────
           Trades  Win%   PnL netto   Fees
Oggi:          3   67%    +42.10      -3.20
7 giorni:     18   61%   +180.40     -14.50
30 giorni:    52   58%   +420.80     -38.20
Totale:       87   59%   +214.30     -62.40

Best trade:   #12 BTCUSDT +89.20 USDT
Worst trade:  #31 ETHUSDT -45.10 USDT
────────────────
/stats trader_a  per filtrare per trader
```

**Calcoli:**
- Trade contati: `lifecycle_state = 'CLOSED'` filtrati per scope e finestra temporale (`created_at`)
- Win%: trade con `cumulative_gross_pnl > 0` / totale trade × 100
- PnL netto: `SUM(cumulative_gross_pnl - cumulative_fees - cumulative_funding)`
- Fees: `SUM(cumulative_fees + cumulative_funding)`
- Best/Worst: `MAX` e `MIN` di `cumulative_gross_pnl` su tutti i trade del totale

---

## 3. Comandi di Emergenza con Conferma Inline

### Comandi

| Comando | Effetto |
|---|---|
| `/close_all` | Chiude tutte le posizioni OPEN/PARTIALLY_CLOSED dell'account |
| `/close_all trader_a` | Come sopra, solo trader_a |
| `/close BTCUSDT` | Chiude la posizione BTCUSDT dell'account |
| `/close trader_a BTCUSDT` | Chiude BTCUSDT solo su trader_a |
| `/cancel_all` | Cancella tutti gli ordini WAITING_ENTRY dell'account |
| `/cancel_all trader_a` | Come sopra, solo trader_a |

### Flusso con conferma inline

**Step 1 — Utente invia `/close_all`:**

```
🚨 CLOSE ALL — demo_1
────────────────
Posizioni da chiudere: 3

#5  📈 BTCUSDT  LONG   OPEN
#7  📉 ETHUSDT  SHORT  OPEN
#9  📈 SOLUSDT  LONG   OPEN

⚠️ Verranno inviati ordini MARKET di chiusura.

Confermi?
[✅ Conferma]  [❌ Annulla]
```

**Step 2a — Utente clicca Conferma:**

Il bot edita lo stesso messaggio:

```
🚨 CLOSE ALL — demo_1
────────────────
#5  📈 BTCUSDT  LONG
#7  📉 ETHUSDT  SHORT
#9  📈 SOLUSDT  LONG

✅ ESEGUITO — 14:32:10
3 comandi MARKET_CLOSE inseriti.
⚡ Monitorare con /trades
```

**Step 2b — Utente clicca Annulla:**

```
🚨 CLOSE ALL — demo_1
────────────────
#5  📈 BTCUSDT  LONG
#7  📉 ETHUSDT  SHORT
#9  📈 SOLUSDT  LONG

❌ ANNULLATO — 14:32:08
Nessuna azione eseguita.
```

**Step 1 — `/cancel_all`:**

```
🛑 CANCEL ALL — demo_1
────────────────
Ordini entry da cancellare: 4
  #2  NEARUSDT  LONG   WAITING_ENTRY
  #4  ZECUSDT   SHORT  WAITING_ENTRY
  #6  SOLUSDT   LONG   WAITING_ENTRY
  #8  BNBUSDT   SHORT  WAITING_ENTRY

Posizioni aperte non toccate: 2

Confermi la cancellazione?
[✅ Conferma]  [❌ Annulla]
```

**Step 2a — Conferma `/cancel_all`:**

```
🛑 CANCEL ALL — demo_1
────────────────
#2  NEARUSDT  LONG
#4  ZECUSDT   SHORT
#6  SOLUSDT   LONG
#8  BNBUSDT   SHORT

✅ ESEGUITO — 14:33:01
4 ordini WAITING_ENTRY cancellati.
Posizioni aperte non toccate: 2
/trades per verificare.
```

### Implementazione interna

```
CommandRouter
  → EmergencyCloseService.preview(scope, action, symbol=None)
      → query ops_trade_chains per chains nel scope
      → ritorna lista chains + pending_id
  → TelegramControlBot invia messaggio con InlineKeyboardMarkup
      callback_data = "confirm:{pending_id}" | "cancel:{pending_id}"

CallbackQueryHandler (nuovo in TelegramControlBot)
  → risolve pending_id da dict in-memoria (TTL: 5 min)
  → se "confirm": EmergencyCloseService.execute(pending_id)
      → inserisce ops_execution_commands (MARKET_CLOSE | CANCEL_ENTRY)
      → status = PENDING → gateway esistente preleva normalmente
  → query.edit_message_text(result_message)
```

**Pending actions:** dict in-memoria `{pending_id: PendingAction}` con TTL 5 minuti.  
**Scadenza — lazy deletion:** nessun timer in background. Se l'utente clicca [✅ Conferma] o [❌ Annulla] dopo la scadenza, il bot:
1. Cancella il messaggio preview (`delete_message`)
2. Risponde al callback con `"⏱ Azione scaduta — reinvia il comando."`

---

## 4. Dashboard Inline Pinnable

### Concetto

Un messaggio pinnato **per topic**, con keyboard inline, aggiornato in-place senza generare spam.  
L'utente invia `/dashboard` nel topic voluto, il bot crea il messaggio, l'utente lo pinna.  
Ogni topic ha il suo dashboard indipendente con il suo scope.

### Scope per topic

`/dashboard` è accettato da topic `clean_log` (tutti i thread, incluso fallback) e `commands`. **Non da `tech_log`.**

Il `ScopeResolver` esteso fa il reverse lookup su tutti i thread_id registrati:

| Thread | Tipo | Scope generato |
|---|---|---|
| `topics.commands.thread_id` | commands | `QueryScope(account_id, trader_ids=None)` |
| `topics.clean_log.thread_id` | clean_log fallback | `QueryScope(account_id, trader_ids=None)` |
| `topics.clean_log.per_trader[trader_a]` | clean_log trader | `QueryScope(account_id, ["trader_a"])` |

### Viste disponibili

**5 viste:** Attivi · Chiusi · Bloccati · PnL · Stats. Nessun Status, Health, Control.

- **Attivi:** trade in stati OPEN, PARTIALLY_CLOSED, WAITING_ENTRY, BE_MOVE_PENDING, PROTECTED_BE
- **Chiusi:** trade in stato CLOSED, ordinati per `closed_at` DESC
- **Bloccati:** trade in `REVIEW_REQUIRED` + `ops_execution_commands` con `status = 'FAILED'`, con motivo
- **PnL:** snapshot account + PnL realizzato da trade chiusi
- **Stats:** statistiche per fasce temporali (Oggi / 7g / 30g / Totale)

Vista di default alla creazione: `attivi:0`.

### Paginazione

Valida per le viste **Attivi**, **Chiusi**, **Bloccati**:
- ≤ 5 trade → nessuna riga paginazione
- > 5 trade → terza riga keyboard con navigazione, pagine da 5 trade

`current_view` codifica `"vista:pagina"` — es. `"attivi:0"`, `"chiusi:2"`, `"bloccati:1"`.

### Keyboard

```
[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
[← Prec]  [  Pagina 2/5  ]  [Succ →]   ← riga condizionale, solo se > 5 trade
```

- `[← Prec]` assente a pagina 0
- `[Succ →]` assente all'ultima pagina
- `[Pagina N/M]` è bottone inerte (`callback_data = "noop"`)

### Creazione

```
/dashboard
```

Inviato da thread 316 (clean_log trader_a) → bot risponde:

```
📊 DASHBOARD — demo_1 · trader_a
────────────────
Aggiornato: 14:32:05

[seleziona una vista o pinna questo messaggio]

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

Inviato da thread 4 (commands demo_1) → bot risponde:

```
📊 DASHBOARD — demo_1
────────────────
Aggiornato: 14:32:05

[seleziona una vista o pinna questo messaggio]

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

### Vista Attivi

```
📊 DASHBOARD — demo_1 · trader_a
────────────────
14:32:05  |  Snapshot: 18s fa

#5  📈 BTCUSDT   LONG   OPEN
    Entry: 63,500  SL: 62,800  BE: ✓
    PnL: +12.40 USDT

#9  📈 SOLUSDT   LONG   WAITING_ENTRY
    Entry attesa: 148.50  SL: 143.00
    PnL: —

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

### Vista Chiusi

```
✅ DASHBOARD — demo_1 · trader_a
────────────────
14:32:05

#22  📉 BNBUSDT   CLOSED  14:28:01   PnL: -12.80 USDT
#18  📈 SOLUSDT   CLOSED  13:55:20   PnL: +34.50 USDT
#15  📉 ETHUSDT   CLOSED  12:10:05   PnL: +8.20 USDT
#12  📈 BTCUSDT   CLOSED  11:40:12   PnL: +21.00 USDT
#9   📈 SOLUSDT   CLOSED  10:05:33   PnL: +5.60 USDT

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
[← Prec]  [  Pagina 1/3  ]  [Succ →]
```

**Dati:** `ops_trade_chains WHERE lifecycle_state='CLOSED'` filtrato per scope, ORDER BY `closed_at` DESC, LIMIT 5 OFFSET `pagina * 5`.

### Vista Bloccati

```
🚫 DASHBOARD — demo_1 · trader_a
────────────────
14:32:05

#7   ETHUSDT   REVIEW_REQUIRED   missing_sl
#12  SOLUSDT   EXEC_FAILED       insufficient_margin

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

**Dati:**
- `ops_trade_chains WHERE lifecycle_state='REVIEW_REQUIRED'` filtrato per scope → motivo da metadata/review_reason
- `ops_execution_commands WHERE status='FAILED'` filtrato per scope → motivo da `error_message`

### Vista PnL

```
💰 DASHBOARD — demo_1 · trader_a
────────────────
14:32:05

Account:
  Equity:    10,432.50 USDT
  Balance:    9,100.00 USDT
  Margin:       820.00 USDT

Realizzato (trader_a):
  Gross:      +142.60 USDT
  Fees:        -11.20 USDT
  Netto:      +130.00 USDT

Open: 1  |  Waiting: 1

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

### Vista Stats

```
📉 DASHBOARD — demo_1 · trader_a
────────────────
14:32:05

           Trades  Win%   Netto
Oggi:           1   100%  +18.40
7g:             6    67%  +62.10
30g:            19   63% +148.30
Tot:            31   61%  +98.20

Best:  #8  SOLUSDT  +34.50
Worst: #22 BNBUSDT  -12.80

[⚡ Attivi]  [✅ Chiusi]  [🚫 Bloccati]
[💰 PnL]    [📉 Stats]   [🔄 Refresh]
```

### Auto-refresh su cambio stato

Event-driven: nessun timer, nessun polling.

```
Lifecycle event (fill, TP, SL, close, state change) per trade X (trader_a, demo_1)
  → NotificationDispatcher (clean_log, tech_log — invariato)
  → DashboardManager.on_trade_event(account_id="demo_1", trader_id="trader_a")
      → cerca in ops_dashboard_messages tutti i dashboard il cui scope copre trader_a
          → thread 316 (scope trader_a)        → aggiorna ✓
          → thread 4   (scope tutti demo_1)    → aggiorna ✓
          → thread 318 (scope trader_b)        → non toccato ✓
      → per ogni dashboard trovato:
          → parse current_view → (view, page)
          → render fresh data per view corrente
          → edit_message_text(chat_id, message_id, text + keyboard)
          → gestisce "MessageNotModified" silenziosamente
```

**Throttle:** minimo 5 secondi tra edit successive sullo stesso messaggio. Se arriva un evento durante il cooldown, l'edit viene schedulata per dopo il cooldown — non scartata.

### `DashboardManager`

```python
class DashboardManager:
    def create(scope: QueryScope, chat_id: int, thread_id: int) -> int
        # invia messaggio iniziale (vista attivi:0), salva in ops_dashboard_messages, ritorna message_id

    def handle_callback(callback_query, callback_data: str) -> None
        # parse callback_data: "view:{name}", "page:prev", "page:next", "noop"
        # aggiorna current_view in DB, edita il messaggio + keyboard

    def on_trade_event(account_id: str, trader_id: str) -> None
        # trova i dashboard nel scope, aggiorna quelli pertinenti (rispetta throttle 5s)

    def _render_view(scope: QueryScope, view: str, page: int) -> tuple[str, InlineKeyboardMarkup]
        # chiama render_template(TEMPLATE_REGISTRY[f"dashboard_{view}"].blocks, payload)
        # costruisce keyboard con riga paginazione se necessario (soglia > 5 trade)
```

### Schema DB — `ops_dashboard_messages`

Chiave: `(chat_id, thread_id)` — un dashboard per topic. `thread_id = 0` per private bot (nessun thread).

```sql
CREATE TABLE ops_dashboard_messages (
    chat_id           INTEGER NOT NULL,
    thread_id         INTEGER NOT NULL DEFAULT 0,
    message_id        INTEGER NOT NULL,
    scope_account_id  TEXT NOT NULL,
    scope_trader_id   TEXT,        -- NULL = tutti i trader dell'account
    current_view      TEXT NOT NULL DEFAULT 'attivi:0',
    updated_at        TEXT,
    PRIMARY KEY (chat_id, thread_id)
);
```

`/dashboard` inviato nello stesso topic sovrascrive il record esistente (nuovo messaggio, vecchio abbandonato).

---

## 5. File Toccati

### Nuovi

| File | Contenuto |
|---|---|
| `control_plane/scope_resolver.py` | `QueryScope`, `ScopeResolver` |
| `control_plane/emergency_close_service.py` | `EmergencyCloseService`, `CloseResult` |
| `control_plane/dashboard_manager.py` | `DashboardManager` |
| `control_plane/formatters/stats.py` | `format_stats()` |
| `control_plane/formatters/templates/commands.py` | `TEMPLATE_REGISTRY` per tutti i comandi |

### Modificati

| File | Modifica |
|---|---|
| `control_plane/models.py` | + `QueryScope`, `CloseResult`, `PendingAction` |
| `control_plane/auth.py` | + whitelist `/dashboard` per topic `clean_log` (tutti i thread, incluso fallback) |
| `control_plane/telegram_bot.py` | + `CallbackQueryHandler`, pending dict (TTL 5min, lazy deletion), `DashboardManager` integration |
| `control_plane/status_queries.py` | + `scope: QueryScope` su `get_status()`, `get_control()`, `get_open_trades()`, `get_pnl()`; + `get_stats()`, `get_closed_trades()`, `get_blocked_trades()`; + unrealized PnL in `get_open_trades()`; `/health` rimane globale |
| `control_plane/service.py` | + `get_stats()`, `close_all()`, `close_symbol()`, `cancel_all()` |
| `control_plane/formatters/trades.py` | + entry price, PnL, snapshot age warning; refactor → render_template |
| `control_plane/formatters/pnl.py` | + PnL realizzato da cumulative_gross_pnl; refactor → render_template |
| `control_plane/formatters/status.py` | refactor → render_template; + account scope in header |
| `control_plane/formatters/control.py` | refactor → render_template; + account scope in header |
| `control_plane/formatters/_blocks.py` | + `TableBlock`; `SectionBlock.label` esteso a `str \| Callable[[dict], str]` |

### Schema DB

| Tabella | Modifica |
|---|---|
| `ops_dashboard_messages` | nuova — traccia message_id dashboard per account |

---

## 6. `/help` Aggiornato

```
COMANDI DISPONIBILI
────────────────
Informativi:
/status              - salute bot e conteggi
/trades [trader]     - trade aperti con PnL snapshot
/trade #id           - dettaglio singola chain
/stats [trader]      - statistiche oggi/7d/30d/totale
/pnl [trader]        - PnL realizzato + snapshot account
/health              - stato workers
/control             - blocchi operativi
/reviews             - casi da controllare
/logs [n]            - ultime N righe log (default: 20)
/debug_on [dur] / /debug_off
/version             - versione runtime
/dashboard           - crea dashboard inline pinnabile
/help                - questo messaggio

Controllo:
/pause [trader]
/resume [trader]
/start
/block <symbol>
/block <trader> <symbol>
/unblock <symbol>
/unblock <trader> <symbol>

Emergenza (richiede conferma inline):
/close_all [trader]        - chiude tutte le posizioni
/close [trader] <symbol>   - chiude singola posizione
/cancel_all [trader]       - cancella ordini entry in attesa
```

---

## 7. Block-Based Rendering per i Comandi

### Principio

Tutti i messaggi generati dai comandi usano `render_template(blocks, payload)` — lo stesso meccanismo di clean_log e tech_log. I formatter diventano funzioni thin che:
1. Convertono la view dataclass in un dict flat (`payload`)
2. Chiamano `render_template(TEMPLATE_REGISTRY["trades"].blocks, payload)`

Il block system genera **solo testo**. La `InlineKeyboardMarkup` (conferme, dashboard) è aggiunta separatamente da `TelegramControlBot` — mai dal block system.

### Nuovo file: `templates/commands.py`

Stesso pattern di `templates/tech_log.py` e `templates/clean_log.py`:

```python
TEMPLATE_REGISTRY: dict[str, TemplateConfig] = {
    # Read-only
    "trades":                       _TRADES,
    "pnl":                          _PNL,
    "stats":                        _STATS,
    "status":                       _STATUS,
    "health":                       _HEALTH,
    "control":                      _CONTROL,
    "reviews":                      _REVIEWS,
    # Emergency — close_all
    "close_all_preview":            _CLOSE_ALL_PREVIEW,
    "close_all_result_ok":          _CLOSE_ALL_RESULT_OK,
    "close_all_result_cancelled":   _CLOSE_ALL_RESULT_CANCELLED,
    # Emergency — close singolo
    "close_single_preview":         _CLOSE_SINGLE_PREVIEW,
    "close_single_result_ok":       _CLOSE_SINGLE_RESULT_OK,
    "close_single_result_cancelled": _CLOSE_SINGLE_RESULT_CANCELLED,
    # Emergency — cancel_all
    "cancel_preview":               _CANCEL_PREVIEW,
    "cancel_result_ok":             _CANCEL_RESULT_OK,
    "cancel_result_cancelled":      _CANCEL_RESULT_CANCELLED,
    # Dashboard
    "dashboard_attivi":             _DASHBOARD_ATTIVI,
    "dashboard_chiusi":             _DASHBOARD_CHIUSI,
    "dashboard_bloccati":           _DASHBOARD_BLOCCATI,
    "dashboard_pnl":                _DASHBOARD_PNL,
    "dashboard_stats":              _DASHBOARD_STATS,
}
```

### Header per comandi: `_cmd_header()`

Stesso pattern di `_tech_header()` in tech_log — helper che ritorna blocchi, non un nuovo tipo:

```python
def _cmd_header(emoji: str, command: str) -> list:
    return [
        DerivedBlock(text_fn=lambda p, _e=emoji, _c=command:
            f"{_e} {_c} — {p['account_id']}"
            + (f" · {p['trader_id']}" if p.get("trader_id") else "")
        ),
        SeparatorBlock(),
    ]
```

Genera: `📊 TRADES — demo_1` oppure `📊 TRADES — demo_1 · trader_a`

### Estensione `SectionBlock`

`SectionBlock.label` è esteso da `str` a `str | Callable[[dict], str]`, coerente con `FieldBlock.label`. Il renderer in `_render_blocks` chiama `lbl(p)` se callable prima di appendere la riga.

### Nuovo blocco: `TableBlock`

La tabella stats (4 righe × 4 colonne allineate) richiede un nuovo primitivo in `_blocks.py`:

```python
@dataclass
class TableBlock:
    """Renders aligned columnar data.
    rows_key: payload key containing list of dicts.
    columns: list of (header_label, row_key, min_width, fmt_fn).
    """
    rows_key: str
    columns: list[tuple[str, str, int, Callable]]
    show_header: bool = True
    fallback: str = "—"
```

`TableBlock` calcola la larghezza massima per colonna e allinea con `str.rjust`/`str.ljust`.

### Formatter refactor

I formatter esistenti (`status.py`, `trades.py`, `pnl.py`, `control.py`) vengono riscritti come thin wrapper:

```python
# PRIMA (manuale)
def format_trades(view: TradesView) -> str:
    lines = ["📊 OPEN TRADES ...", ...]
    return "\n".join(lines)

# DOPO (block-based)
def format_trades(view: TradesView, scope: QueryScope) -> str:
    payload = _trades_to_payload(view, scope)
    config = TEMPLATE_REGISTRY["trades"]
    return render_template(config.blocks, payload, transform=config.payload_transform)
```

**Nota:** per tutti i template commands `payload_transform=None` — il payload viene costruito interamente dal formatter (via `_*_to_payload()`), non da una transform function. Questo è intenzionale: i comandi hanno view dataclass strutturate, non payload raw come clean_log/tech_log. La chiamata con `transform=config.payload_transform` è comunque necessaria per coerenza — se in futuro un template commands aggiunge una transform, viene chiamata automaticamente.

### Separazione testo / keyboard

```
render_template()          → str (testo del messaggio)
TelegramControlBot         → aggiunge InlineKeyboardMarkup se necessario
```

Il `TemplateConfig` non conosce keyboard. La keyboard è costruita in `telegram_bot.py` in base al tipo di comando.

---

## Note Aperte

### Step 0 — Pre-requisito bloccante (verifica prima di implementare `EmergencyCloseService`)

Verificare i command_type esistenti in `ops_execution_commands` e nel gateway:
- **`MARKET_CLOSE`** — esiste già o va aggiunto come nuovo tipo?
- **`CANCEL_ENTRY`** — esiste il meccanismo di cancellazione ordini pendenti nel lifecycle worker?

Se mancano, aggiungere come migration/estensione separata **prima** di qualsiasi altro step di implementazione.

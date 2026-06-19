# Piano 2 — Emergency Close (`/close_all`, `/close`, `/cancel_all`)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementare i tre comandi distruttivi con pattern preview → conferma inline → esecuzione. Ogni comando mostra la lista chains nel scope, aspetta conferma via InlineKeyboard, poi edita lo stesso messaggio con il risultato.

**Architecture:** `EmergencyCloseService` legge le chains da DB ed inserisce comandi in `ops_execution_commands`. `CommandRouter` mantiene `_pending: dict[str, _PendingAction]` (TTL 5 min, lazy deletion al click). `TelegramControlBot` registra un `CallbackQueryHandler` PTB. I template vivono in `EMERGENCY_REGISTRY` separato.

**Tech Stack:** Python 3.11+, SQLite, python-telegram-bot (esistenti).

---

## ⚠️ Correzioni post-revisione (2026-06-19) — BLOCCANTI

Verifica eseguita contro schema reale (`db/ops_migrations/001+002`), gateway (`execution_gateway/repositories.py`, `gateway.py`) e `lifecycle/repositories.py`. Le seguenti correzioni **sostituiscono** quanto scritto sotto dove in conflitto:

1. **`command_type` corretti.** `MARKET_CLOSE` e `CANCEL_ENTRY` **non esistono**. I tipi reali processati dal gateway sono:
   - **`CLOSE_FULL`** — chiusura posizione (qty auto-risolta dal gateway: `repositories.py` → `if cmd.command_type in {"CLOSE_FULL","CLOSE_PARTIAL"} and "qty" not in payload`).
   - **`CANCEL_PENDING_ENTRY`** — cancellazione entry pendente.
   - Aggiornare anche **il testo dei template** (`"comandi MARKET_CLOSE inseriti"` → `"comandi CLOSE_FULL inseriti"`, ecc.).

2. **NON inserire comandi con un INSERT a mano.** Lo schema reale di `ops_execution_commands` ha `idempotency_key TEXT NOT NULL UNIQUE`, `payload_json NOT NULL DEFAULT '{}'`, `updated_at NOT NULL` e **non ha** una colonna `created_by`. Un INSERT con `created_by` e senza `idempotency_key`/`updated_at` **fallisce in produzione**.
   - Riusare il path esistente: `ExecutionCommandRepository.save(cmd)` in `src/runtime_v2/lifecycle/repositories.py`, costruendo un `ExecutionCommand` (`src/runtime_v2/lifecycle/models.py`) con `idempotency_key` unico (es. `f"manual_close:{chain_id}:{token}"` / `f"cancel_entry:{chain_id}:{token}"`), `status="PENDING"`, `payload_json="{}"`.
   - `created_by` (user_id) **non** va nella tabella comandi: tracciarlo nell'audit comando (`CommandAuditStore`), come già fanno gli altri comandi.

3. **Le fixture di test devono derivare dallo schema reale**, applicando `db/ops_migrations/*.sql` su un DB temporaneo. Le fixture inventate sotto (con `created_by`, senza `idempotency_key`/`updated_at`) mascherano i bug #1 e #2: i test passano ma la produzione crasha. Da correggere.

4. **Task 0 è bloccante**, non opzionale: confermare schema e contratto payload di `CLOSE_FULL` prima di scrivere il service.

---

## Dipendenze da Piano 1

- `QueryScope` / `ScopeResolver` (Task 2 Piano 1) — necessari prima di implementare Piano 2
- `_scope_label()` (Task 8 Piano 1)
- Auth + route corretti (Task 3, 8 Piano 1)

## Global Constraints

- `callback_data` Telegram max 64 bytes — formato `"<kind>:<action>:<token8>"` (< 30 byte)
- Lazy deletion: nessun timer in background — scadenza verificata solo al click
- TTL pending: 300 secondi (5 minuti)
- `ops_execution_commands` insert: verificare schema in Task 0
- Nessun template dashboard in questo piano — Piano 3
- `display_symbol()` per simboli

---

## File Structure

**Nuovi file:**
- `src/runtime_v2/control_plane/emergency_close.py` — `EmergencyCloseService`
- `src/runtime_v2/control_plane/formatters/templates/emergency.py` — `EMERGENCY_REGISTRY`
- `tests/runtime_v2/control_plane/test_emergency_close.py`
- `tests/runtime_v2/control_plane/test_emergency_templates.py`

**File modificati:**
- `src/runtime_v2/control_plane/status_queries.py` — `get_open_for_close()`, `get_waiting_for_cancel()`
- `src/runtime_v2/control_plane/service.py` — delega a `EmergencyCloseService`
- `src/runtime_v2/control_plane/telegram_bot.py` — `_pending`, `handle_callback()`, callback handler PTB, nuovi comandi
- `src/runtime_v2/control_plane/bootstrap.py` — nessuna modifica strutturale (router già costruito)

---

## Task 0: Pre-flight schema `ops_execution_commands`

**Files:** nessuno (solo verifica)

- [ ] **Step 1: Verificare schema e comandi esistenti**

```python
# scripts/check_exec_commands.py
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
cols = [r[1] for r in conn.execute("PRAGMA table_info(ops_execution_commands)").fetchall()]
print("ops_execution_commands columns:", cols)
sample = conn.execute("SELECT DISTINCT command_type FROM ops_execution_commands LIMIT 20").fetchall()
print("command_types in DB:", sample)
conn.close()
```

Run: `python scripts/check_exec_commands.py path/to/ops.db`

Colonne reali (verificate, migration 001+002): `command_id` (PK), `trade_chain_id` (NOT NULL), `command_type`, `status`, `payload_json` (NOT NULL DEFAULT '{}'), `idempotency_key` (NOT NULL UNIQUE), `created_at` (NOT NULL), `updated_at` (NOT NULL), `adapter`, `execution_account_id`, `client_order_id`, `result_payload_json`, … **Non esiste** `created_by` né `account_id`/`trader_id` su questa tabella.

I valori `command_type` per i comandi di chiusura sono **`CLOSE_FULL`** e **`CANCEL_PENDING_ENTRY`** (verificati in `execution_gateway/repositories.py`). `MARKET_CLOSE`/`CANCEL_ENTRY` non esistono. La creazione comandi passa per `ExecutionCommandRepository.save()` (Task 2), non per un INSERT manuale.

---

## Task 1: Nuove query in `status_queries.py`

**Files:**
- Modify: `src/runtime_v2/control_plane/status_queries.py`
- Test: integrati in `test_emergency_close.py` (Task 3)

**Interfaces:**
- Produces:
  - `CloseCandidate(chain_id, symbol, side, state, trader_id, account_id)`
  - `StatusQueries.get_open_for_close(scope: QueryScope) -> list[CloseCandidate]`
  - `StatusQueries.get_waiting_for_cancel(scope: QueryScope) -> list[CloseCandidate]`

- [ ] **Step 1: Aggiungere `CloseCandidate` dataclass**

In `status_queries.py`, dopo `TradeRow`:

```python
@dataclass
class CloseCandidate:
    chain_id: int
    symbol: str
    side: str
    state: str
    trader_id: str
    account_id: str
```

- [ ] **Step 2: Aggiungere `get_open_for_close()`**

```python
_CLOSEABLE_STATES = ("OPEN", "PARTIALLY_CLOSED")

def get_open_for_close(self, scope: "QueryScope") -> list[CloseCandidate]:
    """Trade aperti chiudibili via MARKET_CLOSE (OPEN + PARTIALLY_CLOSED)."""
    where, params = _scope_where(scope)
    conn = self._connect()
    try:
        rows = conn.execute(
            f"SELECT trade_chain_id, symbol, side, lifecycle_state, trader_id, account_id "
            f"FROM ops_trade_chains "
            f"WHERE lifecycle_state IN ({','.join('?' * len(_CLOSEABLE_STATES))}) "
            f"AND {where} ORDER BY trade_chain_id",
            (*_CLOSEABLE_STATES, *params),
        ).fetchall()
    finally:
        conn.close()
    return [CloseCandidate(r[0], r[1], r[2], r[3], r[4] or "", r[5] or "") for r in rows]
```

- [ ] **Step 3: Aggiungere `get_waiting_for_cancel()`**

```python
def get_waiting_for_cancel(self, scope: "QueryScope") -> list[CloseCandidate]:
    """Ordini WAITING_ENTRY cancellabili via CANCEL_ENTRY."""
    where, params = _scope_where(scope)
    conn = self._connect()
    try:
        rows = conn.execute(
            f"SELECT trade_chain_id, symbol, side, lifecycle_state, trader_id, account_id "
            f"FROM ops_trade_chains "
            f"WHERE lifecycle_state='WAITING_ENTRY' AND {where} "
            f"ORDER BY trade_chain_id",
            params,
        ).fetchall()
    finally:
        conn.close()
    return [CloseCandidate(r[0], r[1], r[2], r[3], r[4] or "", r[5] or "") for r in rows]
```

- [ ] **Step 4: Aggiungere `get_open_count_excluding_waiting()` per `/cancel_all` preview**

```python
def get_open_count_excluding_waiting(self, scope: "QueryScope") -> int:
    """Conta trade OPEN/PARTIALLY_CLOSED per il messaggio '/cancel_all — posizioni aperte non toccate'."""
    where, params = _scope_where(scope)
    conn = self._connect()
    try:
        count = conn.execute(
            f"SELECT COUNT(*) FROM ops_trade_chains "
            f"WHERE lifecycle_state IN ('OPEN','PARTIALLY_CLOSED') AND {where}",
            params,
        ).fetchone()[0]
    finally:
        conn.close()
    return count
```

- [ ] **Step 5: Commit**

```bash
git add src/runtime_v2/control_plane/status_queries.py
git commit -m "feat: add CloseCandidate and get_open_for_close/get_waiting_for_cancel queries"
```

---

## Task 2: `EmergencyCloseService`

**Files:**
- Create: `src/runtime_v2/control_plane/emergency_close.py`

**Interfaces:**
- Consumes: `CloseCandidate` (Task 1), `QueryScope` (Piano 1 Task 2)
- Produces:
  - `EmergencyCloseService(ops_db_path: str)`
  - `.execute_close(chain_ids: list[int], created_by: str) -> int` — inserisce comandi MARKET_CLOSE, ritorna count
  - `.execute_cancel(chain_ids: list[int], created_by: str) -> int` — inserisce comandi CANCEL_ENTRY, ritorna count

- [ ] **Step 1: Creare `emergency_close.py`**

```python
# src/runtime_v2/control_plane/emergency_close.py
from __future__ import annotations

import secrets

from src.runtime_v2.lifecycle.models import ExecutionCommand
from src.runtime_v2.lifecycle.repositories import ExecutionCommandRepository

# Tipi reali processati dal gateway (verificati in execution_gateway/repositories.py).
# NON usare MARKET_CLOSE / CANCEL_ENTRY: non esistono.
_CMD_CLOSE_FULL = "CLOSE_FULL"
_CMD_CANCEL_ENTRY = "CANCEL_PENDING_ENTRY"


class EmergencyCloseService:
    """Crea comandi di chiusura/cancellazione via il repository esistente.

    Riusa ExecutionCommandRepository.save() (lifecycle/repositories.py) che
    popola idempotency_key/created_at/updated_at e usa INSERT OR IGNORE.
    NON scrive un INSERT a mano: lo schema reale richiede idempotency_key
    NOT NULL UNIQUE + updated_at NOT NULL e non ha colonna created_by.
    """

    def __init__(self, ops_db_path: str) -> None:
        self._repo = ExecutionCommandRepository(ops_db_path)

    def _save(self, chain_ids: list[int], command_type: str, prefix: str) -> int:
        count = 0
        for chain_id in chain_ids:
            cmd = ExecutionCommand(
                trade_chain_id=chain_id,
                command_type=command_type,
                status="PENDING",
                payload_json="{}",  # qty auto-risolta dal gateway per CLOSE_FULL
                idempotency_key=f"{prefix}:{chain_id}:{secrets.token_hex(4)}",
            )
            self._repo.save(cmd)
            count += 1
        return count

    def execute_close(self, chain_ids: list[int], created_by: str) -> int:
        """Crea un comando CLOSE_FULL per ogni chain_id. Ritorna count creati.

        created_by viene tracciato nell'audit comando dal router, non in questa tabella.
        """
        if not chain_ids:
            return 0
        return self._save(chain_ids, _CMD_CLOSE_FULL, "manual_close")

    def execute_cancel(self, chain_ids: list[int], created_by: str) -> int:
        """Crea un comando CANCEL_PENDING_ENTRY per ogni chain_id. Ritorna count creati."""
        if not chain_ids:
            return 0
        return self._save(chain_ids, _CMD_CANCEL_ENTRY, "cancel_entry")


__all__ = ["EmergencyCloseService"]
```

Nota: verificare in Task 0 i campi obbligatori di `ExecutionCommand` (`src/runtime_v2/lifecycle/models.py`) e il contratto payload di `CLOSE_FULL` (se richiede `qty`/`reduce_only` espliciti, aggiungerli a `payload_json`).

- [ ] **Step 2: Aggiungere `EmergencyCloseService` a `RuntimeControlService`**

In `service.py`, aggiungere import e istanza:

```python
from src.runtime_v2.control_plane.emergency_close import EmergencyCloseService
```

In `RuntimeControlService.__init__`:
```python
self._emergency = EmergencyCloseService(ops_db_path)
```

Aggiungere metodi delegate:
```python
def get_open_for_close(self, scope: QueryScope) -> list:
    return self._queries.get_open_for_close(scope)

def get_waiting_for_cancel(self, scope: QueryScope) -> list:
    return self._queries.get_waiting_for_cancel(scope)

def get_open_count_excluding_waiting(self, scope: QueryScope) -> int:
    return self._queries.get_open_count_excluding_waiting(scope)

def execute_close(self, chain_ids: list[int], created_by: str) -> int:
    return self._emergency.execute_close(chain_ids, created_by)

def execute_cancel(self, chain_ids: list[int], created_by: str) -> int:
    return self._emergency.execute_cancel(chain_ids, created_by)
```

- [ ] **Step 3: Commit**

```bash
git add src/runtime_v2/control_plane/emergency_close.py \
        src/runtime_v2/control_plane/service.py
git commit -m "feat: add EmergencyCloseService and delegate methods on RuntimeControlService"
```

---

## Task 3: Test `EmergencyCloseService`

**Files:**
- Test: `tests/runtime_v2/control_plane/test_emergency_close.py`

- [ ] **Step 1: Scrivere i test**

> ⚠️ La fixture sotto deve usare lo **schema reale** applicando le migration
> (`db/ops_migrations/*.sql`) su un DB temporaneo — NON ridichiarare a mano
> `ops_execution_commands` con `created_by`/senza `idempotency_key`, altrimenti i
> test passano ma la produzione crasha. `ops_trade_chains` qui ha solo le colonne
> realmente presenti (niente `review_reason`; `closed_at` non esiste → fallback `updated_at`).

```python
from __future__ import annotations
import os, sqlite3, tempfile, glob
from src.runtime_v2.control_plane.emergency_close import EmergencyCloseService
from src.runtime_v2.control_plane.status_queries import StatusQueries
from src.runtime_v2.control_plane.scope_resolver import QueryScope


def _make_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    # Applica le migration reali: ops_trade_chains + ops_execution_commands con lo
    # schema autentico (idempotency_key NOT NULL UNIQUE, updated_at NOT NULL, niente created_by).
    for sql_file in sorted(glob.glob("db/ops_migrations/*.sql")):
        with open(sql_file, encoding="utf-8") as f:
            conn.executescript(f.read())
    # INSERT con colonne esplicite: ops_trade_chains reale ha molte colonne NOT NULL
    # (source_enrichment_id UNIQUE, canonical_message_id, raw_message_id, entry_mode, ...).
    now = "2026-06-19T10:00:00+00:00"
    def _chain(cid, symbol, side, trader, state):
        conn.execute(
            "INSERT INTO ops_trade_chains "
            "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
            " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, cid, cid, cid, trader, "demo_1", symbol, side, state, "limit", now, now),
        )
    _chain(1, "BTCUSDT", "LONG", "trader_a", "OPEN")
    _chain(2, "ETHUSDT", "SHORT", "trader_a", "PARTIALLY_CLOSED")
    _chain(3, "SOLUSDT", "LONG", "trader_a", "WAITING_ENTRY")
    _chain(4, "BNBUSDT", "SHORT", "trader_b", "OPEN")
    conn.commit()
    conn.close()
    return path


def test_get_open_for_close_returns_open_and_partially():
    db = _make_db()
    q = StatusQueries(db)
    scope = QueryScope(account_id="demo_1", trader_ids=None)
    result = q.get_open_for_close(scope)
    ids = {r.chain_id for r in result}
    assert 1 in ids  # OPEN
    assert 2 in ids  # PARTIALLY_CLOSED
    assert 3 not in ids  # WAITING_ENTRY escluso
    os.unlink(db)


def test_get_open_for_close_filters_by_trader():
    db = _make_db()
    q = StatusQueries(db)
    scope = QueryScope(account_id="demo_1", trader_ids=["trader_a"])
    result = q.get_open_for_close(scope)
    ids = {r.chain_id for r in result}
    assert 4 not in ids  # trader_b escluso
    os.unlink(db)


def test_get_waiting_for_cancel():
    db = _make_db()
    q = StatusQueries(db)
    scope = QueryScope(account_id="demo_1", trader_ids=None)
    result = q.get_waiting_for_cancel(scope)
    assert len(result) == 1
    assert result[0].chain_id == 3
    os.unlink(db)


def test_execute_close_inserts_close_full():
    db = _make_db()
    svc = EmergencyCloseService(db)
    count = svc.execute_close([1, 2], created_by="42")
    assert count == 2
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT command_type, trade_chain_id FROM ops_execution_commands ORDER BY command_id").fetchall()
    conn.close()
    assert rows == [("CLOSE_FULL", 1), ("CLOSE_FULL", 2)]
    os.unlink(db)


def test_execute_cancel_inserts_cancel_pending_entry():
    db = _make_db()
    svc = EmergencyCloseService(db)
    count = svc.execute_cancel([3], created_by="42")
    assert count == 1
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT command_type FROM ops_execution_commands").fetchall()
    conn.close()
    assert rows == [("CANCEL_PENDING_ENTRY",)]
    os.unlink(db)


def test_execute_close_empty_is_noop():
    db = _make_db()
    svc = EmergencyCloseService(db)
    count = svc.execute_close([], created_by="42")
    assert count == 0
    os.unlink(db)
```

Run: `pytest tests/runtime_v2/control_plane/test_emergency_close.py -v`
Expected: PASS

- [ ] **Step 2: Commit**

```bash
git add tests/runtime_v2/control_plane/test_emergency_close.py
git commit -m "test: add integration tests for EmergencyCloseService and new close queries"
```

---

## Task 4: `templates/emergency.py` — EMERGENCY_REGISTRY

**Files:**
- Create: `src/runtime_v2/control_plane/formatters/templates/emergency.py`
- Test: `tests/runtime_v2/control_plane/test_emergency_templates.py`

**Interfaces:**
- Produces: `EMERGENCY_REGISTRY: dict[str, TemplateConfig]` con chiavi:
  - `"close_all_preview"`, `"close_all_result_ok"`, `"close_all_result_cancelled"`
  - `"close_single_preview"`, `"close_single_result_ok"`, `"close_single_result_cancelled"`
  - `"cancel_all_preview"`, `"cancel_all_result_ok"`, `"cancel_all_result_cancelled"`

Tutti `payload_transform=None`.

- [ ] **Step 1: Creare `templates/emergency.py`**

```python
# src/runtime_v2/control_plane/formatters/templates/emergency.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    BranchBlock, DerivedBlock, ListBlock,
    SeparatorBlock, StaticBlock, TemplateConfig,
)


def _chain_renderer_with_state(c: dict, i: int, p: dict) -> list[str]:
    side_emoji = "📈" if c["side"] == "LONG" else "📉"
    return [f"#{c['chain_id']}  {side_emoji} {c['symbol']}  {c['side']}  {c['state']}"]


def _chain_renderer_compact(c: dict, i: int, p: dict) -> list[str]:
    side_emoji = "📈" if c["side"] == "LONG" else "📉"
    return [f"#{c['chain_id']}  {side_emoji} {c['symbol']}  {c['side']}"]


# ── /close_all ───────────────────────────────────────────────────────────────

_CLOSE_ALL_PREVIEW = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🚨 CLOSE ALL — {p['scope_label']}"),
    SeparatorBlock(),
    BranchBlock(
        condition=lambda p: p["total"] == 0,
        then_blocks=[StaticBlock("Nessuna posizione aperta da chiudere.")],
        else_blocks=[
            DerivedBlock(text_fn=lambda p: f"Posizioni da chiudere: {p['total']}"),
            SeparatorBlock(),
            ListBlock(key="chains", item_renderer=_chain_renderer_with_state),
            SeparatorBlock(),
            StaticBlock("⚠️ Verranno inviati ordini MARKET di chiusura."),
            SeparatorBlock(),
            StaticBlock("Confermi?"),
        ],
    ),
])

_CLOSE_ALL_RESULT_OK = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🚨 CLOSE ALL — {p['scope_label']}"),
    SeparatorBlock(),
    ListBlock(key="chains", item_renderer=_chain_renderer_compact),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"✅ ESEGUITO — {p['executed_at']}"),
    DerivedBlock(text_fn=lambda p: f"{p['count']} comandi CLOSE_FULL inseriti."),
    StaticBlock("⚡ Monitorare con /trades"),
])

_CLOSE_ALL_RESULT_CANCELLED = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🚨 CLOSE ALL — {p['scope_label']}"),
    SeparatorBlock(),
    ListBlock(key="chains", item_renderer=_chain_renderer_compact),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"❌ ANNULLATO — {p['cancelled_at']}"),
    StaticBlock("Nessuna azione eseguita."),
])


# ── /close ───────────────────────────────────────────────────────────────────

def _close_single_preview_chain(c: dict, i: int, p: dict) -> list[str]:
    side_emoji = "📈" if c["side"] == "LONG" else "📉"
    lines = [f"#{c['chain_id']}  {side_emoji} {c['symbol']}  {c['side']}  {c['state']}"]
    if c.get("entry_price"):
        lines.append(f"    Entry: {c['entry_price']}  |  PnL: {c.get('pnl', 'n/a')}")
    return lines


_CLOSE_SINGLE_PREVIEW = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🚨 CLOSE — {p['scope_label']}"),
    SeparatorBlock(),
    BranchBlock(
        condition=lambda p: p["total"] == 0,
        then_blocks=[DerivedBlock(text_fn=lambda p: f"{p['symbol']}: nessuna posizione aperta trovata.")],
        else_blocks=[
            BranchBlock(
                condition=lambda p: p["total"] == 1,
                then_blocks=[StaticBlock("Posizione da chiudere:")],
                else_blocks=[DerivedBlock(text_fn=lambda p: f"Trovate {p['total']} posizioni su {p['symbol']}:")],
            ),
            SeparatorBlock(),
            ListBlock(key="chains", item_renderer=_close_single_preview_chain),
            SeparatorBlock(),
            StaticBlock("⚠️ Verrà inviato un ordine MARKET di chiusura."),
            SeparatorBlock(),
            StaticBlock("Confermi?"),
        ],
    ),
])

_CLOSE_SINGLE_RESULT_OK = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🚨 CLOSE — {p['scope_label']}"),
    SeparatorBlock(),
    ListBlock(key="chains", item_renderer=_chain_renderer_compact),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"✅ ESEGUITO — {p['executed_at']}"),
    DerivedBlock(text_fn=lambda p: f"{p['count']} {'comando' if p['count'] == 1 else 'comandi'} CLOSE_FULL inserito."),
    DerivedBlock(text_fn=lambda p: f"⚡ Monitorare con {'  /trade #' + str(p['chains'][0]['chain_id']) if p['count'] == 1 else '/trades'}"),
])

_CLOSE_SINGLE_RESULT_CANCELLED = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🚨 CLOSE — {p['scope_label']}"),
    SeparatorBlock(),
    ListBlock(key="chains", item_renderer=_chain_renderer_compact),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"❌ ANNULLATO — {p['cancelled_at']}"),
])


# ── /cancel_all ──────────────────────────────────────────────────────────────

def _waiting_renderer_with_state(c: dict, i: int, p: dict) -> list[str]:
    side_emoji = "📈" if c["side"] == "LONG" else "📉"
    return [f"#{c['chain_id']}  {side_emoji} {c['symbol']}  {c['side']}  WAITING_ENTRY"]


def _waiting_renderer_compact(c: dict, i: int, p: dict) -> list[str]:
    side_emoji = "📈" if c["side"] == "LONG" else "📉"
    return [f"#{c['chain_id']}  {c['symbol']}  {c['side']}"]


_CANCEL_ALL_PREVIEW = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🛑 CANCEL ALL — {p['scope_label']}"),
    SeparatorBlock(),
    BranchBlock(
        condition=lambda p: p["total"] == 0,
        then_blocks=[StaticBlock("Nessun ordine WAITING_ENTRY da cancellare.")],
        else_blocks=[
            DerivedBlock(text_fn=lambda p: f"Ordini entry in attesa: {p['total']}"),
            SeparatorBlock(),
            ListBlock(key="chains", item_renderer=_waiting_renderer_with_state),
            SeparatorBlock(),
            DerivedBlock(text_fn=lambda p: f"Posizioni aperte non toccate: {p['open_count']}"),
            SeparatorBlock(),
            StaticBlock("Confermi la cancellazione?"),
        ],
    ),
])

_CANCEL_ALL_RESULT_OK = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🛑 CANCEL ALL — {p['scope_label']}"),
    SeparatorBlock(),
    ListBlock(key="chains", item_renderer=_waiting_renderer_compact),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"✅ ESEGUITO — {p['executed_at']}"),
    DerivedBlock(text_fn=lambda p: f"{p['count']} ordini WAITING_ENTRY cancellati."),
    DerivedBlock(text_fn=lambda p: f"Posizioni aperte non toccate: {p['open_count']}"),
    StaticBlock("/trades per verificare."),
])

_CANCEL_ALL_RESULT_CANCELLED = TemplateConfig(blocks=[
    DerivedBlock(text_fn=lambda p: f"🛑 CANCEL ALL — {p['scope_label']}"),
    SeparatorBlock(),
    ListBlock(key="chains", item_renderer=_waiting_renderer_compact),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"❌ ANNULLATO — {p['cancelled_at']}"),
])


EMERGENCY_REGISTRY: dict[str, TemplateConfig] = {
    "close_all_preview": _CLOSE_ALL_PREVIEW,
    "close_all_result_ok": _CLOSE_ALL_RESULT_OK,
    "close_all_result_cancelled": _CLOSE_ALL_RESULT_CANCELLED,
    "close_single_preview": _CLOSE_SINGLE_PREVIEW,
    "close_single_result_ok": _CLOSE_SINGLE_RESULT_OK,
    "close_single_result_cancelled": _CLOSE_SINGLE_RESULT_CANCELLED,
    "cancel_all_preview": _CANCEL_ALL_PREVIEW,
    "cancel_all_result_ok": _CANCEL_ALL_RESULT_OK,
    "cancel_all_result_cancelled": _CANCEL_ALL_RESULT_CANCELLED,
}

__all__ = ["EMERGENCY_REGISTRY"]
```

- [ ] **Step 2: Scrivere smoke test**

Creare `tests/runtime_v2/control_plane/test_emergency_templates.py`:

```python
from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.templates.emergency import EMERGENCY_REGISTRY
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _chains_3():
    return [
        {"chain_id": 5, "symbol": "BTC/USDT", "side": "LONG", "state": "OPEN", "entry_price": "63,500", "pnl": "+12.40 USDT"},
        {"chain_id": 7, "symbol": "ETH/USDT", "side": "SHORT", "state": "OPEN", "entry_price": None, "pnl": None},
        {"chain_id": 9, "symbol": "SOL/USDT", "side": "LONG", "state": "PARTIALLY_CLOSED", "entry_price": "148.50", "pnl": "+5.00 USDT"},
    ]


def test_close_all_preview_with_chains():
    cfg = EMERGENCY_REGISTRY["close_all_preview"]
    payload = {"scope_label": "demo_1", "total": 3, "chains": _chains_3()}
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "CLOSE ALL — demo_1" in result
    assert "Posizioni da chiudere: 3" in result
    assert "#5" in result
    assert "Confermi?" in result


def test_close_all_preview_empty():
    cfg = EMERGENCY_REGISTRY["close_all_preview"]
    payload = {"scope_label": "demo_1", "total": 0, "chains": []}
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "Nessuna posizione aperta" in result
    assert "Confermi?" not in result


def test_close_all_result_ok():
    cfg = EMERGENCY_REGISTRY["close_all_result_ok"]
    payload = {"scope_label": "demo_1", "chains": _chains_3(), "count": 3, "executed_at": _now()}
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "✅ ESEGUITO" in result
    assert "3 comandi MARKET_CLOSE" in result


def test_cancel_all_preview_with_waiting():
    cfg = EMERGENCY_REGISTRY["cancel_all_preview"]
    waiting = [{"chain_id": 2, "symbol": "NEAR/USDT", "side": "LONG", "state": "WAITING_ENTRY"}]
    payload = {"scope_label": "demo_1", "total": 1, "chains": waiting, "open_count": 2}
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "Ordini entry in attesa: 1" in result
    assert "Posizioni aperte non toccate: 2" in result
    assert "Confermi" in result


def test_close_single_preview_not_found():
    cfg = EMERGENCY_REGISTRY["close_single_preview"]
    payload = {"scope_label": "demo_1", "total": 0, "chains": [], "symbol": "XYZUSDT"}
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "XYZUSDT: nessuna posizione aperta" in result
```

Run: `pytest tests/runtime_v2/control_plane/test_emergency_templates.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/templates/emergency.py \
        tests/runtime_v2/control_plane/test_emergency_templates.py
git commit -m "feat: add EMERGENCY_REGISTRY templates for close_all, close_single, cancel_all"
```

---

## Task 5: `telegram_bot.py` — pending dict + callback handler + nuovi comandi

**Files:**
- Modify: `src/runtime_v2/control_plane/telegram_bot.py`

**Interfaces:**
- Produces:
  - `CommandRouter._pending: dict[str, _PendingAction]`
  - `CommandRouter.handle_callback(token, action, user_id, chat_id, message_id, thread_id, created_by) -> CallbackResult`
  - `TelegramControlBot._on_callback_query()` — PTB handler
  - `/close_all`, `/close`, `/cancel_all` in `_dispatch()`

- [ ] **Step 1: Aggiungere dataclass e import**

Aggiungere al top di `telegram_bot.py`:

```python
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.templates.emergency import EMERGENCY_REGISTRY
from src.runtime_v2.control_plane.status_queries import CloseCandidate
```

Aggiungere dataclass:

```python
_PENDING_TTL = 300  # 5 minuti

@dataclass
class _PendingAction:
    kind: Literal["close_all", "close_single", "cancel_all"]
    scope: "QueryScope"
    chain_ids: list[int]
    chains_payload: list[dict]  # già formattati per il template di risultato
    scope_label: str
    open_count: int  # per cancel_all — posizioni non toccate
    created_at: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        return time.time() - self.created_at > _PENDING_TTL


@dataclass
class CallbackResult:
    reply_text: str
    delete_message: bool = False  # True se pending scaduto
    answer_text: str = ""         # testo per answerCallbackQuery
```

- [ ] **Step 2: Aggiornare `CommandRouter.__init__`**

Aggiungere in `__init__`:
```python
self._pending: dict[str, _PendingAction] = {}
```

- [ ] **Step 3: Aggiungere helper `_make_token` e `_build_keyboard`**

```python
def _make_token() -> str:
    return secrets.token_hex(4)  # 8 caratteri hex, < 64 byte con il prefisso


def _emergency_keyboard(kind: str, token: str):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Conferma", callback_data=f"{kind}:confirm:{token}"),
        InlineKeyboardButton("❌ Annulla", callback_data=f"{kind}:cancel:{token}"),
    ]])
```

- [ ] **Step 4: Aggiungere `handle_callback()` a `CommandRouter`**

```python
def handle_callback(
    self,
    *,
    callback_data: str,
    user_id: int,
    chat_id: int,
    message_id: int,
    thread_id: int | None,
    created_by: str,
) -> CallbackResult:
    parts = callback_data.split(":", 2)
    if len(parts) != 3:
        return CallbackResult("Callback non valido.", answer_text="⚠️ Callback non valido")
    kind, action, token = parts
    if action not in ("confirm", "cancel"):
        return CallbackResult("Azione non valida.", answer_text="⚠️")

    pending = self._pending.get(token)
    if pending is None:
        return CallbackResult("", delete_message=False, answer_text="⏱ Azione scaduta — reinvia il comando.")

    if pending.is_expired():
        del self._pending[token]
        return CallbackResult("", delete_message=True, answer_text="⏱ Azione scaduta — reinvia il comando.")

    del self._pending[token]
    now = _now_hms()

    if action == "cancel":
        result_key = f"{kind}_result_cancelled"
        cfg = EMERGENCY_REGISTRY[result_key]
        payload = {
            "scope_label": pending.scope_label,
            "chains": pending.chains_payload,
            "cancelled_at": now,
            "count": len(pending.chain_ids),
            "open_count": pending.open_count,
        }
        text = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
        return CallbackResult(text, answer_text="❌ Annullato")

    # confirm
    if kind == "close_all":
        count = self._service.execute_close(pending.chain_ids, created_by=created_by)
        cfg = EMERGENCY_REGISTRY["close_all_result_ok"]
        payload = {"scope_label": pending.scope_label, "chains": pending.chains_payload, "count": count, "executed_at": now}
    elif kind == "close_single":
        count = self._service.execute_close(pending.chain_ids, created_by=created_by)
        cfg = EMERGENCY_REGISTRY["close_single_result_ok"]
        payload = {"scope_label": pending.scope_label, "chains": pending.chains_payload, "count": count, "executed_at": now}
    elif kind == "cancel_all":
        count = self._service.execute_cancel(pending.chain_ids, created_by=created_by)
        cfg = EMERGENCY_REGISTRY["cancel_all_result_ok"]
        payload = {
            "scope_label": pending.scope_label,
            "chains": pending.chains_payload,
            "count": count,
            "executed_at": now,
            "open_count": pending.open_count,
        }
    else:
        return CallbackResult("Tipo non valido.", answer_text="⚠️")

    text = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    return CallbackResult(text, answer_text="✅ Eseguito")
```

Helper `_now_hms`:
```python
def _now_hms() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%H:%M:%S")
```

- [ ] **Step 5: Aggiungere `/close_all`, `/close`, `/cancel_all` in `_dispatch()`**

Aggiungere dopo il comando `/reviews` esistente in `_dispatch()`:

```python
if command_name == "close_all":
    # optional: /close_all [trader]
    trader_override = args[0] if args else None
    effective_scope = _override_trader(scope, trader_override)
    candidates = self._service.get_open_for_close(effective_scope)
    sl = _scope_label(effective_scope)
    chains_payload = _candidates_to_payload(candidates)
    cfg = EMERGENCY_REGISTRY["close_all_preview"]
    payload = {"scope_label": sl, "total": len(candidates), "chains": chains_payload}
    text = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    if not candidates:
        return _DispatchResult(text)
    token = _make_token()
    self._pending[token] = _PendingAction(
        kind="close_all", scope=effective_scope,
        chain_ids=[c.chain_id for c in candidates],
        chains_payload=chains_payload, scope_label=sl, open_count=0,
    )
    return _DispatchResult(text, keyboard=_emergency_keyboard("close_all", token))

if command_name == "close":
    # /close [trader] <symbol>
    trader_arg, symbol_arg = _parse_scope_symbol(args)
    if not symbol_arg:
        return _DispatchResult("Usage: /close <symbol>  o  /close <trader> <symbol>", decision="REJECTED", reject_reason="invalid_arguments")
    effective_scope = _override_trader(scope, trader_arg)
    candidates = [c for c in self._service.get_open_for_close(effective_scope)
                  if c.symbol.upper() == symbol_arg.upper()]
    sl = _scope_label(effective_scope)
    chains_payload = _candidates_to_payload(candidates)
    cfg = EMERGENCY_REGISTRY["close_single_preview"]
    payload = {"scope_label": sl, "total": len(candidates), "chains": chains_payload, "symbol": symbol_arg.upper()}
    text = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    if not candidates:
        return _DispatchResult(text)
    token = _make_token()
    self._pending[token] = _PendingAction(
        kind="close_single", scope=effective_scope,
        chain_ids=[c.chain_id for c in candidates],
        chains_payload=chains_payload, scope_label=sl, open_count=0,
    )
    return _DispatchResult(text, keyboard=_emergency_keyboard("close_single", token))

if command_name == "cancel_all":
    trader_override = args[0] if args else None
    effective_scope = _override_trader(scope, trader_override)
    candidates = self._service.get_waiting_for_cancel(effective_scope)
    open_count = self._service.get_open_count_excluding_waiting(effective_scope)
    sl = _scope_label(effective_scope)
    chains_payload = _candidates_to_payload(candidates)
    cfg = EMERGENCY_REGISTRY["cancel_all_preview"]
    payload = {"scope_label": sl, "total": len(candidates), "chains": chains_payload, "open_count": open_count}
    text = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    if not candidates:
        return _DispatchResult(text)
    token = _make_token()
    self._pending[token] = _PendingAction(
        kind="cancel_all", scope=effective_scope,
        chain_ids=[c.chain_id for c in candidates],
        chains_payload=chains_payload, scope_label=sl, open_count=open_count,
    )
    return _DispatchResult(text, keyboard=_emergency_keyboard("cancel_all", token))
```

Helper locali da aggiungere nel modulo:

```python
def _candidates_to_payload(candidates: list["CloseCandidate"]) -> list[dict]:
    from src.runtime_v2.control_plane.formatters.display import display_symbol
    return [
        {"chain_id": c.chain_id, "symbol": display_symbol(c.symbol),
         "side": c.side, "state": c.state, "entry_price": None, "pnl": None}
        for c in candidates
    ]


def _override_trader(scope: "QueryScope", trader_arg: str | None) -> "QueryScope":
    """Se trader_arg specificato, restringe lo scope a quel trader."""
    from src.runtime_v2.control_plane.scope_resolver import QueryScope
    if trader_arg:
        return QueryScope(account_id=scope.account_id, trader_ids=[trader_arg])
    return scope
```

- [ ] **Step 6: Aggiungere `keyboard` a `_DispatchResult`**

```python
@dataclass(frozen=True)
class _DispatchResult:
    reply_text: str
    decision: str = "EXECUTED"
    reject_reason: str | None = None
    keyboard: object | None = None  # InlineKeyboardMarkup | None
```

- [ ] **Step 7: Aggiungere `CallbackQueryHandler` in `TelegramControlBot._build_app()`**

```python
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

def _build_app(self):
    app = Application.builder().token(self._config.token).build()
    app.add_handler(MessageHandler(filters.COMMAND, self._on_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text_message))
    app.add_handler(CallbackQueryHandler(self._on_callback_query))
    return app
```

- [ ] **Step 8: Implementare `_on_callback_query()`**

```python
async def _on_callback_query(self, update, context) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return

    await query.answer()  # pre-risposta immediata per togliere lo spinner

    result = self._router.handle_callback(
        callback_data=query.data or "",
        user_id=user.id,
        chat_id=query.message.chat_id,
        message_id=query.message.message_id,
        thread_id=getattr(query.message, "message_thread_id", None),
        created_by=str(user.id),
    )

    if result.answer_text:
        await query.answer(result.answer_text)

    if result.delete_message:
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    if result.reply_text:
        try:
            await query.message.edit_text(result.reply_text)
        except Exception:
            pass
```

- [ ] **Step 9: Aggiornare `_on_command` per inviare `InlineKeyboardMarkup` se presente**

Nel metodo `_on_command`, dopo `result = self._router.route(...)`:

```python
send_kwargs: dict[str, object] = {
    "chat_id": message.chat_id,
    "text": result.reply_text,
}
if thread_id is not None:
    send_kwargs["message_thread_id"] = thread_id
# Aggiungi inline keyboard se presente nel DispatchResult
if hasattr(result, "keyboard") and result.keyboard is not None:
    send_kwargs["reply_markup"] = result.keyboard
await context.bot.send_message(**send_kwargs)
```

Nota: `RouteResult` attualmente non porta `keyboard`. Aggiungere il campo:

```python
@dataclass
class RouteResult:
    decision: str
    reply_text: str | None
    keyboard: object | None = None  # InlineKeyboardMarkup | None
```

E propagare `keyboard` da `_DispatchResult` a `RouteResult` nel metodo `route()`.

- [ ] **Step 10: Aggiornare `_READONLY_COMMANDS` / `_CONTROL_COMMANDS`**

```python
_READONLY_COMMANDS = frozenset(
    {"help", "status", "trades", "trade", "health", "control", "reviews",
     "version", "stats", "dashboard"}
)
_CONTROL_COMMANDS = frozenset({"pause", "resume", "start", "block", "unblock"})
_EMERGENCY_COMMANDS = frozenset({"close_all", "close", "cancel_all"})
_ADVANCED_COMMANDS = frozenset({"pnl", "logs", "debug_on", "debug_off"})
_ALLOWED_COMMANDS = _READONLY_COMMANDS | _CONTROL_COMMANDS | _EMERGENCY_COMMANDS | _ADVANCED_COMMANDS
```

- [ ] **Step 11: Eseguire la suite completa**

Run: `pytest tests/runtime_v2/control_plane/ -v --tb=short`
Expected: tutti PASS

- [ ] **Step 12: Commit finale Piano 2**

```bash
git add src/runtime_v2/control_plane/telegram_bot.py
git commit -m "feat: add emergency close commands with inline keyboard confirm/cancel flow"
```

---

## Self-Review Piano 2

| Requisito spec | Task |
|---|---|
| Preview CLOSE ALL con chains | Task 4 + 5 |
| Preview CLOSE con filtro symbol | Task 4 + 5 |
| Preview CANCEL ALL con open_count | Task 4 + 5 |
| `[✅ Conferma] [❌ Annulla]` keyboard | Task 5 |
| Edit messaggio con risultato | Task 5 |
| Lazy deletion TTL 5min | Task 5 (`is_expired()`) |
| Scope da args `[trader]` | Task 5 (`_override_trader`) |
| Template RESULT_OK / RESULT_CANCELLED | Task 4 |
| Nessun timer background | Task 5 (lazy check solo al click) |

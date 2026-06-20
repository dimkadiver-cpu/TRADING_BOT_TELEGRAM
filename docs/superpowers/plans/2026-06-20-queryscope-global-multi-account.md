# QueryScope Global Multi-Account — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Estendere `QueryScope` con `account_id: str | None` in modo che il topic commands diventi scope globale (tutti gli account e tutti i trader) — senza modificare config YAML, formatter, template o schema DB.

**Architecture:** Una modifica al dataclass (`account_id: str | None`), un ramo aggiuntivo in `_scope_where()` che genera `WHERE 1=1` quando `account_id=None`, e il commands thread mappato a `QueryScope(None, None)` in `ScopeResolver`. `DashboardManager` persiste e ricostruisce il scope globale dal DB (`scope_account_id = NULL`).

**Tech Stack:** Python 3.12, SQLite (sqlite3), pytest

## Global Constraints

- `account_id=None` significa sempre scope globale — nessun filtro per account né trader
- Nessuna modifica a `telegram_control.yaml`, formatter, template, `telegram_bot.py`
- Nessuna migration DB — `scope_account_id` in `ops_dashboard_messages` è già `TEXT` senza `NOT NULL`
- Baseline: 55 test passano — non rompere nulla
- Runner test: `python -m pytest <path> -v`

---

## File Map

| File | Ruolo |
|------|-------|
| `src/runtime_v2/control_plane/scope_resolver.py` | Modifica: `QueryScope.account_id: str → str | None`; commands thread → `QueryScope(None, None)` |
| `src/runtime_v2/control_plane/status_queries.py` | Modifica: `_scope_where()` aggiunge ramo `account_id=None` |
| `src/runtime_v2/control_plane/dashboard_manager.py` | Modifica: `_matches_scope()`, `_save_dashboard()`, ricostruzione scope dal DB |
| `tests/runtime_v2/control_plane/test_scope_resolver.py` | Modifica: aggiorna assert dei comandi thread; aggiunge test global scope |
| `tests/runtime_v2/control_plane/test_status_queries_scoped.py` | Modifica: aggiunge test `_scope_where` con `account_id=None` |
| `tests/runtime_v2/control_plane/test_dashboard_manager.py` | Modifica: `_make_db` rende nullable `scope_account_id`; aggiunge test global scope |

---

## Task 1: `QueryScope` + `ScopeResolver` — scope globale per commands

**Files:**
- Modify: `src/runtime_v2/control_plane/scope_resolver.py`
- Test: `tests/runtime_v2/control_plane/test_scope_resolver.py`

**Interfaces:**
- Produces: `QueryScope(account_id=None, trader_ids=None)` per commands threads
- Consumed by: Task 2 (`_scope_where`), Task 3 (`DashboardManager`)

- [ ] **Step 1: Aggiorna i test esistenti per commands thread**

I test `test_commands_thread_maps_to_full_account_scope` e `test_commands_thread_second_account` oggi si aspettano `account_id="demo_1"` / `account_id="demo_2"`. Con il nuovo design i commands thread mappano a `account_id=None`. Aggiorna gli assert:

```python
# tests/runtime_v2/control_plane/test_scope_resolver.py

def test_commands_thread_maps_to_global_scope():
    r = ScopeResolver(_config_multi())
    scope = r.resolve(4)
    assert scope == QueryScope(account_id=None, trader_ids=None)


def test_commands_thread_second_account_maps_to_global_scope():
    r = ScopeResolver(_config_multi())
    scope = r.resolve(42)
    assert scope == QueryScope(account_id=None, trader_ids=None)
```

Rinomina anche i metodi (da `test_commands_thread_maps_to_full_account_scope` → `test_commands_thread_maps_to_global_scope` ecc.) per chiarezza.

- [ ] **Step 2: Aggiungi test per `QueryScope(account_id=None)` frozen**

Alla fine del file di test:

```python
def test_global_scope_has_none_account_id():
    scope = QueryScope(account_id=None, trader_ids=None)
    assert scope.account_id is None
    assert scope.trader_ids is None


def test_global_scope_is_frozen():
    scope = QueryScope(account_id=None, trader_ids=None)
    try:
        scope.account_id = "x"  # type: ignore[misc]
        assert False, "should raise"
    except (AttributeError, TypeError):
        pass
```

- [ ] **Step 3: Verifica che i test falliscano**

```
python -m pytest tests/runtime_v2/control_plane/test_scope_resolver.py -v
```

Atteso: FAIL su `test_commands_thread_maps_to_global_scope` e `test_commands_thread_second_account_maps_to_global_scope` (assert `account_id` sbagliato).

- [ ] **Step 4: Implementa le modifiche in `scope_resolver.py`**

```python
# src/runtime_v2/control_plane/scope_resolver.py
from __future__ import annotations

from dataclasses import dataclass

from src.runtime_v2.control_plane.models import ControlPlaneConfig


@dataclass(frozen=True)
class QueryScope:
    account_id: str | None        # None = tutti gli account (scope globale)
    trader_ids: list[str] | None  # None = tutti i trader dello scope


class ScopeResolver:
    """Reverse lookup: thread_id → QueryScope.

    Built once at boot from ControlPlaneConfig. Commands threads always
    resolve to global scope (account_id=None). Clean-log per-trader threads
    resolve to single-trader scope. Clean-log fallback threads resolve to
    full-account scope.
    """

    def __init__(self, config: ControlPlaneConfig) -> None:
        self._default_account = config.default_account
        self._map: dict[int, QueryScope] = {}

        for account_id, acc in config.per_account.items():
            topics = acc.topics

            # commands thread → scope globale (tutti gli account)
            if topics.commands.thread_id is not None:
                self._map[topics.commands.thread_id] = QueryScope(
                    account_id=None, trader_ids=None
                )

            # clean_log fallback → account singolo, tutti i trader
            if topics.clean_log.thread_id is not None:
                self._map[topics.clean_log.thread_id] = QueryScope(
                    account_id=account_id, trader_ids=None
                )

            # clean_log per-trader → trader singolo
            for trader_id, tid in topics.clean_log.per_trader.items():
                if tid is not None:
                    self._map[tid] = QueryScope(
                        account_id=account_id, trader_ids=[trader_id]
                    )

            # tech_log è intenzionalmente omesso — non è mai uno scope comandi

    def resolve(self, thread_id: int | None) -> QueryScope:
        """Return scope for thread_id, falling back to default_account if unknown."""
        if thread_id is not None and thread_id in self._map:
            return self._map[thread_id]
        return QueryScope(account_id=self._default_account, trader_ids=None)


__all__ = ["QueryScope", "ScopeResolver"]
```

- [ ] **Step 5: Esegui i test e verifica che passino tutti**

```
python -m pytest tests/runtime_v2/control_plane/test_scope_resolver.py -v
```

Atteso: tutti PASS (inclusi i test pre-esistenti per clean_log, fallback, frozen).

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/scope_resolver.py tests/runtime_v2/control_plane/test_scope_resolver.py
git commit -m "feat(scope): QueryScope.account_id str|None; commands thread → global scope"
```

---

## Task 2: `_scope_where()` — ramo globale in `status_queries.py`

**Files:**
- Modify: `src/runtime_v2/control_plane/status_queries.py` (solo funzione `_scope_where`, righe ~242-254)
- Test: `tests/runtime_v2/control_plane/test_status_queries_scoped.py`

**Interfaces:**
- Consumes: `QueryScope` da Task 1 — con `account_id: str | None`
- Produces: `_scope_where(QueryScope(None, None))` → `("1=1", [])`

- [ ] **Step 1: Aggiungi test per scope globale in `test_status_queries_scoped.py`**

Aggiungi alla fine del file (dopo gli import e gli helper già presenti):

```python
# ---------------------------------------------------------------------------
# Global scope (account_id=None) — _scope_where produces WHERE 1=1
# ---------------------------------------------------------------------------

def test_get_open_trades_global_scope_returns_all_accounts(tmp_path):
    """Global scope must return trades from every account."""
    db_path = str(tmp_path / "ops.db")
    _apply_migrations(db_path)
    conn = sqlite3.connect(db_path)

    _add_chain(conn, 1, "OPEN", account_id="account_A", trader_id="trader_a")
    _add_chain(conn, 2, "OPEN", account_id="account_B", trader_id="trader_b")
    _add_chain(conn, 3, "OPEN", account_id="account_C", trader_id="trader_c")
    conn.commit()
    conn.close()

    sq = StatusQueries(db_path)
    global_scope = QueryScope(account_id=None, trader_ids=None)
    view = sq.get_open_trades(global_scope)

    ids = {r.chain_id for r in view.rows}
    assert ids == {1, 2, 3}


def test_get_closed_trades_global_scope_returns_all_accounts(tmp_path):
    """Global scope must return closed trades from every account."""
    db_path = str(tmp_path / "ops.db")
    _apply_migrations(db_path)
    conn = sqlite3.connect(db_path)

    _add_chain(conn, 10, "CLOSED", account_id="account_A", trader_id="trader_a",
               cumulative_gross_pnl=10.0)
    _add_chain(conn, 11, "CLOSED", account_id="account_B", trader_id="trader_b",
               cumulative_gross_pnl=20.0)
    conn.commit()
    conn.close()

    sq = StatusQueries(db_path)
    global_scope = QueryScope(account_id=None, trader_ids=None)
    view = sq.get_closed_trades(global_scope)

    ids = {r.chain_id for r in view.rows}
    assert ids == {10, 11}


def test_get_stats_global_scope_aggregates_all_accounts(tmp_path):
    """Global scope stats must count trades from all accounts."""
    db_path = str(tmp_path / "ops.db")
    _apply_migrations(db_path)
    conn = sqlite3.connect(db_path)

    _add_chain(conn, 20, "CLOSED", account_id="account_A", trader_id="trader_a",
               cumulative_gross_pnl=5.0)
    _add_chain(conn, 21, "CLOSED", account_id="account_B", trader_id="trader_b",
               cumulative_gross_pnl=15.0)
    conn.commit()
    conn.close()

    sq = StatusQueries(db_path)
    global_scope = QueryScope(account_id=None, trader_ids=None)
    view = sq.get_stats(global_scope)

    totale = next(r for r in view.rows if r.label == "Totale")
    assert totale.trade_count == 2
```

- [ ] **Step 2: Esegui per verificare che i test falliscano**

```
python -m pytest tests/runtime_v2/control_plane/test_status_queries_scoped.py::test_get_open_trades_global_scope_returns_all_accounts tests/runtime_v2/control_plane/test_status_queries_scoped.py::test_get_closed_trades_global_scope_returns_all_accounts tests/runtime_v2/control_plane/test_status_queries_scoped.py::test_get_stats_global_scope_aggregates_all_accounts -v
```

Atteso: FAIL — `account_id=None` genera `WHERE account_id = None` (SQL errato) → 0 risultati o errore.

- [ ] **Step 3: Implementa il ramo globale in `_scope_where()`**

Trova `_scope_where` in `src/runtime_v2/control_plane/status_queries.py` e sostituisci con:

```python
def _scope_where(scope: QueryScope, table_alias: str = "") -> tuple[str, list]:
    """Return (WHERE-fragment, params) for the given scope.

    The fragment does NOT include the leading WHERE keyword.
    account_id=None means global scope — no filter applied.
    """
    prefix = f"{table_alias}." if table_alias else ""

    # Scope globale — nessun filtro account né trader
    if scope.account_id is None and scope.trader_ids is None:
        return "1=1", []

    # Account singolo, tutti i trader
    if scope.trader_ids is None:
        return f"{prefix}account_id = ?", [scope.account_id]

    # Account singolo + trader specifici
    placeholders = ",".join("?" * len(scope.trader_ids))
    return (
        f"{prefix}account_id = ? AND {prefix}trader_id IN ({placeholders})",
        [scope.account_id, *scope.trader_ids],
    )
```

Nota: `get_pnl()` contiene anche una query diretta su `ops_account_snapshots` che filtra per `account_id`. Aggiorna quella query in `get_pnl()` per gestire `scope.account_id=None`:

```python
# in get_pnl(), dentro il blocco `if scope is not None:`
if scope.account_id is not None:
    snapshot = conn.execute(
        "SELECT account_id, equity_usdt, available_balance_usdt, "
        "total_open_risk_usdt, total_margin_used_usdt, source, captured_at "
        "FROM ops_account_snapshots "
        "WHERE account_id=? "
        "ORDER BY datetime(captured_at) DESC, snapshot_id DESC LIMIT 1",
        (scope.account_id,),
    ).fetchone()
else:
    # Scope globale: snapshot più recente tra tutti gli account
    snapshot = conn.execute(
        "SELECT account_id, equity_usdt, available_balance_usdt, "
        "total_open_risk_usdt, total_margin_used_usdt, source, captured_at "
        "FROM ops_account_snapshots "
        "ORDER BY datetime(captured_at) DESC, snapshot_id DESC LIMIT 1"
    ).fetchone()
```

- [ ] **Step 4: Esegui tutti i test del file**

```
python -m pytest tests/runtime_v2/control_plane/test_status_queries_scoped.py -v
```

Atteso: tutti PASS.

- [ ] **Step 5: Verifica la suite completa**

```
python -m pytest tests/runtime_v2/control_plane/ -v
```

Atteso: tutti PASS (≥55 test).

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/status_queries.py tests/runtime_v2/control_plane/test_status_queries_scoped.py
git commit -m "feat(queries): _scope_where global branch; account_id=None yields WHERE 1=1"
```

---

## Task 3: `DashboardManager` — persistenza e matching scope globale

**Files:**
- Modify: `src/runtime_v2/control_plane/dashboard_manager.py`
- Test: `tests/runtime_v2/control_plane/test_dashboard_manager.py`

**Interfaces:**
- Consumes: `QueryScope` da Task 1 — con `account_id: str | None`
- `_matches_scope(None, None, any_account, any_trader)` → `True`
- `_save_dashboard(scope=QueryScope(None,None))` → `scope_account_id=NULL` in DB

- [ ] **Step 1: Aggiorna `_make_db` nel test — `scope_account_id` diventa nullable**

Nel file `tests/runtime_v2/control_plane/test_dashboard_manager.py`, trova `_make_db` e cambia la colonna:

```python
def _make_db(tmp_path) -> str:
    db_path = str(tmp_path / "test_ops.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops_dashboard_messages (
            chat_id           INTEGER NOT NULL,
            thread_id         INTEGER NOT NULL DEFAULT 0,
            message_id        INTEGER NOT NULL,
            scope_account_id  TEXT,            -- NULL = scope globale
            scope_trader_id   TEXT,
            current_view      TEXT NOT NULL DEFAULT 'attivi:0',
            updated_at        TEXT,
            PRIMARY KEY (chat_id, thread_id)
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path
```

Aggiungi anche un helper per scope globale:

```python
def _make_global_scope() -> QueryScope:
    return QueryScope(account_id=None, trader_ids=None)
```

- [ ] **Step 2: Aggiungi test per `_matches_scope` con scope globale**

```python
# tests/runtime_v2/control_plane/test_dashboard_manager.py

def test_matches_scope_global_always_true():
    """scope_account_id=None matches any account and any trader."""
    assert _matches_scope(None, None, "account_A", "trader_a") is True
    assert _matches_scope(None, None, "account_B", "trader_x") is True
    assert _matches_scope(None, None, "demo_2", "trader_devos_crypto") is True


def test_matches_scope_specific_account_still_works():
    """Non-global scopes remain filtered as before."""
    assert _matches_scope("acc1", None, "acc1", "any_trader") is True
    assert _matches_scope("acc1", None, "acc2", "any_trader") is False
    assert _matches_scope("acc1", "t_a", "acc1", "t_a") is True
    assert _matches_scope("acc1", "t_a", "acc1", "t_b") is False
```

- [ ] **Step 3: Aggiungi test per `create()` con scope globale**

```python
@pytest.mark.asyncio
async def test_create_global_scope_saves_null_account_id(tmp_path):
    """Dashboard with global scope saves scope_account_id=NULL in DB."""
    manager = _make_manager(tmp_path, scope=_make_global_scope())
    _patch_render_view(manager)

    await manager.create(
        scope=QueryScope(account_id=None, trader_ids=None),
        chat_id=-100,
        thread_id=4,
    )

    conn = sqlite3.connect(manager._db)
    row = conn.execute(
        "SELECT scope_account_id, scope_trader_id FROM ops_dashboard_messages "
        "WHERE chat_id=? AND thread_id=?",
        (-100, 4),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] is None   # scope_account_id = NULL
    assert row[1] is None   # scope_trader_id = NULL


@pytest.mark.asyncio
async def test_on_trade_event_triggers_global_dashboard(tmp_path):
    """A global dashboard is refreshed for any account/trader trade event."""
    manager = _make_manager(tmp_path, scope=_make_global_scope())
    fake_keyboard = _patch_render_view(manager)

    # Manually insert a global dashboard row
    conn = sqlite3.connect(manager._db)
    conn.execute(
        "INSERT INTO ops_dashboard_messages "
        "(chat_id, thread_id, message_id, scope_account_id, scope_trader_id, current_view) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (-100, 4, 99, None, None, "attivi:0"),
    )
    conn.commit()
    conn.close()

    # Trade event from a completely different account — global dashboard must refresh
    await manager.on_trade_event(account_id="account_Z", trader_id="trader_z")

    manager._bot.edit_message_text.assert_awaited_once()
```

- [ ] **Step 4: Esegui per verificare che i test falliscano**

```
python -m pytest tests/runtime_v2/control_plane/test_dashboard_manager.py -k "global" -v
```

Atteso: FAIL — `_matches_scope` non gestisce `scope_account_id=None`, `_save_dashboard` ha `NOT NULL` sulla colonna.

- [ ] **Step 5: Implementa le modifiche in `dashboard_manager.py`**

**A. `_matches_scope`** — aggiunge ramo globale:

```python
def _matches_scope(
    scope_account_id: str | None,
    scope_trader_id: str | None,
    account_id: str,
    trader_id: str,
) -> bool:
    # Scope globale → si aggiorna sempre
    if scope_account_id is None:
        return True
    if scope_account_id != account_id:
        return False
    if scope_trader_id is None:
        return True
    return scope_trader_id == trader_id
```

**B. `_save_dashboard`** — rimuovi la logica che forzava `trader_id` e usa `scope.account_id` direttamente (può essere `None`):

```python
def _save_dashboard(
    self,
    chat_id: int,
    thread_id: int,
    message_id: int,
    scope: QueryScope,
    current_view: str,
) -> None:
    account_id: str | None = scope.account_id  # None per scope globale
    trader_id: str | None = None
    if scope.trader_ids and len(scope.trader_ids) == 1:
        trader_id = scope.trader_ids[0]

    conn = self._connect()
    try:
        conn.execute(
            """
            INSERT INTO ops_dashboard_messages
                (chat_id, thread_id, message_id, scope_account_id, scope_trader_id,
                 current_view, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, thread_id) DO UPDATE SET
                message_id = excluded.message_id,
                scope_account_id = excluded.scope_account_id,
                scope_trader_id = excluded.scope_trader_id,
                current_view = excluded.current_view,
                updated_at = excluded.updated_at
            """,
            (chat_id, thread_id, message_id, account_id, trader_id, current_view, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
```

**C. Ricostruzione scope dal DB** — aggiorna il blocco presente sia in `handle_callback` che in `_do_refresh`:

```python
# Sostituisci il blocco di ricostruzione in ENTRAMBI i metodi:
if scope_account_id is None:
    scope = QueryScope(account_id=None, trader_ids=None)
elif scope_trader_id is not None:
    scope = QueryScope(account_id=scope_account_id, trader_ids=[scope_trader_id])
else:
    scope = QueryScope(account_id=scope_account_id, trader_ids=None)
```

- [ ] **Step 6: Esegui tutti i test del file**

```
python -m pytest tests/runtime_v2/control_plane/test_dashboard_manager.py -v
```

Atteso: tutti PASS.

- [ ] **Step 7: Esegui la suite completa**

```
python -m pytest tests/runtime_v2/control_plane/ -v
```

Atteso: tutti PASS (≥55 + nuovi test).

- [ ] **Step 8: Commit**

```bash
git add src/runtime_v2/control_plane/dashboard_manager.py tests/runtime_v2/control_plane/test_dashboard_manager.py
git commit -m "feat(dashboard): global scope support — NULL account_id, _matches_scope always-true, scope reconstruction"
```

---

## Verifica finale

- [ ] **Suite completa**

```
python -m pytest tests/runtime_v2/control_plane/ -v
```

Atteso: tutti PASS.

- [ ] **Smoke test scope resolver con config reale**

```python
# Esegui da shell nella root del progetto:
python -c "
import os, yaml, sys
sys.path.insert(0, '.')
os.environ['CONTROL_TELEGRAM_BOT_TOKEN'] = 'dummy:token'
os.environ['CONTROL_TELEGRAM_USER_ID'] = '99999999'
from src.runtime_v2.control_plane.models import ControlPlaneConfig
from src.runtime_v2.control_plane.scope_resolver import ScopeResolver
with open('config/telegram_control.yaml') as f:
    raw = yaml.safe_load(f)
raw['token'] = 'dummy'
raw['authorized_users'] = [99999999]
config = ControlPlaneConfig.model_validate(raw)
resolver = ScopeResolver(config)
for tid, scope in sorted(resolver._map.items()):
    print(f'thread {tid:5d}  account={scope.account_id}  traders={scope.trader_ids}')
"
```

Atteso:
```
thread     2  account=demo_1  traders=None
thread     4  account=None    traders=None    ← globale
thread   316  account=demo_1  traders=['trader_a']
thread   318  account=demo_1  traders=['trader_b']
...
thread  1024  account=demo_2  traders=['trader_devos_crypto']
```

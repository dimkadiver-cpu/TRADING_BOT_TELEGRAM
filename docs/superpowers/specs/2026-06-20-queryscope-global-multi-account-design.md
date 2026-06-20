# Design: QueryScope Global — Dashboard Multi-Account Aggregato
**Data:** 2026-06-20
**Stato:** approvato — pronto per piano di implementazione

---

## Contesto

Il `CommandRouter` e il `DashboardManager` usano `QueryScope` per filtrare i dati per account e trader. Oggi `account_id` è sempre `str` — un commands topic è legato a un singolo account. Con più account exchange (es. `demo_1`, `demo_2`, ...) il dashboard nel topic commands non aggrega tutti i trade: mostra solo quelli dell'account a cui è mappato il thread.

L'obiettivo è che il topic commands sia la **torre di controllo globale** del bot: dashboard, comandi read-only e comandi di emergenza agiscono su **tutti gli account e tutti i trader** simultaneamente.

---

## Scope della modifica

- **In scope:** `QueryScope`, `ScopeResolver`, `_scope_where()`, `DashboardManager`
- **Fuori scope:** template, formatter, `telegram_bot.py`, `telegram_control.yaml`, schema DB (nessuna migration)

---

## Design

### 1. `QueryScope` — `account_id: str | None`

**File:** `src/runtime_v2/control_plane/scope_resolver.py`

```python
@dataclass(frozen=True)
class QueryScope:
    account_id: str | None        # None = tutti gli account (scope globale)
    trader_ids: list[str] | None  # None = tutti i trader dello scope (invariato)
```

Convenzione:
- `QueryScope(account_id=None, trader_ids=None)` → **globale** — nessun filtro
- `QueryScope(account_id='demo_1', trader_ids=None)` → account singolo, tutti i trader
- `QueryScope(account_id='demo_1', trader_ids=['trader_a'])` → trader specifico

### 2. `ScopeResolver` — commands thread mappa a scope globale

**File:** `src/runtime_v2/control_plane/scope_resolver.py`

```python
for account_id, acc in config.per_account.items():
    topics = acc.topics

    # commands thread → scope globale (tutti gli account)
    if topics.commands.thread_id is not None:
        self._map[topics.commands.thread_id] = QueryScope(
            account_id=None, trader_ids=None
        )

    # clean_log fallback → account singolo (invariato)
    if topics.clean_log.thread_id is not None:
        self._map[topics.clean_log.thread_id] = QueryScope(
            account_id=account_id, trader_ids=None
        )

    # clean_log per-trader → trader singolo (invariato)
    for trader_id, tid in topics.clean_log.per_trader.items():
        if tid is not None:
            self._map[tid] = QueryScope(
                account_id=account_id, trader_ids=[trader_id]
            )
```

**Nessuna modifica al `telegram_control.yaml`** — il comportamento globale del commands topic è implicito, non configurabile.

**Fallback invariato:** thread non riconosciuto → `QueryScope(account_id=default_account, trader_ids=None)`.

### 3. `_scope_where()` — caso globale

**File:** `src/runtime_v2/control_plane/status_queries.py`

```python
def _scope_where(scope: QueryScope, table_alias: str = "") -> tuple[str, list]:
    prefix = f"{table_alias}." if table_alias else ""

    # Scope globale — nessun filtro account né trader
    if scope.account_id is None and scope.trader_ids is None:
        return "1=1", []

    # Account singolo, tutti i trader (invariato)
    if scope.trader_ids is None:
        return f"{prefix}account_id = ?", [scope.account_id]

    # Account singolo + trader specifici (invariato)
    placeholders = ",".join("?" * len(scope.trader_ids))
    return (
        f"{prefix}account_id = ? AND {prefix}trader_id IN ({placeholders})",
        [scope.account_id, *scope.trader_ids],
    )
```

Tutti i metodi `StatusQueries` (`get_open_trades`, `get_closed_trades`, `get_stats`, `get_pnl`, `get_blocked_trades`, ecc.) già delegano a `_scope_where()` — prendono il caso globale automaticamente senza modifiche.

**Edge case `get_pnl()`:** la query su `ops_account_snapshots` usa `account_id` separatamente. Con scope globale (`account_id=None`) la query non filtra e restituisce lo snapshot più recente tra tutti gli account — comportamento corretto per la vista aggregata.

### 4. `DashboardManager` — scope globale in DB e matching

**File:** `src/runtime_v2/control_plane/dashboard_manager.py`

**`ops_dashboard_messages`:** `scope_account_id` è già `TEXT` senza `NOT NULL` — accetta `NULL`. Nessuna migration.

```
scope_account_id = NULL         → scope globale
scope_account_id = 'demo_1'    → account singolo (invariato)
scope_trader_id  = NULL         → tutti i trader dello scope (invariato)
```

**`_save_dashboard()`:** salva `scope.account_id` direttamente (può essere `None`/`NULL`).

**`_matches_scope()`** — aggiunge il caso globale:

```python
def _matches_scope(scope_account_id, scope_trader_id, account_id, trader_id):
    # Scope globale → si aggiorna sempre
    if scope_account_id is None:
        return True
    # Account diverso → non toccare
    if scope_account_id != account_id:
        return False
    # Account corretto, tutti i trader
    if scope_trader_id is None:
        return True
    # Account corretto, trader specifico
    return scope_trader_id == trader_id
```

**Ricostruzione scope dal DB** (in `handle_callback` e `_do_refresh`):

```python
if scope_account_id is None:
    scope = QueryScope(account_id=None, trader_ids=None)
elif scope_trader_id is not None:
    scope = QueryScope(account_id=scope_account_id, trader_ids=[scope_trader_id])
else:
    scope = QueryScope(account_id=scope_account_id, trader_ids=None)
```

---

## File toccati

| File | Modifica |
|------|----------|
| `scope_resolver.py` | `QueryScope.account_id: str → str \| None`; commands thread → `QueryScope(None, None)` |
| `status_queries.py` | `_scope_where()` aggiunge ramo `account_id=None` |
| `dashboard_manager.py` | `_matches_scope()` + ricostruzione scope dal DB |

**Nessuna modifica a:** `telegram_control.yaml`, `dashboard.py`, tutti i formatter, `telegram_bot.py`, template, schema DB.

---

## Backward compatibility

- Dashboard già presenti in `ops_dashboard_messages` con `scope_account_id='demo_1'` continuano a funzionare — la ricostruzione scope gestisce entrambi i casi.
- Il record thread 1024 con `scope_account_id='demo_1'` (bug precedente) viene sovrascritto al prossimo `/dashboard` da quel topic — nessun intervento manuale.
- Tutti i comandi read-only e di emergenza diventano automaticamente globali quando inviati dal commands topic — nessuna modifica ai loro handler.

---

## Comportamento risultante

| Topic | Scope | Dashboard mostra |
|-------|-------|-----------------|
| commands (thread 4) | `account_id=None` | **tutti** i trade di tutti gli account |
| clean_log fallback demo_1 (thread 2) | `account_id='demo_1'` | tutti i trader di demo_1 |
| clean_log trader_d (thread 320) | `account_id='demo_1', traders=['trader_d']` | solo trader_d |
| clean_log trader_devos_crypto (thread 1024) | `account_id='demo_2', traders=['trader_devos_crypto']` | solo trader_devos_crypto |

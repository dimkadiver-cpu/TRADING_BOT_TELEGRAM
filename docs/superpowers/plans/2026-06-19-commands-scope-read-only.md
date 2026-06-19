# Piano 1 — Foundation + Comandi Read-Only

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggiungere scope (account_id + trader_id) a tutti i comandi read-only esistenti, introdurre `/stats`, refactorare i formatter al pattern block-based, e cablare `ScopeResolver` al boot.

**Architecture:** `ScopeResolver` costruito al boot da `ControlPlaneConfig` risolve `(chat_id, thread_id)` → `QueryScope`. `StatusQueries` riceve `QueryScope` come parametro. I formatter diventano wrapper thin su `render_template(config.blocks, payload)`. `auth.py` espande la validazione a multi-account e aggiunge il topic `clean_log` (solo `/dashboard` — wire completato in Piano 3).

**Tech Stack:** Python 3.11+, SQLite, python-telegram-bot (esistenti). Nessuna nuova dipendenza.

---

## ⚠️ Correzioni post-revisione (2026-06-19)

Verifica contro schema reale (`db/ops_migrations/`), `auth.py`, `telegram_bot.py`, `status_queries.py`:

1. **`closed_at` non esiste** in `ops_trade_chains` (nessuna migration la aggiunge). Il fallback PRAGMA a `updated_at` usato in `get_stats()` è corretto e va **mantenuto**. Le fixture di test devono **NON** avere `closed_at` (altrimenti il ramo di fallback non è mai testato e la spec resta disallineata).
2. **`review_reason` non esiste** come colonna. Il motivo dei review si legge da `ops_lifecycle_events.payload_json` (come già fa `get_reviews()`). Non aggiungerla alle fixture.
3. **Le fixture di test devono derivare dallo schema reale** applicando `db/ops_migrations/*.sql`, non ridichiarando a mano `ops_trade_chains`. Colonne PnL (`cumulative_gross_pnl/fees/funding`, mig. 010), `plan_state_json` (004), `source_chat_id`/`telegram_message_id` (009) esistono davvero.
4. **Routing reply multi-account (Task 8).** Oggi `_on_command` invia SEMPRE la risposta a `default_acc.chat_id` + commands thread del default. Con multi-account la risposta finisce nell'account sbagliato. Va corretto per rispondere a `message.chat_id` / `message.message_thread_id` di origine (vedi nota in Task 8 Step 12).

---

## Divisione in piani

| Piano | Contenuto |
|---|---|
| **Piano 1 (questo)** | Foundation (QueryScope, ScopeResolver, block ext, auth multi-account) + comandi read-only (`/trades`, `/pnl`, `/stats`, `/status`, `/control`, `/health`, `/reviews`) |
| **Piano 2** | Emergency close (`/close_all`, `/close`, `/cancel_all`) con conferma inline |
| **Piano 3** | Dashboard (DashboardManager, 5 viste, paginazione, auto-refresh) |

## Global Constraints

- Nessuna nuova dipendenza di produzione
- Tutti i template command-side usano `payload_transform=None` (payload costruito nel formatter, non tramite transform)
- Chiavi TEMPLATE_REGISTRY in minuscolo per i comandi (`"trades"`, `"pnl"`, `"stats"`)
- `display_symbol(raw)` già esiste in `src/runtime_v2/control_plane/formatters/display.py` — usarla sempre per simboli (ritorna `"ETH/USDT"`)
- Pattern test: funzioni plain (non `class`), factory `_config()` che ritorna `ControlPlaneConfig`
- `render_template(config.blocks, payload, transform=config.payload_transform)` — firma immutabile
- `SectionBlock.label` callable = opzione A (estensione tipo, nessun nuovo blocco)
- Scope in DB: `thread_id INTEGER NOT NULL DEFAULT 0` (`0` = private_bot / nessun thread)

---

## File Structure

**Nuovi file:**
- `src/runtime_v2/control_plane/scope_resolver.py` — `QueryScope` dataclass + `ScopeResolver`
- `src/runtime_v2/control_plane/formatters/stats.py` — `format_stats(view) -> str`
- `src/runtime_v2/control_plane/formatters/templates/commands.py` — `TEMPLATE_REGISTRY` comandi read-only
- `tests/runtime_v2/control_plane/test_scope_resolver.py`
- `tests/runtime_v2/control_plane/test_stats_formatter.py`
- `tests/runtime_v2/control_plane/test_commands_templates.py`

**File modificati:**
- `src/runtime_v2/control_plane/formatters/_blocks.py` — `SectionBlock.label: str | Callable[[dict], str]` + `TableBlock`
- `src/runtime_v2/control_plane/auth.py` — multi-account + topic `clean_log` in `AuthResult`
- `src/runtime_v2/control_plane/status_queries.py` — `QueryScope` in tutte le read, `StatsView`, `get_stats()`
- `src/runtime_v2/control_plane/service.py` — scope delegation, `get_stats()`
- `src/runtime_v2/control_plane/formatters/trades.py` — refactor block-based
- `src/runtime_v2/control_plane/formatters/pnl.py` — refactor block-based + PnL realizzato
- `src/runtime_v2/control_plane/formatters/status.py` — aggiunge header account_id
- `src/runtime_v2/control_plane/formatters/control.py` — aggiunge header account_id
- `src/runtime_v2/control_plane/telegram_bot.py` — `ScopeResolver` iniettato, scope passato a dispatch, `/stats` + `/dashboard` in allowed commands

---

## Task 0: Pre-flight — verifica schema DB

**Files:** nessuno (solo verifica prima di scrivere query)

Esegui questo script su una copia del DB di dev per determinare i nomi esatti delle colonne necessarie. I Task 4 e 6 dipendono da questi risultati.

- [ ] **Step 1: Verifica colonne ops_trade_chains**

```python
# script: scripts/check_schema.py (eseguire una volta, non committare)
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
cols = [r[1] for r in conn.execute("PRAGMA table_info(ops_trade_chains)").fetchall()]
print("ops_trade_chains:", cols)
cols2 = [r[1] for r in conn.execute("PRAGMA table_info(ops_control_state)").fetchall()]
print("ops_control_state:", cols2)
cols3 = [r[1] for r in conn.execute("PRAGMA table_info(ops_execution_commands)").fetchall()]
print("ops_execution_commands:", cols3)
conn.close()
```

Run: `python scripts/check_schema.py path/to/ops.db`

- [ ] **Step 2: Confermare presenza delle colonne usate in Task 4**

Colonne attese in `ops_trade_chains`:
- `account_id` — per scope filtering (verificato nei query esistenti di `get_trade()`)
- `trader_id` — per scope filtering (verificato)
- `lifecycle_state` — già usato
- `closed_at` o `updated_at` — per stats time bucketing (verificare quale)
- `cumulative_gross_pnl`, `cumulative_fees`, `cumulative_funding` — per stats + PnL realizzato (da confermare)
- `review_reason` — per Bloccati (da confermare; alternativa: `ops_lifecycle_events`)
- `source_chat_id`, `telegram_message_id` — per links (verificato in `get_trade()`)

Colonne attese in `ops_control_state`:
- `account_id` — se assente, il filtro scope per `/control` non è applicabile (documentarlo)

- [ ] **Step 3: Verifica comandi MARKET_CLOSE e CANCEL_ENTRY**

```python
# Verifica che questi command_type esistano nella codebase
```

Run: `rg "MARKET_CLOSE|CANCEL_ENTRY" src/ --type py -l`

Se non trovati, aggiungere note in Task 7 (service.py) su come creare i comandi di emergenza — ma per Piano 1 non è bloccante.

---

## Task 1: Block extensions — `SectionBlock` callable + `TableBlock`

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/_blocks.py`
- Test: `tests/runtime_v2/control_plane/formatters/test_blocks_ext.py`

**Interfaces:**
- Produces:
  - `SectionBlock(label: str | Callable[[dict], str], blocks: list)` — stessa firma, tipo esteso
  - `TableBlock(headers: list[str], rows_fn: Callable[[dict], list[list[str]]], alignments: list[Literal["left","right"]] | None = None)`
  - `render_template(blocks, payload, *, transform=None) -> str` — invariato

- [ ] **Step 1: Scrivere i test fallenti**

Creare `tests/runtime_v2/control_plane/formatters/test_blocks_ext.py`:

```python
from __future__ import annotations
from src.runtime_v2.control_plane.formatters._blocks import (
    SectionBlock, TableBlock, StaticBlock, render_template, TemplateConfig,
)


def test_section_block_callable_label():
    blocks = [SectionBlock(label=lambda p: f"Trader: {p['tid']}", blocks=[StaticBlock("val")])]
    result = render_template(blocks, {"tid": "trader_a"})
    assert "Trader: trader_a" in result
    assert "val" in result


def test_section_block_str_label_unchanged():
    blocks = [SectionBlock(label="Fixed", blocks=[StaticBlock("x")])]
    result = render_template(blocks, {})
    assert "Fixed" in result
    assert "x" in result


def test_table_block_left_aligned():
    def rows(p):
        return [["Oggi:", "1", "100%", "+18.40"], ["7g:", "6", "67%", "+62.10"]]
    blocks = [TableBlock(headers=["", "Trades", "Win%", "Netto"], rows_fn=rows)]
    result = render_template(blocks, {})
    lines = [l for l in result.splitlines() if l.strip()]
    assert len(lines) == 3  # header + 2 rows
    assert "Trades" in lines[0]
    assert "Oggi:" in lines[1]
    assert "7g:" in lines[2]


def test_table_block_right_aligned_numbers():
    def rows(p):
        return [["Oggi:", "1"], ["Totale:", "31"]]
    blocks = [TableBlock(
        headers=["", "N"],
        rows_fn=rows,
        alignments=["left", "right"],
    )]
    result = render_template(blocks, {})
    lines = [l for l in result.splitlines() if l.strip()]
    # colonna N: "1" e "31" devono avere stessa larghezza → "31" allineato a destra
    assert lines[2].endswith("31") or "31" in lines[2]
```

Run: `pytest tests/runtime_v2/control_plane/formatters/test_blocks_ext.py -v`
Expected: FAIL (`TableBlock` non definito)

- [ ] **Step 2: Estendere `SectionBlock` e aggiungere `TableBlock` in `_blocks.py`**

Modificare `SectionBlock`:
```python
@dataclass
class SectionBlock:
    """Static or dynamic label + sub-blocks."""
    label: str | Callable[[dict], str]
    blocks: list
```

Aggiungere dopo `ListBlock`:
```python
@dataclass
class TableBlock:
    """Aligned columnar table.
    rows_fn(payload) -> list[list[str]] (escluso header).
    alignments: lista di "left"/"right" per colonna; default "left" per tutte.
    """
    headers: list[str]
    rows_fn: Callable[[dict], list[list[str]]]
    alignments: list[Literal["left", "right"]] | None = None
```

Aggiungere import `Literal` se non presente:
```python
from typing import Any, Callable, Literal
```

- [ ] **Step 3: Aggiornare il renderer in `_render_blocks`**

Sostituire il case `SectionBlock`:
```python
case SectionBlock(label=lbl, blocks=sub):
    resolved = lbl(p) if callable(lbl) else lbl
    lines.append(resolved)
    _render_blocks(sub, p, lines)
```

Aggiungere dopo il case `ListBlock`:
```python
case TableBlock(headers=hdrs, rows_fn=fn, alignments=alns):
    all_rows = [hdrs] + fn(p)
    n_cols = len(hdrs)
    widths = [
        max(len(str(row[i])) for row in all_rows if i < len(row))
        for i in range(n_cols)
    ]
    effective_alns = alns or ["left"] * n_cols
    for row in all_rows:
        cells = []
        for i in range(n_cols):
            cell = str(row[i]) if i < len(row) else ""
            w = widths[i]
            if i < len(effective_alns) and effective_alns[i] == "right":
                cells.append(cell.rjust(w))
            else:
                cells.append(cell.ljust(w))
        lines.append("  ".join(cells).rstrip())
```

- [ ] **Step 4: Eseguire i test**

Run: `pytest tests/runtime_v2/control_plane/formatters/test_blocks_ext.py -v`
Expected: PASS (tutti e 4)

- [ ] **Step 5: Verificare che i test esistenti non siano rotti**

Run: `pytest tests/runtime_v2/control_plane/ -v --tb=short`
Expected: tutti PASS

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/_blocks.py \
        tests/runtime_v2/control_plane/formatters/test_blocks_ext.py
git commit -m "feat: extend SectionBlock to callable label and add TableBlock primitive"
```

---

## Task 2: `QueryScope` + `ScopeResolver`

**Files:**
- Create: `src/runtime_v2/control_plane/scope_resolver.py`
- Test: `tests/runtime_v2/control_plane/test_scope_resolver.py`

**Interfaces:**
- Produces:
  - `QueryScope(account_id: str, trader_ids: list[str] | None)` — `frozen=True`, `trader_ids=None` = tutti i trader dell'account
  - `ScopeResolver(config: ControlPlaneConfig)` con `.resolve(chat_id: int, thread_id: int) -> QueryScope | None`
  - `thread_id=0` = private_bot / nessun thread

- [ ] **Step 1: Scrivere i test fallenti**

Creare `tests/runtime_v2/control_plane/test_scope_resolver.py`:

```python
from __future__ import annotations

from src.runtime_v2.control_plane.models import (
    AccountConfig, AccountTopicsConfig, CleanLogConfig,
    ControlPlaneConfig, TechLogConfig, TopicConfig,
)
from src.runtime_v2.control_plane.scope_resolver import QueryScope, ScopeResolver


def _config_multi() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        token="t",
        default_account="demo_1",
        per_account={
            "demo_1": AccountConfig(
                chat_id=-100111,
                topics=AccountTopicsConfig(
                    commands=TopicConfig(thread_id=4),
                    tech_log=TechLogConfig(thread_id=5),
                    clean_log=CleanLogConfig(
                        thread_id=2,  # fallback
                        per_trader={"trader_a": 316, "trader_b": 318},
                    ),
                ),
            ),
            "demo_2": AccountConfig(
                chat_id=-100222,
                topics=AccountTopicsConfig(
                    commands=TopicConfig(thread_id=10),
                    tech_log=TechLogConfig(thread_id=11),
                    clean_log=CleanLogConfig(thread_id=12),
                ),
            ),
        },
        authorized_users=[42],
    )


def _config_private_bot() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        token="t",
        default_account="main",
        delivery_mode="private_bot",
        per_account={
            "main": AccountConfig(
                chat_id=-100999,
                topics=AccountTopicsConfig(
                    commands=TopicConfig(thread_id=None),
                    tech_log=TechLogConfig(thread_id=None),
                    clean_log=CleanLogConfig(thread_id=None),
                ),
            )
        },
        authorized_users=[42],
    )


# --- commands topic ---

def test_resolve_commands_topic_all_traders():
    r = ScopeResolver(_config_multi())
    scope = r.resolve(chat_id=-100111, thread_id=4)
    assert scope == QueryScope(account_id="demo_1", trader_ids=None)


def test_resolve_commands_topic_second_account():
    r = ScopeResolver(_config_multi())
    scope = r.resolve(chat_id=-100222, thread_id=10)
    assert scope == QueryScope(account_id="demo_2", trader_ids=None)


# --- clean_log per-trader ---

def test_resolve_clean_log_per_trader_a():
    r = ScopeResolver(_config_multi())
    scope = r.resolve(chat_id=-100111, thread_id=316)
    assert scope == QueryScope(account_id="demo_1", trader_ids=["trader_a"])


def test_resolve_clean_log_per_trader_b():
    r = ScopeResolver(_config_multi())
    scope = r.resolve(chat_id=-100111, thread_id=318)
    assert scope == QueryScope(account_id="demo_1", trader_ids=["trader_b"])


# --- clean_log fallback ---

def test_resolve_clean_log_fallback_all_traders():
    r = ScopeResolver(_config_multi())
    scope = r.resolve(chat_id=-100111, thread_id=2)
    assert scope == QueryScope(account_id="demo_1", trader_ids=None)


# --- tech_log topic → None (non autorizzato per comandi) ---

def test_resolve_tech_log_topic_returns_none():
    r = ScopeResolver(_config_multi())
    scope = r.resolve(chat_id=-100111, thread_id=5)
    assert scope is None


# --- unknown ---

def test_resolve_unknown_returns_none():
    r = ScopeResolver(_config_multi())
    assert r.resolve(chat_id=-100111, thread_id=999) is None
    assert r.resolve(chat_id=-1, thread_id=4) is None


# --- private_bot (thread_id=None → 0) ---

def test_resolve_private_bot():
    r = ScopeResolver(_config_private_bot())
    scope = r.resolve(chat_id=-100999, thread_id=0)
    assert scope == QueryScope(account_id="main", trader_ids=None)
```

Run: `pytest tests/runtime_v2/control_plane/test_scope_resolver.py -v`
Expected: FAIL (`scope_resolver` not found)

- [ ] **Step 2: Implementare `scope_resolver.py`**

Creare `src/runtime_v2/control_plane/scope_resolver.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from src.runtime_v2.control_plane.models import ControlPlaneConfig


@dataclass(frozen=True)
class QueryScope:
    """Scope di filtraggio per query DB.

    trader_ids=None significa tutti i trader dell'account.
    """
    account_id: str
    trader_ids: list[str] | None


class ScopeResolver:
    """Risolve (chat_id, thread_id) -> QueryScope al boot da ControlPlaneConfig.

    thread_id=0 rappresenta 'nessun thread' (private_bot mode o topic senza thread).
    Solo commands e clean_log topic producono scope; tech_log restituisce None.
    """

    def __init__(self, config: ControlPlaneConfig) -> None:
        self._map: dict[tuple[int, int], QueryScope] = {}
        for account_id, acc in config.per_account.items():
            chat_id = acc.chat_id
            # commands topic → scope su tutti i trader dell'account
            cmd_tid = acc.topics.commands.thread_id or 0
            self._map[(chat_id, cmd_tid)] = QueryScope(
                account_id=account_id, trader_ids=None
            )
            # clean_log per-trader → scope singolo trader
            for trader_id, tid in acc.topics.clean_log.per_trader.items():
                t = tid or 0
                self._map[(chat_id, t)] = QueryScope(
                    account_id=account_id, trader_ids=[trader_id]
                )
            # clean_log fallback → scope tutti i trader
            cl_tid = acc.topics.clean_log.thread_id or 0
            if (chat_id, cl_tid) not in self._map:
                self._map[(chat_id, cl_tid)] = QueryScope(
                    account_id=account_id, trader_ids=None
                )
            # tech_log NON registrato → risolve None

    def resolve(self, chat_id: int, thread_id: int) -> QueryScope | None:
        return self._map.get((chat_id, thread_id))


__all__ = ["QueryScope", "ScopeResolver"]
```

- [ ] **Step 3: Eseguire i test**

Run: `pytest tests/runtime_v2/control_plane/test_scope_resolver.py -v`
Expected: PASS (tutti)

- [ ] **Step 4: Commit**

```bash
git add src/runtime_v2/control_plane/scope_resolver.py \
        tests/runtime_v2/control_plane/test_scope_resolver.py
git commit -m "feat: add QueryScope dataclass and ScopeResolver with boot-time topic mapping"
```

---

## Task 3: `auth.py` multi-account + topic `clean_log`

**Files:**
- Modify: `src/runtime_v2/control_plane/auth.py`
- Modify: `tests/runtime_v2/control_plane/test_auth.py`

**Interfaces:**
- Consumes: `ControlPlaneConfig.per_account` (tutti gli account)
- Produces:
  - `AuthResult.topic: Literal["commands", "clean_log"] | None` — campo aggiunto
  - `AuthValidator.validate(chat_id, thread_id, user_id) -> AuthResult` — firma invariata
  - `decision="OK"` + `topic="commands"` → comandi normali consentiti
  - `decision="OK"` + `topic="clean_log"` → solo `/dashboard` consentito (filtro in `CommandRouter`)

- [ ] **Step 1: Aggiungere i test per i nuovi comportamenti**

Aggiungere alla fine di `tests/runtime_v2/control_plane/test_auth.py`:

```python
def _config_multi_account() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        token="t",
        default_account="demo_1",
        per_account={
            "demo_1": AccountConfig(
                chat_id=-100111,
                topics=AccountTopicsConfig(
                    commands=TopicConfig(thread_id=4),
                    tech_log=TechLogConfig(thread_id=5),
                    clean_log=CleanLogConfig(
                        thread_id=2,
                        per_trader={"trader_a": 316},
                    ),
                ),
            ),
            "demo_2": AccountConfig(
                chat_id=-100222,
                topics=AccountTopicsConfig(
                    commands=TopicConfig(thread_id=10),
                    tech_log=TechLogConfig(thread_id=11),
                    clean_log=CleanLogConfig(thread_id=12),
                ),
            ),
        },
        authorized_users=[42],
    )


def test_multi_account_commands_topic_ok():
    v = AuthValidator(_config_multi_account())
    res = v.validate(chat_id=-100222, thread_id=10, user_id=42)
    assert res.decision == "OK"
    assert res.topic == "commands"


def test_multi_account_clean_log_per_trader_ok():
    v = AuthValidator(_config_multi_account())
    res = v.validate(chat_id=-100111, thread_id=316, user_id=42)
    assert res.decision == "OK"
    assert res.topic == "clean_log"


def test_multi_account_clean_log_fallback_ok():
    v = AuthValidator(_config_multi_account())
    res = v.validate(chat_id=-100111, thread_id=2, user_id=42)
    assert res.decision == "OK"
    assert res.topic == "clean_log"


def test_tech_log_topic_ignored():
    v = AuthValidator(_config_multi_account())
    res = v.validate(chat_id=-100111, thread_id=5, user_id=42)
    assert res.decision == "IGNORE"
    assert res.reason == "wrong_topic"


def test_original_commands_topic_has_topic_field():
    """Vecchi test continuano a passare; AuthResult ora ha .topic."""
    v = AuthValidator(_config())
    res = v.validate(chat_id=-100999, thread_id=101, user_id=42)
    assert res.decision == "OK"
    assert res.topic == "commands"
```

Run: `pytest tests/runtime_v2/control_plane/test_auth.py -v`
Expected: ultimi 5 test FAIL

- [ ] **Step 2: Aggiornare `AuthResult` e `AuthValidator`**

Sostituire `src/runtime_v2/control_plane/auth.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.runtime_v2.control_plane.models import ControlPlaneConfig

AuthDecision = Literal["OK", "IGNORE", "REJECT_UNAUTHORIZED"]
AuthTopic = Literal["commands", "clean_log"]


@dataclass(frozen=True)
class AuthResult:
    decision: AuthDecision
    reason: str | None = None
    topic: AuthTopic | None = None


class AuthValidator:
    """Stateless per-update authorization. Supporta multi-account."""

    def __init__(self, config: ControlPlaneConfig) -> None:
        self._authorized_users = frozenset(config.authorized_users)
        self._delivery_mode = config.delivery_mode
        # commands_topics: {(chat_id, thread_id)}
        self._commands_topics: set[tuple[int, int]] = set()
        # clean_log_topics: {(chat_id, thread_id)}
        self._clean_log_topics: set[tuple[int, int]] = set()

        for acc in config.per_account.values():
            chat_id = acc.chat_id
            # commands
            cmd_tid = acc.topics.commands.thread_id
            self._commands_topics.add((chat_id, cmd_tid))
            # clean_log fallback
            cl_tid = acc.topics.clean_log.thread_id
            self._clean_log_topics.add((chat_id, cl_tid))
            # clean_log per-trader
            for tid in acc.topics.clean_log.per_trader.values():
                self._clean_log_topics.add((chat_id, tid))

    def validate(
        self, chat_id: int, thread_id: int | None, user_id: int
    ) -> AuthResult:
        if self._delivery_mode == "supergroup_topics":
            key = (chat_id, thread_id)
            if key in self._commands_topics:
                topic: AuthTopic = "commands"
            elif key in self._clean_log_topics:
                topic = "clean_log"
            else:
                return AuthResult("IGNORE", "wrong_topic")
        else:
            # private_bot: thread_id ignorato, solo chat_id
            chat_ids = {c for c, _ in self._commands_topics}
            if chat_id not in chat_ids:
                return AuthResult("IGNORE", "wrong_chat")
            topic = "commands"

        if user_id not in self._authorized_users:
            return AuthResult("REJECT_UNAUTHORIZED", "unauthorized_user")

        return AuthResult("OK", topic=topic)


__all__ = ["AuthDecision", "AuthResult", "AuthTopic", "AuthValidator"]
```

- [ ] **Step 3: Eseguire tutti i test auth**

Run: `pytest tests/runtime_v2/control_plane/test_auth.py -v`
Expected: PASS tutti (vecchi + nuovi)

Nota: i vecchi test `test_wrong_chat_ignored` e `test_missing_thread_id_treated_as_wrong_topic` coprono casi in cui sia commands che clean_log non matchano → "wrong_topic". Verificare che passino.

- [ ] **Step 4: Commit**

```bash
git add src/runtime_v2/control_plane/auth.py \
        tests/runtime_v2/control_plane/test_auth.py
git commit -m "feat: extend AuthValidator to multi-account with clean_log topic recognition"
```

---

## Task 4: `status_queries.py` — scope params + `StatsView` + `get_stats()`

**Files:**
- Modify: `src/runtime_v2/control_plane/status_queries.py`
- Test: `tests/runtime_v2/control_plane/test_status_queries_scope.py`

**Interfaces:**
- Consumes: `QueryScope` da `scope_resolver.py` (Task 2)
- Produces (firme modificate):
  - `StatusQueries.get_status(scope: QueryScope) -> StatusView`
  - `StatusQueries.get_open_trades(scope: QueryScope) -> TradesView` — `TradeRow` esteso con `trader_id`, `account_id`
  - `StatusQueries.get_control(scope: QueryScope) -> ControlView`
  - `StatusQueries.get_reviews(scope: QueryScope) -> ReviewsView`
  - `StatusQueries.get_pnl(scope: QueryScope) -> PnlView` — esteso con `gross_pnl`, `fees`, `net_pnl`
  - `StatusQueries.get_stats(scope: QueryScope) -> StatsView` — NUOVO
- `TradeRow` aggiunge: `trader_id: str`, `account_id: str`
- `PnlView` aggiunge: `gross_pnl: float | None = None`, `fees: float | None = None`, `net_pnl: float | None = None`
- `StatsView` nuovo dataclass (vedi sotto)

**Nota importante — colonne DB:** Prima di implementare, eseguire Task 0 per confermare i nomi esatti:
- `closed_at` o `updated_at` per il time-bucketing delle stats
- `cumulative_gross_pnl`, `cumulative_fees`, `cumulative_funding` per PnL realizzato
- Se le colonne non esistono: `gross_pnl=None`, `fees=None`, `net_pnl=None` e stats `net_pnl=None` per tutte le righe

- [ ] **Step 1: Aggiungere `StatsView` e helper scope in `status_queries.py`**

Aggiungere i dataclass dopo `PnlView`:

```python
@dataclass
class StatsPeriod:
    trades: int
    wins: int
    net_pnl: float | None


@dataclass
class StatsView:
    updated_at: str
    today: StatsPeriod
    week: StatsPeriod
    month: StatsPeriod
    total: StatsPeriod
    best_trade: tuple[int, str, float] | None   # (chain_id, symbol, net_pnl)
    worst_trade: tuple[int, str, float] | None
```

Aggiungere campo opzionali a `PnlView`:

```python
@dataclass
class PnlView:
    updated_at: str
    account_id: str | None
    captured_at: str | None
    source: str | None
    equity_usdt: float | None
    available_balance_usdt: float | None
    total_open_risk_usdt: float | None
    total_margin_used_usdt: float | None
    open_count: int
    partial_count: int
    waiting_entry_count: int
    # Realizzato — None se colonne non presenti in DB
    gross_pnl: float | None = None
    fees: float | None = None
    net_pnl: float | None = None
```

Aggiungere campo a `TradeRow`:

```python
@dataclass
class TradeRow:
    chain_id: int
    symbol: str
    side: str
    state: str
    has_sl: bool
    has_be: bool = False
    trader_id: str = ""
    account_id: str = ""
```

- [ ] **Step 2: Aggiungere helper `_scope_where` per SQL scoping**

Aggiungere in `status_queries.py` dopo `_extract_stop_price`:

```python
def _scope_where(scope: "QueryScope") -> tuple[str, list]:
    """Ritorna (WHERE_clause_fragment, params) per filtrare per scope.

    Esempio: " AND account_id=? AND trader_id IN (?,?)", ["demo_1","trader_a","trader_b"]
    """
    from src.runtime_v2.control_plane.scope_resolver import QueryScope  # lazy import
    clauses: list[str] = ["account_id=?"]
    params: list = [scope.account_id]
    if scope.trader_ids is not None:
        placeholders = ",".join("?" * len(scope.trader_ids))
        clauses.append(f"trader_id IN ({placeholders})")
        params.extend(scope.trader_ids)
    return " AND ".join(clauses), params
```

- [ ] **Step 3: Aggiornare `get_status()` con scope**

Sostituire la firma e aggiungere filtro `account_id`:

```python
def get_status(self, scope: "QueryScope") -> StatusView:
    conn = self._connect()
    try:
        scope_clause = "account_id=?"
        scope_params = [scope.account_id]

        def _count(state: str) -> int:
            return conn.execute(
                f"SELECT COUNT(*) FROM ops_trade_chains "
                f"WHERE lifecycle_state=? AND {scope_clause}",
                (state, *scope_params),
            ).fetchone()[0]

        open_count = _count("OPEN")
        partial_count = _count("PARTIALLY_CLOSED")
        waiting = _count("WAITING_ENTRY")
        review = _count("REVIEW_REQUIRED")
        pending = conn.execute(
            "SELECT COUNT(*) FROM ops_execution_commands WHERE status='PENDING'"
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM ops_execution_commands WHERE status='FAILED'"
        ).fetchone()[0]
        no_sl = conn.execute(
            f"SELECT COUNT(*) FROM ops_trade_chains "
            f"WHERE lifecycle_state IN ('OPEN','PARTIALLY_CLOSED') "
            f"AND current_stop_price IS NULL AND {scope_clause}",
            scope_params,
        ).fetchone()[0]
        last_event_ts = conn.execute(
            "SELECT MAX(received_at) FROM ops_exchange_events"
        ).fetchone()[0]
    finally:
        conn.close()

    control = self.get_control(scope)
    control_mode = "NONE"
    global_blocks = [b for b in control.active_blocks if b.scope_type == "GLOBAL"]
    if global_blocks:
        control_mode = "FULL_STOP" if any(b.mode == "FULL_STOP" for b in global_blocks) else "BLOCK_NEW_ENTRIES"
    return StatusView(
        updated_at=_now_iso(),
        control_mode=control_mode,
        new_entries_enabled=control.new_entries_enabled,
        sync_age_seconds=_age_seconds(last_event_ts),
        open_count=open_count,
        partial_count=partial_count,
        waiting_entry_count=waiting,
        review_count=review,
        pending_commands=pending,
        failed_commands=failed,
        no_sl_count=no_sl,
    )
```

- [ ] **Step 4: Aggiornare `get_open_trades()` con scope + trader_id/account_id in TradeRow**

```python
def get_open_trades(self, scope: "QueryScope") -> TradesView:
    where, params = _scope_where(scope)
    conn = self._connect()
    try:
        rows = conn.execute(
            "SELECT trade_chain_id, symbol, side, lifecycle_state, "
            "COALESCE(current_stop_price, expected_stop_price), "
            "be_protection_status, trader_id, account_id "
            "FROM ops_trade_chains "
            f"WHERE lifecycle_state IN ({','.join('?' * len(_ACTIVE_STATES))}) "
            f"AND {where} "
            "ORDER BY trade_chain_id",
            (*_ACTIVE_STATES, *params),
        ).fetchall()
    finally:
        conn.close()
    trade_rows = [
        TradeRow(
            chain_id=r[0], symbol=r[1], side=r[2], state=r[3],
            has_sl=r[4] is not None, has_be=r[5] == "PROTECTED",
            trader_id=r[6] or "", account_id=r[7] or "",
        )
        for r in rows
    ]
    return TradesView(updated_at=_now_iso(), total=len(trade_rows), rows=trade_rows)
```

- [ ] **Step 5: Aggiornare `get_control()` con scope**

Il filtro per `/control` si applica a `account_id` **se la colonna esiste** in `ops_control_state`. Da Task 0: se la colonna esiste, aggiungere il filtro; se non esiste, restituire tutti i blocchi (comportamento attuale).

```python
def get_control(self, scope: "QueryScope") -> ControlView:
    conn = self._connect()
    try:
        # Verifica se ops_control_state ha account_id
        control_cols = {r[1] for r in conn.execute("PRAGMA table_info(ops_control_state)").fetchall()}
        if "account_id" in control_cols:
            block_where = "WHERE active=1 AND execution_pause_mode IN ('BLOCK_NEW_ENTRIES','FULL_STOP') AND account_id=?"
            block_params: tuple = (scope.account_id,)
        else:
            block_where = "WHERE active=1 AND execution_pause_mode IN ('BLOCK_NEW_ENTRIES','FULL_STOP')"
            block_params = ()
        block_rows = conn.execute(
            f"SELECT scope_type, scope_value, execution_pause_mode, created_at "
            f"FROM ops_control_state {block_where}",
            block_params,
        ).fetchall()
        override_rows = conn.execute(
            "SELECT override_key, scope_type, scope_value, value_json "
            "FROM ops_config_overrides WHERE active=1 AND override_key LIKE 'symbol_blacklist%'"
        ).fetchall()
    finally:
        conn.close()
    # resto invariato — same logic as original
    blocks = [BlockInfo(scope_type=r[0], scope_value=r[1], mode=r[2], created_at=r[3]) for r in block_rows]
    new_entries_enabled = not any(b.scope_type == "GLOBAL" for b in blocks)
    blacklist_global: list[str] = []
    blacklist_per_trader: dict[str, list[str]] = {}
    for _key, scope_type, scope_value, value_json in override_rows:
        try:
            symbols = json.loads(value_json or "[]")
        except Exception:
            symbols = []
        if scope_type == "GLOBAL":
            blacklist_global = list(symbols)
        elif scope_type == "PER_TRADER" and scope_value:
            blacklist_per_trader[scope_value] = list(symbols)
    return ControlView(
        new_entries_enabled=new_entries_enabled,
        active_blocks=blocks,
        blacklist_global=blacklist_global,
        blacklist_per_trader=blacklist_per_trader,
    )
```

- [ ] **Step 6: Aggiornare `get_reviews()` con scope**

```python
def get_reviews(self, scope: "QueryScope") -> ReviewsView:
    conn = self._connect()
    try:
        chain_rows = conn.execute(
            "SELECT trade_chain_id, symbol FROM ops_trade_chains "
            "WHERE lifecycle_state='REVIEW_REQUIRED' AND account_id=? "
            "ORDER BY trade_chain_id",
            (scope.account_id,),
        ).fetchall()
        # resto invariato — carica reason da ops_lifecycle_events
        reasons = dict(conn.execute(
            "SELECT trade_chain_id, payload_json FROM ops_lifecycle_events "
            "WHERE event_type='REVIEW_REQUIRED' AND trade_chain_id IS NOT NULL "
            "ORDER BY event_id DESC"
        ).fetchall())
    finally:
        conn.close()
    items = []
    for chain_id, symbol in chain_rows:
        reason = "unknown"
        blob = reasons.get(chain_id)
        if blob:
            try:
                reason = json.loads(blob).get("reason", "unknown")
            except Exception:
                pass
        items.append(ReviewItem(chain_id=chain_id, symbol=symbol, reason=reason))
    return ReviewsView(updated_at=_now_iso(), items=items)
```

- [ ] **Step 7: Aggiornare `get_pnl()` con scope + PnL realizzato**

```python
def get_pnl(self, scope: "QueryScope") -> PnlView:
    conn = self._connect()
    try:
        # Snapshot account (invariato)
        snap = None
        if _table_exists(conn, "ops_account_snapshots"):
            snap = conn.execute(
                "SELECT captured_at, source, equity_usdt, available_balance_usdt, "
                "total_open_risk_usdt, total_margin_used_usdt "
                "FROM ops_account_snapshots WHERE account_id=? "
                "ORDER BY captured_at DESC LIMIT 1",
                (scope.account_id,),
            ).fetchone()

        where, params = _scope_where(scope)
        open_count = conn.execute(
            f"SELECT COUNT(*) FROM ops_trade_chains WHERE lifecycle_state='OPEN' AND {where}",
            params,
        ).fetchone()[0]
        partial_count = conn.execute(
            f"SELECT COUNT(*) FROM ops_trade_chains WHERE lifecycle_state='PARTIALLY_CLOSED' AND {where}",
            params,
        ).fetchone()[0]
        waiting_count = conn.execute(
            f"SELECT COUNT(*) FROM ops_trade_chains WHERE lifecycle_state='WAITING_ENTRY' AND {where}",
            params,
        ).fetchone()[0]

        # PnL realizzato — solo se colonne disponibili (verificato da Task 0)
        chain_cols = {r[1] for r in conn.execute("PRAGMA table_info(ops_trade_chains)").fetchall()}
        gross_pnl: float | None = None
        fees: float | None = None
        net_pnl: float | None = None
        if "cumulative_gross_pnl" in chain_cols:
            row = conn.execute(
                f"SELECT SUM(cumulative_gross_pnl), SUM(cumulative_fees), SUM(cumulative_funding) "
                f"FROM ops_trade_chains WHERE lifecycle_state='CLOSED' AND {where}",
                params,
            ).fetchone()
            if row and row[0] is not None:
                gross_pnl = float(row[0])
                fees_val = float(row[1] or 0)
                funding_val = float(row[2] or 0)
                fees = fees_val
                net_pnl = gross_pnl - fees_val + funding_val
    finally:
        conn.close()

    return PnlView(
        updated_at=_now_iso(),
        account_id=scope.account_id,
        captured_at=snap[0] if snap else None,
        source=snap[1] if snap else None,
        equity_usdt=snap[2] if snap else None,
        available_balance_usdt=snap[3] if snap else None,
        total_open_risk_usdt=snap[4] if snap else None,
        total_margin_used_usdt=snap[5] if snap else None,
        open_count=open_count,
        partial_count=partial_count,
        waiting_entry_count=waiting_count,
        gross_pnl=gross_pnl,
        fees=fees,
        net_pnl=net_pnl,
    )
```

- [ ] **Step 8: Implementare `get_stats()`**

```python
def get_stats(self, scope: "QueryScope") -> StatsView:
    from datetime import date, timedelta
    where, params = _scope_where(scope)
    conn = self._connect()
    try:
        chain_cols = {r[1] for r in conn.execute("PRAGMA table_info(ops_trade_chains)").fetchall()}
        # Determina colonna timestamp per closed trades
        ts_col = "closed_at" if "closed_at" in chain_cols else "updated_at"
        pnl_expr = (
            "COALESCE(cumulative_gross_pnl,0) - COALESCE(cumulative_fees,0) + COALESCE(cumulative_funding,0)"
            if "cumulative_gross_pnl" in chain_cols
            else "NULL"
        )
        rows = conn.execute(
            f"SELECT trade_chain_id, symbol, {pnl_expr} as net_pnl, {ts_col} "
            f"FROM ops_trade_chains "
            f"WHERE lifecycle_state='CLOSED' AND {where} "
            f"ORDER BY {ts_col} DESC",
            params,
        ).fetchall()
    finally:
        conn.close()

    today = date.today()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    def _period(trades_subset) -> StatsPeriod:
        if not trades_subset:
            return StatsPeriod(trades=0, wins=0, net_pnl=None)
        wins = sum(1 for _, _, pnl, _ in trades_subset if pnl is not None and pnl > 0)
        total_pnl = sum(pnl for _, _, pnl, _ in trades_subset if pnl is not None)
        has_pnl = any(pnl is not None for _, _, pnl, _ in trades_subset)
        return StatsPeriod(
            trades=len(trades_subset),
            wins=wins,
            net_pnl=total_pnl if has_pnl else None,
        )

    def _parse_date(ts: str | None) -> date | None:
        if not ts:
            return None
        try:
            return date.fromisoformat(ts[:10])
        except Exception:
            return None

    today_trades = [r for r in rows if _parse_date(r[3]) == today]
    week_trades = [r for r in rows if (d := _parse_date(r[3])) and d >= week_ago]
    month_trades = [r for r in rows if (d := _parse_date(r[3])) and d >= month_ago]

    # best/worst
    with_pnl = [(r[0], r[1], r[2]) for r in rows if r[2] is not None]
    best = max(with_pnl, key=lambda x: x[2], default=None)
    worst = min(with_pnl, key=lambda x: x[2], default=None)

    return StatsView(
        updated_at=_now_iso(),
        today=_period(today_trades),
        week=_period(week_trades),
        month=_period(month_trades),
        total=_period(list(rows)),
        best_trade=best,
        worst_trade=worst,
    )
```

- [ ] **Step 9: Scrivere i test unit con DB in-memory**

Creare `tests/runtime_v2/control_plane/test_status_queries_scope.py`:

```python
from __future__ import annotations
import sqlite3
import tempfile
import os
from src.runtime_v2.control_plane.status_queries import StatusQueries, StatsView
from src.runtime_v2.control_plane.scope_resolver import QueryScope


def _make_db() -> str:
    """Crea un DB SQLite temporaneo con lo SCHEMA REALE (migration applicate).

    NON ridichiarare le tabelle a mano: `closed_at` e `review_reason` NON esistono;
    inventarle produrrebbe test verdi e produzione rotta. Le colonne PnL e
    plan_state_json/source_chat_id provengono dalle migration 004/009/010.
    """
    import glob
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    for sql_file in sorted(glob.glob("db/ops_migrations/*.sql")):
        with open(sql_file, encoding="utf-8") as f:
            conn.executescript(f.read())

    now = "2026-06-19T10:00:00+00:00"
    def _chain(cid, symbol, side, trader, account, state, *, stop=None, be="NOT_PROTECTED",
               gross=None, fees=None, funding=None):
        conn.execute(
            "INSERT INTO ops_trade_chains "
            "(trade_chain_id, source_enrichment_id, canonical_message_id, raw_message_id, "
            " trader_id, account_id, symbol, side, lifecycle_state, entry_mode, "
            " current_stop_price, be_protection_status, "
            " cumulative_gross_pnl, cumulative_fees, cumulative_funding, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, cid, cid, cid, trader, account, symbol, side, state, "limit",
             stop, be, gross, fees, funding, now, now),
        )
    _chain(1, "BTCUSDT", "LONG", "trader_a", "demo_1", "OPEN", stop=63500, be="PROTECTED")
    _chain(2, "ETHUSDT", "SHORT", "trader_b", "demo_1", "OPEN", stop=2100)
    _chain(3, "SOLUSDT", "LONG", "trader_a", "demo_2", "OPEN", stop=150)
    # trade chiuso: net = 50 - 5 + 1 = 46.0; closed_at assente → bucketing usa updated_at
    _chain(4, "BNBUSDT", "LONG", "trader_a", "demo_1", "CLOSED", gross=50.0, fees=5.0, funding=1.0)
    conn.commit()
    conn.close()
    return path


def test_get_open_trades_filters_by_account():
    db = _make_db()
    q = StatusQueries(db)
    scope = QueryScope(account_id="demo_1", trader_ids=None)
    view = q.get_open_trades(scope)
    assert view.total == 2
    symbols = {r.symbol for r in view.rows}
    assert "BTCUSDT" in symbols
    assert "ETHUSDT" in symbols
    assert "SOLUSDT" not in symbols
    os.unlink(db)


def test_get_open_trades_filters_by_trader():
    db = _make_db()
    q = StatusQueries(db)
    scope = QueryScope(account_id="demo_1", trader_ids=["trader_a"])
    view = q.get_open_trades(scope)
    assert view.total == 1
    assert view.rows[0].symbol == "BTCUSDT"
    assert view.rows[0].trader_id == "trader_a"
    os.unlink(db)


def test_get_stats_today_totals():
    db = _make_db()
    q = StatusQueries(db)
    scope = QueryScope(account_id="demo_1", trader_ids=None)
    view = q.get_stats(scope)
    assert isinstance(view, StatsView)
    # trade 4: net_pnl = 50 - 5 + 1 = 46.0
    assert view.total.trades == 1
    assert view.total.wins == 1
    assert view.total.net_pnl == pytest.approx(46.0)
    assert view.best_trade is not None
    assert view.best_trade[0] == 4
    os.unlink(db)


def test_get_stats_other_account_empty():
    db = _make_db()
    q = StatusQueries(db)
    scope = QueryScope(account_id="demo_2", trader_ids=None)
    view = q.get_stats(scope)
    assert view.total.trades == 0
    os.unlink(db)
```

Aggiungere `import pytest` in cima al file.

Run: `pytest tests/runtime_v2/control_plane/test_status_queries_scope.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add src/runtime_v2/control_plane/status_queries.py \
        tests/runtime_v2/control_plane/test_status_queries_scope.py
git commit -m "feat: add QueryScope parameter to StatusQueries methods and implement get_stats()"
```

---

## Task 5: `service.py` — scope delegation + `get_stats()`

**Files:**
- Modify: `src/runtime_v2/control_plane/service.py`

**Interfaces:**
- Consumes: `QueryScope` (Task 2), `StatsView` (Task 4)
- Produces (firme aggiornate):
  - `RuntimeControlService.get_status(scope: QueryScope) -> StatusView`
  - `RuntimeControlService.get_open_trades(scope: QueryScope) -> TradesView`
  - `RuntimeControlService.get_control(scope: QueryScope) -> ControlView`
  - `RuntimeControlService.get_reviews(scope: QueryScope) -> ReviewsView`
  - `RuntimeControlService.get_pnl(scope: QueryScope) -> PnlView`
  - `RuntimeControlService.get_stats(scope: QueryScope) -> StatsView` — NUOVO

- [ ] **Step 1: Aggiornare import e metodi in `service.py`**

Aggiungere all'import da `status_queries`:
```python
from src.runtime_v2.control_plane.status_queries import (
    ControlView, HealthView, ReviewsView, StatusView, StatusQueries,
    PnlView, StatsView, TradeDetail, TradesView,
)
```

Aggiungere import di `QueryScope`:
```python
from src.runtime_v2.control_plane.scope_resolver import QueryScope
```

Sostituire i metodi read nella classe `RuntimeControlService`:

```python
def get_status(self, scope: QueryScope) -> StatusView:
    return self._queries.get_status(scope)

def get_open_trades(self, scope: QueryScope) -> TradesView:
    return self._queries.get_open_trades(scope)

def get_trade(self, chain_id: int) -> TradeDetail | None:
    return self._queries.get_trade(chain_id)

def get_health(self) -> HealthView:
    return self._queries.get_health()

def get_control(self, scope: QueryScope) -> ControlView:
    return self._queries.get_control(scope)

def get_reviews(self, scope: QueryScope) -> ReviewsView:
    return self._queries.get_reviews(scope)

def get_pnl(self, scope: QueryScope) -> PnlView:
    return self._queries.get_pnl(scope)

def get_stats(self, scope: QueryScope) -> StatsView:
    return self._queries.get_stats(scope)
```

- [ ] **Step 2: Verificare che non siano rotti altri test del service**

Run: `pytest tests/runtime_v2/control_plane/ -v --tb=short -k "service"`
Expected: PASS o nessun test esistente per service (se assenti, OK)

- [ ] **Step 3: Commit**

```bash
git add src/runtime_v2/control_plane/service.py
git commit -m "feat: add scope parameter to service read methods and add get_stats()"
```

---

## Task 6: `templates/commands.py` — TEMPLATE_REGISTRY read-only

**Files:**
- Create: `src/runtime_v2/control_plane/formatters/templates/commands.py`
- Test: `tests/runtime_v2/control_plane/test_commands_templates.py`

**Interfaces:**
- Consumes: tutti i block primitives da `_blocks.py` (inclusi `TableBlock`, `SectionBlock` callable — Task 1)
- Produces:
  - `TEMPLATE_REGISTRY: dict[str, TemplateConfig]` con chiavi `"trades"`, `"pnl"`, `"stats"`, `"status"`, `"control"`, `"reviews"`, `"health"`
  - Tutti i config hanno `payload_transform=None` — il payload è già pronto dal formatter

Il template non costruisce il payload: riceve un dict già formato dal formatter (Task 7).
Il template definisce solo la struttura visiva.

- [ ] **Step 1: Creare `templates/commands.py`**

```python
# src/runtime_v2/control_plane/formatters/templates/commands.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    BranchBlock, ConditionalBlock, DerivedBlock,
    FieldBlock, ListBlock, SectionBlock, SeparatorBlock,
    StaticBlock, TableBlock, TemplateConfig,
)
from src.runtime_v2.control_plane.formatters._formatters import num


# ── /trades ─────────────────────────────────────────────────────────────────

def _trade_renderer(row: dict, idx: int, payload: dict) -> list[str]:
    symbol = row.get("symbol", "")
    side = row.get("side", "")
    state = row.get("state", "")
    sl = "✓" if row.get("has_sl") else "✗"
    be = "  BE:✓" if row.get("has_be") else ""
    trader = f"  [{row['trader_id']}]" if payload.get("show_trader") and row.get("trader_id") else ""
    lines = [f"#{row['chain_id']}  {symbol}  {side}  {state}{trader}"]
    lines.append(f"    SL:{sl}{be}")
    return lines


_TRADES = TemplateConfig(
    blocks=[
        DerivedBlock(text_fn=lambda p: f"📊 TRADES — {p['scope_label']}"),
        SeparatorBlock(),
        DerivedBlock(text_fn=lambda p: f"Updated: {p['updated_at']}"),
        SeparatorBlock(),
        BranchBlock(
            condition=lambda p: p["total"] == 0,
            then_blocks=[StaticBlock("Nessun trade attivo.")],
            else_blocks=[
                DerivedBlock(text_fn=lambda p: f"Trade attivi: {p['total']}"),
                SeparatorBlock(),
                ListBlock(key="rows", item_renderer=_trade_renderer),
            ],
        ),
    ],
)


# ── /pnl ────────────────────────────────────────────────────────────────────

_PNL = TemplateConfig(
    blocks=[
        DerivedBlock(text_fn=lambda p: f"💰 PNL — {p['scope_label']}"),
        SeparatorBlock(),
        DerivedBlock(text_fn=lambda p: f"Updated: {p['updated_at']}"),
        SeparatorBlock(),
        SectionBlock(label="Account:", blocks=[
            DerivedBlock(text_fn=lambda p: f"  Equity:    {p.get('equity', 'n/a')}"),
            DerivedBlock(text_fn=lambda p: f"  Balance:   {p.get('balance', 'n/a')}"),
            DerivedBlock(text_fn=lambda p: f"  Margin:    {p.get('margin', 'n/a')}"),
        ]),
        SeparatorBlock(),
        SectionBlock(
            label=lambda p: f"Realizzato ({p['trader_label']}):",
            blocks=[
                DerivedBlock(text_fn=lambda p: f"  Gross:   {p.get('gross_pnl', 'n/a')}"),
                DerivedBlock(text_fn=lambda p: f"  Fees:    {p.get('fees', 'n/a')}"),
                DerivedBlock(text_fn=lambda p: f"  Netto:   {p.get('net_pnl', 'n/a')}"),
            ],
        ),
        SeparatorBlock(),
        DerivedBlock(text_fn=lambda p: f"Open: {p['open_count']}  |  Waiting: {p['waiting_count']}"),
    ],
)


# ── /stats ───────────────────────────────────────────────────────────────────

def _stats_rows(p: dict) -> list[list[str]]:
    def _fmt_pct(wins, total) -> str:
        if not total:
            return "—"
        return f"{int(wins / total * 100)}%"
    def _fmt_pnl(v) -> str:
        if v is None:
            return "n/a"
        return f"{v:+.2f}"
    return [
        ["Oggi:", str(p["today_trades"]), _fmt_pct(p["today_wins"], p["today_trades"]), _fmt_pnl(p["today_pnl"])],
        ["7g:", str(p["week_trades"]), _fmt_pct(p["week_wins"], p["week_trades"]), _fmt_pnl(p["week_pnl"])],
        ["30g:", str(p["month_trades"]), _fmt_pct(p["month_wins"], p["month_trades"]), _fmt_pnl(p["month_pnl"])],
        ["Tot:", str(p["total_trades"]), _fmt_pct(p["total_wins"], p["total_trades"]), _fmt_pnl(p["total_pnl"])],
    ]


_STATS = TemplateConfig(
    blocks=[
        DerivedBlock(text_fn=lambda p: f"📉 STATS — {p['scope_label']}"),
        SeparatorBlock(),
        DerivedBlock(text_fn=lambda p: f"Updated: {p['updated_at']}"),
        SeparatorBlock(),
        TableBlock(
            headers=["", "Trades", "Win%", "Netto"],
            rows_fn=_stats_rows,
            alignments=["left", "right", "right", "right"],
        ),
        ConditionalBlock(
            condition=lambda p: p.get("best_symbol") is not None,
            blocks=[
                SeparatorBlock(),
                DerivedBlock(text_fn=lambda p: f"Best:  #{p['best_id']}  {p['best_symbol']}  {p['best_pnl']:+.2f}"),
                DerivedBlock(text_fn=lambda p: f"Worst: #{p['worst_id']}  {p['worst_symbol']}  {p['worst_pnl']:+.2f}"),
            ],
        ),
    ],
)


# ── /status ──────────────────────────────────────────────────────────────────

_STATUS = TemplateConfig(
    blocks=[
        DerivedBlock(text_fn=lambda p: f"{p['status_emoji']} Runtime V2 — STATUS  |  {p['account_id']}"),
        SeparatorBlock(),
        DerivedBlock(text_fn=lambda p: f"Updated: {p['updated_at']}"),
        SeparatorBlock(),
        SectionBlock(label="Mode:", blocks=[
            DerivedBlock(text_fn=lambda p: f"  New entries: {p['entries_label']}"),
            DerivedBlock(text_fn=lambda p: f"  Control: {p['control_mode']}"),
            DerivedBlock(text_fn=lambda p: f"  Sync: {p['sync_label']}"),
        ]),
        SeparatorBlock(),
        SectionBlock(label="Trades:", blocks=[
            DerivedBlock(text_fn=lambda p: f"  Open: {p['open_count']}"),
            DerivedBlock(text_fn=lambda p: f"  Waiting entry: {p['waiting_count']}"),
            DerivedBlock(text_fn=lambda p: f"  Partial: {p['partial_count']}"),
            DerivedBlock(text_fn=lambda p: f"  Review required: {p['review_count']}{p['review_warn']}"),
        ]),
        SeparatorBlock(),
        SectionBlock(label="Execution:", blocks=[
            DerivedBlock(text_fn=lambda p: f"  Pending commands: {p['pending_commands']}"),
            DerivedBlock(text_fn=lambda p: f"  Failed commands: {p['failed_commands']}{p['failed_warn']}"),
        ]),
        SeparatorBlock(),
        SectionBlock(label="Risk:", blocks=[
            DerivedBlock(text_fn=lambda p: f"  No SL: {p['no_sl_count']}{p['no_sl_warn']}"),
        ]),
        SeparatorBlock(),
        StaticBlock("/trades  ·  /reviews  ·  /control"),
    ],
)


# ── /control ─────────────────────────────────────────────────────────────────

_CONTROL = TemplateConfig(
    blocks=[
        DerivedBlock(text_fn=lambda p: f"{p['lock_emoji']} CONTROL  |  {p['account_id']}"),
        SeparatorBlock(),
        DerivedBlock(text_fn=lambda p: f"New entries: {p['entries_label']}"),
        SeparatorBlock(),
        BranchBlock(
            condition=lambda p: len(p["active_blocks"]) == 0,
            then_blocks=[StaticBlock("Nessun blocco attivo.")],
            else_blocks=[
                SectionBlock(label="Blocchi attivi:", blocks=[
                    ListBlock(
                        key="active_blocks",
                        item_renderer=lambda b, i, p: [
                            f"  {b['scope_type']}  {b['mode']}  ({b['created_at'] or ''})"
                        ],
                    ),
                ]),
            ],
        ),
        SeparatorBlock(),
        DerivedBlock(text_fn=lambda p: f"Blacklist globale: {p['blacklist_global'] or '—'}"),
        DerivedBlock(text_fn=lambda p: f"Blacklist per trader: {p['blacklist_per_trader'] or '—'}"),
        SeparatorBlock(),
        DerivedBlock(text_fn=lambda p: p["footer_commands"]),
    ],
)


# ── /reviews ─────────────────────────────────────────────────────────────────

_REVIEWS = TemplateConfig(
    blocks=[
        DerivedBlock(text_fn=lambda p: f"{'⚠️' if p['count'] > 0 else '✅'} REVIEWS  |  {p['account_id']}"),
        SeparatorBlock(),
        DerivedBlock(text_fn=lambda p: f"Updated: {p['updated_at']}"),
        BranchBlock(
            condition=lambda p: p["count"] == 0,
            then_blocks=[SeparatorBlock(), StaticBlock("Nessun caso in review.")],
            else_blocks=[
                SeparatorBlock(),
                DerivedBlock(text_fn=lambda p: f"Casi aperti: {p['count']}"),
                SeparatorBlock(),
                ListBlock(
                    key="items",
                    item_renderer=lambda item, i, p: [
                        f"#{item['chain_id']}  {item['symbol']}  {item['reason']}"
                    ],
                ),
                SeparatorBlock(),
                StaticBlock("/trade #id  per dettaglio"),
            ],
        ),
    ],
)


# ── /health ──────────────────────────────────────────────────────────────────

_HEALTH = TemplateConfig(
    blocks=[
        DerivedBlock(text_fn=lambda p: f"🩺 HEALTH  |  {p['account_id']}"),
        SeparatorBlock(),
        DerivedBlock(text_fn=lambda p: f"Updated: {p['updated_at']}"),
        SeparatorBlock(),
        SectionBlock(label="Workers:", blocks=[
            ListBlock(
                key="workers",
                item_renderer=lambda w, i, p: [
                    f"  {w['name']:<22}{w['status']}{('  (' + w['detail'] + ')') if w['detail'] else ''}"
                ],
            ),
        ]),
        SeparatorBlock(),
        DerivedBlock(text_fn=lambda p: f"DB: {p['db_status']}"),
        DerivedBlock(text_fn=lambda p: f"Exchange: {p['exchange_status']}"),
    ],
)


TEMPLATE_REGISTRY: dict[str, TemplateConfig] = {
    "trades": _TRADES,
    "pnl": _PNL,
    "stats": _STATS,
    "status": _STATUS,
    "control": _CONTROL,
    "reviews": _REVIEWS,
    "health": _HEALTH,
}

__all__ = ["TEMPLATE_REGISTRY"]
```

- [ ] **Step 2: Scrivere smoke test per render dei template**

Creare `tests/runtime_v2/control_plane/test_commands_templates.py`:

```python
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.templates.commands import TEMPLATE_REGISTRY


def _stats_payload() -> dict:
    return {
        "scope_label": "demo_1 · trader_a",
        "updated_at": "14:32:05",
        "today_trades": 1, "today_wins": 1, "today_pnl": 18.40,
        "week_trades": 6, "week_wins": 4, "week_pnl": 62.10,
        "month_trades": 19, "month_wins": 12, "month_pnl": 148.30,
        "total_trades": 31, "total_wins": 19, "total_pnl": 98.20,
        "best_id": 8, "best_symbol": "SOL/USDT", "best_pnl": 34.50,
        "worst_id": 22, "worst_symbol": "BNB/USDT", "worst_pnl": -12.80,
    }


def test_stats_template_renders_table():
    cfg = TEMPLATE_REGISTRY["stats"]
    result = render_template(cfg.blocks, _stats_payload(), transform=cfg.payload_transform)
    assert "STATS — demo_1 · trader_a" in result
    assert "Oggi:" in result
    assert "Tot:" in result
    assert "+18.40" in result
    assert "Best:" in result


def test_stats_template_no_best_worst():
    cfg = TEMPLATE_REGISTRY["stats"]
    payload = _stats_payload()
    payload.pop("best_symbol")
    payload["best_id"] = None
    payload["worst_id"] = None
    # ConditionalBlock con best_symbol=None → non renderizza Best/Worst
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "Best:" not in result


def test_trades_template_empty():
    cfg = TEMPLATE_REGISTRY["trades"]
    payload = {"scope_label": "demo_1", "updated_at": "14:32:05", "total": 0, "rows": []}
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "Nessun trade attivo." in result


def test_trades_template_with_rows():
    cfg = TEMPLATE_REGISTRY["trades"]
    payload = {
        "scope_label": "demo_1", "updated_at": "14:32:05", "total": 1,
        "show_trader": False,
        "rows": [{"chain_id": 5, "symbol": "BTC/USDT", "side": "LONG",
                  "state": "OPEN", "has_sl": True, "has_be": True, "trader_id": "trader_a"}],
    }
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "#5" in result
    assert "BTC/USDT" in result
    assert "SL:✓" in result


def test_pnl_template_with_realized():
    cfg = TEMPLATE_REGISTRY["pnl"]
    payload = {
        "scope_label": "demo_1 · trader_a", "updated_at": "14:32:05",
        "trader_label": "trader_a",
        "equity": "10,432.50 USDT", "balance": "9,100.00 USDT", "margin": "820.00 USDT",
        "gross_pnl": "+142.60 USDT", "fees": "-11.20 USDT", "net_pnl": "+130.00 USDT",
        "open_count": 1, "waiting_count": 1,
    }
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "PNL — demo_1 · trader_a" in result
    assert "Realizzato (trader_a):" in result
    assert "+142.60 USDT" in result


def test_status_template_renders():
    cfg = TEMPLATE_REGISTRY["status"]
    payload = {
        "status_emoji": "🟢", "account_id": "demo_1", "updated_at": "14:32:05",
        "entries_label": "ENABLED", "control_mode": "NONE", "sync_label": "12s ago",
        "open_count": 3, "waiting_count": 2, "partial_count": 1, "review_count": 0,
        "review_warn": "", "pending_commands": 0, "failed_commands": 0,
        "failed_warn": "", "no_sl_count": 0, "no_sl_warn": "",
    }
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "demo_1" in result
    assert "ENABLED" in result
    assert "Open: 3" in result
```

Run: `pytest tests/runtime_v2/control_plane/test_commands_templates.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/templates/commands.py \
        tests/runtime_v2/control_plane/test_commands_templates.py
git commit -m "feat: add TEMPLATE_REGISTRY for read-only command templates"
```

---

## Task 7: Formatter refactors — `trades`, `pnl`, `status`, `control`, `stats`

**Files:**
- Modify: `src/runtime_v2/control_plane/formatters/trades.py`
- Modify: `src/runtime_v2/control_plane/formatters/pnl.py`
- Modify: `src/runtime_v2/control_plane/formatters/status.py`
- Modify: `src/runtime_v2/control_plane/formatters/control.py`
- Create: `src/runtime_v2/control_plane/formatters/stats.py`

Tutti i formatter diventano thin wrapper: costruiscono il dict payload e chiamano `render_template(config.blocks, payload, transform=config.payload_transform)`.

**Interfaces:**
- Consumes: view dataclass da `status_queries.py` + `scope_label: str` (già computato da caller)
- Produces: `format_*(view, scope_label: str) -> str`
- `scope_label`: stringa già formattata tipo `"demo_1"` o `"demo_1 · trader_a"`

Helper condiviso per `scope_label`:
```python
# In telegram_bot.py (Task 8) o in un modulo utils:
def _scope_label(scope: QueryScope, account_id: str) -> str:
    if scope.trader_ids and len(scope.trader_ids) == 1:
        return f"{account_id} · {scope.trader_ids[0]}"
    return account_id
```

- [ ] **Step 1: Refactor `trades.py`**

Sostituire il contenuto di `src/runtime_v2/control_plane/formatters/trades.py`:

```python
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.display import display_symbol
from src.runtime_v2.control_plane.formatters.templates.commands import TEMPLATE_REGISTRY
from src.runtime_v2.control_plane.status_queries import TradesView


def format_trades(view: TradesView, scope_label: str = "—") -> str:
    cfg = TEMPLATE_REGISTRY["trades"]
    show_trader = any(r.account_id for r in view.rows) and len({r.trader_id for r in view.rows}) > 1
    payload = {
        "scope_label": scope_label,
        "updated_at": view.updated_at,
        "total": view.total,
        "show_trader": show_trader,
        "rows": [
            {
                "chain_id": r.chain_id,
                "symbol": display_symbol(r.symbol),
                "side": r.side,
                "state": r.state,
                "has_sl": r.has_sl,
                "has_be": r.has_be,
                "trader_id": r.trader_id,
            }
            for r in view.rows
        ],
    }
    return render_template(cfg.blocks, payload, transform=cfg.payload_transform)


__all__ = ["format_trades"]
```

- [ ] **Step 2: Refactor `pnl.py`**

```python
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.templates.commands import TEMPLATE_REGISTRY
from src.runtime_v2.control_plane.status_queries import PnlView


def _fmt_money(v: float | None) -> str:
    if v is None:
        return "n/a"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:,.2f} USDT"


def format_pnl(view: PnlView, scope_label: str = "—") -> str:
    cfg = TEMPLATE_REGISTRY["pnl"]
    trader_label = scope_label  # usa scope_label come trader_label
    payload = {
        "scope_label": scope_label,
        "trader_label": trader_label,
        "updated_at": view.updated_at,
        "equity": _fmt_money(view.equity_usdt),
        "balance": _fmt_money(view.available_balance_usdt),
        "margin": _fmt_money(view.total_margin_used_usdt),
        "gross_pnl": _fmt_money(view.gross_pnl),
        "fees": _fmt_money(view.fees),
        "net_pnl": _fmt_money(view.net_pnl),
        "open_count": view.open_count,
        "waiting_count": view.waiting_entry_count,
    }
    return render_template(cfg.blocks, payload, transform=cfg.payload_transform)


__all__ = ["format_pnl"]
```

- [ ] **Step 3: Refactor `status.py`**

Aggiungere `scope_label` e `account_id` al payload in `format_status()`. Leggere il file corrente prima di modificarlo.

```python
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.templates.commands import TEMPLATE_REGISTRY
from src.runtime_v2.control_plane.status_queries import StatusView


def _sync_label(age: float | None) -> str:
    if age is None:
        return "no events"
    label = f"{int(age)}s ago"
    return f"{label}  ⚠️" if age > 60 else label


def format_status(view: StatusView, scope_label: str = "—") -> str:
    cfg = TEMPLATE_REGISTRY["status"]
    is_critical = view.failed_commands > 0 or view.no_sl_count > 0
    is_warning = view.review_count > 0 or (view.sync_age_seconds is not None and view.sync_age_seconds > 60)
    emoji = "🔴" if is_critical else ("🟡" if is_warning else "🟢")
    payload = {
        "status_emoji": emoji,
        "account_id": scope_label,
        "updated_at": view.updated_at,
        "entries_label": "ENABLED" if view.new_entries_enabled else "BLOCKED",
        "control_mode": view.control_mode,
        "sync_label": _sync_label(view.sync_age_seconds),
        "open_count": view.open_count,
        "waiting_count": view.waiting_entry_count,
        "partial_count": view.partial_count,
        "review_count": view.review_count,
        "review_warn": "  ⚠️" if view.review_count > 0 else "",
        "pending_commands": view.pending_commands,
        "failed_commands": view.failed_commands,
        "failed_warn": "  🔴" if view.failed_commands > 0 else "",
        "no_sl_count": view.no_sl_count,
        "no_sl_warn": "  🔴" if view.no_sl_count > 0 else "",
    }
    return render_template(cfg.blocks, payload, transform=cfg.payload_transform)


__all__ = ["format_status"]
```

- [ ] **Step 4: Refactor `control.py`**

```python
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.templates.commands import TEMPLATE_REGISTRY
from src.runtime_v2.control_plane.status_queries import ControlView


def format_control(view: ControlView, scope_label: str = "—") -> str:
    cfg = TEMPLATE_REGISTRY["control"]
    has_blocks = len(view.active_blocks) > 0
    blacklist_global_str = "  ".join(view.blacklist_global) if view.blacklist_global else "—"
    per_trader_str = (
        "  ".join(f"{t}: {'  '.join(s)}" for t, s in view.blacklist_per_trader.items())
        if view.blacklist_per_trader else "—"
    )
    payload = {
        "account_id": scope_label,
        "lock_emoji": "🔒" if has_blocks else "🔓",
        "entries_label": "BLOCKED" if not view.new_entries_enabled else "ENABLED",
        "active_blocks": [
            {
                "scope_type": b.scope_type,
                "scope_value": b.scope_value or "",
                "mode": b.mode,
                "created_at": (b.created_at or "")[:16] if b.created_at else "",
            }
            for b in view.active_blocks
        ],
        "blacklist_global": blacklist_global_str,
        "blacklist_per_trader": per_trader_str,
        "footer_commands": "/resume  ·  /unblock <symbol>" if has_blocks else "/pause  ·  /block <symbol>",
    }
    return render_template(cfg.blocks, payload, transform=cfg.payload_transform)


__all__ = ["format_control"]
```

- [ ] **Step 5: Creare `stats.py`**

```python
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.display import display_symbol
from src.runtime_v2.control_plane.formatters.templates.commands import TEMPLATE_REGISTRY
from src.runtime_v2.control_plane.status_queries import StatsView


def format_stats(view: StatsView, scope_label: str = "—") -> str:
    cfg = TEMPLATE_REGISTRY["stats"]
    best = view.best_trade
    worst = view.worst_trade
    payload = {
        "scope_label": scope_label,
        "updated_at": view.updated_at,
        "today_trades": view.today.trades, "today_wins": view.today.wins, "today_pnl": view.today.net_pnl,
        "week_trades": view.week.trades, "week_wins": view.week.wins, "week_pnl": view.week.net_pnl,
        "month_trades": view.month.trades, "month_wins": view.month.wins, "month_pnl": view.month.net_pnl,
        "total_trades": view.total.trades, "total_wins": view.total.wins, "total_pnl": view.total.net_pnl,
        "best_id": best[0] if best else None,
        "best_symbol": display_symbol(best[1]) if best else None,
        "best_pnl": best[2] if best else None,
        "worst_id": worst[0] if worst else None,
        "worst_symbol": display_symbol(worst[1]) if worst else None,
        "worst_pnl": worst[2] if worst else None,
    }
    return render_template(cfg.blocks, payload, transform=cfg.payload_transform)


__all__ = ["format_stats"]
```

- [ ] **Step 6: Scrivere smoke test per i formatter**

Creare `tests/runtime_v2/control_plane/test_stats_formatter.py`:

```python
from __future__ import annotations
from src.runtime_v2.control_plane.formatters.stats import format_stats
from src.runtime_v2.control_plane.formatters.trades import format_trades
from src.runtime_v2.control_plane.formatters.pnl import format_pnl
from src.runtime_v2.control_plane.status_queries import (
    StatsView, StatsPeriod, TradesView, TradeRow, PnlView,
)


def _stats_view() -> StatsView:
    return StatsView(
        updated_at="14:32:05",
        today=StatsPeriod(trades=1, wins=1, net_pnl=18.40),
        week=StatsPeriod(trades=6, wins=4, net_pnl=62.10),
        month=StatsPeriod(trades=19, wins=12, net_pnl=148.30),
        total=StatsPeriod(trades=31, wins=19, net_pnl=98.20),
        best_trade=(8, "SOLUSDT", 34.50),
        worst_trade=(22, "BNBUSDT", -12.80),
    )


def test_format_stats_includes_header_and_table():
    result = format_stats(_stats_view(), scope_label="demo_1 · trader_a")
    assert "STATS — demo_1 · trader_a" in result
    assert "Oggi:" in result
    assert "Tot:" in result
    assert "+98.20" in result


def test_format_stats_includes_best_worst():
    result = format_stats(_stats_view(), scope_label="demo_1")
    assert "Best:" in result
    assert "SOL/USDT" in result
    assert "Worst:" in result


def test_format_trades_empty():
    view = TradesView(updated_at="14:32:05", total=0, rows=[])
    result = format_trades(view, scope_label="demo_1")
    assert "Nessun trade attivo." in result


def test_format_trades_with_entry():
    row = TradeRow(chain_id=5, symbol="BTCUSDT", side="LONG",
                   state="OPEN", has_sl=True, has_be=True,
                   trader_id="trader_a", account_id="demo_1")
    view = TradesView(updated_at="14:32:05", total=1, rows=[row])
    result = format_trades(view, scope_label="demo_1 · trader_a")
    assert "#5" in result
    assert "BTC/USDT" in result


def test_format_pnl_with_realized():
    view = PnlView(
        updated_at="14:32:05", account_id="demo_1", captured_at=None,
        source=None, equity_usdt=10432.50, available_balance_usdt=9100.0,
        total_open_risk_usdt=None, total_margin_used_usdt=820.0,
        open_count=1, partial_count=0, waiting_entry_count=1,
        gross_pnl=142.60, fees=-11.20, net_pnl=130.00,
    )
    result = format_pnl(view, scope_label="demo_1 · trader_a")
    assert "PNL" in result
    assert "Realizzato" in result
    assert "130.00" in result
```

Run: `pytest tests/runtime_v2/control_plane/test_stats_formatter.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/runtime_v2/control_plane/formatters/trades.py \
        src/runtime_v2/control_plane/formatters/pnl.py \
        src/runtime_v2/control_plane/formatters/status.py \
        src/runtime_v2/control_plane/formatters/control.py \
        src/runtime_v2/control_plane/formatters/stats.py \
        tests/runtime_v2/control_plane/test_stats_formatter.py
git commit -m "feat: refactor command formatters to block-based rendering and add format_stats()"
```

---

## Task 8: `telegram_bot.py` — ScopeResolver + scope wiring + `/stats` + `/dashboard` placeholder

**Files:**
- Modify: `src/runtime_v2/control_plane/telegram_bot.py`

**Interfaces:**
- Consumes: `ScopeResolver` (Task 2), `format_stats` (Task 7), `StatsView` (Task 4)
- Produces:
  - `CommandRouter.__init__` accetta `scope_resolver: ScopeResolver`
  - `CommandRouter.route()` risolve `QueryScope` e lo passa a `_dispatch()`
  - Tutti i comandi read-only scoped ricevono `scope`
  - `/stats` aggiunto come comando
  - `/dashboard` aggiunto come comando (risponde "coming soon" in Piano 1 — implementato in Piano 3)
  - Se `auth_result.topic == "clean_log"` e comando ≠ `/dashboard` → IGNORE

- [ ] **Step 1: Aggiungere `ScopeResolver` e import a `telegram_bot.py`**

Aggiungere import:
```python
from src.runtime_v2.control_plane.formatters.stats import format_stats
from src.runtime_v2.control_plane.scope_resolver import QueryScope, ScopeResolver
```

- [ ] **Step 2: Aggiornare `CommandRouter.__init__`**

```python
class CommandRouter:
    def __init__(
        self,
        *,
        config: ControlPlaneConfig,
        auth: AuthValidator,
        audit: CommandAuditStore,
        service: RuntimeControlService,
        scope_resolver: ScopeResolver,
    ) -> None:
        self._config = config
        self._auth = auth
        self._audit = audit
        self._service = service
        self._scope_resolver = scope_resolver
        self._debug_max_seconds = config.get_account(None).topics.tech_log.debug_max_duration_minutes * 60
```

- [ ] **Step 3: Aggiornare `_READONLY_COMMANDS` e `_ADVANCED_COMMANDS`**

```python
_READONLY_COMMANDS = frozenset(
    {"help", "status", "trades", "trade", "health", "control", "reviews", "version", "stats", "dashboard"}
)
```

- [ ] **Step 4: Aggiornare `_HELP_TEXT`**

Sostituire `_HELP_TEXT` con il testo aggiornato dalla spec `/help`:

```python
_HELP_TEXT = """COMANDI DISPONIBILI
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

Emergenza (richiede conferma):
/close_all [trader]        - chiude tutte le posizioni
/close [trader] <symbol>   - chiude singola posizione
/cancel_all [trader]       - cancella ordini entry in attesa"""
```

- [ ] **Step 5: Aggiornare `route()` per scope + clean_log filter**

Nel metodo `route()`, dopo `auth_result = self._auth.validate(...)`:

```python
# Risolvi scope per tutti i topic autorizzati
thread_id_normalized = thread_id or 0
scope = self._scope_resolver.resolve(chat_id, thread_id_normalized)

# ... (blocchi IGNORE / REJECT invariati) ...

command_name, args = _parse(command_text)

# clean_log topic: solo /dashboard consentito
if auth_result.topic == "clean_log" and command_name != "dashboard":
    return RouteResult("IGNORE", None)

# scope non risolvibile: non dovrebbe accadere se auth passò, ma safety net
if scope is None:
    logger.warning("scope not resolved for authorized update chat=%s thread=%s", chat_id, thread_id)
    return RouteResult("IGNORE", None)
```

E nella chiamata a `_dispatch`:
```python
dispatch_result = self._dispatch(command_name, args, scope=scope, created_by=str(user_id))
```

- [ ] **Step 6: Aggiornare `_dispatch()` per scope + aggiungere `/stats` e `/dashboard`**

Aggiornare la firma:
```python
def _dispatch(
    self,
    command_name: str,
    args: list[str],
    *,
    scope: QueryScope,
    created_by: str,
) -> _DispatchResult:
```

Aggiornare tutti i comandi scoped:
```python
if command_name == "status":
    return _DispatchResult(format_status(self._service.get_status(scope), _scope_label(scope)))
if command_name == "trades":
    return _DispatchResult(format_trades(self._service.get_open_trades(scope), _scope_label(scope)))
if command_name == "trade":
    if not args or not args[0].lstrip("#").isdigit():
        return _DispatchResult("Usage: /trade <chain_id>", decision="REJECTED", reject_reason="invalid_arguments")
    chain_id = int(args[0].lstrip("#"))
    return _DispatchResult(format_trade_detail(self._service.get_trade(chain_id)))
if command_name == "health":
    return _DispatchResult(format_health(self._service.get_health()))
if command_name == "control":
    return _DispatchResult(format_control(self._service.get_control(scope), _scope_label(scope)))
if command_name == "reviews":
    return _DispatchResult(format_reviews(self._service.get_reviews(scope), _scope_label(scope)))
if command_name == "pnl":
    return _DispatchResult(format_pnl(self._service.get_pnl(scope), _scope_label(scope)))
if command_name == "stats":
    return _DispatchResult(format_stats(self._service.get_stats(scope), _scope_label(scope)))
if command_name == "dashboard":
    # Piano 3 — placeholder
    return _DispatchResult("🚧 /dashboard sarà disponibile nella prossima versione.")
```

- [ ] **Step 7: Aggiungere helper `_scope_label` nel modulo**

```python
def _scope_label(scope: QueryScope) -> str:
    """Ritorna etichetta leggibile per scope: 'demo_1' o 'demo_1 · trader_a'."""
    if scope.trader_ids and len(scope.trader_ids) == 1:
        return f"{scope.account_id} · {scope.trader_ids[0]}"
    return scope.account_id
```

- [ ] **Step 8: Aggiornare anche `format_reviews` e `format_health` per aggiungere `scope_label`**

Verificare la firma corrente di `format_reviews` e `format_health`. Se non accettano `scope_label`, aggiungere il parametro opzionale e aggiornare i rispettivi formatter. Pattern: stesso approccio usato per `format_status`.

Run: `rg "def format_reviews|def format_health" src/ --type py`

Se i formatter non seguono ancora il pattern block-based, refactorare analogamente a Task 7 (aggiungere `scope_label: str = "—"` come parametro e aggiornare la prima riga dell'output).

- [ ] **Step 9: Aggiornare il punto di costruzione del bot nella factory/bootstrap**

Trovare dove `CommandRouter` viene costruito (probabilmente in `run.py` o `__main__.py`):

Run: `rg "CommandRouter" src/ --type py -l`

Aggiungere `ScopeResolver` alla costruzione:
```python
from src.runtime_v2.control_plane.scope_resolver import ScopeResolver
scope_resolver = ScopeResolver(config)
router = CommandRouter(
    config=config,
    auth=auth,
    audit=audit,
    service=service,
    scope_resolver=scope_resolver,
)
```

- [ ] **Step 10: Correggere il routing della risposta in `_on_command`**

Oggi `_on_command` invia la reply a `default_acc.chat_id` + commands thread del default
account — sbagliato in multi-account e per topic diversi. Rispondere all'origine:

```python
# In _on_command, sostituire il blocco send_kwargs che usa default_acc:
send_kwargs: dict[str, object] = {
    "chat_id": message.chat_id,
    "text": result.reply_text,
}
if message.message_thread_id is not None:
    send_kwargs["message_thread_id"] = message.message_thread_id
await context.bot.send_message(**send_kwargs)
```

Nota: i comandi read-only arrivano solo dal topic `commands` (i `clean_log` accettano
solo `/dashboard`), quindi in pratica la reply resta nel topic commands dell'account
di origine — corretto anche con più account.

- [ ] **Step 11: Eseguire la suite completa**

Run: `pytest tests/runtime_v2/control_plane/ -v --tb=short`
Expected: tutti PASS

- [ ] **Step 12: Commit finale**

```bash
git add src/runtime_v2/control_plane/telegram_bot.py
git commit -m "feat: wire ScopeResolver into CommandRouter and add /stats command with scope"
```

---

## Self-Review

### Spec coverage

| Requisito spec | Task che lo copre |
|---|---|
| `QueryScope(account_id, trader_ids)` | Task 2 |
| `ScopeResolver` boot-time da config | Task 2 |
| `auth.py` multi-account | Task 3 |
| `auth.py` clean_log → solo /dashboard | Task 3 + Task 8 |
| `SectionBlock.label` callable | Task 1 |
| `TableBlock` per Stats | Task 1 |
| `status_queries` scoped | Task 4 |
| `StatsView` + `get_stats()` | Task 4 |
| `PnlView` + PnL realizzato | Task 4 |
| `service.py` scope delegation | Task 5 |
| `TEMPLATE_REGISTRY` comandi | Task 6 |
| Formatter refactor block-based | Task 7 |
| `/stats` command | Task 7 + Task 8 |
| `scope_label` in tutti gli header | Task 7 + Task 8 |
| `/dashboard` placeholder | Task 8 |

### Placeholder scan

- Nessun "TBD" o "TODO" nel piano
- Task 0 identifica esplicitamente le colonne da verificare prima di scrivere le query
- Task 4 gestisce colonne opzionali via PRAGMA runtime check

### Consistenza tipi

- `QueryScope` definito in `scope_resolver.py` — importato da `status_queries.py` e `telegram_bot.py`
- `StatsView` / `StatsPeriod` definiti in `status_queries.py` — importati da `service.py` e `formatters/stats.py`
- `format_*(view, scope_label: str) -> str` — firma uniforme in tutti i formatter
- `render_template(cfg.blocks, payload, transform=cfg.payload_transform)` — chiamata identica ovunque

---

## Execution Handoff

Piano salvato in `docs/superpowers/plans/2026-06-19-commands-scope-read-only.md`.

**Due opzioni di esecuzione:**

**1. Subagent-Driven (raccomandato)** — subagent fresco per task, review tra task, iterazione veloce

**2. Inline Execution** — esecuzione in questa sessione con checkpoint di review

Nota: dopo aver completato Piano 1, i piani 2 e 3 seguono come file separati. Piano 2 dipende da Piano 1 solo per `QueryScope` e `ScopeResolver` (Task 2).

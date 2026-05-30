# Control Plane Spec Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allineare il Control Plane runtime_v2 alle tre spec `COMMANDS_SPEC`, `TECH_LOG_SPEC` e `CLEAN_LOG_SPEC`, chiudendo i gap reali emersi dall'audit del 2026-05-30.

**Architecture:** La chiusura va eseguita in tre workstream dipendenti. Prima si completa il contratto `COMMANDS` e lo stato runtime mancante (`/pnl`, debug reale, startup/snapshot/bootstrap); poi si porta `TECH_LOG` dal formatter minimale a un canale governato da policy (`enabled`, `min_level`, debug, operational events); infine si completa `CLEAN_LOG`, che oggi è il delta più grande perché manca sia copertura eventi sia tracking/aggregazione per reply-root e update compositi.

**Tech Stack:** Python 3.12, asyncio, sqlite3, python-telegram-bot, pytest, raw SQL migrations in `db/ops_migrations/`.

---

## Scope Check

Le spec coprono tre sottosistemi distinti ma collegati:

1. `COMMANDS` e lifecycle control runtime
2. `TECH_LOG` e policy di invio tecnico
3. `CLEAN_LOG` e timeline operativa trade

Il piano resta unico solo perché i tre flussi condividono `outbox`, bootstrap e config. L'esecuzione va comunque fatta per task autonomi e verificabili.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/runtime_v2/control_plane/snapshot_store.py` | Persist/read di `ops_runtime_snapshot`. |
| `src/runtime_v2/control_plane/startup.py` | Decisione pura `auto | standby | restore`. |
| `src/runtime_v2/control_plane/debug_controller.py` | Stato debug con expiry. |
| `src/runtime_v2/control_plane/bootstrap.py` | Wiring unico control-plane. |
| `src/runtime_v2/control_plane/service.py` | `/pnl`, log, debug, startup/shutdown payload, snapshot hooks. |
| `src/runtime_v2/control_plane/status_queries.py` | Dati esposti a `/status`, `/trades`, `/trade`, `/health`, `/reviews`, `/pnl`. |
| `src/runtime_v2/control_plane/telegram_bot.py` | Routing comandi avanzati e reply contract. |
| `src/runtime_v2/control_plane/notification_dispatcher.py` | Policy `TECH_LOG`, render `CLEAN_LOG`, tracking send/reply. |
| `src/runtime_v2/control_plane/outbox_writer.py` | Projection payload per `CLEAN_LOG` e `TECH_LOG`. |
| `src/runtime_v2/control_plane/formatters/clean_log.py` | Tutti i messaggi `CLEAN_LOG` previsti dalle spec. |
| `src/runtime_v2/control_plane/formatters/tech_log.py` | Render strutturato `TECH_LOG`, inclusi private-bot prefix e context blocks. |
| `src/runtime_v2/control_plane/formatters/pnl.py` | Reply `/pnl`. |
| `src/runtime_v2/control_plane/formatters/debug.py` | Reply `/debug_on`, `/debug_off`. |
| `db/ops_migrations/008_ops_clean_log_tracking.sql` | Tracking root/reply message ids e grouping `CLEAN_LOG`. |
| `tests/runtime_v2/control_plane/test_snapshot_store.py` | Snapshot runtime. |
| `tests/runtime_v2/control_plane/test_startup.py` | Startup resolver. |
| `tests/runtime_v2/control_plane/test_debug_controller.py` | Debug duration/expiry. |
| `tests/runtime_v2/control_plane/test_command_router_advanced.py` | `/pnl`, `/logs`, `/debug_*`. |
| `tests/runtime_v2/control_plane/test_tech_log_policy.py` | `enabled`, `min_level`, debug, operational events, rate limit. |
| `tests/runtime_v2/control_plane/test_clean_log_formatter_full.py` | Copertura eventi `CLEAN_LOG` mancanti. |
| `tests/runtime_v2/control_plane/test_clean_log_tracking.py` | Root/reply message id persistence e grouping. |
| `tests/runtime_v2/control_plane/test_main_control_plane.py` | Bootstrap/runtime startup/shutdown. |
| `docs/AUDIT.md` | Riallineamento stato reale dopo chiusura gap. |

---

## Acceptance Contract

**Done means:**
- i comandi esposti dal bot coincidono con la porzione MVP delle spec;
- `TECH_LOG` rispetta le policy runtime dichiarate e non emette eventi fuori livello;
- `CLEAN_LOG` espone una timeline operativa coerente, con root/reply tracking e formatter allineati;
- `docs/AUDIT.md` riflette il comportamento reale del codice, non il piano storico.

**Primary signal:**
- suite `tests/runtime_v2/control_plane` verde con nuovi casi spec-driven;
- smoke runtime con bot/dispatcher che producono messaggi corretti su outbox.

**Secondary signals:**
- `python -m pytest tests/runtime_v2/control_plane -q`
- `python -m pytest tests/runtime_v2/lifecycle -q`
- `python -c "import main; print('import ok')"`

---

### Task 1: Chiudere il delta Part 5 mancante (`COMMANDS` foundation)

Status: completed on 2026-05-30. Snapshot store, startup resolver, debug controller e bootstrap introdotti con test verdi e review approvata.

**Files:**
- Create: `src/runtime_v2/control_plane/snapshot_store.py`
- Create: `src/runtime_v2/control_plane/startup.py`
- Create: `src/runtime_v2/control_plane/debug_controller.py`
- Create: `src/runtime_v2/control_plane/bootstrap.py`
- Test: `tests/runtime_v2/control_plane/test_snapshot_store.py`
- Test: `tests/runtime_v2/control_plane/test_startup.py`
- Test: `tests/runtime_v2/control_plane/test_debug_controller.py`

- [x] **Step 1: Write the failing snapshot/startup/debug tests**

Create these tests first:

```python
def test_save_and_get_latest_snapshot(ops_db):
    store = SnapshotStore(ops_db)
    store.save(
        control_mode="BLOCK_NEW_ENTRIES",
        active_blocks=["GLOBAL:BLOCK_NEW_ENTRIES"],
        open_chain_count=3,
        pending_command_count=2,
        shutdown_reason="SIGTERM",
    )
    snap = store.get_latest()
    assert snap is not None
    assert snap.control_mode == "BLOCK_NEW_ENTRIES"
    assert snap.open_chain_count == 3


def test_restore_stale_snapshot_falls_back_to_auto():
    snap = RuntimeSnapshot(
        snapshot_at=datetime.now(timezone.utc) - timedelta(seconds=500),
        control_mode="BLOCK_NEW_ENTRIES",
        active_blocks_json="[]",
        open_chain_count=1,
        pending_command_count=0,
    )
    plan = resolve_startup(
        mode="restore",
        restore_max_age_seconds=300,
        latest_snapshot=snap,
    )
    assert plan.mode == "auto"
    assert plan.fell_back is True


def test_debug_controller_expires():
    ctrl = DebugModeController(max_seconds=600)
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    ctrl.enable(duration_seconds=300, now=now)
    assert ctrl.is_active(now=now) is True
    assert ctrl.is_active(now=now + timedelta(seconds=301)) is False
```

- [x] **Step 2: Run the tests and verify they fail**

Run:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest ^
  tests\runtime_v2\control_plane\test_snapshot_store.py ^
  tests\runtime_v2\control_plane\test_startup.py ^
  tests\runtime_v2\control_plane\test_debug_controller.py -v
```

Expected: fail with missing modules/symbols.

- [x] **Step 3: Implement the missing runtime-state modules**

Create the minimal contracts:

```python
# snapshot_store.py
class SnapshotStore:
    def save(self, *, control_mode: str, active_blocks: list[str],
             open_chain_count: int, pending_command_count: int,
             shutdown_reason: str | None = None) -> None: ...
    def get_latest(self) -> RuntimeSnapshot | None: ...
    def is_stale(self, snapshot_at: datetime, *, max_age_seconds: int) -> bool: ...


# startup.py
@dataclass
class StartupPlan:
    mode: str
    apply_global_block: bool
    fell_back: bool = False
    message: str = ""

def resolve_startup(*, mode: str, restore_max_age_seconds: int,
                    latest_snapshot: RuntimeSnapshot | None) -> StartupPlan: ...


# debug_controller.py
def parse_duration(text: str | None, *, max_seconds: int = 3600) -> int: ...

class DebugModeController:
    def enable(self, *, duration_seconds: int, now: datetime | None = None) -> datetime: ...
    def disable(self) -> None: ...
    def is_active(self, *, now: datetime | None = None) -> bool: ...
```

- [x] **Step 4: Build the control-plane bootstrap around the new modules**

`bootstrap.py` should own the integration currently embedded in `main.py`:

```python
@dataclass
class ControlPlane:
    config: ControlPlaneConfig
    service: RuntimeControlService
    bot: TelegramControlBot
    dispatcher: TelegramNotificationDispatcher
    snapshot_store: SnapshotStore
    startup_plan: StartupPlan

def build_control_plane(*, config_path: str, ops_db_path: str, log_path: str | None) -> ControlPlane | None:
    ...
```

- [x] **Step 5: Run the new tests until green**

Run:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest ^
  tests\runtime_v2\control_plane\test_snapshot_store.py ^
  tests\runtime_v2\control_plane\test_startup.py ^
  tests\runtime_v2\control_plane\test_debug_controller.py -q
```

Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/snapshot_store.py src/runtime_v2/control_plane/startup.py src/runtime_v2/control_plane/debug_controller.py src/runtime_v2/control_plane/bootstrap.py tests/runtime_v2/control_plane/test_snapshot_store.py tests/runtime_v2/control_plane/test_startup.py tests/runtime_v2/control_plane/test_debug_controller.py
git commit -m "feat(control_plane): add runtime snapshot startup and debug state"
```

---

### Task 2: Portare `COMMANDS` al contratto spec minimo reale

Status: completed on 2026-05-30. `/pnl`, `/debug_on`, `/debug_off`, help/allowed commands, `PnlView`, `Source link` su `/trade` e validazione argomenti sono stati allineati con test verdi e doppia review approvata.

**Files:**
- Modify: `src/runtime_v2/control_plane/service.py`
- Modify: `src/runtime_v2/control_plane/status_queries.py`
- Modify: `src/runtime_v2/control_plane/telegram_bot.py`
- Create: `src/runtime_v2/control_plane/formatters/pnl.py`
- Create: `src/runtime_v2/control_plane/formatters/debug.py`
- Test: `tests/runtime_v2/control_plane/test_command_router_advanced.py`
- Test: `tests/runtime_v2/control_plane/test_status_queries.py`

- [x] **Step 1: Write failing tests for `/pnl`, real `/debug_on`, real `/debug_off`**

Add tests like:

```python
def test_pnl_command_returns_structured_reply(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/pnl",
        message_id=30,
        chat_id=-100999,
        thread_id=101,
        user_id=42,
        username="op",
    )
    assert res.decision == "EXECUTED"
    assert "PnL" in res.reply_text


def test_debug_on_activates_controller(ops_db):
    router = _router_with_debug(ops_db)
    res = router.route(
        command_text="/debug_on 5m",
        message_id=31,
        chat_id=-100999,
        thread_id=101,
        user_id=42,
        username="op",
    )
    assert res.decision == "EXECUTED"
    assert "DEBUG MODE ATTIVATO" in res.reply_text
    assert router._service.debug_status() is True


def test_debug_off_disables_controller(ops_db):
    service = _service_with_enabled_debug(ops_db)
    router = _router_from_service(ops_db, service)
    res = router.route(
        command_text="/debug_off",
        message_id=32,
        chat_id=-100999,
        thread_id=101,
        user_id=42,
        username="op",
    )
    assert res.decision == "EXECUTED"
    assert service.debug_status() is False
```

- [x] **Step 2: Extend `RuntimeControlService`**

Add real methods instead of stub messaging:

```python
class RuntimeControlService:
    def __init__(self, *, ops_db_path: str, log_path: str | None = None,
                 debug_controller: DebugModeController | None = None) -> None:
        ...

    def get_pnl(self) -> PnlView: ...
    def enable_debug(self, *, duration_seconds: int) -> datetime: ...
    def disable_debug(self) -> None: ...
    def debug_status(self) -> bool: ...
```

`get_pnl()` must be honest with current persistence: if mark-price/fees/funding per trade are unavailable, render `n/a`, not fake values.

- [x] **Step 3: Extend `StatusQueries` so replies expose only persisted data**

Tighten the data contract:

```python
class TradeDetail:
    chain_id: int
    symbol: str
    side: str
    trader_id: str
    account_id: str
    state: str
    entry_avg_price: float | None
    current_stop_price: float | None
    last_events: list[str]
    original_message_link: str | None = None
```

Also add a lightweight `PnlView` sourced from `ops_account_snapshots` and open-chain counts. Do not synthesize ROI/PnL fields that are not stored.

- [x] **Step 4: Replace the current debug stubs in `telegram_bot.py`**

The current code returns `"Non ancora disponibile in questa versione."`. Replace it with real dispatch:

```python
if command_name == "pnl":
    return _DispatchResult(format_pnl(self._service.get_pnl()))
if command_name == "debug_on":
    seconds = parse_duration(args[0] if args else None, max_seconds=self._debug_max_seconds)
    expires_at = self._service.enable_debug(duration_seconds=seconds)
    return _DispatchResult(format_debug_on(duration_seconds=seconds, expires_at=expires_at))
if command_name == "debug_off":
    self._service.disable_debug()
    return _DispatchResult(format_debug_off())
```

- [x] **Step 5: Align command help text and allowed command set**

Update `_HELP_TEXT`, `_READONLY_COMMANDS` and `_ALLOWED_COMMANDS` so help, parser and tests agree on:

```python
_ADVANCED_COMMANDS = frozenset({"pnl", "logs", "debug_on", "debug_off"})
_ALLOWED_COMMANDS = _READONLY_COMMANDS | _CONTROL_COMMANDS | _ADVANCED_COMMANDS
```

- [x] **Step 6: Run focused tests**

Run:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest ^
  tests\runtime_v2\control_plane\test_command_router.py ^
  tests\runtime_v2\control_plane\test_command_router_advanced.py ^
  tests\runtime_v2\control_plane\test_status_queries.py -q
```

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/runtime_v2/control_plane/service.py src/runtime_v2/control_plane/status_queries.py src/runtime_v2/control_plane/telegram_bot.py src/runtime_v2/control_plane/formatters/pnl.py src/runtime_v2/control_plane/formatters/debug.py tests/runtime_v2/control_plane/test_command_router_advanced.py tests/runtime_v2/control_plane/test_status_queries.py
git commit -m "feat(control_plane): align commands runtime with spec"
```

---

### Task 3: Rendere `TECH_LOG` governato da policy reali

**Files:**
- Modify: `src/runtime_v2/control_plane/notification_dispatcher.py`
- Modify: `src/runtime_v2/control_plane/service.py`
- Modify: `src/runtime_v2/control_plane/formatters/tech_log.py`
- Test: `tests/runtime_v2/control_plane/test_tech_log_policy.py`
- Test: `tests/runtime_v2/control_plane/test_dispatcher.py`

- [ ] **Step 1: Write failing policy tests**

Add tests for:

```python
def test_tech_log_disabled_suppresses_message(...): ...
def test_warning_blocked_when_min_level_error(...): ...
def test_debug_message_suppressed_when_debug_inactive(...): ...
def test_operational_event_requires_flag(...): ...
def test_private_bot_adds_system_prefix(...): ...
```

- [ ] **Step 2: Add a real TECH_LOG gating function before send**

Inside `notification_dispatcher.py`, add a pure decision helper:

```python
def _should_send_tech_log(self, payload: dict) -> bool:
    cfg = self._config.topics.tech_log
    if not cfg.enabled:
        return False
    level = str(payload.get("level", "INFO")).upper()
    if level == "DEBUG" and not self._debug_status():
        return False
    if level == "INFO" and not cfg.operational_events:
        return False
    order = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "WARN": 30, "ERROR": 40, "CRITICAL": 50}
    min_level = order.get(cfg.min_level.upper(), 30)
    current = order.get(level, 20)
    return current >= min_level
```

Use it in `drain_once()` before rate-limit handling.

- [ ] **Step 3: Inject debug-status from service/bootstrap**

The dispatcher currently has no access to debug mode. Extend constructor:

```python
class TelegramNotificationDispatcher:
    def __init__(..., debug_status: Callable[[], bool] | None = None, ...):
        self._debug_status = debug_status or (lambda: False)
```

Then wire it from `bootstrap.py` with `service.debug_status`.

- [ ] **Step 4: Upgrade `format_tech_log()` to spec-shaped text**

Current output collapses everything into `Details: <json>`. Replace with structured blocks:

```python
lines = [f"[{level}] {category}: {title}" if title else f"[{level}] {category}", _SEP]
lines.append(description)
if context:
    lines.extend(["", "Context:"])
    for key, value in context.items():
        lines.append(f"{key}: {value}")
if action:
    lines.extend(["", f"Action: {action}"])
lines.extend([_SEP, f"Source: {source}"])
```

Keep `⚠️ --SYSTEM--` only for `private_bot`.

- [ ] **Step 5: Keep current rate-limit tests green and add new ones**

Run:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest ^
  tests\runtime_v2\control_plane\test_dispatcher.py ^
  tests\runtime_v2\control_plane\test_tech_log_policy.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/notification_dispatcher.py src/runtime_v2/control_plane/service.py src/runtime_v2/control_plane/formatters/tech_log.py tests/runtime_v2/control_plane/test_dispatcher.py tests/runtime_v2/control_plane/test_tech_log_policy.py
git commit -m "feat(control_plane): enforce tech log runtime policies"
```

---

### Task 4: Integrare bootstrap, startup mode e shutdown reale in `main.py`

**Files:**
- Modify: `main.py`
- Test: `tests/runtime_v2/control_plane/test_main_control_plane.py`

- [ ] **Step 1: Write the failing integration tests**

Cover at least:

```python
def test_build_control_plane_returns_none_when_disabled(...): ...
def test_standby_mode_applies_global_pause(...): ...
def test_shutdown_saves_runtime_snapshot_and_enqueues_tech_log(...): ...
```

- [ ] **Step 2: Replace inline `_build_control_plane()` in `main.py` with `bootstrap.build_control_plane()`**

Remove control-plane wiring duplication from `main.py` and use the new builder:

```python
control_plane = build_control_plane(
    config_path=str(root_dir / "config" / "telegram_control.yaml"),
    ops_db_path=ops_db_path,
    log_path=log_path,
)
```

- [ ] **Step 3: Apply startup mode after build**

If `startup_plan.apply_global_block` is `True`, call:

```python
control_plane.service.pause(scope_value=None, created_by="startup")
```

For `restore`, log the fallback/restored message and enqueue the appropriate TECH_LOG or CLEAN_LOG notification according to the finalized product choice.

- [ ] **Step 4: Save real runtime snapshot on shutdown**

On shutdown, persist:

```python
control_plane.snapshot_store.save(
    control_mode=status.control_mode,
    active_blocks=[f"{b.scope_type}:{b.scope_value or 'GLOBAL'}" for b in control.active_blocks],
    open_chain_count=status.open_count + status.partial_count + status.waiting_entry_count,
    pending_command_count=status.pending_commands,
    shutdown_reason="SIGTERM",
)
```

- [ ] **Step 5: Run focused bootstrap/runtime tests**

Run:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_main_control_plane.py -q
python -c "import main; print('import ok')"
```

Expected: tests PASS and `import ok`.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/runtime_v2/control_plane/test_main_control_plane.py
git commit -m "feat(control_plane): wire startup and shutdown through bootstrap"
```

---

### Task 5: Chiudere la copertura eventi `CLEAN_LOG`

**Files:**
- Modify: `src/runtime_v2/control_plane/outbox_writer.py`
- Modify: `src/runtime_v2/control_plane/formatters/clean_log.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_formatter_full.py`
- Test: `tests/runtime_v2/control_plane/test_outbox_writer.py`

- [ ] **Step 1: Write failing tests for the missing event families**

Add explicit tests for:

```python
def test_entry_updated_renders_fill_and_new_avg(...): ...
def test_update_done_renders_operations_and_changes(...): ...
def test_update_partial_renders_applied_and_rejected(...): ...
def test_update_rejected_renders_reason(...): ...
def test_pending_entry_expired_renders_timeout_worker_source(...): ...
def test_reconciliation_warning_renders_issue_risk_action(...): ...
def test_reentry_accepted_renders_previous_chain(...): ...
```

- [ ] **Step 2: Extend `_CLEAN_LOG_EVENT_MAP`**

Map the currently missing lifecycle events explicitly:

```python
_CLEAN_LOG_EVENT_MAP.update({
    "ENTRY_UPDATED": "ENTRY_UPDATED",
    "UPDATE_DONE": "UPDATE_DONE",
    "UPDATE_PARTIAL": "UPDATE_PARTIAL",
    "UPDATE_REJECTED": "UPDATE_REJECTED",
    "PENDING_TIMEOUT": "PENDING_ENTRY_EXPIRED",
    "RECONCILIATION_WARNING": "RECONCILIATION_WARNING",
    "RECONCILIATION_FIXED": "RECONCILIATION_FIXED",
    "REENTRY_ACCEPTED": "REENTRY_ACCEPTED",
})
```

- [ ] **Step 3: Enrich payloads with the data the formatter needs**

`_build_payload()` must expose structured fields instead of opaque event json:

```python
return {
    **base,
    "source": ev.get("source", "runtime"),
    "link": ev.get("source_message_link"),
    "applied_actions": ev.get("applied_actions", []),
    "rejected_actions": ev.get("rejected_actions", []),
    "changed_fields": ev.get("changed_fields", []),
    "reason": ev.get("reason"),
    "previous_chain_id": ev.get("previous_chain_id"),
}
```

- [ ] **Step 4: Add formatter branches for all mapped event types**

In `clean_log.py`, add dedicated renderers, not fallback text:

```python
if notification_type == "ENTRY_UPDATED":
    return _entry_updated(payload)
if notification_type == "UPDATE_DONE":
    return _update_done(payload)
if notification_type == "UPDATE_PARTIAL":
    return _update_partial(payload)
if notification_type == "UPDATE_REJECTED":
    return _update_rejected(payload)
if notification_type == "PENDING_ENTRY_EXPIRED":
    return _pending_timeout(payload)
if notification_type == "RECONCILIATION_WARNING":
    return _reconciliation_warning(payload)
if notification_type == "RECONCILIATION_FIXED":
    return _reconciliation_fixed(payload)
if notification_type == "REENTRY_ACCEPTED":
    return _reentry_accepted(payload)
```

- [ ] **Step 5: Run focused CLEAN_LOG tests**

Run:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest ^
  tests\runtime_v2\control_plane\test_clean_log_formatter.py ^
  tests\runtime_v2\control_plane\test_clean_log_formatter_full.py ^
  tests\runtime_v2\control_plane\test_outbox_writer.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/runtime_v2/control_plane/outbox_writer.py src/runtime_v2/control_plane/formatters/clean_log.py tests/runtime_v2/control_plane/test_clean_log_formatter_full.py tests/runtime_v2/control_plane/test_outbox_writer.py
git commit -m "feat(control_plane): expand clean log event coverage"
```

---

### Task 6: Aggiungere tracking/root-reply e aggregazione minima `CLEAN_LOG`

**Files:**
- Create: `db/ops_migrations/008_ops_clean_log_tracking.sql`
- Modify: `src/runtime_v2/control_plane/models.py`
- Modify: `src/runtime_v2/control_plane/notification_dispatcher.py`
- Test: `tests/runtime_v2/control_plane/test_clean_log_tracking.py`
- Test: `tests/runtime_v2/control_plane/test_migration_008.py`

- [ ] **Step 1: Write the failing migration and dispatcher tests**

Cover:

```python
def test_clean_log_tracking_table_exists(...): ...
def test_first_chain_message_becomes_root(...): ...
def test_followup_chain_message_replies_to_root(...): ...
def test_same_update_group_reuses_last_message(...): ...
```

- [ ] **Step 2: Add the tracking migration**

Create `db/ops_migrations/008_ops_clean_log_tracking.sql`:

```sql
CREATE TABLE IF NOT EXISTS ops_clean_log_tracking (
    trade_chain_id INTEGER PRIMARY KEY,
    clean_log_root_message_id TEXT,
    clean_log_last_message_id TEXT,
    telegram_chat_id TEXT NOT NULL,
    telegram_thread_id TEXT,
    original_message_link TEXT,
    last_clean_log_event_type TEXT,
    last_clean_log_sent_at TEXT,
    updated_at TEXT NOT NULL
);
```

- [ ] **Step 3: Make the sender return the Telegram message id**

Change the sender protocol from `-> None` to `-> str | None`:

```python
class NotificationSender(Protocol):
    async def send(self, *, chat_id: int, thread_id: int | None, text: str,
                   silent: bool = False, reply_to_message_id: str | None = None) -> str | None: ...
```

`TelegramBotSender.send()` should return `str(message.message_id)`.

- [ ] **Step 4: Persist root/last ids after each CLEAN_LOG send**

In `drain_once()`, when destination is `CLEAN_LOG`:

```python
reply_to = self._resolve_clean_log_reply_target(notification_type, payload)
message_id = await self._sender.send(..., reply_to_message_id=reply_to)
self._update_clean_log_tracking(payload, destination_thread_id=thread_id, sent_message_id=message_id)
```

- [ ] **Step 5: Implement minimal aggregation rule**

Do not build the full debounce engine yet. Implement the smallest coherent rule from the spec:

```python
same_chain + same update_group_id => reply to last message and overwrite grouping state
otherwise => reply to root if root exists, else create root
```

This closes the main structural inconsistency: the spec requires a chain timeline, while today every message is fire-and-forget.

- [ ] **Step 6: Run tracking tests**

Run:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest ^
  tests\runtime_v2\control_plane\test_migration_008.py ^
  tests\runtime_v2\control_plane\test_clean_log_tracking.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add db/ops_migrations/008_ops_clean_log_tracking.sql src/runtime_v2/control_plane/models.py src/runtime_v2/control_plane/notification_dispatcher.py tests/runtime_v2/control_plane/test_migration_008.py tests/runtime_v2/control_plane/test_clean_log_tracking.py
git commit -m "feat(control_plane): add clean log root reply tracking"
```

---

### Task 7: Riallineare `docs/AUDIT.md` al codice reale

**Files:**
- Modify: `docs/AUDIT.md`

- [ ] **Step 1: Re-read the implemented surface**

Use the green test output and the merged code, not the old plan text.

- [ ] **Step 2: Update the audit entries**

The final entry must distinguish:

```text
- realizzato nel codice
- verificato da test
- deferito consapevolmente
- ancora aperto
```

Specifically remove stale statements such as:

```text
Part 5: formatters/tech_log.py + /logs ancora da implementare
```

once those are actually closed in code.

- [ ] **Step 3: Run the final control-plane suite**

Run:

```bash
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/AUDIT.md
git commit -m "docs(audit): align control plane audit with implemented behavior"
```

---

## End-of-plan verification

- [ ] `C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane -q`
- [ ] `C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle -q`
- [ ] `python -c "import main; print('import ok')"`
- [ ] Manual smoke:
  - `startup.mode: standby` => `/control` mostra blocco globale
  - `/start` rimuove il blocco
  - `/pnl`, `/logs 10`, `/debug_on 5m`, `/debug_off` rispondono coerentemente
  - startup/shutdown finiscono in `TECH_LOG`
  - `SIGNAL_ACCEPTED -> ENTRY_OPENED -> UPDATE_DONE -> TP_FILLED_FINAL` produce timeline `CLEAN_LOG` con root/reply coerenti

---

## Self-Review

**Spec coverage:** il piano copre tutti i gap concreti emersi nell’audit: Part 5 mancante, policy `TECH_LOG` non applicate, copertura `CLEAN_LOG` incompleta, tracking/reply assente, `AUDIT.md` disallineato.

**Placeholder scan:** nessun `TODO`/`TBD`; ogni task ha file, test e comandi espliciti.

**Type consistency:** i nuovi contratti (`SnapshotStore`, `StartupPlan`, `DebugModeController`, `PnlView`, sender con `reply_to_message_id`) sono nominati in modo coerente tra task.

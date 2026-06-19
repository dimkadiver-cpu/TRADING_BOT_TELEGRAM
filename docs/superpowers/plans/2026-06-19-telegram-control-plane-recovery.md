# Telegram Control Plane Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore Telegram slash-command polling and prevent clean-log notifications from being blocked forever when the signal root message was not sent.

**Architecture:** Keep the existing control-plane structure. Fix the Telegram bot builder at the PTB boundary, then make clean-log root links a best-effort enrichment instead of a hard dependency after the root notification has failed.

**Tech Stack:** Python 3.12, python-telegram-bot, SQLite ops DB, pytest.

---

### Task 1: Guard Telegram App Construction

**Files:**
- Modify: `src/runtime_v2/control_plane/telegram_bot.py`
- Test: `tests/runtime_v2/control_plane/test_command_router.py`

- [ ] **Step 1: Add a failing regression test**

Add this test near the other `TelegramControlBot` tests:

```python
def test_build_app_does_not_raise_with_custom_request(ops_db):
    bot = _make_bot(ops_db, delivery_mode="supergroup_topics", keyboard=[])
    app = bot._build_app()
    assert app.bot is not None
```

- [ ] **Step 2: Run the focused test and verify failure**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_command_router.py::test_build_app_does_not_raise_with_custom_request -q
```

Expected before fix: fails with `RuntimeError: The parameter connection_pool_size may only be set, if no request instance was set.`

- [ ] **Step 3: Fix `_build_app()` minimally**

Remove the illegal `get_updates_*` builder calls after `.request(build_telegram_request())`. Keep the custom request from `build_telegram_request()` as the single source of Telegram request timeout/pool settings.

```python
app = (
    Application.builder()
    .token(self._config.token)
    .request(build_telegram_request())
    .build()
)
```

- [ ] **Step 4: Verify the regression test passes**

Run the same focused pytest command. Expected: `1 passed`.

### Task 2: Make Missing Root Link Non-Blocking

**Files:**
- Modify: `src/runtime_v2/control_plane/notification_dispatcher.py`
- Test: `tests/runtime_v2/control_plane/test_dispatcher.py`

- [ ] **Step 1: Add a failing test for failed signal root**

Add this test after `test_non_signal_clean_log_waits_for_signal_root_before_send`:

```python
@pytest.mark.asyncio
async def test_non_signal_clean_log_sends_without_link_when_signal_root_failed(ops_db):
    conn = sqlite3.connect(ops_db)
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        write_clean_log_event(
            conn,
            notification_type="ENTRY_OPENED",
            chain_id=40,
            payload={
                "chain_id": 40,
                "symbol": "XAUTUSDT",
                "side": "LONG",
                "fill_price": 4139.6,
                "filled_qty": 4.807,
                "fee": 0.01,
            },
            dedupe_key="clean:entry:40",
        )
        conn.execute(
            "INSERT INTO ops_notification_outbox "
            "(notification_type, destination, payload_json, priority, status, dedupe_key, attempts, created_at, chain_id) "
            "VALUES ('SIGNAL_ACCEPTED', 'CLEAN_LOG', ?, 'MEDIUM', 'FAILED', 'clean:signal:40', 3, ?, 40)",
            (json.dumps({"chain_id": 40, "symbol": "XAUTUSDT", "side": "LONG"}), now),
        )
    conn.close()

    sender = FakeSender()
    disp = _dispatcher(ops_db, sender)

    sent = await disp.drain_once()

    assert sent == 1
    assert len(sender.sent) == 1
    assert sender.sent[0]["text"].splitlines()[0] == "📊 #40 — ENTRY OPENED"
    assert "t.me/c/" not in sender.sent[0]["text"]
```

- [ ] **Step 2: Run the focused test and verify failure**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_dispatcher.py::test_non_signal_clean_log_sends_without_link_when_signal_root_failed -q
```

Expected before fix: fails because the row is requeued and nothing is sent.

- [ ] **Step 3: Add helper to detect failed root notification**

Add a private helper in `TelegramNotificationDispatcher`:

```python
def _has_failed_signal_root(self, chain_id: int) -> bool:
    conn = sqlite3.connect(self._ops_db)
    try:
        row = conn.execute(
            "SELECT 1 FROM ops_notification_outbox "
            "WHERE destination='CLEAN_LOG' "
            "AND notification_type IN ('SIGNAL_ACCEPTED', 'REVIEW_REQUIRED') "
            "AND chain_id=? AND status='FAILED' "
            "LIMIT 1",
            (chain_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()
```

- [ ] **Step 4: Change root wait policy**

In `drain_once()`, when a non-signal clean-log event has a `chain_id` but no root tracking row:

```python
if root_msg_id is None:
    if self._has_failed_signal_root(chain_id):
        logger.warning(
            "clean log root missing after failed signal notification; sending without signal link | notification_id=%s chain_id=%s",
            notification_id,
            chain_id,
        )
    else:
        self._requeue_pending(notification_id)
        continue
```

This preserves waiting when the root may still be sent, but prevents indefinite `PENDING` when the root already failed.

- [ ] **Step 5: Verify dispatcher tests**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_dispatcher.py -q
```

Expected: all dispatcher tests pass.

### Task 3: Validate Combined Control Plane

**Files:**
- Test only.

- [ ] **Step 1: Run focused control-plane tests**

Run:

```powershell
C:\TeleSignalBot\.venv\Scripts\python.exe -m pytest tests\runtime_v2\control_plane\test_command_router.py tests\runtime_v2\control_plane\test_dispatcher.py tests\runtime_v2\control_plane\test_main_control_plane.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Inspect diff**

Run:

```powershell
git diff -- src/runtime_v2/control_plane/telegram_bot.py src/runtime_v2/control_plane/notification_dispatcher.py tests/runtime_v2/control_plane/test_command_router.py tests/runtime_v2/control_plane/test_dispatcher.py
```

Expected: diff only contains the builder fix, the fallback policy, and regression tests.

### Self-Review

- Spec coverage: covers polling crash, timeout regression test gap, and missing-root notification blocking.
- Placeholder scan: no TBD/TODO placeholders.
- Type consistency: helper uses existing `ops_notification_outbox.chain_id`, existing status values, and existing dispatcher flow.

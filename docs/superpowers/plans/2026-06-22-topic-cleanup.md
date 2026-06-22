# Topic Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/clear_topic` and `/clear_all_topic` to the Telegram control plane so authorized users can wipe one topic or all topics in the configured supergroup with inline confirmation, using the live Telethon client for real message enumeration and deletion.

**Architecture:** Keep command parsing, auth, preview, and callback confirmation in the existing control-plane router. Add a focused `TopicCleanupService` that owns Telethon-based topic enumeration, batch deletion, flood-wait handling, and in-memory locks. Wire the already-running Telethon client from `main.py` into the control-plane bootstrap so cleanup reuses the live session instead of opening a second client.

**Tech Stack:** Python 3.12, python-telegram-bot, Telethon, pytest, sqlite audit store, asyncio

---

### Task 1: Extend Auth Scope For Topic Cleanup Commands

**Files:**
- Modify: `src/runtime_v2/control_plane/auth.py`
- Test: `tests/runtime_v2/control_plane/test_auth.py`

- [ ] **Step 1: Write the failing auth tests**

Add these tests to `tests/runtime_v2/control_plane/test_auth.py`:

```python
def test_clear_topic_allowed_from_arbitrary_forum_thread():
    v = AuthValidator(_config_with_per_trader())
    res = v.validate(
        chat_id=-100999,
        thread_id=999,
        user_id=42,
        command_name="clear_topic",
    )
    assert res.decision == "OK"


def test_clear_all_topic_allowed_from_arbitrary_forum_thread():
    v = AuthValidator(_config_with_per_trader())
    res = v.validate(
        chat_id=-100999,
        thread_id=999,
        user_id=42,
        command_name="clear_all_topic",
    )
    assert res.decision == "OK"


def test_clear_topic_wrong_chat_still_ignored():
    v = AuthValidator(_config_with_per_trader())
    res = v.validate(
        chat_id=-1,
        thread_id=999,
        user_id=42,
        command_name="clear_topic",
    )
    assert res.decision == "IGNORE"
    assert res.reason == "wrong_chat"


def test_clear_all_topic_unauthorized_user_rejected():
    v = AuthValidator(_config_with_per_trader())
    res = v.validate(
        chat_id=-100999,
        thread_id=999,
        user_id=77,
        command_name="clear_all_topic",
    )
    assert res.decision == "REJECT_UNAUTHORIZED"
    assert res.reason == "unauthorized_user"
```

- [ ] **Step 2: Run the auth tests to verify failure**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_auth.py -q
```

Expected: the new `clear_topic` / `clear_all_topic` assertions fail with `wrong_topic`.

- [ ] **Step 3: Implement the minimal auth change**

Update `src/runtime_v2/control_plane/auth.py` to add an explicit allow-list for cleanup commands before the existing topic restrictions reject them:

```python
_TOPIC_CLEANUP_COMMANDS = frozenset({"clear_topic", "clear_all_topic"})


class AuthValidator:
    ...
    def validate(
        self,
        chat_id: int,
        thread_id: int | None,
        user_id: int,
        command_name: str | None = None,
    ) -> AuthResult:
        if chat_id != self._chat_id:
            return AuthResult("IGNORE", "wrong_chat")

        if self._delivery_mode == "supergroup_topics":
            if command_name in _TOPIC_CLEANUP_COMMANDS and thread_id is not None:
                pass
            elif thread_id == self._commands_thread_id:
                pass
            elif thread_id in self._clean_log_thread_ids and (
                command_name in _DASHBOARD_ALLOWED_FROM_CLEAN_LOG
                or _DASH_ACTION_RE.match(command_name or "")
            ):
                pass
            else:
                return AuthResult("IGNORE", "wrong_topic")

        if user_id not in self._authorized_users:
            return AuthResult("REJECT_UNAUTHORIZED", "unauthorized_user")
        return AuthResult("OK")
```

- [ ] **Step 4: Run auth tests to verify pass**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_auth.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime_v2/control_plane/test_auth.py src/runtime_v2/control_plane/auth.py
git commit -m "feat: allow topic cleanup commands from any forum thread"
```

### Task 2: Add A Dedicated Topic Cleanup Service

**Files:**
- Create: `src/runtime_v2/control_plane/topic_cleanup.py`
- Test: `tests/runtime_v2/control_plane/test_topic_cleanup.py`

- [ ] **Step 1: Write the failing service tests**

Create `tests/runtime_v2/control_plane/test_topic_cleanup.py` with focused unit tests around one-topic cleanup, all-topic cleanup, batching, and locks:

```python
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.runtime_v2.control_plane.topic_cleanup import TopicCleanupService


def _msg(mid: int, *, top_id: int | None = None):
    reply_to = SimpleNamespace(reply_to_top_id=top_id, forum_topic=True)
    return SimpleNamespace(id=mid, reply_to=reply_to)


@pytest.mark.asyncio
async def test_clear_topic_collects_only_current_topic_and_skips_root():
    client = AsyncMock()
    client.iter_messages.return_value = [_msg(30, top_id=10), _msg(10, top_id=10), _msg(31, top_id=10)]
    service = TopicCleanupService(client)

    deleted = await service.clear_topic(
        chat_id=-100999,
        topic_id=10,
        command_message_id=40,
        preview_message_id=41,
    )

    assert deleted == [30, 31, 40, 41]


@pytest.mark.asyncio
async def test_clear_topic_deletes_in_batches_of_100():
    client = AsyncMock()
    client.iter_messages.return_value = [_msg(i, top_id=10) for i in range(11, 216)]
    service = TopicCleanupService(client)

    await service.clear_topic(
        chat_id=-100999,
        topic_id=10,
        command_message_id=40,
        preview_message_id=41,
    )

    assert client.delete_messages.await_count == 3


@pytest.mark.asyncio
async def test_clear_all_topic_uses_chat_level_lock():
    client = AsyncMock()
    service = TopicCleanupService(client)
    await service._acquire_chat_lock(-100999)

    started = await service.try_clear_all_topics(
        chat_id=-100999,
        origin_topic_id=10,
        command_message_id=40,
        preview_message_id=41,
    )

    assert started is False


@pytest.mark.asyncio
async def test_clear_topic_respects_existing_chat_level_lock():
    client = AsyncMock()
    service = TopicCleanupService(client)
    await service._acquire_chat_lock(-100999)

    started = await service.try_clear_topic(
        chat_id=-100999,
        topic_id=10,
        command_message_id=40,
        preview_message_id=41,
    )

    assert started is False
```

- [ ] **Step 2: Run the new service tests to verify failure**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_topic_cleanup.py -q
```

Expected: FAIL with `ModuleNotFoundError` or missing `TopicCleanupService`.

- [ ] **Step 3: Implement the focused cleanup service**

Create `src/runtime_v2/control_plane/topic_cleanup.py` with a small, explicit API:

```python
from __future__ import annotations

import asyncio
from collections.abc import Iterable


def _chunks(values: list[int], size: int) -> Iterable[list[int]]:
    for i in range(0, len(values), size):
        yield values[i:i + size]


class TopicCleanupService:
    def __init__(self, telethon_client) -> None:
        self._client = telethon_client
        self._topic_locks: set[tuple[int, int]] = set()
        self._chat_locks: set[int] = set()

    async def _acquire_chat_lock(self, chat_id: int) -> bool:
        if chat_id in self._chat_locks:
            return False
        self._chat_locks.add(chat_id)
        return True

    def _release_chat_lock(self, chat_id: int) -> None:
        self._chat_locks.discard(chat_id)

    async def _acquire_topic_lock(self, chat_id: int, topic_id: int) -> bool:
        key = (chat_id, topic_id)
        if chat_id in self._chat_locks or key in self._topic_locks:
            return False
        self._topic_locks.add(key)
        return True

    def _release_topic_lock(self, chat_id: int, topic_id: int) -> None:
        self._topic_locks.discard((chat_id, topic_id))

    async def _collect_topic_message_ids(
        self,
        *,
        chat_id: int,
        topic_id: int,
        command_message_id: int,
        preview_message_id: int,
    ) -> list[int]:
        ids: set[int] = {command_message_id, preview_message_id}
        async for msg in self._client.iter_messages(chat_id, reply_to=topic_id):
            if msg.id == topic_id:
                continue
            ids.add(msg.id)
        return sorted(ids)

    async def _delete_ids(self, chat_id: int, message_ids: list[int]) -> None:
        for batch in _chunks(message_ids, 100):
            if batch:
                await self._client.delete_messages(chat_id, batch)
```

- [ ] **Step 4: Finish the service implementation for clear-one and clear-all**

Extend the same file with the public methods and best-effort cleanup flow:

```python
    async def clear_topic(
        self,
        *,
        chat_id: int,
        topic_id: int,
        command_message_id: int,
        preview_message_id: int,
    ) -> list[int]:
        ids = await self._collect_topic_message_ids(
            chat_id=chat_id,
            topic_id=topic_id,
            command_message_id=command_message_id,
            preview_message_id=preview_message_id,
        )
        await self._delete_ids(chat_id, ids)
        return ids

    async def try_clear_topic(self, **kwargs) -> bool:
        chat_id = kwargs["chat_id"]
        topic_id = kwargs["topic_id"]
        if not await self._acquire_topic_lock(chat_id, topic_id):
            return False
        try:
            await self.clear_topic(**kwargs)
            return True
        finally:
            self._release_topic_lock(chat_id, topic_id)

    async def _iter_forum_topic_ids(self, chat_id: int) -> list[int]:
        topic_ids: set[int] = set()
        async for dialog_msg in self._client.iter_messages(chat_id):
            reply_to = getattr(dialog_msg, "reply_to", None)
            top_id = getattr(reply_to, "reply_to_top_id", None)
            if top_id is not None:
                topic_ids.add(int(top_id))
        return sorted(topic_ids)

    async def try_clear_all_topics(
        self,
        *,
        chat_id: int,
        origin_topic_id: int,
        command_message_id: int,
        preview_message_id: int,
    ) -> bool:
        if not await self._acquire_chat_lock(chat_id):
            return False
        try:
            topic_ids = await self._iter_forum_topic_ids(chat_id)
            if origin_topic_id not in topic_ids:
                topic_ids.append(origin_topic_id)
            for topic_id in sorted(set(topic_ids)):
                ids = await self._collect_topic_message_ids(
                    chat_id=chat_id,
                    topic_id=topic_id,
                    command_message_id=command_message_id if topic_id == origin_topic_id else 0,
                    preview_message_id=preview_message_id if topic_id == origin_topic_id else 0,
                )
                ids = [mid for mid in ids if mid > 0]
                await self._delete_ids(chat_id, ids)
            return True
        finally:
            self._release_chat_lock(chat_id)
```

- [ ] **Step 5: Run the cleanup service tests to verify pass**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_topic_cleanup.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/runtime_v2/control_plane/test_topic_cleanup.py src/runtime_v2/control_plane/topic_cleanup.py
git commit -m "feat: add telethon-backed topic cleanup service"
```

### Task 3: Add Router Commands, Preview State, And Callback Execution

**Files:**
- Modify: `src/runtime_v2/control_plane/telegram_bot.py`
- Test: `tests/runtime_v2/control_plane/test_command_router.py`

- [ ] **Step 1: Write the failing router tests**

Add tests to `tests/runtime_v2/control_plane/test_command_router.py` that exercise preview creation, auth pass-through from arbitrary topics, and callback execution:

```python
def test_clear_topic_from_arbitrary_thread_returns_confirmation_keyboard(ops_db):
    cfg = _config()
    service = RuntimeControlService(ops_db_path=ops_db)
    cleanup = MagicMock()
    router = CommandRouter(
        config=cfg,
        auth=AuthValidator(cfg),
        audit=CommandAuditStore(ops_db),
        service=service,
        topic_cleanup=cleanup,
    )

    res = router.route(
        command_text="/clear_topic",
        message_id=90,
        chat_id=-100999,
        thread_id=999,
        user_id=42,
        username="op",
    )

    assert res.decision == "EXECUTED"
    assert "clear topic" in res.reply_text.lower()
    assert res.keyboard is not None


def test_clear_all_topic_from_arbitrary_thread_returns_confirmation_keyboard(ops_db):
    cfg = _config()
    service = RuntimeControlService(ops_db_path=ops_db)
    cleanup = MagicMock()
    router = CommandRouter(
        config=cfg,
        auth=AuthValidator(cfg),
        audit=CommandAuditStore(ops_db),
        service=service,
        topic_cleanup=cleanup,
    )

    res = router.route(
        command_text="/clear_all_topic",
        message_id=91,
        chat_id=-100999,
        thread_id=999,
        user_id=42,
        username="op",
    )

    assert res.decision == "EXECUTED"
    assert "all topic" in res.reply_text.lower()
    assert res.keyboard is not None
```

- [ ] **Step 2: Add failing callback tests**

In the same file, add callback tests that verify the router calls the cleanup service only on confirm:

```python
def test_clear_topic_callback_confirm_invokes_cleanup_service(ops_db):
    cfg = _config()
    service = RuntimeControlService(ops_db_path=ops_db)
    cleanup = MagicMock()
    cleanup.try_clear_topic.return_value = True
    router = CommandRouter(
        config=cfg,
        auth=AuthValidator(cfg),
        audit=CommandAuditStore(ops_db),
        service=service,
        topic_cleanup=cleanup,
    )

    res = router.route(
        command_text="/clear_topic",
        message_id=92,
        chat_id=-100999,
        thread_id=999,
        user_id=42,
        username="op",
    )

    token = next(iter(router._pending.keys()))
    cb = router.handle_callback(
        callback_data=f"clear_topic:confirm:{token}",
        user_id=42,
        chat_id=-100999,
        message_id=93,
        thread_id=999,
        created_by="42",
    )

    cleanup.try_clear_topic.assert_called_once()
    assert cb.delete_message is True


def test_clear_topic_callback_cancel_does_not_invoke_cleanup_service(ops_db):
    cfg = _config()
    service = RuntimeControlService(ops_db_path=ops_db)
    cleanup = MagicMock()
    router = CommandRouter(
        config=cfg,
        auth=AuthValidator(cfg),
        audit=CommandAuditStore(ops_db),
        service=service,
        topic_cleanup=cleanup,
    )

    router.route(
        command_text="/clear_topic",
        message_id=94,
        chat_id=-100999,
        thread_id=999,
        user_id=42,
        username="op",
    )

    token = next(iter(router._pending.keys()))
    cb = router.handle_callback(
        callback_data=f"clear_topic:cancel:{token}",
        user_id=42,
        chat_id=-100999,
        message_id=95,
        thread_id=999,
        created_by="42",
    )

    cleanup.try_clear_topic.assert_not_called()
    assert cb.delete_message is True
```

- [ ] **Step 3: Run the router tests to verify failure**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_command_router.py -q
```

Expected: FAIL because `CommandRouter` does not accept `topic_cleanup`, does not know the commands, and does not handle `clear_topic:*` callbacks.

- [ ] **Step 4: Extend pending action state and allowed commands**

Modify `src/runtime_v2/control_plane/telegram_bot.py` to track topic cleanup pending state:

```python
@dataclass
class _PendingAction:
    kind: Literal["close_all", "close_single", "cancel_all", "clear_topic", "clear_all_topic"]
    scope: "QueryScope | None" = None
    candidates: list[CloseCandidate] = field(default_factory=list)
    chains_payload: list[dict] = field(default_factory=list)
    scope_label: str = ""
    open_count: int = 0
    topic_id: int | None = None
    command_message_id: int | None = None
    created_at: float = field(default_factory=time.time)
```

Also extend the command registry:

```python
_EMERGENCY_COMMANDS = frozenset(
    {"close_all", "close", "cancel_all", "clear_topic", "clear_all_topic"}
)
```

- [ ] **Step 5: Add preview generation and callback execution**

In the same file, update `CommandRouter.__init__`, `_dispatch()`, and `handle_callback()`:

```python
class CommandRouter:
    def __init__(..., topic_cleanup=None, scope_resolver=None) -> None:
        ...
        self._topic_cleanup = topic_cleanup

    def _dispatch(...):
        ...
        if command_name == "clear_topic":
            if thread_id is None:
                return _DispatchResult("", decision="IGNORE", reject_reason="wrong_topic")
            token = _make_token()
            self._pending[token] = _PendingAction(
                kind="clear_topic",
                topic_id=thread_id,
                command_message_id=self._current_message_id,
            )
            return _DispatchResult(
                "⚠️ Clear topic corrente?\nConferma per cancellare tutti i messaggi del thread.",
                keyboard=_emergency_keyboard("clear_topic", token),
            )

        if command_name == "clear_all_topic":
            if thread_id is None:
                return _DispatchResult("", decision="IGNORE", reject_reason="wrong_topic")
            token = _make_token()
            self._pending[token] = _PendingAction(
                kind="clear_all_topic",
                topic_id=thread_id,
                command_message_id=self._current_message_id,
            )
            return _DispatchResult(
                "⚠️ Clear tutti i topic del supergruppo?\nConferma per una pulizia completa.",
                keyboard=_emergency_keyboard("clear_all_topic", token),
            )
```

And in `handle_callback()`:

```python
is_emergency = (
    len(parts) == 3
    and parts[0] in ("close_all", "close_single", "cancel_all", "clear_topic", "clear_all_topic")
    and parts[1] in ("confirm", "cancel")
)

...
if action == "cancel" and kind in {"clear_topic", "clear_all_topic"}:
    return CallbackResult("", delete_message=True, answer_text="❌ Annullato")

if kind == "clear_topic":
    asyncio.run(
        self._topic_cleanup.try_clear_topic(
            chat_id=chat_id,
            topic_id=pending.topic_id,
            command_message_id=pending.command_message_id,
            preview_message_id=message_id,
        )
    )
    return CallbackResult("", delete_message=True, answer_text="🧹")

if kind == "clear_all_topic":
    asyncio.run(
        self._topic_cleanup.try_clear_all_topics(
            chat_id=chat_id,
            origin_topic_id=pending.topic_id,
            command_message_id=pending.command_message_id,
            preview_message_id=message_id,
        )
    )
    return CallbackResult("", delete_message=True, answer_text="🧹")
```

During implementation, avoid `asyncio.run()` inside an active event loop by extracting an injectable synchronous wrapper or by making `handle_callback()` delegate to `asyncio.run_coroutine_threadsafe(...)` from the bot layer. Keep the router API consistent with the rest of the file.

- [ ] **Step 6: Run the router tests to verify pass**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_command_router.py -q
```

Expected: PASS, including existing emergency command coverage.

- [ ] **Step 7: Commit**

```bash
git add tests/runtime_v2/control_plane/test_command_router.py src/runtime_v2/control_plane/telegram_bot.py
git commit -m "feat: add topic cleanup commands to command router"
```

### Task 4: Wire The Live Telethon Client Into The Control Plane

**Files:**
- Modify: `src/runtime_v2/control_plane/bootstrap.py`
- Modify: `main.py`
- Test: `tests/runtime_v2/control_plane/test_main_control_plane.py`

- [ ] **Step 1: Write the failing wiring test**

Add a bootstrap test to `tests/runtime_v2/control_plane/test_main_control_plane.py`:

```python
def test_build_control_plane_accepts_telethon_client_and_wires_topic_cleanup(tmp_path):
    config_file = _write_config(tmp_path, mode="auto")
    db_path = str(tmp_path / "ops.sqlite3")
    _apply_migrations(db_path)
    fake_client = MagicMock(name="telethon_client")

    with patch("telegram.Bot", return_value=MagicMock()):
        cp = build_control_plane(
            config_path=str(config_file),
            ops_db_path=db_path,
            log_path=None,
            telethon_client=fake_client,
        )

    assert cp is not None
    assert cp.bot._router._topic_cleanup is not None
    assert cp.bot._router._topic_cleanup._client is fake_client
```

- [ ] **Step 2: Run the bootstrap test to verify failure**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_main_control_plane.py -q
```

Expected: FAIL because `build_control_plane()` has no `telethon_client` argument and does not create `TopicCleanupService`.

- [ ] **Step 3: Update bootstrap to build and inject the cleanup service**

Modify `src/runtime_v2/control_plane/bootstrap.py`:

```python
from src.runtime_v2.control_plane.topic_cleanup import TopicCleanupService


def build_control_plane(
    *,
    config_path: str,
    ops_db_path: str,
    log_path: str | None,
    known_trader_ids: set[str] | None = None,
    telethon_client=None,
) -> ControlPlane | None:
    ...
    topic_cleanup = (
        TopicCleanupService(telethon_client)
        if telethon_client is not None
        else None
    )
    router = CommandRouter(
        config=config,
        auth=auth,
        audit=audit,
        service=service,
        topic_cleanup=topic_cleanup,
    )
```

- [ ] **Step 4: Pass the live client from `main.py`**

Update `main.py` so the already-started `TelegramClient` is passed into the control plane after `await client.start()` and before bot callbacks can use it:

```python
client = TelegramClient(session_name, api_id, api_hash)
await client.start()

_cp = build_control_plane(
    config_path=str(root_dir / "config" / "telegram_control.yaml"),
    ops_db_path=ops_db_path,
    log_path=log_path,
    known_trader_ids=(
        {ch.trader_id for ch in channels_config.channels if ch.trader_id}
        | pattern_catalog.all_trader_ids
    ),
    telethon_client=client,
)
```

If the current ordering makes this awkward, move control-plane construction below `await client.start()` and keep the rest of the startup behavior unchanged.

- [ ] **Step 5: Run the bootstrap tests to verify pass**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_main_control_plane.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/runtime_v2/control_plane/test_main_control_plane.py src/runtime_v2/control_plane/bootstrap.py main.py
git commit -m "feat: wire live telethon client into control plane cleanup"
```

### Task 5: Close The Loop With Targeted Regression Coverage

**Files:**
- Modify: `tests/runtime_v2/control_plane/test_command_router.py`
- Modify: `tests/runtime_v2/control_plane/test_topic_cleanup.py`
- Modify: `src/runtime_v2/control_plane/telegram_bot.py`
- Modify: `docs/superpowers/specs/2026-06-22-topic-cleanup-design.md` only if implementation forces a real design adjustment

- [ ] **Step 1: Add regression tests for edge behavior**

Expand the tests with the exact edge cases from the spec:

```python
def test_clear_topic_without_thread_is_ignored(ops_db):
    router = _router(ops_db)
    res = router.route(
        command_text="/clear_topic",
        message_id=96,
        chat_id=-100999,
        thread_id=None,
        user_id=42,
        username="op",
    )
    assert res.decision == "IGNORE"


@pytest.mark.asyncio
async def test_clear_all_topic_keeps_running_after_delete_error():
    client = AsyncMock()
    client.delete_messages.side_effect = [RuntimeError("gone"), None, None]
    service = TopicCleanupService(client)
    ...
```

Add a flood-wait test by simulating a Telethon exception object with a `seconds` attribute and asserting the service sleeps and retries.

- [ ] **Step 2: Run the focused regression suite**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_auth.py tests/runtime_v2/control_plane/test_command_router.py tests/runtime_v2/control_plane/test_topic_cleanup.py tests/runtime_v2/control_plane/test_main_control_plane.py -q
```

Expected: PASS.

- [ ] **Step 3: Run one broader control-plane smoke suite**

Run:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane -q
```

Expected: PASS. If unrelated failures already exist on the branch, record them explicitly before merging.

- [ ] **Step 4: Apply minimal follow-up code fixes if the smoke suite exposes coupling**

Use this pattern for any regression discovered in `src/runtime_v2/control_plane/telegram_bot.py` or `src/runtime_v2/control_plane/topic_cleanup.py`:

```python
if kind in {"clear_topic", "clear_all_topic"} and self._topic_cleanup is None:
    return CallbackResult(
        "",
        delete_message=True,
        answer_text="⚠️ Topic cleanup non disponibile",
    )
```

Keep the fix local to the failing behavior. Do not widen the scope beyond cleanup commands.

- [ ] **Step 5: Re-run the exact failing test target**

Run the narrowest reproducer from Step 3, for example:

```bash
.venv\Scripts\python.exe -m pytest tests/runtime_v2/control_plane/test_command_router.py::test_clear_topic_callback_confirm_invokes_cleanup_service -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/runtime_v2/control_plane/test_auth.py tests/runtime_v2/control_plane/test_command_router.py tests/runtime_v2/control_plane/test_topic_cleanup.py tests/runtime_v2/control_plane/test_main_control_plane.py src/runtime_v2/control_plane/telegram_bot.py src/runtime_v2/control_plane/topic_cleanup.py src/runtime_v2/control_plane/bootstrap.py main.py
git commit -m "test: cover topic cleanup command flow and regressions"
```

## Self-Review

Spec coverage check:

- arbitrary-topic authorization: Task 1
- inline confirmation before delete: Task 3
- Telethon-based real message enumeration: Task 2
- no second Telethon client, reuse live runtime client: Task 4
- `/clear_topic` vs `/clear_all_topic` behavior split: Tasks 2 and 3
- locks, batching, flood-wait, best effort: Tasks 2 and 5
- no final bot message left behind: Task 3 callback delete flow + Task 5 regressions

Placeholder scan:

- no `TBD`, `TODO`, or “implement later”
- each task names exact files and concrete commands
- every code-writing step includes a concrete snippet to anchor implementation

Type consistency check:

- `TopicCleanupService`
- `try_clear_topic(...)`
- `try_clear_all_topics(...)`
- `telethon_client` bootstrap argument
- `topic_cleanup` router dependency

These names must stay aligned during implementation.

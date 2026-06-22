# Signal Message Type Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist a normalized Telegram message presentation type and prevent `SIGNAL` chains from being created in topics that require inline-button messages.

**Architecture:** Keep the parser unchanged. The listener normalizes Telegram UI metadata into `message_presentation_type`, the channel resolver exposes optional topic policy `signal_message_type`, and the lifecycle worker passes a small `SignalAdmissionContext` into `LifecycleEntryGate`, which can now return an internal silent skip (`SIGNAL_SKIPPED`) instead of a user-visible rejection.

**Tech Stack:** Python 3.12, Telethon, Pydantic v2, SQLite, pytest

---

### Task 1: Extend Raw Message Schema And Runtime Models

**Files:**
- Create: `db/migrations/031_raw_message_presentation_type.sql`
- Modify: `src/runtime_v2/intake/models.py`
- Modify: `src/runtime_v2/persistence/raw_messages.py`
- Modify: `src/storage/raw_messages.py`
- Test: `tests/runtime_v2/test_intake_models.py`
- Test: `tests/runtime_v2/test_raw_message_repository.py`
- Test: `src/storage/tests/test_raw_messages_topic.py`

- [ ] **Step 1: Write the failing model and repository tests**

```python
# tests/runtime_v2/test_intake_models.py
def test_raw_ingest_item_supports_message_presentation_type():
    item = RawIngestItem(
        source_chat_id="-100123",
        source_chat_title="Test",
        source_type="channel",
        source_topic_id=3,
        telegram_message_id=456,
        reply_to_message_id=None,
        raw_text="BUY BTC",
        message_ts=_TS,
        acquisition_mode="live",
        message_presentation_type="INLINE_BUTTONS",
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )
    assert item.message_presentation_type == "INLINE_BUTTONS"


def test_raw_message_envelope_exposes_message_presentation_type():
    env = _make_envelope(message_presentation_type="PLAIN")
    assert env.message_presentation_type == "PLAIN"
```

```python
# tests/runtime_v2/test_raw_message_repository.py
def test_save_raw_persists_message_presentation_type(repo):
    env = repo.save_raw(_make_item())
    assert env.message_presentation_type == "PLAIN"
```

```python
# src/storage/tests/test_raw_messages_topic.py
def test_save_and_get_with_message_presentation_type(tmp_path) -> None:
    db_path = str(tmp_path / "db.sqlite3")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            '''
            CREATE TABLE raw_messages (
              raw_message_id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_chat_id TEXT NOT NULL,
              source_chat_title TEXT,
              source_type TEXT,
              source_trader_id TEXT,
              telegram_message_id INTEGER NOT NULL,
              reply_to_message_id INTEGER,
              raw_text TEXT,
              message_ts TEXT NOT NULL,
              acquired_at TEXT NOT NULL,
              acquisition_status TEXT NOT NULL DEFAULT 'ACQUIRED',
              source_topic_id INTEGER,
              message_presentation_type TEXT NOT NULL DEFAULT 'PLAIN'
            );
            CREATE UNIQUE INDEX idx_raw_messages_dedup
            ON raw_messages(source_chat_id, telegram_message_id);
            '''
        )
    store = RawMessageStore(db_path=db_path)
    result = store.save_with_id(_record(source_topic_id=3, message_presentation_type="INLINE_BUTTONS"))
    assert result.saved is True
    stored = store.get_by_source_and_message_id("chat-1", 1)
    assert stored is not None
    assert stored.message_presentation_type == "INLINE_BUTTONS"
```

- [ ] **Step 2: Run the narrow failing tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\test_intake_models.py tests\runtime_v2\test_raw_message_repository.py src\storage\tests\test_raw_messages_topic.py -q
```

Expected: failures for missing `message_presentation_type` field/column support.

- [ ] **Step 3: Add the migration and model fields**

```sql
-- db/migrations/031_raw_message_presentation_type.sql
ALTER TABLE raw_messages
ADD COLUMN message_presentation_type TEXT NOT NULL DEFAULT 'PLAIN';

CREATE INDEX IF NOT EXISTS idx_raw_messages_presentation_type
ON raw_messages(message_presentation_type);
```

```python
# src/runtime_v2/intake/models.py
MessagePresentationType = Literal["PLAIN", "INLINE_BUTTONS"]


@dataclass(slots=True)
class RawIngestItem:
    ...
    acquisition_mode: AcquisitionMode
    message_presentation_type: MessagePresentationType
    has_media: bool
    ...


class RawMessageEnvelope(BaseModel):
    ...
    acquisition_mode: AcquisitionMode
    acquisition_status: AcquisitionStatus
    processing_status: ProcessingStatusV2
    message_presentation_type: MessagePresentationType
    source_trader_id: str | None
    ...
```

```python
# src/storage/raw_messages.py
@dataclass(slots=True)
class RawMessageRecord:
    ...
    acquisition_status: str = "ACQUIRED"
    source_topic_id: int | None = None
    message_presentation_type: str = "PLAIN"
    has_media: bool = False
    ...


@dataclass(slots=True)
class StoredRawMessage:
    ...
    source_topic_id: int | None = None
    message_presentation_type: str = "PLAIN"
    has_media: bool = False
    ...
```

```python
# src/runtime_v2/persistence/raw_messages.py
record = RawMessageRecord(
    ...
    acquisition_status="ACQUIRED",
    message_presentation_type=item.message_presentation_type,
    has_media=item.has_media,
    ...
)

...
message_presentation_type=(
    row["message_presentation_type"]
    if "message_presentation_type" in keys
    else "PLAIN"
),
```

- [ ] **Step 4: Teach `RawMessageStore` to persist and read the new column safely**

```python
# src/storage/raw_messages.py
for column_name, value in [
    ("source_topic_id", record.source_topic_id),
    ("message_presentation_type", record.message_presentation_type),
    ("has_media", 1 if record.has_media else 0),
    ...
]:
    if column_name not in available_columns:
        continue
    insert_columns.append(column_name)
    insert_values.append(value)
```

```python
# src/storage/raw_messages.py
include_presentation = "message_presentation_type" in available_columns
...
if include_topic:
    select_cols += ", source_topic_id"
if include_presentation:
    select_cols += ", message_presentation_type"
if include_media:
    select_cols += ", has_media, media_kind, media_mime_type, media_filename, media_blob"
...
message_presentation_type = "PLAIN"
if include_presentation:
    message_presentation_type = str(row[idx])
    idx += 1
...
message_presentation_type=message_presentation_type,
```

- [ ] **Step 5: Run the tests again**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\test_intake_models.py tests\runtime_v2\test_raw_message_repository.py src\storage\tests\test_raw_messages_topic.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add db/migrations/031_raw_message_presentation_type.sql src/runtime_v2/intake/models.py src/runtime_v2/persistence/raw_messages.py src/storage/raw_messages.py tests/runtime_v2/test_intake_models.py tests/runtime_v2/test_raw_message_repository.py src/storage/tests/test_raw_messages_topic.py
git commit -m "feat: persist raw message presentation type"
```

### Task 2: Detect Telegram Presentation Type At Listener Ingress

**Files:**
- Modify: `src/telegram/ingestion.py`
- Modify: `src/telegram/listener.py`
- Test: `src/telegram/tests/test_listener_process_item.py`
- Test: `tests/telegram/test_listener_edited_messages.py`

- [ ] **Step 1: Write the failing listener/ingestion tests**

```python
# src/telegram/tests/test_listener_process_item.py
def test_build_incoming_marks_plain_message():
    message = MagicMock()
    message.id = 42
    message.message = "BUY BTCUSDT"
    message.date = datetime.now(timezone.utc)
    message.reply_markup = None
    incoming = _build_incoming(
        message=message,
        source_chat_id="-100123",
        chat_title="Test",
        chat_username=None,
        trader_id=None,
        acquisition_status="ACQUIRED_ELIGIBLE",
        source_topic_id=None,
    )
    assert incoming.message_presentation_type == "PLAIN"


def test_build_incoming_marks_inline_buttons_message():
    message = MagicMock()
    message.id = 42
    message.message = "BUY BTCUSDT"
    message.date = datetime.now(timezone.utc)
    message.reply_markup = MagicMock()
    incoming = _build_incoming(
        message=message,
        source_chat_id="-100123",
        chat_title="Test",
        chat_username=None,
        trader_id=None,
        acquisition_status="ACQUIRED_ELIGIBLE",
        source_topic_id=None,
    )
    assert incoming.message_presentation_type == "INLINE_BUTTONS"
```

```python
# tests/telegram/test_listener_edited_messages.py
def test_edited_message_reenqueue_preserves_message_presentation_type():
    ...
    updated = raw_repo.get_by_id(55)
    assert updated.message_presentation_type == "INLINE_BUTTONS"
```

- [ ] **Step 2: Run the failing listener tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest src\telegram\tests\test_listener_process_item.py tests\telegram\test_listener_edited_messages.py -q
```

Expected: failures because `TelegramIncomingMessage` and `_build_incoming()` do not expose the new field.

- [ ] **Step 3: Add the normalized presentation type at the listener boundary**

```python
# src/telegram/ingestion.py
@dataclass(slots=True)
class TelegramIncomingMessage:
    ...
    acquisition_status: str = "ACQUIRED_ELIGIBLE"
    source_topic_id: int | None = None
    message_presentation_type: str = "PLAIN"
    has_media: bool = False
    ...
```

```python
# src/telegram/listener.py
def _resolve_message_presentation_type(message: Message) -> str:
    return "INLINE_BUTTONS" if getattr(message, "reply_markup", None) is not None else "PLAIN"
```

```python
# src/telegram/listener.py
return TelegramIncomingMessage(
    source_chat_id=source_chat_id,
    source_chat_title=chat_title,
    source_type=_resolve_source_type(chat_title, chat_username),
    source_trader_id=trader_id,
    telegram_message_id=int(message.id),
    reply_to_message_id=extract_real_reply_to_message_id(
        message,
        source_topic_id=source_topic_id,
    ),
    raw_text=message.message,
    message_ts=message.date or datetime.now(timezone.utc),
    acquisition_status=acquisition_status,
    source_topic_id=source_topic_id,
    message_presentation_type=_resolve_message_presentation_type(message),
)
```

- [ ] **Step 4: Pass the field through the ingestion service**

```python
# src/telegram/ingestion.py
record = RawMessageRecord(
    ...
    acquisition_status=incoming.acquisition_status,
    source_topic_id=incoming.source_topic_id,
    message_presentation_type=incoming.message_presentation_type,
    has_media=incoming.has_media,
    ...
)
```

- [ ] **Step 5: Re-run the listener tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest src\telegram\tests\test_listener_process_item.py tests\telegram\test_listener_edited_messages.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/telegram/ingestion.py src/telegram/listener.py src/telegram/tests/test_listener_process_item.py tests/telegram/test_listener_edited_messages.py
git commit -m "feat: normalize telegram message presentation type"
```

### Task 3: Add Topic Policy And Worker Admission Context

**Files:**
- Modify: `src/runtime_v2/trader_resolution/channel_config_resolver.py`
- Modify: `tests/runtime_v2/test_channel_config_resolver.py`
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Modify: `main.py`
- Modify: `tests/runtime_v2/test_main_runtime_bootstrap.py`

- [ ] **Step 1: Write the failing config resolver tests**

```python
# tests/runtime_v2/test_channel_config_resolver.py
def test_lookup_signal_message_type_defaults_to_any(resolver):
    entry = resolver.lookup("-1002222222222", topic_id=None)
    assert entry is not None
    assert entry.signal_message_type == "any"


def test_lookup_signal_message_type_reads_inline_buttons_only(tmp_path):
    yaml_content = """
channels:
  - chat_id: -1009999999999
    topic_id: 9
    label: "InlineOnly"
    active: true
    trader_id: trader_a
    signal_message_type: inline_buttons
    blacklist: []
"""
    p = tmp_path / "channels.yaml"
    p.write_text(yaml_content, encoding="utf-8")
    resolver = ChannelConfigResolver(p)
    entry = resolver.lookup("-1009999999999", topic_id=9)
    assert entry is not None
    assert entry.signal_message_type == "inline_buttons"
```

- [ ] **Step 2: Run the failing config tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\test_channel_config_resolver.py -q
```

Expected: failures because `ChannelEntry` has no `signal_message_type`.

- [ ] **Step 3: Extend `ChannelEntry` with the policy field**

```python
# src/runtime_v2/trader_resolution/channel_config_resolver.py
SignalMessageType = Literal["any", "inline_buttons"]


@dataclass(slots=True, frozen=True)
class ChannelEntry:
    ...
    resolution_max_depth: int
    resolution_mode: str = "default"
    pattern_group: str | None = None
    signal_message_type: SignalMessageType = "any"
```

```python
# src/runtime_v2/trader_resolution/channel_config_resolver.py
signal_message_type = str(raw.get("signal_message_type", "any")).strip() or "any"
if signal_message_type not in {"any", "inline_buttons"}:
    raise ValueError(
        f"invalid signal_message_type for chat_id={chat_id}, topic_id={topic_id}: {signal_message_type!r}"
    )
...
signal_message_type=signal_message_type,
```

- [ ] **Step 4: Introduce the admission context that the worker passes into the gate**

```python
# src/runtime_v2/lifecycle/entry_gate.py
@dataclass(slots=True, frozen=True)
class SignalAdmissionContext:
    signal_message_type: str = "any"
    message_presentation_type: str = "PLAIN"
```

```python
# src/runtime_v2/lifecycle/entry_gate.py
class LifecycleGateWorker:
    def __init__(..., channel_resolver) -> None:
        ...
        self._channel_resolver = channel_resolver
```

```python
# src/runtime_v2/lifecycle/entry_gate.py
def _build_signal_admission_context(self, raw_message_id: int) -> SignalAdmissionContext:
    conn = _sqlite3.connect(self._parser_db)
    try:
        row = conn.execute(
            "SELECT source_chat_id, source_topic_id, message_presentation_type "
            "FROM raw_messages WHERE raw_message_id=?",
            (raw_message_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return SignalAdmissionContext()
    entry = self._channel_resolver.lookup(str(row[0]), int(row[1]) if row[1] is not None else None)
    return SignalAdmissionContext(
        signal_message_type=(entry.signal_message_type if entry is not None else "any"),
        message_presentation_type=str(row[2] or "PLAIN"),
    )
```

- [ ] **Step 5: Thread the resolver into bootstrap and cover it with a bootstrap test**

```python
# main.py
gate_worker = LifecycleGateWorker(
    parser_db_path=parser_db_path,
    ops_db_path=ops_db_path,
    gate=entry_gate,
    chain_repo=chain_repo,
    event_repo=event_repo,
    command_repo=command_repo,
    snapshot_repo=snapshot_repo,
    control_repo=control_repo,
    channel_resolver=channel_resolver,
)
```

```python
# tests/runtime_v2/test_main_runtime_bootstrap.py
def test_runtime_build_passes_channel_resolver_to_lifecycle_gate_worker(monkeypatch, tmp_path):
    import main as app_main
    captured = {}

    def fake_gate_worker(**kwargs):
        captured.update(kwargs)
        return MagicMock(name="gate_worker")

    monkeypatch.setattr(app_main, "LifecycleGateWorker", fake_gate_worker)
    ...
    assert "channel_resolver" in captured
```

- [ ] **Step 6: Re-run config and bootstrap tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\test_channel_config_resolver.py tests\runtime_v2\test_main_runtime_bootstrap.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/runtime_v2/trader_resolution/channel_config_resolver.py src/runtime_v2/lifecycle/entry_gate.py main.py tests/runtime_v2/test_channel_config_resolver.py tests/runtime_v2/test_main_runtime_bootstrap.py
git commit -m "feat: add signal admission policy context"
```

### Task 4: Implement Silent Signal Skip In Lifecycle Gate

**Files:**
- Modify: `src/runtime_v2/lifecycle/models.py`
- Modify: `src/runtime_v2/lifecycle/entry_gate.py`
- Modify: `tests/runtime_v2/lifecycle/test_entry_gate.py`
- Modify: `tests/runtime_v2/lifecycle/test_no_chain_clean_log_dedupe.py`

- [ ] **Step 1: Write the failing gate tests for the new silent-skip behavior**

```python
# tests/runtime_v2/lifecycle/test_entry_gate.py
def test_gate_signal_inline_only_plain_message_is_silently_skipped():
    gate = _make_gate()
    enriched = _make_enriched_signal()
    admission = SignalAdmissionContext(
        signal_message_type="inline_buttons",
        message_presentation_type="PLAIN",
    )

    result = gate.process_signal(enriched, [], "NONE", admission)

    assert result.trade_chain is None
    assert result.execution_commands == []
    assert result.review_reason == "signal_message_type_mismatch"
    assert [e.event_type for e in result.lifecycle_events] == ["SIGNAL_SKIPPED"]


def test_gate_signal_inline_only_inline_buttons_is_accepted():
    gate = _make_gate()
    enriched = _make_enriched_signal()
    admission = SignalAdmissionContext(
        signal_message_type="inline_buttons",
        message_presentation_type="INLINE_BUTTONS",
    )

    result = gate.process_signal(enriched, [], "NONE", admission)

    assert result.trade_chain is not None
    assert "SIGNAL_ACCEPTED" in [e.event_type for e in result.lifecycle_events]
```

```python
# tests/runtime_v2/lifecycle/test_no_chain_clean_log_dedupe.py
def test_signal_skipped_does_not_project_clean_log(tmp_path):
    conn = sqlite3.connect(":memory:")
    ...
    _write_no_chain_signal_clean_log(
        conn,
        enriched,
        [
            LifecycleEvent(
                event_type="SIGNAL_SKIPPED",
                source_type="enrichment",
                source_id="1",
                payload_json='{"reason":"signal_message_type_mismatch","source":"runtime"}',
                idempotency_key="signal_skipped:1",
            )
        ],
        src_chat_id="-1001234",
        tg_msg_id=99,
    )
    row = conn.execute("SELECT COUNT(*) FROM ops_notification_outbox").fetchone()
    assert row == (0,)
```

- [ ] **Step 2: Run the failing lifecycle tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_entry_gate.py tests\runtime_v2\lifecycle\test_no_chain_clean_log_dedupe.py -q
```

Expected: failures because `SignalAdmissionContext` / `SIGNAL_SKIPPED` do not exist and `process_signal()` lacks the new argument.

- [ ] **Step 3: Extend the lifecycle event model and gate signature**

```python
# src/runtime_v2/lifecycle/models.py
LifecycleEventType = Literal[
    "SIGNAL_ACCEPTED", "SIGNAL_REJECTED", "SIGNAL_SKIPPED", "TRADE_CHAIN_CREATED", "ENTRY_COMMAND_CREATED",
    ...
]
```

```python
# src/runtime_v2/lifecycle/entry_gate.py
def process_signal(
    self,
    enriched: EnrichedCanonicalMessage,
    open_chains: list[TradeChain],
    control_mode: ControlMode,
    admission: SignalAdmissionContext | None = None,
) -> SignalGateResult:
    admission = admission or SignalAdmissionContext()
    if (
        admission.signal_message_type == "inline_buttons"
        and admission.message_presentation_type != "INLINE_BUTTONS"
    ):
        return self._skip_signal(enriched.enrichment_id, "signal_message_type_mismatch")
```

- [ ] **Step 4: Add the internal skip helper and keep clean-log projection unchanged**

```python
# src/runtime_v2/lifecycle/entry_gate.py
def _skip_signal(self, eid: int | None, reason: str) -> SignalGateResult:
    event = LifecycleEvent(
        event_type="SIGNAL_SKIPPED",
        source_type="enrichment",
        source_id=str(eid),
        payload_json=json.dumps({"reason": reason, "source": "runtime"}),
        idempotency_key=f"signal_skipped:{eid}",
    )
    return SignalGateResult(
        trade_chain=None,
        lifecycle_events=[event],
        execution_commands=[],
        account_snapshot=None,
        market_snapshot=None,
        review_reason=reason,
    )
```

```python
# src/runtime_v2/lifecycle/entry_gate.py
if primary_class == "SIGNAL":
    admission = self._build_signal_admission_context(raw_message_id)
    result = self._gate.process_signal(enriched, open_chains, control_mode, admission)
    self._persist_signal(enriched, result)
```

`_NO_CHAIN_LOGGABLE_EVENTS` must stay:

```python
_NO_CHAIN_LOGGABLE_EVENTS = frozenset({"SIGNAL_REJECTED"})
```

That preserves the “silent skip, no clean_log” contract automatically.

- [ ] **Step 5: Re-run the lifecycle tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\lifecycle\test_entry_gate.py tests\runtime_v2\lifecycle\test_no_chain_clean_log_dedupe.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/runtime_v2/lifecycle/models.py src/runtime_v2/lifecycle/entry_gate.py tests/runtime_v2/lifecycle/test_entry_gate.py tests/runtime_v2/lifecycle/test_no_chain_clean_log_dedupe.py
git commit -m "feat: silently skip mismatched signal message types"
```

### Task 5: Update Example Config And Run Focused Regression Suite

**Files:**
- Modify: `config/channels.yaml`
- Modify: `README.md`

- [ ] **Step 1: Document the new config knob in the live example file**

```yaml
# config/channels.yaml
  - chat_id: -1003722628653
    topic_id: 3
    label: "PifSignal_A"
    active: true
    trader_id: trader_a
    parser_profile: trader_a
    signal_message_type: any
    blacklist: []
```

```yaml
# Inline-only topics can opt in explicitly
# signal_message_type:
#   any                -> default behavior
#   inline_buttons     -> only reposted messages with inline buttons can open chains
```

- [ ] **Step 2: Add one durable README note about the policy**

```markdown
# README.md
- `channels.yaml` now supports optional `signal_message_type` per channel/topic.
- `inline_buttons` preserves raw + parse but blocks chain creation for plain Telegram posts without inline buttons.
```

- [ ] **Step 3: Run the full focused regression suite**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\runtime_v2\test_intake_models.py tests\runtime_v2\test_raw_message_repository.py src\storage\tests\test_raw_messages_topic.py src\telegram\tests\test_listener_process_item.py tests\telegram\test_listener_edited_messages.py tests\runtime_v2\test_channel_config_resolver.py tests\runtime_v2\test_main_runtime_bootstrap.py tests\runtime_v2\lifecycle\test_entry_gate.py tests\runtime_v2\lifecycle\test_no_chain_clean_log_dedupe.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

```powershell
git add config/channels.yaml README.md
git commit -m "docs: document signal message type gate"
```

---

## Self-Review

### Spec coverage

- Persist Telegram presentation metadata: covered by Task 1 and Task 2.
- Configurable topic-level `signal_message_type`: covered by Task 3.
- Gate only in lifecycle `SIGNAL` path: covered by Task 4.
- Parser unchanged: enforced by scope; no parser files appear in any task.
- Silent skip with internal trace and no `clean_log`: covered by Task 4.
- Default behavior unchanged when no policy exists: covered by Task 3 + Task 4 tests.

No spec gaps found.

### Placeholder scan

- No `TODO` / `TBD`.
- Every code-changing step includes concrete snippets.
- Every test step includes exact commands and expected outcomes.

### Type consistency

- Raw metadata name: `message_presentation_type` everywhere.
- Config policy name: `signal_message_type` everywhere.
- Worker-to-gate context: `SignalAdmissionContext` everywhere.
- Silent internal event: `SIGNAL_SKIPPED` everywhere.

# TargetActionGroup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `update: UpdatePayload` + `targeted_actions: list[TargetedAction]` in `parser_v2` with a single target-centric `target_action_groups: list[TargetActionGroup]`, enabling multi-action multi-target UPDATE messages to parse as PARSED instead of PARTIAL.

**Architecture:** New classes `ActionItem` (typed action, replaces `UpdateOperation`+`TargetedAction`) and `TargetActionGroup` (target+actions, replaces both paths) are added to the canonical contract. The translator is updated to group intents by resolved targeting. Tests and flatteners are updated to match.

**Tech Stack:** Python 3.12, Pydantic v2, pytest

**Spec:** `docs/superpowers/specs/2026-05-14-target-action-groups-design.md`

---

## File Map

| File | Change |
|---|---|
| `src/parser_v2/contracts/canonical_message.py` | Add `ActionItem`, `TargetActionGroup`; remove `update`/`targeted_actions`/`target_hints` from `CanonicalMessage`; update validator + `__all__` |
| `src/parser_v2/translation/canonical_translator.py` | Replace UPDATE path: produce `target_action_groups`; add `_build_target_action_groups`, `_resolve_target_hints`, `_targeting_key`; `_operation_from_intent` → returns `ActionItem` |
| `src/parser_v2/tests/test_contracts_canonical.py` | Replace `UpdateOperation`/`TargetedAction` usage with `ActionItem`/`TargetActionGroup` |
| `src/parser_v2/tests/test_runtime_target_binding.py` | Replace `.update.operations` / `.targeted_actions` with `.target_action_groups` |
| `src/parser_v2/tests/test_integration_design.py` | Same |
| `src/parser_v2/tests/test_canonical_translator_v2.py` | Same |
| `src/parser_v2/tests/test_runtime_profile_phase12.py` | Same |
| `parser_test/reporting/flatteners_v2.py` | `_build_all_fields` reads `target_action_groups`; new `_update_fields` signature |
| `parser_test/reporting/report_schema_v2.py` | Replace `targeted_actions_count`/`targeted_actions_summary` with `groups_count`/`actions_count`/`actions_summary`; remove `operations_count`/`operations_summary` |
| `parser_test/reporting/tests/test_flatteners_v2.py` | Update JSON fixtures to use `target_action_groups` |

---

## Task 1: Add ActionItem + TargetActionGroup to contracts (additive)

**Files:**
- Modify: `src/parser_v2/contracts/canonical_message.py`

- [ ] **Step 1.1: Write failing tests for new classes**

Create `src/parser_v2/tests/test_target_action_group.py`:

```python
from __future__ import annotations
import pytest
from src.parser_v2.contracts.canonical_message import ActionItem, TargetActionGroup, SetStopOperation
from src.parser_v2.contracts.context import TargetHints


def test_action_item_set_stop_valid():
    item = ActionItem(
        action_type="SET_STOP",
        set_stop=SetStopOperation(target_type="ENTRY"),
        source_intent="MOVE_STOP_TO_BE",
    )
    assert item.action_type == "SET_STOP"
    assert item.set_stop.target_type == "ENTRY"


def test_action_item_rejects_wrong_payload():
    from src.parser_v2.contracts.canonical_message import CloseOperation
    with pytest.raises(Exception):
        ActionItem(
            action_type="SET_STOP",
            close=CloseOperation(close_scope="FULL"),
            source_intent="MOVE_STOP_TO_BE",
        )


def test_action_item_requires_exactly_one_payload():
    from src.parser_v2.contracts.canonical_message import CancelPendingOperation
    with pytest.raises(Exception):
        ActionItem(
            action_type="CANCEL_PENDING",
            source_intent="CANCEL_PENDING",
        )


def test_target_action_group_valid():
    group = TargetActionGroup(
        targeting=TargetHints(reply_to_message_id=100),
        actions=[
            ActionItem(
                action_type="SET_STOP",
                set_stop=SetStopOperation(target_type="ENTRY"),
                source_intent="MOVE_STOP_TO_BE",
            )
        ],
    )
    assert len(group.actions) == 1
    assert group.secondary_targeting is None


def test_target_action_group_with_secondary_targeting():
    group = TargetActionGroup(
        targeting=TargetHints(telegram_message_ids=[2712], scope_hint="SINGLE_SIGNAL"),
        secondary_targeting=TargetHints(reply_to_message_id=100),
        actions=[
            ActionItem(
                action_type="SET_STOP",
                set_stop=SetStopOperation(target_type="ENTRY"),
                source_intent="MOVE_STOP_TO_BE",
            )
        ],
    )
    assert group.secondary_targeting.reply_to_message_id == 100
    assert group.targeting.telegram_message_ids == [2712]


def test_target_action_group_rejects_empty_actions():
    with pytest.raises(Exception):
        TargetActionGroup(
            targeting=TargetHints(reply_to_message_id=100),
            actions=[],
        )
```

- [ ] **Step 1.2: Run tests to confirm they fail**

```
pytest src/parser_v2/tests/test_target_action_group.py -v
```

Expected: `ImportError` — `ActionItem` and `TargetActionGroup` not yet defined.

- [ ] **Step 1.3: Add ActionItem and TargetActionGroup to canonical_message.py**

In `src/parser_v2/contracts/canonical_message.py`, after the `InvalidateSetupOperation` class (around line 74) and before `UpdateOperation`, insert:

```python
class ActionItem(CanonicalModel):
    action_type: UpdateOperationType
    set_stop: SetStopOperation | None = None
    close: CloseOperation | None = None
    cancel_pending: CancelPendingOperation | None = None
    modify_entries: ModifyEntriesOperation | None = None
    modify_targets: ModifyTargetsOperation | None = None
    invalidate_setup: InvalidateSetupOperation | None = None
    source_intent: IntentType
    source_intent_id: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    raw_fragment: str | None = None

    @model_validator(mode="after")
    def _validate_payload_matches_type(self) -> ActionItem:
        expected_by_type = {
            "SET_STOP": "set_stop",
            "CLOSE": "close",
            "CANCEL_PENDING": "cancel_pending",
            "MODIFY_ENTRIES": "modify_entries",
            "MODIFY_TARGETS": "modify_targets",
            "INVALIDATE_SETUP": "invalidate_setup",
        }
        expected = expected_by_type[self.action_type]
        populated = [f for f in expected_by_type.values() if getattr(self, f) is not None]
        if populated != [expected]:
            raise ValueError(
                f"{self.action_type} requires only `{expected}` to be populated; got {populated}"
            )
        return self


class TargetActionGroup(CanonicalModel):
    targeting: TargetHints
    secondary_targeting: TargetHints | None = None
    actions: list[ActionItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_actions_non_empty(self) -> TargetActionGroup:
        if not self.actions:
            raise ValueError("TargetActionGroup requires non-empty actions")
        return self
```

Add to `__all__` at bottom of file:
```python
    "ActionItem",
    "TargetActionGroup",
```

- [ ] **Step 1.4: Run tests to confirm they pass**

```
pytest src/parser_v2/tests/test_target_action_group.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 1.5: Run full parser_v2 test suite to confirm no regressions**

```
pytest src/parser_v2/ -v
```

Expected: all existing tests still PASS (additive change only).

- [ ] **Step 1.6: Commit**

```bash
git add src/parser_v2/contracts/canonical_message.py src/parser_v2/tests/test_target_action_group.py
git commit -m "feat(parser_v2): add ActionItem and TargetActionGroup to canonical contract"
```

---

## Task 2: Replace CanonicalMessage fields (breaking schema change)

**Files:**
- Modify: `src/parser_v2/contracts/canonical_message.py`

- [ ] **Step 2.1: Write new CanonicalMessage tests before changing the schema**

In `src/parser_v2/tests/test_target_action_group.py`, append:

```python
def test_canonical_message_update_uses_target_action_groups():
    from src.parser_v2.contracts.canonical_message import CanonicalMessage
    from src.parser_v2.contracts.context import RawContext
    msg = CanonicalMessage(
        parser_profile="test",
        primary_class="UPDATE",
        parse_status="PARSED",
        confidence=0.9,
        target_action_groups=[
            TargetActionGroup(
                targeting=TargetHints(reply_to_message_id=100),
                actions=[
                    ActionItem(
                        action_type="SET_STOP",
                        set_stop=SetStopOperation(target_type="ENTRY"),
                        source_intent="MOVE_STOP_TO_BE",
                    )
                ],
            )
        ],
        raw_context=RawContext(raw_text="стоп в бу"),
    )
    assert len(msg.target_action_groups) == 1
    assert msg.target_action_groups[0].targeting.reply_to_message_id == 100


def test_canonical_message_update_requires_target_action_groups_when_parsed():
    from src.parser_v2.contracts.canonical_message import CanonicalMessage
    from src.parser_v2.contracts.context import RawContext
    with pytest.raises(Exception):
        CanonicalMessage(
            parser_profile="test",
            primary_class="UPDATE",
            parse_status="PARSED",
            confidence=0.9,
            target_action_groups=[],
            raw_context=RawContext(raw_text="test"),
        )


def test_canonical_message_signal_forbids_target_action_groups():
    from src.parser_v2.contracts.canonical_message import CanonicalMessage, SignalPayload
    from src.parser_v2.contracts.context import RawContext
    from src.parser_v2.contracts.entities import EntryLeg, StopLoss, TakeProfit, Price
    with pytest.raises(Exception):
        CanonicalMessage(
            parser_profile="test",
            primary_class="SIGNAL",
            parse_status="PARSED",
            confidence=0.9,
            signal=SignalPayload(
                symbol="BTCUSDT", side="LONG",
                entry_structure="ONE_SHOT",
                entries=[EntryLeg(sequence=1, entry_type="LIMIT", price=Price(raw="100", value=100.0))],
                stop_loss=StopLoss(price=Price(raw="90", value=90.0)),
                take_profits=[TakeProfit(sequence=1, price=Price(raw="110", value=110.0))],
            ),
            target_action_groups=[
                TargetActionGroup(
                    targeting=TargetHints(reply_to_message_id=1),
                    actions=[ActionItem(
                        action_type="SET_STOP",
                        set_stop=SetStopOperation(target_type="ENTRY"),
                        source_intent="MOVE_STOP_TO_BE",
                    )],
                )
            ],
            raw_context=RawContext(raw_text="test"),
        )
```

- [ ] **Step 2.2: Run new tests to confirm they fail**

```
pytest src/parser_v2/tests/test_target_action_group.py::test_canonical_message_update_uses_target_action_groups -v
```

Expected: FAIL — `CanonicalMessage` has no `target_action_groups` field yet.

- [ ] **Step 2.3: Replace CanonicalMessage fields in canonical_message.py**

In `CanonicalMessage`, replace:
```python
    update: UpdatePayload | None = None
    report: ReportPayload | None = None
    info: InfoPayload | None = None
    targeted_actions: list[TargetedAction] = Field(default_factory=list)
    target_hints: TargetHints | None = None
```

With:
```python
    report: ReportPayload | None = None
    info: InfoPayload | None = None
    target_action_groups: list[TargetActionGroup] = Field(default_factory=list)
```

Replace the `_validate_primary_class_payloads` validator with:

```python
    @model_validator(mode="after")
    def _validate_primary_class_payloads(self) -> CanonicalMessage:
        has_update_work = bool(self.target_action_groups)

        if self.primary_class == "SIGNAL":
            if self.signal is None:
                raise ValueError("SIGNAL requires signal payload")
            if self.target_action_groups:
                raise ValueError("SIGNAL forbids target_action_groups")

        elif self.primary_class == "UPDATE":
            if self.signal is not None:
                raise ValueError("UPDATE forbids signal payload")
            if self.parse_status == "PARSED" and not has_update_work:
                raise ValueError("PARSED UPDATE requires at least one target_action_group")
            if (
                self.parse_status == "PARTIAL"
                and not has_update_work
                and "ambiguous_target_intent_binding" not in self.warnings
            ):
                raise ValueError(
                    "PARTIAL UPDATE without target_action_groups requires "
                    "ambiguous_target_intent_binding warning"
                )

        elif self.primary_class == "REPORT":
            if self.report is None:
                raise ValueError("REPORT requires report payload")
            if self.signal is not None:
                raise ValueError("REPORT forbids signal payload")
            if self.target_action_groups:
                raise ValueError("REPORT forbids target_action_groups")

        elif self.primary_class == "INFO":
            if (
                self.signal is not None
                or self.report is not None
                or self.target_action_groups
            ):
                raise ValueError("INFO forbids signal/report payloads and target_action_groups")

        return self
```

Remove `UpdatePayload` and `TargetedAction` from `__all__` (keep the classes defined for now — translator still references them internally; they will be removed in Task 3).

- [ ] **Step 2.4: Run new schema tests**

```
pytest src/parser_v2/tests/test_target_action_group.py -v
```

Expected: new tests PASS. Existing tests in other files will now FAIL — that is expected and will be fixed in Task 4.

- [ ] **Step 2.5: Commit**

```bash
git add src/parser_v2/contracts/canonical_message.py src/parser_v2/tests/test_target_action_group.py
git commit -m "feat(parser_v2): replace update/targeted_actions with target_action_groups in CanonicalMessage"
```

---

## Task 3: Update canonical_translator to produce target_action_groups

**Files:**
- Modify: `src/parser_v2/translation/canonical_translator.py`

- [ ] **Step 3.1: Replace the UPDATE path in CanonicalTranslator.translate()**

Replace lines 91–148 (the `if parsed.primary_class == "UPDATE":` block) with:

```python
        if parsed.primary_class == "UPDATE":
            intent_op_pairs = [
                (intent, _operation_from_intent(intent))
                for intent in parsed.intents
                if intent.type in UPDATE_INTENTS
            ]
            intent_op_pairs = [(i, op) for i, op in intent_op_pairs if op is not None]

            for _intent, _op in intent_op_pairs:
                if (
                    _intent.type == "MOVE_STOP"
                    and _op.set_stop is not None
                    and _op.set_stop.target_type == "ENTRY"
                ):
                    warnings = _append_once(warnings, "move_stop_no_price_defaulted_to_be")

            target_action_groups = _build_target_action_groups(intent_op_pairs, parsed.target_hints)

            if (
                not target_action_groups
                and parse_status in {"PARSED", "PARTIAL"}
                and "ambiguous_target_intent_binding" not in warnings
            ):
                parse_status = "ERROR"
                warnings = _append_once(warnings, "canonical_translation_without_update_operation")

            return CanonicalMessage(
                parser_profile=parsed.parser_profile,
                primary_class=parsed.primary_class,
                parse_status=parse_status,
                confidence=parsed.confidence,
                primary_intent=parsed.primary_intent,
                intents=list(dict.fromkeys(intents)),
                report=_report_payload(parsed.intents),
                target_action_groups=target_action_groups,
                warnings=warnings,
                diagnostics=parsed.diagnostics,
                raw_context=parsed.raw_context,
            )
```

- [ ] **Step 3.2: Replace _operation_from_intent to return ActionItem**

Replace the entire `_operation_from_intent` function. Change all `UpdateOperation(` calls to `ActionItem(` and `op_type=` to `action_type=`:

```python
from src.parser_v2.contracts.canonical_message import (
    ActionItem,
    TargetActionGroup,
    CancelPendingOperation,
    CanonicalMessage,
    CloseOperation,
    InfoPayload,
    InvalidateSetupOperation,
    ModifyEntriesOperation,
    ModifyTargetsOperation,
    ReportEvent,
    ReportPayload,
    ReportResult,
    SetStopOperation,
    SignalPayload,
)
```

(Remove `TargetedAction`, `UpdateOperation`, `UpdatePayload` from imports.)

Replace `_operation_from_intent`:

```python
def _operation_from_intent(intent: ParsedIntent) -> ActionItem | None:
    entities = intent.entities

    if intent.type == "MOVE_STOP_TO_BE":
        return ActionItem(
            action_type="SET_STOP",
            set_stop=SetStopOperation(target_type="ENTRY"),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "MOVE_STOP" and isinstance(entities, MoveStopEntities):
        if entities.new_stop_price is not None:
            set_stop = SetStopOperation(target_type="PRICE", price=entities.new_stop_price)
        elif entities.stop_to_tp_level is not None:
            set_stop = SetStopOperation(target_type="TP_LEVEL", tp_level=entities.stop_to_tp_level)
        else:
            set_stop = SetStopOperation(target_type="ENTRY")
        return ActionItem(
            action_type="SET_STOP",
            set_stop=set_stop,
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "CLOSE_FULL":
        close_price = entities.close_price if isinstance(entities, CloseFullEntities) else None
        return ActionItem(
            action_type="CLOSE",
            close=CloseOperation(close_scope="FULL", close_price=close_price),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "CLOSE_PARTIAL" and isinstance(entities, ClosePartialEntities):
        return ActionItem(
            action_type="CLOSE",
            close=CloseOperation(
                close_scope="PARTIAL",
                fraction=entities.fraction,
                close_price=entities.close_price,
            ),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "CANCEL_PENDING":
        cancel_scope_hint: CancelScopeHint = (
            entities.cancel_scope_hint if isinstance(entities, CancelPendingEntities) else "UNKNOWN"
        )
        return ActionItem(
            action_type="CANCEL_PENDING",
            cancel_pending=CancelPendingOperation(cancel_scope_hint=cancel_scope_hint),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "INVALIDATE_SETUP" and isinstance(entities, InvalidateSetupEntities):
        return ActionItem(
            action_type="INVALIDATE_SETUP",
            invalidate_setup=InvalidateSetupOperation(reason_text=entities.reason_text),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "MODIFY_ENTRY" and isinstance(entities, ModifyEntryEntities):
        return ActionItem(
            action_type="MODIFY_ENTRIES",
            modify_entries=ModifyEntriesOperation(
                kind=entities.mode,
                entries=entities.entries,
                entry_structure=entities.entry_structure,
                entry_selector=entities.entry_selector,
            ),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "ADD_ENTRY" and isinstance(entities, AddEntryEntities):
        entries: list[EntryLeg] = []
        if entities.entry_price is not None or entities.entry_type is not None:
            entries.append(
                EntryLeg(
                    sequence=1,
                    entry_type=entities.entry_type or "LIMIT",
                    price=entities.entry_price,
                )
            )
        return ActionItem(
            action_type="MODIFY_ENTRIES",
            modify_entries=ModifyEntriesOperation(kind="ADD", entries=entries),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "REENTER" and isinstance(entities, ReenterEntities):
        entries = [
            EntryLeg(
                sequence=index,
                entry_type=entities.entry_type or "LIMIT",
                price=price,
            )
            for index, price in enumerate(entities.entries, start=1)
        ]
        return ActionItem(
            action_type="MODIFY_ENTRIES",
            modify_entries=ModifyEntriesOperation(
                kind="REENTER",
                entries=entries,
                entry_structure=entities.entry_structure,
            ),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    if intent.type == "MODIFY_TARGETS" and isinstance(entities, ModifyTargetsEntities):
        return ActionItem(
            action_type="MODIFY_TARGETS",
            modify_targets=ModifyTargetsOperation(
                mode=entities.mode,
                take_profits=[
                    TakeProfit(sequence=index, price=price)
                    for index, price in enumerate(entities.take_profits, start=1)
                ],
                target_tp_level=entities.target_tp_level,
            ),
            source_intent=intent.type,
            source_intent_id=intent.intent_id,
            confidence=intent.confidence,
            raw_fragment=intent.raw_fragment,
        )

    return None
```

- [ ] **Step 3.3: Add _build_target_action_groups, _resolve_target_hints, _targeting_key**

Add after `_operation_from_intent`:

```python
def _build_target_action_groups(
    intent_op_pairs: list[tuple[ParsedIntent, ActionItem]],
    message_target_hints: TargetHints | None,
) -> list[TargetActionGroup]:
    if not intent_op_pairs:
        return []

    groups: dict[str, tuple[TargetHints, TargetHints | None, list[ActionItem]]] = {}

    for intent, action in intent_op_pairs:
        primary_hints, secondary_hints = _resolve_target_hints(intent, message_target_hints)
        group_key = _targeting_key(primary_hints)

        if group_key not in groups:
            groups[group_key] = (primary_hints, secondary_hints, [action])
        else:
            groups[group_key][2].append(action)

    return [
        TargetActionGroup(targeting=primary, secondary_targeting=secondary, actions=actions)
        for primary, secondary, actions in groups.values()
    ]


def _resolve_target_hints(
    intent: ParsedIntent,
    message_target_hints: TargetHints | None,
) -> tuple[TargetHints, TargetHints | None]:
    base = intent.target_hints or message_target_hints
    if base is None:
        return TargetHints(scope_hint="SINGLE_SIGNAL"), None

    if (
        base.scope_hint == "UNKNOWN"
        and (base.telegram_message_ids or base.telegram_links or base.explicit_ids)
    ):
        base = base.model_copy(update={"scope_hint": "SINGLE_SIGNAL"})

    has_explicit = bool(base.telegram_message_ids or base.telegram_links or base.explicit_ids)
    has_reply = bool(base.reply_to_message_id)

    if has_explicit and has_reply:
        secondary = TargetHints(reply_to_message_id=base.reply_to_message_id)
        primary = base.model_copy(update={"reply_to_message_id": None})
        return primary, secondary

    return base, None


def _targeting_key(hints: TargetHints) -> str:
    ids = "|".join(str(x) for x in sorted(hints.telegram_message_ids))
    links = "|".join(sorted(hints.telegram_links))
    explicit = "|".join(sorted(hints.explicit_ids))
    symbols = "|".join(sorted(hints.symbols))
    return f"ids:{ids};links:{links};explicit:{explicit};reply:{hints.reply_to_message_id};scope:{hints.scope_hint};symbols:{symbols}"
```

- [ ] **Step 3.4: Remove dead code from translator**

Remove the functions `_make_targeted_action` and `_operation_params` (replaced by `_build_target_action_groups`).

Also remove `_should_use_targeted_actions` (no longer needed).

- [ ] **Step 3.5: Update SIGNAL path in translate() — remove target_hints**

In the SIGNAL return (lines 77–89), remove `target_hints=parsed.target_hints`. Final SIGNAL return:

```python
            return CanonicalMessage(
                parser_profile=parsed.parser_profile,
                primary_class=parsed.primary_class,
                parse_status=parse_status,
                confidence=parsed.confidence,
                primary_intent=parsed.primary_intent if parsed.primary_intent not in UPDATE_INTENTS else None,
                intents=intents,
                signal=_signal_payload(parsed.signal),
                warnings=warnings,
                diagnostics=parsed.diagnostics,
                raw_context=parsed.raw_context,
            )
```

Do the same for the REPORT and INFO returns (remove `target_hints=parsed.target_hints`).

- [ ] **Step 3.6: Run translator-specific tests**

```
pytest src/parser_v2/tests/test_target_action_group.py src/parser_v2/tests/test_canonical_translator_v2.py -v
```

Expected: `test_target_action_group.py` PASS. `test_canonical_translator_v2.py` will FAIL — fix in Task 4.

- [ ] **Step 3.7: Commit**

```bash
git add src/parser_v2/translation/canonical_translator.py
git commit -m "feat(parser_v2): rewrite canonical_translator to produce target_action_groups"
```

---

## Task 4: Fix parser_v2 test files

**Files:**
- Modify: `src/parser_v2/tests/test_contracts_canonical.py`
- Modify: `src/parser_v2/tests/test_runtime_target_binding.py`
- Modify: `src/parser_v2/tests/test_integration_design.py`
- Modify: `src/parser_v2/tests/test_canonical_translator_v2.py`
- Modify: `src/parser_v2/tests/test_runtime_profile_phase12.py`

- [ ] **Step 4.1: Fix test_contracts_canonical.py**

Replace the entire file:

```python
from __future__ import annotations
import pytest
from src.parser_v2.contracts.canonical_message import ActionItem, TargetActionGroup, SetStopOperation
from src.parser_v2.contracts.context import TargetHints


def test_action_item_has_source_intent_id():
    item = ActionItem(
        action_type="SET_STOP",
        set_stop=SetStopOperation(target_type="ENTRY"),
        source_intent="MOVE_STOP_TO_BE",
    )
    assert item.source_intent_id is None


def test_action_item_stores_source_intent_id():
    item = ActionItem(
        action_type="SET_STOP",
        set_stop=SetStopOperation(target_type="ENTRY"),
        source_intent="MOVE_STOP_TO_BE",
        source_intent_id="MOVE_STOP_TO_BE#1",
    )
    assert item.source_intent_id == "MOVE_STOP_TO_BE#1"


def test_target_action_group_has_source_intent_id_in_action():
    group = TargetActionGroup(
        targeting=TargetHints(reply_to_message_id=100),
        actions=[
            ActionItem(
                action_type="SET_STOP",
                set_stop=SetStopOperation(target_type="ENTRY"),
                source_intent="MOVE_STOP_TO_BE",
            )
        ],
    )
    assert group.actions[0].source_intent_id is None


def test_target_action_group_stores_source_intent_id():
    group = TargetActionGroup(
        targeting=TargetHints(reply_to_message_id=100),
        actions=[
            ActionItem(
                action_type="SET_STOP",
                set_stop=SetStopOperation(target_type="ENTRY"),
                source_intent="MOVE_STOP_TO_BE",
                source_intent_id="MOVE_STOP_TO_BE#0",
            )
        ],
    )
    assert group.actions[0].source_intent_id == "MOVE_STOP_TO_BE#0"


def test_canonical_message_validator_uses_new_warning():
    from src.parser_v2.contracts.canonical_message import CanonicalMessage
    from src.parser_v2.contracts.context import RawContext
    msg = CanonicalMessage(
        parser_profile="test",
        primary_class="UPDATE",
        parse_status="PARTIAL",
        confidence=0.5,
        warnings=["ambiguous_target_intent_binding"],
        raw_context=RawContext(raw_text="test"),
    )
    assert "ambiguous_target_intent_binding" in msg.warnings


def test_canonical_message_validator_rejects_old_warning():
    from src.parser_v2.contracts.canonical_message import CanonicalMessage
    from src.parser_v2.contracts.context import RawContext
    with pytest.raises(Exception):
        CanonicalMessage(
            parser_profile="test",
            primary_class="UPDATE",
            parse_status="PARTIAL",
            confidence=0.5,
            warnings=["multi_ref_mixed_intents_not_supported"],
            raw_context=RawContext(raw_text="test"),
        )
```

- [ ] **Step 4.2: Fix test_runtime_target_binding.py**

Replace lines 43–74 (the three tests that use `.update.operations` and `.targeted_actions`):

```python
def test_runtime_assigns_occurrence_ids():
    profile = _MockProfile()
    profile.set_intents([
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
    ])
    result = _run("стоп в бу\nстоп в бу", profile)
    all_actions = [a for g in result.target_action_groups for a in g.actions]
    ids = [a.source_intent_id for a in all_actions]
    assert len(ids) == 2
    assert ids[0] != ids[1]


def test_runtime_with_reply_produces_target_action_group():
    profile = _MockProfile()
    profile.set_intents([
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
    ])
    result = _run("стоп в бу", profile, reply_id=100)
    assert len(result.target_action_groups) == 1
    assert result.target_action_groups[0].targeting.reply_to_message_id == 100


def test_runtime_global_refs_two_ops_not_partial():
    profile = _MockProfile()
    profile.set_intents([
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="CANCEL_PENDING", category="UPDATE", confidence=0.9),
    ])
    text = "https://t.me/c/777/111\nhttps://t.me/c/777/222\nстоп в бу\nлимитки убираем"
    result = _run(text, profile)
    assert result.parse_status != "PARTIAL"
    all_actions = [a for g in result.target_action_groups for a in g.actions]
    assert len(all_actions) == 2
```

- [ ] **Step 4.3: Fix test_integration_design.py**

Replace tests that reference `.update`, `.update.operations`, `.targeted_actions`:

```python
def test_B1_two_same_intents_preserved():
    """Two occurrences of same IntentType must be preserved."""
    intents = [
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
    ]
    result = _run("стоп в бу\nстоп в бу", _SimpleProfile(intents))
    all_actions = [a for g in result.target_action_groups for a in g.actions]
    ids = [a.source_intent_id for a in all_actions]
    assert len(set(ids)) == 2


def test_C1_reply_applies_to_multiple_operations():
    """Reply + two intents → both operations on the reply target."""
    intents = [
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="CANCEL_PENDING", category="UPDATE", confidence=0.9),
    ]
    result = _run("стоп в бу\nлимитки убираем", _SimpleProfile(intents), reply_id=100)
    assert len(result.target_action_groups) == 1
    group = result.target_action_groups[0]
    assert group.targeting.reply_to_message_id == 100
    assert len(group.actions) == 2


def test_C3_global_ref_list_multiple_ops_not_partial():
    """Global link list + multiple ops → PARSED, 1 group with 2 actions."""
    intents = [
        ParsedIntent(type="MOVE_STOP_TO_BE", category="UPDATE", confidence=0.9),
        ParsedIntent(type="CANCEL_PENDING", category="UPDATE", confidence=0.9),
    ]
    text = "https://t.me/c/777/111\nhttps://t.me/c/777/222\nстоп в бу\nлимитки убираем"
    result = _run(text, _SimpleProfile(intents))
    assert result.parse_status == "PARSED"
    assert len(result.target_action_groups) == 1
    group = result.target_action_groups[0]
    assert 111 in group.targeting.telegram_message_ids
    assert 222 in group.targeting.telegram_message_ids
    assert len(group.actions) == 2
```

- [ ] **Step 4.4: Fix test_canonical_translator_v2.py**

Replace tests that reference `.targeted_actions`:

```python
def test_mixed_ops_on_global_target_produces_target_action_group():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", occurrence_index=0),
        _make_intent("CANCEL_PENDING", occurrence_index=0),
    ]
    global_hints = TargetHints(
        target_source="MESSAGE_TEXT_LINK",
        telegram_message_ids=[111, 222],
    )
    parsed = _make_parsed(intents, target_hints=global_hints)
    result = CanonicalTranslator().translate(parsed)

    assert result.parse_status == "PARSED"
    assert len(result.target_action_groups) == 1
    group = result.target_action_groups[0]
    action_types = {a.action_type for a in group.actions}
    assert "SET_STOP" in action_types
    assert "CANCEL_PENDING" in action_types
    assert group.targeting.telegram_message_ids == [111, 222]


def test_mixed_ops_no_partial_warning():
    intents = [
        _make_intent("MOVE_STOP_TO_BE", occurrence_index=0),
        _make_intent("CANCEL_PENDING", occurrence_index=0),
    ]
    parsed = _make_parsed(intents, target_hints=TargetHints(telegram_message_ids=[111]))
    result = CanonicalTranslator().translate(parsed)
    assert "multi_ref_mixed_intents_not_supported" not in result.warnings
    assert "ambiguous_target_intent_binding" not in result.warnings


def test_source_intent_id_propagated():
    intents = [_make_intent("MOVE_STOP_TO_BE", occurrence_index=1)]
    parsed = _make_parsed(intents, target_hints=TargetHints(telegram_message_ids=[111]))
    result = CanonicalTranslator().translate(parsed)
    assert result.target_action_groups[0].actions[0].source_intent_id == "MOVE_STOP_TO_BE#1"
```

- [ ] **Step 4.5: Fix test_runtime_profile_phase12.py**

Find and replace assertions on `.update.operations`:

```python
# Old:
# assert canonical.update.operations[0].op_type == "SET_STOP"
# canonical.update.operations[0].set_stop.target_type == "ENTRY"

# New:
assert len(canonical.target_action_groups) >= 1
first_action = canonical.target_action_groups[0].actions[0]
assert first_action.action_type == "SET_STOP"
assert first_action.set_stop.target_type == "ENTRY"
```

- [ ] **Step 4.6: Run all parser_v2 tests**

```
pytest src/parser_v2/ -v
```

Expected: all tests PASS.

- [ ] **Step 4.7: Commit**

```bash
git add src/parser_v2/tests/
git commit -m "test(parser_v2): update all tests to use target_action_groups"
```

---

## Task 5: Update flatteners, schema, and flattener tests

**Files:**
- Modify: `parser_test/reporting/flatteners_v2.py`
- Modify: `parser_test/reporting/report_schema_v2.py`
- Modify: `parser_test/reporting/tests/test_flatteners_v2.py`

- [ ] **Step 5.1: Write failing flattener test for new structure**

In `parser_test/reporting/tests/test_flatteners_v2.py`, add an UPDATE row fixture and test:

```python
def _update_row_new() -> ReportRow:
    canonical = {
        "schema_version": "2.0",
        "parser_profile": "trader_a",
        "primary_class": "UPDATE",
        "parse_status": "PARSED",
        "primary_intent": "MOVE_STOP_TO_BE",
        "intents": ["MOVE_STOP_TO_BE", "CANCEL_PENDING"],
        "confidence": 0.9,
        "warnings": [],
        "diagnostics": {},
        "target_action_groups": [
            {
                "targeting": {
                    "telegram_message_ids": [2712, 2713, 2718],
                    "scope_hint": "SINGLE_SIGNAL",
                    "telegram_links": [],
                    "explicit_ids": [],
                    "symbols": [],
                    "target_source": "UNKNOWN",
                    "reply_to_message_id": None,
                },
                "secondary_targeting": None,
                "actions": [
                    {
                        "action_type": "SET_STOP",
                        "set_stop": {"target_type": "ENTRY"},
                        "source_intent": "MOVE_STOP_TO_BE",
                        "source_intent_id": "MOVE_STOP_TO_BE#0",
                        "confidence": 0.9,
                        "raw_fragment": "стоп в бу",
                    },
                    {
                        "action_type": "CANCEL_PENDING",
                        "cancel_pending": {"cancel_scope_hint": "TARGETED"},
                        "source_intent": "CANCEL_PENDING",
                        "source_intent_id": "CANCEL_PENDING#0",
                        "confidence": 0.9,
                        "raw_fragment": "лимитки убираем",
                    },
                ],
            }
        ],
        "raw_context": {"raw_text": "стоп в бу\nлимитки убираем"},
    }
    return ReportRow(
        run_id=2,
        raw_message_id=20,
        trader_id="trader_a",
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARSED",
        primary_intent="MOVE_STOP_TO_BE",
        confidence=0.9,
        canonical_json=json.dumps(canonical),
        warnings_json=None,
        diagnostics_json=None,
        error_status="OK",
        error_message=None,
        telegram_message_id=99,
        source_chat_id="chat1",
        source_topic_id=None,
        reply_to_message_id=None,
        message_ts="2026-05-14T10:00:00",
        raw_text="стоп в бу\nлимитки убираем",
    )


def test_flatten_update_groups_count():
    row = _update_row_new()
    result = flatten_for_scope("UPDATE", row)
    assert result["groups_count"] == 1
    assert result["actions_count"] == 2


def test_flatten_update_actions_summary():
    row = _update_row_new()
    result = flatten_for_scope("UPDATE", row)
    assert "SET_STOP" in result["actions_summary"]
    assert "CANCEL_PENDING" in result["actions_summary"]


def test_flatten_update_target_info():
    row = _update_row_new()
    result = flatten_for_scope("UPDATE", row)
    assert result["target_scope_hint"] == "SINGLE_SIGNAL"
    assert result["target_telegram_message_ids"] == "2712|2713|2718"


def test_flatten_update_first_action_details():
    row = _update_row_new()
    result = flatten_for_scope("UPDATE", row)
    assert result["set_stop_target_type"] == "ENTRY"
```

- [ ] **Step 5.2: Run tests to confirm they fail**

```
pytest parser_test/reporting/tests/test_flatteners_v2.py::test_flatten_update_groups_count -v
```

Expected: FAIL — `groups_count` key not in result.

- [ ] **Step 5.3: Update _build_all_fields in flatteners_v2.py**

Replace lines 51–55:
```python
    update = canonical.get("update") or {}
    targeted_actions = canonical.get("targeted_actions") or []
    target_hints = canonical.get("target_hints") or {}
    if update or targeted_actions:
        fields.update(_update_fields(update, targeted_actions, target_hints))
```

With:
```python
    target_action_groups = canonical.get("target_action_groups") or []
    if target_action_groups:
        fields.update(_update_fields(target_action_groups))
```

- [ ] **Step 5.4: Replace _update_fields in flatteners_v2.py**

Replace the entire `_update_fields` function:

```python
def _update_fields(target_action_groups: list[dict[str, Any]]) -> dict[str, Any]:
    all_actions: list[dict[str, Any]] = [
        action
        for group in target_action_groups
        for action in (group.get("actions") or [])
    ]
    first_group = target_action_groups[0] if target_action_groups else {}
    first_targeting = first_group.get("targeting") or {}
    first_action = all_actions[0] if all_actions else {}

    first_set_stop = first_action.get("set_stop") or {}
    first_close = first_action.get("close") or {}
    first_cancel = first_action.get("cancel_pending") or {}
    first_mod_entries = first_action.get("modify_entries") or {}
    first_mod_targets = first_action.get("modify_targets") or {}
    first_invalidate = first_action.get("invalidate_setup") or {}
    mod_entries_entries = first_mod_entries.get("entries") or []
    mod_targets_tps = first_mod_targets.get("take_profits") or []

    return {
        "groups_count": len(target_action_groups),
        "actions_count": len(all_actions),
        "actions_summary": "|".join(
            f"{a.get('action_type')}({a.get('source_intent')})" for a in all_actions
        ),
        "action_types": "|".join(a.get("action_type", "") for a in all_actions),
        "source_intents": "|".join(a.get("source_intent", "") for a in all_actions),
        "action_confidences": "|".join(str(a.get("confidence", "")) for a in all_actions),
        "action_raw_fragments": "|".join(a.get("raw_fragment", "") or "" for a in all_actions),
        "target_scope_hint": first_targeting.get("scope_hint"),
        "target_reply_to_message_id": first_targeting.get("reply_to_message_id"),
        "target_telegram_message_ids": "|".join(
            str(v) for v in (first_targeting.get("telegram_message_ids") or [])
        ),
        "target_telegram_links": "|".join(first_targeting.get("telegram_links") or []),
        "target_explicit_ids": "|".join(first_targeting.get("explicit_ids") or []),
        "target_symbols": "|".join(first_targeting.get("symbols") or []),
        "set_stop_target_type": first_set_stop.get("target_type"),
        "set_stop_price": (first_set_stop.get("price") or {}).get("value"),
        "set_stop_tp_level": first_set_stop.get("tp_level"),
        "close_scope": first_close.get("close_scope"),
        "close_fraction": first_close.get("fraction"),
        "close_price": (first_close.get("close_price") or {}).get("value"),
        "cancel_scope_hint": first_cancel.get("cancel_scope_hint"),
        "modify_entries_kind": first_mod_entries.get("kind"),
        "modify_entries_count": len(mod_entries_entries),
        "modify_entries_summary": "|".join(
            f"{e.get('sequence')}:{e.get('entry_type')}@{(e.get('price') or {}).get('value', '')}"
            for e in mod_entries_entries
        ),
        "modify_entries_entry_structure": first_mod_entries.get("entry_structure"),
        "modify_targets_mode": first_mod_targets.get("mode"),
        "modify_targets_count": len(mod_targets_tps),
        "modify_targets_prices": "|".join(
            str((tp.get("price") or {}).get("value", "")) for tp in mod_targets_tps
        ),
        "modify_targets_target_tp_level": first_mod_targets.get("target_tp_level"),
        "invalidate_reason_text": first_invalidate.get("reason_text"),
    }
```

- [ ] **Step 5.5: Update _UPDATE_COLUMNS in report_schema_v2.py**

Replace `_UPDATE_COLUMNS`:

```python
_UPDATE_COLUMNS = [
    "primary_intent",
    "intents",
    "groups_count",
    "actions_count",
    "actions_summary",
    "action_types",
    "source_intents",
    "action_confidences",
    "action_raw_fragments",
    "target_scope_hint",
    "target_reply_to_message_id",
    "target_telegram_message_ids",
    "target_telegram_links",
    "target_explicit_ids",
    "target_symbols",
    "set_stop_target_type",
    "set_stop_price",
    "set_stop_tp_level",
    "close_scope",
    "close_fraction",
    "close_price",
    "cancel_scope_hint",
    "modify_entries_kind",
    "modify_entries_count",
    "modify_entries_summary",
    "modify_entries_entry_structure",
    "modify_targets_mode",
    "modify_targets_count",
    "modify_targets_prices",
    "modify_targets_target_tp_level",
    "invalidate_reason_text",
]
```

- [ ] **Step 5.6: Update existing UPDATE fixtures in test_flatteners_v2.py**

Find any existing `_update_row` fixture or test that uses the old JSON shape (`"update"`, `"targeted_actions"`, `"target_hints"`) and replace with the new `"target_action_groups"` shape (same pattern as `_update_row_new()` above).

- [ ] **Step 5.7: Run all flattener tests**

```
pytest parser_test/reporting/tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 5.8: Commit**

```bash
git add parser_test/reporting/flatteners_v2.py parser_test/reporting/report_schema_v2.py parser_test/reporting/tests/test_flatteners_v2.py
git commit -m "feat(parser_test): update flatteners and schema for target_action_groups"
```

---

## Task 6: Final cleanup and full test run

**Files:**
- Modify: `src/parser_v2/contracts/canonical_message.py` — remove `UpdatePayload`, `TargetedAction` definitions and remaining exports

- [ ] **Step 6.1: Remove UpdatePayload and TargetedAction from canonical_message.py**

Remove the `UpdatePayload` class definition and the `TargetedAction` class definition. Remove them from `__all__`. Keep `UpdateOperation` only if still imported by other files — check first:

```
grep -r "UpdateOperation\|UpdatePayload\|TargetedAction" src/parser_v2/ --include="*.py" -l
```

If only `canonical_message.py` references them, remove all three. If `UpdateOperation` is still referenced (e.g., by tests not yet updated), fix those references first.

- [ ] **Step 6.2: Run complete test suite**

```
pytest src/parser_v2/ parser_test/ -v
```

Expected: all tests PASS, zero failures.

- [ ] **Step 6.3: Verify no references to removed fields remain**

```
grep -r "\.update\b\|targeted_actions\|UpdatePayload\|TargetedAction\|target_hints" src/parser_v2/ parser_test/ --include="*.py"
```

Expected: no matches (or only comments/docstrings).

- [ ] **Step 6.4: Final commit**

```bash
git add src/parser_v2/contracts/canonical_message.py
git commit -m "chore(parser_v2): remove UpdatePayload and TargetedAction — replaced by TargetActionGroup"
```

---

## Self-Review

**Spec coverage check:**
- ✅ `ActionItem` typed class — Task 1
- ✅ `TargetActionGroup` with `targeting` + `secondary_targeting` + `actions` — Task 1
- ✅ Remove `update`/`targeted_actions` from `CanonicalMessage` — Task 2
- ✅ Reply priority (secondary_targeting) — Task 3 `_resolve_target_hints`
- ✅ All 5 real cases — covered by translator logic in Task 3
- ✅ `canonical_translator.py` updated — Task 3
- ✅ 7 test files updated — Task 4
- ✅ `flatteners_v2.py` updated — Task 5
- ✅ `report_schema_v2.py` updated — Task 5
- ✅ `test_flatteners_v2.py` updated — Task 5

**No placeholders:** all steps have exact code.

**Type consistency:**
- `ActionItem` defined in Task 1, used as return type of `_operation_from_intent` in Task 3 ✅
- `TargetActionGroup` defined in Task 1, used in `CanonicalMessage.target_action_groups` in Task 2 ✅
- `_build_target_action_groups` returns `list[TargetActionGroup]`, assigned to `target_action_groups` ✅
- `group.actions[0].action_type` used in tests matches `ActionItem.action_type` ✅

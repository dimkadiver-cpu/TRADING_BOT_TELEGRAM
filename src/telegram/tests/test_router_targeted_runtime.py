"""Router Fase 4: selezione del percorso targeted vs legacy."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.migrations import apply_migrations
from src.parser.canonical_v1.models import CanonicalMessage, RawContext, TargetedAction, TargetedActionTargeting, UpdatePayload
from src.parser.trader_profiles.base import TraderParseResult
from src.storage.parse_results_v1 import ParseResultV1Store
from src.storage.review_queue import ReviewQueueStore
from src.target_resolver.models import MultiRefResolvedResult, ResolvedActionItem
from src.telegram.channel_config import ChannelEntry, ChannelsConfig
from src.telegram.router import MessageRouter, QueueItem


def _migrations_dir() -> str:
    return str(Path("db/migrations").resolve())


def _config() -> ChannelsConfig:
    return ChannelsConfig(
        recovery_max_hours=4,
        blacklist_global=[],
        channels=[ChannelEntry(chat_id=-100123, label="t", active=True, trader_id="trader_a")],
    )


def _item(raw_message_id: int = 42) -> QueueItem:
    return QueueItem(
        raw_message_id=raw_message_id,
        source_chat_id="-100123",
        telegram_message_id=9001,
        raw_text="close refs",
        source_trader_id="trader_a",
        reply_to_message_id=None,
        acquisition_mode="live",
    )


def _update_result() -> TraderParseResult:
    return TraderParseResult(
        message_type="UPDATE",
        intents=["U_CLOSE_FULL"],
        entities={"close_scope": "FULL"},
        target_refs=[{"kind": "reply", "ref": 9000}],
        confidence=0.75,
        primary_intent="U_CLOSE_FULL",
    )


def _targeted_canonical() -> CanonicalMessage:
    return CanonicalMessage(
        parser_profile="trader_a",
        primary_class="UPDATE",
        parse_status="PARTIAL",
        confidence=0.8,
        raw_context=RawContext(raw_text="close refs"),
        update=UpdatePayload(),
        targeted_actions=[
            TargetedAction(
                action_type="CLOSE",
                params={"close_scope": "FULL"},
                targeting=TargetedActionTargeting(mode="TARGET_GROUP", targets=[1001, 1002]),
            )
        ],
    )


def _build_router(tmp_path: Path) -> MessageRouter:
    db_path = str(tmp_path / "router.sqlite3")
    apply_migrations(db_path=db_path, migrations_dir=_migrations_dir())

    return MessageRouter(
        effective_trader_resolver=MagicMock(**{
            "resolve.return_value": MagicMock(trader_id="trader_a", method="config", detail=None)
        }),
        eligibility_evaluator=MagicMock(**{
            "evaluate.return_value": MagicMock(
                status="ACQUIRED_ELIGIBLE", reason="eligible", strong_link_method=None
            )
        }),
        parse_results_store=MagicMock(),
        processing_status_store=MagicMock(),
        review_queue_store=ReviewQueueStore(db_path=db_path),
        raw_message_store=MagicMock(**{"get_by_source_and_message_id.return_value": None}),
        logger=MagicMock(),
        channels_config=_config(),
        db_path=db_path,
        operation_rules_engine=MagicMock(**{"apply.return_value": MagicMock(is_blocked=False, block_reason=None, parse_result=MagicMock(message_type="UPDATE"), applied_rules=[], warnings=[], risk_mode=None, risk_pct_of_capital=None, risk_usdt_fixed=None, capital_base_usdt=None, risk_budget_usdt=None, sl_distance_pct=None, position_size_pct=None, position_size_usdt=None, entry_split=None, leverage=None, risk_hint_used=None, management_rules=None)}),
        target_resolver=MagicMock(),
        operational_signals_store=MagicMock(**{"get_parse_result_id.return_value": 1, "insert.return_value": 99}),
        parse_results_v1_store=ParseResultV1Store(db_path=db_path),
    )


class _NativeParserStub:
    def __init__(self, *, parse_result: TraderParseResult, canonical: CanonicalMessage) -> None:
        self._parse_result = parse_result
        self._canonical = canonical

    def parse_message(self, text: str, context: object) -> TraderParseResult:
        return self._parse_result

    def parse_canonical(self, text: str, context: object) -> CanonicalMessage:
        return self._canonical


class TestRouterTargetedRuntime:
    def test_targeted_runtime_skips_legacy_update_apply(self, tmp_path: Path) -> None:
        router = _build_router(tmp_path)
        parser = _NativeParserStub(parse_result=_update_result(), canonical=_targeted_canonical())

        resolved = MultiRefResolvedResult(
            resolved_actions=[
                ResolvedActionItem(
                    action_index=0,
                    action_type="CLOSE",
                    resolved_position_ids=[11, 22],
                    resolved_attempt_keys=["T_trader_a_1001", "T_trader_a_1002"],
                    eligibility="ELIGIBLE",
                )
            ]
        )

        with patch("src.telegram.router._USE_TARGETED_RUNTIME", True), \
             patch("src.telegram.router.get_profile_parser", return_value=parser), \
             patch("src.telegram.router._validate_result", return_value=MagicMock(status="VALID", errors=[], to_dict=lambda: {"validation_status": "VALID"})), \
             patch("src.telegram.router.resolve_targeted", return_value=resolved) as resolve_mock, \
             patch("src.telegram.router.build_plan") as build_plan_mock, \
             patch("src.telegram.router.apply_plan") as apply_plan_mock, \
             patch.object(router, "_apply_update_runtime") as legacy_apply_mock:
            router.route(_item())

        resolve_mock.assert_called_once()
        build_plan_mock.assert_called_once()
        apply_plan_mock.assert_called_once()
        legacy_apply_mock.assert_not_called()
        record = router._parse_results_v1.get_by_raw_message_id(42)  # type: ignore[union-attr]
        assert record is not None
        assert record.targeted_resolved_json is not None

    def test_without_targeted_actions_legacy_runtime_remains_active(self, tmp_path: Path) -> None:
        router = _build_router(tmp_path)
        parser = _NativeParserStub(
            parse_result=_update_result(),
            canonical=CanonicalMessage(
                parser_profile="trader_a",
                primary_class="UPDATE",
                parse_status="PARTIAL",
                confidence=0.8,
                raw_context=RawContext(raw_text="legacy"),
                update=UpdatePayload(),
            ),
        )

        with patch("src.telegram.router._USE_TARGETED_RUNTIME", True), \
             patch("src.telegram.router.get_profile_parser", return_value=parser), \
             patch("src.telegram.router._validate_result", return_value=MagicMock(status="VALID", errors=[], to_dict=lambda: {"validation_status": "VALID"})), \
             patch("src.telegram.router.resolve_targeted") as resolve_mock, \
             patch.object(router._resolver, "resolve", return_value=MagicMock(eligibility="ELIGIBLE", position_ids=[11], reason=None)) as legacy_resolve_mock:
            router.route(_item())

        resolve_mock.assert_not_called()
        legacy_resolve_mock.assert_called_once()

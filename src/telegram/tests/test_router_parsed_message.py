"""Integration tests for router dual-stack ParsedMessage persistence (Fasa 4.5)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.migrations import apply_migrations
from src.parser.canonical_v1.models import (
    CanonicalMessage,
    CloseOperation,
    RawContext,
    UpdateOperation,
    UpdatePayload,
)
from src.parser.intent_types import IntentType
from src.parser.parsed_message import CloseFullEntities, IntentResult, ParsedMessage
from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.storage.parse_results_v1 import ParseResultV1Store
from src.storage.parsed_messages import ParsedMessageStore
from src.storage.review_queue import ReviewQueueStore
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
        raw_text="chiudo tutto",
        source_trader_id="trader_a",
        reply_to_message_id=7001,
        acquisition_mode="live",
    )


class _ParsedMessageCapableProfile:
    def __init__(self) -> None:
        self.parse_calls = 0
        self.parse_message_calls = 0
        self.parse_canonical_calls = 0

    def parse(self, text: str, context: ParserContext) -> ParsedMessage:
        self.parse_calls += 1
        return ParsedMessage(
            parser_profile="trader_a",
            primary_class="UPDATE",
            parse_status="PARSED",
            confidence=0.82,
            intents=[
                IntentResult(
                    type=IntentType.CLOSE_FULL,
                    category="UPDATE",
                    entities=CloseFullEntities(),
                    confidence=0.82,
                    status="CONFIRMED",
                    valid_refs=[context.reply_to_message_id or 0],
                )
            ],
            primary_intent=IntentType.CLOSE_FULL,
            validation_status="VALIDATED",
            raw_context=RawContext(
                raw_text=text,
                reply_to_message_id=context.reply_to_message_id,
                extracted_links=context.extracted_links,
                hashtags=context.hashtags,
                source_chat_id=context.channel_id,
                acquisition_mode="live",
            ),
        )

    def parse_message(self, text: str, context: ParserContext) -> TraderParseResult:
        self.parse_message_calls += 1
        return TraderParseResult(
            message_type="UPDATE",
            intents=["U_CLOSE_FULL"],
            entities={"close_scope": "FULL"},
            target_refs=[{"kind": "reply", "ref": context.reply_to_message_id}],
            confidence=0.75,
            primary_intent="U_CLOSE_FULL",
        )

    def parse_canonical(self, text: str, context: ParserContext) -> CanonicalMessage:
        self.parse_canonical_calls += 1
        return CanonicalMessage(
            parser_profile="trader_a",
            primary_class="UPDATE",
            parse_status="PARSED",
            confidence=0.82,
            intents=["CLOSE_FULL"],
            primary_intent="CLOSE_FULL",
            update=UpdatePayload(
                operations=[
                    UpdateOperation(
                        op_type="CLOSE",
                        close=CloseOperation(close_scope="FULL"),
                    )
                ]
            ),
            raw_context=RawContext(
                raw_text=text,
                reply_to_message_id=context.reply_to_message_id,
                extracted_links=context.extracted_links,
                hashtags=context.hashtags,
                source_chat_id=context.channel_id,
                acquisition_mode="live",
            ),
        )


def _build_router(
    tmp_path: Path,
    *,
    parsed_store: ParsedMessageStore,
    v1_store: ParseResultV1Store,
) -> MessageRouter:
    db_path = str(tmp_path / "router.sqlite3")
    return MessageRouter(
        effective_trader_resolver=MagicMock(**{
            "resolve.return_value": MagicMock(trader_id="trader_a", method="config", detail=None)
        }),
        eligibility_evaluator=MagicMock(**{
            "evaluate.return_value": MagicMock(
                status="ACQUIRED_ELIGIBLE",
                reason="eligible",
                strong_link_method=None,
            )
        }),
        parse_results_store=MagicMock(),
        processing_status_store=MagicMock(),
        review_queue_store=ReviewQueueStore(db_path=db_path),
        raw_message_store=MagicMock(**{"get_by_source_and_message_id.return_value": None}),
        logger=MagicMock(),
        channels_config=_config(),
        parse_results_v1_store=v1_store,
        parsed_messages_store=parsed_store,
    )


def test_router_persists_parsed_message_when_feature_flag_enabled(tmp_path: Path) -> None:
    db_path = str(tmp_path / "router.sqlite3")
    apply_migrations(db_path=db_path, migrations_dir=_migrations_dir())

    parsed_store = ParsedMessageStore(db_path=db_path)
    v1_store = ParseResultV1Store(db_path=db_path)
    router = _build_router(tmp_path, parsed_store=parsed_store, v1_store=v1_store)
    profile = _ParsedMessageCapableProfile()

    with patch("src.telegram.router._USE_PARSED_MESSAGE", True), patch(
        "src.telegram.router.get_profile_parser",
        return_value=profile,
    ):
        router.route(_item(raw_message_id=101))

    parsed_record = parsed_store.get_by_raw_message_id(101)
    assert parsed_record is not None
    assert parsed_record.primary_class == "UPDATE"
    assert parsed_record.validation_status == "VALIDATED"
    assert json.loads(parsed_record.intents_confirmed_json) == ["CLOSE_FULL"]

    canonical_record = v1_store.get_by_raw_message_id(101)
    assert canonical_record is not None
    assert json.loads(canonical_record.canonical_json)["primary_class"] == "UPDATE"

    assert profile.parse_calls == 1
    assert profile.parse_message_calls == 1
    assert profile.parse_canonical_calls == 0


def test_router_does_not_run_parsed_message_path_when_flag_disabled(tmp_path: Path) -> None:
    db_path = str(tmp_path / "router.sqlite3")
    apply_migrations(db_path=db_path, migrations_dir=_migrations_dir())

    parsed_store = ParsedMessageStore(db_path=db_path)
    v1_store = ParseResultV1Store(db_path=db_path)
    router = _build_router(tmp_path, parsed_store=parsed_store, v1_store=v1_store)
    profile = _ParsedMessageCapableProfile()

    with patch("src.telegram.router._USE_PARSED_MESSAGE", False), patch(
        "src.telegram.router.get_profile_parser",
        return_value=profile,
    ):
        router.route(_item(raw_message_id=102))

    assert parsed_store.get_by_raw_message_id(102) is None
    assert profile.parse_calls == 0
    assert profile.parse_message_calls == 1

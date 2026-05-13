from __future__ import annotations
import pytest
from datetime import datetime, timezone
from src.runtime_v2.trader_resolution.models import (
    ResolvedTraderContext,
    ParserDispatchCandidate,
)
from src.runtime_v2.intake.models import RawMessageEnvelope
from src.parser_v2.contracts.context import ParserContext

_TS = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)


def _make_envelope(raw_message_id: int = 1) -> RawMessageEnvelope:
    return RawMessageEnvelope(
        raw_message_id=raw_message_id,
        source_chat_id="-100123",
        source_chat_title=None,
        source_type=None,
        source_topic_id=3,
        telegram_message_id=456,
        reply_to_message_id=None,
        raw_text="BUY BTC",
        message_ts=_TS,
        acquired_at=_TS,
        acquisition_mode="live",
        acquisition_status="ACQUIRED",
        processing_status="pending",
        source_trader_id=None,
        resolved_trader_id=None,
        resolution_method=None,
        resolution_detail=None,
        has_media=False,
        media_kind=None,
        media_mime_type=None,
        media_filename=None,
    )


def test_resolved_trader_context_resolved():
    ctx = ResolvedTraderContext(
        raw_message_id=1,
        trader_id="trader_a",
        method="source_chat_id",
        detail=None,
        is_ambiguous=False,
        resolved_at=_TS,
    )
    assert ctx.trader_id == "trader_a"
    assert not ctx.is_ambiguous


def test_resolved_trader_context_unresolved():
    ctx = ResolvedTraderContext(
        raw_message_id=1,
        trader_id=None,
        method="unresolved",
        detail="no alias found",
        is_ambiguous=False,
        resolved_at=_TS,
    )
    assert ctx.trader_id is None
    assert ctx.method == "unresolved"


def test_resolved_trader_context_rejects_invalid_method():
    with pytest.raises(Exception):
        ResolvedTraderContext(
            raw_message_id=1,
            trader_id="trader_a",
            method="invalid_method",
            detail=None,
            is_ambiguous=False,
            resolved_at=_TS,
        )


def test_parser_dispatch_candidate():
    env = _make_envelope()
    resolved = ResolvedTraderContext(
        raw_message_id=1,
        trader_id="trader_a",
        method="source_chat_id",
        detail=None,
        is_ambiguous=False,
        resolved_at=_TS,
    )
    ctx = ParserContext(message_id=456, source_chat_id="-100123", source_topic_id=3)
    candidate = ParserDispatchCandidate(
        raw_message=env,
        resolved_trader=resolved,
        parser_profile="trader_a",
        parser_context=ctx,
    )
    assert candidate.parser_profile == "trader_a"
    assert candidate.raw_message.raw_message_id == 1
    assert candidate.resolved_trader.trader_id == "trader_a"

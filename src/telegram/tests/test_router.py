"""Tests for MessageRouter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.migrations import apply_migrations
from src.parser.trader_profiles.base import TraderParseResult
from src.storage.review_queue import ReviewQueueStore
from src.telegram.channel_config import ChannelEntry, ChannelsConfig
from src.telegram.router import MessageRouter, QueueItem


def _config(
    *,
    blacklist_global: list[str] | None = None,
    channels: list[ChannelEntry] | None = None,
) -> ChannelsConfig:
    return ChannelsConfig(
        recovery_max_hours=4,
        blacklist_global=blacklist_global or [],
        channels=channels or [],
    )


def _item(**overrides: object) -> QueueItem:
    data = {
        "raw_message_id": 10,
        "source_chat_id": "-100123",
        "telegram_message_id": 999,
        "raw_text": "BTC long https://t.me/test #swing",
        "source_trader_id": None,
        "reply_to_message_id": None,
        "acquisition_mode": "live",
    }
    data.update(overrides)
    return QueueItem(**data)


def _router(tmp_path: Path, config: ChannelsConfig) -> tuple[MessageRouter, dict[str, object]]:
    db_path = str(tmp_path / "router.sqlite3")
    apply_migrations(db_path=db_path, migrations_dir=str(Path("db/migrations").resolve()))
    deps: dict[str, object] = {
        "effective_trader_resolver": MagicMock(),
        "eligibility_evaluator": MagicMock(),
        "parse_results_store": MagicMock(),
        "processing_status_store": MagicMock(),
        "review_queue_store": ReviewQueueStore(db_path=db_path),
        "raw_message_store": MagicMock(),
        "logger": MagicMock(),
    }
    deps["eligibility_evaluator"].evaluate.return_value = MagicMock(  # type: ignore[attr-defined]
        status="ACQUIRED_ELIGIBLE",
        reason="eligible",
        strong_link_method=None,
    )
    router = MessageRouter(
        effective_trader_resolver=deps["effective_trader_resolver"],  # type: ignore[arg-type]
        eligibility_evaluator=deps["eligibility_evaluator"],  # type: ignore[arg-type]
        parse_results_store=deps["parse_results_store"],  # type: ignore[arg-type]
        processing_status_store=deps["processing_status_store"],  # type: ignore[arg-type]
        review_queue_store=deps["review_queue_store"],  # type: ignore[arg-type]
        raw_message_store=deps["raw_message_store"],  # type: ignore[arg-type]
        logger=deps["logger"],  # type: ignore[arg-type]
        channels_config=config,
    )
    return router, deps


def test_blacklist_global(tmp_path: Path) -> None:
    router, deps = _router(tmp_path, _config(blacklist_global=["#admin"]))
    router.route(_item(raw_text="hello #admin"))
    deps["processing_status_store"].update.assert_any_call(10, "processing")  # type: ignore[attr-defined]
    deps["processing_status_store"].update.assert_any_call(10, "blacklisted")  # type: ignore[attr-defined]
    deps["parse_results_store"].upsert.assert_not_called()  # type: ignore[attr-defined]


def test_blacklist_channel(tmp_path: Path) -> None:
    cfg = _config(channels=[ChannelEntry(chat_id=-100123, label="x", active=True, trader_id=None, blacklist=["#weekly"])])
    router, deps = _router(tmp_path, cfg)
    router.route(_item(raw_text="results #weekly"))
    deps["processing_status_store"].update.assert_any_call(10, "blacklisted")  # type: ignore[attr-defined]


def test_trader_unresolved(tmp_path: Path) -> None:
    router, deps = _router(tmp_path, _config())
    deps["effective_trader_resolver"].resolve.return_value = MagicMock(trader_id=None, method="unresolved")  # type: ignore[attr-defined]
    router.route(_item())
    deps["processing_status_store"].update.assert_any_call(10, "review")  # type: ignore[attr-defined]
    pending = deps["review_queue_store"].get_pending()  # type: ignore[attr-defined]
    assert len(pending) == 1
    assert pending[0].raw_message_id == 10
    assert pending[0].reason == "unresolved_trader"


def test_trader_inactive(tmp_path: Path) -> None:
    cfg = _config(channels=[ChannelEntry(chat_id=-100123, label="x", active=False, trader_id="trader_a", blacklist=[])])
    router, deps = _router(tmp_path, cfg)
    deps["effective_trader_resolver"].resolve.return_value = MagicMock(trader_id="trader_a", method="source_chat_id")  # type: ignore[attr-defined]
    router.route(_item())
    deps["processing_status_store"].update.assert_any_call(10, "done")  # type: ignore[attr-defined]
    deps["parse_results_store"].upsert.assert_not_called()  # type: ignore[attr-defined]


def test_reply_raw_text(tmp_path: Path) -> None:
    router, deps = _router(tmp_path, _config())
    deps["effective_trader_resolver"].resolve.return_value = MagicMock(trader_id="trader_a", method="content_alias")  # type: ignore[attr-defined]
    deps["raw_message_store"].get_by_source_and_message_id.return_value = MagicMock(raw_text="parent text")  # type: ignore[attr-defined]
    parser = MagicMock()
    parser.parse_message.return_value = TraderParseResult(message_type="NEW_SIGNAL")
    with patch("src.telegram.router.get_profile_parser", return_value=parser):
        router.route(_item(reply_to_message_id=55))
    context = parser.parse_message.call_args.kwargs["context"]
    assert context.reply_raw_text == "parent text"


def test_parser_exception(tmp_path: Path) -> None:
    router, deps = _router(tmp_path, _config())
    deps["effective_trader_resolver"].resolve.return_value = MagicMock(trader_id="trader_a", method="content_alias")  # type: ignore[attr-defined]
    parser = MagicMock()
    parser.parse_message.side_effect = RuntimeError("boom")
    with patch("src.telegram.router.get_profile_parser", return_value=parser):
        router.route(_item())
    deps["processing_status_store"].update.assert_any_call(10, "failed")  # type: ignore[attr-defined]


def test_channels_yaml_fallback(tmp_path: Path) -> None:
    cfg = _config(channels=[ChannelEntry(chat_id=-100123, label="alpha", active=True, trader_id="trader_yaml", blacklist=[])])
    router, deps = _router(tmp_path, cfg)
    deps["effective_trader_resolver"].resolve.return_value = MagicMock(trader_id=None, method="unresolved")  # type: ignore[attr-defined]
    parser = MagicMock()
    parser.parse_message.return_value = TraderParseResult(message_type="NEW_SIGNAL")
    with patch("src.telegram.router.get_profile_parser", return_value=parser):
        router.route(_item())
    context = parser.parse_message.call_args.kwargs["context"]
    assert context.trader_code == "trader_yaml"


def test_hashtag_extraction(tmp_path: Path) -> None:
    router, deps = _router(tmp_path, _config())
    deps["effective_trader_resolver"].resolve.return_value = MagicMock(trader_id="trader_a", method="content_alias")  # type: ignore[attr-defined]
    parser = MagicMock()
    parser.parse_message.return_value = TraderParseResult(message_type="NEW_SIGNAL")
    with patch("src.telegram.router.get_profile_parser", return_value=parser):
        router.route(_item(raw_text="signal #Swing https://t.me/joinchat/abc #Swing #tp1"))
    context = parser.parse_message.call_args.kwargs["context"]
    assert context.hashtags == ["#Swing", "#tp1"]
    assert context.extracted_links == ["https://t.me/joinchat/abc"]

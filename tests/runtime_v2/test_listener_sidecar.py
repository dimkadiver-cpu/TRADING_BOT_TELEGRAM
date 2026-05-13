"""Tests for RuntimeV2ListenerSidecar."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.runtime_v2.intake.models import RawMessageEnvelope
from src.runtime_v2.listener_sidecar import RuntimeV2ListenerSidecar
from src.runtime_v2.parser_pipeline.models import CanonicalParseResult, ParserJobStatus
from src.runtime_v2.trader_resolution.channel_config_resolver import ChannelEntry


@dataclass
class _FakeQueueItem:
    raw_message_id: int = 42
    source_chat_id: str = "-100111"
    source_topic_id: int | None = None


def _make_sidecar(db_path: str, channels_config_path: str) -> RuntimeV2ListenerSidecar:
    return RuntimeV2ListenerSidecar(
        db_path=db_path,
        channels_config_path=channels_config_path,
        logger=logging.getLogger("test"),
    )


def _active_entry(trader_id: str = "trader_a", parser_profile: str = "trader_a") -> ChannelEntry:
    return ChannelEntry(
        chat_id="-100111",
        topic_id=None,
        label=None,
        active=True,
        trader_id=trader_id,
        parser_profile=parser_profile,
        blacklist=[],
    )


def _inactive_entry() -> ChannelEntry:
    return ChannelEntry(
        chat_id="-100111",
        topic_id=None,
        label=None,
        active=False,
        trader_id="trader_a",
        parser_profile="trader_a",
        blacklist=[],
    )


class TestSidecarSkips:
    def test_no_channel_entry_skips_processing(self, tmp_path):
        yaml = tmp_path / "channels.yaml"
        yaml.write_text("channels: []\n", encoding="utf-8")
        db = str(tmp_path / "live.db")

        sidecar = _make_sidecar(db_path=db, channels_config_path=str(yaml))
        processor_mock = MagicMock()
        sidecar._processor = processor_mock

        sidecar.process_queue_item(_FakeQueueItem())
        processor_mock.process.assert_not_called()

    def test_inactive_entry_skips_processing(self, tmp_path):
        yaml = tmp_path / "channels.yaml"
        yaml.write_text("channels: []\n", encoding="utf-8")
        db = str(tmp_path / "live.db")

        sidecar = _make_sidecar(db_path=db, channels_config_path=str(yaml))
        sidecar._channel_resolver.lookup = MagicMock(return_value=_inactive_entry())
        processor_mock = MagicMock()
        sidecar._processor = processor_mock

        sidecar.process_queue_item(_FakeQueueItem())
        processor_mock.process.assert_not_called()


class TestSidecarProcesses:
    def _fake_envelope(self) -> RawMessageEnvelope:
        return RawMessageEnvelope(
            raw_message_id=42,
            source_chat_id="-100111",
            source_chat_title=None,
            source_type=None,
            source_topic_id=None,
            telegram_message_id=999,
            reply_to_message_id=None,
            raw_text="BTC long 70000",
            message_ts=datetime.now(timezone.utc),
            acquired_at=datetime.now(timezone.utc),
            acquisition_mode="live",
            acquisition_status="ACQUIRED",
            processing_status="done",
            source_trader_id=None,
            resolved_trader_id=None,
            resolution_method=None,
            resolution_detail=None,
            has_media=False,
            media_kind=None,
            media_mime_type=None,
            media_filename=None,
        )

    def _setup_sidecar_with_mocks(self, tmp_path, parse_result):
        yaml = tmp_path / "channels.yaml"
        yaml.write_text("channels: []\n", encoding="utf-8")
        db = str(tmp_path / "live.db")
        sidecar = _make_sidecar(db_path=db, channels_config_path=str(yaml))

        sidecar._channel_resolver.lookup = MagicMock(return_value=_active_entry())
        sidecar._raw_repo.get_by_id = MagicMock(return_value=self._fake_envelope())

        processor_mock = MagicMock()
        processor_mock.process.return_value = parse_result
        sidecar._processor = processor_mock

        return sidecar, processor_mock

    def test_successful_parse_calls_processor(self, tmp_path):
        canonical_result = MagicMock(spec=CanonicalParseResult)
        canonical_result.parser_profile = "trader_a"
        canonical_result.primary_class = "SIGNAL"
        canonical_result.parse_status = "PARSED"
        canonical_result.canonical_message_id = 1

        sidecar, processor_mock = self._setup_sidecar_with_mocks(tmp_path, canonical_result)
        sidecar.process_queue_item(_FakeQueueItem())

        processor_mock.process.assert_called_once()
        candidate = processor_mock.process.call_args[0][0]
        assert candidate.parser_profile == "trader_a"
        assert candidate.raw_message.raw_message_id == 42

    def test_failed_parse_logs_warning(self, tmp_path):
        job_status = ParserJobStatus(
            raw_message_id=42,
            status="failed",
            reason="unknown_parser_profile",
            canonical_message_id=None,
        )
        sidecar, _ = self._setup_sidecar_with_mocks(tmp_path, job_status)

        log_mock = MagicMock()
        sidecar._logger = log_mock

        sidecar.process_queue_item(_FakeQueueItem())
        log_mock.warning.assert_called()

    def test_exception_in_process_is_swallowed(self, tmp_path):
        yaml = tmp_path / "channels.yaml"
        yaml.write_text("channels: []\n", encoding="utf-8")
        db = str(tmp_path / "live.db")
        sidecar = _make_sidecar(db_path=db, channels_config_path=str(yaml))

        sidecar._channel_resolver.lookup = MagicMock(return_value=_active_entry())
        sidecar._raw_repo.get_by_id = MagicMock(side_effect=RuntimeError("db error"))

        # Must not raise
        sidecar.process_queue_item(_FakeQueueItem())


class TestSidecarReloadConfig:
    def test_reload_config_delegates_to_resolver(self, tmp_path):
        yaml = tmp_path / "channels.yaml"
        yaml.write_text("channels: []\n", encoding="utf-8")
        db = str(tmp_path / "live.db")
        sidecar = _make_sidecar(db_path=db, channels_config_path=str(yaml))

        reload_mock = MagicMock()
        sidecar._channel_resolver.reload = reload_mock

        sidecar.reload_config()
        reload_mock.assert_called_once()

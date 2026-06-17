from __future__ import annotations

from pathlib import Path

from src.startup_check.validator import (
    ValidationReport,
    _check_channels,
    _check_text_patterns,
    _load_text_pattern_groups,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_load_text_pattern_groups_reads_trader_ids(tmp_path: Path) -> None:
    _write(
        tmp_path / "config" / "text_patterns.yaml",
        """
groups:
  multi_strategy_ru:
    patterns:
      - trader_id: rsi_intraday
        all_of: ["RSI(2) Коннора", "интрадей"]
      - trader_id: sma_intraday
        all_of: ["Кросс SMA 21/55", "интрадей"]
""",
    )
    groups = _load_text_pattern_groups(tmp_path)
    assert groups == {"multi_strategy_ru": {"rsi_intraday", "sma_intraday"}}


def test_check_text_patterns_reports_missing_group_for_patterns_only(tmp_path: Path) -> None:
    _write(
        tmp_path / "config" / "channels.yaml",
        """
channels:
  - chat_id: -1001
    topic_id: 4180
    label: "Multi"
    active: true
    trader_id: null
    resolution:
      mode: patterns_only
    blacklist: []
""",
    )
    _write(
        tmp_path / "config" / "text_patterns.yaml",
        """
groups:
  multi_strategy_ru:
    patterns: []
""",
    )
    _write(
        tmp_path / "config" / "operation_config.yaml",
        """
registered_traders:
  - rsi_intraday
""",
    )
    report = ValidationReport()
    _check_text_patterns(report, tmp_path)
    assert any("mode=patterns_only richiede pattern_group" in result.message for result in report.errors)


def test_check_text_patterns_accepts_existing_group(tmp_path: Path) -> None:
    _write(
        tmp_path / "config" / "channels.yaml",
        """
channels:
  - chat_id: -1001
    topic_id: 4180
    label: "Multi"
    active: true
    trader_id: null
    resolution:
      mode: default
      pattern_group: multi_strategy_ru
    blacklist: []
""",
    )
    _write(
        tmp_path / "config" / "text_patterns.yaml",
        """
groups:
  multi_strategy_ru:
    patterns:
      - trader_id: rsi_intraday
        all_of: ["RSI(2) Коннора", "интрадей"]
""",
    )
    _write(
        tmp_path / "config" / "operation_config.yaml",
        """
registered_traders:
  - rsi_intraday
""",
    )
    report = ValidationReport()
    _check_text_patterns(report, tmp_path)
    assert report.errors == []


def test_check_channels_reports_blank_chat_id_with_human_message(tmp_path: Path) -> None:
    _write(
        tmp_path / "config" / "channels.yaml",
        """
channels:
  - chat_id:
    topic_id: null
    label: "Multi"
    active: true
    trader_id: trader_a
    blacklist: []
""",
    )
    report = ValidationReport()
    _check_channels(report, tmp_path)
    assert any("invalid channel entry 'Multi': chat_id is missing" in result.message for result in report.errors)

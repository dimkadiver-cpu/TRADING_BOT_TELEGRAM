from __future__ import annotations

from src.runtime_v2.control_plane.formatters.tech_log import format_tech_log


def test_format_tech_log_supergroup_has_no_system_prefix():
    text = format_tech_log(
        "UNKNOWN_EXCHANGE_ERROR",
        {
            "level": "ERROR",
            "description": "API error retCode 10001",
            "source": "bybit_sync",
        },
        delivery_mode="supergroup_topics",
    )
    assert "⚠️ --SYSTEM--" not in text
    assert "API error retCode 10001" in text


def test_format_tech_log_private_bot_adds_system_prefix():
    text = format_tech_log(
        "UNKNOWN_RUNTIME_EVENT",
        {
            "level": "WARN",
            "description": "something",
        },
        delivery_mode="private_bot",
    )
    assert text.startswith("⚠️ --SYSTEM--\n")
    assert "something" in text

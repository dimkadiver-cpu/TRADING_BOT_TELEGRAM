from __future__ import annotations

from src.runtime_v2.control_plane.formatters.tech_log import format_tech_log


def test_format_tech_log_supergroup_has_no_system_prefix():
    text = format_tech_log(
        {
            "level": "ERROR",
            "category": "Exchange",
            "description": "API error retCode 10001",
            "source": "bybit_sync",
        },
        delivery_mode="supergroup_topics",
    )
    assert text.startswith("[ERROR] Exchange")
    assert "⚠️ --SYSTEM--" not in text
    assert "API error retCode 10001" in text
    assert "Source: bybit_sync" in text


def test_format_tech_log_private_bot_adds_system_prefix():
    text = format_tech_log(
        {
            "level": "WARN",
            "description": "something",
        },
        delivery_mode="private_bot",
    )
    assert text.startswith("⚠️ --SYSTEM--\n")
    assert "[WARN] Runtime" in text
    assert "something" in text

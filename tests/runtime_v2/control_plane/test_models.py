from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.runtime_v2.control_plane.models import (
    AccountConfig,
    AccountTopicsConfig,
    CleanLogConfig,
    ControlPlaneConfig,
    NotificationOutboxEntry,
    TechLogConfig,
    TopicConfig,
)


def _minimal_config() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        token="123:ABC",
        default_account="main",
        per_account={
            "main": AccountConfig(
                chat_id=-1001234567890,
                topics=AccountTopicsConfig(
                    commands=TopicConfig(thread_id=101),
                    tech_log=TechLogConfig(thread_id=102),
                    clean_log=CleanLogConfig(thread_id=103),
                ),
            )
        },
        authorized_users=[123456789],
    )


def test_config_defaults():
    cfg = _minimal_config()
    assert cfg.enabled is True
    assert cfg.startup.mode == "auto"
    assert cfg.startup.restore_max_age_seconds == 300
    acc = cfg.get_account(None)
    assert acc.topics.tech_log.min_level == "WARNING"
    assert acc.topics.tech_log.operational_events is False
    assert acc.topics.clean_log.min_partial_fill_notify_pct == 10.0


def test_config_rejects_bad_startup_mode():
    with pytest.raises(ValidationError):
        ControlPlaneConfig(
            token="t",
            default_account="main",
            per_account={
                "main": AccountConfig(
                    chat_id=1,
                    topics=AccountTopicsConfig(
                        commands=TopicConfig(thread_id=1),
                        tech_log=TechLogConfig(thread_id=2),
                        clean_log=CleanLogConfig(thread_id=3),
                    ),
                )
            },
            startup={"mode": "nonsense"},
        )


def test_get_account_fallback():
    cfg = _minimal_config()
    # Known account returns correct config
    acc = cfg.get_account("main")
    assert acc.chat_id == -1001234567890
    # Unknown account falls back to default_account
    acc_fallback = cfg.get_account("unknown")
    assert acc_fallback.chat_id == -1001234567890
    # None falls back to default_account
    acc_none = cfg.get_account(None)
    assert acc_none.chat_id == -1001234567890


def test_outbox_entry_roundtrip():
    entry = NotificationOutboxEntry(
        notification_type="SIGNAL_ACCEPTED",
        destination="CLEAN_LOG",
        payload_json='{"chain_id": 145}',
        priority="MEDIUM",
        dedupe_key="clean:sig_accepted:145",
    )
    assert entry.status == "PENDING"
    assert entry.attempts == 0
    again = NotificationOutboxEntry.model_validate(entry.model_dump())
    assert again.dedupe_key == "clean:sig_accepted:145"

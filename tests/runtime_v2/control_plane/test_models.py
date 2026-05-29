from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.runtime_v2.control_plane.models import (
    CleanLogConfig,
    ControlPlaneConfig,
    NotificationOutboxEntry,
    TechLogConfig,
    TopicConfig,
    TopicsConfig,
)


def _minimal_config() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        token="123:ABC",
        chat_id=-1001234567890,
        topics=TopicsConfig(
            commands=TopicConfig(thread_id=101),
            tech_log=TechLogConfig(thread_id=102),
            clean_log=CleanLogConfig(thread_id=103),
        ),
        authorized_users=[123456789],
    )


def test_config_defaults():
    cfg = _minimal_config()
    assert cfg.enabled is True
    assert cfg.startup.mode == "auto"
    assert cfg.startup.restore_max_age_seconds == 300
    assert cfg.topics.tech_log.min_level == "WARNING"
    assert cfg.topics.tech_log.operational_events is False
    assert cfg.topics.clean_log.min_partial_fill_notify_pct == 10.0


def test_config_rejects_bad_startup_mode():
    with pytest.raises(ValidationError):
        ControlPlaneConfig(
            token="t",
            chat_id=1,
            topics=TopicsConfig(
                commands=TopicConfig(thread_id=1),
                tech_log=TechLogConfig(thread_id=2),
                clean_log=CleanLogConfig(thread_id=3),
            ),
            startup={"mode": "nonsense"},
        )


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

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Destination = Literal["TECH_LOG", "CLEAN_LOG", "COMMANDS_REPLY"]
Priority = Literal["HIGH", "MEDIUM", "LOW"]
OutboxStatus = Literal["PENDING", "SENT", "FAILED"]
StartupMode = Literal["auto", "standby", "restore"]
CommandStatus = Literal[
    "RECEIVED",
    "REJECTED",
    "ACCEPTED",
    "EXECUTED",
    "FAILED",
    "IGNORED",
]


class TopicConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    thread_id: int | None = None


class TechLogConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    thread_id: int | None = None
    enabled: bool = True
    min_level: Literal["WARNING", "INFO", "DEBUG"] = "WARNING"
    operational_events: bool = False
    batch_seconds: int = 10
    max_messages_per_minute: int = 20
    dedupe_window_seconds: int = 60
    max_repeated_before_summary: int = 5
    debug_max_duration_minutes: int = 60


class CleanLogConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    thread_id: int | None = None
    debounce_seconds: int = 20
    aggregate_fills_seconds: int = 30
    aggregate_updates_seconds: int = 20
    max_messages_per_chain_per_minute: int = 4
    min_partial_fill_notify_pct: float = 10.0


class TopicsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    commands: TopicConfig
    tech_log: TechLogConfig
    clean_log: CleanLogConfig


class StartupConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: StartupMode = "auto"
    restore_max_age_seconds: int = 300


class ControlPlaneConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    token: str
    chat_id: int
    delivery_mode: Literal["supergroup_topics", "private_bot"] = "supergroup_topics"
    topics: TopicsConfig
    authorized_users: list[int] = Field(default_factory=list)
    startup: StartupConfig = Field(default_factory=StartupConfig)
    keyboard: list[list[str]] = Field(default_factory=list)
    notifications: dict[str, str] = Field(default_factory=dict)


class NotificationOutboxEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    notification_id: int | None = None
    notification_type: str
    destination: Destination
    payload_json: str
    priority: Priority = "MEDIUM"
    status: OutboxStatus = "PENDING"
    dedupe_key: str
    attempts: int = 0
    last_error: str | None = None
    created_at: datetime | None = None
    sent_at: datetime | None = None


class ControlCommand(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    command_request_id: str
    chat_id: str
    message_thread_id: str
    telegram_user_id: str
    telegram_username: str | None = None
    command_text: str
    command_name: str | None = None
    payload_json: str | None = None
    received_at: datetime | None = None
    status: CommandStatus = "RECEIVED"
    reject_reason: str | None = None
    execution_result: str | None = None
    idempotency_key: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ConfigOverride(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    override_key: str
    scope_type: Literal["GLOBAL", "PER_TRADER"]
    scope_value: str | None = None
    value_json: str
    created_by: str
    reason: str | None = None
    active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RuntimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    snapshot_at: datetime
    control_mode: str
    active_blocks_json: str
    open_chain_count: int
    pending_command_count: int
    shutdown_reason: str | None = None
    created_at: datetime | None = None


class CleanLogTracking(BaseModel):
    model_config = ConfigDict(extra="ignore")

    trade_chain_id: int
    clean_log_root_message_id: str | None = None
    clean_log_last_message_id: str | None = None
    telegram_chat_id: str
    telegram_thread_id: str | None = None
    original_message_link: str | None = None
    last_clean_log_event_type: str | None = None
    last_clean_log_sent_at: str | None = None
    updated_at: str


__all__ = [
    "CleanLogConfig",
    "CleanLogTracking",
    "CommandStatus",
    "ConfigOverride",
    "ControlCommand",
    "ControlPlaneConfig",
    "Destination",
    "NotificationOutboxEntry",
    "OutboxStatus",
    "Priority",
    "RuntimeSnapshot",
    "StartupConfig",
    "StartupMode",
    "TechLogConfig",
    "TopicConfig",
    "TopicsConfig",
]

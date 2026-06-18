# src/runtime_v2/control_plane/formatters/tech_log.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.templates.tech_log import TEMPLATE_REGISTRY


def format_tech_log(
    notification_type: str,
    payload: dict,
    *,
    delivery_mode: str = "supergroup_topics",
) -> str:
    config = TEMPLATE_REGISTRY.get(notification_type)
    body = (
        render_template(config.blocks, payload, transform=config.payload_transform)
        if config
        else _fallback(notification_type, payload)
    )
    if delivery_mode == "private_bot":
        return f"⚠️ --SYSTEM--\n{body}"
    return body


def _fallback(notification_type: str, payload: dict) -> str:
    level = str(payload.get("level", "INFO")).upper()
    description = payload.get("description") or notification_type
    return f"[{level}] {notification_type}\n────────────────\n{description}"


__all__ = ["format_tech_log"]

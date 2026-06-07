# src/runtime_v2/control_plane/formatters/clean_log.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    render_template, HeaderBlock, FooterBlock,
)
from src.runtime_v2.control_plane.formatters.templates.clean_log import TEMPLATE_REGISTRY


def format_clean_log(notification_type: str, payload: dict) -> str:
    # SL_FILLED with close_reason=BREAKEVEN_AFTER_TP routes to BE_EXIT behavior
    if notification_type == "SL_FILLED" and payload.get("close_reason") == "BREAKEVEN_AFTER_TP":
        notification_type = "BE_EXIT"
        payload = {**payload, "exit_price": payload.get("sl_price", payload.get("fill_price"))}

    config = TEMPLATE_REGISTRY.get(notification_type)
    if config:
        return render_template(config.blocks, payload, transform=config.payload_transform)
    return _fallback(notification_type, payload)


def _fallback(notification_type: str, payload: dict) -> str:
    blocks = [HeaderBlock("📊", notification_type), FooterBlock()]
    return render_template(blocks, payload)


__all__ = ["format_clean_log"]

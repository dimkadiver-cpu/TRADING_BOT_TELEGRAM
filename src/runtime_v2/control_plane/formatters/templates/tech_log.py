from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    SeparatorBlock, DerivedBlock,
    FieldBlock, FooterBlock,
    TemplateConfig,
)
from src.runtime_v2.control_plane.formatters._formatters import num, text
from src.runtime_v2.control_plane.formatters.display import display_symbol


def _tech_header(emoji: str, category: str, event_label: str) -> list:
    return [
        DerivedBlock(text_fn=lambda p, _e=emoji, _c=category, _l=event_label:
            f"{_e} {_c}: {_l}"),
        SeparatorBlock(),
    ]


_RUNTIME_STARTUP = TemplateConfig([
    *_tech_header("ℹ️", "RUNTIME", "AVVIATO"),
    FieldBlock("Started at", key="started_at", fmt=text, optional=False, default="n/a"),
    FooterBlock(default_source="runtime_main"),
])

_RUNTIME_SHUTDOWN = TemplateConfig([
    *_tech_header("ℹ️", "RUNTIME", "SHUTDOWN"),
    FieldBlock("Reason",           key="reason",           fmt=text, optional=False, default="n/a"),
    FieldBlock("Open chains",      key="open_chains",      fmt=num,  optional=False, default="n/a"),
    FieldBlock("Pending commands", key="pending_commands", fmt=num,  optional=False, default="n/a"),
    FooterBlock(default_source="runtime_main"),
])

_LISTENER_EDIT_SKIPPED = TemplateConfig([
    *_tech_header("⚠️", "LISTENER", "EDIT SKIPPED"),
    DerivedBlock(text_fn=lambda p: p.get("description") or ""),
    FieldBlock("Chat",    key="chat",    fmt=text),
    FieldBlock("Msg ID",  key="msg_id",  fmt=text),
    FieldBlock("Edit ts", key="edit_ts", fmt=text, optional=True),
    FieldBlock("Action",  key="action",  fmt=text, optional=True),
    FooterBlock(default_source="telegram_listener"),
])

_GATEWAY_ENTRY_ALL_FAILED = TemplateConfig([
    *_tech_header("🛑", "GATEWAY", "ENTRY ALL FAILED"),
    DerivedBlock(text_fn=lambda p: p.get("description") or ""),
    FieldBlock(
        "Chain",
        value_fn=lambda p: f"#{p['chain_id']}" if p.get("chain_id") is not None else None,
        fmt=text, optional=True,
    ),
    FieldBlock("Symbol", key="symbol", fmt=display_symbol, optional=True),
    FieldBlock("Side",   key="side",   fmt=text, optional=True),
    FieldBlock("Reason", key="reason", fmt=text, optional=False, default="n/a"),
    FieldBlock("Action", key="action", fmt=text, optional=True),
    FooterBlock(default_source="execution_gateway"),
])

_GATEWAY_REVIEW_REQUIRED = TemplateConfig([
    *_tech_header("⚠️", "GATEWAY", "REVIEW REQUIRED"),
    DerivedBlock(text_fn=lambda p: p.get("description") or ""),
    FieldBlock("Command", key="command_type", fmt=text, optional=True),
    FieldBlock(
        "Chain",
        value_fn=lambda p: f"#{p['chain_id']}" if p.get("chain_id") is not None else None,
        fmt=text, optional=True,
    ),
    FieldBlock("Reason", key="reason", fmt=text, optional=False, default="n/a"),
    FieldBlock("Action", key="action", fmt=text, optional=True),
    FooterBlock(default_source="execution_gateway"),
])

_GATEWAY_COMMAND_FAILED = TemplateConfig([
    *_tech_header("🛑", "GATEWAY", "COMMAND FAILED"),
    FieldBlock("Command", key="command_type", fmt=text, optional=True),
    FieldBlock(
        "Chain",
        value_fn=lambda p: f"#{p['chain_id']}" if p.get("chain_id") is not None else None,
        fmt=text, optional=True,
    ),
    FieldBlock("Reason", key="reason", fmt=text, optional=False, default="n/a"),
    FooterBlock(default_source="execution_gateway"),
])


TEMPLATE_REGISTRY: dict[str, TemplateConfig] = {
    "RUNTIME_STARTUP":          _RUNTIME_STARTUP,
    "RUNTIME_SHUTDOWN":         _RUNTIME_SHUTDOWN,
    "LISTENER_EDIT_SKIPPED":    _LISTENER_EDIT_SKIPPED,
    "GATEWAY_ENTRY_ALL_FAILED": _GATEWAY_ENTRY_ALL_FAILED,
    "GATEWAY_REVIEW_REQUIRED":  _GATEWAY_REVIEW_REQUIRED,
    "GATEWAY_COMMAND_FAILED":   _GATEWAY_COMMAND_FAILED,
}

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


def _runtime_status_template(emoji: str, event_label: str) -> TemplateConfig:
    return TemplateConfig([
        *_tech_header(emoji, "RUNTIME", event_label),
        FieldBlock("Phase", key="phase", fmt=text, optional=False, default="n/a"),
        FieldBlock("Control plane", key="control_plane", fmt=text, optional=False, default="n/a"),
        FieldBlock("Runtime", key="runtime", fmt=text, optional=False, default="n/a"),
        FieldBlock("Started at", key="started_at", fmt=text, optional=False, default="n/a"),
        SeparatorBlock(),
        FooterBlock(default_source="runtime_main"),
    ])


_RUNTIME_STARTING = _runtime_status_template("🟡", "STARTING")

_RUNTIME_READY = _runtime_status_template("🟢", "OK")

_RUNTIME_STARTUP = TemplateConfig([
    *_tech_header("🟡", "RUNTIME", "STARTING"),
    FieldBlock("Phase", key="phase", fmt=text, optional=False, default="BOOTSTRAP"),
    FieldBlock("Control plane", key="control_plane", fmt=text, optional=False, default="ACTIVE"),
    FieldBlock("Runtime", key="runtime", fmt=text, optional=False, default="INITIALIZING"),
    FieldBlock("Started at", key="started_at", fmt=text, optional=False, default="n/a"),
    SeparatorBlock(),
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
    FieldBlock("Trader", key="trader_id", fmt=text, optional=True),
    FieldBlock("Exchange Account", key="execution_account_id", fmt=text, optional=True),
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
    FieldBlock("Trader", key="trader_id", fmt=text, optional=True),
    FieldBlock("Exchange Account", key="execution_account_id", fmt=text, optional=True),
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
    FieldBlock("Trader", key="trader_id", fmt=text, optional=True),
    FieldBlock("Exchange Account", key="execution_account_id", fmt=text, optional=True),
    FieldBlock("Reason", key="reason", fmt=text, optional=False, default="n/a"),
    FooterBlock(default_source="execution_gateway"),
])


TEMPLATE_REGISTRY: dict[str, TemplateConfig] = {
    "RUNTIME_STARTING":         _RUNTIME_STARTING,
    "RUNTIME_READY":            _RUNTIME_READY,
    "RUNTIME_STARTUP":          _RUNTIME_STARTUP,
    "RUNTIME_SHUTDOWN":         _RUNTIME_SHUTDOWN,
    "LISTENER_EDIT_SKIPPED":    _LISTENER_EDIT_SKIPPED,
    "GATEWAY_ENTRY_ALL_FAILED": _GATEWAY_ENTRY_ALL_FAILED,
    "GATEWAY_REVIEW_REQUIRED":  _GATEWAY_REVIEW_REQUIRED,
    "GATEWAY_COMMAND_FAILED":   _GATEWAY_COMMAND_FAILED,
}

from __future__ import annotations

from src.runtime_v2.control_plane.service import BlockResult, UnblockResult

_SEP = "────────────────"


def _scope_label(scope_value: str | None) -> str:
    return "GLOBAL" if scope_value is None else scope_value


def format_block(result: BlockResult) -> str:
    scope = _scope_label(result.scope_value)
    title = (
        f"🚫 {result.symbol} BLOCCATO"
        if result.scope_value is None
        else f"🚫 {result.scope_value} / {result.symbol} BLOCCATO"
    )
    command = (
        f"/unblock {result.symbol}"
        if result.scope_value is None
        else f"/unblock {result.scope_value} {result.symbol}"
    )
    return "\n".join(
        [
            title,
            _SEP,
            f"Scope: {scope}",
            f"Blacklist: {', '.join(result.blacklist) if result.blacklist else 'none'}",
            "",
            "Commands:",
            command,
            "/control",
        ]
    )


def format_unblock(result: UnblockResult) -> str:
    scope = _scope_label(result.scope_value)
    title = (
        f"✅ {result.symbol} SBLOCCATO"
        if result.scope_value is None
        else f"✅ {result.scope_value} / {result.symbol} SBLOCCATO"
    )
    return "\n".join(
        [
            title,
            _SEP,
            f"Scope: {scope}",
            f"Blacklist: {', '.join(result.blacklist) if result.blacklist else 'none'}",
            "",
            "Commands:",
            "/control",
        ]
    )


__all__ = ["format_block", "format_unblock"]

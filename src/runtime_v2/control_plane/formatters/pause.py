from __future__ import annotations

from src.runtime_v2.control_plane.service import PauseResult, ResumeResult

_SEP = "----------------"


def format_pause(result: PauseResult) -> str:
    if result.scope_value is None:
        lines = [
            "NEW ENTRIES BLOCKED",
            _SEP,
            "Scope: GLOBAL",
            f"Mode: {result.mode}",
        ]
        if result.already_active:
            lines.append("Block already active.")
        lines += [
            "",
            "Effect:",
            "New signals are routed to REVIEW_REQUIRED.",
            "",
            "Commands:",
            "/resume",
            "/control",
        ]
        return "\n".join(lines)

    lines = [
        f"{result.scope_value} - NEW ENTRIES BLOCKED",
        _SEP,
        f"Scope: {result.scope_value}",
        f"Mode: {result.mode}",
    ]
    if result.already_active:
        lines.append("Block already active.")
    lines += [
        "",
        "Effect:",
        f"New signals for {result.scope_value} are routed to REVIEW_REQUIRED.",
        "",
        "Commands:",
        f"/resume {result.scope_value}",
        "/control",
    ]
    return "\n".join(lines)


def format_resume(result: ResumeResult) -> str:
    if not result.had_block:
        return "\n".join(
            [
                "NO ACTIVE BLOCK",
                _SEP,
                "No pause block exists for this scope.",
                "",
                "Commands:",
                "/control",
            ]
        )
    if result.scope_value is None:
        return "\n".join(
            [
                "NEW ENTRIES RE-ENABLED",
                _SEP,
                "Global block removed.",
                "",
                "Commands:",
                "/control",
                "/status",
            ]
        )
    return "\n".join(
        [
            f"{result.scope_value} - NEW ENTRIES RE-ENABLED",
            _SEP,
            f"Block removed for {result.scope_value}.",
            "",
            "Commands:",
            "/control",
        ]
    )


def format_start(result: ResumeResult) -> str:
    if result.had_block:
        details = "Global block removed."
    else:
        details = "Runtime was already accepting new entries."
    return "\n".join(
        [
            "RUNTIME ACTIVATED",
            _SEP,
            details,
            "",
            "Commands:",
            "/status",
            "/control",
        ]
    )


__all__ = ["format_pause", "format_resume", "format_start"]

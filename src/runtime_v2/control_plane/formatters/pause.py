from __future__ import annotations

from src.runtime_v2.control_plane.service import PauseResult, ResumeResult

_SEP = "────────────────"


def format_pause(
    result: PauseResult | None = None,
    *,
    scope: str | None = None,
    mode: str | None = None,
    source: str | None = None,
    command: str | None = None,
) -> str:
    # New spec-compliant path: keyword args provided
    if scope is not None:
        return "\n".join([
            "⏸️ EXECUTION PAUSED",
            _SEP,
            f"Scope: {scope}",
            f"Mode: {mode}",
            "Effect: new entries are blocked while existing positions remain managed",
            f"Source: {source}",
            f"Command: {command}",
        ])

    # Legacy path: PauseResult object
    if result is None:
        return ""

    if result.scope_value is None:
        lines = [
            "⏸️ NUOVE ENTRY BLOCCATE",
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
        f"⏸️ {result.scope_value} — NUOVE ENTRY BLOCCATE",
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


def format_resume(
    result: ResumeResult | None = None,
    *,
    scope: str | None = None,
    mode: str | None = None,
    source: str | None = None,
    command: str | None = None,
) -> str:
    # New spec-compliant path: keyword args provided
    if scope is not None:
        return "\n".join([
            "▶️ EXECUTION RESUMED",
            _SEP,
            f"Scope: {scope}",
            f"Mode: {mode}",
            "Effect: new entries may be accepted again according to rules",
            f"Source: {source}",
            f"Command: {command}",
        ])

    # Legacy path: ResumeResult object
    if result is None:
        return ""

    if not result.had_block:
        return "\n".join(
            [
                "ℹ️ NESSUN BLOCCO ATTIVO",
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
                "▶️ NUOVE ENTRY RIABILITATE",
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
            f"▶️ {result.scope_value} — NUOVE ENTRY RIABILITATE",
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
            "▶️ RUNTIME ATTIVATO",
            _SEP,
            details,
            "",
            "Commands:",
            "/status",
            "/control",
        ]
    )


__all__ = ["format_pause", "format_resume", "format_start"]

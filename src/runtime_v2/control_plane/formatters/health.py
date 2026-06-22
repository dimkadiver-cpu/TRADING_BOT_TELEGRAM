# src/runtime_v2/control_plane/formatters/health.py
from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import (
    render_template, StaticBlock, SeparatorBlock, DerivedBlock,
    ConditionalBlock, ListBlock, TemplateConfig,
)
from src.runtime_v2.control_plane.status_queries import HealthView


def _probe_status(view: HealthView) -> str:
    has_failed = any(w[1] == "FAILED" for w in view.workers)
    has_warning = any(w[1] == "WARNING" for w in view.workers)
    if has_failed or not view.db_ok:
        return "failed"
    if has_warning or not view.exchange_connected:
        return "partial"
    return "passed"


def _warnings(view: HealthView) -> list[str]:
    result = []
    for name, status, detail in view.workers:
        if status == "WARNING":
            result.append(f"  - {name.lower()}: {detail or 'degraded'}")
    has_failed = any(w[1] == "FAILED" for w in view.workers)
    if not view.exchange_connected and not has_failed and view.db_ok:
        result.append("  - exchange connectivity degraded")
    return result


def _criticals(view: HealthView) -> list[str]:
    result = []
    for name, status, detail in view.workers:
        if status == "FAILED":
            result.append(f"  - {name.lower()}: {detail or 'failed'}")
    if not view.db_ok:
        result.append("  - database unreachable")
    has_failed = any(w[1] == "FAILED" for w in view.workers)
    if not view.exchange_connected and (has_failed or not view.db_ok):
        result.append("  - exchange connectivity degraded")
    return result


def _render_worker(w: tuple, i: int, p: dict) -> list[str]:
    name, status, detail = w[0], w[1], w[2]
    suffix = f"  ({detail})" if detail else ""
    return [f"  {name:<22} {status}{suffix}"]


_HEALTH_BLOCKS: list = [
    StaticBlock("🩺 HEALTH  |  Global runtime"),
    SeparatorBlock(),
    DerivedBlock(text_fn=lambda p: f"Updated: {p.get('updated_at', 'n/a')}"),
    StaticBlock(""),
    StaticBlock("Workers:"),
    ListBlock(key="workers", item_renderer=_render_worker),
    StaticBlock(""),
    DerivedBlock(text_fn=lambda p: f"DB: {'OK' if p.get('db_ok') else 'ERROR'}"),
    DerivedBlock(text_fn=lambda p: f"Exchange: {'connected' if p.get('exchange_connected') else 'disconnected'}"),
    DerivedBlock(text_fn=lambda p: f"Checks: live probe {p.get('probe_status', 'unknown')}"),
    # Warnings section — only if any warnings exist
    ConditionalBlock(
        condition=lambda p: bool(p.get("warnings")),
        blocks=[
            StaticBlock(""),
            StaticBlock("Warnings:"),
            ListBlock(key="warnings", item_renderer=lambda w, i, p: [w]),
        ],
    ),
    # Critical section — only if any criticals exist
    ConditionalBlock(
        condition=lambda p: bool(p.get("criticals")),
        blocks=[
            StaticBlock(""),
            StaticBlock("Critical:"),
            ListBlock(key="criticals", item_renderer=lambda c, i, p: [c]),
        ],
    ),
]

TEMPLATE_HEALTH = TemplateConfig(_HEALTH_BLOCKS, payload_transform=None)


def format_health(view: HealthView) -> str:
    probe_status = _probe_status(view)
    warnings = _warnings(view)
    criticals = _criticals(view)
    payload = {
        "updated_at": view.updated_at,
        "workers": [(w[0], w[1], w[2]) for w in view.workers],
        "db_ok": view.db_ok,
        "exchange_connected": view.exchange_connected,
        "probe_status": probe_status,
        "warnings": warnings,
        "criticals": criticals,
    }
    return render_template(TEMPLATE_HEALTH.blocks, payload)


__all__ = ["format_health", "TEMPLATE_HEALTH"]

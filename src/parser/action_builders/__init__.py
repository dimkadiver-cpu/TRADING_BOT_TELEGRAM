"""Canonical action builders for parser V2 semantics."""

from __future__ import annotations

from .canonical_v2 import (
    build_actions_structured,
    derive_legacy_actions,
    legacy_action_for_action_type,
    normalize_cancel_scope,
    normalize_close_scope,
    normalize_hit_target,
    normalize_result_mode,
)

__all__ = [
    "build_actions_structured",
    "derive_legacy_actions",
    "legacy_action_for_action_type",
    "normalize_cancel_scope",
    "normalize_close_scope",
    "normalize_hit_target",
    "normalize_result_mode",
]

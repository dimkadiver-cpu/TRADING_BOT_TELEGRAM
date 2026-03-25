"""Models for the Target Resolver (Layer 5).

ResolvedTarget is defined in src/parser/models/operational.py (shared with
OperationalSignal and ResolvedSignal). This module re-exports it so that
target_resolver consumers can use a single, stable import path.

Usage:
    from src.target_resolver.models import ResolvedTarget
"""

from __future__ import annotations

# Re-export from the canonical location so both import paths work:
#   from src.parser.models.operational import ResolvedTarget
#   from src.target_resolver.models import ResolvedTarget
from src.parser.models.operational import ResolvedTarget

__all__ = ["ResolvedTarget"]

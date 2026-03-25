"""Layer 5 — Target Resolver.

Resolves target_ref in an OperationalSignal to concrete op_signal_ids
from the operational_signals table.

Public API:
    from src.target_resolver.resolver import TargetResolver
    resolver = TargetResolver()
    resolved = resolver.resolve(op_signal, db_path=db_path)
"""

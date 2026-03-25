"""Layer 4 — Operation Rules Engine.

Receives a TraderParseResult with validation_status=VALID and produces an
OperationalSignal ready for target resolution and storage.

Public API:
    from src.operation_rules.engine import OperationRulesEngine
    from src.operation_rules.loader import load_effective_rules, EffectiveRules
    from src.operation_rules.risk_calculator import compute_exposure
"""

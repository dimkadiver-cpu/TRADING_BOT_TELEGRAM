from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Column groups — canonical v1 (parsed_message_v1 schema)
# ---------------------------------------------------------------------------

_COMMON: list[str] = [
    "raw_message_id",
    "reply_to_message_id",
    "raw_text",
    "parse_status",
    "primary_class",
]

# UPDATE · REPORT · INFO · UNCLASSIFIED — extra columns
_UPDATE_REPORT_INFO_EXTRA: list[str] = [
    "warnings_summary",
    "primary_intent",
    "intents_confirmed",
    "intents_candidate",
    "intents_invalid",
    "intents_invalid_reason",
    "target_scope_scope",
    "target_refs",
    "new_stop_level",
    "close_scope",
    "close_fraction",
    "hit_target",
    "fill_state",
    "cancel_scope",
    "reported_results",
]

# SIGNAL (PARSED) · SETUP_INCOMPLETE (SIGNAL+PARTIAL) — extra columns
_SIGNAL_EXTRA: list[str] = [
    "symbol",
    "direction",
    "risk_hint_value",
    "market_type",
    "completeness",
    "entry_plan_type",
    "entry_structure",
    "entry_count",
    "entries_summary",
    "stop_loss_price",
    "tp_prices",
    "signal_id",
]

REPORT_SCHEMAS_V1: dict[str, list[str]] = {
    "ALL": _COMMON + _SIGNAL_EXTRA + _UPDATE_REPORT_INFO_EXTRA,
    "NEW_SIGNAL": _COMMON + _SIGNAL_EXTRA,
    "UPDATE": _COMMON + _UPDATE_REPORT_INFO_EXTRA,
    "REPORT": _COMMON + _UPDATE_REPORT_INFO_EXTRA,
    "INFO_ONLY": _COMMON + _UPDATE_REPORT_INFO_EXTRA,
    "SETUP_INCOMPLETE": _COMMON + _SIGNAL_EXTRA,
    "UNCLASSIFIED": _COMMON + _UPDATE_REPORT_INFO_EXTRA,
}

REPORT_SCOPES_V1: list[str] = [
    "ALL",
    "NEW_SIGNAL",
    "UPDATE",
    "REPORT",
    "INFO_ONLY",
    "SETUP_INCOMPLETE",
    "UNCLASSIFIED",
]


@dataclass(frozen=True, slots=True)
class ReportSchemaV1:
    scope: str
    columns: list[str]


def schema_for_scope_v1(scope: str) -> ReportSchemaV1:
    normalized = scope.strip().upper()
    columns = REPORT_SCHEMAS_V1.get(normalized)
    if columns is None:
        raise ValueError(f"Unsupported v1 report scope: {scope!r}")
    return ReportSchemaV1(scope=normalized, columns=list(columns))

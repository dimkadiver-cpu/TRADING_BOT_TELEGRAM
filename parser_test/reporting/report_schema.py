from __future__ import annotations

from dataclasses import dataclass

COMMON_COLUMNS: list[str] = [
    "raw_message_id",
    "reply_to_message_id",
    "parse_status",
    "message_type",
    "raw_text",
    "primary_intent",
    "intents",
    "intents_raw",
    "warning_text",
    "warnings_summary",
    "validation_warning_count",
]

_ALL_MESSAGE_COLUMNS: list[str] = [
    "event_type",
    "message_class",
    "symbol",
    "direction",
    "market_type",
    "status",
    "confidence",
    "parser_used",
    "parser_mode",
    "entry_plan_type",
    "entry_structure",
    "has_averaging_plan",
    "entry_count",
    "entries_summary",
    "stop_loss_price",
    "tp_prices",
    "tp_count",
    "signal_id",
    "target_scope_kind",
    "target_scope_scope",
    "target_refs",
    "target_refs_count",
    "linking_strategy",
    "new_stop_level",
    "close_scope",
    "close_fraction",
    "hit_target",
    "fill_state",
    "result_mode",
    "cancel_scope",
    "reported_results",
    "reported_profit_percent",
    "reported_leverage_hint",
    "notes_summary",
    "links_count",
    "hashtags_count",
    "diagnostics_summary",
]

_NEW_SIGNAL_COLUMNS: list[str] = [
    "symbol",
    "direction",
    "market_type",
    "completeness",
    "confidence",
    "parser_used",
    "parser_mode",
    "status",
    "entry_plan_type",
    "entry_structure",
    "has_averaging_plan",
    "entry_count",
    "entries_summary",
    "stop_loss_price",
    "tp_prices",
    "tp_count",
    "signal_id",
    "target_refs",
    "target_refs_count",
    "linking_strategy",
    "notes_summary",
    "links_count",
    "hashtags_count",
    "diagnostics_summary",
]

_UPDATE_COLUMNS: list[str] = [
    "symbol",
    "direction",
    "market_type",
    "confidence",
    "parser_used",
    "parser_mode",
    "status",
    "signal_id",
    "target_scope_kind",
    "target_scope_scope",
    "target_refs",
    "target_refs_count",
    "linking_strategy",
    "new_stop_level",
    "close_scope",
    "close_fraction",
    "hit_target",
    "fill_state",
    "result_mode",
    "cancel_scope",
    "reported_results",
    "reported_profit_percent",
    "reported_leverage_hint",
    "tp_prices",
    "links_count",
    "hashtags_count",
    "diagnostics_summary",
]

_INFO_ONLY_COLUMNS: list[str] = [
    "symbol",
    "direction",
    "signal_id",
    "linking_strategy",
    "reported_results",
    "reported_profit_percent",
    "reported_leverage_hint",
    "notes_summary",
    "target_refs_count",
    "links_count",
    "hashtags_count",
    "diagnostics_summary",
]

_SETUP_INCOMPLETE_COLUMNS: list[str] = [
    "symbol",
    "direction",
    "completeness",
    "missing_fields",
    "entry_plan_type",
    "entry_structure",
    "has_averaging_plan",
    "missing_stop_flag",
    "missing_tp_flag",
    "missing_entry_flag",
    "notes_summary",
    "diagnostics_summary",
]

_UNCLASSIFIED_COLUMNS: list[str] = [
    "signal_id",
    "target_refs_count",
    "links_count",
    "hashtags_count",
    "diagnostics_summary",
    "notes_summary",
]

REPORT_SCHEMAS: dict[str, list[str]] = {
    "ALL": COMMON_COLUMNS + _ALL_MESSAGE_COLUMNS,
    "NEW_SIGNAL": COMMON_COLUMNS + _NEW_SIGNAL_COLUMNS,
    "UPDATE": COMMON_COLUMNS + _UPDATE_COLUMNS,
    "INFO_ONLY": COMMON_COLUMNS + _INFO_ONLY_COLUMNS,
    "SETUP_INCOMPLETE": COMMON_COLUMNS + _SETUP_INCOMPLETE_COLUMNS,
    "UNCLASSIFIED": COMMON_COLUMNS + _UNCLASSIFIED_COLUMNS,
}


@dataclass(frozen=True, slots=True)
class ReportSchema:
    scope: str
    columns: list[str]


@dataclass(frozen=True, slots=True)
class ReportSchemaOptions:
    include_legacy_debug: bool = False
    include_json_debug: bool = False


def schema_for_scope(
    scope: str,
    *,
    include_legacy_debug: bool = False,
    include_json_debug: bool = False,
) -> ReportSchema:
    normalized = scope.strip().upper()
    columns = REPORT_SCHEMAS.get(normalized)
    if columns is None:
        raise ValueError(f"Unsupported report scope: {scope}")
    ordered = list(columns)
    if include_legacy_debug:
        ordered.extend(["raw_text", "action_types", "actions_structured_summary", "legacy_actions"])
    if include_json_debug:
        ordered.append("normalized_json_debug")
    return ReportSchema(scope=normalized, columns=ordered)

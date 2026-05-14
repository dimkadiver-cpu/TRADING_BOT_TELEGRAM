from __future__ import annotations

_COMMON_COLUMNS = [
    "run_id",
    "trader_id",
    "primary_class",
    "parse_status",
    "raw_message_id",
    "telegram_message_id",
    "reply_to_message_id",
    "raw_text",
    "confidence",
    "warnings",
    "diagnostics_summary",
]

_SIGNAL_COLUMNS = [
    "symbol",
    "side",
    "entry_structure",
    "entries_count",
    "entries_summary",
    "stop_loss_price",
    "take_profit_count",
    "take_profit_prices",
    "risk_hint_raw",
    "risk_hint_value",
    "risk_hint_min_value",
    "risk_hint_max_value",
    "leverage_hint",
    "missing_fields",
    "completeness",
]

_UPDATE_COLUMNS = [
    "primary_intent",
    "intents",
    "groups_count",
    "actions_count",
    "actions_summary",
    "action_types",
    "source_intents",
    "action_confidences",
    "action_raw_fragments",
    "target_scope_hint",
    "target_reply_to_message_id",
    "target_telegram_message_ids",
    "target_telegram_links",
    "target_explicit_ids",
    "target_symbols",
    "set_stop_target_type",
    "set_stop_price",
    "set_stop_tp_level",
    "close_scope",
    "close_fraction",
    "close_price",
    "cancel_scope_hint",
    "modify_entries_kind",
    "modify_entries_count",
    "modify_entries_summary",
    "modify_entries_entry_structure",
    "modify_targets_mode",
    "modify_targets_count",
    "modify_targets_prices",
    "modify_targets_target_tp_level",
    "invalidate_reason_text",
]

_REPORT_COLUMNS = [
    "primary_intent",
    "intents",
    "report_events_count",
    "report_events_summary",
    "report_event_types",
    "report_event_levels",
    "report_event_prices",
    "report_event_source_intents",
    "report_event_raw_fragments",
    "report_result_raw_fragment",
    "hit_target",
    "hit_price",
]

_INFO_COLUMNS = [
    "primary_intent",
    "intents",
    "info_raw_fragment",
]

_ERRORS_COLUMNS = [
    "run_id",
    "raw_message_id",
    "telegram_message_id",
    "primary_intent",
    "intents",
    "trader_id",
    "parser_profile",
    "primary_class",
    "parse_status",
    "primary_intent",
    "error_status",
    "error_message",
    "warnings",
    "diagnostics_summary",
    "raw_text",
]

SCOPE_COLUMNS: dict[str, list[str]] = {
    "ALL": _COMMON_COLUMNS,
    "NEW_SIGNAL": _COMMON_COLUMNS + _SIGNAL_COLUMNS,
    "SETUP_INCOMPLETE": _COMMON_COLUMNS + _SIGNAL_COLUMNS,
    "UPDATE": _COMMON_COLUMNS + _UPDATE_COLUMNS,
    "REPORT": _COMMON_COLUMNS + _REPORT_COLUMNS,
    "INFO_ONLY": _COMMON_COLUMNS + _INFO_COLUMNS,
    "UNCLASSIFIED": _COMMON_COLUMNS,
    "ERRORS": _ERRORS_COLUMNS,
}

__all__ = ["SCOPE_COLUMNS"]

from __future__ import annotations

from typing import Literal

MessageClass = Literal["SIGNAL", "UPDATE", "REPORT", "INFO"]
ParseStatus = Literal["PARSED", "PARTIAL", "UNCLASSIFIED", "ERROR"]
EvidenceStatus = Literal["RESOLVED", "AMBIGUOUS", "LOW_CONFIDENCE"]

IntentType = Literal[
    "MOVE_STOP_TO_BE",
    "MOVE_STOP",
    "CLOSE_FULL",
    "CLOSE_PARTIAL",
    "CANCEL_PENDING",
    "INVALIDATE_SETUP",
    "REENTER",
    "ADD_ENTRY",
    "MODIFY_ENTRY",
    "MODIFY_TARGETS",
    "ENTRY_FILLED",
    "TP_HIT",
    "SL_HIT",
    "EXIT_BE",
    "REPORT_RESULT",
    "INFO_ONLY",
]
IntentCategory = Literal["SIGNAL", "UPDATE", "REPORT", "INFO"]

Side = Literal["LONG", "SHORT"]
EntryStructure = Literal["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"]
EntryType = Literal["MARKET", "LIMIT"]
EntryRole = Literal["PRIMARY", "AVERAGING", "UNKNOWN"]

ModifyEntryMode = Literal[
    "MARKET_NOW",
    "UPDATE_PRICE",
    "UPDATE_RANGE",
    "REPLACE_ENTRY",
    "REMOVE",
    "UNKNOWN",
]
ModifyEntriesOperationKind = Literal[
    "ADD",
    "REENTER",
    "MARKET_NOW",
    "UPDATE_PRICE",
    "UPDATE_RANGE",
    "REPLACE_ENTRY",
    "REMOVE",
    "UNKNOWN",
]
ModifyTargetsMode = Literal["REPLACE_ALL", "ADD", "UPDATE_ONE", "REMOVE_ONE", "UNKNOWN"]

ScopeHint = Literal[
    "SINGLE_SIGNAL",
    "SYMBOL",
    "ALL_LONG",
    "ALL_SHORT",
    "ALL_POSITIONS",
    "ALL_OPEN",
    "ALL_REMAINING",
    "UNKNOWN",
]
CancelScopeHint = Literal["TARGETED", "ALL_PENDING", "ALL_LONG", "ALL_SHORT", "ALL_POSITIONS", "UNKNOWN"]

UpdateOperationType = Literal[
    "SET_STOP",
    "CLOSE",
    "CANCEL_PENDING",
    "MODIFY_ENTRIES",
    "MODIFY_TARGETS",
    "INVALIDATE_SETUP",
]
SetStopTargetType = Literal["ENTRY", "PRICE", "TP_LEVEL", "RISK_TARGET"]
CloseScope = Literal["FULL", "PARTIAL"]
ReportEventType = Literal["ENTRY_FILLED", "TP_HIT", "SL_HIT", "EXIT_BE"]

MarkerStrength = Literal["strong", "weak"]
MarkerKind = Literal[
    "intent",
    "field",
    "side",
    "entry_type",
    "modify_entry_mode",
    "entry_selector",
    "info",
    "target_hint",
]
TargetSource = Literal[
    "LOCAL_TEXT_LINK",
    "LOCAL_EXPLICIT_ID",
    "MESSAGE_TEXT_LINK",
    "MESSAGE_EXPLICIT_ID",
    "REPLY",
    "SYMBOL",
    "GLOBAL_SCOPE",
    "UNKNOWN",
]
Completeness = Literal["COMPLETE", "INCOMPLETE"]

STRONG_WEIGHT: float = 1.0
WEAK_WEIGHT: float = 0.4

PARSED_MESSAGE_SCHEMA_VERSION = "parsed_message_v2"
CANONICAL_MESSAGE_SCHEMA_VERSION = "canonical_message_v2"

INTENT_CATEGORY_BY_TYPE: dict[str, str] = {
    "MOVE_STOP_TO_BE": "UPDATE",
    "MOVE_STOP": "UPDATE",
    "CLOSE_FULL": "UPDATE",
    "CLOSE_PARTIAL": "UPDATE",
    "CANCEL_PENDING": "UPDATE",
    "INVALIDATE_SETUP": "UPDATE",
    "REENTER": "UPDATE",
    "ADD_ENTRY": "UPDATE",
    "MODIFY_ENTRY": "UPDATE",
    "MODIFY_TARGETS": "UPDATE",
    "ENTRY_FILLED": "REPORT",
    "TP_HIT": "REPORT",
    "SL_HIT": "REPORT",
    "EXIT_BE": "REPORT",
    "REPORT_RESULT": "REPORT",
    "INFO_ONLY": "INFO",
}

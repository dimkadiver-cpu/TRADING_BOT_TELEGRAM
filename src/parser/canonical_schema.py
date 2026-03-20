"""Shared canonical intent schema loaded from repository CSV source-of-truth."""

from __future__ import annotations

from csv import DictReader
from functools import lru_cache
from pathlib import Path

_CANONICAL_SCHEMA_CSV = "schema_consigliato_finale_parser.csv"

# Legacy aliases observed in profiles / historical datasets.
_INTENT_ALIASES: dict[str, str] = {
    "U_TP_HIT_EXPLICIT": "U_TP_HIT",
    "U_UPDATE_STOP": "U_MOVE_STOP",
}


@lru_cache(maxsize=1)
def load_canonical_intent_schema() -> dict[str, dict[str, object]]:
    repo_root = Path(__file__).resolve().parents[2]
    csv_path = repo_root / _CANONICAL_SCHEMA_CSV
    if not csv_path.exists():
        return {}

    specs: dict[str, dict[str, object]] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = DictReader(handle)
        for row in reader:
            intent = (row.get("intent") or "").strip()
            if not intent:
                continue
            specs[intent] = {
                "category": (row.get("category") or "").strip() or None,
                "description": (row.get("description") or "").strip() or None,
                "canonical_action": (row.get("canonical_action") or "").strip() or None,
                "required_entities": _split_entities(row.get("required_entities")),
                "optional_entities": _split_entities(row.get("optional_entities")),
            }
    return specs


def normalize_intent_name(intent: str) -> str:
    normalized = intent.strip()
    return _INTENT_ALIASES.get(normalized, normalized)


def normalize_intents(intents: list[str] | None) -> list[str]:
    if not intents:
        return []
    out: list[str] = []
    for raw in intents:
        if not isinstance(raw, str) or not raw.strip():
            continue
        intent = normalize_intent_name(raw)
        if intent not in out:
            out.append(intent)
    return out


def canonical_action_for_intent(intent: str) -> str | None:
    spec = load_canonical_intent_schema().get(normalize_intent_name(intent))
    if not spec:
        return None
    value = spec.get("canonical_action")
    return str(value) if isinstance(value, str) and value else None


def required_entities_for_intent(intent: str) -> list[str]:
    spec = load_canonical_intent_schema().get(normalize_intent_name(intent))
    if not spec:
        return []
    required = spec.get("required_entities")
    return list(required) if isinstance(required, list) else []


def canonical_intents() -> set[str]:
    return set(load_canonical_intent_schema().keys())


def _split_entities(raw: str | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for chunk in raw.split(";"):
        value = chunk.strip()
        if value and value not in out:
            out.append(value)
    return out


_TRADER_INTENT_SUPPORT: dict[str, dict[str, list[str]]] = {
    "TA": {
        "supported": ["NS_CREATE_SIGNAL", "U_MOVE_STOP", "U_MOVE_STOP_TO_BE", "U_CANCEL_PENDING_ORDERS", "U_INVALIDATE_SETUP", "U_CLOSE_FULL", "U_CLOSE_PARTIAL", "U_TP_HIT", "U_STOP_HIT", "U_MARK_FILLED", "U_REPORT_FINAL_RESULT"],
        "partial": ["U_MANUAL_CLOSE"],
    },
    "TB": {
        "supported": ["NS_CREATE_SIGNAL", "U_MOVE_STOP", "U_MOVE_STOP_TO_BE", "U_CLOSE_FULL", "U_STOP_HIT", "U_TP_HIT", "U_CANCEL_PENDING_ORDERS", "U_REPORT_FINAL_RESULT"],
        "partial": [],
    },
    "TC": {
        "supported": ["NS_CREATE_SIGNAL", "U_ACTIVATION", "U_TP_HIT", "U_MOVE_STOP_TO_BE", "U_EXIT_BE", "U_CLOSE_PARTIAL", "U_CLOSE_FULL", "U_CANCEL_PENDING_ORDERS", "U_REMOVE_PENDING_ENTRY", "U_UPDATE_TAKE_PROFITS", "U_UPDATE_PENDING_ENTRY", "U_STOP_HIT", "U_REENTER"],
        "partial": ["U_MOVE_STOP"],
    },
    "T3": {
        "supported": ["NS_CREATE_SIGNAL", "U_CLOSE_FULL", "U_REENTER", "U_TP_HIT", "U_STOP_HIT"],
        "partial": [],
    },
    "TD": {
        "supported": ["NS_CREATE_SIGNAL", "U_MOVE_STOP", "U_MOVE_STOP_TO_BE", "U_CLOSE_FULL", "U_CLOSE_PARTIAL", "U_CANCEL_PENDING_ORDERS", "U_TP_HIT", "U_STOP_HIT", "U_EXIT_BE", "U_UPDATE_TAKE_PROFITS"],
        "partial": [],
    },
}


def trader_intent_support(trader_id: str | None) -> dict[str, list[str]]:
    key = (trader_id or "").upper()
    support = _TRADER_INTENT_SUPPORT.get(key, {"supported": [], "partial": []})
    canonical = canonical_intents()
    supported = [i for i in support.get("supported", []) if i in canonical]
    partial = [i for i in support.get("partial", []) if i in canonical]
    unsupported = sorted(canonical.difference(set(supported)).difference(set(partial)))
    return {"supported": supported, "partial": partial, "unsupported": unsupported}

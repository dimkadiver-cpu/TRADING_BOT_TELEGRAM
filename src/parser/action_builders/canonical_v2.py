"""Canonical V2 action builder.

This module centralizes the operational action contract for parser outputs.
Profiles may contribute semantic extraction, but the final action_type mapping
must flow through here so V2 stays the single source of truth.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from src.parser.canonical_schema import normalize_intents

_LINE_SPLIT_RE = re.compile(r"[\r\n]+")

_LINK_ID_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/(?P<id>\d+)", re.IGNORECASE)
_LEGACY_ALL_OPEN_POSITIONS = "ALL_" + "ALL"

_ACTION_TYPE_BY_INTENT: dict[str, str] = {
    "NS_CREATE_SIGNAL": "CREATE_SIGNAL",
    "U_ACTIVATION": "ACTIVATION",
    "U_ADD_ENTRY": "ADD_ENTRY",
    "U_CANCEL_PENDING_ORDERS": "CANCEL_PENDING",
    "U_CLOSE_FULL": "CLOSE_POSITION",
    "U_CLOSE_PARTIAL": "CLOSE_POSITION",
    "U_EXIT_BE": "CLOSE_POSITION",
    "U_INVALIDATE_SETUP": "INVALIDATE_SETUP",
    "U_MANUAL_CLOSE": "CLOSE_POSITION",
    "U_MARK_FILLED": "MARK_FILLED",
    "U_MOVE_STOP": "MOVE_STOP",
    "U_MOVE_STOP_TO_BE": "MOVE_STOP",
    "U_REENTER": "REENTER_POSITION",
    "U_REMOVE_PENDING_ENTRY": "REMOVE_PENDING_ENTRY",
    "U_REPORT_FINAL_RESULT": "ATTACH_RESULT",
    "U_REVERSE_SIGNAL": "REVERSE_SIGNAL",
    "U_RISK_NOTE": "RISK_NOTE",
    "U_STOP_HIT": "MARK_STOP_HIT",
    "U_TP_HIT": "MARK_TP_HIT",
    "U_UPDATE_PENDING_ENTRY": "UPDATE_PENDING_ENTRY",
    "U_UPDATE_TAKE_PROFITS": "UPDATE_TAKE_PROFITS",
}

_LEGACY_BY_ACTION_TYPE: dict[str, str] = {
    "ACTIVATION": "ACT_MARK_SIGNAL_ACTIVE",
    "ADD_ENTRY": "ACT_ADD_ENTRY",
    "ATTACH_RESULT": "ACT_ATTACH_RESULT",
    "CANCEL_PENDING": "ACT_CANCEL_ALL_PENDING_ENTRIES",
    "CLOSE_POSITION": "ACT_CLOSE_FULL",
    "CREATE_SIGNAL": "ACT_CREATE_SIGNAL",
    "INVALIDATE_SETUP": "ACT_MARK_SIGNAL_INVALID",
    "MARK_FILLED": "ACT_MARK_ORDER_FILLED",
    "MARK_STOP_HIT": "ACT_MARK_STOP_HIT",
    "MARK_TP_HIT": "ACT_MARK_TP_HIT",
    "MOVE_STOP": "ACT_MOVE_STOP_LOSS",
    "REENTER_POSITION": "ACT_REENTER_POSITION",
    "REMOVE_PENDING_ENTRY": "ACT_REMOVE_PENDING_ENTRY",
    "REVERSE_SIGNAL": "ACT_REVERSE_SIGNAL_OR_CREATE_OPPOSITE",
    "RISK_NOTE": "ACT_ATTACH_RISK_NOTE",
    "UPDATE_PENDING_ENTRY": "ACT_UPDATE_PENDING_ENTRY",
    "UPDATE_TAKE_PROFITS": "ACT_UPDATE_TAKE_PROFITS",
}


def canonical_action_type_for_intent(intent: str) -> str | None:
    return _ACTION_TYPE_BY_INTENT.get(intent)


def build_actions_structured(
    *,
    intents: Iterable[str],
    entities: dict[str, Any] | None = None,
    raw_text: str = "",
    target_scope: dict[str, Any] | None = None,
    target_refs: Iterable[int] | None = None,
    reported_results: list[dict[str, Any]] | None = None,
    message_type: str | None = None,
    primary_intent: str | None = None,
) -> list[dict[str, Any]]:
    payload = entities if isinstance(entities, dict) else {}
    scope = target_scope if isinstance(target_scope, dict) else {}
    refs = _unique_ints([value for value in target_refs or [] if isinstance(value, int)])
    results = [item for item in reported_results or [] if isinstance(item, dict)]
    normalized_intents = normalize_intents(list(intents))

    actions: list[dict[str, Any]] = []
    for intent in normalized_intents:
        action_type = canonical_action_type_for_intent(intent)
        if action_type is None:
            continue
        action = _base_action(
            action_type=action_type,
            intent=intent,
            raw_text=raw_text,
            payload=payload,
            scope=scope,
            target_refs=refs,
            reported_results=results,
            message_type=message_type,
            primary_intent=primary_intent,
        )
        _apply_specific_fields(action, intent=intent, payload=payload, scope=scope, target_refs=refs, reported_results=results)
        actions.append(action)
    return actions


def refine_actions_structured_for_targeting(
    actions_structured: list[dict[str, Any]],
    *,
    message_type: str | None,
    intents: Iterable[str],
    raw_text: str = "",
    target_refs: Iterable[int] | None = None,
    global_target_scope: str | None = None,
    enable_targeting_refinement: bool = False,
) -> list[dict[str, Any]]:
    if not enable_targeting_refinement or message_type != "UPDATE":
        return [dict(item) for item in actions_structured if isinstance(item, dict)]

    normalized_intents = set(normalize_intents(list(intents)))
    target_ids = _unique_ints([value for value in target_refs or [] if isinstance(value, int)])
    refined = [dict(item) for item in actions_structured if isinstance(item, dict)]

    granular_stop_actions = _build_line_level_move_stop_actions(raw_text=raw_text)
    if granular_stop_actions:
        remaining = [item for item in refined if item.get("action_type") != "MOVE_STOP"]
        return [*granular_stop_actions, *remaining]

    explicit_target_ids = _explicit_target_message_ids(raw_text=raw_text, target_ids=target_ids)
    if "U_CLOSE_FULL" in normalized_intents and len(explicit_target_ids) >= 2:
        targeted_close = {
            "action_type": "CLOSE_POSITION",
            "close_scope": "FULL",
            "targeting": {
                "mode": "TARGET_GROUP",
                "targets": explicit_target_ids,
            },
        }
        remaining = [item for item in refined if item.get("action_type") != "CLOSE_POSITION"]
        return [targeted_close, *remaining]

    selector = _selector_from_global_scope(global_target_scope=global_target_scope)
    if "U_CLOSE_FULL" in normalized_intents and selector:
        targeted_close = {
            "action_type": "CLOSE_POSITION",
            "close_scope": "FULL",
            "targeting": {
                "mode": "SELECTOR",
                "selector": selector,
            },
        }
        remaining = [item for item in refined if item.get("action_type") != "CLOSE_POSITION"]
        return [targeted_close, *remaining]

    return refined


def _explicit_target_message_ids(*, raw_text: str, target_ids: Iterable[int]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for value in _unique_ints([value for value in target_ids if isinstance(value, int)]):
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    for line in split_lines(raw_text):
        for link in _extract_telegram_links(line):
            match = _LINK_ID_RE.search(link)
            if not match:
                continue
            message_id = int(match.group("id"))
            if message_id in seen:
                continue
            seen.add(message_id)
            out.append(message_id)
    return out


def derive_legacy_actions(actions_structured: Iterable[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for action in actions_structured:
        if not isinstance(action, dict):
            continue
        legacy = legacy_action_for_action_type(action.get("action_type"), action)
        if legacy and legacy not in out:
            out.append(legacy)
    return out


def legacy_action_for_action_type(action_type: Any, action: dict[str, Any] | None = None) -> str | None:
    if not isinstance(action_type, str):
        return None
    action_payload = action if isinstance(action, dict) else {}
    if action_type == "MOVE_STOP":
        new_stop_level = _normalize_stop_level(action_payload.get("new_stop_level"))
        if new_stop_level in {"ENTRY", "BREAKEVEN"}:
            return "ACT_MOVE_STOP_LOSS_TO_BE"
        return "ACT_MOVE_STOP_LOSS"
    if action_type == "CLOSE_POSITION":
        close_scope = _normalize_close_scope(action_payload.get("close_scope"))
        if close_scope == "PARTIAL":
            return "ACT_CLOSE_PARTIAL"
        if close_scope in {"BREAKEVEN", "BE"}:
            return "ACT_MARK_POSITION_CLOSED"
        if action_payload.get("close_status_passive"):
            return "ACT_CLOSE_FULL_AND_MARK_CLOSED"
        return "ACT_CLOSE_FULL"
    if action_type == "CANCEL_PENDING":
        return "ACT_CANCEL_ALL_PENDING_ENTRIES"
    if action_type == "MARK_TP_HIT":
        return "ACT_MARK_TP_HIT"
    if action_type == "MARK_STOP_HIT":
        return "ACT_MARK_STOP_HIT"
    return _LEGACY_BY_ACTION_TYPE.get(action_type)


def normalize_cancel_scope(value: Any, *, target_refs: Iterable[int] | None = None, target_scope: dict[str, Any] | None = None) -> str:
    if _has_any_int(target_refs):
        return "TARGETED"
    scope = target_scope if isinstance(target_scope, dict) else {}
    scope_kind = str(scope.get("kind") or "").strip().lower()
    scope_value = _normalize_cancel_scope_value(scope.get("scope") or scope.get("scope_value"))
    if scope_kind in {"portfolio_side", "portfolio"}:
        if scope_value in {"ALL_LONGS", "ALL_LONG", "LONG"}:
            return "ALL_LONG"
        if scope_value in {"ALL_SHORTS", "ALL_SHORT", "SHORT"}:
            return "ALL_SHORT"
        return "ALL_PENDING_ENTRIES"

    raw = _normalize_cancel_scope_value(value)
    if raw in {"TARGETED", "ALL_LONG", "ALL_SHORT", "ALL_PENDING_ENTRIES"}:
        return raw
    if raw in {_LEGACY_ALL_OPEN_POSITIONS, "ALL_OPEN", "ALL_REMAINING"}:
        return "ALL_PENDING_ENTRIES"
    if raw in {"ALL_LONGS"}:
        return "ALL_LONG"
    if raw in {"ALL_SHORTS"}:
        return "ALL_SHORT"
    if raw:
        return raw
    return "ALL_PENDING_ENTRIES"


def normalize_close_scope(value: Any) -> str | None:
    raw = _normalize_close_scope(value)
    if raw in {"FULL", "PARTIAL", "BREAKEVEN"}:
        return raw
    if raw in {"ALL_LONGS", "ALL_SHORTS", _LEGACY_ALL_OPEN_POSITIONS, "ALL_OPEN", "ALL_REMAINING", "ALL_REMAINING_LONGS", "ALL_REMAINING_SHORTS"}:
        return "FULL"
    return raw or None


def normalize_hit_target(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip().upper()
    if raw.startswith("TP") and raw[2:].isdigit():
        return raw
    if raw in {"TP", "STOP"}:
        return raw
    return raw or None


def normalize_result_mode(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip().upper()
    if raw in {"R_MULTIPLE", "TEXT_SUMMARY", "BREAKEVEN", "PERCENT", "RAW"}:
        return raw
    return raw or None


def _base_action(
    *,
    action_type: str,
    intent: str,
    raw_text: str,
    payload: dict[str, Any],
    scope: dict[str, Any],
    target_refs: list[int],
    reported_results: list[dict[str, Any]],
    message_type: str | None,
    primary_intent: str | None,
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "action_type": action_type,
        "intent": intent,
        "message_type": message_type,
        "confidence": 0.75,
        "target_refs": list(target_refs),
        "target_refs_count": len(target_refs),
        "target_scope": _compact_target_scope(scope),
        "applies_to": _applies_to(payload=payload, scope=scope, target_refs=target_refs),
        "raw_fragment": _raw_fragment(raw_text=raw_text, intent=intent),
    }
    if primary_intent is not None:
        action["primary_intent"] = primary_intent
    symbol = payload.get("symbol") or payload.get("symbol_raw")
    if symbol is not None:
        action["symbol"] = symbol
    side = payload.get("side") or payload.get("direction")
    if side is not None:
        action["side"] = side
    return action


def _apply_specific_fields(
    action: dict[str, Any],
    *,
    intent: str,
    payload: dict[str, Any],
    scope: dict[str, Any],
    target_refs: list[int],
    reported_results: list[dict[str, Any]],
) -> None:
    if action["action_type"] == "CREATE_SIGNAL":
        action["entries"] = list(payload.get("entry_plan_entries") or payload.get("entries") or payload.get("entry") or [])
        action["entry_plan_type"] = payload.get("entry_plan_type")
        action["entry_structure"] = payload.get("entry_structure")
        action["has_averaging_plan"] = bool(payload.get("has_averaging_plan"))
        action["stop_loss"] = payload.get("stop_loss")
        action["take_profits"] = list(payload.get("take_profits") or [])
        action["market_type"] = payload.get("market_context") or payload.get("market_type")
        return

    if action["action_type"] == "MOVE_STOP":
        action["new_stop_level"] = payload.get("new_stop_level")
        action["new_stop_price"] = payload.get("new_stop_price")
        action["stop_basis"] = payload.get("new_stop_reference_text")
        action["target_refs"] = list(target_refs)
        action["target_refs_count"] = len(target_refs)
        return

    if action["action_type"] == "CANCEL_PENDING":
        action["cancel_scope"] = normalize_cancel_scope(
            payload.get("cancel_scope"),
            target_refs=target_refs,
            target_scope=scope,
        )
        return

    if action["action_type"] == "CLOSE_POSITION":
        close_scope = normalize_close_scope(payload.get("close_scope"))
        if close_scope is None:
            close_scope = "FULL"
        action["close_scope"] = close_scope
        action["close_fraction"] = payload.get("close_fraction")
        action["close_price"] = payload.get("close_price") or payload.get("reported_close_price")
        action["target_refs"] = list(target_refs)
        action["target_refs_count"] = len(target_refs)
        if payload.get("result_mode") is not None:
            action["result_mode"] = normalize_result_mode(payload.get("result_mode"))
        if intent == "U_EXIT_BE":
            action["result_mode"] = action.get("result_mode") or "BREAKEVEN"
        if intent == "U_MANUAL_CLOSE":
            action["manual"] = True
        return

    if action["action_type"] == "MARK_TP_HIT":
        action["hit_target"] = normalize_hit_target(payload.get("hit_target")) or _hit_target_from_intent(intent, payload)
        action["close_fraction"] = payload.get("close_fraction")
        action["result_mode"] = normalize_result_mode(payload.get("result_mode")) or ("R_MULTIPLE" if reported_results else None)
        action["reported_results"] = list(reported_results)
        return

    if action["action_type"] == "MARK_STOP_HIT":
        action["hit_target"] = "STOP"
        action["result_mode"] = normalize_result_mode(payload.get("result_mode")) or ("R_MULTIPLE" if reported_results else None)
        return

    if action["action_type"] == "ATTACH_RESULT":
        action["reported_results"] = list(reported_results)
        action["result_mode"] = normalize_result_mode(payload.get("result_mode")) or ("R_MULTIPLE" if reported_results else "TEXT_SUMMARY")
        action["result_value"] = payload.get("result_value")
        action["result_unit"] = payload.get("result_unit")
        return

    if action["action_type"] == "REMOVE_PENDING_ENTRY":
        action["target_entry_id"] = scope.get("target_entry_id") or payload.get("target_entry_id")
        return

    if action["action_type"] == "INVALIDATE_SETUP":
        action["reason"] = payload.get("invalidation_reason") or payload.get("setup_invalidation")
        return

    if action["action_type"] == "MARK_FILLED":
        action["fill_state"] = payload.get("fill_state") or "FILLED"
        return

    if action["action_type"] == "UPDATE_TAKE_PROFITS":
        action["take_profits"] = list(payload.get("take_profits") or [])
        action["target_refs"] = list(target_refs)
        return

    if action["action_type"] == "UPDATE_PENDING_ENTRY":
        action["target_entry_id"] = scope.get("target_entry_id") or payload.get("target_entry_id")
        action["new_entry_price"] = payload.get("new_entry_price")
        return

    if action["action_type"] == "ADD_ENTRY":
        action["new_entry_price"] = payload.get("new_entry_price")
        action["role"] = payload.get("role")
        return

    if action["action_type"] == "RISK_NOTE":
        action["risk_text"] = payload.get("risk_text") or payload.get("notes")
        return

    if action["action_type"] == "REENTER_POSITION":
        action["entries"] = list(payload.get("entry_plan_entries") or payload.get("entries") or [])
        action["stop_loss"] = payload.get("stop_loss")
        action["take_profits"] = list(payload.get("take_profits") or [])


def _compact_target_scope(scope: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(scope, dict) or not scope:
        return {"kind": None, "scope": None}
    if "kind" in scope or "scope" in scope:
        return {"kind": scope.get("kind"), "scope": scope.get("scope")}
    return {
        "kind": scope.get("scope_type") or scope.get("targeting"),
        "scope": scope.get("scope_value"),
    }


def _applies_to(*, payload: dict[str, Any], scope: dict[str, Any], target_refs: list[int]) -> dict[str, Any]:
    close_scope = normalize_close_scope(payload.get("close_scope"))
    if close_scope is not None:
        if close_scope == "FULL" and not target_refs:
            return {"scope_type": "portfolio_scope", "scope_value": "ALL_OPEN_POSITIONS"}
        return {"scope_type": "close_scope", "scope_value": close_scope}

    cancel_scope = normalize_cancel_scope(payload.get("cancel_scope"), target_refs=target_refs, target_scope=scope)
    if cancel_scope and cancel_scope != "ALL_PENDING_ENTRIES":
        return {"scope_type": "cancel_scope", "scope_value": cancel_scope}
    if cancel_scope == "ALL_PENDING_ENTRIES" and not target_refs:
        return {"scope_type": "pending_scope", "scope_value": "ALL_PENDING_ENTRIES"}

    if target_refs:
        return {"scope_type": "target_refs", "scope_value": list(target_refs)}

    scope_kind = scope.get("kind") if isinstance(scope, dict) else None
    scope_value = scope.get("scope") if isinstance(scope, dict) else None
    return {"scope_type": scope_kind, "scope_value": scope_value}


def _hit_target_from_intent(intent: str, payload: dict[str, Any]) -> str | None:
    if intent == "U_STOP_HIT":
        return "STOP"
    if intent == "U_TP_HIT":
        marker = payload.get("hit_target")
        if isinstance(marker, str) and marker.strip():
            return normalize_hit_target(marker)
    return None


def _raw_fragment(*, raw_text: str, intent: str) -> str | None:
    text = (raw_text or "").strip()
    if not text:
        return None
    lowered = text.lower()
    hints: dict[str, tuple[str, ...]] = {
        "U_MOVE_STOP_TO_BE": ("breakeven", "to be", "в бу"),
        "U_MOVE_STOP": ("move stop", "move sl", "стоп"),
        "U_CLOSE_FULL": ("close", "закры"),
        "U_CLOSE_PARTIAL": ("partial", "част"),
        "U_TP_HIT": ("tp", "тейк"),
        "U_STOP_HIT": ("stop", "стоп"),
        "U_CANCEL_PENDING_ORDERS": ("cancel", "отмен"),
    }
    for marker in hints.get(intent, ()):
        idx = lowered.find(marker)
        if idx >= 0:
            start = max(0, idx - 32)
            end = min(len(text), idx + len(marker) + 48)
            return text[start:end].strip()
    return text[:120]


def _build_line_level_move_stop_actions(*, raw_text: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen_targets: set[int] = set()
    for line in split_lines(raw_text):
        level = _line_stop_level(line=line)
        if level is None:
            continue
        message_ids = _extract_message_ids_from_line(line=line)
        if not message_ids:
            continue
        for message_id in message_ids:
            if message_id in seen_targets:
                continue
            seen_targets.add(message_id)
            actions.append(
                {
                    "action_type": "MOVE_STOP",
                    "new_stop_level": level,
                    "targeting": {
                        "mode": "EXPLICIT_TARGETS",
                        "targets": [message_id],
                    },
                }
            )
    return actions


def _extract_message_ids_from_line(*, line: str) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for link in _extract_telegram_links(line):
        match = _LINK_ID_RE.search(link)
        if not match:
            continue
        message_id = int(match.group("id"))
        if message_id in seen:
            continue
        seen.add(message_id)
        out.append(message_id)
    return out


def _extract_telegram_links(raw_text: str) -> list[str]:
    return [match.group(0) for match in _TELEGRAM_LINK_RE.finditer(raw_text)]


_TELEGRAM_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/\d+|[A-Za-z0-9_]+)/\d+", re.IGNORECASE)


def _line_stop_level(*, line: str) -> str | None:
    normalized = normalize_text(line)
    if any(marker in normalized for marker in ("stop on 1 tp", "stop on tp1", "stop on first tp", "стоп на 1 тейк", "стоп на первый тейк")):
        return "TP1"
    if any(
        marker in normalized
        for marker in (
            "стоп в бу",
            "стопы в бу",
            "стоп в безубыток",
            "stop to be",
            "stop to breakeven",
            "stop to entry",
        )
    ):
        return "ENTRY"
    return None


def _selector_from_global_scope(*, global_target_scope: str | None) -> dict[str, str] | None:
    if global_target_scope in {"ALL_REMAINING_SHORTS", "ALL_SHORTS"}:
        return {"side": "SHORT", "status": "OPEN"}
    if global_target_scope in {"ALL_REMAINING_LONGS", "ALL_LONGS"}:
        return {"side": "LONG", "status": "OPEN"}
    return None


def normalize_text(text: str) -> str:
    return _LINE_SPLIT_RE.sub(" ", (text or "").lower()).strip()


def split_lines(text: str) -> list[str]:
    return [line.strip() for line in _LINE_SPLIT_RE.split(text or "") if line.strip()]

def _normalize_stop_level(value: Any) -> str | float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    raw = value.strip().upper()
    if raw in {"ENTRY", "BE", "BREAKEVEN"}:
        return "ENTRY"
    return raw or None


def _normalize_close_scope(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip().upper()
    if raw in {"FULL", "PARTIAL", "BREAKEVEN", "BE"}:
        return "BREAKEVEN" if raw == "BE" else raw
    if raw in {"ALL_LONGS", "ALL_SHORTS", _LEGACY_ALL_OPEN_POSITIONS, "ALL_OPEN", "ALL_REMAINING", "ALL_REMAINING_LONGS", "ALL_REMAINING_SHORTS"}:
        return "FULL"
    return raw or None


def _normalize_cancel_scope_value(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().upper()


def _has_any_int(values: Iterable[int] | None) -> bool:
    if not values:
        return False
    for value in values:
        if isinstance(value, int):
            return True
    return False


def _unique_ints(values: Iterable[int]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out

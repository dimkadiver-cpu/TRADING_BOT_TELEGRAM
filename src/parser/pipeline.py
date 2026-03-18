"""Minimal parser pipeline for raw -> parse_result."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

from src.core.trader_tags import first_normalized_trader_tag, normalize_trader_aliases
from src.core.timeutils import utc_now_iso
from src.parser.dispatcher import ParserDispatcher
from src.parser.intent_action_map import derive_primary_intent, infer_update_intents_from_text, map_intents_to_actions
from src.parser.llm_adapter import LLMInvalidResponse, LLMNotConfigured, LLMParseError, LLMRequestFailed
from src.parser.normalization import ParseResultNormalized, build_parse_result_normalized
from src.parser.parser_config import ParserModeResolver
from src.parser.trader_profiles.base import ParserContext
from src.parser.trader_profiles.common_utils import extract_hashtags, extract_telegram_links
from src.parser.trader_profiles.registry import canonicalize_trader_code, get_profile_parser
from src.storage.parse_results import ParseResultRecord

_ADMIN_MARKERS = ("#admin", "weekly stats", "performance recap", "stats")
_UPDATE_ACTION_MARKERS = (
    "cancel",
    "close",
    "move sl",
    "breakeven",
    "tp hit",
    "move stop",
    "modify entry",
    "modify stop",
    "modify target",
)
_UPDATE_INFO_MARKERS = (
    "market update",
    "vip market update",
    "weekly update",
    "daily update",
    "macro update",
)
_RISKY_MARKERS = ("#risky", "risky", "high risk")

_SYMBOL_RE = re.compile(r"\b([A-Z]{2,12}(?:USDT|USDC|USD|BTC|ETH)?)\b")
_ENTRY_RE = re.compile(r"(?:entry|entries?)\s*[:=@-]?\s*([0-9][0-9.,]*(?:\s*-\s*[0-9][0-9.,]*)?)", re.IGNORECASE)
_STOP_RE = re.compile(r"(?:sl|stop(?:\s*loss)?)\s*[:=@-]?\s*([0-9][0-9.,]*)", re.IGNORECASE)
_TP_RE = re.compile(r"(?:tp\d*|target\s*\d*)\s*[:=@-]?\s*([0-9][0-9.,]*)", re.IGNORECASE)
_LEVERAGE_RE = re.compile(r"\b([0-9]{1,3}(?:\.[0-9]+)?)\s*x\b", re.IGNORECASE)
_RISK_RE = re.compile(r"(?:risk\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?%)|([0-9]+(?:\.[0-9]+)?%)\s*risk)", re.IGNORECASE)


@dataclass(slots=True)
class ParserInput:
    raw_message_id: int
    raw_text: str | None
    eligibility_status: str
    eligibility_reason: str
    resolved_trader_id: str | None
    trader_resolution_method: str
    linkage_method: str | None
    source_chat_id: str | None = None
    source_message_id: int | None = None
    linkage_reference_id: int | None = None
    parser_mode: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None


@dataclass(slots=True)
class ExtractedFields:
    symbol: str | None
    direction: str | None
    entry_raw: str | None
    stop_raw: str | None
    targets: list[str]
    leverage_hint: str | None
    risk_hint: str | None
    risky_flag: bool


class MinimalParserPipeline:
    def __init__(
        self,
        trader_aliases: dict[str, str],
        global_parser_mode: str = "regex_only",
        trader_parser_modes: dict[str, str] | None = None,
        global_llm_provider: str = "openai",
        global_llm_model: str | None = None,
        trader_llm_provider_overrides: dict[str, str] | None = None,
        trader_llm_model_overrides: dict[str, str] | None = None,
        dispatcher: ParserDispatcher | None = None,
    ) -> None:
        self._trader_aliases = normalize_trader_aliases(trader_aliases)
        self._mode_resolver = ParserModeResolver(
            global_parser_mode=global_parser_mode,
            trader_overrides=trader_parser_modes or {},
            global_llm_provider=global_llm_provider,
            trader_llm_provider_overrides=trader_llm_provider_overrides or {},
            global_llm_model=global_llm_model,
            trader_llm_model_overrides=trader_llm_model_overrides or {},
        )
        self._dispatcher = dispatcher or ParserDispatcher()

    def parse(self, payload: ParserInput) -> ParseResultRecord:
        source_trader_label = payload.resolved_trader_id
        canonical_trader_id = canonicalize_trader_code(source_trader_label) or source_trader_label
        if payload.parser_mode is not None:
            effective_mode = payload.parser_mode
        elif (
            source_trader_label
            and self._mode_resolver.trader_overrides
            and source_trader_label in self._mode_resolver.trader_overrides
        ):
            effective_mode = self._mode_resolver.get_effective_parser_mode(source_trader_label)
        else:
            effective_mode = self._mode_resolver.get_effective_parser_mode(canonical_trader_id)

        if payload.llm_provider is not None:
            effective_llm_provider = payload.llm_provider
        elif (
            source_trader_label
            and self._mode_resolver.trader_llm_provider_overrides
            and source_trader_label in self._mode_resolver.trader_llm_provider_overrides
        ):
            effective_llm_provider = self._mode_resolver.get_effective_llm_provider(source_trader_label)
        else:
            effective_llm_provider = self._mode_resolver.get_effective_llm_provider(canonical_trader_id)

        if payload.llm_model is not None:
            effective_llm_model = payload.llm_model
        elif (
            source_trader_label
            and self._mode_resolver.trader_llm_model_overrides
            and source_trader_label in self._mode_resolver.trader_llm_model_overrides
        ):
            effective_llm_model = self._mode_resolver.get_effective_llm_model(source_trader_label)
        else:
            effective_llm_model = self._mode_resolver.get_effective_llm_model(canonical_trader_id)

        dispatch_payload = payload
        if (
            dispatch_payload.parser_mode != effective_mode
            or dispatch_payload.llm_provider != effective_llm_provider
            or dispatch_payload.llm_model != effective_llm_model
            or dispatch_payload.resolved_trader_id != canonical_trader_id
        ):
            dispatch_payload = ParserInput(
                raw_message_id=payload.raw_message_id,
                raw_text=payload.raw_text,
                eligibility_status=payload.eligibility_status,
                eligibility_reason=payload.eligibility_reason,
                resolved_trader_id=canonical_trader_id,
                trader_resolution_method=payload.trader_resolution_method,
                linkage_method=payload.linkage_method,
                source_chat_id=payload.source_chat_id,
                source_message_id=payload.source_message_id,
                linkage_reference_id=payload.linkage_reference_id,
                parser_mode=effective_mode,
                llm_provider=effective_llm_provider,
                llm_model=effective_llm_model,
            )

        declared_tag = self._extract_declared_trader_tag((payload.raw_text or "").strip())

        try:
            decision = self._dispatcher.dispatch_parse(
                parser_input=dispatch_payload,
                parser_mode=effective_mode,
                parse_with_regex=self._parse_with_regex_normalized,
            )
            normalized_result = decision.selected
            selection_reason = decision.selection_reason
        except (LLMNotConfigured, LLMParseError, LLMRequestFailed, LLMInvalidResponse):
            # llm_only may be requested while adapter is unavailable.
            normalized_result = self._parse_with_regex_normalized(dispatch_payload, effective_mode)
            selection_reason = "llm_only_unavailable_regex_fallback"
            normalized_result.selection_metadata = {
                "llm_attempted": True,
                "fallback_from_regex": True,
                "selection_reason": selection_reason,
            }
            if "llm_unavailable" not in normalized_result.validation_warnings:
                normalized_result.validation_warnings.append("llm_unavailable")
            normalized_result.status = "PARSED_WITH_WARNINGS"
        metadata = dict(normalized_result.selection_metadata or {})
        if source_trader_label is not None:
            metadata["source_trader_label"] = source_trader_label
        if canonical_trader_id is not None:
            metadata["canonical_trader_id"] = canonical_trader_id
        normalized_result.selection_metadata = metadata

        normalized_message_type = normalized_result.message_type or "UNCLASSIFIED"
        completeness = _derive_completeness(normalized_result)
        parse_status = normalized_result.status or "PARSED"
        is_executable = (
            normalized_message_type == "NEW_SIGNAL"
            and canonical_trader_id is not None
            and payload.eligibility_status == "ACQUIRED_ELIGIBLE"
        )
        linkage_status = "LINKED" if payload.linkage_method else "UNLINKED"
        warnings = list(normalized_result.validation_warnings)

        now = utc_now_iso()
        return ParseResultRecord(
            raw_message_id=payload.raw_message_id,
            eligibility_status=payload.eligibility_status,
            eligibility_reason=payload.eligibility_reason,
            declared_trader_tag=declared_tag,
            resolved_trader_id=canonical_trader_id,
            trader_resolution_method=payload.trader_resolution_method,
            message_type=normalized_message_type,
            parse_status=parse_status,
            completeness=completeness,
            is_executable=is_executable,
            symbol=normalized_result.symbol,
            direction=_legacy_direction(normalized_result.direction),
            entry_raw=_legacy_entry_raw(normalized_result.entries),
            stop_raw=_legacy_stop_raw(normalized_result.stop_loss),
            target_raw_list=json.dumps(_legacy_target_raw_list(normalized_result.take_profits), ensure_ascii=False),
            leverage_hint=None,
            risk_hint=None,
            risky_flag=bool(any(marker in (payload.raw_text or "").lower() for marker in _RISKY_MARKERS)),
            linkage_method=payload.linkage_method,
            linkage_status=linkage_status,
            warning_text="; ".join(warnings) if warnings else None,
            notes="; ".join(normalized_result.notes) if normalized_result.notes else None,
            parse_result_normalized_json=json.dumps(normalized_result.as_dict(), ensure_ascii=False, sort_keys=True),
            created_at=now,
            updated_at=now,
        )

    def _parse_with_regex_normalized(self, payload: object, parser_mode: str) -> ParseResultNormalized:
        data = payload if isinstance(payload, ParserInput) else ParserInput(**payload.__dict__)
        text = (data.raw_text or "").strip()
        normalized = text.lower()
        extracted = self._extract_fields(text, normalized)
        message_type = self._classify_message(
            normalized=normalized,
            has_strong_link=data.linkage_method is not None,
            extracted=extracted,
        )
        warnings: list[str] = []
        notes_parts = [f"classified={message_type}"]
        intents: list[str] = []
        profile_entities: dict[str, object] | None = None
        profile_target_ids: list[int] = []
        profile_reported_results: list[dict[str, object]] = []
        profile_confidence: float | None = None
        profile_v2_fields: dict[str, object] | None = None
        profile_used = False
        profile_code = _resolve_profile_code(data.resolved_trader_id)
        profile_parser = get_profile_parser(profile_code) if profile_code else None
        if profile_parser is not None:
            try:
                profile_result = profile_parser.parse_message(
                    text=text,
                    context=ParserContext(
                        trader_code=profile_code,
                        message_id=data.source_message_id,
                        reply_to_message_id=data.linkage_reference_id,
                        channel_id=data.source_chat_id,
                        raw_text=text,
                        extracted_links=extract_telegram_links(text),
                        hashtags=extract_hashtags(text),
                    ),
                )
                profile_used = True
                message_type = profile_result.message_type or message_type
                intents = list(profile_result.intents)
                profile_entities = dict(profile_result.entities)
                profile_target_ids = _profile_target_ids(profile_result.target_refs)
                profile_reported_results = _normalize_profile_reported_results(profile_result.reported_results)
                profile_confidence = profile_result.confidence
                profile_v2_fields = {
                    "message_class": getattr(profile_result, "message_class", None) or getattr(profile_result, "message_type", None),
                    "primary_intent": getattr(profile_result, "primary_intent", None),
                    "actions_structured": getattr(profile_result, "actions_structured", None),
                    "target_scope": getattr(profile_result, "target_scope", None),
                    "linking": getattr(profile_result, "linking", None),
                    "diagnostics": getattr(profile_result, "diagnostics", None),
                }
                warnings.extend(profile_result.warnings)
                notes_parts.append(f"profile_parser={profile_code}")
            except Exception:
                warnings.append(f"profile_parser_failed:{profile_code}")
        if profile_used:
            extracted = _apply_profile_entities_to_extracted(extracted, profile_entities)

        has_complete_setup = (
            extracted.symbol is not None
            and extracted.direction is not None
            and extracted.entry_raw is not None
            and extracted.stop_raw is not None
            and len(extracted.targets) > 0
        )
        if message_type == "NEW_SIGNAL" and not has_complete_setup and not profile_used:
            message_type = "SETUP_INCOMPLETE"
            notes_parts[0] = f"classified={message_type}"

        if data.resolved_trader_id is None:
            warnings.append("unresolved trader")
        if message_type == "SETUP_INCOMPLETE":
            warnings.append("missing mandatory setup fields")
        if message_type == "UPDATE" and not intents:
            intents = infer_update_intents_from_text(normalized)

        actions = map_intents_to_actions(intents, entities=profile_entities)
        if message_type == "UPDATE" and not actions:
            actions = ["ACT_REQUEST_MANUAL_REVIEW"]

        normalized_result = build_parse_result_normalized(
            message_type=message_type,
            normalized_text=normalized,
            trader_id=data.resolved_trader_id,
            source_chat_id=data.source_chat_id,
            source_message_id=data.source_message_id,
            raw_text=text,
            parser_used="regex",
            parser_mode=parser_mode,
            parse_status="PARSED",
            instrument=extracted.symbol,
            side=extracted.direction,
            entry_raw=extracted.entry_raw,
            stop_raw=extracted.stop_raw,
            targets=extracted.targets,
            root_ref=data.linkage_reference_id,
            existing_warnings=warnings,
            notes=notes_parts,
            intents=intents,
            actions=actions,
            entities=profile_entities,
        )
        if profile_target_ids:
            normalized_result.target_refs = _merge_unique_ints(normalized_result.target_refs, profile_target_ids)
        if profile_reported_results:
            normalized_result.reported_results = profile_reported_results
        if isinstance(profile_confidence, float) and 0.0 <= profile_confidence <= 1.0:
            normalized_result.confidence = round(profile_confidence, 4)
        normalized_result = _refine_semantics(normalized_result)
        normalized_result = _enrich_v2_semantics(normalized_result, profile_v2_fields=profile_v2_fields)
        # Keep a single policy for weak updates: warn and request review only when no concrete target refs.
        if normalized_result.message_type == "UPDATE":
            has_target_refs = bool(normalized_result.target_refs)
            if not has_target_refs and "update without strong link" not in normalized_result.validation_warnings:
                normalized_result.validation_warnings.append("update without strong link")
            if not normalized_result.actions and "ACT_REQUEST_MANUAL_REVIEW" not in normalized_result.actions:
                normalized_result.actions = [*normalized_result.actions, "ACT_REQUEST_MANUAL_REVIEW"]
        if normalized_result.validation_warnings:
            normalized_result.status = "PARSED_WITH_WARNINGS"
        return normalized_result

    def _extract_declared_trader_tag(self, text: str) -> str | None:
        extracted = first_normalized_trader_tag(text)
        if extracted and extracted in self._trader_aliases:
            return extracted
        return None

    def _extract_fields(self, text: str, normalized: str) -> ExtractedFields:
        symbol = None
        symbol_match = _SYMBOL_RE.search(text)
        if symbol_match:
            symbol = symbol_match.group(1).upper()

        direction = None
        if " buy " in f" {normalized} " or " long " in f" {normalized} ":
            direction = "BUY"
        elif " sell " in f" {normalized} " or " short " in f" {normalized} ":
            direction = "SELL"

        entry_match = _ENTRY_RE.search(text)
        stop_match = _STOP_RE.search(text)
        targets = [m.group(1).strip() for m in _TP_RE.finditer(text)]
        leverage_match = _LEVERAGE_RE.search(text)
        risk_match = _RISK_RE.search(text)
        risk_hint = None
        if risk_match:
            risk_hint = (risk_match.group(1) or risk_match.group(2) or "").strip()

        return ExtractedFields(
            symbol=symbol,
            direction=direction,
            entry_raw=entry_match.group(1).strip() if entry_match else None,
            stop_raw=stop_match.group(1).strip() if stop_match else None,
            targets=targets,
            leverage_hint=leverage_match.group(1).strip() if leverage_match else None,
            risk_hint=risk_hint,
            risky_flag=any(marker in normalized for marker in _RISKY_MARKERS),
        )

    def _classify_message(
        self,
        normalized: str,
        has_strong_link: bool,
        extracted: ExtractedFields,
    ) -> str:
        if not normalized:
            return "UNCLASSIFIED"
        if any(marker in normalized for marker in _ADMIN_MARKERS):
            return "INFO_ONLY"

        has_update_word = "update" in normalized
        has_update_action = any(marker in normalized for marker in _UPDATE_ACTION_MARKERS)
        has_info_update_marker = any(marker in normalized for marker in _UPDATE_INFO_MARKERS)
        setup_fields_count = sum(
            1
            for x in (
                extracted.symbol,
                extracted.direction,
                extracted.entry_raw,
                extracted.stop_raw,
            )
            if x is not None
        ) + (1 if extracted.targets else 0)

        if has_update_action and has_strong_link:
            return "UPDATE"

        if has_info_update_marker and not has_strong_link and not has_update_action and setup_fields_count < 2:
            return "INFO_ONLY"

        has_complete_setup = (
            extracted.symbol is not None
            and extracted.direction is not None
            and extracted.entry_raw is not None
            and extracted.stop_raw is not None
            and bool(extracted.targets)
        )
        if has_complete_setup:
            return "NEW_SIGNAL"
        if setup_fields_count >= 2:
            return "SETUP_INCOMPLETE"

        if has_update_word and not has_update_action and not has_strong_link and setup_fields_count < 2:
            return "INFO_ONLY"
        if has_update_action:
            return "SETUP_INCOMPLETE"
        if "signal" in normalized or "entry" in normalized or "tp" in normalized or "sl" in normalized:
            return "SETUP_INCOMPLETE"
        if "admin" in normalized or "stats" in normalized:
            return "INFO_ONLY"
        return "UNCLASSIFIED"



def _is_meaningful_v2_field(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _enrich_v2_semantics(
    normalized_result: ParseResultNormalized,
    *,
    profile_v2_fields: dict[str, object] | None,
) -> ParseResultNormalized:
    # Default to normalized semantic values from v1/v2 builder.
    if not normalized_result.message_class:
        normalized_result.message_class = normalized_result.message_type
    if not normalized_result.primary_intent:
        normalized_result.primary_intent = derive_primary_intent(normalized_result.intents)

    v2_fallbacks_used = {
        "actions_structured": False,
        "instrument_obj": False,
        "position_obj": False,
        "entry_plan": False,
        "risk_plan": False,
        "results_v2": False,
        "target_scope": False,
        "linking": False,
    }

    if not _is_meaningful_v2_field(normalized_result.actions_structured):
        v2_fallbacks_used["actions_structured"] = True
        normalized_result.actions_structured = [
            {"action": action, "kind": "legacy_action"}
            for action in normalized_result.actions
            if isinstance(action, str) and action
        ]
    if not _is_meaningful_v2_field(normalized_result.instrument_obj):
        v2_fallbacks_used["instrument_obj"] = True
        normalized_result.instrument_obj = {
            "symbol": normalized_result.instrument,
            "symbol_raw": normalized_result.instrument,
            "base_asset": None,
            "quote_asset": None,
            "market_type": normalized_result.market_type,
            "exchange_hint": None,
        }
    if not _is_meaningful_v2_field(normalized_result.position_obj):
        v2_fallbacks_used["position_obj"] = True
        normalized_result.position_obj = {
            "side": normalized_result.side,
            "direction": normalized_result.side,
            "entry_mode": normalized_result.entry_mode,
            "entry_plan_type": normalized_result.entry_plan_type,
            "entry_structure": normalized_result.entry_structure,
            "has_averaging_plan": normalized_result.has_averaging_plan,
        }
    if not _is_meaningful_v2_field(normalized_result.entry_plan):
        v2_fallbacks_used["entry_plan"] = True
        normalized_result.entry_plan = {
            "entries": list(normalized_result.entries),
            "entry_plan_type": normalized_result.entry_plan_type,
            "entry_structure": normalized_result.entry_structure,
            "has_averaging_plan": normalized_result.has_averaging_plan,
        }
    if not _is_meaningful_v2_field(normalized_result.risk_plan):
        v2_fallbacks_used["risk_plan"] = True
        normalized_result.risk_plan = {
            "stop_loss": normalized_result.stop_loss,
            "take_profits": normalized_result.take_profits,
            "invalidation": normalized_result.entities.get("new_stop_level") if isinstance(normalized_result.entities, dict) else None,
            "risk_hint": normalized_result.entities.get("risk_hint") if isinstance(normalized_result.entities, dict) else None,
            "risk_percent": normalized_result.entities.get("risk_percent") if isinstance(normalized_result.entities, dict) else None,
        }
    if not _is_meaningful_v2_field(normalized_result.results_v2) and normalized_result.reported_results:
        v2_fallbacks_used["results_v2"] = True
        normalized_result.results_v2 = [
            {
                "symbol": item.get("symbol"),
                "value": item.get("r_multiple"),
                "unit": item.get("unit") or "R",
                "direction": normalized_result.side,
                "raw_fragment": None,
                "result_type": "R_MULTIPLE" if str(item.get("unit") or "R").upper() == "R" else "UNKNOWN",
            }
            for item in normalized_result.reported_results
            if isinstance(item, dict)
        ]
    if not _is_meaningful_v2_field(normalized_result.target_scope):
        v2_fallbacks_used["target_scope"] = True
        normalized_result.target_scope = {
            "kind": "signal",
            "scope": "single" if normalized_result.target_refs else "unknown",
            "target_refs": list(normalized_result.target_refs),
            "root_ref": normalized_result.root_ref,
        }
    if not _is_meaningful_v2_field(normalized_result.linking):
        v2_fallbacks_used["linking"] = True
        normalized_result.linking = {
            "targeted": bool(normalized_result.target_refs or normalized_result.root_ref is not None),
            "target_refs_count": len(normalized_result.target_refs),
            "root_ref": normalized_result.root_ref,
            "strategy": "reply_or_link" if (normalized_result.target_refs or normalized_result.root_ref is not None) else "unresolved",
        }
    if not normalized_result.diagnostics:
        normalized_result.diagnostics = {
            "pipeline": "minimal",
            "has_warnings": bool(normalized_result.validation_warnings),
            "warning_count": len(normalized_result.validation_warnings),
        }
    normalized_result.diagnostics["v2_fallbacks_used"] = v2_fallbacks_used

    if not isinstance(profile_v2_fields, dict):
        return normalized_result

    # When profile provides v2 semantics, prefer profile-level trader-specific precision.
    message_class = profile_v2_fields.get("message_class")
    if isinstance(message_class, str) and message_class:
        normalized_result.message_class = message_class

    primary_intent = profile_v2_fields.get("primary_intent")
    if isinstance(primary_intent, str) and primary_intent:
        normalized_result.primary_intent = primary_intent

    actions_structured = profile_v2_fields.get("actions_structured")
    if isinstance(actions_structured, list):
        filtered_actions_structured = [item for item in actions_structured if isinstance(item, dict)]
        if filtered_actions_structured:
            normalized_result.actions_structured = filtered_actions_structured

    target_scope = profile_v2_fields.get("target_scope")
    if isinstance(target_scope, dict) and target_scope:
        normalized_result.target_scope = dict(target_scope)

    linking = profile_v2_fields.get("linking")
    if isinstance(linking, dict) and linking:
        normalized_result.linking = dict(linking)

    diagnostics = profile_v2_fields.get("diagnostics")
    if isinstance(diagnostics, dict) and diagnostics:
        merged_diagnostics = dict(normalized_result.diagnostics)
        merged_diagnostics.update(diagnostics)
        normalized_result.diagnostics = merged_diagnostics

    return normalized_result

def _legacy_direction(direction: str | None) -> str | None:
    if direction == "LONG":
        return "BUY"
    if direction == "SHORT":
        return "SELL"
    return None


def _legacy_entry_raw(entries: list[dict[str, object]]) -> str | None:
    raws = [str(entry.get("raw")) for entry in entries if entry.get("raw")]
    if not raws:
        return None
    return "-".join(raws)


def _legacy_stop_raw(stop_loss: dict[str, object] | None) -> str | None:
    if not stop_loss:
        return None
    raw = stop_loss.get("raw")
    return str(raw) if raw else None


def _legacy_target_raw_list(take_profits: list[dict[str, object]]) -> list[str]:
    values: list[str] = []
    for item in take_profits:
        raw = item.get("raw")
        if raw:
            values.append(str(raw))
    return values


def _derive_completeness(normalized_result: ParseResultNormalized) -> str:
    message_type = normalized_result.message_type or "UNCLASSIFIED"
    if message_type in {"UNCLASSIFIED", "SETUP_INCOMPLETE"}:
        return "INCOMPLETE"
    return "COMPLETE"


def _refine_semantics(result: ParseResultNormalized) -> ParseResultNormalized:
    if result.message_type == "NEW_SIGNAL":
        if "NS_CREATE_SIGNAL" not in result.intents:
            result.intents = [*result.intents, "NS_CREATE_SIGNAL"]
        entities = dict(result.entities or {})
        if entities.get("symbol") is None and result.symbol:
            entities["symbol"] = result.symbol
        if entities.get("side") is None and result.direction:
            entities["side"] = result.direction
        if entities.get("entry") is None and result.entries:
            entries = [value.get("price") for value in result.entries if isinstance(value.get("price"), float)]
            if entries:
                entities["entry"] = entries
        if entities.get("stop_loss") is None and isinstance(result.stop_loss_price, float):
            entities["stop_loss"] = result.stop_loss_price
        if entities.get("take_profits") is None and result.take_profit_prices:
            entities["take_profits"] = list(result.take_profit_prices)
        if entities.get("averaging") is None and isinstance(result.average_entry, float) and isinstance(result.entry_main, float):
            if result.average_entry != result.entry_main:
                entities["averaging"] = result.average_entry
        if entities.get("entry_plan_entries") is None and result.entries:
            entities["entry_plan_entries"] = list(result.entries)
        if entities.get("entry_plan_type") is None and result.entry_plan_type:
            entities["entry_plan_type"] = result.entry_plan_type
        if entities.get("entry_structure") is None and result.entry_structure:
            entities["entry_structure"] = result.entry_structure
        if entities.get("has_averaging_plan") is None:
            entities["has_averaging_plan"] = bool(result.has_averaging_plan)
        averaging = entities.get("averaging")
        if isinstance(averaging, (int, float)):
            result.average_entry = float(averaging)
        result.entities = entities
        result.raw_entities = dict(entities)
    else:
        result.intents = [intent for intent in result.intents if intent != "NS_CREATE_SIGNAL"]
    return result


def _resolve_profile_code(resolved_trader_id: str | None) -> str | None:
    return canonicalize_trader_code(resolved_trader_id)


def _profile_target_ids(target_refs: list[dict[str, object]]) -> list[int]:
    values: list[int] = []
    for item in target_refs:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        ref = item.get("ref")
        if kind in {"reply", "message_id"} and isinstance(ref, int):
            values.append(ref)
    return _merge_unique_ints([], values)


def _normalize_profile_reported_results(results: list[dict[str, object]]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol")
        value = item.get("value")
        unit = item.get("unit")
        if isinstance(symbol, str) and isinstance(value, (int, float)) and str(unit).upper() == "R":
            out.append({"symbol": symbol.upper(), "r_multiple": float(value)})
    return out


def _apply_profile_entities_to_extracted(extracted: ExtractedFields, entities: dict[str, object] | None) -> ExtractedFields:
    if not isinstance(entities, dict):
        return extracted

    symbol = extracted.symbol
    direction = extracted.direction
    entry_raw = extracted.entry_raw
    stop_raw = extracted.stop_raw
    targets = list(extracted.targets)

    raw_symbol = entities.get("symbol")
    if isinstance(raw_symbol, str) and raw_symbol.strip():
        profile_symbol = raw_symbol.strip().upper()
        if symbol is None:
            symbol = profile_symbol
        elif profile_symbol.endswith(".P") and symbol == profile_symbol[:-2]:
            symbol = profile_symbol
        elif not symbol.endswith(("USDT", "USDC", "USD", "BTC", "ETH")) and profile_symbol.endswith(("USDT", "USDC", "USD", "BTC", "ETH")):
            symbol = profile_symbol

    raw_side = entities.get("side")
    if direction is None and isinstance(raw_side, str):
        side = raw_side.strip().upper()
        if side == "LONG":
            direction = "BUY"
        elif side == "SHORT":
            direction = "SELL"

    entry = entities.get("entry")
    if entry_raw is None and isinstance(entry, list) and entry:
        first = entry[0]
        if isinstance(first, (int, float)):
            entry_raw = str(float(first))

    raw_stop = entities.get("stop_loss")
    if stop_raw is None and isinstance(raw_stop, (int, float)):
        stop_raw = str(float(raw_stop))

    raw_tps = entities.get("take_profits")
    if not targets and isinstance(raw_tps, list):
        normalized_targets = [str(float(value)) for value in raw_tps if isinstance(value, (int, float))]
        if normalized_targets:
            targets = normalized_targets

    return ExtractedFields(
        symbol=symbol,
        direction=direction,
        entry_raw=entry_raw,
        stop_raw=stop_raw,
        targets=targets,
        leverage_hint=extracted.leverage_hint,
        risk_hint=extracted.risk_hint,
        risky_flag=extracted.risky_flag,
    )


def _merge_unique_ints(left: list[int], right: list[int]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for value in [*left, *right]:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out






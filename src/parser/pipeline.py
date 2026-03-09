"""Minimal parser pipeline for raw -> parse_result."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import TYPE_CHECKING

from src.core.trader_tags import first_normalized_trader_tag, normalize_trader_aliases
from src.parser.trader_profiles.ta_profile import classify_ta_message, extract_ta_fields
from src.core.timeutils import utc_now_iso
from src.storage.parse_results import ParseResultRecord

if TYPE_CHECKING:
    from src.parser.trader_profiles.ta_profile import TAExtractedFields

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
    def __init__(self, trader_aliases: dict[str, str]) -> None:
        self._trader_aliases = normalize_trader_aliases(trader_aliases)

    def parse(self, payload: ParserInput) -> ParseResultRecord:
        text = (payload.raw_text or "").strip()
        normalized = text.lower()
        declared_tag = self._extract_declared_trader_tag(text)
        extracted = self._extract_fields(text, normalized)
        message_type = self._classify_message(
            normalized=normalized,
            has_strong_link=payload.linkage_method is not None,
            extracted=extracted,
        )
        warnings: list[str] = []
        notes_parts = [f"classified={message_type}"]

        if payload.resolved_trader_id == "TA":
            ta_fields = extract_ta_fields(text=text, normalized=normalized)
            extracted = self._merge_extracted_fields(extracted, ta_fields)
            message_type = classify_ta_message(
                normalized=normalized,
                extracted=extracted,
                has_strong_link=payload.linkage_method is not None,
                ta_fields=ta_fields,
            )
            notes_parts[0] = f"classified={message_type}"
            if ta_fields.secondary_entry_raw is not None:
                notes_parts.append(f"ta_secondary_entry={self._to_note_safe(ta_fields.secondary_entry_raw)}")
            if ta_fields.entry_cancel_rule_raw is not None:
                notes_parts.append(f"ta_entry_cancel_rule={self._to_note_safe(ta_fields.entry_cancel_rule_raw)}")
            if ta_fields.multi_symbol_update or ta_fields.multi_action_update:
                warnings.append("ta complex update preserved without multi-action split")
                notes_parts.append(f"ta_update_complex=multi_symbol:{int(ta_fields.multi_symbol_update)},update_hits:{ta_fields.update_hits}")

        has_complete_setup = (
            extracted.symbol is not None
            and extracted.direction is not None
            and extracted.entry_raw is not None
            and extracted.stop_raw is not None
            and len(extracted.targets) > 0
        )
        if message_type == "NEW_SIGNAL" and not has_complete_setup:
            message_type = "SETUP_INCOMPLETE"
            notes_parts[0] = f"classified={message_type}"

        completeness = "COMPLETE" if message_type == "NEW_SIGNAL" else "INCOMPLETE"
        parse_status = "PARSED"

        if payload.resolved_trader_id is None:
            warnings.append("unresolved trader")
        if message_type == "UPDATE" and payload.linkage_method is None:
            warnings.append("update without strong link")
        if message_type == "SETUP_INCOMPLETE":
            warnings.append("missing mandatory setup fields")

        is_executable = (
            message_type == "NEW_SIGNAL"
            and payload.resolved_trader_id is not None
            and payload.eligibility_status == "ACQUIRED_ELIGIBLE"
        )

        linkage_status = "LINKED" if payload.linkage_method else "UNLINKED"
        if message_type in ("INFO_ONLY", "UNCLASSIFIED"):
            linkage_status = "N/A"

        now = utc_now_iso()
        return ParseResultRecord(
            raw_message_id=payload.raw_message_id,
            eligibility_status=payload.eligibility_status,
            eligibility_reason=payload.eligibility_reason,
            declared_trader_tag=declared_tag,
            resolved_trader_id=payload.resolved_trader_id,
            trader_resolution_method=payload.trader_resolution_method,
            message_type=message_type,
            parse_status=parse_status,
            completeness=completeness,
            is_executable=is_executable,
            symbol=extracted.symbol,
            direction=extracted.direction,
            entry_raw=extracted.entry_raw,
            stop_raw=extracted.stop_raw,
            target_raw_list=json.dumps(extracted.targets) if extracted.targets else "[]",
            leverage_hint=extracted.leverage_hint,
            risk_hint=extracted.risk_hint,
            risky_flag=extracted.risky_flag,
            linkage_method=payload.linkage_method,
            linkage_status=linkage_status,
            warning_text="; ".join(warnings) if warnings else None,
            notes="; ".join(notes_parts),
            created_at=now,
            updated_at=now,
        )

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

    def _merge_extracted_fields(self, generic: ExtractedFields, ta: "TAExtractedFields") -> ExtractedFields:
        targets = ta.targets if ta.targets else generic.targets
        return ExtractedFields(
            symbol=ta.symbol or generic.symbol,
            direction=ta.direction or generic.direction,
            entry_raw=ta.primary_entry_raw or generic.entry_raw,
            stop_raw=ta.stop_raw or generic.stop_raw,
            targets=targets,
            leverage_hint=generic.leverage_hint,
            risk_hint=ta.risk_hint or generic.risk_hint,
            risky_flag=generic.risky_flag,
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

        if setup_fields_count >= 2:
            return "NEW_SIGNAL"

        if has_update_word and not has_update_action and not has_strong_link and setup_fields_count < 2:
            return "INFO_ONLY"
        if has_update_action:
            return "SETUP_INCOMPLETE"
        if "signal" in normalized or "entry" in normalized or "tp" in normalized or "sl" in normalized:
            return "SETUP_INCOMPLETE"
        if "admin" in normalized or "stats" in normalized:
            return "INFO_ONLY"
        return "UNCLASSIFIED"

    @staticmethod
    def _to_note_safe(value: str) -> str:
        return value.encode("unicode_escape").decode("ascii")

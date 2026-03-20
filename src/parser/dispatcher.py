"""Parser dispatcher for regex/llm/hybrid_auto modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from src.parser.llm_adapter import LLMAdapter, LLMInvalidResponse, LLMNotConfigured, LLMParseError, LLMRequestFailed
from src.parser.normalization import ParseResultNormalized


@dataclass(slots=True)
class ParserDispatchDecision:
    selected: ParseResultNormalized
    llm_attempted: bool
    fallback_from_regex: bool
    selection_reason: str


class ParserDispatcher:
    def __init__(self, llm_adapter: LLMAdapter | None = None) -> None:
        self._llm_adapter = llm_adapter or LLMAdapter()

    def dispatch_parse(
        self,
        *,
        parser_input: object,
        parser_mode: str,
        parse_with_regex: Callable[[object, str], ParseResultNormalized],
    ) -> ParserDispatchDecision:
        if parser_mode == "regex_only":
            regex_result = parse_with_regex(parser_input, parser_mode)
            return self._annotate(
                regex_result,
                llm_attempted=False,
                fallback_from_regex=False,
                selection_reason="regex_only_mode",
            )

        if parser_mode == "llm_only":
            llm_result = self._llm_adapter.parse_with_llm(parser_input, parser_mode=parser_mode)
            return self._annotate(
                llm_result,
                llm_attempted=True,
                fallback_from_regex=False,
                selection_reason="llm_only_mode",
            )

        # hybrid_auto
        regex_result = parse_with_regex(parser_input, parser_mode)
        if not should_fallback_to_llm(regex_result):
            return self._annotate(
                regex_result,
                llm_attempted=False,
                fallback_from_regex=False,
                selection_reason="hybrid_keep_regex",
            )

        try:
            llm_result = self._llm_adapter.parse_with_llm(parser_input, parser_mode=parser_mode)
        except (LLMNotConfigured, LLMParseError, LLMRequestFailed, LLMInvalidResponse):
            return self._annotate(
                regex_result,
                llm_attempted=True,
                fallback_from_regex=True,
                selection_reason="hybrid_llm_unavailable_fallback_regex",
            )

        if is_llm_result_better(regex_result=regex_result, llm_result=llm_result):
            return self._annotate(
                llm_result,
                llm_attempted=True,
                fallback_from_regex=True,
                selection_reason="hybrid_selected_llm",
            )

        return self._annotate(
            regex_result,
            llm_attempted=True,
            fallback_from_regex=False,
            selection_reason="hybrid_kept_regex_after_llm_compare",
        )

    @staticmethod
    def _annotate(
        result: ParseResultNormalized,
        *,
        llm_attempted: bool,
        fallback_from_regex: bool,
        selection_reason: str,
    ) -> ParserDispatchDecision:
        result.selection_metadata = {
            "llm_attempted": llm_attempted,
            "fallback_from_regex": fallback_from_regex,
            "selection_reason": selection_reason,
        }
        return ParserDispatchDecision(
            selected=result,
            llm_attempted=llm_attempted,
            fallback_from_regex=fallback_from_regex,
            selection_reason=selection_reason,
        )


def should_fallback_to_llm(result: ParseResultNormalized) -> bool:
    """Conservative fallback policy for hybrid_auto mode.

    Legacy checks remain authoritative; v2 semantic checks are additive so we
    do not reduce fallback safety for under-specified parses.
    """
    if result.message_type in {"UNCLASSIFIED", "SETUP_INCOMPLETE"}:
        return True
    if result.confidence < 0.6:
        return True
    if result.validation_warnings:
        return True

    if result.message_type == "NEW_SIGNAL":
        if not result.symbol or not result.direction or result.stop_loss_price is None or len(result.take_profit_prices) == 0:
            return True
        if not result.entries and result.entry_main is None:
            return True

        # v2 additive safety checks
        if isinstance(result.instrument_obj, dict) and not result.instrument_obj.get("symbol"):
            return True
        if isinstance(result.position_obj, dict):
            if not result.position_obj.get("side"):
                return True
            if not result.risk_plan.get("take_profits"):
                return True
        if isinstance(result.entry_plan, dict):
            if not result.entry_plan.get("entries") and not result.entries:
                return True

    if result.message_type == "UPDATE":
        if not result.actions and not result.message_subtype:
            return True

        # v2 additive safety checks
        if result.primary_intent is None or not str(result.primary_intent).strip():
            return True
        if isinstance(result.actions_structured, list) and not result.actions_structured and result.actions:
            return True

    if result.message_type == "INFO_ONLY":
        has_notes = any((note or "").strip() for note in result.notes)
        if not result.message_subtype and not result.reported_results and not has_notes and not result.target_refs:
            return True

    # Generic v2 consistency checks (additive only).
    if isinstance(result.instrument_obj, dict) and result.instrument_obj:
        instrument_symbol = result.instrument_obj.get("symbol")
        if instrument_symbol is not None and result.instrument is not None and str(instrument_symbol).strip().upper() != str(result.instrument).strip().upper():
            return True
    if isinstance(result.risk_plan, dict) and result.risk_plan:
        risk_conf = result.risk_plan.get("confidence")
        if isinstance(risk_conf, (int, float)) and risk_conf < 0.6:
            return True

    return False


def is_llm_result_better(*, regex_result: ParseResultNormalized, llm_result: ParseResultNormalized) -> bool:
    if len(llm_result.validation_warnings) < len(regex_result.validation_warnings):
        return True
    if llm_result.confidence > regex_result.confidence + 0.05:
        return True
    if regex_result.message_type in {"UNCLASSIFIED", "SETUP_INCOMPLETE"} and llm_result.message_type not in {"UNCLASSIFIED", "SETUP_INCOMPLETE"}:
        return True
    return False

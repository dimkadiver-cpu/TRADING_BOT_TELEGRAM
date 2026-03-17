"""Trader D profile parser."""

from __future__ import annotations

from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.trader_b.profile import TraderBProfileParser


class TraderDProfileParser(TraderBProfileParser):
    """Trader D parser built on top of Trader B deterministic rules.

    The parser keeps backwards compatibility with the legacy profile output and
    additionally fills the v2 semantic envelope fields.
    """

    trader_code = "trader_d"

    def parse_message(self, text: str, context: ParserContext) -> TraderParseResult:
        base_result = super().parse_message(text=text, context=context)
        primary_intent = self._derive_primary_intent(
            message_type=base_result.message_type,
            intents=base_result.intents,
        )
        actions_structured = self._build_actions_structured(
            message_type=base_result.message_type,
            intents=base_result.intents,
            entities=base_result.entities,
        )
        linking = self._build_linking(target_refs=base_result.target_refs, context=context)
        target_scope = {"kind": "signal", "scope": "single" if linking["targeted"] else "unknown"}

        return TraderParseResult(
            message_type=base_result.message_type,
            intents=base_result.intents,
            entities=base_result.entities,
            target_refs=base_result.target_refs,
            reported_results=base_result.reported_results,
            warnings=base_result.warnings,
            confidence=base_result.confidence,
            primary_intent=primary_intent,
            actions_structured=actions_structured,
            target_scope=target_scope,
            linking=linking,
            diagnostics={
                "parser_version": "trader_d_v2_compatible",
                "warning_count": len(base_result.warnings),
            },
        )

    @staticmethod
    def _derive_primary_intent(*, message_type: str, intents: list[str]) -> str | None:
        if message_type == "NEW_SIGNAL":
            return "NS_CREATE_SIGNAL"
        if message_type == "SETUP_INCOMPLETE":
            return "NS_CREATE_SIGNAL"
        for intent in intents:
            if intent.startswith("U_"):
                return intent
        return None

    @staticmethod
    def _build_actions_structured(*, message_type: str, intents: list[str], entities: dict) -> list[dict]:
        if message_type == "NEW_SIGNAL":
            return [
                {
                    "action": "CREATE_SIGNAL",
                    "instrument": entities.get("symbol"),
                    "side": entities.get("side"),
                    "entries": entities.get("entry", []),
                    "stop_loss": entities.get("stop_loss"),
                    "take_profits": entities.get("take_profits", []),
                }
            ]

        actions: list[dict] = []
        for intent in intents:
            if intent == "U_MOVE_STOP_TO_BE":
                actions.append({"action": "MOVE_STOP", "new_stop_level": "ENTRY"})
            elif intent == "U_MOVE_STOP":
                actions.append({"action": "MOVE_STOP", "new_stop_level": entities.get("new_stop_level")})
            elif intent == "U_CLOSE_FULL":
                actions.append({"action": "CLOSE_POSITION", "scope": "FULL"})
            elif intent == "U_CANCEL_PENDING_ORDERS":
                actions.append({"action": "CANCEL_PENDING", "scope": "ALL_PENDING_ENTRIES"})
            elif intent == "U_TP_HIT":
                actions.append({"action": "TAKE_PROFIT", "target": "TP"})
            elif intent == "U_STOP_HIT":
                actions.append({"action": "CLOSE_POSITION", "target": "STOP"})
        return actions

    @staticmethod
    def _build_linking(*, target_refs: list[dict], context: ParserContext) -> dict:
        return {
            "targeted": bool(target_refs or context.reply_to_message_id),
            "reply_to_message_id": context.reply_to_message_id,
            "target_refs_count": len(target_refs),
            "strategy": "reply_or_link" if (target_refs or context.reply_to_message_id) else "unresolved",
        }

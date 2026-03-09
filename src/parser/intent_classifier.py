
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import ParseIntent


@dataclass(slots=True)
class IntentHints:
    has_strong_link: bool = False
    should_run_full_extractors: bool = False
    should_run_claim_extractors: bool = False
    prefer_linking_extractors: bool = False


@dataclass(slots=True)
class IntentClassification:
    intent: ParseIntent
    confidence: float
    reasons: list[str] = field(default_factory=list)
    hints: IntentHints = field(default_factory=IntentHints)
    debug: dict[str, Any] = field(default_factory=dict)


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(n in text for n in needles)


def _count_contains(text: str, needles: list[str]) -> int:
    return sum(1 for n in needles if n in text)


def classify_intent(
    raw_text: str,
    normalized_text: str,
    parsing_rules: dict[str, Any],
    *,
    reply_to_msg_id: str | None = None,
    extracted_links: list[str] | None = None,
) -> IntentClassification:
    """
    MVP deterministic intent classifier.

    Uses gates + intent_hints from parsing_rules.
    """
    extracted_links = extracted_links or []

    gates = parsing_rules.get("gates", {})
    hints_cfg = parsing_rules.get("intent_hints", {})

    ignore_if_contains = gates.get("ignore_if_contains", [])
    require_any = gates.get("require_any", [])

    # Hard ignore => NOTE wins immediately
    for token in ignore_if_contains:
        if token and token in normalized_text:
            return IntentClassification(
                intent="NOTE",
                confidence=0.99,
                reasons=[f"hard ignore matched: {token}"],
                hints=IntentHints(
                    has_strong_link=False,
                    should_run_full_extractors=False,
                    should_run_claim_extractors=False,
                    prefer_linking_extractors=False,
                ),
                debug={"hard_ignore": token},
            )

    has_reply = reply_to_msg_id is not None
    has_link = len(extracted_links) > 0

    # Simple lexical features
    feature_signal_id = "SIGNAL ID" in normalized_text
    feature_entry = "ENTRY" in normalized_text
    feature_sl = "STOP LOSS" in normalized_text or "SL" in normalized_text
    feature_tp = "TARGET" in normalized_text or "TP" in normalized_text
    feature_side = "LONG" in normalized_text or "SHORT" in normalized_text
    feature_profit = "PROFIT" in normalized_text or "Profit" in normalized_text
    feature_loss = "LOSS" in normalized_text or "Loss" in normalized_text
    feature_target_hit = "Target 1" in normalized_text or "Target 2" in normalized_text or "TP1" in normalized_text
    feature_move = "MOVE SL" in normalized_text or "Move SL" in normalized_text or "CLOSE" in normalized_text or "Close" in normalized_text

    strong_link = has_reply or has_link or feature_signal_id

    score_signal_new = 0.0
    score_signal_update = 0.0
    score_unlinked_update = 0.0
    score_note = 0.0

    reasons_new: list[str] = []
    reasons_update: list[str] = []
    reasons_unlinked: list[str] = []
    reasons_note: list[str] = []

    # SIGNAL_NEW scoring
    if feature_signal_id:
        score_signal_new += 0.35
        reasons_new.append("contains SIGNAL ID")
    if feature_entry:
        score_signal_new += 0.20
        reasons_new.append("contains ENTRY")
    if feature_sl:
        score_signal_new += 0.20
        reasons_new.append("contains SL")
    if feature_tp:
        score_signal_new += 0.20
        reasons_new.append("contains TP/TARGETS")
    if feature_side:
        score_signal_new += 0.10
        reasons_new.append("contains LONG/SHORT")
    if feature_profit or feature_loss:
        score_signal_new -= 0.25
        reasons_new.append("contains Profit/Loss penalty")

    # SIGNAL_UPDATE scoring
    if strong_link:
        score_signal_update += 0.40
        reasons_update.append("has strong link")
    if feature_profit or feature_loss:
        score_signal_update += 0.25
        reasons_update.append("contains Profit/Loss")
    if feature_target_hit:
        score_signal_update += 0.20
        reasons_update.append("contains TP hit language")
    if feature_move:
        score_signal_update += 0.20
        reasons_update.append("contains move/close language")
    if feature_entry and feature_sl and feature_tp:
        score_signal_update -= 0.20
        reasons_update.append("full signal structure penalty")

    # UNLINKED_UPDATE scoring
    if (feature_profit or feature_loss or feature_target_hit or feature_move):
        score_unlinked_update += 0.35
        reasons_unlinked.append("contains update language")
    if not strong_link:
        score_unlinked_update += 0.20
        reasons_unlinked.append("no strong link")
    if not (feature_entry and feature_sl and feature_tp):
        score_unlinked_update += 0.20
        reasons_unlinked.append("no full signal structure")

    # NOTE scoring
    if not _contains_any(normalized_text, require_any):
        score_note += 0.40
        reasons_note.append("missing required signal gates")
    if not (feature_entry or feature_sl or feature_tp or feature_profit or feature_loss or feature_target_hit):
        score_note += 0.30
        reasons_note.append("informational/no signal fields")
    if len(normalized_text.strip()) < 40:
        score_note += 0.10
        reasons_note.append("short message")

    scores = {
        "SIGNAL_NEW": max(0.0, min(1.0, score_signal_new)),
        "SIGNAL_UPDATE": max(0.0, min(1.0, score_signal_update)),
        "UNLINKED_UPDATE": max(0.0, min(1.0, score_unlinked_update)),
        "NOTE": max(0.0, min(1.0, score_note)),
    }

    # Tie-break rules
    # 1) NOTE wins if ignore gate matched (handled above)
    # 2) SIGNAL_UPDATE beats UNLINKED_UPDATE if strong link
    # 3) SIGNAL_NEW beats others if full signal structure
    full_signal_structure = feature_signal_id and feature_entry and feature_sl and feature_tp

    if full_signal_structure:
        winning_intent: ParseIntent = "SIGNAL_NEW"
    else:
        winning_intent = max(scores, key=scores.get)  # type: ignore[assignment]
        if strong_link and scores["SIGNAL_UPDATE"] >= scores["UNLINKED_UPDATE"]:
            winning_intent = "SIGNAL_UPDATE"

    winning_confidence = scores[winning_intent]

    reasons_map = {
        "SIGNAL_NEW": reasons_new,
        "SIGNAL_UPDATE": reasons_update,
        "UNLINKED_UPDATE": reasons_unlinked,
        "NOTE": reasons_note,
    }

    hints = IntentHints(
        has_strong_link=strong_link,
        should_run_full_extractors=winning_intent == "SIGNAL_NEW",
        should_run_claim_extractors=winning_intent in ("SIGNAL_UPDATE", "UNLINKED_UPDATE"),
        prefer_linking_extractors=strong_link,
    )

    # fallback to ERROR_PARSE only if classifier is too uncertain
    if winning_confidence < 0.25 and not scores["NOTE"] > 0.30:
        return IntentClassification(
            intent="ERROR_PARSE",
            confidence=winning_confidence,
            reasons=["no intent reached minimum confidence"],
            hints=hints,
            debug={"scores": scores},
        )

    return IntentClassification(
        intent=winning_intent,
        confidence=winning_confidence,
        reasons=reasons_map.get(winning_intent, []),
        hints=hints,
        debug={"scores": scores},
    )
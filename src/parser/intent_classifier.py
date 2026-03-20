"""Intent classifier for the parser pipeline.

Scoring model
-------------
Each message is scored against four intent categories using weighted lexical
features extracted from the normalized text plus structural signals (reply-to,
extracted links, signal-ID header).

Weights and thresholds are defined as module-level constants so they can be
tuned without hunting through the scoring logic.

To add a new intent:
  1. Add ``score_<intent>`` and ``reasons_<intent>`` in ``classify_intent()``.
  2. Add its weights as ``_W_<INTENT>_*`` constants in this module.
  3. Add it to the ``scores`` dict and ``reasons_map``.
  4. Update ``_resolve_tie()`` if the new intent needs special tie-break handling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import ParseIntent

# ---------------------------------------------------------------------------
# Scoring weights — SIGNAL_NEW
# ---------------------------------------------------------------------------
_W_NS_SIGNAL_ID: float = 0.35            # "SIGNAL ID" header present
_W_NS_ENTRY: float = 0.20               # entry price present
_W_NS_SL: float = 0.20                  # stop-loss present
_W_NS_TP: float = 0.20                  # take-profit / target present
_W_NS_SIDE: float = 0.10                # LONG / SHORT direction present
_W_NS_PROFIT_LOSS_PENALTY: float = -0.25 # result language → likely not a new setup

# ---------------------------------------------------------------------------
# Scoring weights — SIGNAL_UPDATE
# ---------------------------------------------------------------------------
_W_UPD_STRONG_LINK: float = 0.40        # reply-to or extracted link present
_W_UPD_PROFIT_LOSS: float = 0.25        # result / P&L language
_W_UPD_TARGET_HIT: float = 0.20         # TP-hit language
_W_UPD_MOVE_CLOSE: float = 0.20         # move-stop / close language
_W_UPD_FULL_SETUP_PENALTY: float = -0.20 # full signal structure → likely not an update

# ---------------------------------------------------------------------------
# Scoring weights — UNLINKED_UPDATE
# ---------------------------------------------------------------------------
_W_UNLINKED_ACTION_LANG: float = 0.35   # update-like language but no strong link
_W_UNLINKED_NO_LINK: float = 0.20       # absence of strong link is positive signal
_W_UNLINKED_NO_FULL_SETUP: float = 0.20 # no complete signal structure

# ---------------------------------------------------------------------------
# Scoring weights — NOTE
# ---------------------------------------------------------------------------
_W_NOTE_GATE_MISS: float = 0.40         # required signal gates not matched
_W_NOTE_NO_SIGNAL_FIELDS: float = 0.30  # no tradeable fields detected
_W_NOTE_SHORT_TEXT: float = 0.10        # short message text

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
_MIN_TEXT_LEN_FOR_SIGNAL: int = 40      # texts shorter than this lean toward NOTE
_CONFIDENCE_FLOOR: float = 0.0
_CONFIDENCE_CEILING: float = 1.0
# Classifier falls back to ERROR_PARSE when winning score is below this AND
# NOTE score does not exceed the override threshold (NOTE at that level is a
# valid low-confidence outcome, not a parse failure).
_FALLBACK_CONFIDENCE_THRESHOLD: float = 0.25
_NOTE_OVERRIDE_THRESHOLD: float = 0.30


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


def _clip(value: float) -> float:
    return max(_CONFIDENCE_FLOOR, min(_CONFIDENCE_CEILING, value))


def _resolve_tie(
    scores: dict[str, float],
    *,
    full_signal_structure: bool,
    strong_link: bool,
) -> ParseIntent:
    """Return the winning intent from the score dict.

    Priority rules (first match wins):
    1. Full signal structure (SIGNAL_ID + ENTRY + SL + TP) → SIGNAL_NEW.
    2. Strong link and SIGNAL_UPDATE >= UNLINKED_UPDATE → SIGNAL_UPDATE.
    3. Highest raw score wins.
    """
    if full_signal_structure:
        return "SIGNAL_NEW"
    if strong_link and scores["SIGNAL_UPDATE"] >= scores["UNLINKED_UPDATE"]:
        return "SIGNAL_UPDATE"
    return max(scores, key=scores.get)  # type: ignore[return-value]


def classify_intent(
    raw_text: str,
    normalized_text: str,
    parsing_rules: dict[str, Any],
    *,
    reply_to_msg_id: str | None = None,
    extracted_links: list[str] | None = None,
) -> IntentClassification:
    """Deterministic intent classifier based on weighted lexical features.

    Uses ``gates`` and optional ``intent_hints`` from *parsing_rules*.
    Returns an :class:`IntentClassification` with intent, confidence, reasons,
    downstream hints, and a debug score breakdown.
    """
    extracted_links = extracted_links or []

    gates = parsing_rules.get("gates", {})
    ignore_if_contains = gates.get("ignore_if_contains", [])
    require_any = gates.get("require_any", [])

    # Hard-ignore gate: any matching token → classify immediately as NOTE.
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

    # -----------------------------------------------------------------------
    # Lexical feature extraction
    # -----------------------------------------------------------------------
    feature_signal_id = "SIGNAL ID" in normalized_text
    feature_entry = "ENTRY" in normalized_text
    feature_sl = "STOP LOSS" in normalized_text or "SL" in normalized_text
    feature_tp = "TARGET" in normalized_text or "TP" in normalized_text
    feature_side = "LONG" in normalized_text or "SHORT" in normalized_text
    feature_profit = "PROFIT" in normalized_text or "Profit" in normalized_text
    feature_loss = "LOSS" in normalized_text or "Loss" in normalized_text
    feature_target_hit = (
        "Target 1" in normalized_text
        or "Target 2" in normalized_text
        or "TP1" in normalized_text
    )
    feature_move = (
        "MOVE SL" in normalized_text
        or "Move SL" in normalized_text
        or "CLOSE" in normalized_text
        or "Close" in normalized_text
    )

    # A "strong link" is any structural pointer to an existing signal.
    strong_link = has_reply or has_link or feature_signal_id

    # -----------------------------------------------------------------------
    # SIGNAL_NEW scoring
    # -----------------------------------------------------------------------
    score_signal_new = 0.0
    reasons_new: list[str] = []

    if feature_signal_id:
        score_signal_new += _W_NS_SIGNAL_ID
        reasons_new.append("contains SIGNAL ID")
    if feature_entry:
        score_signal_new += _W_NS_ENTRY
        reasons_new.append("contains ENTRY")
    if feature_sl:
        score_signal_new += _W_NS_SL
        reasons_new.append("contains SL")
    if feature_tp:
        score_signal_new += _W_NS_TP
        reasons_new.append("contains TP/TARGETS")
    if feature_side:
        score_signal_new += _W_NS_SIDE
        reasons_new.append("contains LONG/SHORT")
    if feature_profit or feature_loss:
        score_signal_new += _W_NS_PROFIT_LOSS_PENALTY
        reasons_new.append("contains Profit/Loss penalty")

    # -----------------------------------------------------------------------
    # SIGNAL_UPDATE scoring
    # -----------------------------------------------------------------------
    score_signal_update = 0.0
    reasons_update: list[str] = []

    if strong_link:
        score_signal_update += _W_UPD_STRONG_LINK
        reasons_update.append("has strong link")
    if feature_profit or feature_loss:
        score_signal_update += _W_UPD_PROFIT_LOSS
        reasons_update.append("contains Profit/Loss")
    if feature_target_hit:
        score_signal_update += _W_UPD_TARGET_HIT
        reasons_update.append("contains TP hit language")
    if feature_move:
        score_signal_update += _W_UPD_MOVE_CLOSE
        reasons_update.append("contains move/close language")
    if feature_entry and feature_sl and feature_tp:
        score_signal_update += _W_UPD_FULL_SETUP_PENALTY
        reasons_update.append("full signal structure penalty")

    # -----------------------------------------------------------------------
    # UNLINKED_UPDATE scoring
    # -----------------------------------------------------------------------
    score_unlinked_update = 0.0
    reasons_unlinked: list[str] = []

    if feature_profit or feature_loss or feature_target_hit or feature_move:
        score_unlinked_update += _W_UNLINKED_ACTION_LANG
        reasons_unlinked.append("contains update language")
    if not strong_link:
        score_unlinked_update += _W_UNLINKED_NO_LINK
        reasons_unlinked.append("no strong link")
    if not (feature_entry and feature_sl and feature_tp):
        score_unlinked_update += _W_UNLINKED_NO_FULL_SETUP
        reasons_unlinked.append("no full signal structure")

    # -----------------------------------------------------------------------
    # NOTE scoring
    # -----------------------------------------------------------------------
    score_note = 0.0
    reasons_note: list[str] = []

    if not _contains_any(normalized_text, require_any):
        score_note += _W_NOTE_GATE_MISS
        reasons_note.append("missing required signal gates")
    if not (feature_entry or feature_sl or feature_tp or feature_profit or feature_loss or feature_target_hit):
        score_note += _W_NOTE_NO_SIGNAL_FIELDS
        reasons_note.append("informational/no signal fields")
    if len(normalized_text.strip()) < _MIN_TEXT_LEN_FOR_SIGNAL:
        score_note += _W_NOTE_SHORT_TEXT
        reasons_note.append("short message")

    # -----------------------------------------------------------------------
    # Resolve winner
    # -----------------------------------------------------------------------
    scores = {
        "SIGNAL_NEW": _clip(score_signal_new),
        "SIGNAL_UPDATE": _clip(score_signal_update),
        "UNLINKED_UPDATE": _clip(score_unlinked_update),
        "NOTE": _clip(score_note),
    }

    full_signal_structure = feature_signal_id and feature_entry and feature_sl and feature_tp
    winning_intent: ParseIntent = _resolve_tie(
        scores,
        full_signal_structure=full_signal_structure,
        strong_link=strong_link,
    )
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

    # Fallback to ERROR_PARSE only when the classifier is genuinely uncertain.
    # Exception: NOTE above _NOTE_OVERRIDE_THRESHOLD is a valid outcome, not an
    # error — low-confidence informational messages should stay as NOTE.
    if winning_confidence < _FALLBACK_CONFIDENCE_THRESHOLD and scores["NOTE"] <= _NOTE_OVERRIDE_THRESHOLD:
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

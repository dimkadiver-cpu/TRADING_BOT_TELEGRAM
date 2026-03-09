
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Any

from .models import Candidate, CandidateSet, ParseWarning


@dataclass(slots=True)
class ScoreBreakdown:
    local_mean: float
    coherence_score: float
    final_confidence: float
    warnings: list[ParseWarning] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WinningCombination:
    signal_id: Candidate | None = None
    symbol: Candidate | None = None
    side: Candidate | None = None
    entry: Candidate | None = None
    sl: Candidate | None = None
    tp_candidates: list[Candidate] = field(default_factory=list)
    link_ref: Candidate | None = None

    def winner_patterns(self) -> dict[str, str]:
        return {
            "signal_id": self.signal_id.pattern_id if self.signal_id else "",
            "symbol": self.symbol.pattern_id if self.symbol else "",
            "side": self.side.pattern_id if self.side else "",
            "entry": self.entry.pattern_id if self.entry else "",
            "sl": self.sl.pattern_id if self.sl else "",
            "tp": self.tp_candidates[0].pattern_id if self.tp_candidates else "",
            "link_ref": self.link_ref.pattern_id if self.link_ref else "",
        }


def top_k(candidates: list[Candidate], k: int) -> list[Candidate]:
    return sorted(candidates, key=lambda c: c.score, reverse=True)[:k]


def _entry_reference(entry_value: dict[str, Any] | None) -> float | None:
    if not entry_value:
        return None
    etype = entry_value.get("type")
    if etype == "SINGLE":
        return entry_value.get("price")
    if etype == "RANGE":
        return entry_value.get("avg") or entry_value.get("max")
    if etype == "LEVELS":
        return entry_value.get("avg")
    return None


def _coherence_for_entry(entry: Candidate | None) -> tuple[float, list[ParseWarning]]:
    warnings: list[ParseWarning] = []
    if not entry:
        return 0.0, [ParseWarning("MISSING_ENTRY", "No entry candidate selected.")]

    value = entry.value
    etype = value.get("type")
    if etype == "SINGLE":
        return 1.0, warnings
    if etype == "RANGE":
        mn = value.get("min")
        mx = value.get("max")
        if mn is None or mx is None:
            return 0.2, [ParseWarning("INVALID_ENTRY_RANGE", "Range missing min/max.")]
        if mn < mx:
            return 1.0, warnings
        return 0.1, [ParseWarning("INVALID_ENTRY_RANGE", "entry.min >= entry.max")]
    if etype == "LEVELS":
        levels = value.get("levels") or []
        if len(levels) >= 1:
            return 0.9, warnings
        return 0.2, [ParseWarning("INVALID_ENTRY_LEVELS", "No entry levels found.")]
    return 0.1, [ParseWarning("UNKNOWN_ENTRY_TYPE", f"Unknown entry type: {etype}")]


def _coherence_for_side_sl_tp(
    side: Candidate | None,
    entry: Candidate | None,
    sl: Candidate | None,
    tp_candidates: list[Candidate],
) -> tuple[float, list[ParseWarning], dict[str, Any]]:
    warnings: list[ParseWarning] = []
    debug: dict[str, Any] = {}

    if not side or not entry:
        return 0.0, [ParseWarning("MISSING_SIDE_OR_ENTRY", "Cannot evaluate side coherence.")], debug

    side_value = side.value.get("side")
    entry_ref = _entry_reference(entry.value)
    if entry_ref is None:
        return 0.0, [ParseWarning("MISSING_ENTRY_REFERENCE", "Cannot compute entry reference.")], debug

    score = 1.0

    # SL coherence
    sl_score = 1.0
    if sl:
        sl_price = sl.value.get("price")
        if sl_price is None:
            sl_score = 0.2
            warnings.append(ParseWarning("INVALID_SL", "SL candidate has no price."))
        else:
            if side_value == "LONG" and not (sl_price < entry_ref):
                sl_score = 0.1
                warnings.append(ParseWarning("INCOHERENT_SL", "LONG requires SL below entry."))
            elif side_value == "SHORT" and not (sl_price > entry_ref):
                sl_score = 0.1
                warnings.append(ParseWarning("INCOHERENT_SL", "SHORT requires SL above entry."))
    else:
        sl_score = 0.0
        warnings.append(ParseWarning("MISSING_SL", "No SL candidate selected."))

    # TP coherence
    tp_score = 1.0
    tp_prices = []
    for tp in tp_candidates:
        price = tp.value.get("price")
        if price is not None:
            tp_prices.append(price)

    if not tp_prices:
        tp_score = 0.0
        warnings.append(ParseWarning("MISSING_TP", "No TP candidates selected."))
    else:
        if side_value == "LONG":
            if not all(p > entry_ref for p in tp_prices):
                tp_score -= 0.5
                warnings.append(ParseWarning("INCOHERENT_TP", "LONG requires TP above entry."))
            if tp_prices != sorted(tp_prices):
                tp_score -= 0.3
                warnings.append(ParseWarning("TP_NOT_SORTED", "LONG TP should be ascending."))
        elif side_value == "SHORT":
            if not all(p < entry_ref for p in tp_prices):
                tp_score -= 0.5
                warnings.append(ParseWarning("INCOHERENT_TP", "SHORT requires TP below entry."))
            if tp_prices != sorted(tp_prices, reverse=True):
                tp_score -= 0.3
                warnings.append(ParseWarning("TP_NOT_SORTED", "SHORT TP should be descending."))

        # duplicates
        if len(tp_prices) != len(set(tp_prices)):
            tp_score -= 0.2
            warnings.append(ParseWarning("TP_DUPLICATES", "Duplicate TP values detected."))

    tp_score = max(0.0, tp_score)

    score = (sl_score + tp_score) / 2.0
    debug["sl_score"] = sl_score
    debug["tp_score"] = tp_score
    debug["tp_prices"] = tp_prices
    return score, warnings, debug


def _local_mean_of_combination(combo: WinningCombination) -> float:
    scores = []
    for cand in [combo.signal_id, combo.symbol, combo.side, combo.entry, combo.sl, combo.link_ref]:
        if cand:
            scores.append(cand.score)
    for tp in combo.tp_candidates:
        scores.append(tp.score)
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def _candidate_counts(candidate_set: CandidateSet) -> dict[str, int]:
    return {k: len(v) for k, v in candidate_set.as_dict().items()}


def _choose_tp_candidates(tp_candidates: list[Candidate]) -> list[Candidate]:
    """
    MVP strategy:
    - keep all TP candidates
    - sort by explicit tp_index if present, else by price
    """
    def sort_key(c: Candidate):
        idx = c.value.get("tp_index")
        price = c.value.get("price")
        return (999 if idx is None else idx, 0 if price is None else price)

    return sorted(tp_candidates, key=sort_key)


def build_combinations(candidate_set: CandidateSet) -> list[WinningCombination]:
    signal_ids = top_k(candidate_set.signal_id, 1) or [None]
    symbols = top_k(candidate_set.symbol, 2) or [None]
    sides = top_k(candidate_set.side, 2) or [None]
    entries = top_k(candidate_set.entry, 2) or [None]
    sls = top_k(candidate_set.sl, 2) or [None]
    links = top_k(candidate_set.link_ref, 1) or [None]
    tps = _choose_tp_candidates(candidate_set.tp)

    combos: list[WinningCombination] = []
    for signal_id, symbol, side, entry, sl, link_ref in product(
        signal_ids, symbols, sides, entries, sls, links
    ):
        combos.append(
            WinningCombination(
                signal_id=signal_id,
                symbol=symbol,
                side=side,
                entry=entry,
                sl=sl,
                tp_candidates=tps,
                link_ref=link_ref,
            )
        )
    return combos


def score_combination(combo: WinningCombination, candidate_set: CandidateSet) -> ScoreBreakdown:
    warnings: list[ParseWarning] = []
    debug: dict[str, Any] = {}

    local_mean = _local_mean_of_combination(combo)

    entry_score, entry_warnings = _coherence_for_entry(combo.entry)
    warnings.extend(entry_warnings)

    side_score, side_warnings, side_debug = _coherence_for_side_sl_tp(
        combo.side, combo.entry, combo.sl, combo.tp_candidates
    )
    warnings.extend(side_warnings)

    # overlap penalty placeholder for MVP
    overlap_penalty = 0.0

    coherence_score = max(0.0, min(1.0, (entry_score + side_score) / 2.0 + overlap_penalty))
    final_confidence = 0.6 * local_mean + 0.4 * coherence_score

    if final_confidence < 0.80:
        warnings.append(ParseWarning("LOW_CONFIDENCE_PARSE", f"confidence={final_confidence:.3f}"))

    debug["winner_patterns"] = combo.winner_patterns()
    debug["candidate_counts"] = _candidate_counts(candidate_set)
    debug["coherence_breakdown"] = {
        "entry": entry_score,
        "side_sl_tp": side_score,
        "overlap_penalty": overlap_penalty,
    }
    debug["side_details"] = side_debug

    return ScoreBreakdown(
        local_mean=local_mean,
        coherence_score=coherence_score,
        final_confidence=final_confidence,
        warnings=warnings,
        debug=debug,
    )


def choose_best_combination(candidate_set: CandidateSet) -> tuple[WinningCombination | None, ScoreBreakdown | None]:
    combos = build_combinations(candidate_set)
    if not combos:
        return None, None

    best_combo: WinningCombination | None = None
    best_score: ScoreBreakdown | None = None

    for combo in combos:
        score = score_combination(combo, candidate_set)

        if best_score is None:
            best_combo, best_score = combo, score
            continue

        # tie-break: higher confidence, then fewer warnings
        if score.final_confidence > best_score.final_confidence:
            best_combo, best_score = combo, score
        elif score.final_confidence == best_score.final_confidence:
            if len(score.warnings) < len(best_score.warnings):
                best_combo, best_score = combo, score

    return best_combo, best_score
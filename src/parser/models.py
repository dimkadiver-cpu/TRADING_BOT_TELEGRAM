"""Core parser data models.

These models are intentionally small and stable.
The parser layer should exchange structured objects, not raw Telegram text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from src.parser.normalization import ParseResultNormalized

ParseIntent = Literal["SIGNAL_NEW", "SIGNAL_UPDATE", "UNLINKED_UPDATE", "NOTE"]


@dataclass(slots=True)
class ParseWarning:
    code: str
    message: str


@dataclass(slots=True)
class Candidate:
    field_name: str
    value: dict[str, Any]
    score: float
    source_text: str = ""
    span: tuple[int, int] | None = None
    pattern_id: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CandidateSet:
    signal_id: list[Candidate] = field(default_factory=list)
    symbol: list[Candidate] = field(default_factory=list)
    side: list[Candidate] = field(default_factory=list)
    entry: list[Candidate] = field(default_factory=list)
    sl: list[Candidate] = field(default_factory=list)
    tp: list[Candidate] = field(default_factory=list)
    claim_profit: list[Candidate] = field(default_factory=list)
    claim_loss: list[Candidate] = field(default_factory=list)
    claim_tp_hit: list[Candidate] = field(default_factory=list)
    link_ref: list[Candidate] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[Candidate]]:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "side": self.side,
            "entry": self.entry,
            "sl": self.sl,
            "tp": self.tp,
            "claim_profit": self.claim_profit,
            "claim_loss": self.claim_loss,
            "claim_tp_hit": self.claim_tp_hit,
            "link_ref": self.link_ref,
        }


@dataclass(slots=True)
class ParseResult:
    intent: ParseIntent
    trader_id: str | None = None
    signal: dict[str, Any] = field(default_factory=dict)
    claims: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    warnings: list[ParseWarning] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

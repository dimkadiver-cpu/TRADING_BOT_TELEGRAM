from __future__ import annotations

import re

from src.parser_v2.contracts.entities import EntryLeg, Price, StopLoss, TakeProfit
from src.parser_v2.contracts.markers import NormalizedText
from src.parser_v2.contracts.parsed_message import SignalDraft
from src.parser_v2.core.symbol_normalizer import normalize_symbol

# ── anchor detection ──────────────────────────────────────────────────────────
_DEVOS_HEADER_RE = re.compile(r"devos\s+crypto\s+signals", re.IGNORECASE)
_FLOW_DESK_RE = re.compile(r"flow\s+desk\s*[··]?\s*signal\s+relay", re.IGNORECASE)

# ── symbol ─────────────────────────────────────────────────────────────────────
# Format: bare "ENAUSDT" on its own line (no "#" prefix, no "/" separator)
_SYMBOL_LINE_RE = re.compile(
    r"^(?P<symbol>[A-Z0-9]{2,20}USDT[CP]?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# ── direction ─────────────────────────────────────────────────────────────────
_DIRECTION_RE = re.compile(r"direction\s*:\s*(?P<side>long|short)\b", re.IGNORECASE)

# ── leverage ──────────────────────────────────────────────────────────────────
_LEVERAGE_RE = re.compile(r"leverage\s*:\s*(?:cross|isolated)?\s*(?P<value>\d+(?:\.\d+)?)x", re.IGNORECASE)

# ── Format A: numbered entry targets ─────────────────────────────────────────
# "1) 0.09182"  or  "1) 0.09182\n"
_ENTRY_TARGETS_HEADER_RE = re.compile(r"entry\s+targets\s*:", re.IGNORECASE)
_NUMBERED_ENTRY_RE = re.compile(r"^\s*\d+\)\s*(?P<price>\d[\d,]*(?:\.\d+)?)", re.MULTILINE)

# ── Format B: range entry  ────────────────────────────────────────────────────
# "★ Entry: 0.3558 — 0.3586 ★"
_RANGE_ENTRY_RE = re.compile(
    r"entry\s*:\s*(?P<low>\d[\d,]*(?:\.\d+)?)\s*[—–\-]+\s*(?P<high>\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)

# ── stop loss ─────────────────────────────────────────────────────────────────
# Format A: ⚡⚡Stop Loss: 0.086737⚡⚡
# Format B: 🔥Stop Loss: 0.329912🔥
_STOP_LOSS_RE = re.compile(
    r"stop\s+loss\s*:\s*(?P<value>\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)

# ── take profits ──────────────────────────────────────────────────────────────
# "Target 1 - 0.094751"
_TP_LINE_RE = re.compile(
    r"target\s+(?P<seq>\d+)\s*[-–—]\s*(?P<price>\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)

_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


class SignalExtractor:
    def extract(self, normalized: NormalizedText) -> SignalDraft | None:
        text = normalized.raw_text

        if not _looks_like_signal(text):
            return None

        symbol = _extract_symbol(text)
        side = _extract_side(text)
        leverage = _extract_leverage(text)
        entries = _extract_entries(text)
        stop_loss = _extract_stop_loss(text)
        take_profits = _extract_take_profits(text)

        missing = _missing_fields(
            symbol=symbol, side=side, entries=entries,
            stop_loss=stop_loss, take_profits=take_profits,
        )

        return SignalDraft(
            symbol=symbol,
            side=side,
            entry_structure=_entry_structure(entries),
            entries=entries,
            stop_loss=stop_loss,
            take_profits=take_profits,
            risk_hint=None,
            leverage_hint=leverage,
            missing_fields=missing,
            completeness="COMPLETE" if not missing else "INCOMPLETE",
        )


def _looks_like_signal(text: str) -> bool:
    return bool(_DEVOS_HEADER_RE.search(text) or _FLOW_DESK_RE.search(text))


def _extract_symbol(text: str) -> str | None:
    for m in _SYMBOL_LINE_RE.finditer(text):
        raw = m.group("symbol").upper()
        # Skip lines that are clearly header/footer text
        if any(skip in raw.upper() for skip in ("FLOW", "DESK", "SIGNAL", "RELAY", "DEVOS")):
            continue
        return normalize_symbol(raw)
    return None


def _extract_side(text: str) -> str | None:
    m = _DIRECTION_RE.search(text)
    return m.group("side").upper() if m else None


def _extract_leverage(text: str) -> float | None:
    m = _LEVERAGE_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group("value"))
    except ValueError:
        return None


def _extract_entries(text: str) -> list[EntryLeg]:
    # Format A: numbered entries after "Entry Targets:"
    if _ENTRY_TARGETS_HEADER_RE.search(text):
        matches = list(_NUMBERED_ENTRY_RE.finditer(text))
        if matches:
            entries: list[EntryLeg] = []
            for idx, m in enumerate(matches, start=1):
                price = _price(m.group("price"))
                if price:
                    role: str = "PRIMARY" if idx == 1 else "AVERAGING"
                    entries.append(
                        EntryLeg(sequence=idx, entry_type="LIMIT", price=price, role=role, is_optional=False)
                    )
            return entries

    # Format B: range entry "★ Entry: X — Y ★"
    m = _RANGE_ENTRY_RE.search(text)
    if m:
        low = _price(m.group("low"))
        high = _price(m.group("high"))
        if low and high:
            first, second = (low, high) if low.value <= high.value else (high, low)
            return [
                EntryLeg(sequence=1, entry_type="LIMIT", price=first, role="PRIMARY", is_optional=False),
                EntryLeg(sequence=2, entry_type="LIMIT", price=second, role="AVERAGING", is_optional=False),
            ]
        if low:
            return [EntryLeg(sequence=1, entry_type="LIMIT", price=low, role="PRIMARY", is_optional=False)]

    return []


def _extract_stop_loss(text: str) -> StopLoss | None:
    m = _STOP_LOSS_RE.search(text)
    if not m:
        return None
    p = _price(m.group("value"))
    return StopLoss(price=p) if p else None


def _extract_take_profits(text: str) -> list[TakeProfit]:
    tps: list[TakeProfit] = []
    for m in _TP_LINE_RE.finditer(text):
        p = _price(m.group("price"))
        if p:
            seq = int(m.group("seq"))
            tps.append(TakeProfit(sequence=seq, price=p, label=f"TP{seq}"))
    tps.sort(key=lambda t: t.sequence)
    return tps


def _entry_structure(entries: list[EntryLeg]) -> str | None:
    n = len(entries)
    if n == 0:
        return None
    if n == 1:
        return "ONE_SHOT"
    if n == 2:
        return "RANGE"
    return "LADDER"


def _missing_fields(
    *,
    symbol: str | None,
    side: str | None,
    entries: list[EntryLeg],
    stop_loss: StopLoss | None,
    take_profits: list[TakeProfit],
) -> list[str]:
    missing: list[str] = []
    if symbol is None:
        missing.append("symbol")
    if side is None:
        missing.append("side")
    if not entries:
        missing.append("entries")
    if stop_loss is None:
        missing.append("stop_loss")
    if not take_profits:
        missing.append("take_profits")
    return missing


def _price(raw: str | None) -> Price | None:
    if not raw:
        return None
    compact = raw.strip().replace(",", "")
    try:
        value = float(compact)
        return Price(raw=raw.strip(), value=value)
    except ValueError:
        return None

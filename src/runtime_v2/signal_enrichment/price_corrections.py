from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import math
import re

from src.runtime_v2.symbols import symbol_base_asset, to_raw_symbol


_NUMERIC_PREFIX_RE = re.compile(r"^(?P<prefix>\d+)(?P<rest>[A-Z].*)$")


@dataclass
class PriceCorrectionAudit:
    check: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass
class PriceCorrectionResult:
    signal: object
    audits: list[PriceCorrectionAudit] = field(default_factory=list)
    rejected: bool = False
    reason_code: str | None = None


def apply_price_corrections(signal, market_snapshot, config) -> PriceCorrectionResult:
    if not config.enabled:
        return PriceCorrectionResult(signal=signal)
    if config.numeric_prefix_exchange_rescale:
        return _correct_numeric_prefix_contract_prices(signal, market_snapshot, config)
    return PriceCorrectionResult(signal=signal)


def _correct_numeric_prefix_contract_prices(signal, market_snapshot, config) -> PriceCorrectionResult:
    raw_symbol = to_raw_symbol(getattr(signal, "symbol", None))
    base_asset = symbol_base_asset(raw_symbol)
    if not base_asset:
        return PriceCorrectionResult(signal=signal)
    match = _NUMERIC_PREFIX_RE.match(base_asset)
    if match is None:
        return PriceCorrectionResult(signal=signal)

    mark_price = getattr(market_snapshot, "mark_price", None)
    if mark_price is None or mark_price <= 0:
        if config.reject_on_unresolved_numeric_prefix_mismatch:
            return PriceCorrectionResult(
                signal=signal,
                rejected=True,
                reason_code="numeric_prefix_price_mismatch_unresolved",
                audits=[
                    PriceCorrectionAudit(
                        check="numeric_prefix_exchange_rescale_rejected",
                        details={"reason": "missing_mark_price", "symbol": raw_symbol},
                    )
                ],
            )
        return PriceCorrectionResult(signal=signal)

    prefix = int(match.group("prefix"))
    if prefix <= 1:
        return PriceCorrectionResult(signal=signal)

    first_entry = signal.entries[0] if getattr(signal, "entries", None) else None
    first_entry_price = getattr(getattr(first_entry, "price", None), "value", None)
    if first_entry_price is None or first_entry_price <= 0:
        return PriceCorrectionResult(signal=signal)

    max_ratio = float(config.numeric_prefix_max_mark_deviation_ratio)
    if _is_close_enough(first_entry_price, mark_price, max_ratio):
        return PriceCorrectionResult(signal=signal)

    scaled_signal = deepcopy(signal)
    _rescale_signal_prices(scaled_signal, prefix)
    scaled_entry_price = scaled_signal.entries[0].price.value

    if (
        not _is_close_enough(scaled_entry_price, mark_price, max_ratio)
        or not _signal_price_structure_is_valid(scaled_signal)
    ):
        return PriceCorrectionResult(
            signal=signal,
            rejected=True,
            reason_code="numeric_prefix_price_mismatch_unresolved",
            audits=[
                PriceCorrectionAudit(
                    check="numeric_prefix_exchange_rescale_rejected",
                    details={
                        "reason": "scaled_entry_not_coherent_with_mark_price",
                        "symbol": raw_symbol,
                        "factor": prefix,
                        "entry_price": first_entry_price,
                        "scaled_entry_price": scaled_entry_price,
                        "mark_price": mark_price,
                    },
                )
            ],
        )

    return PriceCorrectionResult(
        signal=scaled_signal,
        audits=[
            PriceCorrectionAudit(
                check="numeric_prefix_exchange_rescale",
                details={
                    "symbol": raw_symbol,
                    "factor": prefix,
                    "mark_price": mark_price,
                    "entry_price_before": first_entry_price,
                    "entry_price_after": scaled_entry_price,
                },
            )
        ],
    )


def _is_close_enough(candidate: float, reference: float, max_ratio: float) -> bool:
    if candidate <= 0 or reference <= 0:
        return False
    deviation_ratio = abs(candidate - reference) / reference
    return deviation_ratio <= max_ratio


def _rescale_signal_prices(signal, factor: int) -> None:
    for entry in getattr(signal, "entries", []):
        if entry.price is not None:
            entry.price.value = _scaled_price(entry.price.value, factor)
            entry.price.raw = _format_price(entry.price.value)
    stop_loss = getattr(signal, "stop_loss", None)
    if stop_loss is not None and stop_loss.price is not None:
        stop_loss.price.value = _scaled_price(stop_loss.price.value, factor)
        stop_loss.price.raw = _format_price(stop_loss.price.value)
    for tp in getattr(signal, "take_profits", []):
        tp.price.value = _scaled_price(tp.price.value, factor)
        tp.price.raw = _format_price(tp.price.value)


def _signal_price_structure_is_valid(signal) -> bool:
    entries = [entry.price.value for entry in getattr(signal, "entries", []) if entry.price is not None]
    if not entries:
        return False
    side = getattr(signal, "side", None)
    stop_loss = getattr(signal, "stop_loss", None)
    stop_price = getattr(getattr(stop_loss, "price", None), "value", None)
    if stop_price is None or stop_price <= 0:
        return False
    tp_prices = [tp.price.value for tp in getattr(signal, "take_profits", []) if tp.price is not None]
    if any(price <= 0 for price in entries) or any(price <= 0 for price in tp_prices):
        return False
    if side == "SHORT":
        if stop_price <= max(entries):
            return False
        if any(tp_price >= min(entries) for tp_price in tp_prices):
            return False
        return True
    if side == "LONG":
        if stop_price >= min(entries):
            return False
        if any(tp_price <= max(entries) for tp_price in tp_prices):
            return False
        return True
    return False


def _scaled_price(value: float, factor: int) -> float:
    return round(value * factor, 12)


def _format_price(value: float) -> str:
    return f"{value:.12f}".rstrip("0").rstrip(".")


__all__ = [
    "PriceCorrectionAudit",
    "PriceCorrectionResult",
    "apply_price_corrections",
]

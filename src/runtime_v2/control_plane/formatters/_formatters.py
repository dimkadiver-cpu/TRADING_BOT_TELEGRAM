from __future__ import annotations

import math


def num(value: object) -> str:
    if value is None:
        return "n/a"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f) and abs(f) < 1e15:
        return f"{int(f):,}"
    formatted = f"{f:.8g}"
    if "e" not in formatted and "." in formatted:
        int_part, dec_part = formatted.split(".")
        try:
            int_part = f"{int(int_part):,}"
        except ValueError:
            pass
        return f"{int_part}.{dec_part}"
    return formatted


def text(value: object) -> str:
    if value is None:
        return "n/a"
    return str(value)


def money(value: object) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f} USDT"
    except (TypeError, ValueError):
        return str(value)


def money_signed(value: object) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    number += 0.0  # normalize -0.0, otherwise it renders as "+-0.00"
    prefix = "+" if number >= 0 else ""
    return f"{prefix}{number:.2f} USDT"


def pct(value: object) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    result = f"{number:.2f}%"
    return result.replace(".00%", "%")


def pct_signed(value: object) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    number += 0.0  # normalize -0.0, otherwise it renders as "+-0.00"
    prefix = "+" if number >= 0 else ""
    result = f"{prefix}{number:.2f}%"
    return result.replace(".00%", "%")


def fee_rate(value: object) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.3f}%"
    except (TypeError, ValueError):
        return str(value)


def price(value: object) -> str:
    """Format a price value:
    - Integer: 2 decimal places with thousands separator (65,020.00)
    - 0 < |v| < 1: (leading_zeros + 5) decimal places — e.g. 0.028283651 → 0.028284
    - Otherwise: 8 significant figures with thousands separator
    """
    if value is None:
        return "n/a"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f) and abs(f) < 1e15:
        return f"{f:,.2f}"
    abs_f = abs(f)
    if 0 < abs_f < 1.0:
        n_leading = max(0, -int(math.floor(math.log10(abs_f))) - 1)
        return f"{f:.{n_leading + 5}f}"
    formatted = f"{f:.8g}"
    if "e" not in formatted and "." in formatted:
        int_part, dec_part = formatted.split(".")
        try:
            int_part = f"{int(int_part):,}"
        except ValueError:
            pass
        return f"{int_part}.{dec_part}"
    return formatted


def r_mult(value: object) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    prefix = "+" if number >= 0 else ""
    return f"{prefix}{number:.2f}R"


__all__ = ["num", "text", "money", "money_signed", "pct", "pct_signed", "fee_rate", "price", "r_mult"]

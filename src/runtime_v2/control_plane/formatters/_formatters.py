from __future__ import annotations


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


__all__ = ["num", "text", "money", "money_signed", "pct", "pct_signed", "fee_rate"]

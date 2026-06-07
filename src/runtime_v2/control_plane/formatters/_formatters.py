from __future__ import annotations


def num(value) -> str:
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


def text(value) -> str:
    if value is None:
        return "n/a"
    return str(value)


def money(value) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f} USDT"


def money_signed(value) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    prefix = "+" if number >= 0 else ""
    return f"{prefix}{number:.2f} USDT"


def pct(value) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    result = f"{number:.2f}%"
    return result.replace(".00%", "%")


def pct_signed(value) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    prefix = "+" if number >= 0 else ""
    result = f"{prefix}{number:.2f}%"
    return result.replace(".00%", "%")


def fee_rate(value) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.3f}%"


__all__ = ["num", "text", "money", "money_signed", "pct", "pct_signed", "fee_rate"]

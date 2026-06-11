"""Hardcoded pattern-based trader extraction for special topics.

Used as fallback when alias lookup in channels.yaml finds no match.
Add new cases here only when a topic uses derived trader identity
(e.g. strategy + timeframe) rather than explicit trader tags.
"""

from __future__ import annotations

# Topic ID del canale RSI multi-strategia (channels.yaml: RSI_MultiTrader).
# Aggiornare se il topic cambia.
RSI_TOPIC_ID = 4180


def extract_trader_by_pattern(topic_id: int, text: str) -> str | None:
    """Return trader_id derived from message content patterns, or None if no match.

    Gli id ritornati devono esistere in operation_config.yaml:registered_traders.
    """
    if not text:
        return None
    if topic_id == RSI_TOPIC_ID:
        if "«RSI(2) Коннора»" in text and "интрадей" in text:
            return "rsi_intraday"
        if "«RSI(2) Коннора»" in text and "свинг" in text:
            return "rsi_swing"
    return None

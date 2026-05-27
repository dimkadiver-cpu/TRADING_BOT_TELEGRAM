# src/runtime_v2/execution_gateway/event_ingest/classifier.py
from __future__ import annotations

from src.runtime_v2.execution_gateway.event_ingest.models import (
    ClassifiedEvent,
    EventSource,
    ExchangeEventType,
    ExchangeRawEvent,
)

# ---------------------------------------------------------------------------
# Deterministic Bybit field sets (Priority 1)
# ---------------------------------------------------------------------------

_CREATE_TYPE_TP = frozenset({"CreateByTakeProfit", "CreateByPartialTakeProfit"})
_CREATE_TYPE_SL = frozenset({"CreateByStopLoss", "CreateByPartialStopLoss"})
_STOP_TYPE_TP   = frozenset({"TakeProfit", "PartialTakeProfit"})
_STOP_TYPE_SL   = frozenset({"StopLoss", "PartialStopLoss"})


class EventClassifier:
    """
    Converts an ExchangeRawEvent into a ClassifiedEvent using deterministic
    Bybit fields only — no price matching, no heuristics.

    Parameters
    ----------
    known_order_link_ids:
        Mapping from orderLinkId → (trade_chain_id, role, sequence).
        role examples: "entry", "tp_1", "tp_2", "sl"
    """

    def __init__(
        self,
        known_order_link_ids: dict[str, tuple[int, str, int]],
    ) -> None:
        self._known = known_order_link_ids

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, raw: ExchangeRawEvent) -> ClassifiedEvent:
        """Classify a raw exchange event. Never raises, never returns None."""
        stream = raw.source_stream

        if stream == "watch_orders":
            return self._classify_watch_orders(raw)

        if stream == "watch_positions":
            return self._classify_watch_positions(raw)

        # execution streams: watch_my_trades / fetch_my_trades / fetch_open_orders / fetch_positions
        return self._classify_execution(raw)

    # ------------------------------------------------------------------
    # Stream-specific classifiers
    # ------------------------------------------------------------------

    def _classify_execution(self, raw: ExchangeRawEvent) -> ClassifiedEvent:
        """Handle execution-stream events (watch_my_trades, fetch_my_trades, etc.)."""

        # Priority 1 — deterministic createType / stopOrderType
        p1_type = self._p1_event_type(raw)
        if p1_type is not None:
            # Still attempt orderLinkId correlation to enrich chain/level
            chain_id, tp_level = self._correlate_link_id(raw)
            return ClassifiedEvent(
                raw=raw,
                event_type=p1_type,
                source="exchange_auto",
                trade_chain_id=chain_id,
                tp_level=tp_level,
                is_actionable=True,
            )

        # Priority 2 — orderLinkId correlation
        link = raw.order_link_id
        if link:
            entry = self._known.get(link)
            if entry is not None:
                chain_id, role, _seq = entry
                event_type, source, tp_level = self._event_from_role(role, raw)
                return ClassifiedEvent(
                    raw=raw,
                    event_type=event_type,
                    source=source,
                    trade_chain_id=chain_id,
                    tp_level=tp_level,
                    is_actionable=True,
                )

        # Priority 3 — structural inference (CreateByUser, no orderLinkId)
        if raw.create_type == "CreateByUser":
            closed = raw.closed_size or 0.0
            pos = raw.pos_qty or 0.0
            if closed > 0:
                if pos == 0.0:
                    return ClassifiedEvent(
                        raw=raw,
                        event_type="MANUAL_CLOSE_FULL",
                        source="exchange_manual",
                        is_actionable=True,
                    )
                else:
                    return ClassifiedEvent(
                        raw=raw,
                        event_type="MANUAL_CLOSE_PARTIAL",
                        source="exchange_manual",
                        is_actionable=True,
                    )

        # Fallback
        return self._unknown(raw)

    def _classify_watch_orders(self, raw: ExchangeRawEvent) -> ClassifiedEvent:
        """Handle watch_orders stream."""
        if raw.order_status == "Cancelled":
            link = raw.order_link_id
            if link:
                entry = self._known.get(link)
                if entry is not None:
                    chain_id, role, _seq = entry
                    if role == "entry":
                        return ClassifiedEvent(
                            raw=raw,
                            event_type="PENDING_ENTRY_CANCELLED",
                            source="bot_command",
                            trade_chain_id=chain_id,
                            is_actionable=True,
                        )
                    if role.startswith("tp_") or role == "sl":
                        return ClassifiedEvent(
                            raw=raw,
                            event_type="STANDALONE_PROTECTIVE_CANCELLED",
                            source="bot_command",
                            trade_chain_id=chain_id,
                            is_actionable=True,
                        )
            # Cancelled but not in known ids
            return ClassifiedEvent(
                raw=raw,
                event_type="UNKNOWN",
                source="exchange_manual",
                is_actionable=False,
            )

        return self._unknown(raw)

    def _classify_watch_positions(self, raw: ExchangeRawEvent) -> ClassifiedEvent:
        """Handle watch_positions stream."""
        tp = raw.position_take_profit
        sl = raw.position_stop_loss
        if tp == 0.0 or sl == 0.0:
            return ClassifiedEvent(
                raw=raw,
                event_type="PROTECTIVE_ORDER_CANCELLED",
                source="exchange_auto",
                is_actionable=True,
            )
        return ClassifiedEvent(
            raw=raw,
            event_type="UNKNOWN",
            source="exchange_auto",
            is_actionable=False,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _p1_event_type(self, raw: ExchangeRawEvent) -> ExchangeEventType | None:
        """
        Return the event type determined by deterministic Bybit fields,
        or None if no match.
        """
        ct = raw.create_type
        sot = raw.stop_order_type

        if (ct in _CREATE_TYPE_TP) or (sot in _STOP_TYPE_TP):
            return "TP_FILLED"
        if (ct in _CREATE_TYPE_SL) or (sot in _STOP_TYPE_SL):
            return "SL_FILLED"
        if ct == "CreateByLiq":
            return "LIQUIDATION_FILLED"
        return None

    def _correlate_link_id(self, raw: ExchangeRawEvent) -> tuple[int | None, int | None]:
        """
        Attempt orderLinkId lookup for chain/tp_level enrichment only.
        Returns (trade_chain_id, tp_level) — both None if no match.
        """
        link = raw.order_link_id
        if not link:
            return None, None
        entry = self._known.get(link)
        if entry is None:
            return None, None
        chain_id, role, _seq = entry
        tp_level: int | None = None
        if role.startswith("tp_"):
            try:
                tp_level = int(role.split("_")[1])
            except (IndexError, ValueError):
                pass
        return chain_id, tp_level

    def _event_from_role(
        self,
        role: str,
        raw: ExchangeRawEvent,
    ) -> tuple[ExchangeEventType, EventSource, int | None]:
        """Determine event_type + source + tp_level from a known role."""
        if role.startswith("tp_"):
            try:
                tp_level = int(role.split("_")[1])
            except (IndexError, ValueError):
                tp_level = None
            return "TP_FILLED", "bot_command", tp_level

        if role == "entry":
            closed = raw.closed_size or 0.0
            if closed == 0.0:
                return "ENTRY_FILLED", "bot_command", None
            pos = raw.pos_qty or 0.0
            if pos == 0.0:
                return "CLOSE_FULL_FILLED", "bot_command", None
            return "CLOSE_PARTIAL_FILLED", "bot_command", None

        if role == "sl":
            return "SL_FILLED", "bot_command", None

        return "UNKNOWN", "bot_command", None

    @staticmethod
    def _unknown(raw: ExchangeRawEvent) -> ClassifiedEvent:
        return ClassifiedEvent(
            raw=raw,
            event_type="UNKNOWN",
            source="exchange_auto",
            is_actionable=False,
        )

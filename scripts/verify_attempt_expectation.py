"""Verify expected dry-run bridge behavior for one attempt_key.

Examples:
    python scripts/verify_attempt_expectation.py --latest-signal --expect move_stop
    python scripts/verify_attempt_expectation.py --attempt-key T_xxx --expect close_partial
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.inspect_attempt import (  # type: ignore[import-not-found]
    _connect,
    load_attempt_snapshot,
    load_freqtrade_snapshot,
    resolve_db_and_attempt_key,
)

DEFAULT_FREQTRADE_DB = PROJECT_ROOT / "freqtrade" / "tradesv3.dryrun.sqlite"


class CheckResult:
    def __init__(self, name: str, ok: bool, detail: str) -> None:
        self.name = name
        self.ok = ok
        self.detail = detail


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify expected bridge behavior for one attempt_key."
    )
    parser.add_argument("--attempt-key", default=None, help="Exact attempt_key to verify.")
    parser.add_argument("--latest-signal", action="store_true", help="Use newest signal.")
    parser.add_argument("--latest-trade", action="store_true", help="Use newest trade.")
    parser.add_argument("--symbol", default=None, help="Use latest attempt for a symbol.")
    parser.add_argument(
        "--expect",
        required=True,
        choices=(
            "move_stop",
            "tp1",
            "close_partial",
            "cancel_pending",
            "close_full",
            "entry_filled",
        ),
        help="Expected scenario to verify.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to the TeleSignalBot SQLite DB. If omitted, auto-detects a matching DB.",
    )
    parser.add_argument(
        "--freqtrade-db-path",
        default=str(DEFAULT_FREQTRADE_DB),
        help="Path to the freqtrade dry-run trades DB.",
    )
    return parser.parse_args()


def _events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return list(snapshot.get("events") or [])


def _orders(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return list(snapshot.get("orders") or [])


def _event_types(snapshot: dict[str, Any]) -> set[str]:
    return {str(row.get("event_type")) for row in _events(snapshot)}


def _has_event(snapshot: dict[str, Any], *names: str) -> bool:
    existing = _event_types(snapshot)
    return any(name in existing for name in names)


def _filter_orders(snapshot: dict[str, Any], *, purpose: str) -> list[dict[str, Any]]:
    return [row for row in _orders(snapshot) if str(row.get("purpose")) == purpose]


def _trade_state(snapshot: dict[str, Any]) -> str | None:
    trade = snapshot.get("trade") or {}
    state = trade.get("state")
    return str(state) if isinstance(state, str) else None


def _position_size(snapshot: dict[str, Any]) -> float | None:
    position = snapshot.get("position") or {}
    value = position.get("size")
    return float(value) if isinstance(value, (int, float)) else None


def _signal_status(snapshot: dict[str, Any]) -> str | None:
    signal = snapshot.get("signal") or {}
    status = signal.get("status")
    return str(status) if isinstance(status, str) else None


def _signal_stop(snapshot: dict[str, Any]) -> float | None:
    signal = snapshot.get("signal") or {}
    value = signal.get("sl")
    return float(value) if isinstance(value, (int, float)) else None


def _open_freqtrade_entry_orders(freqtrade: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in (freqtrade.get("orders") or [])
        if str(row.get("ft_order_tag") or "") and ":ENTRY:" in str(row.get("ft_order_tag"))
        and str(row.get("status") or "").lower() in {"open", "new"}
    ]


def verify_entry_filled(snapshot: dict[str, Any], freqtrade: dict[str, Any]) -> list[CheckResult]:
    orders_entry = _filter_orders(snapshot, purpose="ENTRY")
    return [
        CheckResult(
            "entry_filled_event",
            _has_event(snapshot, "ENTRY_FILLED"),
            "events contains ENTRY_FILLED",
        ),
        CheckResult(
            "signal_active",
            _signal_status(snapshot) in {"ACTIVE", "CLOSED"},
            f"signal status is {_signal_status(snapshot)}",
        ),
        CheckResult(
            "entry_order_filled",
            any(str(row.get("status")) == "FILLED" for row in orders_entry),
            "bot DB has ENTRY order with status FILLED",
        ),
        CheckResult(
            "trade_created",
            snapshot.get("trade") is not None,
            "trade row exists",
        ),
        CheckResult(
            "protective_orders_present",
            bool(_filter_orders(snapshot, purpose="SL")) or bool(_filter_orders(snapshot, purpose="TP")),
            "bot DB has SL or TP orders",
        ),
        CheckResult(
            "freqtrade_side_visible",
            bool(freqtrade.get("orders") or []),
            "freqtrade DB has orders linked to attempt_key",
        ),
    ]


def verify_move_stop(snapshot: dict[str, Any], freqtrade: dict[str, Any]) -> list[CheckResult]:
    sl_orders = _filter_orders(snapshot, purpose="SL")
    stop_value = _signal_stop(snapshot)
    sl_matches = any(
        isinstance(row.get("trigger_price"), (int, float))
        and stop_value is not None
        and abs(float(row.get("trigger_price")) - stop_value) < 1e-9
        for row in sl_orders
    )
    return [
        CheckResult(
            "stop_change_event",
            _has_event(snapshot, "STOP_MOVED", "STOP_REPLACED", "MACHINE_EVENT_MOVE_STOP_TO_BE"),
            "events contains a stop-change marker",
        ),
        CheckResult(
            "signal_stop_present",
            stop_value is not None and stop_value > 0,
            f"signals.sl is {stop_value}",
        ),
        CheckResult(
            "sl_order_matches_signal",
            sl_matches,
            "at least one SL order trigger matches signals.sl",
        ),
        CheckResult(
            "freqtrade_stop_visible",
            any(
                str(row.get("ft_order_side") or "").lower() == "stoploss"
                for row in (freqtrade.get("orders") or [])
            ),
            "freqtrade DB shows a stoploss-side order for the attempt",
        ),
    ]


def verify_tp1(snapshot: dict[str, Any], freqtrade: dict[str, Any]) -> list[CheckResult]:
    tp_orders = _filter_orders(snapshot, purpose="TP")
    tp1_filled = any(int(row.get("idx") or -1) == 0 and str(row.get("status")) == "FILLED" for row in tp_orders)
    return [
        CheckResult(
            "tp_hit_event",
            _has_event(snapshot, "TP_FILL_SYNCED", "PARTIAL_CLOSE_FILLED", "POSITION_CLOSED"),
            "events contains TP-related fill/close signal",
        ),
        CheckResult(
            "tp1_marked_filled",
            tp1_filled,
            "bot DB marks TP idx=0 as FILLED",
        ),
        CheckResult(
            "trade_still_meaningful",
            _trade_state(snapshot) in {"OPEN", "CLOSED"},
            f"trade state is {_trade_state(snapshot)}",
        ),
        CheckResult(
            "freqtrade_exit_visible",
            any(
                str(row.get("ft_order_side") or "").lower() in {"sell", "buy"}
                and ":TP:" in str(row.get("ft_order_tag") or "")
                for row in (freqtrade.get("orders") or [])
            ) or _has_event(snapshot, "PARTIAL_CLOSE_FILLED", "POSITION_CLOSED"),
            "freqtrade/bridge exit evidence exists",
        ),
    ]


def verify_close_partial(snapshot: dict[str, Any], freqtrade: dict[str, Any]) -> list[CheckResult]:
    remaining_size = _position_size(snapshot)
    return [
        CheckResult(
            "partial_close_event",
            _has_event(snapshot, "PARTIAL_CLOSE_FILLED"),
            "events contains PARTIAL_CLOSE_FILLED",
        ),
        CheckResult(
            "trade_stays_open",
            _trade_state(snapshot) == "OPEN",
            f"trade state is {_trade_state(snapshot)}",
        ),
        CheckResult(
            "position_remaining",
            remaining_size is not None and remaining_size > 0,
            f"position size is {remaining_size}",
        ),
        CheckResult(
            "freqtrade_exit_seen",
            any(
                str(row.get("ft_order_tag") or "").startswith(str(snapshot.get("signal", {}).get("attempt_key", "")))
                and str(row.get("ft_order_side") or "").lower() in {"sell", "buy"}
                for row in (freqtrade.get("orders") or [])
            ) or _has_event(snapshot, "PARTIAL_CLOSE_FILLED"),
            "freqtrade/bridge exit evidence exists",
        ),
    ]


def verify_cancel_pending(snapshot: dict[str, Any], freqtrade: dict[str, Any]) -> list[CheckResult]:
    entry_orders = _filter_orders(snapshot, purpose="ENTRY")
    return [
        CheckResult(
            "no_entry_fill",
            not _has_event(snapshot, "ENTRY_FILLED"),
            "events does not contain ENTRY_FILLED",
        ),
        CheckResult(
            "entry_cancelled_or_absent",
            not entry_orders or all(str(row.get("status")) in {"CANCELLED", "EXPIRED", "REJECTED"} for row in entry_orders),
            "ENTRY orders are cancelled/expired/rejected or absent",
        ),
        CheckResult(
            "no_trade_opened",
            snapshot.get("trade") is None or _trade_state(snapshot) != "OPEN",
            f"trade state is {_trade_state(snapshot)}",
        ),
        CheckResult(
            "no_open_freqtrade_entry",
            not _open_freqtrade_entry_orders(freqtrade),
            "freqtrade DB has no open ENTRY order for the attempt",
        ),
    ]


def verify_close_full(snapshot: dict[str, Any], freqtrade: dict[str, Any]) -> list[CheckResult]:
    remaining_size = _position_size(snapshot)
    return [
        CheckResult(
            "close_event",
            _has_event(snapshot, "POSITION_CLOSED"),
            "events contains POSITION_CLOSED",
        ),
        CheckResult(
            "trade_closed",
            _trade_state(snapshot) == "CLOSED",
            f"trade state is {_trade_state(snapshot)}",
        ),
        CheckResult(
            "position_zero_or_missing",
            remaining_size in (None, 0.0),
            f"position size is {remaining_size}",
        ),
        CheckResult(
            "no_open_bot_orders",
            all(str(row.get("status")) not in {"OPEN", "NEW"} for row in _orders(snapshot)),
            "bot DB has no open/new orders for the attempt",
        ),
    ]


VERIFY_MAP: dict[str, Callable[[dict[str, Any], dict[str, Any]], list[CheckResult]]] = {
    "entry_filled": verify_entry_filled,
    "move_stop": verify_move_stop,
    "tp1": verify_tp1,
    "close_partial": verify_close_partial,
    "cancel_pending": verify_cancel_pending,
    "close_full": verify_close_full,
}


def main() -> None:
    args = parse_args()
    db_path, attempt_key = resolve_db_and_attempt_key(args)
    with _connect(db_path) as conn:
        snapshot = load_attempt_snapshot(conn=conn, attempt_key=attempt_key)

    freqtrade = load_freqtrade_snapshot(
        freqtrade_db_path=str(Path(args.freqtrade_db_path).resolve()),
        attempt_key=attempt_key,
    )

    results = VERIFY_MAP[args.expect](snapshot, freqtrade)
    passed = sum(1 for result in results if result.ok)
    failed = len(results) - passed

    print(f"BOT_DB: {db_path}")
    print(f"ATTEMPT_KEY: {attempt_key}")
    print(f"EXPECTATION: {args.expect}")
    print(f"RESULT: {'PASS' if failed == 0 else 'FAIL'} ({passed} passed, {failed} failed)")
    print("")
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.name} - {result.detail}")


if __name__ == "__main__":
    main()

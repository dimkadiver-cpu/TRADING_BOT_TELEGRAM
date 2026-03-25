"""Tests for src/target_resolver/resolver.py."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.core.migrations import apply_migrations
from src.parser.models.operational import OperationalSignal, ResolvedTarget
from src.parser.trader_profiles.base import TraderParseResult
from src.target_resolver.resolver import TargetResolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "resolver_test.sqlite3")
    apply_migrations(db_path=db_path, migrations_dir=str(Path("db/migrations").resolve()))
    return db_path


def _insert_signal(
    db_path: str,
    *,
    attempt_key: str,
    trader_id: str,
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    status: str = "PENDING",
    root_telegram_id: str = "1",
    trader_signal_id: int | None = None,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO signals
               (attempt_key, env, channel_id, root_telegram_id, trader_id, trader_prefix,
                symbol, side, entry_json, sl, tp_json, status, confidence, raw_text,
                created_at, updated_at, trader_signal_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (attempt_key, "T", "100", root_telegram_id, trader_id, "TP",
             symbol, side, "[]", 55000.0, "[]", status, 0.9, "test",
             "2026-01-01", "2026-01-01", trader_signal_id),
        )
        conn.commit()


def _insert_op_signal(db_path: str, *, parse_result_id: int, attempt_key: str,
                      trader_id: str) -> int:
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO operational_signals
               (parse_result_id, attempt_key, trader_id, message_type, is_blocked,
                created_at)
               VALUES (?,?,?,?,?,?)""",
            (parse_result_id, attempt_key, trader_id, "NEW_SIGNAL", 0, "2026-01-01"),
        )
        conn.commit()
        return int(cur.lastrowid)


def _insert_parse_result(db_path: str, *, raw_message_id: int = 1) -> int:
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO parse_results
               (raw_message_id,eligibility_status,eligibility_reason,
                resolved_trader_id,trader_resolution_method,message_type,
                parse_status,completeness,is_executable,risky_flag,created_at,updated_at)
               VALUES (?,'OK','ok','tr_a','direct','NEW_SIGNAL','PARSED','COMPLETE',1,0,
                       '2026-01-01','2026-01-01')""",
            (raw_message_id,),
        )
        conn.commit()
        return int(cur.lastrowid)


def _make_op_signal(
    message_type: str = "UPDATE",
    intents: list[str] | None = None,
    target_refs: list[dict] | None = None,
    trader_id: str = "tr_a",
    symbol: str = "BTCUSDT",
) -> OperationalSignal:
    return OperationalSignal(
        parse_result=TraderParseResult(
            message_type=message_type,
            intents=intents or ["U_CLOSE_FULL"],
            entities={"symbol": symbol, "side": "BUY"},
            target_refs=target_refs or [],
        ),
        trader_id=trader_id,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestResolverNewSignalNoTarget:
    def test_new_signal_no_target_returns_none(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        op = _make_op_signal(message_type="NEW_SIGNAL", intents=[], target_refs=[])
        result = TargetResolver().resolve(op, db_path=db_path)
        assert result is None


class TestResolverSymbolTarget:
    def test_symbol_target_resolved(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        pr_id = _insert_parse_result(db_path, raw_message_id=1)
        _insert_signal(db_path, attempt_key="T_100_1_tr_a", trader_id="tr_a",
                       symbol="BTCUSDT", status="ACTIVE")
        _insert_op_signal(db_path, parse_result_id=pr_id, attempt_key="T_100_1_tr_a",
                          trader_id="tr_a")

        op = _make_op_signal(
            target_refs=[{"kind": "SYMBOL", "symbol": "BTCUSDT"}],
            intents=["U_CLOSE_FULL"],
            trader_id="tr_a",
        )
        result = TargetResolver().resolve(op, db_path=db_path)
        assert result is not None
        assert result.eligibility == "ELIGIBLE"
        assert len(result.position_ids) == 1

    def test_symbol_target_no_match_unresolved(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        op = _make_op_signal(
            target_refs=[{"kind": "SYMBOL", "symbol": "ETHUSDT"}],
            intents=["U_CLOSE_FULL"],
        )
        result = TargetResolver().resolve(op, db_path=db_path)
        assert result is not None
        assert result.eligibility == "UNRESOLVED"

    def test_symbol_target_pending_warns(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        pr_id = _insert_parse_result(db_path, raw_message_id=1)
        _insert_signal(db_path, attempt_key="T_100_1_tr_a", trader_id="tr_a",
                       symbol="BTCUSDT", status="PENDING")
        _insert_op_signal(db_path, parse_result_id=pr_id, attempt_key="T_100_1_tr_a",
                          trader_id="tr_a")

        op = _make_op_signal(
            target_refs=[{"kind": "SYMBOL", "symbol": "BTCUSDT"}],
            intents=["U_CLOSE_FULL"],  # PENDING + U_CLOSE_FULL → WARN
        )
        result = TargetResolver().resolve(op, db_path=db_path)
        assert result is not None
        assert result.eligibility == "WARN"


class TestResolverStrongReply:
    def test_reply_target_resolved(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        pr_id = _insert_parse_result(db_path, raw_message_id=1)
        _insert_signal(db_path, attempt_key="T_100_10_tr_a", trader_id="tr_a",
                       root_telegram_id="10", status="ACTIVE")
        _insert_op_signal(db_path, parse_result_id=pr_id, attempt_key="T_100_10_tr_a",
                          trader_id="tr_a")

        op = _make_op_signal(
            target_refs=[{"kind": "STRONG", "method": "REPLY", "ref": 10}],
            intents=["U_CLOSE_FULL"],
        )
        result = TargetResolver().resolve(op, db_path=db_path)
        assert result is not None
        assert result.eligibility == "ELIGIBLE"

    def test_reply_no_match_unresolved(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        op = _make_op_signal(
            target_refs=[{"kind": "STRONG", "method": "REPLY", "ref": 999}],
        )
        result = TargetResolver().resolve(op, db_path=db_path)
        assert result is not None
        assert result.eligibility == "UNRESOLVED"


class TestResolverGlobal:
    def test_all_positions_resolved(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        pr1 = _insert_parse_result(db_path, raw_message_id=1)
        pr2 = _insert_parse_result(db_path, raw_message_id=2)
        _insert_signal(db_path, attempt_key="T_100_1_tr_a", trader_id="tr_a",
                       symbol="BTCUSDT", side="BUY", status="ACTIVE",
                       root_telegram_id="1")
        _insert_signal(db_path, attempt_key="T_100_2_tr_a", trader_id="tr_a",
                       symbol="ETHUSDT", side="BUY", status="ACTIVE",
                       root_telegram_id="2")
        _insert_op_signal(db_path, parse_result_id=pr1, attempt_key="T_100_1_tr_a",
                          trader_id="tr_a")
        _insert_op_signal(db_path, parse_result_id=pr2, attempt_key="T_100_2_tr_a",
                          trader_id="tr_a")

        op = _make_op_signal(
            target_refs=[{"kind": "GLOBAL", "scope": "all_positions"}],
            intents=["U_CLOSE_FULL"],
        )
        result = TargetResolver().resolve(op, db_path=db_path)
        assert result is not None
        assert result.eligibility == "ELIGIBLE"
        assert len(result.position_ids) == 2

    def test_all_long_only_buys(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        pr1 = _insert_parse_result(db_path, raw_message_id=1)
        pr2 = _insert_parse_result(db_path, raw_message_id=2)
        _insert_signal(db_path, attempt_key="T_100_1_tr_a", trader_id="tr_a",
                       symbol="BTCUSDT", side="BUY", status="ACTIVE",
                       root_telegram_id="1")
        _insert_signal(db_path, attempt_key="T_100_2_tr_a", trader_id="tr_a",
                       symbol="ETHUSDT", side="SELL", status="ACTIVE",
                       root_telegram_id="2")
        _insert_op_signal(db_path, parse_result_id=pr1, attempt_key="T_100_1_tr_a",
                          trader_id="tr_a")
        _insert_op_signal(db_path, parse_result_id=pr2, attempt_key="T_100_2_tr_a",
                          trader_id="tr_a")

        op = _make_op_signal(
            target_refs=[{"kind": "GLOBAL", "scope": "all_long"}],
            intents=["U_CLOSE_FULL"],
        )
        result = TargetResolver().resolve(op, db_path=db_path)
        assert result is not None
        assert result.eligibility == "ELIGIBLE"
        assert len(result.position_ids) == 1  # only BUY


class TestResolverClosedIneligible:
    def test_closed_signal_ineligible(self, tmp_path: Path) -> None:
        db_path = _make_db(tmp_path)
        # Closed signals are not returned by get_open_by_trader_and_symbol → UNRESOLVED
        _insert_signal(db_path, attempt_key="T_100_1_tr_a", trader_id="tr_a",
                       symbol="BTCUSDT", status="CLOSED")

        op = _make_op_signal(
            target_refs=[{"kind": "SYMBOL", "symbol": "BTCUSDT"}],
            intents=["U_CLOSE_FULL"],
        )
        result = TargetResolver().resolve(op, db_path=db_path)
        assert result is not None
        assert result.eligibility == "UNRESOLVED"  # closed → not in open signals query

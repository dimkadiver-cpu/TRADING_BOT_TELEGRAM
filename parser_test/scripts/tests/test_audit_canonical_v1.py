from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.core.migrations import apply_migrations

from parser_test.reporting.canonical_v1_audit import run_canonical_v1_audit


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "parser_test.sqlite3"
    apply_migrations(
        db_path=str(db_path),
        migrations_dir=str(Path("db/migrations").resolve()),
    )
    return db_path


def _insert_raw(
    conn: sqlite3.Connection,
    *,
    source_chat_id: str,
    telegram_message_id: int,
    raw_text: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO raw_messages (
            source_chat_id,
            telegram_message_id,
            raw_text,
            message_ts,
            acquired_at,
            acquisition_status
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            source_chat_id,
            telegram_message_id,
            raw_text,
            "2026-04-22T10:00:00+00:00",
            "2026-04-22T10:00:01+00:00",
            "ACQUIRED",
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def _insert_parse_result(
    conn: sqlite3.Connection,
    *,
    raw_message_id: int,
    trader_id: str,
    message_type: str,
    normalized_json: dict,
) -> None:
    conn.execute(
        """
        INSERT INTO parse_results (
            raw_message_id,
            eligibility_status,
            eligibility_reason,
            resolved_trader_id,
            trader_resolution_method,
            message_type,
            parse_status,
            completeness,
            is_executable,
            created_at,
            updated_at,
            parse_result_normalized_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            raw_message_id,
            "ACQUIRED_ELIGIBLE",
            "eligible",
            trader_id,
            "test",
            message_type,
            "PARSED",
            "COMPLETE",
            1 if message_type == "NEW_SIGNAL" else 0,
            "2026-04-22T10:00:02+00:00",
            "2026-04-22T10:00:03+00:00",
            json.dumps(normalized_json),
        ),
    )
    conn.commit()


def test_run_canonical_v1_audit_generates_summary_and_rows(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    output_dir = tmp_path / "reports"

    with sqlite3.connect(str(db_path)) as conn:
        raw_1 = _insert_raw(
            conn,
            source_chat_id="-1001",
            telegram_message_id=10,
            raw_text="BTC long entry 50000 sl 48000 tp 52000",
        )
        _insert_parse_result(
            conn,
            raw_message_id=raw_1,
            trader_id="trader_a",
            message_type="NEW_SIGNAL",
            normalized_json={
                "message_type": "NEW_SIGNAL",
                "intents": ["NS_CREATE_SIGNAL"],
                "entities": {
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "stop_loss": 48000.0,
                    "take_profits": [52000.0],
                    "entry_plan_entries": [
                        {
                            "sequence": 1,
                            "role": "PRIMARY",
                            "order_type": "LIMIT",
                            "price": 50000.0,
                            "is_optional": False,
                        }
                    ],
                    "entry_structure": "ONE_SHOT",
                },
                "actions_structured": [{"action": "CREATE_SIGNAL"}],
                "warnings": [],
                "confidence": 0.9,
            },
        )

        raw_2 = _insert_raw(
            conn,
            source_chat_id="-1001",
            telegram_message_id=11,
            raw_text="note only",
        )
        _insert_parse_result(
            conn,
            raw_message_id=raw_2,
            trader_id="trader_a",
            message_type="INFO_ONLY",
            normalized_json={
                "message_type": "INFO_ONLY",
                "intents": ["NS_CREATE_SIGNAL"],
                "entities": {
                    "symbol": "ETHUSDT",
                    "side": "LONG",
                    "stop_loss": 2400.0,
                    "take_profits": [2600.0],
                    "entry_plan_entries": [
                        {
                            "sequence": 1,
                            "role": "PRIMARY",
                            "order_type": "LIMIT",
                            "price": 2500.0,
                            "is_optional": False,
                        }
                    ],
                    "entry_structure": "ONE_SHOT",
                },
                "actions_structured": [{"action": "CREATE_SIGNAL"}],
                "warnings": [],
                "confidence": 0.2,
            },
        )

        raw_3 = _insert_raw(
            conn,
            source_chat_id="-1001",
            telegram_message_id=12,
            raw_text="unknown intent",
        )
        _insert_parse_result(
            conn,
            raw_message_id=raw_3,
            trader_id="trader_a",
            message_type="UPDATE",
            normalized_json={
                "message_type": "UPDATE",
                "intents": ["U_UNKNOWN_INTENT"],
                "entities": {},
                "actions_structured": [{"action": "NOOP"}],
                "warnings": [],
                "confidence": 0.2,
            },
        )

    result = run_canonical_v1_audit(
        db_path=db_path,
        output_dir=output_dir,
        trader="trader_a",
    )

    assert result.total_rows == 3
    assert result.canonical_valid_rows == 3
    assert result.normalizer_error_rows == 0
    assert result.class_mismatch_rows == 1
    assert result.primary_class_counts["SIGNAL"] == 2
    assert result.primary_class_counts["UPDATE"] == 1
    assert result.unmapped_intent_counts["U_UNKNOWN_INTENT"] == 1
    assert result.summary_path.exists()
    assert result.rows_path.exists()

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["total_rows"] == 3
    assert summary["class_mismatch_rows"] == 1
    assert summary["unmapped_intent_counts"]["U_UNKNOWN_INTENT"] == 1


def test_run_canonical_v1_audit_filters_by_trader(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    output_dir = tmp_path / "reports"

    with sqlite3.connect(str(db_path)) as conn:
        raw_a = _insert_raw(
            conn,
            source_chat_id="-1001",
            telegram_message_id=20,
            raw_text="A message",
        )
        _insert_parse_result(
            conn,
            raw_message_id=raw_a,
            trader_id="trader_a",
            message_type="INFO_ONLY",
            normalized_json={
                "message_type": "INFO_ONLY",
                "intents": ["U_RISK_NOTE"],
                "entities": {},
                "warnings": [],
                "confidence": 0.3,
            },
        )

        raw_b = _insert_raw(
            conn,
            source_chat_id="-1002",
            telegram_message_id=21,
            raw_text="B message",
        )
        _insert_parse_result(
            conn,
            raw_message_id=raw_b,
            trader_id="trader_b",
            message_type="INFO_ONLY",
            normalized_json={
                "message_type": "INFO_ONLY",
                "intents": ["U_RISK_NOTE"],
                "entities": {},
                "warnings": [],
                "confidence": 0.3,
            },
        )

    result = run_canonical_v1_audit(
        db_path=db_path,
        output_dir=output_dir,
        trader="trader_b",
    )

    assert result.total_rows == 1
    assert result.normalizer_error_rows == 0
    assert result.primary_class_counts["INFO"] == 1


def test_run_canonical_v1_audit_records_normalizer_errors_without_crashing(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    output_dir = tmp_path / "reports"

    with sqlite3.connect(str(db_path)) as conn:
        raw_id = _insert_raw(
            conn,
            source_chat_id="-1003",
            telegram_message_id=30,
            raw_text="broken ladder",
        )
        _insert_parse_result(
            conn,
            raw_message_id=raw_id,
            trader_id="trader_c",
            message_type="NEW_SIGNAL",
            normalized_json={
                "message_type": "NEW_SIGNAL",
                "intents": ["NS_CREATE_SIGNAL"],
                "entities": {
                    "symbol": "BTCUSDT",
                    "entry_structure": "LADDER",
                    "entry_plan_entries": [
                        {
                            "sequence": 1,
                            "role": "PRIMARY",
                            "order_type": "LIMIT",
                            "price": 50000.0,
                            "is_optional": False,
                        },
                        {
                            "sequence": 2,
                            "role": "AVERAGING",
                            "order_type": "LIMIT",
                            "price": 49000.0,
                            "is_optional": False,
                        },
                    ],
                    "stop_loss": 48000.0,
                    "take_profits": [52000.0],
                },
                "warnings": [],
                "confidence": 0.7,
            },
        )

    result = run_canonical_v1_audit(
        db_path=db_path,
        output_dir=output_dir,
        trader="trader_c",
    )

    assert result.total_rows == 1
    assert result.canonical_valid_rows == 0
    assert result.normalizer_error_rows == 1
    assert result.primary_class_counts["ERROR"] == 1

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["normalizer_error_rows"] == 1
    assert summary["normalizer_error_examples"]

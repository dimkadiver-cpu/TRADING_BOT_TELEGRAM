from __future__ import annotations

from pathlib import Path

import pytest

from parser_test.scripts.db_paths import build_named_parser_test_db_path, resolve_parser_test_db_path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PARSER_TEST_DIR = PROJECT_ROOT / "parser_test"


def test_resolve_parser_test_db_path_uses_explicit_path() -> None:
    db_path = resolve_parser_test_db_path(
        project_root=PROJECT_ROOT,
        parser_test_dir=PARSER_TEST_DIR,
        explicit_db_path="parser_test/db/custom.sqlite3",
    )
    assert db_path == str((PROJECT_ROOT / "parser_test/db/custom.sqlite3").resolve())


def test_resolve_parser_test_db_path_uses_db_name() -> None:
    db_path = resolve_parser_test_db_path(
        project_root=PROJECT_ROOT,
        parser_test_dir=PARSER_TEST_DIR,
        explicit_db_path=None,
        db_name="Trader A March",
    )
    assert db_path.endswith("parser_test__trader_a_march.sqlite3")


def test_resolve_parser_test_db_path_uses_chat_suffix() -> None:
    db_path = resolve_parser_test_db_path(
        project_root=PROJECT_ROOT,
        parser_test_dir=PARSER_TEST_DIR,
        explicit_db_path=None,
        db_per_chat=True,
        chat_ref="-1001234567890",
    )
    assert db_path.endswith("parser_test__chat_1001234567890.sqlite3")


def test_resolve_parser_test_db_path_requires_chat_for_db_per_chat() -> None:
    with pytest.raises(RuntimeError, match="db-per-chat"):
        resolve_parser_test_db_path(
            project_root=PROJECT_ROOT,
            parser_test_dir=PARSER_TEST_DIR,
            explicit_db_path=None,
            db_per_chat=True,
            chat_ref=None,
        )


def test_build_named_parser_test_db_path_slugifies_value() -> None:
    db_path = build_named_parser_test_db_path(parser_test_dir=PARSER_TEST_DIR, name="Canale VIP / Marzo")
    assert db_path.endswith("parser_test__canale_vip_marzo.sqlite3")

"""Helpers for resolving parser_test database paths."""

from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_DB_FILENAME = "parser_test.sqlite3"


def resolve_parser_test_db_path(
    *,
    project_root: Path,
    parser_test_dir: Path,
    explicit_db_path: str | None,
    db_name: str | None = None,
    db_per_chat: bool = False,
    chat_ref: str | None = None,
) -> str:
    if explicit_db_path:
        return _resolve_path(project_root=project_root, value=explicit_db_path)

    if db_name:
        return str((parser_test_dir / "db" / _named_db_filename(db_name)).resolve())

    if db_per_chat:
        if not chat_ref or not str(chat_ref).strip():
            raise RuntimeError("--db-per-chat requires --chat-id or PARSER_TEST_CHAT_ID.")
        return str((parser_test_dir / "db" / _named_db_filename(f"chat_{chat_ref}")).resolve())

    configured = os.getenv("PARSER_TEST_DB_PATH", str(parser_test_dir / "db" / DEFAULT_DB_FILENAME))
    return _resolve_path(project_root=project_root, value=configured)


def build_named_parser_test_db_path(*, parser_test_dir: Path, name: str) -> str:
    return str((parser_test_dir / "db" / _named_db_filename(name)).resolve())


def _named_db_filename(name: str) -> str:
    slug = _slugify(name)
    return f"parser_test__{slug}.sqlite3"


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip())
    cleaned = cleaned.strip("_").lower()
    return cleaned or "default"


def _resolve_path(*, project_root: Path, value: str) -> str:
    path = Path(value)
    return str((project_root / path).resolve()) if not path.is_absolute() else str(path)

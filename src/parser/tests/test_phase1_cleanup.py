from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "relative_path",
    [
        "src/parser/action_builders/__init__.py",
        "src/parser/adapters/__init__.py",
    ],
)
def test_phase1_cleanup_removes_unreferenced_legacy_files(relative_path: str) -> None:
    assert not Path(relative_path).exists()

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parent
_TMP_BASE = _PROJECT_ROOT / ".test_tmp"


@pytest.fixture()
def tmp_path() -> Path:
    """Override pytest's default tmp_path to stay inside the workspace.

    On Windows, the default resolves to %LOCALAPPDATA%\\Temp which can
    trigger PermissionError in restricted environments.  This fixture
    creates an isolated subdirectory under <project_root>/.test_tmp and
    cleans it up after each test.
    """
    path = _TMP_BASE / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)

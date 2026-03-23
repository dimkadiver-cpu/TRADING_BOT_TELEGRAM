from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import uuid

import pytest


@pytest.fixture()
def tmp_path() -> Path:
    path = Path("C:/TeleSignalBot/.codex_tmp/pytest") / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    test_func = pyfuncitem.obj
    if not asyncio.iscoroutinefunction(test_func):
        return None
    asyncio.run(test_func(**pyfuncitem.funcargs))
    return True

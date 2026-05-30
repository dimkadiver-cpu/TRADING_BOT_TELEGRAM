# tests/runtime_v2/control_plane/conftest.py
from __future__ import annotations

import asyncio
import inspect

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    test_func = pyfuncitem.obj
    if not asyncio.iscoroutinefunction(test_func):
        return None
    sig = inspect.signature(test_func)
    # Filter out kwargs injected by pytest internals (e.g. event_loop_policy)
    # that are not declared in the test function's signature.
    filtered = {k: v for k, v in pyfuncitem.funcargs.items() if k in sig.parameters}
    asyncio.run(test_func(**filtered))
    return True

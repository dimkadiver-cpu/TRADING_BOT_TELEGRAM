from __future__ import annotations

import asyncio

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    test_func = pyfuncitem.obj
    if not asyncio.iscoroutinefunction(test_func):
        return None
    asyncio.run(test_func(**pyfuncitem.funcargs))
    return True

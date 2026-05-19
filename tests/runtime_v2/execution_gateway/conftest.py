# tests/runtime_v2/execution_gateway/conftest.py
from __future__ import annotations

import os
import pytest

# Set BYBIT_TESTNET_API_KEY env var to enable Bybit testnet integration tests.
# The secret for the adapter goes in BYBIT_API_SECRET_{ADAPTER_NAME_UPPER} (separate concern).


def pytest_collection_modifyitems(config, items):
    if not os.environ.get("BYBIT_TESTNET_API_KEY"):
        skip_marker = pytest.mark.skip(
            reason="Set BYBIT_TESTNET_API_KEY env var to run Bybit testnet integration tests"
        )
        for item in items:
            if item.get_closest_marker("bybit_testnet"):
                item.add_marker(skip_marker)

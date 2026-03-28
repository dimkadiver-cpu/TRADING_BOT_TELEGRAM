from __future__ import annotations

import json
from pathlib import Path

from src.execution.dynamic_pairlist import DynamicPairlistManager


def test_manager_creates_empty_file_with_refresh_period(tmp_path: Path) -> None:
    path = tmp_path / 'dynamic_pairs.json'

    manager = DynamicPairlistManager(path, refresh_period=15)

    payload = json.loads(path.read_text(encoding='utf-8'))
    assert manager.path == path
    assert payload == {'pairs': [], 'refresh_period': 15}


def test_manager_adds_mappable_symbol_once(tmp_path: Path) -> None:
    path = tmp_path / 'dynamic_pairs.json'
    manager = DynamicPairlistManager(path, refresh_period=10)

    first = manager.ensure_symbol('BTCUSDT')
    second = manager.ensure_symbol('BTCUSDT')

    payload = json.loads(path.read_text(encoding='utf-8'))
    assert first == 'BTC/USDT:USDT'
    assert second == 'BTC/USDT:USDT'
    assert payload == {'pairs': ['BTC/USDT:USDT'], 'refresh_period': 10}


def test_manager_ignores_unmappable_symbol(tmp_path: Path) -> None:
    path = tmp_path / 'dynamic_pairs.json'
    manager = DynamicPairlistManager(path)

    result = manager.ensure_symbol('BTCUSD')

    payload = json.loads(path.read_text(encoding='utf-8'))
    assert result is None
    assert payload == {'pairs': [], 'refresh_period': 10}

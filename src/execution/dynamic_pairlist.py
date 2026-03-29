"""Helpers to maintain a dynamic freqtrade pairlist file."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from src.execution.freqtrade_normalizer import canonical_symbol_to_freqtrade_pair


class DynamicPairlistManager:
    """Maintain a local JSON pairlist that freqtrade can refresh via RemotePairList."""

    def __init__(self, path: str | Path, *, refresh_period: int = 10) -> None:
        self._path = Path(path)
        self._refresh_period = max(1, int(refresh_period))
        self._ensure_file()

    @property
    def path(self) -> Path:
        return self._path

    def ensure_symbol(self, symbol: str | None) -> str | None:
        pair = canonical_symbol_to_freqtrade_pair(symbol)
        if not pair:
            return None
        self.ensure_pair(pair)
        return pair

    def ensure_pair(self, pair: str | None) -> bool:
        normalized = str(pair or "").strip().upper()
        if not normalized:
            return False

        payload = self._load_payload()
        pairs = {
            str(item).strip().upper()
            for item in payload.get("pairs", [])
            if isinstance(item, str)
        }
        before = len(pairs)
        pairs.add(normalized)
        if len(pairs) == before:
            return False

        self._write_payload(
            {
                "pairs": sorted(pairs),
                "refresh_period": self._refresh_period,
            }
        )
        return True

    def snapshot(self) -> dict[str, Any]:
        return self._load_payload()

    def _ensure_file(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._write_payload({"pairs": [], "refresh_period": self._refresh_period})

    def _load_payload(self) -> dict[str, Any]:
        self._ensure_file()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        pairs = payload.get("pairs")
        if not isinstance(pairs, list):
            pairs = []
        refresh_period = payload.get("refresh_period")
        if not isinstance(refresh_period, int) or refresh_period <= 0:
            refresh_period = self._refresh_period
        return {"pairs": pairs, "refresh_period": refresh_period}

    def _write_payload(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(self._path.parent),
            suffix=".tmp",
        ) as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            tmp_path = Path(handle.name)
        tmp_path.replace(self._path)


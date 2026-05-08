from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.parser_v2.profiles.registry import canonicalize_trader_v2, list_parser_v2_profiles
from src.storage.raw_messages import RawMessageStore
from src.telegram.effective_trader import EffectiveTraderResolver
from src.telegram.trader_mapping import TelegramSourceTraderMapper

_TRADER_ALIASES_PATH = PROJECT_ROOT / "config" / "trader_aliases.json"
_TELEGRAM_SOURCE_MAP_PATH = PROJECT_ROOT / "config" / "telegram_source_map.json"


def _load_json_file(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_known_trader_ids() -> set[str]:
    known = {t.strip().lower() for t in list_parser_v2_profiles()}
    payload = _load_json_file(_TRADER_ALIASES_PATH)
    aliases = payload.get("aliases", {})
    if isinstance(aliases, dict):
        for v in aliases.values():
            if isinstance(v, str):
                n = v.strip().lower()
                if n:
                    known.add(n)
    return known


def normalize_trader_id(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return canonicalize_trader_v2(stripped) or stripped.lower()


def build_trader_resolver(db_path: str) -> EffectiveTraderResolver:
    payload = _load_json_file(_TRADER_ALIASES_PATH)
    trader_aliases = payload.get("aliases", {})
    if not isinstance(trader_aliases, dict):
        trader_aliases = {}
    known_trader_ids = load_known_trader_ids()
    source_mapper = TelegramSourceTraderMapper.from_json_file(
        str(_TELEGRAM_SOURCE_MAP_PATH),
        trader_aliases={str(k): str(v) for k, v in trader_aliases.items()},
        known_trader_ids=known_trader_ids,
    )
    return EffectiveTraderResolver(
        source_mapper=source_mapper,
        raw_store=RawMessageStore(db_path=db_path),
        trader_aliases={str(k): str(v) for k, v in trader_aliases.items()},
        known_trader_ids=known_trader_ids,
    )


__all__ = ["normalize_trader_id", "load_known_trader_ids", "build_trader_resolver"]

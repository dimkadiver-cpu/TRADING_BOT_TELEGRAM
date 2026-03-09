"""Config loader.

Loads JSON config files and provides a structured object.
See README.md, docs/MASTER_PLAN.md and docs/CONFIG_SCHEMA.md.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict
import json, os

from src.core.trader_tags import normalize_trader_aliases

@dataclass(frozen=True)
class Config:
    trader_aliases: Dict[str, str]
    portfolio_rules: Dict[str, Any]
    traders: Dict[str, Dict[str, Any]]  # trader_id -> {parsing, execution}

def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_config(root_dir: str = ".") -> Config:
    # TODO: implement robust validation per docs/CONFIG_SCHEMA.md
    aliases = normalize_trader_aliases(load_json(os.path.join(root_dir, "config", "trader_aliases.json"))["aliases"])
    portfolio = load_json(os.path.join(root_dir, "config", "portfolio_rules.json"))
    traders = {}
    traders_dir = os.path.join(root_dir, "traders")
    for tid in os.listdir(traders_dir):
        tpath = os.path.join(traders_dir, tid)
        if not os.path.isdir(tpath):
            continue
        parsing = load_json(os.path.join(tpath, "parsing_rules.json"))
        execution = load_json(os.path.join(tpath, "execution_rules.json"))
        traders[tid] = {"parsing": parsing, "execution": execution}
    return Config(trader_aliases=aliases, portfolio_rules=portfolio, traders=traders)

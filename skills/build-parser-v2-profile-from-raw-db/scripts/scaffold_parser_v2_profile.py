from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PROFILE_TEMPLATE = """from __future__ import annotations

import json
from pathlib import Path

from .intent_entity_extractor import IntentEntityExtractor
from .signal_extractor import SignalExtractor


class {class_name}:
    trader_code = "{trader_code}"

    def __init__(self) -> None:
        self._base_dir = Path(__file__).resolve().parent
        self._signal_extractor = SignalExtractor()
        self._intent_entity_extractor = IntentEntityExtractor()

    def load_markers(self):
        return json.loads((self._base_dir / "semantic_markers.json").read_text(encoding="utf-8"))

    def load_rules(self):
        return json.loads((self._base_dir / "rules.json").read_text(encoding="utf-8"))

    def extract_signal(self, text, context):
        return self._signal_extractor.extract(text=text, context=context)

    def extract_intent_entities(self, text, context, classification_hint=None, signal_hint=None):
        return self._intent_entity_extractor.extract(
            text=text,
            context=context,
            classification_hint=classification_hint,
            signal_hint=signal_hint,
        )
"""


SIGNAL_EXTRACTOR_TEMPLATE = """from __future__ import annotations


class SignalExtractor:
    def extract(self, text, context):
        # Replace with trader-specific signal extraction logic backed by raw_messages evidence.
        return None
"""


INTENT_EXTRACTOR_TEMPLATE = """from __future__ import annotations


class IntentEntityExtractor:
    def extract(self, text, context, classification_hint=None, signal_hint=None):
        # Replace with trader-specific intent/entity extraction logic backed by raw_messages evidence.
        return {}
"""


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _upsert_registry(registry_path: Path, trader_code: str, class_name: str) -> None:
    text = registry_path.read_text(encoding="utf-8")

    import_line = f"from src.parser_v2.profiles.{trader_code}.profile import {class_name}"
    if import_line not in text:
        anchor = "from src.parser_v2.profiles.trader_prova.profile import TraderProvaProfile"
        if anchor in text:
            text = text.replace(anchor, anchor + "\n" + import_line)
        else:
            first_profile_import = re.search(
                r"from src\.parser_v2\.profiles\.[^.]+\.profile import .+\n",
                text,
            )
            if first_profile_import:
                idx = first_profile_import.end()
                text = text[:idx] + import_line + "\n" + text[idx:]
            else:
                text = import_line + "\n" + text

    factory_entry = f'    "{trader_code}": {class_name},'
    if factory_entry not in text:
        text = text.replace(
            "_PROFILE_FACTORIES: dict[str, type] = {\n",
            "_PROFILE_FACTORIES: dict[str, type] = {\n" + factory_entry + "\n",
            1,
        )

    alias_entries = [f'    "{trader_code}": "{trader_code}",']
    if trader_code.startswith("trader_"):
        short_alias = trader_code[len("trader_") :]
        if short_alias:
            alias_entries.insert(0, f'    "{short_alias}": "{trader_code}",')

    for alias_entry in reversed(alias_entries):
        if alias_entry not in text:
            text = text.replace(
                "_ALIASES: dict[str, str] = {\n",
                "_ALIASES: dict[str, str] = {\n" + alias_entry + "\n",
                1,
            )

    registry_path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scaffold a current parser_v2 profile directory.")
    parser.add_argument("--trader-code", required=True, help="Directory/trader code, e.g. trader_x.")
    parser.add_argument("--class-name", required=True, help="Profile class name, e.g. TraderXProfile.")
    parser.add_argument(
        "--base-dir",
        default="src/parser_v2/profiles",
        help="Base profiles directory. Defaults to src/parser_v2/profiles.",
    )
    parser.add_argument(
        "--registry-path",
        default="src/parser_v2/profiles/registry.py",
        help="Registry file to update. Defaults to src/parser_v2/profiles/registry.py.",
    )
    args = parser.parse_args()

    profile_dir = Path(args.base_dir) / args.trader_code
    profile_dir.mkdir(parents=True, exist_ok=True)

    (profile_dir / "__init__.py").write_text("", encoding="utf-8")
    (profile_dir / "profile.py").write_text(
        PROFILE_TEMPLATE.format(class_name=args.class_name, trader_code=args.trader_code),
        encoding="utf-8",
    )
    (profile_dir / "signal_extractor.py").write_text(SIGNAL_EXTRACTOR_TEMPLATE, encoding="utf-8")
    (profile_dir / "intent_entity_extractor.py").write_text(INTENT_EXTRACTOR_TEMPLATE, encoding="utf-8")

    _write_json(
        profile_dir / "semantic_markers.json",
        {
            "classification_markers": {},
            "intent_markers": {},
            "entity_markers": {},
        },
    )
    _write_json(
        profile_dir / "rules.json",
        {
            "notes": [
                "Replace this placeholder with rules grounded on raw_messages evidence.",
            ]
        },
    )
    _upsert_registry(Path(args.registry_path), trader_code=args.trader_code, class_name=args.class_name)

    print(f"Scaffolded profile at: {profile_dir}")
    print(f"Updated registry: {args.registry_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

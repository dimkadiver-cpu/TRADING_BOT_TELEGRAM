#!/usr/bin/env python3
"""Build a draft parser spec from labeled examples (text + comment).

Input formats:
- CSV with columns: text, comment
- JSONL with keys: text, comment
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


COMMENT_TO_INTENT = {
    "move stop": "U_MOVE_STOP",
    "breakeven": "U_MOVE_STOP_TO_BE",
    "move to be": "U_MOVE_STOP_TO_BE",
    "cancel pending": "U_CANCEL_PENDING_ORDERS",
    "cancel limit": "U_CANCEL_PENDING_ORDERS",
    "close all": "U_CLOSE_FULL",
    "close full": "U_CLOSE_FULL",
    "partial close": "U_CLOSE_PARTIAL",
    "tp hit": "U_TP_HIT",
    "stop hit": "U_STOP_HIT",
    "filled": "U_MARK_FILLED",
    "result": "U_REPORT_FINAL_RESULT",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate parser spec draft from text/comment dataset")
    parser.add_argument("--input", required=True, help="Path to .csv or .jsonl")
    parser.add_argument("--trader-id", required=True, help="Canonical trader id (e.g. trader_a)")
    parser.add_argument("--out", default=None, help="Optional output JSON file path")
    return parser.parse_args()


def read_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            return [dict(r) for r in reader]
    if suffix == ".jsonl":
        out: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                out.append(json.loads(line))
        return out
    raise ValueError(f"Unsupported input format: {path}")


def detect_candidate_message_type(text: str) -> str:
    low = text.lower()
    has_entry = "entry" in low or "вход" in low
    has_sl = "sl" in low or "stop" in low or "стоп" in low
    has_tp = "tp" in low or "тейк" in low or "target" in low
    if has_entry and has_sl and has_tp:
        return "NEW_SIGNAL"
    if any(k in low for k in ["close", "cancel", "move stop", "tp hit", "stopped"]):
        return "UPDATE"
    return "UNCLASSIFIED"


def derive_intents_from_comment(comment: str) -> list[str]:
    low = comment.lower()
    found: list[str] = []
    for marker, intent in COMMENT_TO_INTENT.items():
        if marker in low and intent not in found:
            found.append(intent)
    return found


def main() -> None:
    args = parse_args()
    in_path = Path(args.input)
    rows = read_rows(in_path)

    total = 0
    missing_text = 0
    missing_comment = 0
    guessed_types: Counter[str] = Counter()
    intent_counts: Counter[str] = Counter()
    marker_examples: dict[str, list[str]] = defaultdict(list)

    for row in rows:
        text = str(row.get("text") or "").strip()
        comment = str(row.get("comment") or "").strip()
        if not text:
            missing_text += 1
            continue
        total += 1
        if not comment:
            missing_comment += 1
        guessed = detect_candidate_message_type(text)
        guessed_types[guessed] += 1

        intents = derive_intents_from_comment(comment)
        for intent in intents:
            intent_counts[intent] += 1
            if len(marker_examples[intent]) < 5:
                marker_examples[intent].append(text[:200])

    spec = {
        "trader_id": args.trader_id,
        "dataset": {
            "input_path": str(in_path),
            "rows_total": len(rows),
            "rows_usable": total,
            "rows_missing_text": missing_text,
            "rows_missing_comment": missing_comment,
        },
        "classification_hints": {
            "guessed_message_types": dict(guessed_types),
            "candidate_markers": {
                "new_signal_strong": ["entry", "sl", "tp", "вход", "стоп", "тейк"],
                "update_strong": ["move stop", "close", "cancel", "tp hit", "stopped"],
            },
        },
        "intent_hints": {
            "counts": dict(intent_counts),
            "examples": marker_examples,
        },
        "canonical_schema_required": [
            "event_type",
            "trader_id",
            "source_chat_id",
            "source_message_id",
            "raw_text",
            "parser_mode",
            "confidence",
            "instrument",
            "side",
            "market_type",
            "entries",
            "stop_loss",
            "take_profits",
            "root_ref",
            "status",
        ],
        "next_steps": [
            "Map comment taxonomy -> deterministic intent markers",
            "Implement/extend trader profile parser",
            "Add golden tests per message type and ambiguous cases",
            "Validate normalization warnings and weak-link behavior",
        ],
    }

    rendered = json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"written: {out_path}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()

"""Replay-quality report for Trader A parser v2 semantics.

Usage:
  PYTHONPATH=. python src/parser/report_trader_a_v2_quality.py \
    --input WORKFLOW_PARSING_SEMPLICE/TEST/trader_a_all_messages.csv \
    --output WORKFLOW_PARSING_SEMPLICE/TEST/reports/trader_a_v2_quality_report.json
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from src.parser.pipeline import MinimalParserPipeline, ParserInput


def _safe_json_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_json_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _pct(n: int, d: int) -> float:
    if d <= 0:
        return 0.0
    return round((n / d) * 100.0, 2)


def _sample_entry(item: dict[str, Any]) -> dict[str, Any]:
    raw_text = str(item.get("raw_text") or "")
    return {
        "message_id": item.get("source_message_id"),
        "message_class": item.get("message_class"),
        "primary_intent": item.get("primary_intent"),
        "warnings": item.get("validation_warnings", []),
        "raw_text": raw_text[:240],
    }


def build_report(messages: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(messages)
    by_class = Counter(str(m.get("message_class") or "UNCLASSIFIED") for m in messages)
    primary_intents = Counter(str(m.get("primary_intent") or "") for m in messages if m.get("primary_intent"))

    primary_intent_pop = sum(1 for m in messages if m.get("primary_intent"))
    actions_structured_pop = sum(1 for m in messages if _safe_json_list(m.get("actions_structured")))
    target_scope_pop = sum(1 for m in messages if _safe_json_dict(m.get("target_scope")))
    results_v2_pop = sum(1 for m in messages if _safe_json_list(m.get("results_v2")))

    new_signal = [m for m in messages if m.get("message_class") == "NEW_SIGNAL"]
    updates = [m for m in messages if m.get("message_class") == "UPDATE"]

    new_signal_entry_plan_entries = sum(
        1 for m in new_signal if _safe_json_list(_safe_json_dict(m.get("entry_plan")).get("entries"))
    )
    new_signal_risk_sl = sum(
        1 for m in new_signal if _safe_json_dict(m.get("risk_plan")).get("stop_loss")
    )
    new_signal_risk_tp = sum(
        1 for m in new_signal if _safe_json_dict(m.get("risk_plan")).get("take_profits")
    )

    fallback_fields = [
        "actions_structured",
        "instrument_obj",
        "position_obj",
        "entry_plan",
        "risk_plan",
        "results_v2",
        "target_scope",
        "linking",
    ]
    fallback_counts = Counter()
    for m in messages:
        diag = _safe_json_dict(m.get("diagnostics"))
        fb = _safe_json_dict(diag.get("v2_fallbacks_used"))
        for key in fallback_fields:
            if bool(fb.get(key)):
                fallback_counts[key] += 1

    update_with_primary = sum(1 for m in updates if m.get("primary_intent"))
    update_with_actions = sum(1 for m in updates if _safe_json_list(m.get("actions_structured")))
    update_avg_actions = round(
        sum(len(_safe_json_list(m.get("actions_structured"))) for m in updates) / len(updates), 4
    ) if updates else 0.0

    update_action_types = Counter()
    update_applies_to_ok = 0
    update_applies_to_total = 0
    for m in updates:
        for action in _safe_json_list(m.get("actions_structured")):
            if isinstance(action, dict):
                action_type = action.get("action_type") or action.get("action")
                if action_type:
                    update_action_types[str(action_type)] += 1
                applies_to = action.get("applies_to")
                update_applies_to_total += 1
                if isinstance(applies_to, dict) and "scope_type" in applies_to and "scope_value" in applies_to:
                    update_applies_to_ok += 1

    ns_symbol = sum(1 for m in new_signal if _safe_json_dict(m.get("instrument_obj")).get("symbol"))
    ns_side = sum(1 for m in new_signal if _safe_json_dict(m.get("position_obj")).get("side"))

    results_candidates = [m for m in messages if _safe_json_list(m.get("reported_results")) or "result" in str(m.get("raw_text") or "").lower() or " r" in str(m.get("raw_text") or "").lower()]
    results_non_empty = sum(1 for m in results_candidates if _safe_json_list(m.get("results_v2")))
    units = Counter()
    direction_pop = 0
    raw_fragment_pop = 0
    total_result_items = 0
    for m in results_candidates:
        for item in _safe_json_list(m.get("results_v2")):
            if not isinstance(item, dict):
                continue
            total_result_items += 1
            unit = item.get("unit")
            if unit:
                units[str(unit)] += 1
            if item.get("direction"):
                direction_pop += 1
            if item.get("raw_fragment"):
                raw_fragment_pop += 1

    sample_update_no_primary = []
    sample_update_empty_actions = []
    sample_ns_no_entries = []
    sample_target_scope_fallback = []
    sample_results_v2_fallback = []

    for m in messages:
        if m.get("message_class") == "UPDATE" and not m.get("primary_intent") and len(sample_update_no_primary) < 5:
            sample_update_no_primary.append(_sample_entry(m))
        if m.get("message_class") == "UPDATE" and not _safe_json_list(m.get("actions_structured")) and len(sample_update_empty_actions) < 5:
            sample_update_empty_actions.append(_sample_entry(m))
        if m.get("message_class") == "NEW_SIGNAL" and not _safe_json_list(_safe_json_dict(m.get("entry_plan")).get("entries")) and len(sample_ns_no_entries) < 5:
            sample_ns_no_entries.append(_sample_entry(m))

        fb = _safe_json_dict(_safe_json_dict(m.get("diagnostics")).get("v2_fallbacks_used"))
        if fb.get("target_scope") and len(sample_target_scope_fallback) < 5:
            sample_target_scope_fallback.append(_sample_entry(m))
        if fb.get("results_v2") and len(sample_results_v2_fallback) < 5:
            sample_results_v2_fallback.append(_sample_entry(m))

    return {
        "total_messages": total,
        "distribution": {
            "message_class": dict(by_class),
            "primary_intent_top": primary_intents.most_common(15),
        },
        "v2_population_quality": {
            "primary_intent_populated": {"count": primary_intent_pop, "pct": _pct(primary_intent_pop, total)},
            "actions_structured_non_empty": {"count": actions_structured_pop, "pct": _pct(actions_structured_pop, total)},
            "target_scope_populated": {"count": target_scope_pop, "pct": _pct(target_scope_pop, total)},
            "results_v2_populated": {"count": results_v2_pop, "pct": _pct(results_v2_pop, total)},
            "new_signal_entry_plan_entries": {"count": new_signal_entry_plan_entries, "pct": _pct(new_signal_entry_plan_entries, len(new_signal))},
            "new_signal_risk_plan_stop_loss": {"count": new_signal_risk_sl, "pct": _pct(new_signal_risk_sl, len(new_signal))},
            "new_signal_risk_plan_take_profits": {"count": new_signal_risk_tp, "pct": _pct(new_signal_risk_tp, len(new_signal))},
        },
        "v2_fallback_observability": {
            key: {"count": fallback_counts.get(key, 0), "pct": _pct(fallback_counts.get(key, 0), total)}
            for key in fallback_fields
        },
        "update_quality": {
            "total_updates": len(updates),
            "with_primary_intent": {"count": update_with_primary, "pct": _pct(update_with_primary, len(updates))},
            "with_actions_structured": {"count": update_with_actions, "pct": _pct(update_with_actions, len(updates))},
            "avg_actions_structured": update_avg_actions,
            "top_action_types": update_action_types.most_common(15),
            "applies_to_dict_shape": {"count": update_applies_to_ok, "pct": _pct(update_applies_to_ok, update_applies_to_total)},
        },
        "new_signal_quality": {
            "total_new_signal": len(new_signal),
            "instrument_symbol": {"count": ns_symbol, "pct": _pct(ns_symbol, len(new_signal))},
            "position_side": {"count": ns_side, "pct": _pct(ns_side, len(new_signal))},
            "entry_plan_entries": {"count": new_signal_entry_plan_entries, "pct": _pct(new_signal_entry_plan_entries, len(new_signal))},
            "risk_plan_stop_loss": {"count": new_signal_risk_sl, "pct": _pct(new_signal_risk_sl, len(new_signal))},
            "risk_plan_take_profits": {"count": new_signal_risk_tp, "pct": _pct(new_signal_risk_tp, len(new_signal))},
        },
        "results_quality": {
            "total_candidates": len(results_candidates),
            "with_results_v2": {"count": results_non_empty, "pct": _pct(results_non_empty, len(results_candidates))},
            "top_units": units.most_common(10),
            "direction_populated_pct": _pct(direction_pop, total_result_items),
            "raw_fragment_populated_pct": _pct(raw_fragment_pop, total_result_items),
        },
        "samples": {
            "update_without_primary_intent": sample_update_no_primary,
            "update_empty_actions_structured": sample_update_empty_actions,
            "new_signal_without_entry_plan_entries": sample_ns_no_entries,
            "target_scope_fallback_used": sample_target_scope_fallback,
            "results_v2_fallback_used": sample_results_v2_fallback,
        },
    }


def _print_summary(report: dict[str, Any]) -> None:
    print("\n=== Trader A Parser V2 Replay Quality Report ===")
    print(f"Total messages: {report['total_messages']}")

    print("\n[Overall distribution]")
    print("message_class:", report["distribution"]["message_class"])
    print("top primary_intent:", report["distribution"]["primary_intent_top"][:10])

    print("\n[V2 population quality]")
    for key, value in report["v2_population_quality"].items():
        print(f"- {key}: {value['count']} ({value['pct']}%)")

    print("\n[Fallback usage]")
    for key, value in report["v2_fallback_observability"].items():
        print(f"- {key}: {value['count']} ({value['pct']}%)")

    print("\n[Update quality]")
    uq = report["update_quality"]
    print("total_updates:", uq["total_updates"])
    print("with_primary_intent:", uq["with_primary_intent"])
    print("with_actions_structured:", uq["with_actions_structured"])
    print("avg_actions_structured:", uq["avg_actions_structured"])
    print("top_action_types:", uq["top_action_types"][:10])
    print("applies_to_dict_shape:", uq["applies_to_dict_shape"])

    print("\n[New-signal quality]")
    for key, value in report["new_signal_quality"].items():
        if isinstance(value, dict):
            print(f"- {key}: {value['count']} ({value['pct']}%)")
        else:
            print(f"- {key}: {value}")

    print("\n[Results quality]")
    rq = report["results_quality"]
    print("total_candidates:", rq["total_candidates"])
    print("with_results_v2:", rq["with_results_v2"])
    print("top_units:", rq["top_units"])
    print("direction_populated_pct:", rq["direction_populated_pct"])
    print("raw_fragment_populated_pct:", rq["raw_fragment_populated_pct"])

    print("\n[Sample problematic messages]")
    for key, sample in report["samples"].items():
        print(f"- {key}: {len(sample)} sample(s)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="WORKFLOW_PARSING_SEMPLICE/TEST/trader_a_all_messages.csv")
    parser.add_argument("--output", default="WORKFLOW_PARSING_SEMPLICE/TEST/reports/trader_a_v2_quality_report.json")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pipeline = MinimalParserPipeline(trader_aliases={"A": "A", "TA": "TA", "trader_a": "trader_a", "TB": "TB"})

    messages: list[dict[str, Any]] = []
    with in_path.open("r", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        for idx, row in enumerate(reader, start=1):
            if args.limit and idx > args.limit:
                break
            raw_text = str(row.get("raw_text") or "")
            raw_message_id = int(row.get("raw_message_id") or idx)
            source_message_id = int(row.get("telegram_message_id") or raw_message_id)
            reply_to = row.get("reply_to_message_id")
            linkage_reference_id = int(reply_to) if str(reply_to or "").isdigit() else None
            linkage_method = "direct_reply" if linkage_reference_id is not None else None

            record = pipeline.parse(
                ParserInput(
                    raw_message_id=raw_message_id,
                    raw_text=raw_text,
                    eligibility_status="ACQUIRED_ELIGIBLE",
                    eligibility_reason="replay_report",
                    resolved_trader_id="trader_a",
                    trader_resolution_method="replay",
                    linkage_method=linkage_method,
                    source_chat_id=str(row.get("source_chat_id") or ""),
                    source_message_id=source_message_id,
                    linkage_reference_id=linkage_reference_id,
                )
            )
            normalized = json.loads(record.parse_result_normalized_json or "{}")
            messages.append(normalized)

    report = build_report(messages)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(report)
    print(f"\nJSON report saved to: {out_path}")


if __name__ == "__main__":
    main()

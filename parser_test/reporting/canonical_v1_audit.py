from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.parser.canonical_v1.normalizer import normalize
from src.parser.trader_profiles.base import ParserContext, TraderParseResult
from src.parser.trader_profiles.common_utils import extract_hashtags, extract_telegram_links
from src.parser.trader_profiles.registry import canonicalize_trader_code


_LEGACY_TO_CANONICAL_CLASS = {
    "NEW_SIGNAL": "SIGNAL",
    "SETUP_INCOMPLETE": "SIGNAL",
    "UPDATE": "UPDATE",
    "INFO_ONLY": "INFO",
    "INFO": "INFO",
}

_MAPPED_INTENTS: frozenset[str] = frozenset({
    "NS_CREATE_SIGNAL",
    "U_MOVE_STOP",
    "U_UPDATE_STOP",
    "U_MOVE_STOP_TO_BE",
    "U_CLOSE_FULL",
    "U_CLOSE_PARTIAL",
    "U_CANCEL_PENDING_ORDERS",
    "U_REMOVE_PENDING_ENTRY",
    "U_ADD_ENTRY",
    "U_REENTER",
    "U_UPDATE_TAKE_PROFITS",
    "U_INVALIDATE_SETUP",
    "U_REVERSE_SIGNAL",
    "U_TP_HIT",
    "U_STOP_HIT",
    "U_REPORT_FINAL_RESULT",
    "U_ACTIVATION",
    "U_MARK_FILLED",
    "U_EXIT_BE",
    "U_RISK_NOTE",
})


@dataclass(frozen=True, slots=True)
class CanonicalAuditRow:
    raw_message_id: int
    trader_id: str
    legacy_message_type: str
    canonical_primary_class: str
    canonical_parse_status: str
    canonical_confidence: float
    normalizer_error: str | None
    class_mismatch: bool
    unmapped_intents: list[str]
    normalizer_warnings: list[str]
    legacy_action_types: list[str]
    canonical_summary: str
    raw_text_preview: str


@dataclass(frozen=True, slots=True)
class CanonicalAuditResult:
    trader_filter: str | None
    generated_at: str
    total_rows: int
    canonical_valid_rows: int
    normalizer_error_rows: int
    class_mismatch_rows: int
    parse_status_counts: dict[str, int]
    primary_class_counts: dict[str, int]
    mismatch_counts: dict[str, int]
    unmapped_intent_counts: dict[str, int]
    output_dir: Path
    summary_path: Path
    rows_path: Path


def run_canonical_v1_audit(
    *,
    db_path: str | Path,
    output_dir: str | Path,
    trader: str | None = None,
    limit: int | None = None,
) -> CanonicalAuditResult:
    db_path_resolved = _resolve_path(db_path)
    output_dir_resolved = _resolve_path(output_dir)
    output_dir_resolved.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows = _load_audit_rows(db_path=db_path_resolved, trader=trader, limit=limit)

    parse_status_counts = Counter(row.canonical_parse_status for row in rows)
    primary_class_counts = Counter(row.canonical_primary_class for row in rows)
    normalizer_error_rows = sum(1 for row in rows if row.normalizer_error)
    mismatch_counts = Counter(
        f"{row.legacy_message_type}->{row.canonical_primary_class}"
        for row in rows
        if row.class_mismatch
    )
    unmapped_intent_counts = Counter(
        intent
        for row in rows
        for intent in row.unmapped_intents
    )

    safe_trader = trader or "all"
    timestamp = generated_at.replace(":", "").replace("-", "").replace("+00:00", "Z")
    rows_path = output_dir_resolved / f"{safe_trader}_canonical_v1_rows_{timestamp}.csv"
    summary_path = output_dir_resolved / f"{safe_trader}_canonical_v1_summary_{timestamp}.json"

    _write_rows_csv(rows_path, rows)
    _write_summary_json(
        summary_path,
        generated_at=generated_at,
        trader_filter=trader,
        total_rows=len(rows),
        canonical_valid_rows=len(rows) - normalizer_error_rows,
        normalizer_error_rows=normalizer_error_rows,
        class_mismatch_rows=sum(1 for row in rows if row.class_mismatch),
        parse_status_counts=parse_status_counts,
        primary_class_counts=primary_class_counts,
        mismatch_counts=mismatch_counts,
        unmapped_intent_counts=unmapped_intent_counts,
        rows=rows,
    )

    return CanonicalAuditResult(
        trader_filter=trader,
        generated_at=generated_at,
        total_rows=len(rows),
        canonical_valid_rows=len(rows) - normalizer_error_rows,
        normalizer_error_rows=normalizer_error_rows,
        class_mismatch_rows=sum(1 for row in rows if row.class_mismatch),
        parse_status_counts=dict(parse_status_counts),
        primary_class_counts=dict(primary_class_counts),
        mismatch_counts=dict(mismatch_counts),
        unmapped_intent_counts=dict(unmapped_intent_counts),
        output_dir=output_dir_resolved,
        summary_path=summary_path,
        rows_path=rows_path,
    )


def _load_audit_rows(
    *,
    db_path: Path,
    trader: str | None,
    limit: int | None,
) -> list[CanonicalAuditRow]:
    normalized_trader = canonicalize_trader_code(trader) if trader else None
    sql = """
    SELECT
      rm.raw_message_id,
      rm.source_chat_id,
      rm.telegram_message_id,
      rm.reply_to_message_id,
      rm.raw_text,
      pr.resolved_trader_id,
      pr.message_type,
      pr.parse_result_normalized_json
    FROM raw_messages rm
    JOIN parse_results pr ON pr.raw_message_id = rm.raw_message_id
    WHERE pr.parse_result_normalized_json IS NOT NULL
    """
    params: list[object] = []
    if normalized_trader:
        sql += " AND pr.resolved_trader_id = ?"
        params.append(normalized_trader)
    sql += " ORDER BY rm.message_ts ASC, rm.raw_message_id ASC"
    if limit is not None and limit > 0:
        sql += " LIMIT ?"
        params.append(limit)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        db_rows = conn.execute(sql, params).fetchall()

    rows: list[CanonicalAuditRow] = []
    for db_row in db_rows:
        normalized = _parse_json_object(db_row["parse_result_normalized_json"])
        trader_id = str(db_row["resolved_trader_id"] or "").strip()
        if not trader_id:
            continue

        context = ParserContext(
            trader_code=trader_id,
            message_id=int(db_row["telegram_message_id"]) if db_row["telegram_message_id"] is not None else None,
            reply_to_message_id=(
                int(db_row["reply_to_message_id"]) if db_row["reply_to_message_id"] is not None else None
            ),
            channel_id=str(db_row["source_chat_id"]) if db_row["source_chat_id"] is not None else None,
            raw_text=str(db_row["raw_text"] or ""),
            extracted_links=_context_links(str(db_row["raw_text"] or "")),
            hashtags=_context_hashtags(str(db_row["raw_text"] or "")),
        )
        result = _rebuild_trader_parse_result(
            normalized=normalized,
            legacy_message_type=str(db_row["message_type"] or ""),
        )
        legacy_message_type = str(db_row["message_type"] or "")
        intents = [str(item) for item in normalized.get("intents", []) if isinstance(item, str)]
        normalizer_error: str | None = None
        canonical_primary_class = "ERROR"
        canonical_parse_status = "ERROR"
        canonical_confidence = 0.0
        class_mismatch = False
        canonical_warnings: list[str] = []
        canonical_summary = "ERROR"
        try:
            canonical = normalize(result, context)
            expected_class = _LEGACY_TO_CANONICAL_CLASS.get(legacy_message_type.upper())
            canonical_primary_class = canonical.primary_class
            canonical_parse_status = canonical.parse_status
            canonical_confidence = float(canonical.confidence)
            class_mismatch = bool(expected_class and expected_class != canonical.primary_class)
            canonical_warnings = list(canonical.warnings or [])
            canonical_summary = _canonical_summary(canonical.model_dump(exclude_none=True))
        except Exception as exc:
            normalizer_error = f"{type(exc).__name__}: {exc}"
        rows.append(
            CanonicalAuditRow(
                raw_message_id=int(db_row["raw_message_id"]),
                trader_id=trader_id,
                legacy_message_type=legacy_message_type,
                canonical_primary_class=canonical_primary_class,
                canonical_parse_status=canonical_parse_status,
                canonical_confidence=canonical_confidence,
                normalizer_error=normalizer_error,
                class_mismatch=class_mismatch,
                unmapped_intents=[intent for intent in intents if intent not in _MAPPED_INTENTS],
                normalizer_warnings=canonical_warnings,
                legacy_action_types=_legacy_action_types(normalized),
                canonical_summary=canonical_summary,
                raw_text_preview=_preview_text(str(db_row["raw_text"] or "")),
            )
        )
    return rows


def _rebuild_trader_parse_result(
    *,
    normalized: dict[str, Any],
    legacy_message_type: str,
) -> TraderParseResult:
    intents = [
        str(item)
        for item in normalized.get("intents", [])
        if isinstance(item, str)
    ]
    return TraderParseResult(
        message_type=str(normalized.get("message_type") or legacy_message_type or ""),
        intents=intents,
        entities=dict(normalized.get("entities") or {}),
        target_refs=list(normalized.get("target_refs") or []),
        reported_results=list(normalized.get("reported_results") or []),
        warnings=[str(item) for item in normalized.get("warnings", []) if isinstance(item, str)],
        confidence=float(normalized.get("confidence") or 0.0),
        primary_intent=(
            str(normalized.get("primary_intent"))
            if normalized.get("primary_intent") is not None
            else None
        ),
        actions_structured=list(normalized.get("actions_structured") or []),
        target_scope=dict(normalized.get("target_scope") or {}),
        linking=dict(normalized.get("linking") or {}),
        diagnostics=dict(normalized.get("diagnostics") or {}),
    )


def _legacy_action_types(normalized: dict[str, Any]) -> list[str]:
    actions = normalized.get("actions_structured")
    if not isinstance(actions, list):
        return []
    values: list[str] = []
    for item in actions:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("action_type") or item.get("action") or item.get("type") or "").strip()
        if action_type and action_type not in values:
            values.append(action_type)
    return values


def _canonical_summary(canonical: dict[str, Any]) -> str:
    primary_class = str(canonical.get("primary_class") or "")
    if primary_class == "SIGNAL":
        signal = canonical.get("signal") if isinstance(canonical.get("signal"), dict) else {}
        symbol = str(signal.get("symbol") or "")
        side = str(signal.get("side") or "")
        entry_structure = str(signal.get("entry_structure") or "")
        return f"SIGNAL:{symbol}:{side}:{entry_structure}".strip(":")
    if primary_class == "UPDATE":
        update = canonical.get("update") if isinstance(canonical.get("update"), dict) else {}
        operations = update.get("operations") if isinstance(update.get("operations"), list) else []
        op_types = [str(item.get("op_type")) for item in operations if isinstance(item, dict) and item.get("op_type")]
        return "UPDATE:" + "|".join(op_types)
    if primary_class == "REPORT":
        report = canonical.get("report") if isinstance(canonical.get("report"), dict) else {}
        events = report.get("events") if isinstance(report.get("events"), list) else []
        event_types = [str(item.get("event_type")) for item in events if isinstance(item, dict) and item.get("event_type")]
        return "REPORT:" + "|".join(event_types)
    return primary_class or "INFO"


def _write_rows_csv(path: Path, rows: list[CanonicalAuditRow]) -> None:
    fieldnames = [
        "raw_message_id",
        "trader_id",
        "legacy_message_type",
        "canonical_primary_class",
        "canonical_parse_status",
        "canonical_confidence",
        "normalizer_error",
        "class_mismatch",
        "unmapped_intents",
        "normalizer_warnings",
        "legacy_action_types",
        "canonical_summary",
        "raw_text_preview",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "raw_message_id": row.raw_message_id,
                    "trader_id": row.trader_id,
                    "legacy_message_type": row.legacy_message_type,
                    "canonical_primary_class": row.canonical_primary_class,
                    "canonical_parse_status": row.canonical_parse_status,
                    "canonical_confidence": row.canonical_confidence,
                    "normalizer_error": row.normalizer_error or "",
                    "class_mismatch": row.class_mismatch,
                    "unmapped_intents": " | ".join(row.unmapped_intents),
                    "normalizer_warnings": " | ".join(row.normalizer_warnings),
                    "legacy_action_types": " | ".join(row.legacy_action_types),
                    "canonical_summary": row.canonical_summary,
                    "raw_text_preview": row.raw_text_preview,
                }
            )


def _write_summary_json(
    path: Path,
    *,
    generated_at: str,
    trader_filter: str | None,
    total_rows: int,
    canonical_valid_rows: int,
    normalizer_error_rows: int,
    class_mismatch_rows: int,
    parse_status_counts: Counter[str],
    primary_class_counts: Counter[str],
    mismatch_counts: Counter[str],
    unmapped_intent_counts: Counter[str],
    rows: list[CanonicalAuditRow],
) -> None:
    payload = {
        "generated_at": generated_at,
        "trader_filter": trader_filter,
        "total_rows": total_rows,
        "canonical_valid_rows": canonical_valid_rows,
        "canonical_valid_pct": _pct(canonical_valid_rows, total_rows),
        "normalizer_error_rows": normalizer_error_rows,
        "normalizer_error_pct": _pct(normalizer_error_rows, total_rows),
        "class_mismatch_rows": class_mismatch_rows,
        "class_mismatch_pct": _pct(class_mismatch_rows, total_rows),
        "parse_status_counts": dict(parse_status_counts),
        "primary_class_counts": dict(primary_class_counts),
        "mismatch_counts": dict(mismatch_counts),
        "unmapped_intent_counts": dict(unmapped_intent_counts),
        "normalizer_error_examples": [
            {
                "raw_message_id": row.raw_message_id,
                "legacy_message_type": row.legacy_message_type,
                "normalizer_error": row.normalizer_error,
                "raw_text_preview": row.raw_text_preview,
            }
            for row in rows
            if row.normalizer_error
        ][:20],
        "mismatch_examples": [
            {
                "raw_message_id": row.raw_message_id,
                "legacy_message_type": row.legacy_message_type,
                "canonical_primary_class": row.canonical_primary_class,
                "canonical_summary": row.canonical_summary,
            }
            for row in rows
            if row.class_mismatch
        ][:20],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _context_links(raw_text: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for link in extract_telegram_links(raw_text):
        normalized = link if link.startswith("http") else f"https://{link}"
        if normalized in seen:
            continue
        seen.add(normalized)
        links.append(normalized)
    return links


def _context_hashtags(raw_text: str) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for tag in extract_hashtags(raw_text):
        rendered = f"#{tag}"
        lowered = rendered.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        tags.append(rendered)
    return tags


def _preview_text(value: str, *, limit: int = 160) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _pct(value: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((value / total) * 100.0, 2)


def _resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()

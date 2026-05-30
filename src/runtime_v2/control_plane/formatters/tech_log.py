from __future__ import annotations

import json

_SEP = "────────────────"


def _stringify_details(details) -> str:
    if details is None:
        return ""
    if isinstance(details, str):
        return details
    return json.dumps(details, ensure_ascii=False, sort_keys=True)


def format_tech_log(payload: dict, *, delivery_mode: str = "supergroup_topics") -> str:
    level = str(payload.get("level", "INFO")).upper()
    category = payload.get("category") or "Runtime"
    description = payload.get("description") or ""
    source = payload.get("source")
    details = _stringify_details(payload.get("details"))

    lines = [f"[{level}] {category}", _SEP]
    if description:
        lines.append(description)
    if details:
        lines.extend(["", f"Details: {details}"])
    if source:
        lines.extend(["", f"Source: {source}"])

    body = "\n".join(lines).strip()
    if delivery_mode == "private_bot":
        return f"⚠️ --SYSTEM--\n{body}"
    return body


__all__ = ["format_tech_log"]

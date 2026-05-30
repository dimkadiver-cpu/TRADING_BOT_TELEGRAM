from __future__ import annotations

_SEP = "────────────────"


def format_tech_log(payload: dict, *, delivery_mode: str = "supergroup_topics") -> str:
    level = str(payload.get("level", "INFO")).upper()
    category = payload.get("category") or "Runtime"
    title = payload.get("title") or ""
    description = payload.get("description") or ""
    source = payload.get("source")
    context = payload.get("context")  # dict | None
    action = payload.get("action")    # str | None

    header = f"[{level}] {category}: {title}" if title else f"[{level}] {category}"
    lines = [header, _SEP]
    if description:
        lines.append(description)
    if context and isinstance(context, dict):
        lines.extend(["", "Context:"])
        for key, value in context.items():
            lines.append(f"{key}: {value}")
    if action:
        lines.extend(["", f"Action: {action}"])
    if source:
        lines.extend([_SEP, f"Source: {source}"])

    body = "\n".join(lines).strip()
    if delivery_mode == "private_bot":
        return f"⚠️ --SYSTEM--\n{body}"
    return body


__all__ = ["format_tech_log"]

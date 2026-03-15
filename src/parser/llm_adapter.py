"""LLM parser adapter with configurable multi-provider backend.

The adapter always returns ParseResultNormalized and keeps all failures as
controlled exceptions so the dispatcher can apply fallback policies.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any, Callable

import requests

from src.parser.normalization import (
    ParseResultNormalized,
    build_parse_result_normalized,
    validate_parse_result_normalized,
)
from src.parser.parser_config import normalize_llm_model, normalize_llm_provider

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_LINK_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]+)")


class LLMNotConfigured(RuntimeError):
    """Raised when LLM mode is requested but not configured."""


class LLMRequestFailed(RuntimeError):
    """Raised when the LLM provider request fails."""


class LLMParseError(RuntimeError):
    """Raised when LLM parser fails."""


class LLMInvalidResponse(RuntimeError):
    """Raised when the LLM response is not valid JSON contract."""


@dataclass(slots=True)
class LLMSettings:
    enabled: bool
    provider: str
    model: str
    timeout_ms: int
    api_key: str | None
    api_base: str

    @classmethod
    def from_env(
        cls,
        *,
        enabled_override: bool | None = None,
        provider_override: str | None = None,
        model_override: str | None = None,
    ) -> "LLMSettings":
        enabled = _as_bool(os.getenv("LLM_ENABLED"), default=False)
        if enabled_override is not None:
            enabled = enabled_override

        provider = normalize_llm_provider(provider_override or os.getenv("LLM_PROVIDER"))
        model = normalize_llm_model(model_override or os.getenv("LLM_MODEL"), provider=provider)
        timeout_ms = _to_int(os.getenv("LLM_TIMEOUT_MS"), default=15000)

        if provider == "gemini":
            api_key = os.getenv("GEMINI_API_KEY")
            api_base = (os.getenv("GEMINI_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        else:
            api_key = os.getenv("OPENAI_API_KEY")
            api_base = (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")

        return cls(
            enabled=enabled,
            provider=provider,
            model=model,
            timeout_ms=timeout_ms,
            api_key=api_key,
            api_base=api_base,
        )


@dataclass(slots=True)
class LLMAdapter:
    enabled: bool | None = None
    request_fn: Callable[..., requests.Response] | None = None

    def parse_with_llm(self, parser_input: object, *, parser_mode: str) -> ParseResultNormalized:
        settings = LLMSettings.from_env(
            enabled_override=self.enabled,
            provider_override=getattr(parser_input, "llm_provider", None),
            model_override=getattr(parser_input, "llm_model", None),
        )
        self._validate_settings(settings)

        request_payload = _build_request_payload(parser_input)
        prompt = _build_user_prompt(request_payload)
        raw_response = self._invoke_model(prompt=prompt, settings=settings)
        llm_data = _parse_llm_json_response(raw_response)
        semantic = _coerce_semantic_payload(llm_data)

        result = build_parse_result_normalized(
            message_type=semantic["message_type"] or "UNCLASSIFIED",
            normalized_text=(parser_input.raw_text or "").lower(),
            trader_id=getattr(parser_input, "resolved_trader_id", None),
            source_chat_id=getattr(parser_input, "source_chat_id", None),
            source_message_id=getattr(parser_input, "source_message_id", None),
            raw_text=(parser_input.raw_text or "").strip(),
            parser_used="llm",
            parser_mode=parser_mode,
            parse_status="PARSED",
            instrument=semantic["symbol"],
            side=_direction_to_side(semantic["direction"]),
            entry_raw=_entry_raw_from_semantic(semantic),
            stop_raw=str(semantic["stop_loss_price"]) if semantic["stop_loss_price"] is not None else None,
            targets=[str(v) for v in semantic["take_profit_prices"]],
            root_ref=getattr(parser_input, "linkage_reference_id", None),
            existing_warnings=list(semantic["validation_warnings"]),
            notes=list(semantic["notes"]) + [f"llm_backend={settings.provider}", f"llm_model={settings.model}"],
            intents=list(semantic["intents"]),
            actions=list(semantic["actions"]),
            entities=dict(semantic["entities"]),
        )

        result.message_type = semantic["message_type"]
        result.intents = semantic["intents"]
        result.message_subtype = semantic["message_subtype"]
        result.symbol = semantic["symbol"]
        result.direction = semantic["direction"]
        result.entries = semantic["entries"]
        result.entry_main = semantic["entry_main"]
        result.entry_mode = semantic["entry_mode"]
        result.average_entry = semantic["average_entry"]
        result.stop_loss_price = semantic["stop_loss_price"]
        result.take_profit_prices = semantic["take_profit_prices"]
        result.actions = semantic["actions"]
        result.target_refs = semantic["target_refs"]
        result.reported_results = semantic["reported_results"]
        result.notes = semantic["notes"]
        result.entities = semantic["entities"]
        result.raw_entities = semantic["entities"]
        result.confidence = semantic["confidence"]

        result.instrument = result.symbol
        result.side = _direction_to_side(result.direction)
        result.stop_loss = _legacy_stop_loss(result)
        result.take_profits = _legacy_take_profits(result)

        warnings = list(result.validation_warnings)
        warnings.extend(semantic["validation_warnings"])
        warnings.extend(validate_parse_result_normalized(result))
        result.validation_warnings = _unique(warnings)
        if result.validation_warnings:
            result.status = "PARSED_WITH_WARNINGS"
        return result

    def _validate_settings(self, settings: LLMSettings) -> None:
        if not settings.enabled:
            raise LLMNotConfigured("LLM parser is disabled (LLM_ENABLED=false).")
        if settings.provider not in {"openai", "gemini"}:
            raise LLMNotConfigured(f"Unsupported LLM provider: {settings.provider}")
        if not settings.api_key:
            if settings.provider == "gemini":
                raise LLMNotConfigured("GEMINI_API_KEY is missing for LLM provider gemini.")
            raise LLMNotConfigured("OPENAI_API_KEY is missing for LLM provider openai.")
        if not settings.model:
            raise LLMNotConfigured("LLM_MODEL is missing.")

    def _invoke_model(self, *, prompt: str, settings: LLMSettings) -> str:
        if settings.provider == "gemini":
            return self._invoke_gemini(prompt=prompt, settings=settings)
        if settings.provider == "openai":
            return self._invoke_openai(prompt=prompt, settings=settings)
        raise LLMNotConfigured(f"Unsupported LLM provider: {settings.provider}")

    def _invoke_openai(self, *, prompt: str, settings: LLMSettings) -> str:
        url = f"{settings.api_base}/responses"
        payload = {
            "model": settings.model,
            "input": [
                {"role": "system", "content": [{"type": "text", "text": _system_prompt()}]},
                {"role": "user", "content": [{"type": "text", "text": prompt}]},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "telegram_parse_result",
                    "strict": True,
                    "schema": _json_schema(),
                }
            },
        }
        headers = {
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
        }
        requester = self.request_fn or requests.post
        try:
            response = requester(url, headers=headers, json=payload, timeout=max(1, settings.timeout_ms / 1000.0))
        except requests.RequestException as exc:
            raise LLMRequestFailed(f"LLM request failed: {exc}") from exc

        if response.status_code >= 400:
            raise LLMRequestFailed(f"LLM request failed: HTTP {response.status_code} {response.text[:240]}")

        try:
            body = response.json()
        except ValueError as exc:
            raise LLMInvalidResponse("LLM response is not JSON") from exc

        text = _extract_openai_response_text(body)
        if not text:
            raise LLMInvalidResponse("LLM response did not include text output")
        return text

    def _invoke_gemini(self, *, prompt: str, settings: LLMSettings) -> str:
        url = f"{settings.api_base}/models/{settings.model}:generateContent"
        payload = {
            "system_instruction": {"parts": [{"text": _system_prompt()}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        }
        headers = {
            "Content-Type": "application/json",
        }
        requester = self.request_fn or requests.post
        try:
            response = requester(
                url,
                headers=headers,
                params={"key": settings.api_key},
                json=payload,
                timeout=max(1, settings.timeout_ms / 1000.0),
            )
        except requests.RequestException as exc:
            raise LLMRequestFailed(f"LLM request failed: {exc}") from exc

        if response.status_code >= 400:
            raise LLMRequestFailed(f"LLM request failed: HTTP {response.status_code} {response.text[:240]}")

        try:
            body = response.json()
        except ValueError as exc:
            raise LLMInvalidResponse("LLM response is not JSON") from exc

        text = _extract_gemini_response_text(body)
        if not text:
            raise LLMInvalidResponse("LLM response did not include text output")
        return text


def _build_request_payload(parser_input: object) -> dict[str, Any]:
    raw_text = getattr(parser_input, "raw_text", None) or ""
    explicit_links = _extract_links(raw_text)
    explicit_hashtags = _extract_hashtags(raw_text)

    return {
        "trader_id": getattr(parser_input, "resolved_trader_id", None),
        "raw_text": raw_text,
        "source_chat_id": getattr(parser_input, "source_chat_id", None),
        "source_message_id": getattr(parser_input, "source_message_id", None),
        "root_ref": getattr(parser_input, "linkage_reference_id", None),
        "reply_to_message_id": getattr(parser_input, "reply_to_message_id", None)
        or getattr(parser_input, "linkage_reference_id", None),
        "links": explicit_links,
        "hashtags": explicit_hashtags,
        "hints": {
            "parser_mode": getattr(parser_input, "parser_mode", None),
        },
    }


def _build_user_prompt(payload: dict[str, Any]) -> str:
    return (
        "Input telegram message context as JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, sort_keys=True)}\n\n"
        "Return ONLY one valid JSON object with these keys exactly: "
        "message_type, intents, message_subtype, symbol, direction, entries, entry_main, entry_mode, average_entry, "
        "stop_loss_price, take_profit_prices, actions, target_refs, reported_results, notes, entities, "
        "confidence, validation_warnings."
    )


def _system_prompt() -> str:
    return (
        "You are a structured Telegram trading parser. Return ONLY valid JSON. "
        "Do not add prose. Do not invent missing values. Use null or [] when unsure. "
        "Classify message_type in NEW_SIGNAL, UPDATE, INFO_ONLY, SETUP_INCOMPLETE, UNCLASSIFIED. "
        "For UPDATE messages provide intents and canonical actions."
    )


def _json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "message_type": {"type": ["string", "null"]},
            "intents": {"type": "array", "items": {"type": "string"}},
            "message_subtype": {"type": ["string", "null"]},
            "symbol": {"type": ["string", "null"]},
            "direction": {"type": ["string", "null"]},
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "label": {"type": ["string", "null"]},
                        "price": {"type": ["number", "null"]},
                        "kind": {"type": ["string", "null"]},
                        "raw": {"type": ["string", "null"]},
                    },
                    "required": ["label", "price", "kind", "raw"],
                },
            },
            "entry_main": {"type": ["number", "null"]},
            "entry_mode": {"type": ["string", "null"]},
            "average_entry": {"type": ["number", "null"]},
            "stop_loss_price": {"type": ["number", "null"]},
            "take_profit_prices": {"type": "array", "items": {"type": "number"}},
            "actions": {"type": "array", "items": {"type": "string"}},
            "target_refs": {"type": "array", "items": {"type": "integer"}},
            "reported_results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "symbol": {"type": "string"},
                        "r_multiple": {"type": ["number", "null"]},
                    },
                    "required": ["symbol", "r_multiple"],
                },
            },
            "notes": {"type": "array", "items": {"type": "string"}},
            "entities": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                    "links": {"type": "array", "items": {"type": "string"}},
                    "time_hint": {"type": ["string", "null"]},
                },
                "required": ["hashtags", "links", "time_hint"],
            },
            "confidence": {"type": ["number", "null"]},
            "validation_warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "message_type",
            "intents",
            "message_subtype",
            "symbol",
            "direction",
            "entries",
            "entry_main",
            "entry_mode",
            "average_entry",
            "stop_loss_price",
            "take_profit_prices",
            "actions",
            "target_refs",
            "reported_results",
            "notes",
            "entities",
            "confidence",
            "validation_warnings",
        ],
    }


def _extract_openai_response_text(body: dict[str, Any]) -> str | None:
    output_text = body.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = body.get("output")
    if isinstance(output, list):
        chunks: list[str] = []
        for item in output:
            content = item.get("content") if isinstance(item, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        joined = "\n".join(chunks).strip()
        if joined:
            return joined
    return None


def _extract_gemini_response_text(body: dict[str, Any]) -> str | None:
    candidates = body.get("candidates")
    if not isinstance(candidates, list):
        return None

    chunks: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                chunks.append(text)

    joined = "\n".join(chunks).strip()
    return joined if joined else None


def _parse_llm_json_response(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = _JSON_BLOCK_RE.search(text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    raise LLMInvalidResponse("LLM response is not valid JSON object")


def _coerce_semantic_payload(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "message_type": _as_message_type(data.get("message_type")),
        "intents": _as_str_list(data.get("intents")),
        "message_subtype": _as_opt_str(data.get("message_subtype")),
        "symbol": _as_opt_str(data.get("symbol")),
        "direction": _as_direction(data.get("direction")),
        "entries": _as_entries(data.get("entries")),
        "entry_main": _as_opt_float(data.get("entry_main")),
        "entry_mode": _as_opt_str(data.get("entry_mode")),
        "average_entry": _as_opt_float(data.get("average_entry")),
        "stop_loss_price": _as_opt_float(data.get("stop_loss_price")),
        "take_profit_prices": _as_float_list(data.get("take_profit_prices")),
        "actions": _as_str_list(data.get("actions")),
        "target_refs": _as_int_list(data.get("target_refs")),
        "reported_results": _as_reported_results(data.get("reported_results")),
        "notes": _as_str_list(data.get("notes")),
        "entities": _as_raw_entities(data.get("entities") or data.get("raw_entities")),
        "confidence": _as_confidence(data.get("confidence")),
        "validation_warnings": _as_str_list(data.get("validation_warnings")),
    }

    if out["message_type"] is None:
        out["message_type"] = "UNCLASSIFIED"
        out["validation_warnings"].append("llm_missing_message_type")
    return out


def _direction_to_side(direction: str | None) -> str | None:
    if direction == "LONG":
        return "BUY"
    if direction == "SHORT":
        return "SELL"
    return None


def _entry_raw_from_semantic(payload: dict[str, Any]) -> str | None:
    entries = payload.get("entries") or []
    raws = [entry.get("raw") for entry in entries if isinstance(entry.get("raw"), str) and entry.get("raw")]
    if raws:
        return "-".join(raws)
    value = payload.get("entry_main")
    return str(value) if value is not None else None


def _legacy_stop_loss(result: ParseResultNormalized) -> dict[str, Any] | None:
    if result.stop_loss_price is None:
        return None
    return {
        "label": "SL",
        "price": result.stop_loss_price,
        "kind": "STOP_LOSS",
        "raw": str(result.stop_loss_price),
    }


def _legacy_take_profits(result: ParseResultNormalized) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for index, price in enumerate(result.take_profit_prices):
        values.append(
            {
                "label": f"TP{index + 1}",
                "price": price,
                "kind": "TAKE_PROFIT",
                "raw": str(price),
            }
        )
    return values


def _extract_links(text: str) -> list[str]:
    return [match.group(0) for match in _LINK_RE.finditer(text)]


def _extract_hashtags(text: str) -> list[str]:
    return [match.group(1) for match in _HASHTAG_RE.finditer(text)]


def _as_message_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    if candidate in {"NEW_SIGNAL", "UPDATE", "INFO_ONLY", "SETUP_INCOMPLETE", "UNCLASSIFIED"}:
        return candidate
    return None


def _as_direction(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    if candidate in {"LONG", "SHORT"}:
        return candidate
    return None


def _as_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "label": _as_opt_str(item.get("label")),
                "price": _as_opt_float(item.get("price")),
                "kind": _as_opt_str(item.get("kind")),
                "raw": _as_opt_str(item.get("raw")),
            }
        )
    return rows


def _as_reported_results(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        symbol = _as_opt_str(item.get("symbol"))
        if not symbol:
            continue
        rows.append({"symbol": symbol.upper(), "r_multiple": _as_opt_float(item.get("r_multiple"))})
    return rows


def _as_raw_entities(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"hashtags": [], "links": [], "time_hint": None}
    return {
        "hashtags": _as_str_list(value.get("hashtags")),
        "links": _as_str_list(value.get("links")),
        "time_hint": _as_opt_str(value.get("time_hint")),
    }


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _as_float_list(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    out: list[float] = []
    for item in value:
        converted = _as_opt_float(item)
        if converted is not None:
            out.append(converted)
    return out


def _as_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            out.append(item)
            continue
        if isinstance(item, str) and item.isdigit():
            out.append(int(item))
    return out


def _as_confidence(value: Any) -> float:
    numeric = _as_opt_float(value)
    if numeric is None:
        return 0.5
    return max(0.0, min(1.0, numeric))


def _as_opt_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _as_opt_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text else None


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output

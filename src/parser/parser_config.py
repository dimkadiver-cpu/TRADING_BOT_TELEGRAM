"""Parser mode configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Mapping

VALID_PARSER_MODES = {"regex_only", "llm_only", "hybrid_auto"}
VALID_LLM_PROVIDERS = {"openai", "gemini"}


@dataclass(slots=True)
class ParserModeResolver:
    global_parser_mode: str = "regex_only"
    trader_overrides: Mapping[str, str] | None = None
    global_llm_provider: str = "openai"
    trader_llm_provider_overrides: Mapping[str, str] | None = None
    global_llm_model: str | None = None
    trader_llm_model_overrides: Mapping[str, str] | None = None
    logger: logging.Logger | None = None

    def get_effective_parser_mode(self, trader_id: str | None) -> str:
        selected = self.global_parser_mode
        if trader_id and self.trader_overrides and trader_id in self.trader_overrides:
            selected = self.trader_overrides[trader_id]
        normalized = normalize_parser_mode(selected)
        if normalized != selected and self.logger is not None:
            self.logger.warning("invalid parser mode '%s', falling back to '%s'", selected, normalized)
        return normalized

    def get_effective_llm_provider(self, trader_id: str | None) -> str:
        selected = self.global_llm_provider
        if trader_id and self.trader_llm_provider_overrides and trader_id in self.trader_llm_provider_overrides:
            selected = self.trader_llm_provider_overrides[trader_id]
        normalized = normalize_llm_provider(selected)
        if normalized != selected and self.logger is not None:
            self.logger.warning("invalid llm provider '%s', falling back to '%s'", selected, normalized)
        return normalized

    def get_effective_llm_model(self, trader_id: str | None) -> str:
        selected = self.global_llm_model
        if trader_id and self.trader_llm_model_overrides and trader_id in self.trader_llm_model_overrides:
            selected = self.trader_llm_model_overrides[trader_id]
        provider = self.get_effective_llm_provider(trader_id)
        return normalize_llm_model(selected, provider=provider)


def normalize_parser_mode(value: str | None) -> str:
    if value is None:
        return "regex_only"
    normalized = value.strip().lower()
    if normalized in VALID_PARSER_MODES:
        return normalized
    return "regex_only"


def normalize_llm_provider(value: str | None) -> str:
    if value is None:
        return "openai"
    normalized = value.strip().lower()
    if normalized in VALID_LLM_PROVIDERS:
        return normalized
    return "openai"


def normalize_llm_model(value: str | None, *, provider: str | None = None) -> str:
    if value is not None:
        normalized = value.strip()
        if normalized:
            return normalized
    resolved_provider = normalize_llm_provider(provider)
    if resolved_provider == "gemini":
        return "gemini-2.5-flash"
    return "gpt-4.1-mini"


def build_trader_parser_mode_overrides(traders: Mapping[str, Mapping[str, object]] | None) -> dict[str, str]:
    if not traders:
        return {}
    overrides: dict[str, str] = {}
    for trader_id, payload in traders.items():
        parsing = payload.get("parsing") if isinstance(payload, Mapping) else None
        if isinstance(parsing, Mapping):
            profile_options = parsing.get("profile_options") if isinstance(parsing.get("profile_options"), Mapping) else {}
            mode = profile_options.get("parser_mode") if profile_options else parsing.get("parser_mode")
            normalized = normalize_parser_mode(str(mode) if mode is not None else None)
            if mode is not None:
                overrides[str(trader_id)] = normalized
    return overrides


def build_trader_llm_provider_overrides(traders: Mapping[str, Mapping[str, object]] | None) -> dict[str, str]:
    if not traders:
        return {}
    overrides: dict[str, str] = {}
    for trader_id, payload in traders.items():
        parsing = payload.get("parsing") if isinstance(payload, Mapping) else None
        if not isinstance(parsing, Mapping):
            continue
        profile_options = parsing.get("profile_options") if isinstance(parsing.get("profile_options"), Mapping) else {}
        provider = profile_options.get("llm_provider") if profile_options else parsing.get("llm_provider")
        if provider is None:
            continue
        overrides[str(trader_id)] = normalize_llm_provider(str(provider))
    return overrides


def build_trader_llm_model_overrides(traders: Mapping[str, Mapping[str, object]] | None) -> dict[str, str]:
    if not traders:
        return {}
    overrides: dict[str, str] = {}
    for trader_id, payload in traders.items():
        parsing = payload.get("parsing") if isinstance(payload, Mapping) else None
        if not isinstance(parsing, Mapping):
            continue
        profile_options = parsing.get("profile_options") if isinstance(parsing.get("profile_options"), Mapping) else {}
        model = profile_options.get("llm_model") if profile_options else parsing.get("llm_model")
        if model is None:
            continue
        model_text = str(model).strip()
        if model_text:
            overrides[str(trader_id)] = model_text
    return overrides

# src/runtime_v2/signal_enrichment/config_loader.py
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import yaml

from src.runtime_v2.signal_enrichment.models import (
    AccountConfig,
    CloseDistributionConfig,
    EffectiveEnrichmentConfig,
    EntryRangeConfig,
    EntrySplitConfig,
    EntryWeightsConfig,
    LimitEntrySplitConfig,
    ManagementPlanConfig,
    MarketEntrySplitConfig,
    MarketExecutionConfig,
    PriceCorrectionsConfig,
    PriceSanityConfig,
    RiskConfig,
    SignalPolicyConfig,
    SlConfig,
    TpConfig,
)

logger = logging.getLogger(__name__)


class ConfigLoadError(Exception):
    pass


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


class OperationConfigLoader:
    def __init__(self, config_dir: str) -> None:
        self._config_dir = Path(config_dir)
        self._global_raw: dict = {}
        self._mtimes: dict[str, float] = {}
        self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_effective_config(self, trader_id: str) -> EffectiveEnrichmentConfig | None:
        if trader_id not in self._global_raw.get("registered_traders", []):
            return None
        trader_raw = self._load_trader_raw(trader_id)
        return self._merge(trader_id, self._global_raw, trader_raw)

    def get_symbol_blacklist_global(self) -> list[str]:
        return self._global_raw.get("symbol_blacklist", {}).get("global", [])

    def get_symbol_blacklist_for_trader(self, trader_id: str) -> list[str]:
        return (
            self._global_raw.get("symbol_blacklist", {})
            .get("per_trader", {})
            .get(trader_id, [])
        )

    def get_policy_version(self) -> str:
        content = json.dumps(self._global_raw, sort_keys=True, default=str)
        return "sha256:" + hashlib.sha256(content.encode()).hexdigest()[:16]

    def reload_if_changed(self) -> bool:
        op_path = self._config_dir / "operation_config.yaml"
        try:
            mtime = op_path.stat().st_mtime
        except FileNotFoundError:
            return False
        if mtime == self._mtimes.get("operation_config", 0.0):
            return False
        try:
            self._load()
            return True
        except Exception as exc:
            logger.error("Config reload failed, keeping last valid config: %s", exc)
            return False

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load(self) -> None:
        op_path = self._config_dir / "operation_config.yaml"
        with op_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        self._validate_global(raw)
        self._global_raw = raw
        self._mtimes["operation_config"] = op_path.stat().st_mtime

    def _validate_global(self, raw: dict) -> None:
        market_split = (
            raw.get("defaults", {})
            .get("signal_policy", {})
            .get("entry_split", {})
            .get("MARKET", {})
        )
        if "range" in market_split:
            raise ConfigLoadError(
                "entry_split.MARKET.range is invalid: RANGE structure requires LIMIT legs only"
            )

    def _load_trader_raw(self, trader_id: str) -> dict:
        trader_path = self._config_dir / "traders" / f"{trader_id}.yaml"
        if not trader_path.exists():
            return {}
        with trader_path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _merge(self, trader_id: str, global_raw: dict, trader_raw: dict) -> EffectiveEnrichmentConfig:
        account_mode = global_raw.get("account_mode", "single")
        global_account = global_raw.get("account", {})

        if account_mode == "single":
            account_id = global_account.get("id", "main")
        else:
            trader_account = trader_raw.get("account", global_account)
            account_id = trader_account.get("id", global_account.get("id", "main"))

        defaults = global_raw.get("defaults", {})
        # trader_raw keys that are NOT account: override defaults
        trader_overrides = {k: v for k, v in trader_raw.items() if k != "account"}
        merged = _deep_merge(defaults, trader_overrides)

        signal_policy_raw = merged.get("signal_policy", {})
        entry_split_raw = signal_policy_raw.get("entry_split", {})
        limit_raw = entry_split_raw.get("LIMIT", {})
        market_raw = entry_split_raw.get("MARKET", {})

        signal_policy = SignalPolicyConfig(
            accepted_entry_structures=signal_policy_raw.get("accepted_entry_structures", ["ONE_SHOT"]),
            market_execution=MarketExecutionConfig(**signal_policy_raw.get("market_execution", {})),
            entry_split=EntrySplitConfig(
                LIMIT=LimitEntrySplitConfig(
                    single=EntryWeightsConfig(**limit_raw.get("single", {"weights": {"E1": 1.0}})),
                    range=EntryRangeConfig(**limit_raw.get("range", {"weights": {"E1": 0.5, "E2": 0.5}})),
                    averaging=EntryWeightsConfig(**limit_raw.get("averaging", {"weights": {"E1": 0.7, "E2": 0.3}})),
                    ladder=EntryWeightsConfig(**limit_raw.get("ladder", {"weights": {"E1": 0.5, "E2": 0.3, "E3": 0.2}})),
                ),
                MARKET=MarketEntrySplitConfig(
                    single=EntryWeightsConfig(**market_raw.get("single", {"weights": {"E1": 1.0}})),
                    averaging=EntryWeightsConfig(**market_raw.get("averaging", {"weights": {"E1": 0.7, "E2": 0.3}})),
                ),
            ),
            tp=TpConfig(**signal_policy_raw.get("tp", {})),
            sl=SlConfig(**signal_policy_raw.get("sl", {})),
            price_corrections=PriceCorrectionsConfig(**signal_policy_raw.get("price_corrections", {})),
            price_sanity=PriceSanityConfig(**signal_policy_raw.get("price_sanity", {})),
        )

        mgmt_raw = merged.get("management_plan", {})
        dist_raw = mgmt_raw.get("close_distribution", {})
        management_plan = ManagementPlanConfig(
            be_trigger=mgmt_raw.get("be_trigger"),
            be_buffer_pct=mgmt_raw.get("be_buffer_pct", 0.0),
            close_distribution=CloseDistributionConfig(
                mode=dist_raw.get("mode", "table"),
                table={int(k): v for k, v in dist_raw.get("table", {}).items()},
            ),
            cancel_pending_by_engine=mgmt_raw.get("cancel_pending_by_engine", True),
            cancel_pending_on_timeout=mgmt_raw.get("cancel_pending_on_timeout", True),
            pending_timeout_hours=mgmt_raw.get("pending_timeout_hours", 24),
            cancel_averaging_pending_after=mgmt_raw.get("cancel_averaging_pending_after"),
            cancel_unfilled_pending_after=mgmt_raw.get("cancel_unfilled_pending_after"),
            risk_freed_by_be=mgmt_raw.get("risk_freed_by_be", True),
            protective_sl_mode=mgmt_raw.get("protective_sl_mode", "exchange_native_first"),
        )

        # Populate AccountConfig from global account block
        account_raw = global_raw.get("account", {})
        account = None
        if account_raw:
            try:
                account = AccountConfig(
                    id=account_raw.get("id", "main"),
                    capital_base_usdt=float(account_raw.get("capital_base_usdt", 1000.0)),
                    max_leverage=int(account_raw.get("max_leverage", 10)),
                    max_capital_at_risk_pct=float(account_raw.get("max_capital_at_risk_pct", 10.0)),
                    hard_max_per_signal_risk_pct=float(account_raw.get("hard_max_per_signal_risk_pct", 2.0)),
                )
            except Exception:
                pass

        return EffectiveEnrichmentConfig(
            trader_id=trader_id,
            enabled=merged.get("enabled", True),
            gate_mode=merged.get("gate_mode", "block"),
            hedge_mode=merged.get("hedge_mode", False),
            account_id=account_id,
            signal_policy=signal_policy,
            update_admission=merged.get("update_admission", {}),
            management_plan=management_plan,
            risk=RiskConfig(**merged.get("risk", {})),
            account=account,
        )


__all__ = ["OperationConfigLoader", "ConfigLoadError"]

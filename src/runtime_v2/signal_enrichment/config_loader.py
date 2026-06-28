# src/runtime_v2/signal_enrichment/config_loader.py
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import yaml

from src.runtime_v2.control_plane.override_store import OverrideStore
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
    ReshapeEntriesConfig,
    ReshapeMatchConfig,
    ReshapeStopLossConfig,
    ReshapeTakeProfitsConfig,
    ReshapeTemplateConfig,
    RiskConfig,
    SignalPolicyConfig,
    SlConfig,
    TpConfig,
)

logger = logging.getLogger(__name__)


class ConfigLoadError(Exception):
    pass


def _merge_unique_symbols(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for symbol in group:
            if symbol not in merged:
                merged.append(symbol)
    return merged


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


class OperationConfigLoader:
    def __init__(self, config_dir: str, ops_db_path: str | None = None) -> None:
        self._config_dir = Path(config_dir)
        self._override_store = OverrideStore(ops_db_path) if ops_db_path else None
        self._global_raw: dict = {}
        self._mtimes: dict[str, float] = {}
        self._reshape_templates: dict[str, ReshapeTemplateConfig] = {}
        self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_effective_config(self, trader_id: str) -> EffectiveEnrichmentConfig | None:
        if trader_id not in self._global_raw.get("registered_traders", []):
            return None
        trader_raw = self._load_trader_raw(trader_id)
        return self._merge(trader_id, self._global_raw, trader_raw)

    def get_symbol_blacklist_global(self) -> list[str]:
        config_values = self._global_raw.get("symbol_blacklist", {}).get("global", [])
        override_values = []
        if self._override_store is not None:
            override_values = self._override_store.get_blacklist("GLOBAL", None)
        return _merge_unique_symbols(config_values, override_values)

    def get_symbol_blacklist_for_trader(self, trader_id: str) -> list[str]:
        config_values = (
            self._global_raw.get("symbol_blacklist", {})
            .get("per_trader", {})
            .get(trader_id, [])
        )
        override_values = []
        if self._override_store is not None:
            override_values = self._override_store.get_blacklist("PER_TRADER", trader_id)
        return _merge_unique_symbols(config_values, override_values)

    def get_unfilled_price_check_interval(self) -> int:
        """Return unfilled_price_check_interval_seconds from global_safety, default 60."""
        return int(
            self._global_raw.get("global_safety", {})
            .get("unfilled_price_check_interval_seconds", 60)
        )

    def get_policy_version(self, trader_id: str | None = None) -> str:
        if trader_id is None:
            payload: dict | str = self._global_raw
        else:
            effective = self.get_effective_config(trader_id)
            if effective is None:
                raise ConfigLoadError(f"Unknown trader for policy version: {trader_id}")
            payload = effective.model_dump(mode="json")
        content = json.dumps(payload, sort_keys=True, default=str)
        return "sha256:" + hashlib.sha256(content.encode()).hexdigest()[:16]

    def reload_if_changed(self) -> bool:
        op_path = self._config_dir / "operation_config.yaml"
        tpl_path = self._config_dir / "setup_reshape_templates.yaml"
        try:
            op_mtime = op_path.stat().st_mtime
        except FileNotFoundError:
            return False
        tpl_mtime = tpl_path.stat().st_mtime if tpl_path.exists() else 0.0
        if (op_mtime == self._mtimes.get("operation_config", 0.0)
                and tpl_mtime == self._mtimes.get("setup_reshape_templates", 0.0)):
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

        templates_path = self._config_dir / "setup_reshape_templates.yaml"
        self._reshape_templates = {}
        if templates_path.exists():
            with templates_path.open(encoding="utf-8") as f:
                tpl_raw = yaml.safe_load(f) or {}
            for tpl in tpl_raw.get("templates", []):
                cfg = self._build_reshape_template(tpl)
                self._reshape_templates[cfg.id] = cfg
            self._mtimes["setup_reshape_templates"] = templates_path.stat().st_mtime

        # Validate all reshape references at load time (fail-fast)
        self._validate_reshape_references(raw)

    def _validate_global(self, raw: dict) -> None:
        self._validate_market_entry_split(
            raw.get("defaults", {})
        )

    def _load_trader_raw(self, trader_id: str) -> dict:
        trader_path = self._config_dir / "traders" / f"{trader_id}.yaml"
        if not trader_path.exists():
            return {}
        with trader_path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @staticmethod
    def _validate_market_entry_split(config_raw: dict) -> None:
        market_split = (
            config_raw.get("signal_policy", {})
            .get("entry_split", {})
            .get("MARKET", {})
        )
        if "range" in market_split:
            raise ConfigLoadError(
                "entry_split.MARKET.range is invalid: RANGE structure requires LIMIT legs only"
            )

    def _validate_reshape_references(self, raw: dict) -> None:
        """Fail-fast: any registered trader with setup_mode=reshape must reference a known template id."""
        for trader_id in raw.get("registered_traders", []):
            trader_raw = self._load_trader_raw(trader_id)
            setup_mode = trader_raw.get("setup_mode", "passthrough")
            if setup_mode == "reshape":
                template_id = (trader_raw.get("setup_reshape") or {}).get("template")
                if not template_id:
                    raise ConfigLoadError(
                        f"Trader '{trader_id}' has setup_mode=reshape but no setup_reshape.template"
                    )
                if template_id not in self._reshape_templates:
                    raise ConfigLoadError(
                        f"Trader '{trader_id}' references unknown reshape template id '{template_id}'"
                    )

    @staticmethod
    def _build_reshape_template(raw: dict) -> ReshapeTemplateConfig:
        match_raw = raw.get("match", {})
        entries_raw = raw.get("entries", {})
        sl_raw = raw.get("stop_loss", {})
        tp_raw = raw.get("take_profits", {})
        return ReshapeTemplateConfig(
            id=raw["id"],
            enabled=raw.get("enabled", True),
            match=ReshapeMatchConfig(
                entry_structure=match_raw["entry_structure"],
                normalized_entry_count=match_raw.get("normalized_entry_count"),
                min_entry_count=match_raw.get("min_entry_count"),
                min_tp_count=match_raw.get("min_tp_count"),
            ),
            entries=ReshapeEntriesConfig(
                mode=entries_raw["mode"],
                indexes=entries_raw.get("indexes", []),
                n=entries_raw.get("n"),
            ),
            stop_loss=ReshapeStopLossConfig(
                mode=sl_raw["mode"],
                entry=sl_raw.get("entry"),
                pct=sl_raw.get("pct"),
            ),
            take_profits=ReshapeTakeProfitsConfig(
                mode=tp_raw["mode"],
                indexes=tp_raw.get("indexes", []),
                n=tp_raw.get("n"),
                desired_rr=tp_raw.get("desired_rr", []),
                strategy=tp_raw.get("strategy", "nearest_unique"),
                max_rr_deviation_abs=tp_raw.get("max_rr_deviation_abs", 0.35),
                on_missing_target=tp_raw.get("on_missing_target", "REJECT"),
            ),
            on_failure=raw.get("on_failure", "REJECT"),
        )

    @staticmethod
    def _build_account_config(account_raw: dict) -> AccountConfig | None:
        if not account_raw:
            return None
        try:
            return AccountConfig(
                id=account_raw.get("id", "main"),
                capital_base_usdt=float(account_raw.get("capital_base_usdt", 1000.0)),
                max_leverage=int(account_raw.get("max_leverage", 10)),
                max_capital_at_risk_pct=float(account_raw.get("max_capital_at_risk_pct", 10.0)),
                hard_max_per_signal_risk_pct=float(account_raw.get("hard_max_per_signal_risk_pct", 2.0)),
            )
        except (TypeError, ValueError) as exc:
            raise ConfigLoadError(f"Invalid account config: {exc}") from exc

    def _merge(self, trader_id: str, global_raw: dict, trader_raw: dict) -> EffectiveEnrichmentConfig:
        account_mode = global_raw.get("account_mode", "single")
        global_account = global_raw.get("account", {})

        if account_mode == "single":
            effective_account_raw = global_account
        else:
            effective_account_raw = trader_raw.get("account", global_account)

        account_id = effective_account_raw.get("id", global_account.get("id", "main"))

        defaults = global_raw.get("defaults", {})
        # trader_raw keys that are NOT account: override defaults
        trader_overrides = {k: v for k, v in trader_raw.items() if k != "account"}
        merged = _deep_merge(defaults, trader_overrides)
        self._validate_market_entry_split(merged)

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
            be_fee_correction_enabled=mgmt_raw.get("be_fee_correction_enabled", False),
            be_fee_fallback_profile=mgmt_raw.get("be_fee_fallback_profile"),
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

        account = self._build_account_config(effective_account_raw)

        # Resolve reshape setup mode
        setup_mode = merged.get("setup_mode", "passthrough")
        setup_reshape_template: ReshapeTemplateConfig | None = None
        if setup_mode == "reshape":
            template_id = (merged.get("setup_reshape") or {}).get("template")
            if template_id and template_id in self._reshape_templates:
                setup_reshape_template = self._reshape_templates[template_id]

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
            setup_mode=setup_mode,
            setup_reshape_template=setup_reshape_template,
        )


__all__ = ["OperationConfigLoader", "ConfigLoadError"]

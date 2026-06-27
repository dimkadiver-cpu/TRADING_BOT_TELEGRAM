# tests/runtime_v2/signal_enrichment/test_config_loader.py
from __future__ import annotations

import pytest
import yaml
from pathlib import Path


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.dump(data, f)


def _minimal_global_config(overrides: dict | None = None) -> dict:
    base = {
        "account_mode": "single",
        "account": {
            "id": "main",
            "capital_base_usdt": 1000.0,
            "max_leverage": 5,
            "max_capital_at_risk_pct": 10.0,
            "hard_max_per_signal_risk_pct": 2.0,
        },
        "registered_traders": ["trader_a", "trader_b"],
        "symbol_blacklist": {"global": [], "per_trader": {}},
        "defaults": {
            "enabled": True,
            "gate_mode": "block",
            "hedge_mode": False,
            "signal_policy": {
                "accepted_entry_structures": ["ONE_SHOT", "TWO_STEP", "RANGE", "LADDER"],
                "market_execution": {"mode": "tolerance", "tolerance_pct": 0.5, "range_tolerance_pct": 0.2},
                "entry_split": {
                    "LIMIT": {
                        "single": {"weights": {"E1": 1.0}},
                        "range": {"split_mode": "endpoints", "weights": {"E1": 0.5, "E2": 0.5}},
                        "averaging": {"weights": {"E1": 0.7, "E2": 0.3}},
                        "ladder": {"weights": {"E1": 0.5, "E2": 0.3, "E3": 0.2}},
                    },
                    "MARKET": {
                        "single": {"weights": {"E1": 1.0}},
                        "averaging": {"weights": {"E1": 0.7, "E2": 0.3}},
                    },
                },
                "tp": {"use_tp_count": None},
                "sl": {"use_original_sl": True, "require_sl": True},
                "price_corrections": {"enabled": False, "round_to_tick": False, "clamp_to_exchange_precision": False},
                "price_sanity": {"enabled": False, "symbol_ranges": {}},
            },
            "update_admission": {
                "MOVE_STOP": True,
                "MOVE_STOP_TO_BE": False,
                "CLOSE_FULL": True,
                "CLOSE_PARTIAL": True,
                "CANCEL_PENDING": True,
                "ADD_ENTRY": False,
                "REENTER": False,
                "MODIFY_ENTRY": False,
                "MODIFY_TARGETS": False,
                "INVALIDATE_SETUP": False,
            },
            "management_plan": {
                "be_trigger": None,
                "be_fee_correction_enabled": False,
                "be_fee_fallback_profile": None,
                "close_distribution": {"mode": "table", "table": {1: [100], 2: [50, 50]}},
                "cancel_pending_by_engine": True,
                "cancel_pending_on_timeout": True,
                "pending_timeout_hours": 24,
                "cancel_averaging_pending_after": None,
                "cancel_unfilled_pending_after": None,
                "risk_freed_by_be": True,
                "protective_sl_mode": "exchange_native_first",
            },
            "risk": {
                "mode": "risk_pct_of_capital",
                "risk_pct_of_capital": 1.0,
                "risk_usdt_fixed": 10.0,
                "capital_base_mode": "static_config",
                "capital_base_usdt": 1000.0,
                "leverage": 1,
                "use_trader_risk_hint": False,
                "use_trader_leverage_hint": False,
                "max_capital_at_risk_per_trader_pct": 5.0,
                "max_concurrent_trades": 5,
                "max_concurrent_same_symbol": 1,
            },
        },
    }
    if overrides:
        base.update(overrides)
    return base


@pytest.fixture
def config_dir(tmp_path):
    op_path = tmp_path / "operation_config.yaml"
    _write_yaml(op_path, _minimal_global_config())
    (tmp_path / "traders").mkdir()
    return tmp_path


def test_load_defaults_for_registered_trader(config_dir):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    loader = OperationConfigLoader(str(config_dir))
    cfg = loader.get_effective_config("trader_a")
    assert cfg is not None
    assert cfg.trader_id == "trader_a"
    assert cfg.account_id == "main"
    assert cfg.gate_mode == "block"
    assert cfg.signal_policy.sl.require_sl is True
    assert cfg.update_admission["MOVE_STOP"] is True
    assert cfg.update_admission["MOVE_STOP_TO_BE"] is False


def test_unregistered_trader_returns_none(config_dir):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    loader = OperationConfigLoader(str(config_dir))
    assert loader.get_effective_config("unknown_trader") is None


def test_trader_override_merges_tp_count(config_dir):
    trader_yaml = config_dir / "traders" / "trader_a.yaml"
    _write_yaml(trader_yaml, {"signal_policy": {"tp": {"use_tp_count": 2}}})
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    loader = OperationConfigLoader(str(config_dir))
    cfg = loader.get_effective_config("trader_a")
    assert cfg.signal_policy.tp.use_tp_count == 2
    # trader_b should still have null
    cfg_b = loader.get_effective_config("trader_b")
    assert cfg_b.signal_policy.tp.use_tp_count is None


def test_trader_override_reads_use_trader_leverage_hint(config_dir):
    trader_yaml = config_dir / "traders" / "trader_a.yaml"
    _write_yaml(trader_yaml, {"risk": {"use_trader_leverage_hint": True}})
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    loader = OperationConfigLoader(str(config_dir))
    cfg = loader.get_effective_config("trader_a")
    assert cfg.risk.use_trader_leverage_hint is True


def test_default_effective_config_reads_use_trader_leverage_hint_as_false(config_dir):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader

    loader = OperationConfigLoader(str(config_dir))
    cfg = loader.get_effective_config("trader_b")
    assert cfg.risk.use_trader_leverage_hint is False


def test_management_plan_reads_fee_aware_be_flags(config_dir):
    trader_yaml = config_dir / "traders" / "trader_a.yaml"
    _write_yaml(
        trader_yaml,
        {
            "management_plan": {
                "be_trigger": "tp2",
                "be_fee_correction_enabled": True,
                "be_fee_fallback_profile": "bybit_linear",
            }
        },
    )
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    loader = OperationConfigLoader(str(config_dir))
    cfg = loader.get_effective_config("trader_a")
    assert cfg.management_plan.be_trigger == "tp2"
    assert cfg.management_plan.be_fee_correction_enabled is True
    assert cfg.management_plan.be_fee_fallback_profile == "bybit_linear"


def test_trader_override_update_admission(config_dir):
    trader_yaml = config_dir / "traders" / "trader_a.yaml"
    _write_yaml(trader_yaml, {"update_admission": {"MOVE_STOP_TO_BE": True}})
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    loader = OperationConfigLoader(str(config_dir))
    cfg = loader.get_effective_config("trader_a")
    assert cfg.update_admission["MOVE_STOP_TO_BE"] is True
    assert cfg.update_admission["MOVE_STOP"] is True  # dalla config globale


def test_symbol_blacklist_global(config_dir):
    global_cfg = _minimal_global_config()
    global_cfg["symbol_blacklist"]["global"] = ["SCAM/USDT"]
    _write_yaml(config_dir / "operation_config.yaml", global_cfg)
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    loader = OperationConfigLoader(str(config_dir))
    assert "SCAM/USDT" in loader.get_symbol_blacklist_global()


def test_symbol_blacklist_per_trader(config_dir):
    global_cfg = _minimal_global_config()
    global_cfg["symbol_blacklist"]["per_trader"] = {"trader_a": ["RUG/USDT"]}
    _write_yaml(config_dir / "operation_config.yaml", global_cfg)
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    loader = OperationConfigLoader(str(config_dir))
    assert "RUG/USDT" in loader.get_symbol_blacklist_for_trader("trader_a")
    assert "RUG/USDT" not in loader.get_symbol_blacklist_for_trader("trader_b")


def test_market_range_in_entry_split_raises_config_error(config_dir):
    global_cfg = _minimal_global_config()
    global_cfg["defaults"]["signal_policy"]["entry_split"]["MARKET"]["range"] = {
        "weights": {"E1": 0.5, "E2": 0.5}
    }
    _write_yaml(config_dir / "operation_config.yaml", global_cfg)
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader, ConfigLoadError
    with pytest.raises(ConfigLoadError, match="MARKET.range"):
        OperationConfigLoader(str(config_dir))


def test_trader_override_market_range_in_entry_split_raises_config_error(config_dir):
    trader_yaml = config_dir / "traders" / "trader_a.yaml"
    _write_yaml(
        trader_yaml,
        {
            "signal_policy": {
                "entry_split": {
                    "MARKET": {
                        "range": {"weights": {"E1": 0.5, "E2": 0.5}}
                    }
                }
            }
        },
    )
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader, ConfigLoadError
    loader = OperationConfigLoader(str(config_dir))
    with pytest.raises(ConfigLoadError, match="MARKET.range"):
        loader.get_effective_config("trader_a")


def test_per_trader_subaccount_uses_effective_account_source(config_dir):
    global_cfg = _minimal_global_config()
    global_cfg["account_mode"] = "per_trader_subaccount"
    _write_yaml(config_dir / "operation_config.yaml", global_cfg)
    trader_yaml = config_dir / "traders" / "trader_a.yaml"
    _write_yaml(
        trader_yaml,
        {
            "account": {
                "id": "sub_a",
                "capital_base_usdt": 2500.0,
                "max_leverage": 7,
                "max_capital_at_risk_pct": 12.5,
                "hard_max_per_signal_risk_pct": 1.5,
            }
        },
    )
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    loader = OperationConfigLoader(str(config_dir))
    cfg = loader.get_effective_config("trader_a")
    assert cfg.account_id == "sub_a"
    assert cfg.account is not None
    assert cfg.account.id == "sub_a"
    assert cfg.account.capital_base_usdt == 2500.0
    assert cfg.account.max_leverage == 7
    assert cfg.account.max_capital_at_risk_pct == 12.5
    assert cfg.account.hard_max_per_signal_risk_pct == 1.5


def test_invalid_account_values_raise_config_load_error(config_dir):
    global_cfg = _minimal_global_config()
    global_cfg["account"]["max_leverage"] = "not-an-int"
    _write_yaml(config_dir / "operation_config.yaml", global_cfg)
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader, ConfigLoadError
    loader = OperationConfigLoader(str(config_dir))
    with pytest.raises(ConfigLoadError, match="account"):
        loader.get_effective_config("trader_a")


def test_invalid_yaml_does_not_crash_reload(config_dir):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    loader = OperationConfigLoader(str(config_dir))
    # Scrivi YAML invalido
    (config_dir / "operation_config.yaml").write_text("invalid: [unclosed", encoding="utf-8")
    # Forza reload cambiando mtime
    loader._mtimes["operation_config"] = 0.0
    result = loader.reload_if_changed()
    assert result is False
    # Il loader funziona ancora con la config precedente
    cfg = loader.get_effective_config("trader_a")
    assert cfg is not None


def test_policy_version_is_stable(config_dir):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    loader = OperationConfigLoader(str(config_dir))
    v1 = loader.get_policy_version("trader_a")
    v2 = loader.get_policy_version("trader_a")
    assert v1 == v2
    assert v1.startswith("sha256:")


def test_policy_version_changes_with_trader_override(config_dir):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader

    loader = OperationConfigLoader(str(config_dir))
    base_version = loader.get_policy_version("trader_a")

    trader_yaml = config_dir / "traders" / "trader_a.yaml"
    _write_yaml(trader_yaml, {"signal_policy": {"tp": {"use_tp_count": 2}}})

    loader_with_override = OperationConfigLoader(str(config_dir))
    override_version = loader_with_override.get_policy_version("trader_a")
    other_trader_version = loader_with_override.get_policy_version("trader_b")

    assert override_version != base_version
    assert other_trader_version != override_version

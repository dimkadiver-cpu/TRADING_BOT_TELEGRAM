import pytest
from pathlib import Path
import yaml

from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader, ConfigLoadError


def _write_global(tmp_path: Path, extra_defaults: dict | None = None) -> Path:
    defaults = {
        "signal_policy": {
            "accepted_entry_structures": ["LADDER"],
            "market_execution": {"mode": "tolerance", "tolerance_pct": 0.5, "range_tolerance_pct": 0.2},
            "entry_split": {
                "LIMIT": {
                    "single": {"weights": {"E1": 1.0}},
                    "range": {"weights": {"E1": 0.5, "E2": 0.5}},
                    "averaging": {"weights": {"E1": 0.7, "E2": 0.3}},
                    "ladder": {"weights": {"E1": 0.4, "E2": 0.3, "E3": 0.2, "E4": 0.1}},
                },
                "MARKET": {
                    "single": {"weights": {"E1": 1.0}},
                    "averaging": {"weights": {"E1": 0.7, "E2": 0.3}},
                },
            },
            "tp": {"use_tp_count": None},
            "sl": {"use_original_sl": True, "require_sl": True},
            "price_corrections": {"enabled": False},
            "price_sanity": {"enabled": False},
        },
        "management_plan": {
            "be_trigger": None,
            "close_distribution": {"mode": "equal"},
        },
        "risk": {"mode": "risk_pct_of_capital"},
    }
    if extra_defaults:
        defaults.update(extra_defaults)
    raw = {
        "registered_traders": ["trader_reshape"],
        "account": {"id": "main", "capital_base_usdt": 1000.0, "max_leverage": 10,
                     "max_capital_at_risk_pct": 10.0, "hard_max_per_signal_risk_pct": 2.0},
        "defaults": defaults,
    }
    config_path = tmp_path / "operation_config.yaml"
    config_path.write_text(yaml.dump(raw))
    (tmp_path / "traders").mkdir()
    return tmp_path


def _write_templates(tmp_path: Path):
    tpl = {
        "templates": [
            {
                "id": "ladder_4_aggressive",
                "enabled": True,
                "match": {"entry_structure": "LADDER", "normalized_entry_count": 4, "min_tp_count": 8},
                "entries": {"mode": "drop", "indexes": ["E1"]},
                "stop_loss": {"mode": "from_entry", "entry": "E4"},
                "take_profits": {
                    "mode": "by_rr",
                    "desired_rr": [1.0, 1.5, 2.5, 3.5],
                    "strategy": "nearest_unique",
                    "max_rr_deviation_abs": 0.35,
                    "on_missing_target": "REJECT",
                },
                "on_failure": "REJECT",
            }
        ]
    }
    (tmp_path / "setup_reshape_templates.yaml").write_text(yaml.dump(tpl))


def _write_trader(tmp_path: Path, setup_mode: str = "reshape", template_id: str = "ladder_4_aggressive"):
    trader = {
        "setup_mode": setup_mode,
        "setup_reshape": {"template": template_id},
    }
    (tmp_path / "traders" / "trader_reshape.yaml").write_text(yaml.dump(trader))


def test_passthrough_trader_no_template(tmp_path):
    _write_global(tmp_path)
    _write_templates(tmp_path)
    (tmp_path / "traders" / "trader_reshape.yaml").write_text(
        yaml.dump({"setup_mode": "passthrough"})
    )
    loader = OperationConfigLoader(str(tmp_path))
    cfg = loader.get_effective_config("trader_reshape")
    assert cfg.setup_mode == "passthrough"
    assert cfg.setup_reshape_template is None


def test_reshape_trader_resolves_template(tmp_path):
    _write_global(tmp_path)
    _write_templates(tmp_path)
    _write_trader(tmp_path)
    loader = OperationConfigLoader(str(tmp_path))
    cfg = loader.get_effective_config("trader_reshape")
    assert cfg.setup_mode == "reshape"
    assert cfg.setup_reshape_template is not None
    assert cfg.setup_reshape_template.id == "ladder_4_aggressive"
    assert cfg.setup_reshape_template.entries.mode == "drop"


def test_unknown_template_id_raises_at_load(tmp_path):
    _write_global(tmp_path)
    _write_templates(tmp_path)
    _write_trader(tmp_path, template_id="nonexistent_id")
    with pytest.raises(ConfigLoadError, match="nonexistent_id"):
        OperationConfigLoader(str(tmp_path))


def test_missing_templates_file_passthrough_still_works(tmp_path):
    # If setup_reshape_templates.yaml doesn't exist, passthrough traders still load fine
    _write_global(tmp_path)
    (tmp_path / "traders" / "trader_reshape.yaml").write_text(
        yaml.dump({"setup_mode": "passthrough"})
    )
    loader = OperationConfigLoader(str(tmp_path))
    cfg = loader.get_effective_config("trader_reshape")
    assert cfg.setup_mode == "passthrough"


def test_reshape_mode_without_templates_file_raises(tmp_path):
    _write_global(tmp_path)
    _write_trader(tmp_path)  # reshape but no templates file
    with pytest.raises(ConfigLoadError):
        OperationConfigLoader(str(tmp_path))

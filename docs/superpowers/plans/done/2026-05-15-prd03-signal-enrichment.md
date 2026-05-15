# PRD 03 — Signal Enrichment Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementare il Signal Enrichment Layer (Gate 1 stateless) che valida, arricchisce e smista i `CanonicalMessage` dopo il parser, persistendo `EnrichedCanonicalMessage` in `parser.sqlite3` con handoff DB-based verso il Lifecycle Entry Gate (PRD 04).

**Architecture:** `SignalEnrichmentProcessor` riceve un `CanonicalParseResult` dal parser pipeline, carica la config effettiva per trader mergiando `config/operation_config.yaml` con `config/traders/<id>.yaml`, esegue check stateless (blacklist, SL, struttura entry, TP trim, weight split), e persiste il risultato. REPORT/INFO e ogni BLOCK/REVIEW hanno `lifecycle_processed=1` al momento del salvataggio. Solo SIGNAL/UPDATE PASS hanno `lifecycle_processed=0` e sono eleggibili al worker PRD 04.

**Tech Stack:** Python 3.12+, Pydantic v2, PyYAML, sqlite3 (sync), pytest

---

## File Map

**Nuovi file:**
- `src/runtime_v2/signal_enrichment/__init__.py`
- `src/runtime_v2/signal_enrichment/models.py`
- `src/runtime_v2/signal_enrichment/config_loader.py`
- `src/runtime_v2/signal_enrichment/processor.py`
- `src/runtime_v2/signal_enrichment/repository.py`
- `config/operation_config.yaml`
- `config/traders/trader_a.yaml`
- `config/traders/trader_b.yaml`
- `config/traders/trader_c.yaml`
- `config/traders/trader_d.yaml`
- `config/traders/trader_3.yaml`
- `db/migrations/027_enriched_canonical_messages.sql`
- `scripts/setup_parser_db_separation.py`
- `tests/runtime_v2/signal_enrichment/__init__.py`
- `tests/runtime_v2/signal_enrichment/test_models.py`
- `tests/runtime_v2/signal_enrichment/test_config_loader.py`
- `tests/runtime_v2/signal_enrichment/test_repository.py`
- `tests/runtime_v2/signal_enrichment/test_processor_signal.py`
- `tests/runtime_v2/signal_enrichment/test_processor_update.py`
- `tests/runtime_v2/signal_enrichment/test_processor_routing.py`
- `tests/runtime_v2/signal_enrichment/test_integration.py`

**File modificati:**
- `.env` — aggiunge `PARSER_DB_PATH`, `OPS_DB_PATH`

---

## Task 1: Models

**Files:**
- Create: `src/runtime_v2/signal_enrichment/__init__.py`
- Create: `src/runtime_v2/signal_enrichment/models.py`
- Create: `tests/runtime_v2/signal_enrichment/__init__.py`
- Create: `tests/runtime_v2/signal_enrichment/test_models.py`

- [ ] **Step 1: Crea i file vuoti e scrivi il test**

```python
# tests/runtime_v2/signal_enrichment/test_models.py
from __future__ import annotations
import pytest
from pydantic import ValidationError


def test_enrichment_log_entry_rejects_extra_fields():
    from src.runtime_v2.signal_enrichment.models import EnrichmentLogEntry
    with pytest.raises(ValidationError):
        EnrichmentLogEntry(check="x", result="y", unknown_field="z")


def test_management_plan_config_defaults():
    from src.runtime_v2.signal_enrichment.models import ManagementPlanConfig, CloseDistributionConfig
    plan = ManagementPlanConfig(close_distribution=CloseDistributionConfig())
    assert plan.be_trigger is None
    assert plan.be_buffer_pct == 0.0
    assert plan.cancel_pending_by_engine is True
    assert plan.pending_timeout_hours == 24
    assert plan.risk_freed_by_be is True
    assert plan.protective_sl_mode == "exchange_native_first"


def test_effective_enrichment_config_fields():
    from src.runtime_v2.signal_enrichment.models import (
        EffectiveEnrichmentConfig, SignalPolicyConfig, ManagementPlanConfig,
        CloseDistributionConfig, RiskConfig, MarketExecutionConfig,
        EntrySplitConfig, LimitEntrySplitConfig, MarketEntrySplitConfig,
        EntryWeightsConfig, EntryRangeConfig, TpConfig, SlConfig,
        PriceCorrectionsConfig, PriceSanityConfig,
    )
    signal_policy = SignalPolicyConfig(
        accepted_entry_structures=["ONE_SHOT"],
        market_execution=MarketExecutionConfig(),
        entry_split=EntrySplitConfig(
            LIMIT=LimitEntrySplitConfig(
                single=EntryWeightsConfig(weights={"E1": 1.0}),
                range=EntryRangeConfig(weights={"E1": 0.5, "E2": 0.5}),
                averaging=EntryWeightsConfig(weights={"E1": 0.7, "E2": 0.3}),
                ladder=EntryWeightsConfig(weights={"E1": 0.5, "E2": 0.3, "E3": 0.2}),
            ),
            MARKET=MarketEntrySplitConfig(
                single=EntryWeightsConfig(weights={"E1": 1.0}),
                averaging=EntryWeightsConfig(weights={"E1": 0.7, "E2": 0.3}),
            ),
        ),
        tp=TpConfig(),
        sl=SlConfig(),
        price_corrections=PriceCorrectionsConfig(),
        price_sanity=PriceSanityConfig(),
    )
    cfg = EffectiveEnrichmentConfig(
        trader_id="trader_a",
        enabled=True,
        gate_mode="block",
        hedge_mode=False,
        account_id="main",
        signal_policy=signal_policy,
        update_admission={"MOVE_STOP": True},
        management_plan=ManagementPlanConfig(close_distribution=CloseDistributionConfig()),
        risk=RiskConfig(),
    )
    assert cfg.trader_id == "trader_a"
    assert cfg.account_id == "main"
    assert cfg.hedge_mode is False


def test_enriched_canonical_message_defaults():
    from src.runtime_v2.signal_enrichment.models import EnrichedCanonicalMessage
    msg = EnrichedCanonicalMessage(
        canonical_message_id=1,
        raw_message_id=10,
        trader_id="trader_a",
        account_id="main",
        primary_class="SIGNAL",
        enrichment_decision="BLOCK",
        reason_code="missing_stop_loss",
        policy_version="sha256:abc",
    )
    assert msg.enriched_signal is None
    assert msg.lifecycle_processed is False
    assert msg.enrichment_log == []
```

- [ ] **Step 2: Esegui il test per verificare il fallimento**

```
pytest tests/runtime_v2/signal_enrichment/test_models.py -v
```
Expected: ImportError — modulo non esiste ancora.

- [ ] **Step 3: Crea i modelli**

```python
# src/runtime_v2/signal_enrichment/__init__.py
# (vuoto)
```

```python
# src/runtime_v2/signal_enrichment/models.py
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.parser_v2.contracts.canonical_message import TargetActionGroup
from src.parser_v2.contracts.entities import Price, StopLoss, TakeProfit
from src.parser_v2.contracts.enums import EntryRole, EntryStructure, EntryType, MessageClass, Side


# ── Config models ──────────────────────────────────────────────────────────────

class MarketExecutionConfig(BaseModel):
    mode: Literal["tolerance", "free"] = "tolerance"
    tolerance_pct: float = 0.5
    range_tolerance_pct: float = 0.2


class EntryWeightsConfig(BaseModel):
    weights: dict[str, float]


class EntryRangeConfig(BaseModel):
    split_mode: Literal["endpoints", "firstpoint", "lastpoint", "midpoint"] = "endpoints"
    weights: dict[str, float]


class LimitEntrySplitConfig(BaseModel):
    single: EntryWeightsConfig
    range: EntryRangeConfig
    averaging: EntryWeightsConfig
    ladder: EntryWeightsConfig


class MarketEntrySplitConfig(BaseModel):
    single: EntryWeightsConfig
    averaging: EntryWeightsConfig


class EntrySplitConfig(BaseModel):
    LIMIT: LimitEntrySplitConfig
    MARKET: MarketEntrySplitConfig


class TpConfig(BaseModel):
    use_tp_count: int | None = None


class SlConfig(BaseModel):
    use_original_sl: bool = True
    require_sl: bool = True


class PriceCorrectionsConfig(BaseModel):
    enabled: bool = False
    round_to_tick: bool = False
    clamp_to_exchange_precision: bool = False


class PriceSanityConfig(BaseModel):
    enabled: bool = False
    symbol_ranges: dict[str, list[float]] = Field(default_factory=dict)


class SignalPolicyConfig(BaseModel):
    accepted_entry_structures: list[EntryStructure]
    market_execution: MarketExecutionConfig
    entry_split: EntrySplitConfig
    tp: TpConfig
    sl: SlConfig
    price_corrections: PriceCorrectionsConfig
    price_sanity: PriceSanityConfig


class CloseDistributionConfig(BaseModel):
    mode: Literal["table", "equal"] = "table"
    table: dict[int, list[int]] = Field(default_factory=dict)


class ManagementPlanConfig(BaseModel):
    be_trigger: Literal["tp1", "tp2", "tp3"] | None = None
    be_buffer_pct: float = 0.0
    close_distribution: CloseDistributionConfig = Field(default_factory=CloseDistributionConfig)
    cancel_pending_by_engine: bool = True
    cancel_pending_on_timeout: bool = True
    pending_timeout_hours: int = 24
    cancel_averaging_pending_after: Literal["tp1", "tp2"] | None = None
    cancel_unfilled_pending_after: Literal["tp1", "tp2"] | None = None
    risk_freed_by_be: bool = True
    protective_sl_mode: Literal["exchange_native_first", "bot_managed"] = "exchange_native_first"


class RiskConfig(BaseModel):
    mode: Literal["risk_pct_of_capital", "risk_usdt_fixed"] = "risk_pct_of_capital"
    risk_pct_of_capital: float = 1.0
    risk_usdt_fixed: float = 10.0
    capital_base_mode: Literal["static_config", "live_equity"] = "static_config"
    capital_base_usdt: float = 1000.0
    leverage: int = 1
    use_trader_risk_hint: bool = False
    max_capital_at_risk_per_trader_pct: float = 5.0
    max_concurrent_trades: int = 5
    max_concurrent_same_symbol: int = 1


class AccountConfig(BaseModel):
    id: str
    capital_base_usdt: float
    max_leverage: int
    max_capital_at_risk_pct: float
    hard_max_per_signal_risk_pct: float


class EffectiveEnrichmentConfig(BaseModel):
    trader_id: str
    enabled: bool
    gate_mode: Literal["block", "warn"]
    hedge_mode: bool
    account_id: str
    signal_policy: SignalPolicyConfig
    update_admission: dict[str, bool]
    management_plan: ManagementPlanConfig
    risk: RiskConfig


# ── Enrichment models ──────────────────────────────────────────────────────────

class EnrichedEntryLeg(BaseModel):
    sequence: int
    entry_type: EntryType
    price: Price | None = None
    role: EntryRole = "UNKNOWN"
    weight: float = 1.0


class EnrichedSignalPayload(BaseModel):
    symbol: str | None
    side: Side | None
    entry_structure: EntryStructure | None
    entries: list[EnrichedEntryLeg]
    take_profits: list[TakeProfit]
    stop_loss: StopLoss | None


class EnrichmentLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check: str
    original: str | None = None
    result: str
    detail: str | None = None


class EnrichedCanonicalMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enrichment_id: int | None = None
    canonical_message_id: int
    raw_message_id: int
    trader_id: str
    account_id: str
    primary_class: MessageClass
    enrichment_decision: Literal["PASS", "BLOCK", "REVIEW"]
    reason_code: str | None = None
    enriched_signal: EnrichedSignalPayload | None = None
    enriched_actions: list[TargetActionGroup] | None = None
    management_plan: ManagementPlanConfig | None = None
    enrichment_log: list[EnrichmentLogEntry] = Field(default_factory=list)
    policy_snapshot: dict = Field(default_factory=dict)
    policy_version: str = ""
    lifecycle_processed: bool = False
    created_at: datetime | None = None


__all__ = [
    "MarketExecutionConfig", "EntryWeightsConfig", "EntryRangeConfig",
    "LimitEntrySplitConfig", "MarketEntrySplitConfig", "EntrySplitConfig",
    "TpConfig", "SlConfig", "PriceCorrectionsConfig", "PriceSanityConfig",
    "SignalPolicyConfig", "CloseDistributionConfig", "ManagementPlanConfig",
    "RiskConfig", "AccountConfig", "EffectiveEnrichmentConfig",
    "EnrichedEntryLeg", "EnrichedSignalPayload",
    "EnrichmentLogEntry", "EnrichedCanonicalMessage",
]
```

- [ ] **Step 4: Esegui i test**

```
pytest tests/runtime_v2/signal_enrichment/test_models.py -v
```
Expected: tutti PASS.

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/signal_enrichment/ tests/runtime_v2/signal_enrichment/
git commit -m "feat(prd03): add signal_enrichment models"
```

---

## Task 2: Config YAML files

**Files:**
- Create: `config/operation_config.yaml`
- Create: `config/traders/trader_a.yaml`
- Create: `config/traders/trader_b.yaml`
- Create: `config/traders/trader_c.yaml`
- Create: `config/traders/trader_d.yaml`
- Create: `config/traders/trader_3.yaml`

- [ ] **Step 1: Crea `config/operation_config.yaml`**

```yaml
# config/operation_config.yaml

global_safety:
  allow_unprotected_positions: false

account_mode: single

# single: unico account condiviso — max_capital_at_risk_pct e hard_max_per_signal_risk_pct
#         sono hard cap non overridabili.
# per_trader_subaccount: ogni config/traders/<id>.yaml definisce il proprio blocco account:
account:
  id: "main"
  capital_base_usdt: 1000.0
  max_leverage: 5
  max_capital_at_risk_pct: 10.0
  hard_max_per_signal_risk_pct: 2.0

registered_traders:
  - trader_3
  - trader_a
  - trader_b
  - trader_c
  - trader_d

symbol_blacklist:
  global: []
  per_trader: {}

defaults:
  enabled: true
  gate_mode: block
  hedge_mode: false

  signal_policy:
    accepted_entry_structures:
      - ONE_SHOT
      - TWO_STEP
      - RANGE
      - LADDER

    market_execution:
      mode: tolerance
      tolerance_pct: 0.5
      range_tolerance_pct: 0.2

    # MARKET.range non esiste: RANGE richiede leg LIMIT — errore esplicito del loader.
    entry_split:
      LIMIT:
        single:
          weights: {E1: 1.0}
        range:
          split_mode: endpoints
          weights: {E1: 0.50, E2: 0.50}
        averaging:
          weights: {E1: 0.70, E2: 0.30}
        ladder:
          weights: {E1: 0.50, E2: 0.30, E3: 0.20}
      MARKET:
        single:
          weights: {E1: 1.0}
        averaging:
          weights: {E1: 0.70, E2: 0.30}

    tp:
      use_tp_count: null

    sl:
      use_original_sl: true
      require_sl: true

    price_corrections:
      enabled: false
      round_to_tick: false
      clamp_to_exchange_precision: false

    price_sanity:
      enabled: false
      symbol_ranges: {}

  # Ammissione update Telegram (source_intent da parser_v2, senza prefisso U_)
  update_admission:
    MOVE_STOP: true
    MOVE_STOP_TO_BE: false
    CLOSE_FULL: true
    CLOSE_PARTIAL: true
    CANCEL_PENDING: true
    ADD_ENTRY: false
    REENTER: false
    MODIFY_ENTRY: false
    MODIFY_TARGETS: false
    INVALIDATE_SETUP: false

  management_plan:
    be_trigger: null
    be_buffer_pct: 0.0
    close_distribution:
      mode: table
      table:
        1: [100]
        2: [50, 50]
        3: [30, 30, 40]
        4: [25, 25, 25, 25]
        5: [20, 20, 20, 20, 20]
        6: [20, 20, 20, 20, 10, 10]
    cancel_pending_by_engine: true
    cancel_pending_on_timeout: true
    pending_timeout_hours: 24
    cancel_averaging_pending_after: null
    cancel_unfilled_pending_after: null
    risk_freed_by_be: true
    protective_sl_mode: exchange_native_first

  risk:
    mode: risk_pct_of_capital
    risk_pct_of_capital: 1.0
    risk_usdt_fixed: 10.0
    capital_base_mode: static_config
    capital_base_usdt: 1000.0
    leverage: 1
    use_trader_risk_hint: false
    max_capital_at_risk_per_trader_pct: 5.0
    max_concurrent_trades: 5
    max_concurrent_same_symbol: 1
```

- [ ] **Step 2: Crea `config/traders/trader_a.yaml`**

```yaml
# config/traders/trader_a.yaml
enabled: true
gate_mode: block

signal_policy:
  tp:
    use_tp_count: 2

update_admission:
  MOVE_STOP_TO_BE: true

management_plan:
  be_trigger: tp2
  be_buffer_pct: 0.05

risk:
  risk_pct_of_capital: 0.5
  max_concurrent_trades: 3
```

- [ ] **Step 3: Crea i file per i trader rimanenti**

```yaml
# config/traders/trader_b.yaml
enabled: true
gate_mode: block
```

```yaml
# config/traders/trader_c.yaml
enabled: true
gate_mode: block
```

```yaml
# config/traders/trader_d.yaml
enabled: true
gate_mode: block
```

```yaml
# config/traders/trader_3.yaml
enabled: true
gate_mode: block
```

- [ ] **Step 4: Commit**

```
git add config/
git commit -m "feat(prd03): add operation_config.yaml and trader config templates"
```

---

## Task 3: Config Loader

**Files:**
- Create: `src/runtime_v2/signal_enrichment/config_loader.py`
- Create: `tests/runtime_v2/signal_enrichment/test_config_loader.py`

- [ ] **Step 1: Scrivi i test**

```python
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
                "be_buffer_pct": 0.0,
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
    v1 = loader.get_policy_version()
    v2 = loader.get_policy_version()
    assert v1 == v2
    assert v1.startswith("sha256:")
```

- [ ] **Step 2: Esegui per verificare il fallimento**

```
pytest tests/runtime_v2/signal_enrichment/test_config_loader.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implementa il config loader**

```python
# src/runtime_v2/signal_enrichment/config_loader.py
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import yaml

from src.runtime_v2.signal_enrichment.models import (
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
        )


__all__ = ["OperationConfigLoader", "ConfigLoadError"]
```

- [ ] **Step 4: Esegui i test**

```
pytest tests/runtime_v2/signal_enrichment/test_config_loader.py -v
```
Expected: tutti PASS.

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/signal_enrichment/config_loader.py tests/runtime_v2/signal_enrichment/test_config_loader.py
git commit -m "feat(prd03): add OperationConfigLoader with merge, validation and hot-reload"
```

---

## Task 4: DB — Migrazione e separazione

**Files:**
- Create: `db/migrations/027_enriched_canonical_messages.sql`
- Create: `scripts/setup_parser_db_separation.py`

- [ ] **Step 1: Crea la migrazione SQL**

```sql
-- db/migrations/027_enriched_canonical_messages.sql
-- Aggiunge la tabella enriched_canonical_messages a parser.sqlite3 (ex tele_signal_bot.sqlite3)

CREATE TABLE IF NOT EXISTS enriched_canonical_messages (
    enrichment_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_message_id     INTEGER NOT NULL UNIQUE,
    raw_message_id           INTEGER NOT NULL,
    trader_id                TEXT NOT NULL,
    account_id               TEXT NOT NULL,
    primary_class            TEXT NOT NULL,
    enrichment_decision      TEXT NOT NULL,
    reason_code              TEXT,
    enriched_signal_json     TEXT,
    enriched_actions_json    TEXT,
    management_plan_json     TEXT,
    enrichment_log_json      TEXT NOT NULL DEFAULT '[]',
    policy_snapshot_json     TEXT NOT NULL DEFAULT '{}',
    policy_version           TEXT NOT NULL DEFAULT '',
    lifecycle_processed      INTEGER NOT NULL DEFAULT 0,
    created_at               TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ecm_trader_id
    ON enriched_canonical_messages(trader_id);

CREATE INDEX IF NOT EXISTS idx_ecm_decision
    ON enriched_canonical_messages(enrichment_decision);

CREATE INDEX IF NOT EXISTS idx_ecm_lifecycle
    ON enriched_canonical_messages(lifecycle_processed, enrichment_decision, primary_class);

CREATE INDEX IF NOT EXISTS idx_ecm_created
    ON enriched_canonical_messages(created_at);
```

- [ ] **Step 2: Crea lo script di separazione DB**

```python
# scripts/setup_parser_db_separation.py
"""
One-time script: rinomina db/tele_signal_bot.sqlite3 → db/parser.sqlite3
e crea db/ops.sqlite3 vuoto. Poi applica le migrazioni SQL a parser.sqlite3.

Eseguire UNA VOLTA prima di avviare il sistema con la nuova configurazione.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def main() -> None:
    old_path = PROJECT_ROOT / "db" / "tele_signal_bot.sqlite3"
    parser_path = PROJECT_ROOT / "db" / "parser.sqlite3"
    ops_path = PROJECT_ROOT / "db" / "ops.sqlite3"

    # 1. Rinomina il DB esistente
    if old_path.exists() and not parser_path.exists():
        print(f"Rinomina {old_path} → {parser_path}")
        shutil.copy2(str(old_path), str(parser_path))
        print("Copia completata. Rimuovi manualmente il vecchio file quando sicuro.")
    elif parser_path.exists():
        print(f"parser.sqlite3 già esiste, skip rinomina.")
    else:
        print(f"ATTENZIONE: né {old_path} né {parser_path} esistono. Crea un DB vuoto.")
        parser_path.touch()

    # 2. Crea ops.sqlite3 vuoto con schema_migrations
    if not ops_path.exists():
        print(f"Crea {ops_path}")
        conn = sqlite3.connect(str(ops_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()
    else:
        print(f"ops.sqlite3 già esiste, skip.")

    # 3. Applica migrazione 027 a parser.sqlite3
    migration = PROJECT_ROOT / "db" / "migrations" / "027_enriched_canonical_messages.sql"
    if migration.exists():
        conn = sqlite3.connect(str(parser_path))
        conn.executescript(migration.read_text(encoding="utf-8"))
        conn.commit()
        conn.close()
        print(f"Migrazione 027 applicata a parser.sqlite3.")
    else:
        print(f"ATTENZIONE: migrazione 027 non trovata in {migration}")

    print("\nDB setup completato.")
    print(f"  parser.sqlite3: {parser_path}")
    print(f"  ops.sqlite3:    {ops_path}")
    print("\nAggiorna .env:")
    print("  PARSER_DB_PATH=db/parser.sqlite3")
    print("  OPS_DB_PATH=db/ops.sqlite3")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Aggiungi le variabili a `.env`**

Aggiungi in fondo a `.env` (non rimuovere `DB_PATH` — usato da codice legacy):

```
PARSER_DB_PATH=db/parser.sqlite3
OPS_DB_PATH=db/ops.sqlite3
```

- [ ] **Step 4: Esegui lo script**

```
python scripts/setup_parser_db_separation.py
```

Verifica: `db/parser.sqlite3` e `db/ops.sqlite3` esistono.

- [ ] **Step 5: Verifica la tabella**

```
python -c "import sqlite3; conn = sqlite3.connect('db/parser.sqlite3'); print([r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")])"
```

Expected: output include `enriched_canonical_messages`.

- [ ] **Step 6: Commit**

```
git add db/migrations/027_enriched_canonical_messages.sql scripts/setup_parser_db_separation.py .env
git commit -m "feat(prd03): add migration 027 and DB separation setup script"
```

---

## Task 5: Repository

**Files:**
- Create: `src/runtime_v2/signal_enrichment/repository.py`
- Create: `tests/runtime_v2/signal_enrichment/test_repository.py`

- [ ] **Step 1: Scrivi i test**

```python
# tests/runtime_v2/signal_enrichment/test_repository.py
from __future__ import annotations

import sqlite3
import pytest
from pathlib import Path


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    _apply_migrations(path)
    return path


def _make_block_msg(canonical_message_id: int = 1) -> object:
    from src.runtime_v2.signal_enrichment.models import EnrichedCanonicalMessage
    return EnrichedCanonicalMessage(
        canonical_message_id=canonical_message_id,
        raw_message_id=10,
        trader_id="trader_a",
        account_id="main",
        primary_class="SIGNAL",
        enrichment_decision="BLOCK",
        reason_code="missing_stop_loss",
        policy_version="sha256:abc",
        lifecycle_processed=True,
    )


def test_save_returns_enrichment_id(db_path):
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    repo = EnrichedCanonicalMessageRepository(db_path)
    saved = repo.save(_make_block_msg())
    assert saved.enrichment_id is not None
    assert saved.enrichment_id > 0


def test_get_by_canonical_message_id_returns_saved(db_path):
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    repo = EnrichedCanonicalMessageRepository(db_path)
    repo.save(_make_block_msg(canonical_message_id=42))
    retrieved = repo.get_by_canonical_message_id(42)
    assert retrieved is not None
    assert retrieved.trader_id == "trader_a"
    assert retrieved.enrichment_decision == "BLOCK"
    assert retrieved.reason_code == "missing_stop_loss"
    assert retrieved.lifecycle_processed is True


def test_get_by_canonical_message_id_missing_returns_none(db_path):
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    repo = EnrichedCanonicalMessageRepository(db_path)
    assert repo.get_by_canonical_message_id(999) is None


def test_save_idempotent_unique_constraint(db_path):
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    repo = EnrichedCanonicalMessageRepository(db_path)
    saved1 = repo.save(_make_block_msg(canonical_message_id=5))
    saved2 = repo.save(_make_block_msg(canonical_message_id=5))
    assert saved1.enrichment_id == saved2.enrichment_id


def test_save_pass_with_enrichment_log(db_path):
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.models import EnrichedCanonicalMessage, EnrichmentLogEntry
    repo = EnrichedCanonicalMessageRepository(db_path)
    msg = EnrichedCanonicalMessage(
        canonical_message_id=7,
        raw_message_id=70,
        trader_id="trader_a",
        account_id="main",
        primary_class="SIGNAL",
        enrichment_decision="PASS",
        policy_version="sha256:abc",
        lifecycle_processed=False,
        enrichment_log=[
            EnrichmentLogEntry(check="tp_count_trimmed", original="5", result="2"),
        ],
    )
    saved = repo.save(msg)
    retrieved = repo.get_by_canonical_message_id(7)
    assert retrieved is not None
    assert len(retrieved.enrichment_log) == 1
    assert retrieved.enrichment_log[0].check == "tp_count_trimmed"
    assert retrieved.enrichment_log[0].original == "5"
    assert retrieved.lifecycle_processed is False
```

- [ ] **Step 2: Esegui per verificare il fallimento**

```
pytest tests/runtime_v2/signal_enrichment/test_repository.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implementa il repository**

```python
# src/runtime_v2/signal_enrichment/repository.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.parser_v2.contracts.canonical_message import TargetActionGroup
from src.runtime_v2.signal_enrichment.models import (
    EnrichedCanonicalMessage,
    EnrichedSignalPayload,
    EnrichmentLogEntry,
    ManagementPlanConfig,
)


class EnrichedCanonicalMessageRepository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def save(self, enriched: EnrichedCanonicalMessage) -> EnrichedCanonicalMessage:
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO enriched_canonical_messages (
                    canonical_message_id, raw_message_id, trader_id, account_id,
                    primary_class, enrichment_decision, reason_code,
                    enriched_signal_json, enriched_actions_json, management_plan_json,
                    enrichment_log_json, policy_snapshot_json, policy_version,
                    lifecycle_processed, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    enriched.canonical_message_id,
                    enriched.raw_message_id,
                    enriched.trader_id,
                    enriched.account_id,
                    enriched.primary_class,
                    enriched.enrichment_decision,
                    enriched.reason_code,
                    enriched.enriched_signal.model_dump_json() if enriched.enriched_signal else None,
                    (
                        json.dumps([a.model_dump() for a in enriched.enriched_actions])
                        if enriched.enriched_actions else None
                    ),
                    enriched.management_plan.model_dump_json() if enriched.management_plan else None,
                    json.dumps([e.model_dump() for e in enriched.enrichment_log]),
                    json.dumps(enriched.policy_snapshot),
                    enriched.policy_version,
                    1 if enriched.lifecycle_processed else 0,
                    now,
                ),
            )
            conn.commit()
            if cursor.lastrowid and cursor.rowcount > 0:
                row_id = cursor.lastrowid
            else:
                row = conn.execute(
                    "SELECT enrichment_id FROM enriched_canonical_messages WHERE canonical_message_id = ?",
                    (enriched.canonical_message_id,),
                ).fetchone()
                row_id = row[0]
        finally:
            conn.close()
        return enriched.model_copy(update={"enrichment_id": row_id})

    def get_by_canonical_message_id(self, canonical_message_id: int) -> EnrichedCanonicalMessage | None:
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                """
                SELECT enrichment_id, canonical_message_id, raw_message_id, trader_id,
                       account_id, primary_class, enrichment_decision, reason_code,
                       enriched_signal_json, enriched_actions_json, management_plan_json,
                       enrichment_log_json, policy_snapshot_json, policy_version,
                       lifecycle_processed, created_at
                FROM enriched_canonical_messages WHERE canonical_message_id = ?
                """,
                (canonical_message_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_model(row)
        finally:
            conn.close()

    def _row_to_model(self, row: tuple) -> EnrichedCanonicalMessage:
        (
            enrichment_id, canonical_message_id, raw_message_id, trader_id,
            account_id, primary_class, enrichment_decision, reason_code,
            enriched_signal_json, enriched_actions_json, management_plan_json,
            enrichment_log_json, policy_snapshot_json, policy_version,
            lifecycle_processed, created_at,
        ) = row

        enriched_signal = (
            EnrichedSignalPayload.model_validate_json(enriched_signal_json)
            if enriched_signal_json else None
        )
        enriched_actions = None
        if enriched_actions_json:
            enriched_actions = [
                TargetActionGroup.model_validate(a)
                for a in json.loads(enriched_actions_json)
            ]
        management_plan = (
            ManagementPlanConfig.model_validate_json(management_plan_json)
            if management_plan_json else None
        )
        return EnrichedCanonicalMessage(
            enrichment_id=enrichment_id,
            canonical_message_id=canonical_message_id,
            raw_message_id=raw_message_id,
            trader_id=trader_id,
            account_id=account_id,
            primary_class=primary_class,
            enrichment_decision=enrichment_decision,
            reason_code=reason_code,
            enriched_signal=enriched_signal,
            enriched_actions=enriched_actions,
            management_plan=management_plan,
            enrichment_log=[
                EnrichmentLogEntry.model_validate(e)
                for e in json.loads(enrichment_log_json)
            ],
            policy_snapshot=json.loads(policy_snapshot_json),
            policy_version=policy_version,
            lifecycle_processed=bool(lifecycle_processed),
            created_at=datetime.fromisoformat(created_at) if created_at else None,
        )


__all__ = ["EnrichedCanonicalMessageRepository"]
```

- [ ] **Step 4: Esegui i test**

```
pytest tests/runtime_v2/signal_enrichment/test_repository.py -v
```
Expected: tutti PASS.

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/signal_enrichment/repository.py tests/runtime_v2/signal_enrichment/test_repository.py
git commit -m "feat(prd03): add EnrichedCanonicalMessageRepository"
```

---

## Task 6: Processor — Gate SIGNAL

**Files:**
- Create: `src/runtime_v2/signal_enrichment/processor.py`
- Create: `tests/runtime_v2/signal_enrichment/test_processor_signal.py`

- [ ] **Step 1: Scrivi i test SIGNAL**

```python
# tests/runtime_v2/signal_enrichment/test_processor_signal.py
from __future__ import annotations

import sqlite3
import pytest
import yaml
from pathlib import Path
from unittest.mock import MagicMock


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _minimal_global_config() -> dict:
    return {
        "account_mode": "single",
        "account": {"id": "main", "capital_base_usdt": 1000.0, "max_leverage": 5,
                     "max_capital_at_risk_pct": 10.0, "hard_max_per_signal_risk_pct": 2.0},
        "registered_traders": ["trader_a"],
        "symbol_blacklist": {"global": [], "per_trader": {}},
        "defaults": {
            "enabled": True, "gate_mode": "block", "hedge_mode": False,
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
            "update_admission": {"MOVE_STOP": True, "MOVE_STOP_TO_BE": False, "CLOSE_FULL": True,
                                  "CLOSE_PARTIAL": True, "CANCEL_PENDING": True, "ADD_ENTRY": False,
                                  "REENTER": False, "MODIFY_ENTRY": False, "MODIFY_TARGETS": False,
                                  "INVALIDATE_SETUP": False},
            "management_plan": {
                "be_trigger": None, "be_buffer_pct": 0.0,
                "close_distribution": {"mode": "table", "table": {1: [100], 2: [50, 50]}},
                "cancel_pending_by_engine": True, "cancel_pending_on_timeout": True,
                "pending_timeout_hours": 24, "cancel_averaging_pending_after": None,
                "cancel_unfilled_pending_after": None, "risk_freed_by_be": True,
                "protective_sl_mode": "exchange_native_first",
            },
            "risk": {"mode": "risk_pct_of_capital", "risk_pct_of_capital": 1.0,
                     "risk_usdt_fixed": 10.0, "capital_base_mode": "static_config",
                     "capital_base_usdt": 1000.0, "leverage": 1, "use_trader_risk_hint": False,
                     "max_capital_at_risk_per_trader_pct": 5.0, "max_concurrent_trades": 5,
                     "max_concurrent_same_symbol": 1},
        },
    }


def _make_parse_result(
    *,
    trader_id: str = "trader_a",
    canonical_message_id: int = 1,
    raw_message_id: int = 10,
    symbol: str = "BTC/USDT",
    side: str = "LONG",
    entry_structure: str = "ONE_SHOT",
    has_sl: bool = True,
    tp_count: int = 3,
    primary_class: str = "SIGNAL",
):
    from src.parser_v2.contracts.canonical_message import (
        CanonicalMessage, SignalPayload, StopLoss,
    )
    from src.parser_v2.contracts.entities import EntryLeg, Price, TakeProfit
    from src.parser_v2.contracts.context import RawContext
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

    entries = [EntryLeg(sequence=1, entry_type="LIMIT", price=Price(raw="50000", value=50000.0))]
    take_profits = [
        TakeProfit(sequence=i + 1, price=Price(raw=str(51000 + i * 500), value=51000.0 + i * 500))
        for i in range(tp_count)
    ]
    stop_loss = StopLoss(price=Price(raw="49000", value=49000.0)) if has_sl else None

    signal = SignalPayload(
        symbol=symbol, side=side, entry_structure=entry_structure,
        entries=entries, take_profits=take_profits, stop_loss=stop_loss,
        completeness="COMPLETE",
    )
    canonical = CanonicalMessage(
        parser_profile=trader_id, primary_class=primary_class,
        parse_status="PARSED", confidence=1.0,
        signal=signal, raw_context=RawContext(raw_text="test"),
    )
    return CanonicalParseResult(
        raw_message_id=raw_message_id,
        canonical_message_id=canonical_message_id,
        parser_profile=trader_id,
        primary_class=primary_class,
        parse_status="PARSED",
        canonical_message=canonical,
        warnings=[],
        parsed_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )


@pytest.fixture
def processor(tmp_path):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    config_file = tmp_path / "operation_config.yaml"
    with config_file.open("w") as f:
        yaml.dump(_minimal_global_config(), f)
    (tmp_path / "traders").mkdir()

    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)

    loader = OperationConfigLoader(str(tmp_path))
    repo = EnrichedCanonicalMessageRepository(db_path)
    return SignalEnrichmentProcessor(config_loader=loader, repository=repo)


def test_unregistered_trader_is_blocked(processor):
    result = _make_parse_result(trader_id="unknown_trader")
    enriched = processor.process(result)
    assert enriched.enrichment_decision == "BLOCK"
    assert enriched.reason_code == "trader_not_registered"
    assert enriched.lifecycle_processed is True


def test_global_blacklisted_symbol_is_blocked(tmp_path):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    cfg = _minimal_global_config()
    cfg["symbol_blacklist"]["global"] = ["SCAM/USDT"]
    config_file = tmp_path / "operation_config.yaml"
    with config_file.open("w") as f:
        yaml.dump(cfg, f)
    (tmp_path / "traders").mkdir()
    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)
    proc = SignalEnrichmentProcessor(
        config_loader=OperationConfigLoader(str(tmp_path)),
        repository=EnrichedCanonicalMessageRepository(db_path),
    )
    result = _make_parse_result(symbol="SCAM/USDT")
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "BLOCK"
    assert enriched.reason_code == "symbol_blacklisted_global"
    assert enriched.lifecycle_processed is True


def test_per_trader_blacklisted_symbol_is_blocked(tmp_path):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    cfg = _minimal_global_config()
    cfg["symbol_blacklist"]["per_trader"] = {"trader_a": ["RUG/USDT"]}
    config_file = tmp_path / "operation_config.yaml"
    with config_file.open("w") as f:
        yaml.dump(cfg, f)
    (tmp_path / "traders").mkdir()
    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)
    proc = SignalEnrichmentProcessor(
        config_loader=OperationConfigLoader(str(tmp_path)),
        repository=EnrichedCanonicalMessageRepository(db_path),
    )
    result = _make_parse_result(symbol="RUG/USDT")
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "BLOCK"
    assert enriched.reason_code == "symbol_blacklisted_trader"


def test_missing_sl_is_blocked(processor):
    result = _make_parse_result(has_sl=False)
    enriched = processor.process(result)
    assert enriched.enrichment_decision == "BLOCK"
    assert enriched.reason_code == "missing_stop_loss"
    assert enriched.lifecycle_processed is True


def test_unsupported_entry_structure_is_blocked(tmp_path):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    cfg = _minimal_global_config()
    cfg["defaults"]["signal_policy"]["accepted_entry_structures"] = ["ONE_SHOT"]
    config_file = tmp_path / "operation_config.yaml"
    with config_file.open("w") as f:
        yaml.dump(cfg, f)
    (tmp_path / "traders").mkdir()
    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)
    proc = SignalEnrichmentProcessor(
        config_loader=OperationConfigLoader(str(tmp_path)),
        repository=EnrichedCanonicalMessageRepository(db_path),
    )
    result = _make_parse_result(entry_structure="TWO_STEP")
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "BLOCK"
    assert enriched.reason_code == "unsupported_entry_structure"


def test_signal_pass_with_tp_trim(tmp_path):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    cfg = _minimal_global_config()
    cfg["defaults"]["signal_policy"]["tp"]["use_tp_count"] = 2
    config_file = tmp_path / "operation_config.yaml"
    with config_file.open("w") as f:
        yaml.dump(cfg, f)
    (tmp_path / "traders").mkdir()
    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)
    proc = SignalEnrichmentProcessor(
        config_loader=OperationConfigLoader(str(tmp_path)),
        repository=EnrichedCanonicalMessageRepository(db_path),
    )
    result = _make_parse_result(tp_count=5)
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "PASS"
    assert enriched.enriched_signal is not None
    assert len(enriched.enriched_signal.take_profits) == 2
    assert any(e.check == "tp_count_trimmed" for e in enriched.enrichment_log)
    log = next(e for e in enriched.enrichment_log if e.check == "tp_count_trimmed")
    assert log.original == "5"
    assert log.result == "2"
    assert enriched.lifecycle_processed is False


def test_signal_pass_has_management_plan(processor):
    result = _make_parse_result(tp_count=2)
    enriched = processor.process(result)
    assert enriched.enrichment_decision == "PASS"
    assert enriched.management_plan is not None
    assert enriched.management_plan.pending_timeout_hours == 24


def test_signal_pass_entry_weights_applied(processor):
    result = _make_parse_result(tp_count=2)
    enriched = processor.process(result)
    assert enriched.enriched_signal is not None
    assert len(enriched.enriched_signal.entries) == 1
    assert enriched.enriched_signal.entries[0].weight == 1.0


def test_idempotency_same_canonical_message_id(processor):
    result = _make_parse_result(has_sl=False, canonical_message_id=99)
    enriched1 = processor.process(result)
    enriched2 = processor.process(result)
    assert enriched1.enrichment_id == enriched2.enrichment_id


def test_trader_override_tp_count_via_yaml(tmp_path):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    cfg = _minimal_global_config()
    config_file = tmp_path / "operation_config.yaml"
    with config_file.open("w") as f:
        yaml.dump(cfg, f)
    traders_dir = tmp_path / "traders"
    traders_dir.mkdir()
    with (traders_dir / "trader_a.yaml").open("w") as f:
        yaml.dump({"signal_policy": {"tp": {"use_tp_count": 2}}}, f)

    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)
    proc = SignalEnrichmentProcessor(
        config_loader=OperationConfigLoader(str(tmp_path)),
        repository=EnrichedCanonicalMessageRepository(db_path),
    )
    result = _make_parse_result(tp_count=3)
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "PASS"
    assert len(enriched.enriched_signal.take_profits) == 2
```

- [ ] **Step 2: Esegui per verificare il fallimento**

```
pytest tests/runtime_v2/signal_enrichment/test_processor_signal.py -v
```
Expected: ImportError — processor non esiste.

- [ ] **Step 3: Implementa il processor con il gate SIGNAL**

```python
# src/runtime_v2/signal_enrichment/processor.py
from __future__ import annotations

import logging

from src.runtime_v2.parser_pipeline.models import CanonicalParseResult
from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
from src.runtime_v2.signal_enrichment.models import (
    EffectiveEnrichmentConfig,
    EnrichedCanonicalMessage,
    EnrichedEntryLeg,
    EnrichedSignalPayload,
    EnrichmentLogEntry,
)
from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository

logger = logging.getLogger(__name__)


class SignalEnrichmentProcessor:
    def __init__(
        self,
        config_loader: OperationConfigLoader,
        repository: EnrichedCanonicalMessageRepository,
    ) -> None:
        self._config = config_loader
        self._repo = repository

    def process(self, result: CanonicalParseResult) -> EnrichedCanonicalMessage:
        existing = self._repo.get_by_canonical_message_id(result.canonical_message_id)
        if existing is not None:
            return existing

        self._config.reload_if_changed()
        trader_id = result.parser_profile
        config = self._config.get_effective_config(trader_id)

        if config is None:
            enriched = self._make_outcome(result, "BLOCK", "trader_not_registered",
                                          lifecycle_processed=True)
        elif not config.enabled:
            enriched = self._make_outcome(result, "BLOCK", "trader_disabled",
                                          lifecycle_processed=True)
        else:
            policy_snapshot = config.model_dump()
            policy_version = self._config.get_policy_version()
            enriched = self._route(result, config, policy_snapshot, policy_version)

        return self._repo.save(enriched)

    # ── Routing ───────────────────────────────────────────────────────────────

    def _route(
        self,
        result: CanonicalParseResult,
        config: EffectiveEnrichmentConfig,
        policy_snapshot: dict,
        policy_version: str,
    ) -> EnrichedCanonicalMessage:
        pc = result.primary_class
        if pc == "SIGNAL":
            return self._process_signal(result, config, policy_snapshot, policy_version)
        if pc == "UPDATE":
            return self._process_update(result, config, policy_snapshot, policy_version)
        return self._pass_direct(result, config, policy_snapshot, policy_version)

    # ── SIGNAL gate ───────────────────────────────────────────────────────────

    def _process_signal(
        self,
        result: CanonicalParseResult,
        config: EffectiveEnrichmentConfig,
        policy_snapshot: dict,
        policy_version: str,
    ) -> EnrichedCanonicalMessage:
        log: list[EnrichmentLogEntry] = []
        signal = result.canonical_message.signal
        trader_id = result.parser_profile
        symbol = signal.symbol or ""

        def block(reason: str) -> EnrichedCanonicalMessage:
            return self._make_outcome(
                result, "BLOCK", reason, lifecycle_processed=True,
                log=log, policy_snapshot=policy_snapshot, policy_version=policy_version,
                config=config,
            )

        # 1. Blacklist globale
        if symbol in self._config.get_symbol_blacklist_global():
            return block("symbol_blacklisted_global")

        # 2. Blacklist per-trader
        if symbol in self._config.get_symbol_blacklist_for_trader(trader_id):
            return block("symbol_blacklisted_trader")

        # 3. Entry structure accettata
        if signal.entry_structure not in config.signal_policy.accepted_entry_structures:
            return block("unsupported_entry_structure")

        # 4. SL richiesto
        if config.signal_policy.sl.require_sl:
            if signal.stop_loss is None or signal.stop_loss.price is None:
                return block("missing_stop_loss")

        # 5. TP trim
        take_profits = list(signal.take_profits)
        use_tp_count = config.signal_policy.tp.use_tp_count
        if use_tp_count is not None and len(take_profits) > use_tp_count:
            original_count = len(take_profits)
            take_profits = take_profits[:use_tp_count]
            log.append(EnrichmentLogEntry(
                check="tp_count_trimmed",
                original=str(original_count),
                result=str(use_tp_count),
            ))

        # 6. Entry split weights
        entries = self._apply_entry_weights(signal, config)

        # 7. Price sanity (se abilitata)
        if config.signal_policy.price_sanity.enabled:
            ranges = config.signal_policy.price_sanity.symbol_ranges.get(symbol)
            if ranges and len(ranges) == 2:
                for tp in take_profits:
                    if not (ranges[0] <= tp.price.value <= ranges[1]):
                        return block("price_out_of_range")

        enriched_signal = EnrichedSignalPayload(
            symbol=symbol or None,
            side=signal.side,
            entry_structure=signal.entry_structure,
            entries=entries,
            take_profits=take_profits,
            stop_loss=signal.stop_loss,
        )

        return EnrichedCanonicalMessage(
            canonical_message_id=result.canonical_message_id,
            raw_message_id=result.raw_message_id,
            trader_id=trader_id,
            account_id=config.account_id,
            primary_class=result.primary_class,
            enrichment_decision="PASS",
            enriched_signal=enriched_signal,
            management_plan=config.management_plan,
            enrichment_log=log,
            policy_snapshot=policy_snapshot,
            policy_version=policy_version,
            lifecycle_processed=False,
        )

    def _apply_entry_weights(self, signal, config: EffectiveEnrichmentConfig) -> list[EnrichedEntryLeg]:
        split = config.signal_policy.entry_split
        structure = signal.entry_structure
        first_leg = signal.entries[0] if signal.entries else None
        entry_type_key = first_leg.entry_type if first_leg else "LIMIT"

        if entry_type_key == "LIMIT":
            limit = split.LIMIT
            if structure == "ONE_SHOT":
                weights_map = dict(limit.single.weights)
            elif structure == "RANGE":
                weights_map = dict(limit.range.weights)
            elif structure == "TWO_STEP":
                weights_map = dict(limit.averaging.weights)
            else:
                weights_map = dict(limit.ladder.weights)
        else:
            market = split.MARKET
            if structure == "TWO_STEP":
                weights_map = dict(market.averaging.weights)
            else:
                weights_map = dict(market.single.weights)

        total = sum(weights_map.values())
        if total > 0 and abs(total - 1.0) > 0.001:
            weights_map = {k: v / total for k, v in weights_map.items()}

        result = []
        for i, leg in enumerate(signal.entries):
            key = f"E{i + 1}"
            result.append(EnrichedEntryLeg(
                sequence=leg.sequence,
                entry_type=leg.entry_type,
                price=leg.price,
                role=leg.role,
                weight=weights_map.get(key, 0.0),
            ))
        return result

    # ── UPDATE gate (Task 7) ──────────────────────────────────────────────────

    def _process_update(
        self,
        result: CanonicalParseResult,
        config: EffectiveEnrichmentConfig,
        policy_snapshot: dict,
        policy_version: str,
    ) -> EnrichedCanonicalMessage:
        raise NotImplementedError("Implementato in Task 7")

    # ── REPORT / INFO ─────────────────────────────────────────────────────────

    def _pass_direct(
        self,
        result: CanonicalParseResult,
        config: EffectiveEnrichmentConfig,
        policy_snapshot: dict,
        policy_version: str,
    ) -> EnrichedCanonicalMessage:
        raise NotImplementedError("Implementato in Task 8")

    # ── Utility ───────────────────────────────────────────────────────────────

    def _make_outcome(
        self,
        result: CanonicalParseResult,
        decision: str,
        reason_code: str | None = None,
        *,
        lifecycle_processed: bool,
        log: list[EnrichmentLogEntry] | None = None,
        policy_snapshot: dict | None = None,
        policy_version: str = "",
        config: EffectiveEnrichmentConfig | None = None,
    ) -> EnrichedCanonicalMessage:
        return EnrichedCanonicalMessage(
            canonical_message_id=result.canonical_message_id,
            raw_message_id=result.raw_message_id,
            trader_id=result.parser_profile,
            account_id=config.account_id if config else "",
            primary_class=result.primary_class,
            enrichment_decision=decision,
            reason_code=reason_code,
            enrichment_log=log or [],
            policy_snapshot=policy_snapshot or {},
            policy_version=policy_version,
            lifecycle_processed=lifecycle_processed,
        )


__all__ = ["SignalEnrichmentProcessor"]
```

- [ ] **Step 4: Esegui i test SIGNAL**

```
pytest tests/runtime_v2/signal_enrichment/test_processor_signal.py -v
```
Expected: tutti PASS (UPDATE e REPORT/INFO danno NotImplementedError, ma non sono testati qui).

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/signal_enrichment/processor.py tests/runtime_v2/signal_enrichment/test_processor_signal.py
git commit -m "feat(prd03): add SignalEnrichmentProcessor with SIGNAL gate"
```

---

## Task 7: Processor — Gate UPDATE

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/processor.py`
- Create: `tests/runtime_v2/signal_enrichment/test_processor_update.py`

- [ ] **Step 1: Scrivi i test UPDATE**

```python
# tests/runtime_v2/signal_enrichment/test_processor_update.py
from __future__ import annotations

import sqlite3
import pytest
import yaml
from pathlib import Path
from datetime import datetime, timezone


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _make_update_result(
    *,
    trader_id: str = "trader_a",
    canonical_message_id: int = 1,
    source_intent: str = "MOVE_STOP",
):
    from src.parser_v2.contracts.canonical_message import (
        CanonicalMessage, TargetActionGroup, ActionItem,
        SetStopOperation,
    )
    from src.parser_v2.contracts.entities import Price
    from src.parser_v2.contracts.context import RawContext, TargetHints
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

    set_stop = SetStopOperation(target_type="PRICE", price=Price(raw="49000", value=49000.0))
    action = ActionItem(
        action_type="SET_STOP",
        set_stop=set_stop,
        source_intent=source_intent,
    )
    tag = TargetActionGroup(
        targeting=TargetHints(scope_hint="SINGLE_SIGNAL"),
        actions=[action],
    )
    canonical = CanonicalMessage(
        parser_profile=trader_id, primary_class="UPDATE",
        parse_status="PARSED", confidence=1.0,
        target_action_groups=[tag],
        raw_context=RawContext(raw_text="test"),
    )
    return CanonicalParseResult(
        raw_message_id=10,
        canonical_message_id=canonical_message_id,
        parser_profile=trader_id,
        primary_class="UPDATE",
        parse_status="PARSED",
        canonical_message=canonical,
        warnings=[],
        parsed_at=datetime.now(timezone.utc),
    )


def _make_processor(tmp_path, config_overrides: dict | None = None):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    base = {
        "account_mode": "single",
        "account": {"id": "main", "capital_base_usdt": 1000.0, "max_leverage": 5,
                     "max_capital_at_risk_pct": 10.0, "hard_max_per_signal_risk_pct": 2.0},
        "registered_traders": ["trader_a"],
        "symbol_blacklist": {"global": [], "per_trader": {}},
        "defaults": {
            "enabled": True, "gate_mode": "block", "hedge_mode": False,
            "signal_policy": {
                "accepted_entry_structures": ["ONE_SHOT"],
                "market_execution": {"mode": "tolerance", "tolerance_pct": 0.5, "range_tolerance_pct": 0.2},
                "entry_split": {
                    "LIMIT": {"single": {"weights": {"E1": 1.0}}, "range": {"split_mode": "endpoints", "weights": {"E1": 0.5, "E2": 0.5}}, "averaging": {"weights": {"E1": 0.7, "E2": 0.3}}, "ladder": {"weights": {"E1": 0.5, "E2": 0.3, "E3": 0.2}}},
                    "MARKET": {"single": {"weights": {"E1": 1.0}}, "averaging": {"weights": {"E1": 0.7, "E2": 0.3}}},
                },
                "tp": {"use_tp_count": None}, "sl": {"use_original_sl": True, "require_sl": True},
                "price_corrections": {"enabled": False, "round_to_tick": False, "clamp_to_exchange_precision": False},
                "price_sanity": {"enabled": False, "symbol_ranges": {}},
            },
            "update_admission": {"MOVE_STOP": True, "MOVE_STOP_TO_BE": False, "CLOSE_FULL": True,
                                  "CLOSE_PARTIAL": True, "CANCEL_PENDING": True, "ADD_ENTRY": False,
                                  "REENTER": False, "MODIFY_ENTRY": False, "MODIFY_TARGETS": False,
                                  "INVALIDATE_SETUP": False},
            "management_plan": {"be_trigger": None, "be_buffer_pct": 0.0,
                "close_distribution": {"mode": "table", "table": {1: [100]}},
                "cancel_pending_by_engine": True, "cancel_pending_on_timeout": True,
                "pending_timeout_hours": 24, "cancel_averaging_pending_after": None,
                "cancel_unfilled_pending_after": None, "risk_freed_by_be": True,
                "protective_sl_mode": "exchange_native_first"},
            "risk": {"mode": "risk_pct_of_capital", "risk_pct_of_capital": 1.0,
                     "risk_usdt_fixed": 10.0, "capital_base_mode": "static_config",
                     "capital_base_usdt": 1000.0, "leverage": 1, "use_trader_risk_hint": False,
                     "max_capital_at_risk_per_trader_pct": 5.0, "max_concurrent_trades": 5,
                     "max_concurrent_same_symbol": 1},
        },
    }
    if config_overrides:
        import copy
        from tests.runtime_v2.signal_enrichment.test_processor_signal import _minimal_global_config
        # merge overrides into defaults
        for k, v in config_overrides.items():
            base["defaults"][k] = v

    config_file = tmp_path / "operation_config.yaml"
    with config_file.open("w") as f:
        yaml.dump(base, f)
    (tmp_path / "traders").mkdir(exist_ok=True)

    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)
    return SignalEnrichmentProcessor(
        config_loader=OperationConfigLoader(str(tmp_path)),
        repository=EnrichedCanonicalMessageRepository(db_path),
    )


def test_admitted_update_intent_passes(tmp_path):
    proc = _make_processor(tmp_path)
    result = _make_update_result(source_intent="MOVE_STOP")
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "PASS"
    assert enriched.enriched_actions is not None
    assert len(enriched.enriched_actions) == 1
    assert enriched.lifecycle_processed is False


def test_blocked_update_intent_blocks(tmp_path):
    proc = _make_processor(tmp_path)
    result = _make_update_result(source_intent="MOVE_STOP_TO_BE")
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "BLOCK"
    assert enriched.reason_code == "action_type_disabled:MOVE_STOP_TO_BE"
    assert enriched.lifecycle_processed is True


def test_warn_mode_produces_review(tmp_path):
    proc = _make_processor(tmp_path, config_overrides={"gate_mode": "warn"})
    result = _make_update_result(source_intent="MOVE_STOP_TO_BE", canonical_message_id=2)
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "REVIEW"
    assert "MOVE_STOP_TO_BE" in (enriched.reason_code or "")
    assert enriched.lifecycle_processed is True
```

- [ ] **Step 2: Esegui per verificare il fallimento**

```
pytest tests/runtime_v2/signal_enrichment/test_processor_update.py -v
```
Expected: NotImplementedError su `_process_update`.

- [ ] **Step 3: Implementa `_process_update` nel processor**

Sostituisci il metodo `_process_update` nel file `src/runtime_v2/signal_enrichment/processor.py`:

```python
    def _process_update(
        self,
        result: CanonicalParseResult,
        config: EffectiveEnrichmentConfig,
        policy_snapshot: dict,
        policy_version: str,
    ) -> EnrichedCanonicalMessage:
        log: list[EnrichmentLogEntry] = []
        trader_id = result.parser_profile
        tags = result.canonical_message.target_action_groups

        for tag in tags:
            for action_item in tag.actions:
                intent = action_item.source_intent
                admitted = config.update_admission.get(intent, False)
                if not admitted:
                    decision = "BLOCK" if config.gate_mode == "block" else "REVIEW"
                    reason = f"action_type_{'disabled' if decision == 'BLOCK' else 'warned'}:{intent}"
                    return EnrichedCanonicalMessage(
                        canonical_message_id=result.canonical_message_id,
                        raw_message_id=result.raw_message_id,
                        trader_id=trader_id,
                        account_id=config.account_id,
                        primary_class=result.primary_class,
                        enrichment_decision=decision,
                        reason_code=reason,
                        enrichment_log=log,
                        policy_snapshot=policy_snapshot,
                        policy_version=policy_version,
                        lifecycle_processed=True,
                    )

        return EnrichedCanonicalMessage(
            canonical_message_id=result.canonical_message_id,
            raw_message_id=result.raw_message_id,
            trader_id=trader_id,
            account_id=config.account_id,
            primary_class=result.primary_class,
            enrichment_decision="PASS",
            enriched_actions=list(tags),
            enrichment_log=log,
            policy_snapshot=policy_snapshot,
            policy_version=policy_version,
            lifecycle_processed=False,
        )
```

- [ ] **Step 4: Esegui i test UPDATE**

```
pytest tests/runtime_v2/signal_enrichment/test_processor_update.py -v
```
Expected: tutti PASS.

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/signal_enrichment/processor.py tests/runtime_v2/signal_enrichment/test_processor_update.py
git commit -m "feat(prd03): add UPDATE admission gate to SignalEnrichmentProcessor"
```

---

## Task 8: Processor — REPORT/INFO routing

**Files:**
- Modify: `src/runtime_v2/signal_enrichment/processor.py`
- Create: `tests/runtime_v2/signal_enrichment/test_processor_routing.py`

- [ ] **Step 1: Scrivi i test di routing**

```python
# tests/runtime_v2/signal_enrichment/test_processor_routing.py
from __future__ import annotations

import sqlite3
import pytest
import yaml
from pathlib import Path
from datetime import datetime, timezone


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _make_result(primary_class: str, trader_id: str = "trader_a", canonical_message_id: int = 1):
    from src.parser_v2.contracts.canonical_message import (
        CanonicalMessage, ReportPayload, InfoPayload, ReportEvent,
    )
    from src.parser_v2.contracts.context import RawContext
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

    if primary_class == "REPORT":
        payload_kwargs = {"report": ReportPayload(events=[ReportEvent(event_type="TP_HIT", source_intent="TP_HIT")])}
    else:
        payload_kwargs = {"info": InfoPayload(raw_fragment="test")}

    canonical = CanonicalMessage(
        parser_profile=trader_id, primary_class=primary_class,
        parse_status="PARSED", confidence=1.0,
        raw_context=RawContext(raw_text="test"),
        **payload_kwargs,
    )
    return CanonicalParseResult(
        raw_message_id=10, canonical_message_id=canonical_message_id,
        parser_profile=trader_id, primary_class=primary_class,
        parse_status="PARSED", canonical_message=canonical,
        warnings=[], parsed_at=datetime.now(timezone.utc),
    )


def _make_processor(tmp_path):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    cfg = {
        "account_mode": "single",
        "account": {"id": "main", "capital_base_usdt": 1000.0, "max_leverage": 5,
                     "max_capital_at_risk_pct": 10.0, "hard_max_per_signal_risk_pct": 2.0},
        "registered_traders": ["trader_a"],
        "symbol_blacklist": {"global": [], "per_trader": {}},
        "defaults": {
            "enabled": True, "gate_mode": "block", "hedge_mode": False,
            "signal_policy": {
                "accepted_entry_structures": ["ONE_SHOT"],
                "market_execution": {"mode": "tolerance", "tolerance_pct": 0.5, "range_tolerance_pct": 0.2},
                "entry_split": {
                    "LIMIT": {"single": {"weights": {"E1": 1.0}}, "range": {"split_mode": "endpoints", "weights": {"E1": 0.5, "E2": 0.5}}, "averaging": {"weights": {"E1": 0.7, "E2": 0.3}}, "ladder": {"weights": {"E1": 0.5, "E2": 0.3, "E3": 0.2}}},
                    "MARKET": {"single": {"weights": {"E1": 1.0}}, "averaging": {"weights": {"E1": 0.7, "E2": 0.3}}},
                },
                "tp": {"use_tp_count": None}, "sl": {"use_original_sl": True, "require_sl": True},
                "price_corrections": {"enabled": False, "round_to_tick": False, "clamp_to_exchange_precision": False},
                "price_sanity": {"enabled": False, "symbol_ranges": {}},
            },
            "update_admission": {"MOVE_STOP": True, "MOVE_STOP_TO_BE": False, "CLOSE_FULL": True,
                                  "CLOSE_PARTIAL": True, "CANCEL_PENDING": True, "ADD_ENTRY": False,
                                  "REENTER": False, "MODIFY_ENTRY": False, "MODIFY_TARGETS": False,
                                  "INVALIDATE_SETUP": False},
            "management_plan": {"be_trigger": None, "be_buffer_pct": 0.0,
                "close_distribution": {"mode": "table", "table": {1: [100]}},
                "cancel_pending_by_engine": True, "cancel_pending_on_timeout": True,
                "pending_timeout_hours": 24, "cancel_averaging_pending_after": None,
                "cancel_unfilled_pending_after": None, "risk_freed_by_be": True,
                "protective_sl_mode": "exchange_native_first"},
            "risk": {"mode": "risk_pct_of_capital", "risk_pct_of_capital": 1.0,
                     "risk_usdt_fixed": 10.0, "capital_base_mode": "static_config",
                     "capital_base_usdt": 1000.0, "leverage": 1, "use_trader_risk_hint": False,
                     "max_capital_at_risk_per_trader_pct": 5.0, "max_concurrent_trades": 5,
                     "max_concurrent_same_symbol": 1},
        },
    }
    config_file = tmp_path / "operation_config.yaml"
    with config_file.open("w") as f:
        yaml.dump(cfg, f)
    (tmp_path / "traders").mkdir(exist_ok=True)
    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)
    return SignalEnrichmentProcessor(
        config_loader=OperationConfigLoader(str(tmp_path)),
        repository=EnrichedCanonicalMessageRepository(db_path),
    )


def test_report_passes_with_lifecycle_processed_true(tmp_path):
    proc = _make_processor(tmp_path)
    result = _make_result("REPORT", canonical_message_id=1)
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "PASS"
    assert enriched.enriched_signal is None
    assert enriched.enriched_actions is None
    assert enriched.management_plan is None
    assert enriched.lifecycle_processed is True


def test_info_passes_with_lifecycle_processed_true(tmp_path):
    proc = _make_processor(tmp_path)
    result = _make_result("INFO", canonical_message_id=2)
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "PASS"
    assert enriched.lifecycle_processed is True
```

- [ ] **Step 2: Esegui per verificare il fallimento**

```
pytest tests/runtime_v2/signal_enrichment/test_processor_routing.py -v
```
Expected: NotImplementedError su `_pass_direct`.

- [ ] **Step 3: Implementa `_pass_direct` nel processor**

Sostituisci il metodo `_pass_direct` in `src/runtime_v2/signal_enrichment/processor.py`:

```python
    def _pass_direct(
        self,
        result: CanonicalParseResult,
        config: EffectiveEnrichmentConfig,
        policy_snapshot: dict,
        policy_version: str,
    ) -> EnrichedCanonicalMessage:
        return EnrichedCanonicalMessage(
            canonical_message_id=result.canonical_message_id,
            raw_message_id=result.raw_message_id,
            trader_id=result.parser_profile,
            account_id=config.account_id,
            primary_class=result.primary_class,
            enrichment_decision="PASS",
            enrichment_log=[],
            policy_snapshot=policy_snapshot,
            policy_version=policy_version,
            lifecycle_processed=True,
        )
```

- [ ] **Step 4: Esegui tutti i test del package**

```
pytest tests/runtime_v2/signal_enrichment/ -v
```
Expected: tutti PASS.

- [ ] **Step 5: Commit**

```
git add src/runtime_v2/signal_enrichment/processor.py tests/runtime_v2/signal_enrichment/test_processor_routing.py
git commit -m "feat(prd03): complete REPORT/INFO routing with lifecycle_processed=True"
```

---

## Task 9: Integration tests

**Files:**
- Create: `tests/runtime_v2/signal_enrichment/test_integration.py`

- [ ] **Step 1: Scrivi i test di integrazione**

```python
# tests/runtime_v2/signal_enrichment/test_integration.py
"""
Integration tests: verifica il flusso end-to-end dal CanonicalParseResult
fino alla persistenza in DB con lifecycle_processed corretto.
"""
from __future__ import annotations

import sqlite3
import pytest
import yaml
from pathlib import Path
from datetime import datetime, timezone


def _apply_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    for f in sorted(Path("db/migrations").glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def _make_signal_result(
    *,
    trader_id: str = "trader_a",
    canonical_message_id: int = 1,
    symbol: str = "BTC/USDT",
    has_sl: bool = True,
    tp_count: int = 2,
):
    from src.parser_v2.contracts.canonical_message import CanonicalMessage, SignalPayload, StopLoss
    from src.parser_v2.contracts.entities import EntryLeg, Price, TakeProfit
    from src.parser_v2.contracts.context import RawContext
    from src.runtime_v2.parser_pipeline.models import CanonicalParseResult

    entries = [EntryLeg(sequence=1, entry_type="LIMIT", price=Price(raw="50000", value=50000.0))]
    tps = [TakeProfit(sequence=i+1, price=Price(raw=str(51000+i*500), value=51000.0+i*500)) for i in range(tp_count)]
    sl = StopLoss(price=Price(raw="49000", value=49000.0)) if has_sl else None
    signal = SignalPayload(symbol=symbol, side="LONG", entry_structure="ONE_SHOT",
                           entries=entries, take_profits=tps, stop_loss=sl, completeness="COMPLETE")
    canonical = CanonicalMessage(parser_profile=trader_id, primary_class="SIGNAL",
                                  parse_status="PARSED", confidence=1.0,
                                  signal=signal, raw_context=RawContext(raw_text="BUY BTC"))
    return CanonicalParseResult(
        raw_message_id=canonical_message_id * 10, canonical_message_id=canonical_message_id,
        parser_profile=trader_id, primary_class="SIGNAL", parse_status="PARSED",
        canonical_message=canonical, warnings=[], parsed_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def setup(tmp_path):
    from src.runtime_v2.signal_enrichment.config_loader import OperationConfigLoader
    from src.runtime_v2.signal_enrichment.repository import EnrichedCanonicalMessageRepository
    from src.runtime_v2.signal_enrichment.processor import SignalEnrichmentProcessor

    op_config_path = Path("config/operation_config.yaml")
    if op_config_path.exists():
        config_dir = str(op_config_path.parent)
    else:
        pytest.skip("config/operation_config.yaml non trovato — esegui Task 2 prima")

    db_path = str(tmp_path / "test.db")
    _apply_migrations(db_path)
    loader = OperationConfigLoader(config_dir)
    repo = EnrichedCanonicalMessageRepository(db_path)
    proc = SignalEnrichmentProcessor(config_loader=loader, repository=repo)
    return proc, repo


def test_signal_pass_persisted_with_lifecycle_zero(setup):
    proc, repo = setup
    result = _make_signal_result(canonical_message_id=100)
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "PASS"
    assert enriched.enrichment_id is not None
    assert enriched.lifecycle_processed is False

    from_db = repo.get_by_canonical_message_id(100)
    assert from_db is not None
    assert from_db.enrichment_decision == "PASS"
    assert from_db.lifecycle_processed is False
    assert from_db.enriched_signal is not None
    assert from_db.management_plan is not None
    assert from_db.policy_version.startswith("sha256:")


def test_signal_block_persisted_with_lifecycle_one(setup):
    proc, repo = setup
    result = _make_signal_result(canonical_message_id=101, has_sl=False)
    enriched = proc.process(result)
    assert enriched.enrichment_decision == "BLOCK"

    from_db = repo.get_by_canonical_message_id(101)
    assert from_db is not None
    assert from_db.lifecycle_processed is True
    assert from_db.enriched_signal is None
    assert from_db.management_plan is None


def test_idempotency_no_duplicate_row(setup):
    proc, repo = setup
    result = _make_signal_result(canonical_message_id=102, has_sl=False)
    e1 = proc.process(result)
    e2 = proc.process(result)
    assert e1.enrichment_id == e2.enrichment_id

    conn = sqlite3.connect(str(Path(repo._db_path)))
    count = conn.execute(
        "SELECT COUNT(*) FROM enriched_canonical_messages WHERE canonical_message_id = 102"
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_policy_snapshot_is_auditabile(setup):
    proc, repo = setup
    result = _make_signal_result(canonical_message_id=103)
    enriched = proc.process(result)
    from_db = repo.get_by_canonical_message_id(103)
    assert isinstance(from_db.policy_snapshot, dict)
    assert "signal_policy" in from_db.policy_snapshot


def test_ops_db_is_empty(tmp_path):
    ops_path = Path("db/ops.sqlite3")
    if not ops_path.exists():
        pytest.skip("db/ops.sqlite3 non trovato — esegui setup_parser_db_separation.py prima")
    conn = sqlite3.connect(str(ops_path))
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name != 'schema_migrations'"
    )]
    conn.close()
    assert tables == [], f"ops.sqlite3 dovrebbe essere vuoto, trovate tabelle: {tables}"
```

- [ ] **Step 2: Esegui i test di integrazione**

```
pytest tests/runtime_v2/signal_enrichment/test_integration.py -v
```
Expected: tutti PASS (tranne skip se config/operation_config.yaml o ops.sqlite3 non esistono).

- [ ] **Step 3: Esegui l'intera suite signal_enrichment**

```
pytest tests/runtime_v2/signal_enrichment/ -v
```
Expected: tutti PASS.

- [ ] **Step 4: Commit**

```
git add tests/runtime_v2/signal_enrichment/test_integration.py
git commit -m "test(prd03): add integration tests for SignalEnrichmentProcessor end-to-end"
```

---

## Task 10: Wiring + Regression

**Files:**
- Modify: `.env`
- Verify: `tests/runtime_v2/test_acceptance.py`

- [ ] **Step 1: Esegui lo script di separazione DB (se non già fatto)**

```
python scripts/setup_parser_db_separation.py
```

- [ ] **Step 2: Verifica che `.env` abbia i nuovi path**

Controlla che `.env` contenga:
```
PARSER_DB_PATH=db/parser.sqlite3
OPS_DB_PATH=db/ops.sqlite3
```

- [ ] **Step 3: Verifica i test di regressione esistenti**

```
pytest tests/runtime_v2/ -v --ignore=tests/runtime_v2/signal_enrichment
```
Expected: tutti PASS. Se falliscono per path DB: la fix è aggiornare la costruzione del `CanonicalMessageRepository` per leggere `PARSER_DB_PATH` invece di `DB_PATH`.

- [ ] **Step 4: Verifica import isolation**

```python
# Esegui questo snippet per verificare che signal_enrichment non importi lifecycle/risk/execution
python -c "
import ast, pathlib
pkg = pathlib.Path('src/runtime_v2/signal_enrichment')
forbidden = ['operation_rules', 'target_resolver', 'lifecycle', 'risk_gate', 'execution']
for f in pkg.glob('*.py'):
    src = f.read_text()
    for term in forbidden:
        assert term not in src, f'{f}: importa modulo vietato {term}'
print('OK: nessun import vietato trovato')
"
```

- [ ] **Step 5: Esegui tutta la test suite**

```
pytest tests/ -v --tb=short -q
```
Expected: tutti PASS.

- [ ] **Step 6: Commit finale**

```
git add .
git commit -m "feat(prd03): complete Signal Enrichment Layer — Gate 1 stateless

- Models: EnrichedCanonicalMessage, ManagementPlanConfig, EffectiveEnrichmentConfig
- Config: OperationConfigLoader con merge, hot-reload, MARKET.range error
- Processor: SIGNAL gate, UPDATE admission, REPORT/INFO routing
- Repository: save + get_by_canonical_message_id, idempotenza
- Migration 027: enriched_canonical_messages con lifecycle_processed
- DB separation: parser.sqlite3 / ops.sqlite3
- Handoff PRD04 via DB: lifecycle_processed=0 solo per SIGNAL/UPDATE PASS

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage check:**

| Sezione spec | Coperta da |
|---|---|
| 3.2 Unica classe con metodi interni | Task 6 `_route` |
| 3.3 Update admission su source_intent | Task 7 `_process_update` |
| 3.4 DB separati da PRD 03 | Task 4 migration + setup script |
| 3.5 Config file centrale + per trader | Task 2, 3 |
| 4.1 operation_config.yaml template | Task 2 |
| 4.2 trader_a.yaml esempio | Task 2 |
| 5.1 EffectiveEnrichmentConfig | Task 1, 3 |
| 5.2 EnrichedCanonicalMessage | Task 1 |
| 5.3 EnrichmentLogEntry | Task 1 |
| 5.4 ManagementPlanConfig | Task 1 |
| 6.1 Routing primary_class | Task 6-8 |
| 6.2 Check SIGNAL (1-9) | Task 6 |
| 6.3 Check UPDATE | Task 7 |
| 6.4 gate_mode: warn | Task 7 test `warn_mode_produces_review` |
| 7.2 Schema DB + lifecycle_processed | Task 4, 5 |
| 9 Flusso live (handoff DB) | Task 5 repository, Task 10 wiring |
| 10.1 Done significa | Task 9 integration tests |
| 10.2 Casi 1-24 | Task 6-9 tests |
| 11.1 Unit tests loader | Task 3 |
| 11.2 Integration | Task 9 |
| 11.3 Regression | Task 10 |
| 12.2 Hot-reload con fallback | Task 3 `test_invalid_yaml_does_not_crash_reload` |
| 12.3 policy_version SHA256 | Task 3 `get_policy_version` |
| 12.4 Entry split weights normalizzati | Task 6 `_apply_entry_weights` |

**Placeholder scan:** nessun TBD/TODO nel piano.

**Type consistency:** tutti i metodi usano `CanonicalParseResult`, `EffectiveEnrichmentConfig`, `EnrichedCanonicalMessage` con nomi coerenti tra task.

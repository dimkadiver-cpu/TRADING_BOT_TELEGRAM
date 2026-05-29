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
    be_fee_correction_enabled: bool = False
    be_fee_fallback_profile: str | None = None
    close_distribution: CloseDistributionConfig = Field(default_factory=CloseDistributionConfig)
    cancel_pending_by_engine: bool = True
    cancel_pending_on_timeout: bool = True
    pending_timeout_hours: int = 24
    cancel_averaging_pending_after: Literal["tp1", "tp2"] | None = None
    cancel_unfilled_pending_after: Literal["tp1", "tp2"] | None = None
    risk_freed_by_be: bool = True
    protective_sl_mode: Literal["exchange_native_first", "bot_managed"] = "exchange_native_first"
    market_convert_mode: Literal["cancel_subsequent", "keep_subsequent"] = "cancel_subsequent"


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
    account: AccountConfig | None = None


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

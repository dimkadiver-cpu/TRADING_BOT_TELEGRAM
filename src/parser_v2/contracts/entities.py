from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import (
    CancelScopeHint,
    Completeness,
    EntryRole,
    EntryStructure,
    EntryType,
    ModifyEntryMode,
    ModifyTargetsMode,
    Side,
)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def normalize_price(raw: str, *, decimal_separator: str = ".", thousands_separator: str | None = None) -> float:
    if not raw or not raw.strip():
        raise ValueError("Cannot normalize empty price")

    value = raw.strip()
    if thousands_separator is not None:
        value = value.replace(thousands_separator, "")
    value = value.replace(" ", "")

    if decimal_separator == ",":
        value = value.replace(".", "").replace(",", ".")
    else:
        value = value.replace(",", "")

    return float(value)


class Price(ContractModel):
    raw: str
    value: float

    @classmethod
    def from_raw(
        cls,
        raw: str,
        *,
        decimal_separator: str = ".",
        thousands_separator: str | None = None,
    ) -> Price:
        return cls(
            raw=raw,
            value=normalize_price(
                raw,
                decimal_separator=decimal_separator,
                thousands_separator=thousands_separator,
            ),
        )


class EntryLeg(ContractModel):
    sequence: int = Field(ge=1)
    entry_type: EntryType
    price: Price | None = None
    role: EntryRole = "UNKNOWN"
    is_optional: bool = False

    @model_validator(mode="after")
    def _validate_entry_price(self) -> EntryLeg:
        if self.entry_type == "LIMIT" and self.price is None:
            raise ValueError("LIMIT entry leg requires price")
        return self


class StopLoss(ContractModel):
    price: Price | None = None


class TakeProfit(ContractModel):
    sequence: int = Field(ge=1)
    price: Price
    label: str | None = None
    close_fraction: float | None = Field(default=None, ge=0.0, le=1.0)


class RiskHint(ContractModel):
    raw: str | None = None
    value: float | None = None
    min_value: float | None = None
    max_value: float | None = None


class SignalFields(ContractModel):
    symbol: str | None = None
    side: Side | None = None
    entry_structure: EntryStructure | None = None
    entries: list[EntryLeg] = Field(default_factory=list)
    stop_loss: StopLoss | None = None
    take_profits: list[TakeProfit] = Field(default_factory=list)
    risk_hint: RiskHint | None = None
    leverage_hint: float | None = None
    missing_fields: list[str] = Field(default_factory=list)
    completeness: Completeness


class IntentEntities(ContractModel):
    pass


class MoveStopToBEEntities(IntentEntities):
    pass


class MoveStopEntities(IntentEntities):
    new_stop_price: Price | None = None
    stop_to_tp_level: int | None = Field(default=None, ge=1)


class CloseFullEntities(IntentEntities):
    close_price: Price | None = None


class ClosePartialEntities(IntentEntities):
    fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    close_price: Price | None = None


class CancelPendingEntities(IntentEntities):
    cancel_scope_hint: CancelScopeHint = "UNKNOWN"


class InvalidateSetupEntities(IntentEntities):
    reason_text: str | None = None


class ReenterEntities(IntentEntities):
    entries: list[Price] = Field(default_factory=list)
    entry_type: EntryType | None = None
    entry_structure: EntryStructure | None = None


class AddEntryEntities(IntentEntities):
    entry_price: Price | None = None
    entry_type: EntryType | None = None


class EntrySelector(ContractModel):
    role: EntryRole | None = None
    sequence: int | None = Field(default=None, ge=1)
    label: str | None = None
    raw: str | None = None


class ModifyEntryEntities(IntentEntities):
    mode: ModifyEntryMode = "UNKNOWN"
    entry_selector: EntrySelector | None = None
    entries: list[EntryLeg] = Field(default_factory=list)
    entry_structure: EntryStructure | None = None
    raw_mode_marker: str | None = None
    raw_selector_marker: str | None = None


class ModifyTargetsEntities(IntentEntities):
    take_profits: list[Price] = Field(default_factory=list)
    target_tp_level: int | None = Field(default=None, ge=1)
    mode: ModifyTargetsMode = "UNKNOWN"


class EntryFilledEntities(IntentEntities):
    level: int | None = Field(default=None, ge=1)
    fill_price: Price | None = None


class TpHitEntities(IntentEntities):
    level: int | None = Field(default=None, ge=1)
    price: Price | None = None


class SlHitEntities(IntentEntities):
    price: Price | None = None


class ExitBeEntities(IntentEntities):
    price: Price | None = None


class ReportResultEntities(IntentEntities):
    raw_summary: str | None = None


class InfoOnlyEntities(IntentEntities):
    raw_fragment: str | None = None

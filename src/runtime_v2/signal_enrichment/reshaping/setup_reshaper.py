from __future__ import annotations

from src.runtime_v2.signal_enrichment.models import (
    ReshapeAudit,
    ReshapeAuditDiscarded,
    ReshapeAuditEntry,
    ReshapeAuditRr,
    ReshapeAuditStopLoss,
    ReshapeAuditTpSelected,
    ReshapeAuditTpSelection,
    ReshapeRejectionInfo,
    ReshapeTemplateConfig,
)
from src.runtime_v2.signal_enrichment.reshaping.tp_rr_selector import (
    compute_anchor,
    select_tps_by_rr,
)
from src.runtime_v2.signal_enrichment.reshaping.reshape_validator import validate_reshape


def apply_reshape(
    *,
    signal_entries: list[tuple[str, float]],
    signal_sl_price: float | None,
    signal_tp_prices: list[float],
    signal_entry_structure: str,
    signal_side: str,
    template: ReshapeTemplateConfig,
    weights_map: dict[str, float],
) -> ReshapeAudit | ReshapeRejectionInfo:
    """Apply a reshape template to a signal.

    signal_entries must be already realigned (E1 = nearest to price for side).
    signal_tp_prices must be the original parsed TPs, before any use_tp_count trim.
    weights_map keys are "E1", "E2", etc. from the flusso normale config.
    """
    rule_id = template.id

    if not _matches(template, signal_entry_structure, len(signal_entries), len(signal_tp_prices)):
        return ReshapeRejectionInfo(rule_id=rule_id, phase="no_match", reason_code="reshape_no_match")

    entries_map = {key: price for key, price in signal_entries}

    operative, discarded = _apply_entries(template.entries, signal_entries)
    if operative is None:
        return ReshapeRejectionInfo(rule_id=rule_id, phase="invalid_output", reason_code=discarded)

    effective_sl, archived_sl, stop_source, sl_err = _apply_stop_loss(
        template.stop_loss, entries_map, signal_sl_price
    )
    if sl_err:
        return ReshapeRejectionInfo(rule_id=rule_id, phase="invalid_output", reason_code=sl_err)

    # If an entry was promoted to SL role, remove it from operative
    if stop_source is not None:
        operative = [(k, p) for k, p in operative if k != stop_source]
    if not operative:
        return ReshapeRejectionInfo(rule_id=rule_id, phase="invalid_output", reason_code="reshape_no_operative_entry")

    # Weights are positional: operative[0] → "E1", operative[1] → "E2", etc.
    # weights_map contains the weights that the flusso normale will assign to the
    # resulting structure (positional keys), read-only.
    operative_with_weights = [
        (price, weights_map.get(f"E{i + 1}", 0.0))
        for i, (key, price) in enumerate(operative)
    ]
    try:
        anchor = compute_anchor(operative_with_weights)
    except ValueError:
        return ReshapeRejectionInfo(rule_id=rule_id, phase="invalid_output", reason_code="reshape_zero_risk_distance")

    r_unit = abs(anchor - effective_sl) if effective_sl is not None else 0.0
    rr_info: ReshapeAuditRr | None = None
    if r_unit > 0 and effective_sl is not None:
        rr_info = ReshapeAuditRr(anchor=anchor, stop=effective_sl, r_unit=r_unit)

    selected_tps, tp_discarded, tp_selected_with_rr, tp_err = _apply_take_profits(
        template.take_profits, signal_tp_prices, anchor, r_unit
    )
    if tp_err:
        return ReshapeRejectionInfo(rule_id=rule_id, phase="invalid_output", reason_code=tp_err)

    operative_prices = [price for _, price in operative]
    reason = validate_reshape(
        operative_prices=operative_prices,
        stop_loss_price=effective_sl,
        take_profits=selected_tps,
        side=signal_side,
        anchor=anchor,
    )
    if reason:
        return ReshapeRejectionInfo(rule_id=rule_id, phase="invalid_output", reason_code=reason)

    return ReshapeAudit(
        rule_id=rule_id,
        discarded_entries=[
            ReshapeAuditDiscarded(source=key, price=price, reason="initial_entry_skipped")
            for key, price in discarded
        ],
        operative_entries=[ReshapeAuditEntry(source=key, price=price) for key, price in operative],
        stop_loss=ReshapeAuditStopLoss(
            source=stop_source,
            price=effective_sl,
            replaced_original=archived_sl,
        ),
        rr=rr_info,
        tp_selection=ReshapeAuditTpSelection(
            mode=template.take_profits.mode,
            selected=[
                ReshapeAuditTpSelected(
                    price=p,
                    rr=round(abs(p - anchor) / r_unit, 4) if r_unit > 0 else None,
                )
                for p in selected_tps
            ],
            discarded=tp_discarded,
        ),
    )


def _matches(
    template: ReshapeTemplateConfig,
    entry_structure: str,
    entry_count: int,
    tp_count: int,
) -> bool:
    if not template.enabled:
        return False
    m = template.match
    if m.entry_structure != entry_structure:
        return False
    if m.normalized_entry_count is not None and m.normalized_entry_count != entry_count:
        return False
    if m.min_entry_count is not None and entry_count < m.min_entry_count:
        return False
    if m.min_tp_count is not None and tp_count < m.min_tp_count:
        return False
    return True


def _apply_entries(
    cfg,
    signal_entries: list[tuple[str, float]],
) -> tuple[list[tuple[str, float]] | None, list[tuple[str, float]] | str]:
    """Returns (operative, discarded) or (None, reason_code)."""
    mode = cfg.mode

    if mode == "keep":
        return list(signal_entries), []

    if mode == "drop":
        drop_set = set(cfg.indexes)
        operative = [(k, p) for k, p in signal_entries if k not in drop_set]
        discarded = [(k, p) for k, p in signal_entries if k in drop_set]
        if not operative:
            return None, "reshape_no_operative_entry"
        return operative, discarded

    if mode == "keep_only":
        keep_set = set(cfg.indexes)
        operative = [(k, p) for k, p in signal_entries if k in keep_set]
        discarded = [(k, p) for k, p in signal_entries if k not in keep_set]
        if not operative:
            return None, "reshape_no_operative_entry"
        return operative, discarded

    if mode == "keep_last":
        n = cfg.n or 1
        if n > len(signal_entries):
            return None, "reshape_keep_n_too_large"
        operative = signal_entries[-n:]
        discarded = signal_entries[:-n]
        return operative, discarded

    if mode == "keep_first":
        n = cfg.n or 1
        if n > len(signal_entries):
            return None, "reshape_keep_n_too_large"
        operative = signal_entries[:n]
        discarded = signal_entries[n:]
        return operative, discarded

    return None, "reshape_unknown_entries_mode"


def _apply_stop_loss(
    cfg,
    entries_map: dict[str, float],
    original_sl: float | None,
) -> tuple[float, float | None, str | None, str | None]:
    """Returns (effective_sl, archived_sl, stop_source, error_code)."""
    mode = cfg.mode

    if mode == "original":
        if original_sl is None:
            return 0.0, None, None, "reshape_missing_original_sl"
        return original_sl, None, None, None

    if mode == "from_entry":
        entry_key = cfg.entry
        if entry_key not in entries_map:
            return 0.0, None, None, "reshape_entry_index_absent"
        new_sl = entries_map[entry_key]
        return new_sl, original_sl, entry_key, None

    if mode == "from_distance_pct":
        if original_sl is None:
            return 0.0, None, None, "reshape_missing_original_sl"
        return original_sl, None, None, "reshape_from_distance_pct_not_implemented_v1"

    return 0.0, None, None, "reshape_unknown_stop_mode"


def _apply_take_profits(
    cfg,
    signal_tp_prices: list[float],
    anchor: float,
    r_unit: float,
) -> tuple[list[float], list[float], list[dict] | None, str | None]:
    """Returns (selected, discarded, tp_with_rr_detail, error_code)."""
    mode = cfg.mode

    if mode == "keep_all":
        return list(signal_tp_prices), [], None, None

    if mode == "drop":
        drop_1based = set(cfg.indexes)
        selected = [p for i, p in enumerate(signal_tp_prices, start=1) if i not in drop_1based]
        discarded = [p for i, p in enumerate(signal_tp_prices, start=1) if i in drop_1based]
        if not selected:
            return [], [], None, "reshape_no_take_profit"
        return selected, discarded, None, None

    if mode == "count":
        n = cfg.n or len(signal_tp_prices)
        selected = signal_tp_prices[:n]
        discarded = signal_tp_prices[n:]
        if not selected:
            return [], [], None, "reshape_no_take_profit"
        return selected, discarded, None, None

    if mode == "by_rr":
        if r_unit <= 0:
            return [], [], None, "reshape_zero_risk_distance"
        selected = select_tps_by_rr(
            tp_prices=signal_tp_prices,
            desired_rr=cfg.desired_rr,
            anchor=anchor,
            r_unit=r_unit,
            strategy=cfg.strategy,
            max_rr_deviation_abs=cfg.max_rr_deviation_abs,
            on_missing_target=cfg.on_missing_target,
        )
        if selected is None:
            return [], [], None, "reshape_no_tp_in_tolerance"
        discarded = [p for p in signal_tp_prices if p not in set(selected)]
        return selected, discarded, None, None

    return [], [], None, "reshape_unknown_tp_mode"


__all__ = ["apply_reshape"]

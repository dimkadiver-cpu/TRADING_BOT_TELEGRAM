"""Normalize canonical bot signals into freqtrade-ready contexts."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Entry leg runtime model
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class FreqtradeEntryLeg:
    """Runtime model of a single entry leg in the signal execution plan.

    Attributes:
        entry_id    Canonical leg identifier: E1, E2, E3, ...
        sequence    1-based position in the plan (1 = first leg).
        order_type  MARKET or LIMIT.
        price       Limit price; None for MARKET legs.
        split       Fraction of the total stake allocated to this leg (0.0–1.0).
        role        Optional semantic label (e.g. "averaging").
    """

    entry_id: str
    sequence: int
    order_type: str
    price: float | None
    split: float
    role: str | None = None


@dataclass(slots=True, frozen=True)
class FreqtradeRuntimeEntryLeg:
    """Execution-plan leg with persisted runtime status from trades.meta_json."""

    entry_id: str
    sequence: int
    order_type: str
    price: float | None
    split: float
    status: str
    role: str | None = None
    filled_at: str | None = None


# ---------------------------------------------------------------------------
# Entry price policy
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class EntryPricePolicy:
    """Runtime policy that governs whether a freqtrade-proposed fill rate is acceptable.

    This is the *runtime* gate — it runs inside ``confirm_trade_entry()`` in
    ``SignalBridgeStrategy`` and validates the actual fill rate against
    ``FreqtradeSignalContext.entry_prices``.

    Note on scope vs ``price_sanity``:
        ``price_sanity`` (Gate 9 in ``src/operation_rules/engine.py``) is a
        *parse-time* gate. It validates entry prices from the parsed signal text
        against static ``symbol_ranges`` in the config YAML, **before** the signal
        is written to the DB. It does NOT operate at runtime and cannot guard against
        fill-price drift after signal creation.

        ``EntryPricePolicy`` (this class) is the complementary *runtime* gate.
        The two are independent; both can be active simultaneously without conflict.

    Parameters:
        enabled          If False, all rates are accepted (no-op).
        max_slippage_pct Max bilateral deviation from E1 for single-price entries.
                         0.005 = 0.5% (e.g. E1=66100, reject if |rate-66100|/66100 > 0.005).
        zone_tolerance_pct
                         Tolerance applied at both edges of a multi-price zone.
                         0.002 = 0.2% (e.g. zone [66100,66200] + 0.2% → [66100*0.998, 66200*1.002]).
    """

    enabled: bool = True
    max_slippage_pct: float = 0.005
    zone_tolerance_pct: float = 0.002


_DEFAULT_ENTRY_PRICE_POLICY = EntryPricePolicy()
_PERMISSIVE_ENTRY_PRICE_POLICY = EntryPricePolicy(enabled=False)


def resolve_entry_price_policy(
    context_management_rules: dict[str, Any] | None,
    runtime_config: dict[str, Any] | None,
) -> EntryPricePolicy:
    """Resolve the effective entry price policy.

    Priority (highest to lowest):
    1. ``management_rules["entry_policy"]`` — snapshotted per-signal from operation_rules config.
    2. ``config["execution"]["entry_price_policy"]`` — runtime freqtrade config.
    3. Default policy (enabled=True, max_slippage_pct=0.5%, zone_tolerance_pct=0.2%).

    Any missing key falls through to the next level; individual fields within a level
    can be partial overrides.
    """
    # Collect raw dict from management_rules snapshot
    raw: dict[str, Any] = {}
    if isinstance(context_management_rules, dict):
        ep = context_management_rules.get("entry_policy")
        if isinstance(ep, dict):
            raw = ep

    # Fall back to runtime config if snapshot has no entry_policy key
    if not raw and isinstance(runtime_config, dict):
        execution_cfg = runtime_config.get("execution")
        if isinstance(execution_cfg, dict):
            ep2 = execution_cfg.get("entry_price_policy")
            if isinstance(ep2, dict):
                raw = ep2

    if not raw:
        return _DEFAULT_ENTRY_PRICE_POLICY

    enabled = bool(raw.get("enabled", True))
    max_slippage_pct = float(raw.get("max_slippage_pct", 0.005))
    zone_tolerance_pct = float(raw.get("zone_tolerance_pct", 0.002))
    return EntryPricePolicy(
        enabled=enabled,
        max_slippage_pct=max_slippage_pct,
        zone_tolerance_pct=zone_tolerance_pct,
    )


def check_entry_rate(
    entry_prices: tuple[dict[str, Any], ...],
    rate: float,
    order_type: str,
    policy: EntryPricePolicy,
) -> dict[str, Any] | None:
    """Check whether *rate* is within the policy bounds for *entry_prices*.

    Returns:
        None   — rate is acceptable (no rejection).
        dict   — rejection info with keys: reason, rate, e1, e2, deviation_pct, policy_pct.

    Validation rules:
    - Policy disabled or MARKET order_type → always None (acceptable).
    - No entry prices → always None (no constraint).
    - First entry is MARKET type → always None.
    - Single LIMIT price (n=1): |rate - E1| / E1 <= max_slippage_pct.
    - Multi LIMIT prices (n≥2): min(prices)*(1-tol) <= rate <= max(prices)*(1+tol).
    """
    if not policy.enabled:
        return None

    normalized_ot = str(order_type or "").strip().upper()
    if normalized_ot == "MARKET":
        return None

    if not entry_prices:
        return None

    first_type = str(entry_prices[0].get("type") or "LIMIT").upper()
    if first_type == "MARKET":
        return None

    # Collect valid limit prices
    limit_prices: list[float] = []
    for ep in entry_prices:
        if str(ep.get("type") or "LIMIT").upper() == "MARKET":
            break  # stop at first MARKET — mixed plans use market for E1
        p = ep.get("price")
        if p is not None:
            try:
                limit_prices.append(float(p))
            except (TypeError, ValueError):
                pass

    if not limit_prices:
        return None

    e1 = limit_prices[0]
    e2 = limit_prices[-1] if len(limit_prices) > 1 else None

    if len(limit_prices) == 1:
        # Single limit: bilateral tolerance around E1
        if e1 <= 0:
            return None
        deviation_pct = abs(rate - e1) / e1
        if deviation_pct > policy.max_slippage_pct:
            return {
                "reason": "rate_outside_limit_tolerance",
                "rate": rate,
                "e1": e1,
                "e2": None,
                "deviation_pct": deviation_pct,
                "policy_pct": policy.max_slippage_pct,
            }
        return None

    # Multi-price: zone check [min - tol, max + tol]
    lo = min(limit_prices)
    hi = max(limit_prices)
    tol = policy.zone_tolerance_pct
    lo_bound = lo * (1.0 - tol)
    hi_bound = hi * (1.0 + tol)

    if rate < lo_bound:
        deviation_pct = (lo_bound - rate) / lo_bound if lo_bound > 0 else 0.0
        return {
            "reason": "rate_below_zone",
            "rate": rate,
            "e1": lo,
            "e2": hi,
            "deviation_pct": deviation_pct,
            "policy_pct": tol,
        }
    if rate > hi_bound:
        deviation_pct = (rate - hi_bound) / hi_bound if hi_bound > 0 else 0.0
        return {
            "reason": "rate_above_zone",
            "rate": rate,
            "e1": lo,
            "e2": hi,
            "deviation_pct": deviation_pct,
            "policy_pct": tol,
        }
    return None


def persist_entry_rejected_event(
    db_path: str,
    attempt_key: str,
    event_type: str,
    payload_info: dict[str, Any],
) -> None:
    """Persist a generic entry rejection event to the events table."""
    parts = attempt_key.split("_", 3)
    env = parts[0] if len(parts) > 0 else "T"
    channel_id = parts[1] if len(parts) > 1 else "unknown"
    telegram_msg_id = parts[2] if len(parts) > 2 else "0"
    trader_id = parts[3] if len(parts) > 3 else ""

    now_ts = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(payload_info)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO events
                  (env, channel_id, telegram_msg_id, trader_id, trader_prefix,
                   attempt_key, event_type, payload_json, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?)
                """,
                (env, channel_id, telegram_msg_id, trader_id, trader_id[:4].upper(),
                 attempt_key, event_type, payload, now_ts),
            )
    except sqlite3.Error:
        pass  # Non-fatal: audit trail is best-effort; entry rejection is already enforced.


def persist_entry_price_rejected_event(
    db_path: str,
    attempt_key: str,
    rejection_info: dict[str, Any],
) -> None:
    """Persist an ENTRY_PRICE_REJECTED event to the events table."""
    persist_entry_rejected_event(
        db_path=db_path,
        attempt_key=attempt_key,
        event_type="ENTRY_PRICE_REJECTED",
        payload_info=rejection_info,
    )


# ---------------------------------------------------------------------------
# Position management: auto_apply_intents filter
# ---------------------------------------------------------------------------

#: ``machine_event.rules`` are evaluated by freqtrade callbacks (post-fill / stop events),
#: not by Telegram UPDATE auto-application.
#:
#: Supported behavior:
#: - ``trader_hint.auto_apply_intents`` controls which Telegram UPDATE intents are auto-applied.
#: - ``machine_event.rules`` controls callback-driven reactions such as
#:   ``TP_EXECUTED -> MOVE_STOP_TO_BE``.
#:
#: Therefore when ``position_management.mode == "machine_event"``, Telegram UPDATE
#: directives are blocked here so event-driven management becomes the sole runtime
#: source of truth for management actions.
MACHINE_EVENT_RULES_NOT_SUPPORTED: bool = False

#: price_corrections is declared in operation_rules config and persisted in
#: ``operational_signals.price_corrections_json`` for audit purposes only.
#:
#: At runtime, no adjustment or correction is applied to entry or stop-loss prices
#: based on this field. The router always writes ``price_corrections_json = None``.
#:
#: Supported alternative: ``EntryPricePolicy`` governs runtime fill price validation
#: (slippage tolerance / zone tolerance) via ``confirm_trade_entry()``.
#: If price correction logic is needed in the future, implement it in this module.
PRICE_CORRECTIONS_NOT_SUPPORTED: bool = True


def resolve_allowed_update_intents(
    management_rules: dict[str, Any] | None,
) -> frozenset[str] | None:
    """Resolve the set of UPDATE intents that the runtime auto-applies.

    Returns:
        None            → no filter; all eligible intents are applied (backward compat default).
        frozenset[str]  → only intents in this set are applied; others are received but ignored.

    Rules:
    - ``management_rules`` absent or None → None (allow all).
    - mode ``machine_event`` → empty frozenset (block Telegram auto-apply; use callback-driven rules only).
    - mode ``trader_hint`` or ``hybrid`` → read ``trader_hint.auto_apply_intents``.
      - If the list is empty or absent → None (allow all; backward compat).
      - If the list is non-empty → frozenset of those intent names.
    """
    if not isinstance(management_rules, dict):
        return None

    mode = str(management_rules.get("mode") or "hybrid").lower()

    if mode == "machine_event":
        # machine_event mode delegates management to callback-driven rules.
        return frozenset()

    # trader_hint or hybrid: consult auto_apply_intents
    trader_hint = management_rules.get("trader_hint")
    if not isinstance(trader_hint, dict):
        return None  # not configured → allow all

    auto_apply = trader_hint.get("auto_apply_intents")
    if not isinstance(auto_apply, list) or not auto_apply:
        return None  # empty list or absent → allow all (backward compat)

    return frozenset(str(intent).upper() for intent in auto_apply if isinstance(intent, str))


def is_machine_event_mode(management_rules: dict[str, Any] | None) -> bool:
    """Return True when position_management.mode is ``machine_event``."""
    if not isinstance(management_rules, dict):
        return False
    return str(management_rules.get("mode") or "").lower() == "machine_event"


@dataclass(slots=True, frozen=True)
class FreqtradeUpdateDirective:
    """Normalized execution-side view of a targeted UPDATE message."""

    update_op_signal_id: int
    intent: str
    eligibility: str
    close_fraction: float | None = None
    cancel_scope: str | None = None
    new_stop_level: float | str | None = None


@dataclass(slots=True)
class FreqtradeSignalContext:
    """Execution-side view consumed by the SignalBridge strategy."""

    env: str
    attempt_key: str
    trader_id: str
    symbol: str | None
    pair: str | None
    signal_side: str | None
    side: str | None
    entry_tag: str
    stake_amount: float | None
    leverage: int | None
    signal_status: str | None
    is_blocked: bool
    block_reason: str | None
    stoploss_ref: float | None
    take_profit_refs: tuple[float, ...]
    management_rules: dict[str, Any] | None
    op_signal_id: int | None
    trade_state: str | None
    protective_orders_mode: str | None
    trade_meta: dict[str, Any] | None
    update_directives: tuple[FreqtradeUpdateDirective, ...]
    entry_prices: tuple[dict[str, Any], ...]
    """Raw entry_json rows from signals table: [{"price": float, "type": str}, ...]."""
    entry_split: dict[str, float] | None
    """Split weights from operational_signals.entry_split_json, e.g. {"E1": 0.5, "E2": 0.5}."""
    entry_legs: tuple[FreqtradeEntryLeg, ...] = field(default_factory=tuple)
    """Ordered execution plan built from entry_json + entry_split_json."""

    @property
    def first_entry_leg(self) -> FreqtradeEntryLeg | None:
        """First leg in the entry plan, or None when no entries are defined."""
        return self.entry_legs[0] if self.entry_legs else None

    @property
    def runtime_entry_legs(self) -> tuple[FreqtradeRuntimeEntryLeg, ...]:
        """Entry plan augmented with persisted leg status from trade metadata."""
        return _resolve_runtime_entry_legs(
            planned_entry_legs=self.entry_legs,
            trade_meta=self.trade_meta,
        )

    @property
    def next_pending_entry_leg(self) -> FreqtradeRuntimeEntryLeg | None:
        """Next leg still pending in the runtime plan, if any."""
        for leg in self.runtime_entry_legs:
            if leg.status == "PENDING":
                return leg
        return None

    @property
    def pending_limit_entry_legs(self) -> tuple[FreqtradeRuntimeEntryLeg, ...]:
        """Pending entry legs that still require LIMIT execution."""
        return tuple(
            leg
            for leg in self.runtime_entry_legs
            if leg.status == "PENDING" and leg.order_type == "LIMIT"
        )

    @property
    def market_entry_required(self) -> bool:
        """True when the first entry leg is MARKET."""
        leg = self.first_entry_leg
        return leg is not None and leg.order_type == "MARKET"

    @property
    def limit_entry_required(self) -> bool:
        """True when the first entry leg is LIMIT."""
        leg = self.first_entry_leg
        return leg is not None and leg.order_type == "LIMIT"

    @property
    def first_entry_price(self) -> float | None:
        """First price in the entry plan — single-entry policy: first_in_plan.

        Returns the price of entry_prices[0] (E1 in the split plan):
        - SINGLE_LIMIT / LIMIT_WITH_LIMIT_AVERAGING → first limit level
        - ZONE endpoints → lower endpoint (E1)
        - MARKET → None (caller should fall back to proposed_rate)
        Returns None when no entry price is available.
        """
        if not self.entry_prices:
            return None
        first = self.entry_prices[0]
        price = first.get("price")
        if price is None:
            return None
        try:
            return float(price)
        except (TypeError, ValueError):
            return None

    @property
    def first_entry_order_type(self) -> str:
        """Order type of the first entry in the plan: LIMIT or MARKET.

        Derived from first_entry_leg when entry_legs are present; falls back to
        reading entry_prices directly for backward compatibility with contexts
        constructed without entry_legs.
        """
        leg = self.first_entry_leg
        if leg is not None:
            return leg.order_type
        if not self.entry_prices:
            return "MARKET"
        return str(self.entry_prices[0].get("type") or "LIMIT").upper()

    @property
    def is_pair_mappable(self) -> bool:
        return bool(self.pair)

    @property
    def is_executable(self) -> bool:
        return (
            bool(self.attempt_key)
            and self.signal_status == "PENDING"
            and not self.is_blocked
            and not self.cancel_pending_requested
            and self.is_pair_mappable
            and self.side in {"long", "short"}
            and self.stake_amount is not None
            and self.stake_amount > 0
            and self.leverage is not None
            and self.leverage > 0
        )

    @property
    def allowed_update_directives(self) -> tuple[FreqtradeUpdateDirective, ...]:
        """Update directives filtered by position_management.trader_hint.auto_apply_intents.

        Only directives whose intent appears in ``auto_apply_intents`` (when the list is
        non-empty) are returned. If the list is empty, absent, or management_rules is None,
        all directives pass through (backward-compatible permissive default).

        Note: when mode is ``machine_event`` Telegram UPDATE directives are blocked here;
        management is delegated to callback-driven machine_event rules.
        """
        allowed = resolve_allowed_update_intents(self.management_rules)
        if allowed is None:
            return self.update_directives
        return tuple(d for d in self.update_directives if d.intent in allowed)

    @property
    def close_full_requested(self) -> bool:
        return any(directive.intent == "U_CLOSE_FULL" for directive in self.allowed_update_directives)

    @property
    def cancel_pending_requested(self) -> bool:
        return any(directive.intent == "U_CANCEL_PENDING" for directive in self.allowed_update_directives)

    @property
    def latest_partial_close(self) -> FreqtradeUpdateDirective | None:
        partials = [directive for directive in self.allowed_update_directives if directive.intent == "U_CLOSE_PARTIAL"]
        if not partials:
            return None
        return max(partials, key=lambda directive: directive.update_op_signal_id)

    @property
    def partial_close_fraction(self) -> float | None:
        directive = self.latest_partial_close
        if directive is None:
            return None
        if self.last_applied_partial_update_id is not None and directive.update_op_signal_id <= self.last_applied_partial_update_id:
            return None
        return directive.close_fraction

    @property
    def partial_close_update_id(self) -> int | None:
        directive = self.latest_partial_close
        if directive is None:
            return None
        if self.partial_close_fraction is None:
            return None
        return directive.update_op_signal_id

    @property
    def last_applied_partial_update_id(self) -> int | None:
        if not isinstance(self.trade_meta, dict):
            return None
        value = self.trade_meta.get("last_partial_exit_update_id")
        return int(value) if isinstance(value, (int, float)) else None


def canonical_symbol_to_freqtrade_pair(
    symbol: str | None,
    *,
    quote_asset: str = "USDT",
    futures_suffix: str = ":USDT",
) -> str | None:
    """Convert the bot canonical symbol format into a freqtrade pair."""
    if not symbol:
        return None

    normalized = str(symbol).strip().upper()
    if not normalized:
        return None

    if "/" in normalized:
        base, quote_part = normalized.split("/", 1)
        quote = quote_part.split(":", 1)[0]
        if not base or quote != quote_asset:
            return None
        return f"{base}/{quote_asset}{futures_suffix}"

    if normalized.endswith(quote_asset) and len(normalized) > len(quote_asset):
        base = normalized[: -len(quote_asset)]
        return f"{base}/{quote_asset}{futures_suffix}"

    return None


def canonical_side_to_freqtrade_side(side: str | None) -> str | None:
    """Convert canonical bot sides into the freqtrade long/short side."""
    if not side:
        return None

    normalized = str(side).strip().upper()
    if normalized in {"BUY", "LONG"}:
        return "long"
    if normalized in {"SELL", "SHORT"}:
        return "short"
    return None


def load_contexts_for_pair(
    pair: str,
    db_path: str,
    *,
    statuses: tuple[str, ...] | None = None,
) -> list[FreqtradeSignalContext]:
    """Load contexts whose normalized pair matches *pair*."""
    if not pair:
        return []

    rows = _fetch_signal_rows(db_path, statuses=statuses)
    normalized_pair = str(pair).strip().upper()
    return [
        context
        for context in (_row_to_context(row, db_path=db_path) for row in rows)
        if (context.pair or "").upper() == normalized_pair
    ]


def load_pending_contexts_for_pair(pair: str, db_path: str) -> list[FreqtradeSignalContext]:
    """Load pending contexts whose normalized pair matches *pair*."""
    return load_contexts_for_pair(pair, db_path, statuses=("PENDING",))


def load_active_contexts_for_pair(pair: str, db_path: str) -> list[FreqtradeSignalContext]:
    """Load active contexts whose normalized pair matches *pair*."""
    return load_contexts_for_pair(pair, db_path, statuses=("ACTIVE",))


def load_context_by_attempt_key(attempt_key: str, db_path: str) -> FreqtradeSignalContext | None:
    """Load a single execution context by attempt_key."""
    if not attempt_key:
        return None

    rows = _fetch_rows(
        db_path,
        """
        SELECT
          s.env,
          s.attempt_key,
          s.trader_id,
          s.symbol,
          s.side,
          s.status,
          s.sl,
          s.tp_json,
          s.entry_json,
          os.op_signal_id,
          os.is_blocked,
          os.block_reason,
          os.position_size_usdt,
          os.leverage,
          os.management_rules_json,
          os.entry_split_json,
          tr.state AS trade_state,
          tr.protective_orders_mode AS trade_protective_orders_mode,
          tr.meta_json AS trade_meta_json
        FROM signals s
        LEFT JOIN operational_signals os
          ON os.op_signal_id = (
            SELECT inner_os.op_signal_id
            FROM operational_signals inner_os
            WHERE inner_os.attempt_key = s.attempt_key
              AND inner_os.message_type = 'NEW_SIGNAL'
            ORDER BY inner_os.op_signal_id DESC
            LIMIT 1
          )
        LEFT JOIN trades tr
          ON tr.env = s.env
         AND tr.attempt_key = s.attempt_key
        WHERE s.attempt_key = ?
        LIMIT 1
        """,
        [attempt_key],
    )
    if not rows:
        return None
    return _row_to_context(rows[0], db_path=db_path)


def load_targeted_update_directives(
    attempt_key: str,
    db_path: str,
    *,
    allowed_eligibility: tuple[str, ...] = ("ELIGIBLE",),
) -> list[FreqtradeUpdateDirective]:
    """Return normalized UPDATE directives targeting *attempt_key*."""
    if not attempt_key:
        return []

    rows = _fetch_rows(
        db_path,
        """
        SELECT
          base_os.op_signal_id,
          update_os.op_signal_id AS update_op_signal_id,
          update_os.target_eligibility,
          update_os.resolved_target_ids,
          pr.parse_result_normalized_json
        FROM operational_signals base_os
        JOIN operational_signals update_os
          ON update_os.message_type = 'UPDATE'
        JOIN parse_results pr
          ON pr.parse_result_id = update_os.parse_result_id
        WHERE base_os.attempt_key = ?
          AND base_os.message_type = 'NEW_SIGNAL'
        ORDER BY update_os.op_signal_id ASC
        """,
        [attempt_key],
    )
    if not rows:
        return []

    allowed = {value.upper() for value in allowed_eligibility}
    directives: list[FreqtradeUpdateDirective] = []
    for row in rows:
        base_op_signal_id = row["op_signal_id"]
        target_eligibility = str(row["target_eligibility"] or "").upper()
        if base_op_signal_id is None or target_eligibility not in allowed:
            continue
        target_ids = _safe_json_loads(row["resolved_target_ids"])
        if not isinstance(target_ids, list) or int(base_op_signal_id) not in {int(value) for value in target_ids if isinstance(value, (int, float))}:
            continue
        payload = _safe_json_loads(row["parse_result_normalized_json"])
        update_intents = payload.get("intents") if isinstance(payload, dict) else None
        entities = payload.get("entities") if isinstance(payload, dict) else None
        if not isinstance(update_intents, list):
            continue
        for intent in update_intents:
            normalized_intent = _normalize_update_intent(intent)
            if normalized_intent is None:
                continue
            directives.append(
                FreqtradeUpdateDirective(
                    update_op_signal_id=int(row["update_op_signal_id"]),
                    intent=normalized_intent,
                    eligibility=target_eligibility,
                    close_fraction=_normalize_close_fraction(entities),
                    cancel_scope=_normalize_cancel_scope(entities),
                    new_stop_level=_normalize_new_stop_level(entities),
                )
            )
    return directives


def load_targeted_update_intents(
    attempt_key: str,
    db_path: str,
    *,
    allowed_eligibility: tuple[str, ...] = ("ELIGIBLE",),
) -> list[str]:
    """Return UPDATE intents that target the NEW_SIGNAL identified by *attempt_key*."""
    return [
        directive.intent
        for directive in load_targeted_update_directives(
            attempt_key,
            db_path,
            allowed_eligibility=allowed_eligibility,
        )
    ]


def has_eligible_close_full_update(attempt_key: str, db_path: str) -> bool:
    """Return True when an eligible UPDATE carrying U_CLOSE_FULL targets *attempt_key*."""
    return "U_CLOSE_FULL" in load_targeted_update_intents(attempt_key, db_path)


def _fetch_rows(db_path: str, query: str, params: list[Any] | None = None) -> list[sqlite3.Row]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn.execute(query, params or []).fetchall()


def _fetch_signal_rows(db_path: str, *, statuses: tuple[str, ...] | None) -> list[sqlite3.Row]:
    where_clause = ""
    params: list[Any] = []
    if statuses:
        where_clause = "WHERE s.status IN ({placeholders})".format(placeholders=",".join("?" for _ in statuses))
        params.extend(statuses)
    return _fetch_rows(
        db_path,
        f"""
        SELECT
          s.env,
          s.attempt_key,
          s.trader_id,
          s.symbol,
          s.side,
          s.status,
          s.sl,
          s.tp_json,
          s.entry_json,
          os.op_signal_id,
          os.is_blocked,
          os.block_reason,
          os.position_size_usdt,
          os.leverage,
          os.management_rules_json,
          os.entry_split_json,
          tr.state AS trade_state,
          tr.protective_orders_mode AS trade_protective_orders_mode,
          tr.meta_json AS trade_meta_json
        FROM signals s
        LEFT JOIN operational_signals os
          ON os.op_signal_id = (
            SELECT inner_os.op_signal_id
            FROM operational_signals inner_os
            WHERE inner_os.attempt_key = s.attempt_key
              AND inner_os.message_type = 'NEW_SIGNAL'
            ORDER BY inner_os.op_signal_id DESC
            LIMIT 1
          )
        LEFT JOIN trades tr
          ON tr.env = s.env
         AND tr.attempt_key = s.attempt_key
        {where_clause}
        ORDER BY s.created_at ASC, s.rowid ASC
        """,
        params,
    )


def _build_entry_legs(
    entry_prices: tuple[dict[str, Any], ...],
    entry_split: dict[str, float] | None,
) -> tuple[FreqtradeEntryLeg, ...]:
    """Build the ordered entry leg plan from parsed entry_json + entry_split_json.

    Split rules:
    - entry_split present: use mapped weight for each E{n} key.
    - entry_split absent, single leg: split = 1.0.
    - entry_split absent, N legs: split = 1/N (uniform).
    """
    if not entry_prices:
        return ()

    n = len(entry_prices)
    uniform_split = 1.0 / n

    legs: list[FreqtradeEntryLeg] = []
    for i, entry in enumerate(entry_prices):
        entry_id = f"E{i + 1}"
        order_type = str(entry.get("type") or "LIMIT").upper()
        price_raw = entry.get("price")
        try:
            price: float | None = float(price_raw) if price_raw is not None else None
        except (TypeError, ValueError):
            price = None

        if entry_split and entry_id in entry_split:
            try:
                split = float(entry_split[entry_id])
            except (TypeError, ValueError):
                split = uniform_split
        else:
            split = 1.0 if n == 1 else uniform_split

        legs.append(
            FreqtradeEntryLeg(
                entry_id=entry_id,
                sequence=i + 1,
                order_type=order_type,
                price=price,
                split=split,
            )
        )

    return tuple(legs)


def _resolve_runtime_entry_legs(
    *,
    planned_entry_legs: tuple[FreqtradeEntryLeg, ...],
    trade_meta: dict[str, Any] | None,
) -> tuple[FreqtradeRuntimeEntryLeg, ...]:
    serialized_runtime_legs = trade_meta.get("entry_legs") if isinstance(trade_meta, dict) else None
    if isinstance(serialized_runtime_legs, list):
        parsed_runtime_legs = tuple(
            runtime_leg
            for runtime_leg in (
                _parse_runtime_entry_leg(item)
                for item in serialized_runtime_legs
            )
            if runtime_leg is not None
        )
        if parsed_runtime_legs:
            return parsed_runtime_legs

    return tuple(
        FreqtradeRuntimeEntryLeg(
            entry_id=leg.entry_id,
            sequence=leg.sequence,
            order_type=leg.order_type,
            price=leg.price,
            split=leg.split,
            status="PENDING",
            role=leg.role,
        )
        for leg in planned_entry_legs
    )


def _parse_runtime_entry_leg(payload: Any) -> FreqtradeRuntimeEntryLeg | None:
    if not isinstance(payload, dict):
        return None

    entry_id = payload.get("entry_id")
    sequence = payload.get("sequence")
    order_type = payload.get("order_type")
    split = payload.get("split")
    if not isinstance(entry_id, str) or not entry_id.strip():
        return None
    if not isinstance(sequence, (int, float)):
        return None
    if not isinstance(order_type, str) or not order_type.strip():
        return None
    if not isinstance(split, (int, float)):
        return None

    price_raw = payload.get("price")
    try:
        price = float(price_raw) if price_raw is not None else None
    except (TypeError, ValueError):
        price = None

    role = payload.get("role")
    filled_at = payload.get("filled_at")
    status = str(payload.get("status") or "PENDING").upper()

    return FreqtradeRuntimeEntryLeg(
        entry_id=entry_id.strip(),
        sequence=int(sequence),
        order_type=order_type.strip().upper(),
        price=price,
        split=float(split),
        status=status,
        role=role if isinstance(role, str) else None,
        filled_at=filled_at if isinstance(filled_at, str) else None,
    )


def _row_to_context(row: sqlite3.Row, *, db_path: str) -> FreqtradeSignalContext:
    management_rules = _safe_json_loads(row["management_rules_json"])
    trade_meta = _safe_json_loads(row["trade_meta_json"])
    leverage = int(row["leverage"]) if row["leverage"] is not None else None
    stake_amount = float(row["position_size_usdt"]) if row["position_size_usdt"] is not None else None
    symbol = row["symbol"]
    signal_side = row["side"]
    attempt_key = str(row["attempt_key"])

    raw_entry_json = _safe_json_loads(row["entry_json"])
    entry_prices = tuple(
        item for item in (raw_entry_json if isinstance(raw_entry_json, list) else [])
        if isinstance(item, dict)
    )
    raw_entry_split = _safe_json_loads(row["entry_split_json"])
    entry_split = raw_entry_split if isinstance(raw_entry_split, dict) else None

    return FreqtradeSignalContext(
        env=str(row["env"]) if row["env"] is not None else "T",
        attempt_key=attempt_key,
        trader_id=str(row["trader_id"]) if row["trader_id"] is not None else "",
        symbol=str(symbol) if symbol is not None else None,
        pair=canonical_symbol_to_freqtrade_pair(symbol),
        signal_side=str(signal_side) if signal_side is not None else None,
        side=canonical_side_to_freqtrade_side(signal_side),
        entry_tag=str(row["attempt_key"]),
        stake_amount=stake_amount,
        leverage=leverage,
        signal_status=str(row["status"]) if row["status"] is not None else None,
        is_blocked=bool(row["is_blocked"]) if row["is_blocked"] is not None else True,
        block_reason=str(row["block_reason"]) if row["block_reason"] is not None else None,
        stoploss_ref=float(row["sl"]) if row["sl"] is not None else None,
        take_profit_refs=_parse_price_levels(row["tp_json"]),
        management_rules=management_rules if isinstance(management_rules, dict) else None,
        op_signal_id=int(row["op_signal_id"]) if row["op_signal_id"] is not None else None,
        trade_state=str(row["trade_state"]) if row["trade_state"] is not None else None,
        protective_orders_mode=(
            str(row["trade_protective_orders_mode"])
            if row["trade_protective_orders_mode"] is not None
            else None
        ),
        trade_meta=trade_meta if isinstance(trade_meta, dict) else None,
        update_directives=tuple(load_targeted_update_directives(attempt_key, db_path)),
        entry_prices=entry_prices,
        entry_split=entry_split,
        entry_legs=_build_entry_legs(entry_prices, entry_split),
    )


def _safe_json_loads(payload: str | None) -> Any:
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _parse_price_levels(payload: str | None) -> tuple[float, ...]:
    parsed = _safe_json_loads(payload)
    if not isinstance(parsed, list):
        return ()

    values: list[float] = []
    for item in parsed:
        if isinstance(item, (int, float)):
            values.append(float(item))
        elif isinstance(item, dict) and isinstance(item.get("price"), (int, float)):
            values.append(float(item["price"]))
    return tuple(values)


def _normalize_update_intent(intent: Any) -> str | None:
    if not isinstance(intent, str):
        return None
    normalized = intent.strip().upper()
    if normalized in {"U_CANCEL_PENDING", "U_CANCEL_PENDING_ORDERS"}:
        return "U_CANCEL_PENDING"
    if normalized in {"U_MOVE_STOP", "U_MOVE_STOP_TO_BE", "U_CLOSE_FULL", "U_CLOSE_PARTIAL"}:
        return normalized
    return None


def _normalize_close_fraction(entities: Any) -> float | None:
    if not isinstance(entities, dict):
        return None
    value = entities.get("close_fraction")
    if not isinstance(value, (int, float)):
        value = entities.get("close_pct")
    if not isinstance(value, (int, float)):
        return None
    normalized = float(value)
    if normalized > 1.0:
        normalized = normalized / 100.0
    if normalized <= 0:
        return None
    return min(normalized, 1.0)


def _normalize_cancel_scope(entities: Any) -> str | None:
    if not isinstance(entities, dict):
        return None
    value = entities.get("cancel_scope")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().upper()


def _normalize_new_stop_level(entities: Any) -> float | str | None:
    if not isinstance(entities, dict):
        return None
    value = entities.get("new_stop_level")
    if value is None:
        value = entities.get("new_sl_level")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        return value.strip().upper()
    return None

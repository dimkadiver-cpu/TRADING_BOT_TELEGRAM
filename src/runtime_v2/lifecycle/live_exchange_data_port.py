from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.runtime_v2.execution_gateway.adapters.base import ExecutionAdapter
from src.runtime_v2.execution_gateway.models import (
    ExecutionConfig,
    RawAccountSnapshot,
    RawMarketSnapshot,
)
from src.runtime_v2.lifecycle.ports import (
    AccountStateSnapshot,
    ExchangeDataPort,
    OrderSnapshot,
    PositionSnapshot,
    SymbolMarketSnapshot,
)
from src.runtime_v2.lifecycle.static_exchange_data_port import StaticExchangeDataPort


class LiveExchangeDataPort(ExchangeDataPort):
    """Lifecycle-facing exchange port backed by the configured execution adapters.

    When an adapter cannot provide a live snapshot, fall back to the existing static port
    so tests and degraded startup paths keep working.
    """

    def __init__(
        self,
        *,
        execution_config: ExecutionConfig,
        adapter_registry: dict[str, ExecutionAdapter],
        ops_db_path: str,
        known_symbols: frozenset[str] | None = None,
    ) -> None:
        self._execution_config = execution_config
        self._adapter_registry = adapter_registry
        self._ops_db_path = ops_db_path
        self._fallback = StaticExchangeDataPort(known_symbols=known_symbols)

    def _resolve_adapter(self, account_id: str) -> tuple[ExecutionAdapter, str] | None:
        try:
            routing, _ = self._execution_config.resolve_routing(account_id)
        except Exception:
            return None
        adapter = self._adapter_registry.get(routing.adapter)
        if adapter is None:
            return None
        return adapter, routing.execution_account_id

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _to_payload_json(payload: dict) -> str:
        return json.dumps(payload or {}, default=str, sort_keys=True)

    def _compute_total_open_risk_usdt(self, account_id: str) -> float | None:
        conn = sqlite3.connect(self._ops_db_path)
        try:
            rows = conn.execute(
                """
                SELECT be_protection_status, risk_remaining, risk_snapshot_json
                FROM ops_trade_chains
                WHERE account_id=?
                  AND lifecycle_state NOT IN ('CLOSED','CANCELLED','EXPIRED')
                """,
                (account_id,),
            ).fetchall()
        finally:
            conn.close()

        total = 0.0
        seen_any = False
        for be_status, risk_remaining, risk_snapshot_json in rows:
            if be_status == "PROTECTED":
                seen_any = True
                continue
            risk_value: float | None = None
            try:
                remaining = float(risk_remaining) if risk_remaining is not None else None
                if remaining is not None and remaining > 0.0:
                    risk_value = remaining
                else:
                    snap = json.loads(risk_snapshot_json or "{}")
                    raw_risk = snap.get("risk_amount")
                    if raw_risk is not None:
                        risk_value = float(raw_risk)
            except (TypeError, ValueError, json.JSONDecodeError):
                risk_value = None
            if risk_value is not None:
                total += max(0.0, risk_value)
                seen_any = True
        return total if seen_any else None

    def get_account_state(self, account_id: str) -> AccountStateSnapshot:
        computed_open_risk = self._compute_total_open_risk_usdt(account_id)
        resolved = self._resolve_adapter(account_id)
        if resolved is None:
            return self._fallback.get_account_state(account_id).model_copy(
                update={"total_open_risk_usdt": computed_open_risk}
            )
        adapter, execution_account_id = resolved
        raw = adapter.fetch_account_snapshot(execution_account_id)
        if raw is None:
            return self._fallback.get_account_state(account_id).model_copy(
                update={"total_open_risk_usdt": computed_open_risk}
            )
        assert isinstance(raw, RawAccountSnapshot)
        return AccountStateSnapshot(
            account_id=account_id,
            equity_usdt=raw.equity_usdt,
            available_balance_usdt=raw.available_balance_usdt,
            total_open_risk_usdt=computed_open_risk,
            total_margin_used_usdt=raw.total_margin_used_usdt,
            captured_at=self._now(),
            source=raw.source,
            payload_json=self._to_payload_json(raw.payload),
        )

    def get_symbol_market_state(self, account_id: str, symbol: str) -> SymbolMarketSnapshot:
        resolved = self._resolve_adapter(account_id)
        if resolved is None:
            return self._fallback.get_symbol_market_state(account_id, symbol)
        adapter, execution_account_id = resolved
        raw = adapter.fetch_market_snapshot(symbol, execution_account_id)
        if raw is None:
            return self._fallback.get_symbol_market_state(account_id, symbol)
        assert isinstance(raw, RawMarketSnapshot)
        return SymbolMarketSnapshot(
            symbol=raw.symbol,
            mark_price=raw.mark_price,
            bid=raw.bid,
            ask=raw.ask,
            min_order_size=raw.min_order_size,
            price_precision=raw.price_precision,
            qty_precision=raw.qty_precision,
            captured_at=self._now(),
            source=raw.source,
            payload_json=self._to_payload_json(raw.payload),
        )

    def get_open_orders(self, account_id: str, symbol: str | None = None) -> list[OrderSnapshot]:
        return self._fallback.get_open_orders(account_id, symbol)

    def get_open_position(self, account_id: str, symbol: str, side: str) -> PositionSnapshot | None:
        return self._fallback.get_open_position(account_id, symbol, side)

    def symbol_exists(self, account_id: str, symbol: str) -> bool:
        return self._fallback.symbol_exists(account_id, symbol)

    def resolve_symbol(self, account_id: str, symbol: str) -> str:
        return self._fallback.resolve_symbol(account_id, symbol)


__all__ = ["LiveExchangeDataPort"]

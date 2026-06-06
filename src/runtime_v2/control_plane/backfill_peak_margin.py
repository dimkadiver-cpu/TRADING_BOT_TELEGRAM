from __future__ import annotations

import json
import sqlite3


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def backfill_minimum_roi_fields(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    updated = 0
    try:
        with conn:
            rows = conn.execute(
                "SELECT trade_chain_id, risk_snapshot_json, initial_risk_amount, peak_margin_used, "
                "entry_avg_price, filled_entry_qty, open_position_qty "
                "FROM ops_trade_chains"
            ).fetchall()
            for chain_id, risk_json, initial_risk, peak_margin, entry_avg, filled_qty, open_qty in rows:
                try:
                    risk = json.loads(risk_json or "{}")
                except Exception:
                    risk = {}
                new_initial = _safe_float(initial_risk)
                if new_initial is None:
                    new_initial = _safe_float(risk.get("risk_amount"))

                new_peak = _safe_float(peak_margin)
                leverage = _safe_float(risk.get("leverage"))
                qty = _safe_float(filled_qty) or _safe_float(open_qty)
                price = _safe_float(entry_avg)
                if new_peak is None and leverage and leverage > 0 and qty and price:
                    new_peak = round(qty * price / leverage, 8)

                if new_initial != initial_risk or new_peak != peak_margin:
                    conn.execute(
                        "UPDATE ops_trade_chains SET initial_risk_amount=?, peak_margin_used=? WHERE trade_chain_id=?",
                        (new_initial, new_peak, chain_id),
                    )
                    updated += 1
    finally:
        conn.close()
    return updated

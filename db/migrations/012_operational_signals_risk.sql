-- Migration 012: Add risk-based sizing columns to operational_signals
-- Aligns the table with the risk-first model from FASE_4_RISK_RULES_SIMPLE.md
-- All columns are nullable for backward compatibility with existing rows.

ALTER TABLE operational_signals ADD COLUMN risk_mode TEXT;
        -- "risk_pct_of_capital" | "risk_usdt_fixed"
ALTER TABLE operational_signals ADD COLUMN risk_pct_of_capital REAL;
        -- % capitale configurato per trade (es. 1.0 = 1%)
ALTER TABLE operational_signals ADD COLUMN risk_usdt_fixed REAL;
        -- USDT fissi se risk_mode = risk_usdt_fixed
ALTER TABLE operational_signals ADD COLUMN capital_base_usdt REAL;
        -- capitale di riferimento usato per il calcolo
ALTER TABLE operational_signals ADD COLUMN risk_budget_usdt REAL;
        -- perdita massima calcolata per questo segnale (USDT)
ALTER TABLE operational_signals ADD COLUMN sl_distance_pct REAL;
        -- distanza percentuale entry → stop loss (0.05 = 5%)

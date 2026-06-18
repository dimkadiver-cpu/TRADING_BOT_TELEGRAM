"""Real-case tests for the trader_devos_crypto parser profile."""
from __future__ import annotations

import pytest

from src.parser_v2.contracts.context import ParserContext, RawContext
from src.parser_v2.core.runtime import UniversalParserRuntime
from src.parser_v2.profiles.trader_devos_crypto.profile import TraderDevosCryptoProfile

_runtime = UniversalParserRuntime()
_profile = TraderDevosCryptoProfile()


def _parse(text: str, reply_to: int | None = None):
    ctx = ParserContext(
        raw_context=RawContext(raw_text=text, reply_to_message_id=reply_to),
        reply_to_message_id=reply_to,
    )
    return _runtime.parse(text, ctx, _profile)


# ── new signal Format A ───────────────────────────────────────────────────────

_SIGNAL_A = """Devos Crypto Signals

ENAUSDT

Direction: LONG
Leverage: Cross 20x

Entry Targets:
1) 0.09182
2) 0.09428
3) 0.090143
4) 0.088465
⚡⚡Stop Loss: 0.086737⚡⚡

Take Profits:
Target 1 - 0.094751
Target 2 - 0.095222
Target 3 - 0.096165
Target 4 - 0.097108
Target 5 - 0.098051
Target 6 - 0.098994
Target 7 - 0.099936
Target 8 - 0.100879

──────────────
◎ Informational relay · not financial advice"""


def test_new_signal_format_a_class():
    result = _parse(_SIGNAL_A)
    assert result.primary_class == "SIGNAL"
    assert result.parse_status == "PARSED"


def test_new_signal_format_a_symbol():
    result = _parse(_SIGNAL_A)
    assert result.signal is not None
    assert result.signal.symbol == "ENAUSDT"


def test_new_signal_format_a_side_long():
    result = _parse(_SIGNAL_A)
    assert result.signal.side == "LONG"


def test_new_signal_format_a_entries():
    result = _parse(_SIGNAL_A)
    s = result.signal
    assert len(s.entries) == 4
    assert s.entries[0].price.value == pytest.approx(0.09182)
    assert s.entry_structure == "LADDER"


def test_new_signal_format_a_stop_loss():
    result = _parse(_SIGNAL_A)
    assert result.signal.stop_loss is not None
    assert result.signal.stop_loss.price.value == pytest.approx(0.086737)


def test_new_signal_format_a_take_profits():
    result = _parse(_SIGNAL_A)
    tps = result.signal.take_profits
    assert len(tps) == 8
    assert tps[0].price.value == pytest.approx(0.094751)
    assert tps[7].price.value == pytest.approx(0.100879)


def test_new_signal_format_a_leverage():
    result = _parse(_SIGNAL_A)
    assert result.signal.leverage_hint == pytest.approx(20.0)


# ── new signal Format B ───────────────────────────────────────────────────────

_SIGNAL_B = """┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ ◇ Flow Desk · signal relay ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

BUSDT

Direction: LONG
Leverage: Cross 20x
★ Entry: 0.3558 — 0.3586 ★

🔥Stop Loss: 0.329912🔥

Take Profits:
Target 1 - 0.360392
Target 2 - 0.362185
Target 3 - 0.365772
Target 4 - 0.369357
Target 5 - 0.372944
Target 6 - 0.37653
Target 7 - 0.380115
Target 8 - 0.383702

6a0080942ea8036691ecadd6

──────────────
◎ Informational relay · not financial advice"""


def test_new_signal_format_b_class():
    result = _parse(_SIGNAL_B)
    assert result.primary_class == "SIGNAL"


def test_new_signal_format_b_range_entries():
    result = _parse(_SIGNAL_B)
    s = result.signal
    assert len(s.entries) == 2
    assert s.entry_structure == "RANGE"
    assert s.entries[0].price.value == pytest.approx(0.3558)
    assert s.entries[1].price.value == pytest.approx(0.3586)


def test_new_signal_format_b_stop_loss():
    result = _parse(_SIGNAL_B)
    assert result.signal.stop_loss.price.value == pytest.approx(0.329912)


# ── TP_HIT ────────────────────────────────────────────────────────────────────

def test_tp_hit_class_and_intent():
    result = _parse("#ETH/USDT Take-Profit target 1 ✅\nProfit: 12.1114% 📈\nPeriod: 52 min ⏰", reply_to=100)
    assert result.primary_class == "REPORT"
    assert result.primary_intent == "TP_HIT"


def test_tp_hit_level():
    result = _parse("#XRP/USDT Take-Profit target 3 ✅\nProfit: 26.0614% 📈\nPeriod: 9 hr 43 min ⏰", reply_to=100)
    assert result.report is not None
    events = result.report.events
    assert len(events) == 1
    assert events[0].level == 3


def test_all_targets_achieved():
    result = _parse("#TRX/USDT All targets achieved 😎\nProfit: 255.7486% 📈\nPeriod: 6 months 10 days ⏰", reply_to=100)
    assert result.primary_class == "REPORT"
    assert result.primary_intent == "TP_HIT"


# ── SL_HIT ────────────────────────────────────────────────────────────────────

def test_sl_hit_class_and_intent():
    result = _parse("#UNI/USDT Stop Target Hit ⛔\nLoss: 105.1341% 📉", reply_to=100)
    assert result.primary_class == "REPORT"
    assert result.primary_intent == "SL_HIT"


# ── EXIT_BE ───────────────────────────────────────────────────────────────────

def test_exit_be_trailing_stoploss():
    result = _parse(
        "#HYPE/USDT Closed at trailing stoploss after reaching take profit ⚠\nProfit: 8.6035% 📈",
        reply_to=100,
    )
    assert result.primary_class == "REPORT"
    assert result.primary_intent == "EXIT_BE"


def test_exit_be_stoploss_after_tp():
    result = _parse(
        "#ASTER/USDT Closed at stoploss after reaching take profit ⚠\nLoss: 138.9545% 📉",
        reply_to=100,
    )
    assert result.primary_class == "REPORT"
    assert result.primary_intent == "EXIT_BE"


# ── CLOSE_FULL ────────────────────────────────────────────────────────────────

def test_close_full_opposite_direction():
    result = _parse(
        "HyperLiquid Futures, KuCoin Futures, OKX Futures\n"
        "#XRP/USDT Closed due to opposite direction signal ⚠\n"
        "Loss: 24.5688% 📉\nPeriod: 3 hr 37 min ⏰",
        reply_to=100,
    )
    assert result.primary_class == "UPDATE"
    assert result.primary_intent == "CLOSE_FULL"


# ── CANCEL_PENDING ────────────────────────────────────────────────────────────

def test_cancel_pending():
    result = _parse(
        "#H/USDT Cancelled ❌\nTarget achieved before entering the entry zone",
        reply_to=100,
    )
    assert result.primary_class == "UPDATE"
    assert result.primary_intent == "CANCEL_PENDING"


# ── ENTRY_FILLED ──────────────────────────────────────────────────────────────

def test_entry_filled_single():
    result = _parse(
        "#ENA/USDT Entry 1 ✅\nAverage Entry Price: 0.09428 💵",
        reply_to=100,
    )
    assert result.primary_class == "REPORT"
    assert result.primary_intent == "ENTRY_FILLED"


def test_entry_filled_level():
    result = _parse(
        "#ENA/USDT Entry 3 ✅\nAverage Entry Price: 0.09274 💵",
        reply_to=100,
    )
    events = result.report.events
    assert len(events) == 1
    assert events[0].level == 3


def test_all_entries_achieved():
    result = _parse(
        "#UNI/USDT All entries achieved\nAverage Entry Price: 3.246 💵",
        reply_to=100,
    )
    assert result.primary_class == "REPORT"
    assert result.primary_intent == "ENTRY_FILLED"


# ── SHORT signal ──────────────────────────────────────────────────────────────

def test_short_signal():
    text = """Devos Crypto Signals

ENAUSDT

Direction: SHORT
Leverage: Cross 20x

Entry Targets:
1) 0.09142
2) 0.09235
3) 0.094456
4) 0.096563
⚡⚡Stop Loss: 0.098733⚡⚡

Take Profits:
Target 1 - 0.090962
Target 2 - 0.090505
Target 3 - 0.089591
Target 4 - 0.088677
Target 5 - 0.087763
Target 6 - 0.086849
Target 7 - 0.085934
Target 8 - 0.08502

──────────────
◎ Informational relay · not financial advice"""
    result = _parse(text)
    assert result.primary_class == "SIGNAL"
    assert result.signal.side == "SHORT"
    assert result.signal.stop_loss.price.value > result.signal.entries[0].price.value

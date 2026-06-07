from __future__ import annotations

from src.runtime_v2.control_plane.formatters._formatters import (
    num, text, money, money_signed, pct, pct_signed, fee_rate,
)


def test_num_none():        assert num(None) == "n/a"
def test_num_int():         assert num(65000) == "65,000"
def test_num_float():       assert num(65020.5) == "65,020.5"
def test_num_small():       assert num(0.004) == "0.004"
def test_num_zero():        assert num(0) == "0"
def test_num_str_bad():     assert num("abc") == "abc"

def test_text_none():       assert text(None) == "n/a"
def test_text_str():        assert text("hello") == "hello"

def test_money_none():      assert money(None) == "n/a"
def test_money_pos():       assert money(12.34) == "12.34 USDT"
def test_money_neg():       assert money(-5.00) == "-5.00 USDT"

def test_money_signed_none():    assert money_signed(None) == "n/a"
def test_money_signed_pos():     assert money_signed(12.34) == "+12.34 USDT"
def test_money_signed_neg():     assert money_signed(-5.00) == "-5.00 USDT"
def test_money_signed_zero():    assert money_signed(0.0) == "+0.00 USDT"

def test_pct_none():        assert pct(None) == "n/a"
def test_pct_whole():       assert pct(30.0) == "30%"
def test_pct_frac():        assert pct(12.34) == "12.34%"

def test_pct_signed_pos():  assert pct_signed(5.0) == "+5%"
def test_pct_signed_neg():  assert pct_signed(-5.17) == "-5.17%"

def test_fee_rate_none():   assert fee_rate(None) == "n/a"
def test_fee_rate():        assert fee_rate(0.001) == "0.100%"

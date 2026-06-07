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


# --- block dataclass tests ---
from src.runtime_v2.control_plane.formatters._blocks import (
    SeparatorBlock, StaticBlock, DerivedBlock, HeaderBlock, FieldBlock,
    SectionBlock, ConditionalBlock, BranchBlock, ListBlock, FooterBlock,
    TemplateConfig, _SEP, _BULLET,
)


def test_sep_constant():        assert _SEP == "__SEP__"
def test_bullet_constant():     assert _BULLET == "▪️"

def test_separator_block():     assert SeparatorBlock() is not None
def test_static_block():        assert StaticBlock("hi").text == "hi"
def test_header_block():        assert HeaderBlock("✅", "SIGNAL ACCEPTED").emoji == "✅"
def test_field_block_defaults():
    fb = FieldBlock("Label", key="k")
    assert fb.optional is True
    assert fb.default == "n/a"

def test_footer_block_defaults():
    fb = FooterBlock()
    assert fb.source_key == "source"
    assert fb.default_source == "runtime"
    assert fb.include_trader_id is False

def test_template_config():
    tc = TemplateConfig([StaticBlock("x")])
    assert tc.payload_transform is None


# --- renderer tests ---
from src.runtime_v2.control_plane.formatters._blocks import render_template


def test_render_static():
    result = render_template([StaticBlock("hello")], {})
    assert "hello" in result


def test_render_separator_dynamic_width():
    blocks = [StaticBlock("short"), SeparatorBlock(), StaticBlock("longer line here")]
    result = render_template(blocks, {})
    lines = result.split("\n")
    sep_line = lines[1]
    assert "-" in sep_line
    assert len(sep_line) >= 4


def test_render_field_optional_missing():
    blocks = [FieldBlock("Price", key="price")]
    result = render_template(blocks, {})
    assert "Price" not in result


def test_render_field_optional_present():
    blocks = [FieldBlock("Price", key="price")]
    result = render_template(blocks, {"price": 100})
    assert "Price: 100" in result


def test_render_field_not_optional():
    blocks = [FieldBlock("Price", key="price", optional=False)]
    result = render_template(blocks, {})
    assert "Price: n/a" in result


def test_render_conditional_true():
    blocks = [ConditionalBlock(condition=lambda p: p.get("show"), blocks=[StaticBlock("visible")])]
    assert "visible" in render_template(blocks, {"show": True})


def test_render_conditional_false():
    blocks = [ConditionalBlock(condition=lambda p: p.get("show"), blocks=[StaticBlock("visible")])]
    assert "visible" not in render_template(blocks, {"show": False})


def test_render_branch():
    blocks = [BranchBlock(
        condition=lambda p: p.get("flag"),
        then_blocks=[StaticBlock("yes")],
        else_blocks=[StaticBlock("no")],
    )]
    assert "yes" in render_template(blocks, {"flag": True})
    assert "no" in render_template(blocks, {"flag": False})


def test_render_list():
    blocks = [ListBlock(key="items", item_renderer=lambda x, i, p: [f"Item {i}: {x}"])]
    result = render_template(blocks, {"items": ["a", "b"]})
    assert "Item 1: a" in result
    assert "Item 2: b" in result


def test_render_header_with_chain_id():
    blocks = [HeaderBlock("✅", "TEST EVENT")]
    result = render_template(blocks, {"chain_id": 42, "symbol": "BTC/USDT", "side": "LONG"})
    assert "#42" in result
    assert "TEST EVENT" in result
    assert "BTC/USDT" in result
    assert "📈" in result


def test_render_header_no_symbol_side_omits_line():
    blocks = [HeaderBlock("✅", "TEST")]
    result = render_template(blocks, {"chain_id": 1})
    assert "None" not in result


def test_render_footer_source():
    blocks = [FooterBlock(default_source="exchange")]
    result = render_template(blocks, {})
    assert "Source: exchange" in result


def test_render_footer_link():
    blocks = [FooterBlock()]
    result = render_template(blocks, {"link": "https://t.me/c/1/2"})
    source_pos = result.find("Source:")
    link_pos = result.find("https://t.me/c/1/2")
    assert source_pos < link_pos


def test_render_footer_trader_id_hidden_by_default():
    blocks = [FooterBlock()]
    result = render_template(blocks, {"trader_id": "trader_a"})
    assert "Trader:" not in result


def test_render_footer_trader_id_shown_when_enabled():
    blocks = [FooterBlock(include_trader_id=True)]
    result = render_template(blocks, {"trader_id": "trader_a"})
    assert "Trader: trader_a" in result


def test_render_transform():
    blocks = [StaticBlock("x"), FieldBlock("V", key="_v")]
    result = render_template(blocks, {"val": 5}, transform=lambda p: {**p, "_v": p["val"] * 2})
    assert "V: 10" in result

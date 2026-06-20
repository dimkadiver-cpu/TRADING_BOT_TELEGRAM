from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.templates.emergency import EMERGENCY_REGISTRY
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _chains_3():
    return [
        {"chain_id": 5, "symbol": "BTC/USDT", "side": "LONG", "state": "OPEN", "entry_price": "63,500", "pnl": "+12.40 USDT"},
        {"chain_id": 7, "symbol": "ETH/USDT", "side": "SHORT", "state": "OPEN", "entry_price": None, "pnl": None},
        {"chain_id": 9, "symbol": "SOL/USDT", "side": "LONG", "state": "PARTIALLY_CLOSED", "entry_price": "148.50", "pnl": "+5.00 USDT"},
    ]


def test_close_all_preview_with_chains():
    cfg = EMERGENCY_REGISTRY["close_all_preview"]
    payload = {"scope_label": "demo_1", "total": 3, "chains": _chains_3()}
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "CLOSE ALL — demo_1" in result
    assert "Posizioni da chiudere: 3" in result
    assert "#5" in result
    assert "Confermi?" in result


def test_close_all_preview_empty():
    cfg = EMERGENCY_REGISTRY["close_all_preview"]
    payload = {"scope_label": "demo_1", "total": 0, "chains": []}
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "Nessuna posizione aperta" in result
    assert "Confermi?" not in result


def test_close_all_result_ok():
    cfg = EMERGENCY_REGISTRY["close_all_result_ok"]
    payload = {"scope_label": "demo_1", "chains": _chains_3(), "count": 3, "executed_at": _now()}
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "✅ ESEGUITO" in result
    assert "3 comandi CLOSE_FULL inseriti" in result


def test_cancel_all_preview_with_waiting():
    cfg = EMERGENCY_REGISTRY["cancel_all_preview"]
    waiting = [{"chain_id": 2, "symbol": "NEAR/USDT", "side": "LONG", "state": "WAITING_ENTRY"}]
    payload = {"scope_label": "demo_1", "total": 1, "chains": waiting, "open_count": 2}
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "Ordini entry in attesa: 1" in result
    assert "Posizioni aperte non toccate: 2" in result
    assert "Confermi" in result


def test_close_single_preview_not_found():
    cfg = EMERGENCY_REGISTRY["close_single_preview"]
    payload = {"scope_label": "demo_1", "total": 0, "chains": [], "symbol": "XYZUSDT"}
    result = render_template(cfg.blocks, payload, transform=cfg.payload_transform)
    assert "XYZUSDT: nessuna posizione aperta" in result

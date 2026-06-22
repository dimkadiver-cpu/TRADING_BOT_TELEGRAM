from __future__ import annotations

from src.runtime_v2.control_plane.formatters._blocks import render_template
from src.runtime_v2.control_plane.formatters.templates.tech_log import TEMPLATE_REGISTRY


def _render(notification_type: str, payload: dict) -> str:
    config = TEMPLATE_REGISTRY[notification_type]
    return render_template(config.blocks, payload, transform=config.payload_transform)


def test_runtime_startup_header_and_fields():
    text = _render("RUNTIME_STARTUP", {
        "started_at": "2026-06-18 10:00:00 UTC",
        "source": "runtime_main",
    })
    assert "ℹ️ RUNTIME: AVVIATO" in text
    assert "Started at: 2026-06-18 10:00:00 UTC" in text
    assert "Source: runtime_main" in text


def test_runtime_shutdown_all_fields():
    text = _render("RUNTIME_SHUTDOWN", {
        "reason": "SIGTERM",
        "open_chains": 3,
        "pending_commands": 1,
        "source": "runtime_main",
    })
    assert "ℹ️ RUNTIME: SHUTDOWN" in text
    assert "Reason: SIGTERM" in text
    assert "Open chains: 3" in text
    assert "Pending commands: 1" in text
    assert "Source: runtime_main" in text


def test_listener_edit_skipped_fields():
    text = _render("LISTENER_EDIT_SKIPPED", {
        "description": "Edit di un segnale con trade chain già creata — non riprocessato.",
        "chat": -100123,
        "msg_id": 789,
        "action": "verifica il messaggio",
        "source": "telegram_listener",
    })
    assert "⚠️ LISTENER: EDIT SKIPPED" in text
    assert "Chat: -100123" in text
    assert "Msg ID: 789" in text
    assert "Action: verifica il messaggio" in text
    assert "Source: telegram_listener" in text


def test_listener_edit_skipped_optional_edit_ts_absent():
    text = _render("LISTENER_EDIT_SKIPPED", {
        "chat": -100123,
        "msg_id": 789,
        "source": "telegram_listener",
    })
    assert "Edit ts" not in text
    assert "Action" not in text


def test_gateway_entry_all_failed_fields():
    text = _render("GATEWAY_ENTRY_ALL_FAILED", {
        "description": "Tutti i comandi PLACE_ENTRY falliti. Catena cancellata.",
        "chain_id": 42,
        "symbol": "BTC/USDT",
        "side": "LONG",
        "reason": "order rejected by exchange",
        "action": "intervento manuale richiesto",
        "source": "execution_gateway",
    })
    assert "🛑 GATEWAY: ENTRY ALL FAILED" in text
    assert "#42" in text
    assert "BTC/USDT" in text
    assert "LONG" in text
    assert "order rejected by exchange" in text
    assert "intervento manuale richiesto" in text


def test_gateway_review_required_fields():
    text = _render("GATEWAY_REVIEW_REQUIRED", {
        "description": "Comando bloccato in REVIEW_REQUIRED.",
        "command_type": "PLACE_ENTRY",
        "chain_id": 42,
        "trader_id": "trader_b",
        "execution_account_id": "demo_1",
        "reason": "capability_missing:can_place_limit_entry",
        "action": "intervento manuale richiesto",
        "source": "execution_gateway",
    })
    assert "⚠️ GATEWAY: REVIEW REQUIRED" in text
    assert "Command: PLACE_ENTRY" in text
    assert "#42" in text
    assert "Trader: trader_b" in text
    assert "Exchange Account: demo_1" in text
    assert "capability_missing" in text


def test_gateway_command_failed_fields():
    text = _render("GATEWAY_COMMAND_FAILED", {
        "command_type": "SET_SL",
        "chain_id": 42,
        "trader_id": "trader_b",
        "execution_account_id": "demo_1",
        "reason": "KeyError: 'order_id'",
        "source": "execution_gateway",
    })
    assert "🛑 GATEWAY: COMMAND FAILED" in text
    assert "Command: SET_SL" in text
    assert "#42" in text
    assert "Trader: trader_b" in text
    assert "Exchange Account: demo_1" in text
    assert "KeyError" in text
    assert "Source: execution_gateway" in text


def test_gateway_command_failed_no_chain_id():
    text = _render("GATEWAY_COMMAND_FAILED", {
        "command_type": "SET_SL",
        "chain_id": None,
        "reason": "some error",
        "source": "execution_gateway",
    })
    assert "Chain" not in text
    assert "Reason: some error" in text


def test_all_six_types_are_registered():
    expected = {
        "RUNTIME_STARTUP",
        "RUNTIME_SHUTDOWN",
        "LISTENER_EDIT_SKIPPED",
        "GATEWAY_ENTRY_ALL_FAILED",
        "GATEWAY_REVIEW_REQUIRED",
        "GATEWAY_COMMAND_FAILED",
    }
    assert expected == set(TEMPLATE_REGISTRY.keys())


from src.runtime_v2.control_plane.formatters.tech_log import format_tech_log


def test_format_tech_log_dispatches_to_template():
    text = format_tech_log("RUNTIME_STARTUP", {
        "started_at": "2026-06-18 10:00:00 UTC",
        "source": "runtime_main",
    })
    assert "ℹ️ RUNTIME: AVVIATO" in text


def test_format_tech_log_private_bot_prepends_system():
    text = format_tech_log(
        "RUNTIME_STARTUP",
        {"started_at": "2026-06-18 10:00:00 UTC"},
        delivery_mode="private_bot",
    )
    assert text.startswith("⚠️ --SYSTEM--\n")
    assert "ℹ️ RUNTIME: AVVIATO" in text


def test_format_tech_log_unknown_type_fallback():
    text = format_tech_log("UNKNOWN_EVENT", {
        "level": "ERROR",
        "description": "qualcosa è andato storto",
    })
    assert "UNKNOWN_EVENT" in text
    assert "qualcosa è andato storto" in text


def test_format_tech_log_fallback_default_delivery_mode():
    text = format_tech_log("UNKNOWN_EVENT", {"level": "INFO", "description": "x"})
    assert not text.startswith("⚠️ --SYSTEM--")

from __future__ import annotations

from unittest.mock import MagicMock

from main import _configure_shadow_mode, _is_enabled_env
from src.storage.parse_results_v1 import ParseResultV1Store


def test_is_enabled_env_accepts_truthy_values() -> None:
    assert _is_enabled_env("1") is True
    assert _is_enabled_env("true") is True
    assert _is_enabled_env(" YES ") is True
    assert _is_enabled_env("on") is True


def test_is_enabled_env_rejects_missing_or_falsey_values() -> None:
    assert _is_enabled_env(None) is False
    assert _is_enabled_env("") is False
    assert _is_enabled_env("0") is False
    assert _is_enabled_env("false") is False
    assert _is_enabled_env("off") is False


def test_configure_shadow_mode_enables_router_when_flag_is_set(monkeypatch) -> None:
    router = MagicMock()
    logger = MagicMock()
    monkeypatch.setenv("PARSER_V1_SHADOW_MODE", "true")

    enabled = _configure_shadow_mode(
        router=router,
        db_path="C:\\TeleSignalBot\\db\\tele_signal_bot.sqlite3",
        logger=logger,
    )

    assert enabled is True
    router.enable_shadow_normalizer.assert_called_once()
    store = router.enable_shadow_normalizer.call_args.args[0]
    assert isinstance(store, ParseResultV1Store)
    logger.info.assert_called_once()


def test_configure_shadow_mode_leaves_router_untouched_when_flag_is_off(monkeypatch) -> None:
    router = MagicMock()
    logger = MagicMock()
    monkeypatch.delenv("PARSER_V1_SHADOW_MODE", raising=False)

    enabled = _configure_shadow_mode(
        router=router,
        db_path="C:\\TeleSignalBot\\db\\tele_signal_bot.sqlite3",
        logger=logger,
    )

    assert enabled is False
    router.enable_shadow_normalizer.assert_not_called()
    logger.info.assert_not_called()

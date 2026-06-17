from __future__ import annotations

import logging
from pathlib import Path

from src.core.logger import setup_logging


def _cleanup_logging() -> None:
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, "_tsb_managed", False):
            root_logger.removeHandler(handler)
            handler.close()

    app_logger = logging.getLogger("TeleSignalBot")
    app_logger.handlers.clear()
    app_logger.setLevel(logging.NOTSET)


def test_setup_logging_captures_module_loggers(tmp_path: Path) -> None:
    _cleanup_logging()
    log_path = tmp_path / "logs" / "bot.log"

    setup_logging(str(log_path), "INFO")
    logging.getLogger("src.runtime_v2.control_plane.topic_router").warning("module warning")

    content = log_path.read_text(encoding="utf-8")
    assert "module warning" in content
    assert "src.runtime_v2.control_plane.topic_router" in content

    _cleanup_logging()


def test_setup_logging_reconfigures_managed_file_handler(tmp_path: Path) -> None:
    _cleanup_logging()
    log_path_1 = tmp_path / "a" / "bot.log"
    log_path_2 = tmp_path / "b" / "bot.log"

    logger = setup_logging(str(log_path_1), "INFO")
    logger.info("first")

    logger = setup_logging(str(log_path_2), "INFO")
    logger.info("second")

    content_1 = log_path_1.read_text(encoding="utf-8")
    content_2 = log_path_2.read_text(encoding="utf-8")
    assert "first" in content_1
    assert "second" not in content_1
    assert "second" in content_2

    _cleanup_logging()

"""Logging (LOG1).

Rotating file handler -> logs/bot.log
"""

import logging
from logging.handlers import RotatingFileHandler
import os


_MANAGED_HANDLER_FLAG = "_tsb_managed"


def setup_logging(log_path: str, level: str = "INFO") -> logging.Logger:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    for existing in list(root_logger.handlers):
        if getattr(existing, _MANAGED_HANDLER_FLAG, False):
            root_logger.removeHandler(existing)
            existing.close()

    handler = RotatingFileHandler(log_path, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    handler.setFormatter(formatter)
    setattr(handler, _MANAGED_HANDLER_FLAG, True)
    root_logger.addHandler(handler)

    logger = logging.getLogger("TeleSignalBot")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = True
    return logger

"""Logging (LOG1).

Rotating file handler -> logs/bot.log
"""

import logging
from logging.handlers import RotatingFileHandler
import os

def setup_logging(log_path: str, level: str = "INFO") -> logging.Logger:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logger = logging.getLogger("TeleSignalBot")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = RotatingFileHandler(log_path, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    handler.setFormatter(formatter)
    if not logger.handlers:
        logger.addHandler(handler)
    return logger

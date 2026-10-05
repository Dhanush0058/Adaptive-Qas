"""Logging utility for Adaptive QoS engine."""

import logging
import sys

def get_logger(name: str = "adaptive_qos", level: int = logging.INFO) -> logging.Logger:
    """Return a configured logger with standard formatting."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(level)
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.propagate = False
    return logger

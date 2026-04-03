"""Structured logger for the Ultrastar Song Generator backend."""

import logging
import sys
from datetime import datetime


def setup_logger(name: str = "ultrastar", level: int = logging.DEBUG) -> logging.Logger:
    """Create a structured logger with consistent formatting."""
    logger = logging.getLogger(name)
    
    if logger.handlers:
        return logger
    
    logger.setLevel(level)
    
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s | %(message)s",
        datefmt="%H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger


# Global logger instance
log = setup_logger()


def log_step(step: str, message: str):
    """Log a processing pipeline step."""
    log.info(f"[{step}] {message}")


def log_progress(step: str, current: int, total: int, detail: str = ""):
    """Log progress within a step."""
    pct = int((current / total) * 100) if total > 0 else 0
    msg = f"[{step}] {pct}% ({current}/{total})"
    if detail:
        msg += f" - {detail}"
    log.info(msg)

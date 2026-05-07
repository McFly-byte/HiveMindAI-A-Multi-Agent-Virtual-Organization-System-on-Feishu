from __future__ import annotations

import logging
import os


DEBUG_ENV_NAMES = ("AGENT_HIVE_DEBUG", "HIVEMIND_DEBUG")


def debug_enabled() -> bool:
    return any(os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "y", "on"} for name in DEBUG_ENV_NAMES)


def configure_logging(*, debug: bool | None = None) -> None:
    enabled = debug_enabled() if debug is None else debug
    level = logging.DEBUG if enabled else logging.INFO
    root = logging.getLogger("agent_hive")
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(handler)
    for handler in root.handlers:
        handler.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"agent_hive.{name}")

"""
Central logging setup for the whole app.

Call configure_logging() once, as early as possible (app.py does this before
importing anything else from ai_agent). Every module then just does:

    import logging
    logger = logging.getLogger(__name__)

and its output goes to both the console and logs/app.log, at the level set
by the LOG_LEVEL env var (default INFO).
"""

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")

_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    os.makedirs(LOG_DIR, exist_ok=True)

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [file_handler, console_handler]

    # Quiet down noisy third-party loggers unless the user asks for DEBUG.
    if level > logging.DEBUG:
        logging.getLogger("apscheduler").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

    logging.getLogger(__name__).info("Logging configured (level=%s, file=%s)", level_name, LOG_FILE)

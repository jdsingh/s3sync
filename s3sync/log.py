import logging
import logging.handlers
from pathlib import Path


LOG_DIR = Path.home() / "Library" / "Logs" / "s3sync"
LOG_FILE = LOG_DIR / "s3sync.log"


def setup_logging(verbose: bool = False) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"

    root = logging.getLogger()
    root.setLevel(level)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3
    )
    file_handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(console_handler)


def get_log_path() -> Path:
    return LOG_FILE

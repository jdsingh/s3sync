import fcntl
import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PLIST_LABEL = "com.s3sync.agent"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
LOCK_PATH = Path.home() / ".config" / "s3sync" / "daemon.lock"
LOG_DIR = Path.home() / "Library" / "Logs" / "s3sync"


def _plist_content(python_path: str, config_dir: Path) -> str:
    log_dir = str(LOG_DIR)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>-m</string>
        <string>s3sync.cli</string>
        <string>daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_dir}/s3sync.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/s3sync.log</string>
</dict>
</plist>
"""


def install() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = _plist_content(sys.executable, LOCK_PATH.parent)
    PLIST_PATH.write_text(content)
    logger.info("Wrote plist to %s", PLIST_PATH)
    _launchctl("load", PLIST_PATH)


def uninstall() -> None:
    if PLIST_PATH.exists():
        try:
            _launchctl("unload", PLIST_PATH)
        except Exception:
            pass
        PLIST_PATH.unlink()
        logger.info("Removed plist %s", PLIST_PATH)


def start() -> None:
    _launchctl("load", PLIST_PATH)


def stop() -> None:
    _launchctl("unload", PLIST_PATH)


def is_running() -> bool:
    result = subprocess.run(
        ["launchctl", "list", PLIST_LABEL],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _launchctl(action: str, plist: Path) -> None:
    result = subprocess.run(
        ["launchctl", action, str(plist)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"launchctl {action} failed: {result.stderr.strip()}")


class DaemonLock:
    """Context manager that acquires an exclusive lockfile using flock."""

    def __init__(self, lock_path: Path = LOCK_PATH) -> None:
        self._path = lock_path
        self._fd = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(self._path, "w")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self._fd.close()
            self._fd = None
            return False

    def release(self) -> None:
        if self._fd:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None

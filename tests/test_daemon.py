import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from s3sync.config import WatchEntry
from s3sync.daemon import FileEventHandler
from s3sync.initial_sync import should_sync


@pytest.fixture
def entry(tmp_path: Path) -> WatchEntry:
    (tmp_path / "watch").mkdir()
    return WatchEntry(path=tmp_path / "watch", bucket="b", prefix="p/")


def test_should_sync_no_filters(entry: WatchEntry) -> None:
    assert should_sync(Path("/watch/file.txt"), entry) is True


def test_should_sync_excluded(tmp_path: Path) -> None:
    entry = WatchEntry(
        path=tmp_path / "watch", bucket="b", prefix="p/", exclude=["*.tmp"]
    )
    assert should_sync(Path("/watch/file.tmp"), entry) is False
    assert should_sync(Path("/watch/file.txt"), entry) is True


def test_should_sync_include_filter(tmp_path: Path) -> None:
    entry = WatchEntry(
        path=tmp_path / "watch", bucket="b", prefix="p/", include=["*.pdf"]
    )
    assert should_sync(Path("/watch/doc.pdf"), entry) is True
    assert should_sync(Path("/watch/doc.txt"), entry) is False


def test_debounce_coalesces_rapid_events(tmp_path: Path, entry: WatchEntry) -> None:
    syncer = MagicMock()
    db = MagicMock()
    handler = FileEventHandler(entry=entry, syncer=syncer, db=db, tmp_dir=tmp_path / "tmp", debounce_seconds=0.1)

    f = tmp_path / "watch" / "rapid.txt"
    f.write_text("v1")

    # Fire 5 events rapidly
    for _ in range(5):
        handler._schedule_upload(f)

    time.sleep(0.3)  # wait for debounce to fire
    handler._executor.shutdown(wait=True)

    # Should have uploaded only once despite 5 events
    assert syncer.upload.call_count <= 1

import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from s3sync.config import WatchEntry
from s3sync.state import StateDB, SyncRecord
from s3sync.initial_sync import run_initial_sync


@pytest.fixture
def entry(tmp_path: Path) -> WatchEntry:
    (tmp_path / "watch").mkdir()
    return WatchEntry(path=tmp_path / "watch", bucket="b", prefix="p/")


@pytest.fixture
def db(tmp_path: Path) -> StateDB:
    return StateDB(tmp_path / "state.db")


def test_new_file_is_uploaded(tmp_path: Path, entry: WatchEntry, db: StateDB) -> None:
    f = entry.path / "new.txt"
    f.write_text("hello")

    syncer = MagicMock()
    run_initial_sync(entry, db, syncer, tmp_dir=tmp_path / "tmp")

    syncer.upload.assert_called_once_with(f, entry)


def test_unchanged_file_is_skipped(tmp_path: Path, entry: WatchEntry, db: StateDB) -> None:
    f = entry.path / "existing.txt"
    f.write_text("data")
    stat = f.stat()

    db.upsert(SyncRecord(
        path=f,
        watch_root=entry.path,
        mtime=stat.st_mtime,
        size=stat.st_size,
        s3_key="p/existing.txt",
        synced_at=time.time(),
    ))

    syncer = MagicMock()
    run_initial_sync(entry, db, syncer, tmp_dir=tmp_path / "tmp")

    syncer.upload.assert_not_called()


def test_changed_mtime_triggers_upload(tmp_path: Path, entry: WatchEntry, db: StateDB) -> None:
    f = entry.path / "modified.txt"
    f.write_text("new content")
    stat = f.stat()

    db.upsert(SyncRecord(
        path=f,
        watch_root=entry.path,
        mtime=stat.st_mtime - 100,   # stale mtime
        size=stat.st_size,
        s3_key="p/modified.txt",
        synced_at=time.time(),
    ))

    syncer = MagicMock()
    run_initial_sync(entry, db, syncer, tmp_dir=tmp_path / "tmp")

    syncer.upload.assert_called_once()


def test_exclude_pattern_skips_file(tmp_path: Path, db: StateDB) -> None:
    entry = WatchEntry(
        path=tmp_path / "watch",
        bucket="b",
        prefix="p/",
        exclude=["*.tmp"],
    )
    entry.path.mkdir()
    (entry.path / "file.tmp").write_text("skip me")
    (entry.path / "file.txt").write_text("sync me")

    syncer = MagicMock()
    run_initial_sync(entry, db, syncer, tmp_dir=tmp_path / "tmp")

    assert syncer.upload.call_count == 1
    syncer.upload.assert_called_once_with(entry.path / "file.txt", entry)


def test_include_pattern_filters_files(tmp_path: Path, db: StateDB) -> None:
    entry = WatchEntry(
        path=tmp_path / "watch",
        bucket="b",
        prefix="p/",
        include=["*.pdf"],
    )
    entry.path.mkdir()
    (entry.path / "doc.pdf").write_bytes(b"pdf")
    (entry.path / "doc.txt").write_text("text")

    syncer = MagicMock()
    run_initial_sync(entry, db, syncer, tmp_dir=tmp_path / "tmp")

    assert syncer.upload.call_count == 1
    syncer.upload.assert_called_once_with(entry.path / "doc.pdf", entry)

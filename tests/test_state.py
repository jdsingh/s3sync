import time
import pytest
from pathlib import Path
from s3sync.state import StateDB, SyncRecord


def test_db_creates_schema(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    records = db.get_all()
    assert records == []


def test_insert_and_retrieve(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    rec = SyncRecord(
        path=Path("/tmp/reports/q1.pdf"),
        watch_root=Path("/tmp/reports"),
        mtime=1700000000.0,
        size=12345,
        s3_key="reports/q1.pdf",
        synced_at=time.time(),
        encrypted=False,
    )
    db.upsert(rec)
    result = db.get(Path("/tmp/reports/q1.pdf"))
    assert result is not None
    assert result.s3_key == "reports/q1.pdf"
    assert result.size == 12345


def test_upsert_updates_existing(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    rec = SyncRecord(
        path=Path("/tmp/f.txt"),
        watch_root=Path("/tmp"),
        mtime=100.0,
        size=10,
        s3_key="f.txt",
        synced_at=time.time(),
        encrypted=False,
    )
    db.upsert(rec)
    rec2 = rec.model_copy(update={"mtime": 200.0, "size": 20})
    db.upsert(rec2)
    result = db.get(Path("/tmp/f.txt"))
    assert result.mtime == 200.0
    assert result.size == 20


def test_delete_removes_record(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    rec = SyncRecord(
        path=Path("/tmp/del.txt"),
        watch_root=Path("/tmp"),
        mtime=100.0,
        size=5,
        s3_key="del.txt",
        synced_at=time.time(),
        encrypted=False,
    )
    db.upsert(rec)
    db.delete(Path("/tmp/del.txt"))
    assert db.get(Path("/tmp/del.txt")) is None


def test_get_missing_returns_none(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    assert db.get(Path("/nonexistent")) is None


def test_db_persists_across_open(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    db = StateDB(db_path)
    rec = SyncRecord(
        path=Path("/tmp/persist.txt"),
        watch_root=Path("/tmp"),
        mtime=1.0,
        size=1,
        s3_key="persist.txt",
        synced_at=time.time(),
        encrypted=False,
    )
    db.upsert(rec)
    db.close()

    db2 = StateDB(db_path)
    result = db2.get(Path("/tmp/persist.txt"))
    assert result is not None
    assert result.s3_key == "persist.txt"

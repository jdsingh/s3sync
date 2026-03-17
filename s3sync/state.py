import sqlite3
import time
from pathlib import Path
from typing import Optional
from pydantic import BaseModel


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS synced_files (
    path       TEXT    PRIMARY KEY,
    watch_root TEXT    NOT NULL,
    mtime      REAL    NOT NULL,
    size       INTEGER NOT NULL,
    s3_key     TEXT    NOT NULL,
    synced_at  REAL    NOT NULL,
    encrypted  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
"""


class SyncRecord(BaseModel):
    path: Path
    watch_root: Path
    mtime: float
    size: int
    s3_key: str
    synced_at: float
    encrypted: bool = False


class StateDB:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    def upsert(self, record: SyncRecord) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO synced_files
                (path, watch_root, mtime, size, s3_key, synced_at, encrypted)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(record.path),
                str(record.watch_root),
                record.mtime,
                record.size,
                record.s3_key,
                record.synced_at,
                int(record.encrypted),
            ),
        )
        self._conn.commit()

    def get(self, path: Path) -> Optional[SyncRecord]:
        row = self._conn.execute(
            "SELECT * FROM synced_files WHERE path = ?", (str(path),)
        ).fetchone()
        if row is None:
            return None
        return SyncRecord(
            path=Path(row["path"]),
            watch_root=Path(row["watch_root"]),
            mtime=row["mtime"],
            size=row["size"],
            s3_key=row["s3_key"],
            synced_at=row["synced_at"],
            encrypted=bool(row["encrypted"]),
        )

    def delete(self, path: Path) -> None:
        self._conn.execute(
            "DELETE FROM synced_files WHERE path = ?", (str(path),)
        )
        self._conn.commit()

    def get_all(self) -> list[SyncRecord]:
        rows = self._conn.execute("SELECT * FROM synced_files").fetchall()
        return [
            SyncRecord(
                path=Path(r["path"]),
                watch_root=Path(r["watch_root"]),
                mtime=r["mtime"],
                size=r["size"],
                s3_key=r["s3_key"],
                synced_at=r["synced_at"],
                encrypted=bool(r["encrypted"]),
            )
            for r in rows
        ]

    def get_by_watch_root(self, watch_root: Path) -> list[SyncRecord]:
        rows = self._conn.execute(
            "SELECT * FROM synced_files WHERE watch_root = ?", (str(watch_root),)
        ).fetchall()
        return [
            SyncRecord(
                path=Path(r["path"]),
                watch_root=Path(r["watch_root"]),
                mtime=r["mtime"],
                size=r["size"],
                s3_key=r["s3_key"],
                synced_at=r["synced_at"],
                encrypted=bool(r["encrypted"]),
            )
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()

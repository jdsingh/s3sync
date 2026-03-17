import fnmatch
import logging
import time
from pathlib import Path

from s3sync.config import WatchEntry
from s3sync.state import StateDB, SyncRecord
from s3sync.sync import S3Syncer, _s3_key

logger = logging.getLogger(__name__)


def _matches(filename: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(filename, p) for p in patterns)


def _should_sync(file: Path, entry: WatchEntry) -> bool:
    name = file.name
    if _matches(name, entry.exclude):
        return False
    if entry.include and not _matches(name, entry.include):
        return False
    return True


def run_initial_sync(
    entry: WatchEntry,
    db: StateDB,
    syncer: S3Syncer,
    tmp_dir: Path,
) -> None:
    logger.info("InitialSync: scanning %s", entry.path)
    uploaded = 0
    skipped = 0

    for file in sorted(entry.path.rglob("*")):
        # is_file() follows symlinks; symlink targets that are dirs or broken
        # links return False, so they are skipped. The symlink path itself is
        # used for the S3 key so the key stays within the watched directory.
        if not file.is_file():
            continue
        if not _should_sync(file, entry):
            logger.debug("Skipping excluded/unincluded file: %s", file)
            continue

        try:
            stat = file.stat()
        except OSError as e:
            logger.warning("Cannot stat %s: %s", file, e)
            continue

        record = db.get(file)
        needs_upload = (
            record is None
            or record.mtime != stat.st_mtime
            or record.size != stat.st_size
        )

        if not needs_upload:
            skipped += 1
            continue

        try:
            if entry.encrypt:
                _upload_encrypted(file, entry, syncer, db, tmp_dir, stat)
            else:
                syncer.upload(file, entry)
                db.upsert(SyncRecord(
                    path=file,
                    watch_root=entry.path,
                    mtime=stat.st_mtime,
                    size=stat.st_size,
                    s3_key=_s3_key(file, entry, encrypted=False),
                    synced_at=time.time(),
                    encrypted=False,
                ))
            uploaded += 1
        except Exception as e:
            logger.error("Failed to upload %s: %s", file, e)

    logger.info("InitialSync done for %s: %d uploaded, %d skipped", entry.path, uploaded, skipped)


def _upload_encrypted(
    file: Path,
    entry: WatchEntry,
    syncer: S3Syncer,
    db: StateDB,
    tmp_dir: Path,
    stat,
) -> None:
    from s3sync.crypto import encrypt_file, parse_recipient

    recipients = [parse_recipient(r) for r in entry.age_recipients]
    enc_tmp = encrypt_file(file, recipients, tmp_dir)
    try:
        syncer.upload_encrypted(enc_tmp, file, entry)
        db.upsert(SyncRecord(
            path=file,
            watch_root=entry.path,
            mtime=stat.st_mtime,
            size=stat.st_size,
            s3_key=_s3_key(file, entry, encrypted=True),
            synced_at=time.time(),
            encrypted=True,
        ))
    finally:
        if enc_tmp.exists():
            enc_tmp.unlink()

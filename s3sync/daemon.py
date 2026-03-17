import logging
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from s3sync.config import AppConfig, WatchEntry
from s3sync.crypto import encrypt_file, parse_recipient
from s3sync.initial_sync import run_initial_sync, should_sync
from s3sync.state import StateDB, SyncRecord
from s3sync.sync import S3Syncer, _s3_key

logger = logging.getLogger(__name__)
DEBOUNCE_SECONDS = 0.5


def _is_stable(file: Path, wait: float = 0.1) -> bool:
    try:
        size1 = file.stat().st_size
        time.sleep(wait)
        size2 = file.stat().st_size
        return size1 == size2
    except OSError:
        return False


class FileEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        entry: WatchEntry,
        syncer: S3Syncer,
        db: StateDB,
        tmp_dir: Path,
        debounce_seconds: float = DEBOUNCE_SECONDS,
    ) -> None:
        self._entry = entry
        self._syncer = syncer
        self._db = db
        self._tmp_dir = tmp_dir
        self._debounce = debounce_seconds
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        self._stopped = False
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="s3sync")

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule_upload(Path(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule_upload(Path(event.src_path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._handle_delete(Path(event.src_path))

    def _schedule_upload(self, file: Path) -> None:
        key = str(file)
        with self._lock:
            if self._stopped:
                return
            if key in self._timers:
                self._timers[key].cancel()
            timer = threading.Timer(self._debounce, self._do_upload, args=[file])
            self._timers[key] = timer
            timer.start()

    def _do_upload(self, file: Path) -> None:
        with self._lock:
            self._timers.pop(str(file), None)

        if not file.is_file():
            if file.is_dir():
                logger.warning("Path changed from file to directory, skipping: %s", file)
                if self._entry.delete_on_remove:
                    try:
                        s3_key = _s3_key(file, self._entry)
                        self._syncer.delete(s3_key, self._entry)
                        self._db.delete(file)
                    except Exception as e:
                        logger.error("Failed to delete S3 key for dir path %s: %s", file, e)
            return

        if not should_sync(file, self._entry):
            return
        if not _is_stable(file):
            self._schedule_upload(file)
            return

        try:
            self._executor.submit(self._upload_file, file)
        except RuntimeError:
            logger.debug("Executor already shut down, dropping upload for %s", file)

    def _upload_file(self, file: Path) -> None:
        try:
            if self._entry.encrypt:
                recipients = [parse_recipient(r) for r in self._entry.age_recipients]
                try:
                    enc_tmp = encrypt_file(file, recipients, self._tmp_dir)
                except Exception as e:
                    logger.error("Encryption failed for %s, skipping upload: %s", file, e)
                    return
                try:
                    self._syncer.upload_encrypted(enc_tmp, file, self._entry)
                finally:
                    if enc_tmp.exists():
                        enc_tmp.unlink()
            else:
                self._syncer.upload(file, self._entry)

            # Stat after upload so DB records the file state that was actually sent.
            try:
                stat = file.stat()
            except OSError as e:
                logger.warning("Cannot stat %s after upload: %s", file, e)
                return

            self._db.upsert(SyncRecord(
                path=file,
                watch_root=self._entry.path,
                mtime=stat.st_mtime,
                size=stat.st_size,
                s3_key=_s3_key(file, self._entry, encrypted=self._entry.encrypt),
                synced_at=time.time(),
                encrypted=self._entry.encrypt,
            ))
        except Exception as e:
            logger.error("Upload failed for %s: %s", file, e)

    def _handle_delete(self, file: Path) -> None:
        if not self._entry.delete_on_remove:
            return
        record = self._db.get(file)
        s3_key = record.s3_key if record else _s3_key(file, self._entry, self._entry.encrypt)
        try:
            self._syncer.delete(s3_key, self._entry)
            self._db.delete(file)
        except Exception as e:
            logger.error("Delete failed for %s: %s", s3_key, e)

    def flush(self) -> None:
        """Cancel pending timers and wait for in-flight uploads."""
        with self._lock:
            self._stopped = True
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
        self._executor.shutdown(wait=True)


def run_daemon(config: AppConfig, config_dir: Path) -> None:
    from s3sync.log import setup_logging
    setup_logging()

    db_path = config_dir / "state.db"
    tmp_dir = config_dir / "tmp"
    db = StateDB(db_path)

    try:
        _run_daemon_inner(config, config_dir, db, tmp_dir)
    finally:
        db.close()


def _run_daemon_inner(config: AppConfig, config_dir: Path, db: StateDB, tmp_dir: Path) -> None:
    import boto3 as _boto3

    syncer = S3Syncer(region=config.aws.region, profile=config.aws.profile)

    # Validate AWS credentials before doing anything else
    try:
        sts = _boto3.Session(
            profile_name=config.aws.profile, region_name=config.aws.region
        ).client("sts")
        sts.get_caller_identity()
    except Exception as e:
        logger.error("AWS credential validation failed: %s", e)
        raise SystemExit(1)

    # Clean stale temp files
    _cleanup_stale_tmp(tmp_dir)

    # Validate watch entries
    valid_entries = []
    for entry in config.watch:
        if not entry.path.exists():
            logger.warning("Watch path does not exist, skipping: %s", entry.path)
            continue
        valid_entries.append(entry)

    # Initial sync
    for entry in valid_entries:
        run_initial_sync(entry, db, syncer, tmp_dir)

    # Start watchdog observers
    observer = Observer()
    handlers = []
    for entry in valid_entries:
        handler = FileEventHandler(entry=entry, syncer=syncer, db=db, tmp_dir=tmp_dir)
        observer.schedule(handler, str(entry.path), recursive=True)
        handlers.append(handler)

    observer.start()
    logger.info("Daemon watching %d path(s)", len(valid_entries))

    def _shutdown(signum, frame):
        logger.info("Shutting down daemon...")
        for h in handlers:
            h.flush()
        observer.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    observer.join()
    logger.info("Daemon stopped.")


def _cleanup_stale_tmp(tmp_dir: Path, max_age_seconds: int = 3600) -> None:
    if not tmp_dir.exists():
        return
    now = time.time()
    for f in tmp_dir.iterdir():
        if f.is_file() and (now - f.stat().st_mtime) > max_age_seconds:
            try:
                f.unlink()
                logger.debug("Cleaned stale temp file: %s", f)
            except OSError as e:
                logger.warning("Could not remove stale temp file %s: %s", f, e)

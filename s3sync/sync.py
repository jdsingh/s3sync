import logging
import time
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig

from s3sync.config import WatchEntry

logger = logging.getLogger(__name__)

MULTIPART_THRESHOLD = 50 * 1024 * 1024  # 50 MB
TRANSFER_CONFIG = TransferConfig(multipart_threshold=MULTIPART_THRESHOLD)
MAX_RETRIES = 3
RETRY_DELAYS = [1, 3, 9]


def _s3_key(local_file: Path, entry: WatchEntry, encrypted: bool = False) -> str:
    relative = local_file.relative_to(entry.path)
    key = entry.prefix.rstrip("/") + "/" + str(relative).lstrip("/") if entry.prefix else str(relative)
    if encrypted:
        key += ".age"
    return key


class S3Syncer:
    def __init__(self, region: str, profile: str | None) -> None:
        session = boto3.Session(profile_name=profile, region_name=region)
        self._s3 = session.client("s3")

    def _retry(self, action_name: str, fn) -> None:
        for attempt, delay in enumerate(RETRY_DELAYS, 1):
            try:
                fn()
                return
            except Exception as e:
                if attempt >= MAX_RETRIES:
                    logger.error("%s failed after %d attempts: %s", action_name, MAX_RETRIES, e)
                    raise
                logger.warning("%s attempt %d failed: %s — retrying in %ds", action_name, attempt, e, delay)
                time.sleep(delay)

    def upload(self, local_file: Path, entry: WatchEntry) -> None:
        if not local_file.is_file():
            logger.warning("Skipping non-regular file: %s", local_file)
            return
        key = _s3_key(local_file, entry, encrypted=False)
        logger.info("Uploading %s → s3://%s/%s", local_file, entry.bucket, key)

        def _do():
            with open(local_file, "rb") as f:
                self._s3.upload_fileobj(f, entry.bucket, key, Config=TRANSFER_CONFIG)

        self._retry(f"upload {key}", _do)

    def upload_encrypted(self, enc_tmp: Path, original: Path, entry: WatchEntry) -> None:
        if not enc_tmp.is_file():
            logger.warning("Skipping non-regular encrypted file: %s", enc_tmp)
            return
        key = _s3_key(original, entry, encrypted=True)
        logger.info("Uploading (encrypted) %s → s3://%s/%s", original, entry.bucket, key)

        def _do():
            with open(enc_tmp, "rb") as f:
                self._s3.upload_fileobj(f, entry.bucket, key, Config=TRANSFER_CONFIG)

        self._retry(f"upload_encrypted {key}", _do)

    def delete(self, s3_key: str, entry: WatchEntry) -> None:
        logger.info("Deleting s3://%s/%s", entry.bucket, s3_key)

        def _do():
            self._s3.delete_object(Bucket=entry.bucket, Key=s3_key)

        self._retry(f"delete {s3_key}", _do)

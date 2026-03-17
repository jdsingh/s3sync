import logging
import pytest
import boto3
from moto import mock_aws
from pathlib import Path
from unittest.mock import MagicMock
from botocore.exceptions import ClientError
from s3sync.sync import S3Syncer, TRANSFER_CONFIG
from s3sync.config import WatchEntry


@pytest.fixture
def watch_entry(tmp_path: Path) -> WatchEntry:
    return WatchEntry(
        path=tmp_path / "watch",
        bucket="test-bucket",
        prefix="data/",
        delete_on_remove=False,
    )


@pytest.fixture
def aws_setup():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-bucket")
        yield s3


def test_upload_file(tmp_path: Path, watch_entry: WatchEntry, aws_setup) -> None:
    f = tmp_path / "watch" / "report.txt"
    f.parent.mkdir(parents=True)
    f.write_text("hello")

    syncer = S3Syncer(region="us-east-1", profile=None)
    syncer.upload(f, watch_entry)

    obj = aws_setup.get_object(Bucket="test-bucket", Key="data/report.txt")
    assert obj["Body"].read() == b"hello"


def test_upload_preserves_subdir_structure(tmp_path: Path, watch_entry: WatchEntry, aws_setup) -> None:
    f = tmp_path / "watch" / "2024" / "q1.txt"
    f.parent.mkdir(parents=True)
    f.write_text("q1")

    syncer = S3Syncer(region="us-east-1", profile=None)
    syncer.upload(f, watch_entry)

    obj = aws_setup.get_object(Bucket="test-bucket", Key="data/2024/q1.txt")
    assert obj["Body"].read() == b"q1"


def test_delete_file(tmp_path: Path, watch_entry: WatchEntry, aws_setup) -> None:
    aws_setup.put_object(Bucket="test-bucket", Key="data/old.txt", Body=b"old")

    syncer = S3Syncer(region="us-east-1", profile=None)
    syncer.delete("data/old.txt", watch_entry)

    with pytest.raises(aws_setup.exceptions.NoSuchKey):
        aws_setup.get_object(Bucket="test-bucket", Key="data/old.txt")


def test_upload_encrypted_file(tmp_path: Path, aws_setup) -> None:
    from s3sync.crypto import generate_test_keypair, encrypt_file

    _, recipient = generate_test_keypair()
    f = tmp_path / "watch" / "secret.pdf"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"secret content")

    entry = WatchEntry(
        path=tmp_path / "watch",
        bucket="test-bucket",
        prefix="docs/",
        encrypt=True,
        age_recipients=[str(recipient)],
    )

    tmp_dir = tmp_path / "tmp"
    enc = encrypt_file(f, [recipient], tmp_dir)

    syncer = S3Syncer(region="us-east-1", profile=None)
    syncer.upload_encrypted(enc, f, entry)

    obj = aws_setup.get_object(Bucket="test-bucket", Key="docs/secret.pdf.age")
    assert len(obj["Body"].read()) > 0


# ---------------------------------------------------------------------------
# Retry behavior, permanent failure logging, TransferConfig, named profile
# ---------------------------------------------------------------------------

def _make_client_error() -> ClientError:
    return ClientError({"Error": {"Code": "InternalError", "Message": "simulated"}}, "Upload")


def test_upload_retries_on_client_error(
    tmp_path: Path, watch_entry: WatchEntry, aws_setup, mocker
) -> None:
    """upload() retries 3 times; time.sleep is called after attempts 1 and 2 (not after final)."""
    f = tmp_path / "watch" / "retry.txt"
    f.parent.mkdir(parents=True)
    f.write_text("retry")

    syncer = S3Syncer(region="us-east-1", profile=None)
    mock_sleep = mocker.patch("s3sync.sync.time.sleep")
    mocker.patch.object(syncer._s3, "upload_fileobj", side_effect=_make_client_error())

    with pytest.raises(ClientError):
        syncer.upload(f, watch_entry)

    # Delays [1, 3, 9]: sleep after attempt 1 → 1s, after attempt 2 → 3s,
    # attempt 3 raises immediately (no sleep).
    assert mock_sleep.call_count == 2
    assert [c.args[0] for c in mock_sleep.call_args_list] == [1, 3]


def test_upload_logs_error_after_all_retries(
    tmp_path: Path, watch_entry: WatchEntry, aws_setup, mocker, caplog
) -> None:
    """logger.error is called after all retries exhausted and exception propagates."""
    f = tmp_path / "watch" / "logtest.txt"
    f.parent.mkdir(parents=True)
    f.write_text("log")

    syncer = S3Syncer(region="us-east-1", profile=None)
    mocker.patch("s3sync.sync.time.sleep")
    mocker.patch.object(syncer._s3, "upload_fileobj", side_effect=_make_client_error())

    with caplog.at_level(logging.ERROR, logger="s3sync.sync"):
        with pytest.raises(ClientError):
            syncer.upload(f, watch_entry)

    errors = [r.message for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "failed after 3 attempts" in errors[0]


def test_upload_passes_transfer_config(
    tmp_path: Path, watch_entry: WatchEntry, aws_setup, mocker
) -> None:
    """upload() passes Config=TRANSFER_CONFIG to upload_fileobj."""
    f = tmp_path / "watch" / "cfg.txt"
    f.parent.mkdir(parents=True)
    f.write_text("cfg")

    syncer = S3Syncer(region="us-east-1", profile=None)
    mock_upload = mocker.patch.object(syncer._s3, "upload_fileobj")

    syncer.upload(f, watch_entry)

    mock_upload.assert_called_once()
    assert mock_upload.call_args.kwargs.get("Config") is TRANSFER_CONFIG


def test_named_aws_profile(mocker) -> None:
    """S3Syncer passes profile_name to boto3.Session when a named profile is given."""
    mock_session = MagicMock()
    mock_session.client.return_value = MagicMock()
    mock_cls = mocker.patch("s3sync.sync.boto3.Session", return_value=mock_session)

    S3Syncer(region="us-east-1", profile="myprofile")

    mock_cls.assert_called_once_with(profile_name="myprofile", region_name="us-east-1")

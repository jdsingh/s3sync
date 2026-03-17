import time
import pytest
import boto3
from moto import mock_aws
from pathlib import Path
from s3sync.sync import S3Syncer
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

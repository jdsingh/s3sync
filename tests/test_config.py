import pytest
from pathlib import Path
from s3sync.config import load_config, AppConfig, WatchEntry


MINIMAL_TOML = """
[aws]
profile = "default"
region = "us-east-1"

[[watch]]
path = "/tmp/test"
bucket = "my-bucket"
prefix = "data/"
"""

ENCRYPTED_TOML = """
[aws]
profile = "default"
region = "us-east-1"

[[watch]]
path = "/tmp/test"
bucket = "my-bucket"
prefix = "docs/"
encrypt = true
age_recipients = ["age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p"]
age_identity_file = "~/.config/s3sync/age-key.txt"
"""

MISSING_RECIPIENTS_TOML = """
[aws]
profile = "default"
region = "us-east-1"

[[watch]]
path = "/tmp/test"
bucket = "my-bucket"
prefix = "docs/"
encrypt = true
"""


def test_load_minimal_config(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(MINIMAL_TOML)
    cfg = load_config(cfg_file)
    assert isinstance(cfg, AppConfig)
    assert cfg.aws.profile == "default"
    assert len(cfg.watch) == 1
    assert cfg.watch[0].bucket == "my-bucket"


def test_watch_entry_defaults(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(MINIMAL_TOML)
    cfg = load_config(cfg_file)
    entry = cfg.watch[0]
    assert entry.delete_on_remove is False
    assert entry.encrypt is False
    assert entry.include == []
    assert entry.exclude == []


def test_encrypted_entry_parsed(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(ENCRYPTED_TOML)
    cfg = load_config(cfg_file)
    entry = cfg.watch[0]
    assert entry.encrypt is True
    assert len(entry.age_recipients) == 1
    assert entry.age_identity_file is not None


def test_encrypt_without_recipients_raises(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(MISSING_RECIPIENTS_TOML)
    with pytest.raises(ValueError, match="age_recipients"):
        load_config(cfg_file)


def test_missing_config_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nonexistent.toml")

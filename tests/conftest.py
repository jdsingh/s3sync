import pytest
from pathlib import Path


@pytest.fixture
def tmp_watch_dir(tmp_path: Path) -> Path:
    d = tmp_path / "watch"
    d.mkdir()
    return d


@pytest.fixture
def tmp_config_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".config" / "s3sync"
    d.mkdir(parents=True)
    return d

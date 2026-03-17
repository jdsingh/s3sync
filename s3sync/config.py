import tomllib
from pathlib import Path
from pydantic import BaseModel, model_validator, field_validator


class AwsConfig(BaseModel):
    profile: str = "default"
    region: str = "us-east-1"


class WatchEntry(BaseModel):
    path: Path
    bucket: str
    prefix: str = ""
    delete_on_remove: bool = False
    include: list[str] = []
    exclude: list[str] = []
    encrypt: bool = False
    age_recipients: list[str] = []
    age_identity_file: Path | None = None

    @field_validator("age_identity_file", mode="before")
    @classmethod
    def expand_identity_path(cls, v: str | None) -> Path | None:
        if v is None:
            return None
        return Path(v).expanduser()

    @field_validator("path", mode="before")
    @classmethod
    def expand_watch_path(cls, v: str) -> Path:
        return Path(v).expanduser()

    @model_validator(mode="after")
    def check_encryption_fields(self) -> "WatchEntry":
        if self.encrypt and not self.age_recipients:
            raise ValueError(
                f"Watch entry for '{self.path}' has encrypt=true "
                "but age_recipients is missing or empty"
            )
        return self


class AppConfig(BaseModel):
    aws: AwsConfig
    watch: list[WatchEntry]


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return AppConfig.model_validate(data)


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "s3sync" / "config.toml"

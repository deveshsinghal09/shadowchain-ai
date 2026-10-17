"""Typed environment configuration; secrets are never embedded in source."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.auth import ApiClient

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: Literal["development", "test", "production"] = "development"
    demo_mode: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    cors_origins: list[str] = Field(default_factory=list)
    cors_allow_credentials: bool = False
    api_clients: list[ApiClient] = Field(default_factory=list, max_length=100, repr=False)
    api_query_limit_per_minute: int = Field(default=30, ge=1, le=1000)
    api_request_max_bytes: int = Field(default=131072, ge=4096, le=1048576)
    ws_poll_seconds: float = Field(default=1, ge=0.05, le=10)
    ws_send_timeout_seconds: float = Field(default=5, ge=0.1, le=15)

    @field_validator("api_clients")
    @classmethod
    def unique_api_clients(cls, value):
        if len({c.principal_id for c in value}) != len(value) or len(
            {c.token_sha256 for c in value}
        ) != len(value):
            raise ValueError("API principal IDs and token digests must be unique")
        return value

    falkordb_host: str = "localhost"
    falkordb_port: int = Field(default=6379, ge=1, le=65535)
    falkordb_username: str = "default"
    falkordb_password: SecretStr | None = None
    falkordb_graph: str = Field(default="shadowchain", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    falkordb_tls: bool = False
    falkordb_connect_timeout_seconds: float = Field(default=2, gt=0, le=30)
    falkordb_socket_timeout_seconds: float = Field(default=3, gt=0, le=60)
    falkordb_query_timeout_ms: int = Field(default=2000, ge=1, le=30000)
    falkordb_max_connections: int = Field(default=20, ge=1, le=100)
    falkordb_retry_attempts: int = Field(default=3, ge=1, le=5)
    falkordb_retry_backoff_seconds: float = Field(default=0.05, ge=0, le=1)
    falkordb_schema_timeout_seconds: float = Field(default=30, gt=0, le=120)
    aws_enabled: bool = False
    cloudtrail_enabled: bool = False
    aws_region: str = Field(default="us-east-1", pattern=r"^[a-z]{2}(?:-[a-z]+)+-\d+$")
    aws_profile: str | None = None
    aws_account_id: str | None = Field(default=None, pattern=r"^\d{12}$")
    aws_tenant_id: str = Field(default="shadowchain-aws-import", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    aws_max_records: int = Field(default=300, ge=1, le=1000)
    aws_max_pages: int = Field(default=20, ge=1, le=100)
    export_mode: Literal["local", "s3"] = "local"
    s3_export_bucket: str | None = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$"
    )
    export_local_dir: Path = Path("artifacts/reports")

    @field_validator("aws_profile", "aws_account_id", "s3_export_bucket", mode="before")
    @classmethod
    def empty_aws_value(cls, value):
        return None if value == "" else value

    gemini_api_key: SecretStr | None = None
    agent_provider: Literal["mock", "gemini"] = "mock"
    gemini_model: str = Field(default="gemini-3.8-flash", pattern=r"^[a-zA-Z0-9.-]{1,80}$")
    agent_model_timeout_seconds: float = Field(default=5, gt=0, le=15)
    agent_model_attempts: int = Field(default=2, ge=1, le=3)
    workflow_lease_seconds: int = Field(default=120, ge=90, le=300)
    workflow_max_attempts: int = Field(default=3, ge=1, le=5)
    workflow_recovery_limit: int = Field(default=10, ge=1, le=50)
    workflow_recovery_enabled: bool = True
    workflow_recovery_interval_seconds: float = Field(default=2, ge=1, le=60)

    @field_validator("falkordb_password", "gemini_api_key", mode="before")
    @classmethod
    def empty_secret_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("cors_origins")
    @classmethod
    def validate_origins(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "*" in origin
            ):
                raise ValueError("CORS origins must be explicit HTTP(S) origins without a path")
        return origins

    @model_validator(mode="after")
    def validate_timeouts_and_production(self) -> "Settings":
        if self.aws_enabled and not self.aws_account_id:
            raise ValueError("AWS_ENABLED requires an explicit AWS_ACCOUNT_ID scope")
        if self.cloudtrail_enabled and not self.aws_enabled:
            raise ValueError("CLOUDTRAIL_ENABLED requires AWS_ENABLED")
        if self.export_mode == "s3" and (not self.aws_enabled or not self.s3_export_bucket):
            raise ValueError("S3 exports require AWS_ENABLED and one S3_EXPORT_BUCKET")
        if self.agent_provider == "gemini" and not self.gemini_api_key:
            raise ValueError("Gemini mode requires an environment API key")
        if self.falkordb_query_timeout_ms >= self.falkordb_socket_timeout_seconds * 1000:
            raise ValueError("FalkorDB query timeout must be below the socket timeout")
        if self.app_env == "production" and not self.falkordb_password:
            raise ValueError("Production requires a FalkorDB password")
        return self


@lru_cache
def get_settings() -> Settings:
    # Configuration only: never cache authoritative application or agent state here.
    return Settings()

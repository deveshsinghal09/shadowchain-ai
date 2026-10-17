import json
import logging

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import JsonFormatter, request_id_context


def test_demo_requires_no_external_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config = Settings(_env_file=None, gemini_api_key="", falkordb_password="")
    assert config.demo_mode
    assert config.gemini_api_key is None
    assert config.falkordb_password is None


@pytest.mark.parametrize("origin", ["*", "https://example.com/path", "ftp://example.com"])
def test_cors_rejects_invalid_origins(origin: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins=[origin])


def test_production_requires_password() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", falkordb_password=None)


def test_query_timeout_must_fit_socket_timeout() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, falkordb_query_timeout_ms=3000, falkordb_socket_timeout_seconds=2)


def test_secrets_are_masked() -> None:
    config = Settings(_env_file=None, gemini_api_key="test-only-secret-value")
    assert "test-only-secret-value" not in repr(config)


def test_structured_logs_are_utc_and_exclude_sensitive_extras() -> None:
    record = logging.LogRecord("test", logging.INFO, "", 0, "request_completed", (), None)
    record.authorization = "sensitive"
    record.status_code = 200
    token = request_id_context.set("correlation-id")
    try:
        output = JsonFormatter().format(record)
    finally:
        request_id_context.reset(token)
    payload = json.loads(output)
    assert payload["timestamp"].endswith("+00:00")
    assert payload["request_id"] == "correlation-id"
    assert payload["status_code"] == 200
    assert "sensitive" not in output

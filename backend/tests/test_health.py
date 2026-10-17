from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import DatabaseUnavailable
from app.main import create_app
from tests.api_helpers import TEST_HEADERS


def test_liveness_does_not_query_database(client: TestClient, graph_stub: MagicMock) -> None:
    graph_stub.reset_mock()
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["demo_mode"] is True
    assert datetime.fromisoformat(body["timestamp"]).utcoffset() == timedelta(0)
    graph_stub.check_ready.assert_not_called()
    graph_stub.query.assert_not_called()


def test_readiness_checks_graph(client: TestClient, graph_stub: MagicMock) -> None:
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json()["dependencies"] == {"falkordb": "ok"}
    graph_stub.check_ready.assert_called_once()


def test_outage_is_503_and_health_stays_alive(client: TestClient, graph_stub: MagicMock) -> None:
    graph_stub.check_ready.side_effect = DatabaseUnavailable()
    response = client.get("/api/v1/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"
    assert response.headers["retry-after"] == "3"
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert client.get("/api/v1/health").status_code == 200
    graph_stub.check_ready.side_effect = None
    assert client.get("/api/v1/ready").status_code == 200


def test_initial_outage_does_not_prevent_process_start(unit_settings: Settings) -> None:
    with patch("app.main.GraphDatabase") as cls:
        database = cls.return_value
        database.query.side_effect = DatabaseUnavailable()
        database.check_ready.side_effect = DatabaseUnavailable()
        with TestClient(create_app(unit_settings), headers=TEST_HEADERS) as client:
            assert client.get("/api/v1/health").status_code == 200
            assert client.get("/api/v1/ready").status_code == 503
            database.check_ready.side_effect = None
            assert client.get("/api/v1/ready").status_code == 200
        database.close.assert_called_once()


def test_request_id_accepts_uuid_and_replaces_untrusted_text(client: TestClient) -> None:
    request_id = str(uuid4())
    assert (
        client.get("/api/v1/health", headers={"X-Request-ID": request_id}).headers["x-request-id"]
        == request_id
    )
    response = client.get("/api/v1/health", headers={"X-Request-ID": "untrusted-secret"})
    assert str(UUID(response.headers["x-request-id"])) != "untrusted-secret"


def test_cors_allows_configured_origin_and_denies_others(client: TestClient) -> None:
    allowed = client.options(
        "/api/v1/ready",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert allowed.status_code == 200
    assert UUID(allowed.headers["x-request-id"])
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    denied = client.options(
        "/api/v1/ready",
        headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "GET"},
    )
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_not_found_has_central_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"


def test_unexpected_exception_does_not_leak_details(
    client: TestClient, graph_stub: MagicMock
) -> None:
    graph_stub.check_ready.side_effect = RuntimeError("secret-internal-password")
    response = client.get("/api/v1/ready", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.json()["error"]["code"] == "internal_error"
    assert "secret-internal-password" not in response.text
    assert response.headers["x-request-id"] == response.json()["request_id"]


def test_database_pool_closed_on_shutdown(unit_settings: Settings, graph_stub: MagicMock) -> None:
    with TestClient(create_app(unit_settings), headers=TEST_HEADERS):
        pass
    graph_stub.close.assert_called_once()


def test_production_disables_openapi(unit_settings: Settings, graph_stub: MagicMock) -> None:
    config = Settings(_env_file=None, app_env="production", falkordb_password="test-only-password")
    with TestClient(create_app(config), headers=TEST_HEADERS) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_validation_errors_do_not_echo_sensitive_inputs(
    unit_settings: Settings, graph_stub: MagicMock
) -> None:
    application = create_app(unit_settings)

    @application.get("/test-validation")
    def validation_route(count: int) -> dict[str, int]:
        return {"count": count}

    with TestClient(application, raise_server_exceptions=False, headers=TEST_HEADERS) as client:
        response = client.get("/test-validation", params={"count": "private-secret-input"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        assert "private-secret-input" not in response.text

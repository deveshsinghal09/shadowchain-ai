import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.falkordb import GraphDatabase
from app.main import create_app
from app.repositories.foundation import FoundationRepository
from tests.api_helpers import TEST_HEADERS

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "true", reason="Real DB opt-in required"
    ),
]


def test_live_health_and_graph_readiness(isolated_graph_settings: Settings) -> None:
    with TestClient(create_app(isolated_graph_settings), headers=TEST_HEADERS) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/ready").status_code == 200


def test_foundation_is_idempotent_and_recovers_with_new_client(
    isolated_graph_settings: Settings,
) -> None:
    first = GraphDatabase(isolated_graph_settings)
    try:
        repository = FoundationRepository(first)
        before = repository.ensure()
        assert repository.ensure() == before
    finally:
        first.close()
    second = GraphDatabase(isolated_graph_settings)
    try:
        assert FoundationRepository(second).get() == before
    finally:
        second.close()


def test_wrong_password_is_not_ready(isolated_graph_settings: Settings) -> None:
    from pydantic import SecretStr

    config = isolated_graph_settings.model_copy(
        update={"falkordb_password": SecretStr("deliberately-invalid-integration-password")}
    )
    with TestClient(create_app(config), headers=TEST_HEADERS) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/ready").status_code == 503

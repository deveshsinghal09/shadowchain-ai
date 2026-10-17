import time

import pytest
from fastapi.testclient import TestClient

from app.db.falkordb import GraphDatabase
from app.main import create_app
from app.models.workflow import STAGES
from tests.api_helpers import TEST_HEADERS
from tests.workflow_helpers import workflow_world

pytestmark = pytest.mark.integration


def test_startup_recovers_persisted_partial_run_and_exposes_audit(isolated_graph_settings):
    database = GraphDatabase(isolated_graph_settings)
    try:
        database.initialize_schema()
        workflow, request = workflow_world(database, isolated_graph_settings)
        identifier = workflow.repository.create(request, workflow.bootstrap())
        workflow.step(identifier, STAGES[0])
        before = workflow.repository.inspect(identifier)
        assert before.completed_steps == 1
    finally:
        database.close()
    with TestClient(create_app(isolated_graph_settings), headers=TEST_HEADERS) as first:
        saved = first.get("/api/v1/agents/investigations/" + identifier)
        assert saved.status_code == 200 and saved.json()["completed_steps"] == 1
    # Entire application/pool is new. Startup reads graph state; no resume call or local file.
    enabled = isolated_graph_settings.model_copy(update={"workflow_recovery_enabled": True})
    with TestClient(create_app(enabled), headers=TEST_HEADERS) as second:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            response = second.get("/api/v1/agents/investigations/" + identifier)
            if response.json()["status"] == "completed":
                break
            time.sleep(0.05)
        assert response.status_code == 200 and response.json()["status"] == "completed"
        runs = response.json()["runs"]
        assert runs[0] == before.runs[0].model_dump(mode="json")
        assert len(second.get("/api/v1/agents").json()) == 6
        assert len(second.get(f"/api/v1/agents/investigations/{identifier}/memories").json()) == 6
        assert len(second.get(f"/api/v1/agents/investigations/{identifier}/handoffs").json()) == 6
        assert second.get("/api/v1/agents/runs/" + runs[-1]["id"]).json() == runs[-1]
        resumed = second.post(f"/api/v1/agents/investigations/{identifier}/resume")
        assert resumed.json() == response.json()


def test_http_trigger_and_authority_boundary(isolated_graph_settings):
    database = GraphDatabase(isolated_graph_settings)
    try:
        database.initialize_schema()
        _, request = workflow_world(database, isolated_graph_settings)
    finally:
        database.close()
    with TestClient(create_app(isolated_graph_settings), headers=TEST_HEADERS) as client:
        payload = request.model_dump(mode="json")
        response = client.post(
            "/api/v1/agents/investigations",
            json=payload,
            headers={"X-Tenant-ID": "unrelated", "X-Agent-ID": request.event_id},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "completed"
        assert client.post("/api/v1/agents/investigations", json=payload).json() == response.json()
        assert (
            client.post(
                "/api/v1/agents/investigations", json={**payload, "tenant_id": "other"}
            ).status_code
            == 422
        )


@pytest.mark.parametrize("disabled", [{"demo_mode": False}, {"app_env": "production"}])
def test_agent_http_is_gated(isolated_graph_settings, disabled):
    config = isolated_graph_settings.model_copy(update=disabled)
    with TestClient(create_app(config), headers=TEST_HEADERS) as client:
        assert client.get("/api/v1/agents").status_code == 404

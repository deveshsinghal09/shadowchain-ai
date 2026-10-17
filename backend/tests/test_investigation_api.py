import time

import pytest
from fastapi.testclient import TestClient

from app.db.falkordb import GraphDatabase
from app.demo.investigation_setup import setup_investigation_demo
from app.main import create_app
from app.models.workflow import STAGES
from tests.api_helpers import TEST_HEADERS

pytestmark = pytest.mark.integration


def test_soc_payloads_and_cross_backend_session_recovery(isolated_graph_settings):
    database = GraphDatabase(isolated_graph_settings)
    try:
        database.initialize_schema()
        setup = setup_investigation_demo(database, isolated_graph_settings)
        repo, workflow = setup["repository"], setup["workflow"]
        workflow_id = repo.create(setup["request"], setup["space_id"])
        for stage in STAGES[:5]:
            workflow.step(workflow_id, stage)
        case_id = repo.workflow_cases(workflow_id)[0]
    finally:
        database.close()
    base = "/api/v1/investigations/" + case_id
    with TestClient(create_app(isolated_graph_settings), headers=TEST_HEADERS) as first:
        before = first.get(base).json()
        checks = [t for t in before["completed_tasks"] if t["task_type"] == "investigation_check"]
        assert len(checks) == 2 and before["status"] == "INVESTIGATING"
        activity = first.get(base + "/activity").json()
        assert len([r for r in activity["runs"] if r["status"] == "completed"]) == 5
    config = isolated_graph_settings.model_copy(update={"workflow_recovery_enabled": True})
    with TestClient(create_app(config), headers=TEST_HEADERS) as second:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            response = second.get(base)
            if response.json().get("status") == "AWAITING_REVIEW":
                break
            time.sleep(0.05)
        assert response.status_code == 200, response.text
        after = response.json()
        assert after["status"] == "AWAITING_REVIEW"
        restored = {t["id"]: t for t in after["completed_tasks"]}
        assert all(restored[t["id"]] == t for t in checks)
        cases = second.get("/api/v1/investigations?status=AWAITING_REVIEW").json()
        assert cases["count"] == 1 and cases["investigations"][0]["investigation_id"] == case_id
        assert cases["investigations"][0]["created_at"].endswith("Z")
        assert second.get(base + "/timeline").json()["items"]
        graph = second.get(base + "/graph")
        assert graph.status_code == 200 and graph.json()["relationships"]
        history = second.get(base + "/history").json()
        assert history["previous_incidents"] and history["useful_historical_checks"]
        assert second.get(base + "/memories").json()["memories"]
        evidence_id = after["verified_evidence"][0]["id"]
        inspected = second.get(base + "/evidence/" + evidence_id).json()
        assert inspected["verification_scope"] == "source_integrity" and inspected["provenance"]
        invalid = second.post(
            base + "/transitions",
            json={
                "request_id": "invalid",
                "expected_revision": after["revision"],
                "status": "CLOSED",
                "reason": "Must reject skipping review",
            },
        )
        assert invalid.status_code == 409
        extra = second.post(
            base + "/transitions",
            json={
                "request_id": "invalid",
                "expected_revision": after["revision"],
                "status": "RESOLVED",
                "reason": "No actor override",
                "actor_id": "injected",
            },
        )
        assert extra.status_code == 422


@pytest.mark.parametrize("override", [{"demo_mode": False}, {"app_env": "production"}])
def test_case_routes_are_disabled_outside_local_demo(isolated_graph_settings, override):
    with TestClient(
        create_app(isolated_graph_settings.model_copy(update=override)), headers=TEST_HEADERS
    ) as client:
        assert client.get("/api/v1/investigations").status_code == 404

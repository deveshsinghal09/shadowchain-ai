import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.api_helpers import TEST_HEADERS
from tests.workflow_helpers import workflow_world

pytestmark = pytest.mark.integration


def test_interactive_remediation_api_and_explicit_reviews(live_database, isolated_graph_settings):
    workflow, _ = workflow_world(live_database, isolated_graph_settings)
    workflow.bootstrap()
    with TestClient(create_app(isolated_graph_settings), headers=TEST_HEADERS) as client:
        response = client.post(
            "/api/v1/remediation/recommendations",
            json={"request_id": "api", "analysis": {"at": "2026-10-16T12:00:00Z", "scenario": "A"}},
        )
        assert response.status_code == 200, response.text
        proposal = response.json()["proposals"][0]
        base = "/api/v1/remediation/proposals/" + proposal["proposal_id"]
        response = client.post(base + "/simulations", json={"request_id": "api-simulation"})
        assert response.status_code == 200, response.text
        simulation = response.json()
        assert not simulation["after"]["findings"] and simulation["historical_evidence_ids"]
        assert not simulation["source_cloud_graph_mutated"]
        assert (
            client.get("/api/v1/remediation/simulations/" + simulation["simulation_id"]).json()
            == simulation
        )
        assert client.get(base).json()["simulation_ids"] == [simulation["simulation_id"]]
        assert client.get("/api/v1/remediation/proposals").json()
        invalid = client.post(
            base + "/reviews",
            json={
                "request_id": "inject",
                "expected_revision": 0,
                "status": "APPROVED",
                "reason": "SYNTHETIC",
                "execute": True,
            },
        )
        assert invalid.status_code == 422
        review = client.post(
            base + "/reviews",
            json={
                "request_id": "review",
                "expected_revision": 0,
                "status": "UNDER_REVIEW",
                "reason": "SYNTHETIC explicit human review",
            },
        )
        assert review.status_code == 200, review.text
        approval = client.post(
            base + "/reviews",
            json={
                "request_id": "approve",
                "expected_revision": 1,
                "status": "APPROVED",
                "simulation_id": simulation["simulation_id"],
                "reason": "SYNTHETIC approve recommendation, no deployment",
            },
        )
        assert approval.status_code == 200, approval.text
        assert approval.json()["approval_scope"] == "recommendation_only"
        assert not approval.json()["execution_allowed"]
        assert client.post(base + "/execute").status_code == 404


@pytest.mark.parametrize("override", [{"demo_mode": False}, {"app_env": "production"}])
def test_remediation_api_is_local_demo_only(isolated_graph_settings, override):
    with TestClient(
        create_app(isolated_graph_settings.model_copy(update=override)), headers=TEST_HEADERS
    ) as client:
        assert client.get("/api/v1/remediation/proposals").status_code == 404

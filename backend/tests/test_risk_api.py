import pytest
from fastapi.testclient import TestClient

from app.demo.dataset import hour
from app.main import create_app
from tests.api_helpers import TEST_HEADERS


@pytest.mark.integration
def test_risk_endpoints_discovery_history_timeline_evidence_difference_and_simulation(
    isolated_graph_settings,
):
    with TestClient(create_app(isolated_graph_settings), headers=TEST_HEADERS) as client:
        assert client.post("/api/v1/demo/seed").status_code == 200
        assert (
            client.post(
                "/api/v1/demo/scenarios/A/replay", json={"expected_generation": 0}
            ).status_code
            == 200
        )
        before = client.post("/api/v1/risk/paths/historical", json={"at": hour(9).isoformat()})
        assert before.status_code == 200, before.text
        assert before.json()["findings"] == []
        after = client.post("/api/v1/risk/paths/discover", json={"at": hour(12).isoformat()})
        assert after.status_code == 200, after.text
        finding = after.json()["findings"][0]
        assert finding["classification"] == "POTENTIAL ATTACK PATH"
        window = {"before": hour(9).isoformat(), "after": hour(12).isoformat()}
        timeline = client.post("/api/v1/risk/timeline", json=window)
        assert timeline.status_code == 200, timeline.text
        assert [p["path_count"] for p in timeline.json()["points"]] == [0, 0, 0, 1]
        difference = client.post("/api/v1/risk/differences", json=window)
        assert difference.status_code == 200, difference.text
        assert difference.json()["added_path_ids"] == [finding["path_id"]]
        evidence = client.get(f"/api/v1/risk/findings/{finding['finding_id']}/evidence")
        assert evidence.status_code == 200, evidence.text
        assert evidence.json()["evidence"]
        stored = client.get(f"/api/v1/risk/analyses/{after.json()['analysis_id']}")
        assert stored.status_code == 200 and stored.json() == after.json()
        simulation = client.post(
            "/api/v1/risk/what-if",
            json={
                "at": hour(12).isoformat(),
                "remove_relationship_ids": [finding["relationship_ids"][0]],
            },
        )
        assert simulation.status_code == 200, simulation.text
        assert simulation.json()["after"]["findings"] == []
        assert simulation.json()["graph_mutated"] is False
        assert (
            client.post("/api/v1/risk/paths/discover", json={"at": hour(12).isoformat()}).json()
            == after.json()
        )


@pytest.mark.parametrize(
    "endpoint,payload",
    [
        ("paths/discover", {"at": hour(12).isoformat(), "tenant_id": "victim"}),
        ("paths/discover", {"at": hour(12).isoformat(), "scenario": None}),
        ("paths/discover", {"at": hour(12).isoformat(), "max_role_hops": 100}),
        ("differences", {"before": hour(12).isoformat(), "after": hour(9).isoformat()}),
        ("what-if", {"at": hour(12).isoformat(), "remove_relationship_ids": ["') DELETE n //"]}),
    ],
)
def test_risk_api_rejects_unsafe_and_out_of_scope_requests(client, endpoint, payload):
    response = client.post("/api/v1/risk/" + endpoint, json=payload)
    assert response.status_code == 422

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.api_helpers import TEST_HEADERS


def test_demo_endpoints_are_disabled_when_demo_mode_is_off(
    unit_settings: Settings,
    graph_stub: MagicMock,
) -> None:
    config = unit_settings.model_copy(update={"demo_mode": False})
    with TestClient(create_app(config), headers=TEST_HEADERS) as client:
        assert client.get("/api/v1/demo/scenarios").status_code == 404
        assert client.post("/api/v1/demo/seed").status_code == 404


def test_event_payload_cannot_choose_tenant(client: TestClient) -> None:
    response = client.post("/api/v1/demo/events", json={"tenant_id": "production"})
    assert response.status_code == 422


@pytest.mark.integration
@pytest.mark.parametrize("scenario", ["A", "B", "C"])
def test_scenario_api_seed_replay_inspect_compare_reset(
    isolated_graph_settings: Settings,
    scenario: str,
) -> None:
    with TestClient(create_app(isolated_graph_settings), headers=TEST_HEADERS) as client:
        seed = client.post("/api/v1/demo/seed")
        assert seed.status_code == 200, seed.text
        assert seed.json()["scenarios"][scenario]["inventory"]["Identity"] == 12
        catalog = client.get("/api/v1/demo/scenarios")
        assert catalog.status_code == 200
        assert catalog.json()["synthetic"]
        replay = client.post(
            f"/api/v1/demo/scenarios/{scenario}/replay", json={"expected_generation": 0}
        )
        assert replay.status_code == 200, replay.text
        snapshot = client.get(f"/api/v1/demo/scenarios/{scenario}/snapshot")
        assert snapshot.status_code == 200, snapshot.text
        assert snapshot.json()["synthetic"]
        compare = client.post(
            f"/api/v1/demo/scenarios/{scenario}/compare",
            json={"before": "2026-10-16T09:00:00Z", "after": "2026-10-16T12:00:00Z"},
        )
        assert compare.status_code == 200, compare.text
        assert compare.json()["before_sha256"] != compare.json()["after_sha256"]
        reset = client.post(
            f"/api/v1/demo/scenarios/{scenario}/reset", json={"expected_generation": 0}
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["generation"] == 1 and reset.json()["sequence"] == 0
        stale = client.post(
            f"/api/v1/demo/scenarios/{scenario}/replay", json={"expected_generation": 0}
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "demo_generation_conflict"


@pytest.mark.integration
def test_event_ingestion_api_returns_receipts_and_duplicates(
    isolated_graph_settings: Settings,
) -> None:
    with TestClient(create_app(isolated_graph_settings), headers=TEST_HEADERS) as client:
        assert client.post("/api/v1/demo/seed").status_code == 200
        event = client.get("/api/v1/demo/scenarios").json()["scenarios"]["A"][0]
        body = {"event": event, "expected_generation": 0}
        first = client.post("/api/v1/demo/events", json=body)
        assert first.status_code == 200, first.text
        assert first.json()["occurred_at"] and first.json()["ingested_at"]
        assert not first.json()["duplicate"]
        second = client.post("/api/v1/demo/events", json=body)
        assert second.status_code == 200 and second.json()["duplicate"]
        assert second.json()["ingested_at"] == first.json()["ingested_at"]

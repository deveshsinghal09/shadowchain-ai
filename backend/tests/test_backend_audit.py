import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.v1.memory import DEMO_REVIEWER_ID
from app.demo.dataset import hour
from app.demo.memory_setup import setup_memory_demo
from app.main import create_app
from app.models.events import DEMO_TENANT
from app.models.graph import (
    NodeInput,
    NodeKind,
    Provenance,
    RelationshipInput,
    RelationshipKind,
    TenantScope,
    epoch_us,
)
from app.repositories.graph import GraphRepository
from app.repositories.live import LiveRepository
from app.repositories.visualization import VisualizationRepository
from app.schemas.contract import DiffQuery, GraphQuery
from tests.api_helpers import TEST_HEADERS, TEST_TOKEN
from tests.test_api_contract import configuration


def test_writer_cannot_make_lifecycle_review_decisions(unit_settings, graph_stub):
    settings = configuration(unit_settings, permissions=["read", "write"])
    with TestClient(create_app(settings), headers=TEST_HEADERS) as client:
        denied = client.post(
            "/api/v1/investigations/00000000-0000-0000-0000-000000000001/transitions",
            json={},
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "access_denied"
        operation = client.get("/openapi.json").json()["paths"][
            "/api/v1/investigations/{investigation_id}/transitions"
        ]["post"]
        assert "review" in operation["x-required-permissions"]


@pytest.mark.integration
def test_memory_review_uses_graph_authorized_curator(live_database, isolated_graph_settings):
    setup = setup_memory_demo(live_database)
    resource = next(n for n in setup["sources"] if n.kind == NodeKind.RESOURCE)
    evidence = next(n for n in setup["sources"] if n.kind == NodeKind.EVIDENCE)
    provenance = Provenance(
        source="shadowchain.audit",
        source_id="review-memory",
        observed_at=hour(10),
        collector="pytest",
        source_uri="urn:shadowchain:synthetic:audit:review",
    )
    with TestClient(create_app(isolated_graph_settings), headers=TEST_HEADERS) as client:
        response = client.post(
            "/api/v1/memory/write",
            json={
                "request_id": "audit/review-memory",
                "memory_key": "audit/review-memory",
                "memory_type": "episodic",
                "content": "SYNTHETIC investigation observation",
                "audience": "team",
                "subject_ids": [resource.id],
                "evidence_ids": [evidence.id],
                "provenance": provenance.model_dump(mode="json"),
            },
        )
        assert response.status_code == 200, response.text
        memory_id = response.json()["id"]
        reviewed = client.post(
            "/api/v1/memory/review",
            json={
                "request_id": "audit/curator-review",
                "memory_id": memory_id,
                "expected_version": 1,
                "verification_status": "verified",
                "reason": "Reviewed the synthetic source evidence",
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["verification_status"] == "verified"
        assert reviewed.json()["executable"] is False
    rows = live_database.query(
        "MATCH (m:AgentMemory {tenant_id:$tenant,id:$id}) RETURN m.reviewed_by",
        {"tenant": DEMO_TENANT, "id": memory_id},
        read_only=True,
    )
    assert rows == [[DEMO_REVIEWER_ID]]


@pytest.mark.integration
def test_edge_diff_matches_snapshots_when_endpoint_expires(live_database):
    scope = TenantScope(tenant_id="audit-temporal-diff")
    repository = GraphRepository(live_database, scope)

    def node(key, valid_to=None):
        return repository.upsert_node(
            NodeInput(
                kind=NodeKind.INVESTIGATION,
                provenance=Provenance(
                    source="shadowchain.audit",
                    source_id=key,
                    observed_at=hour(9),
                    collector="pytest",
                    source_uri="urn:shadowchain:synthetic:audit:" + key,
                ),
                attributes={
                    "title": "SYNTHETIC " + key,
                    "valid_from": epoch_us(hour(9)),
                    **({"valid_to": epoch_us(valid_to)} if valid_to else {}),
                },
            )
        )

    source, target = node("expired-endpoint", hour(11)), node("retained-endpoint")
    edge = repository.create_relationship(
        RelationshipInput(
            kind=RelationshipKind.BELONGS_TO,
            source=source.ref,
            target=target.ref,
            provenance=source.provenance,
            valid_from=hour(9),
            verification_status="verified",
        )
    )
    view = VisualizationRepository(live_database, scope.tenant_id)
    before = view.snapshot(GraphQuery(at=hour(10), collection="edges"))
    after = view.snapshot(GraphQuery(at=hour(12), collection="edges"))
    difference = view.difference(DiffQuery(before=hour(10), after=hour(12), collection="edges"))
    assert [e.id for e in before.edges] == [edge.id]
    assert after.edges == []
    assert [e.id for e in difference.removed_edges] == [edge.id]
    assert difference.added_edges == []


@pytest.mark.integration
def test_rapid_websocket_pings_do_not_accelerate_graph_queries(isolated_graph_settings):
    settings = configuration(isolated_graph_settings, ws_poll_seconds=0.1)
    observed = []
    original = LiveRepository.page

    def page(*args, **kwargs):
        observed.append(time.monotonic())
        return original(*args, **kwargs)

    with (
        TestClient(create_app(settings)) as client,
        patch.object(LiveRepository, "page", page),
        client.websocket_connect("/api/v1/ws/live") as websocket,
    ):
        websocket.send_json({"type": "authenticate", "token": TEST_TOKEN})
        assert websocket.receive_json()["type"] == "hello"
        for _ in range(5):
            websocket.send_json({"type": "ping"})
            assert websocket.receive_json()["type"] == "pong"
    assert len(observed) >= 5
    assert all(right - left >= 0.07 for left, right in zip(observed, observed[1:], strict=False))

import pytest
from fastapi.testclient import TestClient

from app.api.v1.memory import DEMO_AGENT_ID
from app.core.time import utc_now
from app.db.falkordb import GraphDatabase
from app.demo.memory_proof import run_proof
from app.demo.memory_setup import setup_memory_demo
from app.main import create_app
from app.models.graph import NodeKind, Provenance
from tests.api_helpers import TEST_HEADERS

pytestmark = pytest.mark.integration


def test_memory_proof_is_repeatable_without_losing_audit_history(live_database):
    proof = run_proof(live_database)
    assert proof["verified"] and proof["verified_semantic_count"] == 2
    assert run_proof(live_database) == proof


def test_memories_survive_complete_backend_lifecycle(isolated_graph_settings):
    bootstrap = GraphDatabase(isolated_graph_settings)
    try:
        setup = setup_memory_demo(bootstrap)
    finally:
        bootstrap.close()
    resource = next(n for n in setup["sources"] if n.kind == NodeKind.RESOURCE)
    evidence = next(n for n in setup["sources"] if n.kind == NodeKind.EVIDENCE)
    payload = {
        "request_id": "api/episode",
        "memory_key": "api/episode",
        "memory_type": "episodic",
        "content": "SYNTHETIC incident retained across backend restart",
        "audience": "team",
        "subject_ids": [resource.id],
        "evidence_ids": [evidence.id],
        "provenance": Provenance(
            source="synthetic.api",
            source_id="incident",
            observed_at=utc_now(),
            collector="pytest",
            source_uri="urn:synthetic:incident",
        ).model_dump(mode="json"),
    }
    with TestClient(create_app(isolated_graph_settings), headers=TEST_HEADERS) as first:
        write = first.post("/api/v1/memory/write", json=payload)
        assert write.status_code == 200, write.text
        stored = write.json()
        context_before = first.post("/api/v1/memory/context", json={}).json()
        assert context_before["agent_id"] == DEMO_AGENT_ID
        assert len(context_before["observations"]) == 1
    # A new application, connection pool, service and Python object graph; no shared cache.
    with TestClient(create_app(isolated_graph_settings), headers=TEST_HEADERS) as second:
        context = second.post(
            "/api/v1/memory/context", json={}, headers={"X-Agent-ID": setup["verifier"]}
        )
        assert context.status_code == 200 and context.json() == context_before
        assert context.json()["agent_id"] == DEMO_AGENT_ID
        read = second.post("/api/v1/memory/retrieve", json={"subject_ids": [resource.id]})
        assert read.status_code == 200 and read.json()[0] == stored
        handoff = second.post(
            "/api/v1/memory/handoffs",
            json={
                "request_id": "api/handoff",
                "recipient_agent_id": setup["verifier"],
                "subject_ids": [resource.id],
            },
        )
        assert handoff.status_code == 200, handoff.text
        retrieved = second.get("/api/v1/memory/handoffs/" + handoff.json()["handoff_id"])
        assert retrieved.status_code == 200 and retrieved.json() == handoff.json()

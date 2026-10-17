from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.errors import DatabaseUnavailable
from app.core.time import utc_now
from app.db.falkordb import GraphDatabase
from app.main import create_app
from app.models.graph import Provenance, TenantScope
from app.models.memory import AgentContext, MemoryQuery, MemoryWrite, ProcedurePlan
from app.repositories.memory import MemoryRepository
from app.services.memory import MemoryService
from tests.api_helpers import TEST_HEADERS


def valid_write():
    return {
        "request_id": "synthetic/test",
        "memory_key": "test",
        "memory_type": "episodic",
        "content": "Synthetic data",
        "evidence_ids": ["00000000-0000-0000-0000-000000000001"],
        "provenance": Provenance(
            source="synthetic.test",
            source_id="event",
            observed_at=utc_now(),
            collector="pytest",
            source_uri="urn:synthetic:test",
        ).model_dump(),
    }


@pytest.mark.parametrize(
    "extra",
    [
        {"verification_status": "verified"},
        {"author_agent_id": "curator"},
        {"tenant_id": "victim"},
        {"source_trust": "system"},
        {"executable": True},
        {"confidence": float("nan")},
        {"evidence_ids": []},
        {"content": "AKIA" + "A" * 16},
        {"content": "x" * 6001},
        {"memory_type": "procedural"},
    ],
)
def test_memory_requests_cannot_supply_privilege_or_invalid_evidence(extra):
    with pytest.raises(ValidationError):
        MemoryWrite.model_validate(valid_write() | extra)


@pytest.mark.parametrize(
    "step", ["delete_bucket", "grant_admin", "exec", "shell", "os.system('whoami')"]
)
def test_procedure_steps_cannot_encode_privileged_actions(step):
    with pytest.raises(ValidationError):
        ProcedurePlan(steps=[step], outcome="successful")


@pytest.mark.parametrize(
    "values", [{"max_hops": 3}, {"max_hops": True}, {"limit": 101}, {"max_hops": "2]->() DELETE n"}]
)
def test_memory_queries_are_bounded_and_closed(values):
    with pytest.raises(ValidationError):
        MemoryQuery.model_validate(values)


def test_agent_context_cannot_contain_privileged_actions():
    with pytest.raises(ValidationError):
        AgentContext(
            agent_id="a",
            space_id="s",
            observations=[],
            established_facts=[],
            validated_procedures=[],
            read_only_capabilities=[],
            privileged_actions=["grant_admin"],
        )


def test_memory_database_outage_fails_clearly(unit_settings):
    database = GraphDatabase(unit_settings.model_copy(update={"falkordb_port": 1}))
    try:
        with pytest.raises(DatabaseUnavailable):
            MemoryService(
                MemoryRepository(database, TenantScope(tenant_id="t"), "space", "agent")
            ).retrieve_related_memories()
    finally:
        database.close()


@pytest.mark.parametrize(
    "overrides",
    [{"demo_mode": False}, {"app_env": "production", "falkordb_password": "synthetic-password"}],
)
def test_memory_routes_disabled_outside_local_demo(unit_settings, graph_stub: MagicMock, overrides):
    with TestClient(
        create_app(unit_settings.model_copy(update=overrides)), headers=TEST_HEADERS
    ) as client:
        assert client.post("/api/v1/memory/context", json={}).status_code == 404


def test_http_requests_cannot_select_an_agent_or_tenant(client):
    for field in ("agent_id", "tenant_id", "space_id"):
        response = client.post("/api/v1/memory/context", json={field: "other"})
        assert response.status_code == 422

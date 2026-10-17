import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import ApiClient
from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.db.falkordb import GraphDatabase
from app.demo.dataset import hour
from app.main import create_app
from app.models.events import DEMO_TENANT
from app.schemas.contract import GraphQuery, WindowQuery
from tests.api_helpers import TEST_CLIENT, TEST_HEADERS
from tests.workflow_helpers import workflow_world


def configuration(settings, tenant=DEMO_TENANT, permissions=None, **values):
    entry = ApiClient.model_validate(
        {
            **TEST_CLIENT,
            "tenant_id": tenant,
            "permissions": permissions or ["read", "write", "review", "aws:sync"],
        }
    )
    return settings.model_copy(update={"api_clients": [entry], **values})


def test_canonical_query_times_are_normalized_to_utc():
    assert GraphQuery(at="2026-10-16T17:30:00+05:30").at.isoformat() == "2026-10-16T12:00:00+00:00"
    window = WindowQuery(before="2026-10-16T14:30:00+05:30", after="2026-10-16T17:30:00+05:30")
    assert window.before.isoformat() == "2026-10-16T09:00:00+00:00"


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/demo/seed"),
        ("get", "/graph/snapshot"),
        ("post", "/agents/run"),
        ("post", "/aws/sync"),
        ("get", "/aws/status"),
        ("get", "/investigations"),
    ],
)
def test_all_data_and_mutation_routes_require_auth(unit_settings, graph_stub, method, path):
    with TestClient(create_app(unit_settings)) as client:
        response = getattr(client, method)("/api/v1" + path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_required"
        assert response.headers["www-authenticate"] == "Bearer"
        assert client.get("/api/v1/health").status_code == 200


def test_empty_auth_configuration_fails_closed(graph_stub):
    with TestClient(
        create_app(Settings(_env_file=None, workflow_recovery_enabled=False))
    ) as client:
        assert client.get("/api/v1/agents", headers=TEST_HEADERS).status_code == 401


def test_reader_cannot_mutate_or_review(unit_settings, graph_stub):
    with TestClient(
        create_app(configuration(unit_settings, permissions=["read"])), headers=TEST_HEADERS
    ) as client:
        for path in ["/demo/seed", "/agents/run", "/memory/review", "/reports", "/aws/sync"]:
            assert client.post("/api/v1" + path, json={}).status_code == 403
        assert client.get("/api/v1/aws/status").status_code == 200


def test_write_scope_does_not_grant_review_or_aws_permission(unit_settings, graph_stub):
    with TestClient(
        create_app(configuration(unit_settings, permissions=["read", "write"])),
        headers=TEST_HEADERS,
    ) as client:
        assert client.post("/api/v1/memory/review", json={}).status_code == 403
        assert client.post("/api/v1/aws/sync", json={}).status_code == 403


def test_request_bytes_are_bounded_without_echo(unit_settings, graph_stub):
    config = configuration(unit_settings, api_request_max_bytes=4096)
    with TestClient(create_app(config), headers=TEST_HEADERS) as client:
        response = client.post(
            "/api/v1/events/ingest",
            content=b"PRIVATE" * 1000,
            headers={"Origin": "http://localhost:3000"},
        )
        assert response.status_code == 413 and "PRIVATE" not in response.text
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert "Retry-After" in response.headers["access-control-expose-headers"]


def test_websocket_log_transport_never_reflects_frame_or_url_secret():
    for level, message in [
        (logging.INFO, "WebSocket /api/v1/ws/live?token=PRIVATE"),
        (logging.DEBUG, '< TEXT {"token":"PRIVATE"}'),
    ]:
        record = logging.LogRecord("uvicorn.error", level, "", 0, message, (), None)
        assert "PRIVATE" not in JsonFormatter().format(record)


def test_openapi_has_canonical_routes_security_models_and_uniform_errors(client):
    document = client.get("/openapi.json").json()
    paths = {
        "/dashboard/summary": "get",
        "/graph/snapshot": "get",
        "/graph/diff": "get",
        "/graph/path": "get",
        "/graph/timeline": "get",
        "/demo/reset": "post",
        "/demo/replay": "post",
        "/events/ingest": "post",
        "/risks": "get",
        "/risks/{risk_id}": "get",
        "/agents/run": "post",
        "/agents/runs": "get",
        "/agents/memory": "get",
        "/aws/sync": "post",
    }
    for path, method in paths.items():
        operation = document["paths"]["/api/v1" + path][method]
        assert operation["security"] == [{"ScopedBearer": []}]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]
        for code in ["401", "403", "409", "422", "429", "503"]:
            schema = operation["responses"][code]["content"]["application/json"]["schema"]
            assert schema["$ref"].endswith("/ErrorResponse")
    assert "security" not in document["paths"]["/api/v1/health"]["get"]


@pytest.mark.integration
def test_complete_frontend_contract_and_read_only_simulation(isolated_graph_settings):
    config = configuration(isolated_graph_settings, api_query_limit_per_minute=1000)
    database = GraphDatabase(config)
    try:
        database.initialize_schema()
        _, request = workflow_world(database, config)
    finally:
        database.close()
    with TestClient(create_app(config), headers=TEST_HEADERS) as client:
        with patch("boto3.Session") as aws:
            result = client.post("/api/v1/agents/run", json=request.model_dump(mode="json"))
            assert result.status_code == 200, result.text
            workflow = result.json()
            assert workflow["status"] == "completed"
            summary = client.get("/api/v1/dashboard/summary")
            assert summary.status_code == 200, summary.text
            assert summary.json()["entity_counts"]["Identity"] == 12
            query = {
                "at": hour(12).isoformat(),
                "scenario": "A",
                "collection": "edges",
                "limit": 100,
            }
            graph = client.get("/api/v1/graph/snapshot", params=query)
            assert graph.status_code == 200, graph.text
            assert graph.json()["edges"] and graph.json()["snapshot_at"].startswith("2026-10-16T12")
            for edge in graph.json()["edges"]:
                assert edge["source"] and edge["target"] and edge["valid_from"]
                assert "evidence_ids" in edge and "investigation_ids" in edge
            diff = client.get(
                "/api/v1/graph/diff",
                params={
                    "before": hour(9).isoformat(),
                    "after": hour(12).isoformat(),
                    "scenario": "A",
                },
            )
            assert diff.status_code == 200, diff.text
            assert diff.json()["added_edges"]
            timeline = client.get(
                "/api/v1/graph/timeline",
                params={"before": hour(9).isoformat(), "after": hour(13).isoformat()},
            )
            assert timeline.status_code == 200, timeline.text
            assert len(timeline.json()["events"]) >= 4
            risks = client.get("/api/v1/risks").json()["risks"]
            assert risks
            risk_id = risks[0]["risk_id"]
            risk = client.get("/api/v1/risks/" + risk_id)
            assert risk.status_code == 200, risk.text
            path = client.get("/api/v1/graph/path", params={"risk_id": risk_id})
            assert path.status_code == 200, path.text
            assert path.json()["nodes"] and path.json()["edges"]
            assert all(n["evidence_ids"] and n["risk_severity"] for n in path.json()["nodes"])
            simulation = client.post(
                "/api/v1/what-if/remove-grant",
                json={
                    "at": hour(12).isoformat(),
                    "remove_relationship_ids": [risk.json()["risk"]["relationship_ids"][0]],
                },
            )
            assert simulation.status_code == 200, simulation.text
            assert (
                simulation.json()["graph_mutated"] is False
                and not simulation.json()["after"]["findings"]
            )
            assert client.get("/api/v1/graph/snapshot", params=query).json() == graph.json()
            runs = client.get("/api/v1/agents/runs", params={"investigation_id": workflow["id"]})
            assert runs.status_code == 200, runs.text
            assert len(runs.json()["runs"]) == 6
            assert all(r["memory_written_ids"] and r["handoff_ids"] for r in runs.json()["runs"])
            oracle = next(r for r in runs.json()["runs"] if r["run"]["stage"] == "risk_oracle")
            assert oracle["evidence_produced_ids"]
            memories = client.get(
                "/api/v1/agents/memory", params={"investigation_id": workflow["id"]}
            )
            assert memories.status_code == 200 and len(memories.json()["memories"]) == 6
            cases = client.get("/api/v1/investigations").json()["investigations"]
            assert cases
            resumed = client.post(
                "/api/v1/investigations/" + cases[0]["investigation_id"] + "/resume"
            )
            assert resumed.status_code == 200, resumed.text
            aws.assert_not_called()


@pytest.mark.integration
def test_keyset_pagination_staleness_and_tenant_isolation(isolated_graph_settings):
    config = configuration(isolated_graph_settings, api_query_limit_per_minute=1000)
    with TestClient(create_app(config), headers=TEST_HEADERS) as client:
        assert client.post("/api/v1/demo/seed").status_code == 200
        first = client.get("/api/v1/graph/snapshot", params={"limit": 5}).json()
        params = {
            "limit": 5,
            "at": first["snapshot_at"],
            "cursor": first["next_cursor"],
            "snapshot_sequence": first["snapshot_sequence"],
        }
        second = client.get("/api/v1/graph/snapshot", params=params)
        assert second.status_code == 200, second.text
        assert not {n["id"] for n in first["nodes"]} & {n["id"] for n in second.json()["nodes"]}
        assert (
            client.post(
                "/api/v1/demo/replay", json={"scenario": "A", "expected_generation": 0}
            ).status_code
            == 200
        )
        assert client.get("/api/v1/graph/snapshot", params=params).status_code == 409
        assert (
            client.post(
                "/api/v1/demo/reset", json={"scenario": "A", "expected_generation": 0}
            ).status_code
            == 200
        )
    with TestClient(
        create_app(configuration(config, tenant="unrelated")), headers=TEST_HEADERS
    ) as outsider:
        assert outsider.get("/api/v1/graph/snapshot").json()["nodes"] == []
        assert outsider.get("/api/v1/live/events").json()["events"] == []
        assert outsider.get("/api/v1/agents").status_code == 403
        assert outsider.post("/api/v1/demo/seed").status_code == 403


@pytest.mark.integration
def test_shared_rate_budget_survives_reconnect(isolated_graph_settings):
    config = configuration(isolated_graph_settings, api_query_limit_per_minute=1)
    with TestClient(create_app(config), headers=TEST_HEADERS) as client:
        assert client.get("/api/v1/graph/snapshot").status_code == 200
    with TestClient(create_app(config), headers=TEST_HEADERS) as client:
        denied = client.get("/api/v1/graph/snapshot")
        assert denied.status_code == 429 and denied.headers["retry-after"] == "60"
        assert client.get("/api/v1/health").status_code == 200


@pytest.mark.integration
def test_unified_aws_sync_stays_disabled_without_sdk(isolated_graph_settings):
    config = configuration(isolated_graph_settings, tenant="shadowchain-aws-import")
    with (
        TestClient(create_app(config), headers=TEST_HEADERS) as client,
        patch("boto3.Session") as aws,
    ):
        response = client.post(
            "/api/v1/aws/sync",
            json={"kind": "iam", "request_id": "off", "confirm_account_id": "123456789012"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "aws_integration_disabled"
        aws.assert_not_called()

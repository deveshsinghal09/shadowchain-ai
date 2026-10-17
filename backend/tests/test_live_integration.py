import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.db.errors import GraphConflict
from app.main import create_app
from app.models.events import DEMO_TENANT
from app.repositories.live import LiveRepository, journal_query
from tests.api_helpers import TEST_HEADERS, TEST_TOKEN
from tests.test_api_contract import configuration


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "authenticate", "token": "invalid"},
        {"type": "authenticate", "token": TEST_TOKEN, "tenant_id": "victim"},
        {"type": "authenticate", "token": TEST_TOKEN, "after": -1},
        {"type": "authenticate", "token": TEST_TOKEN, "limit": 10000},
    ],
)
def test_websocket_rejects_invalid_authentication_and_scope_override(
    unit_settings, graph_stub, frame
):
    with (
        TestClient(create_app(unit_settings)) as client,
        client.websocket_connect("/api/v1/ws/live") as ws,
    ):
        ws.send_json(frame)
        error = ws.receive_json()
        assert error["type"] == "error" and "request_id" in error and "timestamp" in error
        assert frame["token"] not in json.dumps(error)
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


@pytest.mark.parametrize(
    "path,headers",
    [
        ("/api/v1/ws/live?token=PRIVATE", {}),
        ("/api/v1/ws/live", {"origin": "https://untrusted.invalid"}),
    ],
)
def test_ws_url_credentials_and_unapproved_origins_are_rejected(
    unit_settings, graph_stub, path, headers
):
    with (
        TestClient(create_app(unit_settings)) as client,
        client.websocket_connect(path, headers=headers) as ws,
    ):
        assert ws.receive_json()["error"]["code"] == "access_denied"


@pytest.mark.integration
def test_websocket_persisted_order_replay_and_process_restart(isolated_graph_settings):
    config = configuration(isolated_graph_settings, ws_poll_seconds=0.05)
    with TestClient(create_app(config), headers=TEST_HEADERS) as client:
        assert client.post("/api/v1/demo/seed").status_code == 200
        with client.websocket_connect("/api/v1/ws/live") as ws:
            ws.send_json({"type": "authenticate", "token": TEST_TOKEN, "after": 0, "limit": 1})
            hello = ws.receive_json()
            assert hello["high_watermark"] == 1
            initial = ws.receive_json()
            assert initial["events"][0]["type"] == "demo.seeded"
            assert (
                client.post(
                    "/api/v1/demo/replay", json={"scenario": "A", "expected_generation": 0}
                ).status_code
                == 200
            )
            observed = []
            while len(observed) < 3:
                page = ws.receive_json()
                assert len(json.dumps(page).encode()) <= 65536
                if page["type"] == "events":
                    observed.extend(page["events"])
            assert [e["sequence"] for e in observed] == [2, 3, 4]
            assert all(e["type"] == "security.event.ingested" for e in observed)
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"
        assert (
            client.post(
                "/api/v1/demo/replay", json={"scenario": "A", "expected_generation": 0}
            ).status_code
            == 200
        )
        assert client.get("/api/v1/live/events", params={"after": 4}).json()["events"] == []
    # New application, pool and socket: nothing is replayed from process memory.
    with TestClient(create_app(config), headers=TEST_HEADERS) as fresh:
        assert fresh.get("/api/v1/live/events", params={"after": 1}).json()["events"] == observed
        with fresh.websocket_connect("/api/v1/ws/live") as ws:
            ws.send_json({"type": "authenticate", "token": TEST_TOKEN, "after": 1})
            assert ws.receive_json()["high_watermark"] == 4
            assert ws.receive_json()["events"] == observed
    with TestClient(create_app(configuration(config, tenant="unrelated"))) as other:
        with other.websocket_connect("/api/v1/ws/live") as ws:
            ws.send_json({"type": "authenticate", "token": TEST_TOKEN})
            assert ws.receive_json()["high_watermark"] == 0
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"


@pytest.mark.integration
def test_invalid_future_cursor_is_explicit(isolated_graph_settings):
    with TestClient(create_app(isolated_graph_settings), headers=TEST_HEADERS) as client:
        assert client.get("/api/v1/live/events", params={"after": 999}).status_code == 409
        with client.websocket_connect("/api/v1/ws/live") as ws:
            ws.send_json({"type": "authenticate", "token": TEST_TOKEN, "after": 999})
            assert ws.receive_json()["error"]["code"] == "cursor_conflict"
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 4409


@pytest.mark.integration
def test_journal_atomic_rollback_and_retried_statement(live_database):
    db = live_database
    repo = LiveRepository(db, DEMO_TENANT)
    repo.ensure()
    identifier = str(uuid4())
    query, params = journal_query(
        "CREATE (n:JournalSubject {id:$id,tenant_id:$tenant}) RETURN n.id",
        {"id": identifier, "tenant": DEMO_TENANT},
        DEMO_TENANT,
        "test.committed",
        [identifier],
    )
    assert db.query(query, params, idempotent=True) == [[identifier]]
    # Same operation/statement after a lost acknowledgment is safely ignored.
    assert db.query(query, params, idempotent=True) == []
    assert repo.page().high_watermark == 1
    failed = str(uuid4())
    with pytest.raises(GraphConflict):
        db.mutate(
            "CREATE (:JournalSubject {id:$id,tenant_id:$tenant}) "
            "CREATE (:Entity {id:$bad}) RETURN 1",
            {"id": failed, "tenant": DEMO_TENANT, "bad": str(uuid4())},
            tenant_id=DEMO_TENANT,
            event_type="test.failed",
            entity_ids=[failed],
        )
    assert (
        db.query("MATCH (n:JournalSubject {id:$id}) RETURN n.id", {"id": failed}, read_only=True)
        == []
    )
    assert repo.page().high_watermark == 1


@pytest.mark.integration
def test_concurrent_events_have_contiguous_tenant_sequences_and_bounded_frames(live_database):
    db = live_database

    def commit(index):
        tenant = "tenant-a" if index % 2 == 0 else "tenant-b"
        identifier = str(uuid4())
        return db.mutate(
            "CREATE (n:JournalSubject {id:$id,tenant_id:$tenant}) RETURN n.id",
            {"id": identifier, "tenant": tenant},
            tenant_id=tenant,
            event_type="test.concurrent",
            entity_ids=[str(uuid4()) for _ in range(64)],
        )

    # Initialize streams before competing writes. Creation races use database uniqueness.
    for tenant in ["tenant-a", "tenant-b"]:
        LiveRepository(db, tenant).ensure()
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(commit, range(12)))
    assert all(results)
    for tenant in ["tenant-a", "tenant-b"]:
        page = LiveRepository(db, tenant).page(0, 100)
        assert [e.sequence for e in page.events] == list(range(1, 7))
        assert len(page.model_dump_json().encode()) < 65536
    assert LiveRepository(db, "unrelated").page().events == []


@pytest.mark.integration
def test_stream_and_core_reads_fail_closed_during_database_outage(isolated_graph_settings):
    config = configuration(isolated_graph_settings)
    with TestClient(create_app(config), headers=TEST_HEADERS) as client:
        from app.core.errors import DatabaseUnavailable

        with pytest.MonkeyPatch.context() as monkey:

            def fail(*_args, **_kwargs):
                raise DatabaseUnavailable()

            monkey.setattr(client.app.state.database, "query", fail)
            assert client.get("/api/v1/dashboard/summary").status_code == 503
            with client.websocket_connect("/api/v1/ws/live") as ws:
                ws.send_json({"type": "authenticate", "token": TEST_TOKEN})
                assert ws.receive_json()["error"]["code"] == "dependency_unavailable"
                with pytest.raises(WebSocketDisconnect) as closed:
                    ws.receive_json()
                assert closed.value.code == 1013

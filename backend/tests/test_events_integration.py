from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.config import Settings
from app.db.errors import GraphConflict, GraphQueryError
from app.db.falkordb import GraphDatabase
from app.demo.dataset import dataset_definition, hour
from app.models.events import EdgeSpec, NodeSpec, Scenario, SyntheticSecurityEvent
from app.models.graph import NodeKind, RelationshipKind, TenantScope
from app.repositories.demo import DemoRepository
from app.services.event_errors import (
    DemoGenerationConflict,
    DemoMutationError,
    DemoNotSeeded,
    DemoVersionConflict,
    EventOrderError,
    LateEventError,
)
from app.services.events import EventIngestionService

pytestmark = pytest.mark.integration


@pytest.fixture
def engine(live_database: GraphDatabase) -> EventIngestionService:
    service = EventIngestionService(
        DemoRepository(live_database, TenantScope(tenant_id="demo-tests"))
    )
    service.seed()
    return service


def test_shared_inventory_is_exact_and_idempotent(engine: EventIngestionService) -> None:
    assert engine.seed()["created"] is False
    inventory = engine.status(Scenario.A).inventory
    for label, count in {
        "CloudOrganization": 1,
        "CloudAccount": 2,
        "Identity": 12,
        "Role": 10,
        "Policy": 15,
        "Resource": 20,
    }.items():
        assert inventory[label] == count
    snapshot = engine.snapshot(Scenario.A, hour(9))
    assert all(
        n["synthetic"] and n["dataset_version"] == "synthetic-2026.1" for n in snapshot.nodes
    )
    buckets = [n for n in snapshot.nodes if n.get("resource_type") == "s3:bucket"]
    assert len(buckets) == 8
    assert sum(n["sensitive"] for n in buckets) == 4
    assert sum(n.get("service_principal") == "ec2.amazonaws.com" for n in snapshot.nodes) == 3
    assert inventory["Investigation"] == 3
    assert all(
        not r["valid_from"] > int(hour(9).timestamp() * 1_000_000) for r in snapshot.relationships
    )


def test_scenario_a_changes_at_four_timestamps_and_denies_override_allow(
    engine: EventIngestionService,
) -> None:
    observed = []
    for stamp in (9, 10, 11, 12):
        engine.replay(Scenario.A, 0, hour(stamp))
        snapshot = engine.snapshot(Scenario.A, hour(stamp))
        observed.append(
            (
                len(snapshot.reachable_roles["identity/alice"]),
                len(snapshot.potential_sensitive_paths),
                snapshot.semantic_sha256,
            )
        )
    assert [(roles, paths) for roles, paths, _ in observed] == [(0, 0), (1, 0), (2, 0), (2, 1)]
    assert len({digest for _, _, digest in observed}) == 4
    snapshot = engine.snapshot(Scenario.A, hour(12))
    path = snapshot.potential_sensitive_paths[0]
    assert path.principal_chain == ["identity/alice", "role/temp-analyst", "role/ec2-backup"]
    assert path.resource_key == "resource/hr-data"
    # The same Allow statement targets finance-data, but its explicit Deny still wins.
    assert all(
        p.resource_key != "resource/finance-data" for p in snapshot.potential_sensitive_paths
    )
    comparison = engine.compare(Scenario.A, hour(9), hour(12))
    assert len(comparison.relationships_added) == 7
    assert len(comparison.potential_paths_added) == 1


def test_scenario_b_resolved_justification_does_not_revoke_actual_grant(
    engine: EventIngestionService,
) -> None:
    engine.replay(Scenario.B, 0, hour(10))
    assert engine.snapshot(Scenario.B, hour(10)).stale_justifications == []
    engine.replay(Scenario.B, 0)
    snapshot = engine.snapshot(Scenario.B, hour(11))
    assert len(snapshot.stale_justifications) == 1
    stale = snapshot.stale_justifications[0]
    assert stale["grant_still_active"] and stale["business_window_expired"]
    assert stale["justification_status"] == "resolved"
    edge = next(e for e in snapshot.relationships if e["edge_key"] == "b/temporary-grant")
    assert "valid_to" not in edge
    assert any(p.identity_key == "identity/bob" for p in snapshot.potential_sensitive_paths)
    states = [n for n in snapshot.nodes if n["logical_key"] == "b/incident"]
    assert len(states) == 2
    assert next(n for n in states if n["is_current"])["status"] == "resolved"


def test_scenario_c_preserves_both_memories_evidence_and_conflict_review(
    engine: EventIngestionService,
) -> None:
    engine.replay(Scenario.C, 0, hour(10))
    initial = engine.snapshot(Scenario.C, hour(10))
    assert initial.counts["AgentMemory"] == 1 and not initial.memory_conflicts
    engine.replay(Scenario.C, 0)
    snapshot = engine.snapshot(Scenario.C, hour(11))
    assert snapshot.counts["AgentMemory"] == 2
    assert len(snapshot.memory_conflicts) == 3
    memories = [n for n in snapshot.nodes if n["kind"] == "AgentMemory"]
    assert all(n["is_current"] and n["source_event_id"] for n in memories)
    current_review = next(
        n for n in snapshot.nodes if n["logical_key"] == "c/investigation" and n["is_current"]
    )
    assert current_review["status"] == "needs_investigation"
    assert any(n.get("status") == "disputed" and n["is_current"] for n in snapshot.nodes)
    assert len([e for e in snapshot.relationships if e["kind"] == "REMEMBERS"]) == 2


@pytest.mark.parametrize("scenario", list(Scenario))
def test_reset_and_replay_reproduce_logical_graph(
    engine: EventIngestionService, scenario: Scenario
) -> None:
    baseline = engine.snapshot(scenario, hour(9)).semantic_sha256
    engine.replay(scenario, 0)
    before = engine.snapshot(scenario, hour(12))
    status = engine.reset(scenario, 0)
    assert status.generation == 1 and status.sequence == 0
    assert engine.snapshot(scenario, hour(9)).semantic_sha256 == baseline
    engine.replay(scenario, status.generation)
    after = engine.snapshot(scenario, hour(12))
    assert before.semantic_sha256 == after.semantic_sha256
    assert [n["id"] for n in before.nodes] == [n["id"] for n in after.nodes]


def test_reset_never_erases_other_scenarios(engine: EventIngestionService) -> None:
    for scenario in Scenario:
        engine.replay(scenario, 0)
    before = engine.snapshot(Scenario.B, hour(12)).semantic_sha256
    engine.reset(Scenario.A, 0)
    assert engine.snapshot(Scenario.B, hour(12)).semantic_sha256 == before
    assert engine.snapshot(Scenario.C, hour(12)).counts["AgentMemory"] == 2
    with pytest.raises(DemoGenerationConflict):
        engine.replay(Scenario.A, 0)


def test_out_of_order_and_late_events_are_rejected_without_side_effects(
    engine: EventIngestionService,
) -> None:
    events = dataset_definition().events[Scenario.A]
    with pytest.raises(EventOrderError):
        engine.ingest(events[1], 0)
    assert engine.status(Scenario.A).sequence == 0
    first = engine.ingest(events[0], 0)
    late = SyntheticSecurityEvent.model_validate(
        {
            **events[1].model_dump(),
            "event_id": None,
            "source_id": "a/late-observation",
            "occurred_at": hour(9),
        }
    )
    with pytest.raises(LateEventError):
        engine.ingest(late, 0)
    assert engine.status(Scenario.A).sequence == 1
    replay = engine.ingest(events[0], 0)
    assert replay.duplicate and replay.ingested_at == first.ingested_at


def test_duplicate_id_with_different_content_is_rejected(engine: EventIngestionService) -> None:
    event = dataset_definition().events[Scenario.A][0]
    engine.ingest(event, 0)
    changed = SyntheticSecurityEvent.model_validate(
        {**event.model_dump(), "event_type": "Conflicting"}
    )
    with pytest.raises(GraphConflict):
        engine.ingest(changed, 0)
    assert engine.status(Scenario.A).sequence == 1


def test_concurrent_duplicate_ingestion_advances_cursor_once(engine: EventIngestionService) -> None:
    event = dataset_definition().events[Scenario.A][0]
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: engine.ingest(event, 0), range(16)))
    assert len({r.graph_id for r in receipts}) == 1
    assert sum(not r.duplicate for r in receipts) == 1
    assert engine.status(Scenario.A).sequence == 1
    assert (
        len([n for n in engine.snapshot(Scenario.A).nodes if n.get("event_id") == event.event_id])
        == 1
    )


def test_restart_recovery_uses_database_cursor(
    engine: EventIngestionService, isolated_graph_settings: Settings
) -> None:
    engine.replay(Scenario.A, 0, hour(11))
    replacement = GraphDatabase(isolated_graph_settings)
    try:
        restarted = EventIngestionService(
            DemoRepository(replacement, TenantScope(tenant_id="demo-tests"))
        )
        assert restarted.status(Scenario.A).sequence == 2
        result = restarted.replay(Scenario.A, 0)
        assert len(result.applied_event_ids) == 1 and len(result.duplicate_event_ids) == 2
        assert len(restarted.snapshot(Scenario.A).potential_sensitive_paths) == 1
    finally:
        replacement.close()


def test_invalid_related_change_is_rejected_as_a_whole(engine: EventIngestionService) -> None:
    original = dataset_definition().events[Scenario.A][0]
    values = original.model_dump()
    values["nodes"] = [
        NodeSpec(
            key="a/decision", kind=NodeKind.DECISION, attributes={"action": "synthetic-observation"}
        ).model_dump()
    ]
    values["edges"] = [
        *values["edges"],
        EdgeSpec(
            key="a/invalid",
            kind=RelationshipKind.CAN_ASSUME,
            source_key="identity/alice",
            target_key="role/does-not-exist",
        ).model_dump(),
    ]
    with pytest.raises(DemoMutationError):
        engine.ingest(SyntheticSecurityEvent.model_validate(values), 0)
    assert engine.status(Scenario.A).sequence == 0
    assert not any(
        n.get("entity_key") == "a/decision" or n.get("event_id") == original.event_id
        for n in engine.snapshot(Scenario.A).nodes
    )


def test_graph_failure_rolls_back_event_and_cursor(engine: EventIngestionService) -> None:
    # Inject a server-side failure AFTER mutation clauses, proving the real statement
    # rolls back its writes rather than merely validating an event in Python.
    repository = engine.repository
    original_query = repository.query

    def fail_after_mutations(cypher: str, parameters: dict, *, read_only: bool = False) -> list:
        if "s.last_event_id = $event_id" in cypher:
            cypher = cypher.rsplit("RETURN properties(", 1)[0] + "RETURN 1 / 0"
        return original_query(cypher, parameters, read_only=read_only)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(repository, "query", fail_after_mutations)
        with pytest.raises(GraphQueryError):
            engine.ingest(dataset_definition().events[Scenario.A][0], 0)
    assert engine.status(Scenario.A).sequence == 0
    assert not engine.snapshot(Scenario.A, hour(12)).reachable_roles["identity/alice"]


def test_tenant_and_dataset_version_guards(
    engine: EventIngestionService, live_database: GraphDatabase
) -> None:
    foreign = EventIngestionService(
        DemoRepository(live_database, TenantScope(tenant_id="unrelated-tenant"))
    )
    with pytest.raises(DemoNotSeeded):
        foreign.snapshot(Scenario.A)
    live_database.query(
        "MATCH (d:DemoDataset {tenant_id: $tenant}) SET d.manifest_sha256 = $digest",
        {"tenant": "demo-tests", "digest": "0" * 64},
        idempotent=True,
    )
    with pytest.raises(DemoVersionConflict):
        engine.replay(Scenario.A, 0)


def test_revocation_event_keeps_half_open_historical_relationship(
    engine: EventIngestionService,
) -> None:
    engine.replay(Scenario.A, 0)
    event = SyntheticSecurityEvent(
        scenario=Scenario.A,
        source_id="a/revoke-temporary-role",
        account_id="000000000001",
        sequence=4,
        occurred_at=hour(13),
        event_type="SyntheticTemporaryRoleAccessRevoked",
        close_edges=["a/alice-temp"],
    )
    engine.ingest(event, 0)
    assert len(engine.snapshot(Scenario.A, hour(12)).potential_sensitive_paths) == 1
    assert engine.snapshot(Scenario.A, hour(13)).potential_sensitive_paths == []
    _, _, all_edges = engine.repository.graph()
    closed = next(edge for edge in all_edges if edge["edge_key"] == "a/alice-temp")
    assert closed["valid_to"] == int(hour(13).timestamp() * 1_000_000)
    assert closed["closed_by_event_id"]


def test_reset_of_an_empty_scenario_preserves_baseline(engine: EventIngestionService) -> None:
    baseline = engine.snapshot(Scenario.A, hour(9)).semantic_sha256
    state = engine.reset(Scenario.A, 0)
    assert state.generation == 1 and state.sequence == 0
    assert engine.snapshot(Scenario.A, hour(9)).semantic_sha256 == baseline


def test_lost_write_ack_is_reconciled_from_committed_graph(engine: EventIngestionService) -> None:
    repository, event = engine.repository, dataset_definition().events[Scenario.A][0]
    original = repository.query

    def lose_ack(cypher: str, parameters: dict, *, read_only: bool = False) -> list:
        result = original(cypher, parameters, read_only=read_only)
        return [] if "s.last_event_id = $event_id" in cypher else result

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(repository, "query", lose_ack)
        receipt = engine.ingest(event, 0)
    assert receipt.duplicate
    assert engine.status(Scenario.A).sequence == 1


def test_concurrent_seed_creates_one_inventory(live_database: GraphDatabase) -> None:
    service = EventIngestionService(
        DemoRepository(live_database, TenantScope(tenant_id="seed-race"))
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.seed(), range(4)))
    assert sum(result["created"] for result in results) == 1
    assert service.status(Scenario.A).inventory["Identity"] == 12


def test_custom_event_cannot_reference_another_scenario_memory(
    engine: EventIngestionService,
) -> None:
    engine.replay(Scenario.C, 0, hour(10))
    event = SyntheticSecurityEvent(
        scenario=Scenario.B,
        source_id="b/invalid-cross-scenario",
        account_id="000000000002",
        sequence=1,
        occurred_at=hour(10),
        event_type="SyntheticCrossScenarioMutation",
        nodes=[
            NodeSpec(
                key="b/finding",
                kind=NodeKind.RISK_FINDING,
                attributes={"title": "SYNTHETIC finding"},
            )
        ],
        edges=[
            EdgeSpec(
                key="b/foreign-memory",
                kind=RelationshipKind.SUPPORTS,
                source_key="c/memory/scout",
                target_key="b/finding",
            )
        ],
    )
    with pytest.raises(DemoMutationError):
        engine.ingest(event, 0)
    assert engine.status(Scenario.B).sequence == 0
    assert engine.snapshot(Scenario.C).counts["AgentMemory"] == 1


def test_snapshot_digest_does_not_change_just_because_query_time_changes(
    engine: EventIngestionService,
) -> None:
    assert (
        engine.snapshot(Scenario.A, hour(9)).semantic_sha256
        == engine.snapshot(Scenario.A, hour(9).replace(minute=30)).semantic_sha256
    )

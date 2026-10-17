from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app.core.config import Settings
from app.db.cypher import creation_properties
from app.db.errors import GraphConflict, GraphNotFound, GraphSchemaError
from app.db.falkordb import GraphDatabase
from app.db.schema import SchemaManager
from app.models.graph import NodeKind, RelationshipInput, RelationshipKind, TenantScope
from app.repositories.graph import GraphRepository
from tests.graph_helpers import END, START, node_input, provenance

pytestmark = pytest.mark.integration

RELATIONSHIP_CASES = [
    (RelationshipKind.BELONGS_TO, NodeKind.IDENTITY, NodeKind.CLOUD_ACCOUNT),
    (RelationshipKind.CAN_ASSUME, NodeKind.IDENTITY, NodeKind.ROLE),
    (RelationshipKind.HAS_POLICY, NodeKind.ROLE, NodeKind.POLICY),
    (RelationshipKind.GRANTS, NodeKind.POLICY, NodeKind.PERMISSION),
    (RelationshipKind.TARGETS, NodeKind.PERMISSION, NodeKind.RESOURCE),
    (RelationshipKind.AFFECTS, NodeKind.SECURITY_EVENT, NodeKind.RESOURCE),
    (RelationshipKind.DERIVED_FROM, NodeKind.EVIDENCE, NodeKind.SECURITY_EVENT),
    (RelationshipKind.SUPPORTS, NodeKind.EVIDENCE, NodeKind.RISK_FINDING),
    (RelationshipKind.CONTRADICTS, NodeKind.EVIDENCE, NodeKind.RISK_FINDING),
    (RelationshipKind.CAUSED_BY, NodeKind.RISK_FINDING, NodeKind.SECURITY_EVENT),
    (RelationshipKind.INVESTIGATES, NodeKind.INVESTIGATION, NodeKind.RISK_FINDING),
    (RelationshipKind.REMEMBERS, NodeKind.AGENT, NodeKind.AGENT_MEMORY),
    (RelationshipKind.ASSIGNED_TO, NodeKind.TASK, NodeKind.AGENT),
    (RelationshipKind.DEPENDS_ON, NodeKind.TASK, NodeKind.TASK),
    (RelationshipKind.HANDS_OFF_TO, NodeKind.HANDOFF, NodeKind.AGENT),
    (RelationshipKind.USES_PROCEDURE, NodeKind.AGENT_RUN, NodeKind.PROCEDURE),
    (RelationshipKind.SUPERSEDES, NodeKind.ROLE, NodeKind.ROLE),
]


@pytest.mark.parametrize("kind, source_kind, target_kind", RELATIONSHIP_CASES)
def test_all_required_relationship_types_persist(
    tenant_repository: GraphRepository,
    kind: RelationshipKind,
    source_kind: NodeKind,
    target_kind: NodeKind,
) -> None:
    source = tenant_repository.upsert_node(node_input(source_kind, "source"))
    target = tenant_repository.upsert_node(node_input(target_kind, "target"))
    event = tenant_repository.ingest_event(node_input(NodeKind.SECURITY_EVENT, "event"))
    edge = RelationshipInput(
        kind=kind,
        source=source.ref,
        target=target.ref,
        provenance=provenance(kind.value),
        valid_from=START,
        source_event_id=event.id,
    )
    record = tenant_repository.create_relationship(edge)
    assert tenant_repository.create_relationship(edge) == record
    assert tenant_repository.relationships(source.ref, kind) == [record]


@pytest.mark.parametrize("kind", list(NodeKind))
def test_required_nodes_persist_and_are_readable(
    tenant_repository: GraphRepository, kind: NodeKind
) -> None:
    written = tenant_repository.upsert_node(node_input(kind))
    assert tenant_repository.get_node(written.ref) == written
    assert written.created_at.utcoffset() == timedelta(0)
    assert written.provenance.collector == "pytest"


def access_edge(repository: GraphRepository) -> RelationshipInput:
    identity = repository.upsert_node(node_input(NodeKind.IDENTITY))
    role = repository.upsert_node(node_input(NodeKind.ROLE))
    event = repository.ingest_event(node_input(NodeKind.SECURITY_EVENT))
    return RelationshipInput(
        kind=RelationshipKind.CAN_ASSUME,
        source=identity.ref,
        target=role.ref,
        provenance=provenance("grant-observation"),
        valid_from=START,
        source_event_id=event.id,
        confidence=0.95,
        verification_status="verified",
    )


def test_relationships_persist_with_provenance(tenant_repository: GraphRepository) -> None:
    edge = access_edge(tenant_repository)
    written = tenant_repository.create_relationship(edge)
    assert tenant_repository.relationships(edge.source, edge.kind) == [written]
    assert written.source_event_id == edge.source_event_id
    assert written.provenance.source_uri.startswith("urn:")
    assert written.confidence == 0.95


def test_duplicate_relationships_are_reconciled_concurrently(
    tenant_repository: GraphRepository,
) -> None:
    edge = access_edge(tenant_repository)
    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(lambda _: tenant_repository.create_relationship(edge), range(16)))
    assert len({record.id for record in records}) == 1
    assert tenant_repository.relationships(edge.source, edge.kind) == [records[0]]


def test_duplicate_events_are_not_duplicated_even_concurrently(
    tenant_repository: GraphRepository,
) -> None:
    event = node_input(NodeKind.SECURITY_EVENT)
    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(lambda _: tenant_repository.ingest_event(event), range(16)))
    assert len({record.id for record in records}) == 1
    assert len({record.created_at for record in records}) == 1
    assert len(tenant_repository.list_nodes(NodeKind.SECURITY_EVENT)) == 1
    changed = event.model_copy(update={"attributes": {**event.attributes, "event_type": "Changed"}})
    with pytest.raises(GraphConflict):
        tenant_repository.ingest_event(changed)
    assert tenant_repository.get_node(records[0].ref) == records[0]


def test_half_open_history_and_idempotent_closure(tenant_repository: GraphRepository) -> None:
    edge = access_edge(tenant_repository)
    written = tenant_repository.create_relationship(edge)
    assert tenant_repository.create_relationship(edge) == written
    closing_event = tenant_repository.ingest_event(node_input(NodeKind.SECURITY_EVENT, "revoke"))
    closed = tenant_repository.close_relationship(
        edge.source,
        edge.kind,
        written.id,
        valid_to=END,
        source_event_id=closing_event.id,
    )
    replay = tenant_repository.close_relationship(
        edge.source,
        edge.kind,
        written.id,
        valid_to=END,
        source_event_id=closing_event.id,
    )
    assert closed == replay
    assert closed.updated_at >= closed.created_at
    assert not tenant_repository.relationships(
        edge.source, edge.kind, at=START - timedelta(microseconds=1)
    )
    assert tenant_repository.relationships(edge.source, edge.kind, at=START) == [closed]
    assert tenant_repository.relationships(
        edge.source, edge.kind, at=END - timedelta(microseconds=1)
    ) == [closed]
    assert not tenant_repository.relationships(edge.source, edge.kind, at=END)
    assert tenant_repository.create_relationship(edge) == closed
    assert tenant_repository.relationships(edge.source, edge.kind) == [closed]
    with pytest.raises(GraphConflict):
        tenant_repository.close_relationship(
            edge.source,
            edge.kind,
            written.id,
            valid_to=END + timedelta(days=1),
            source_event_id=closing_event.id,
        )


def test_adjacent_versions_do_not_overlap_at_boundary(tenant_repository: GraphRepository) -> None:
    edge = access_edge(tenant_repository)
    first = tenant_repository.create_relationship(edge.model_copy(update={"valid_to": END}))
    next_version = tenant_repository.create_relationship(
        edge.model_copy(update={"valid_from": END, "provenance": provenance("regrant-observation")})
    )
    assert tenant_repository.relationships(edge.source, edge.kind, at=START) == [first]
    assert tenant_repository.relationships(edge.source, edge.kind, at=END) == [next_version]


def test_records_recover_after_reconnect_and_new_manager(
    tenant_repository: GraphRepository,
    live_database: GraphDatabase,
    isolated_graph_settings: Settings,
) -> None:
    edge = access_edge(tenant_repository)
    written = tenant_repository.create_relationship(edge)
    live_database.reconnect()
    assert tenant_repository.relationships(edge.source, edge.kind) == [written]
    replacement = GraphDatabase(isolated_graph_settings)
    try:
        repo = GraphRepository(replacement, TenantScope(tenant_id="tenant-a"))
        assert repo.relationships(edge.source, edge.kind) == [written]
        assert repo.get_node(edge.source) is not None
    finally:
        replacement.close()


def test_tenant_isolation_covers_reads_writes_history_and_source_events(
    tenant_repository: GraphRepository,
    live_database: GraphDatabase,
) -> None:
    alice = tenant_repository
    bob = GraphRepository(live_database, TenantScope(tenant_id="tenant-b"))
    edge_a = access_edge(alice)
    written_a = alice.create_relationship(edge_a)
    assert bob.get_node(edge_a.source) is None
    assert bob.list_nodes(NodeKind.IDENTITY) == []
    assert bob.relationships(edge_a.source, edge_a.kind, at=START) == []
    edge_b = access_edge(bob)
    assert edge_b.source.id != edge_a.source.id
    with pytest.raises(GraphNotFound):
        bob.create_relationship(edge_b.model_copy(update={"target": edge_a.target}))
    with pytest.raises(GraphNotFound):
        bob.create_relationship(
            edge_b.model_copy(update={"source_event_id": edge_a.source_event_id})
        )
    with pytest.raises(GraphConflict):
        bob.close_relationship(
            edge_a.source,
            edge_a.kind,
            written_a.id,
            valid_to=END,
            source_event_id=edge_b.source_event_id,
        )
    assert alice.relationships(edge_a.source, edge_a.kind) == [written_a]
    assert bob.relationships(edge_b.source, edge_b.kind) == []


def test_parameter_values_cannot_inject_cypher(tenant_repository: GraphRepository) -> None:
    malicious = "value' }) MATCH (n) DETACH DELETE n //"
    original = node_input(NodeKind.AGENT, "safe-source-id")
    node = original.model_copy(update={"attributes": {"name": malicious}})
    result = tenant_repository.upsert_node(node)
    assert tenant_repository.get_node(result.ref).attributes["name"] == malicious
    assert len(tenant_repository.list_nodes(NodeKind.AGENT)) == 1


def test_two_accounts_can_use_identical_source_identifiers(
    tenant_repository: GraphRepository,
) -> None:
    original = node_input(NodeKind.ROLE, "Admin")
    a = tenant_repository.upsert_node(original)
    b = tenant_repository.upsert_node(original.model_copy(update={"account_id": "222222222222"}))
    assert a.id != b.id
    assert tenant_repository.list_nodes(NodeKind.ROLE, account_id="111111111111") == [a]


def test_schema_reinitialization_and_unique_constraint_rollback(
    tenant_repository: GraphRepository,
    live_database: GraphDatabase,
) -> None:
    SchemaManager(live_database).initialize()
    original = tenant_repository.upsert_node(node_input(NodeKind.AGENT))
    properties = live_database.query(
        "MATCH (n:Entity {id: $id}) RETURN properties(n)",
        {"id": original.id},
        read_only=True,
    )[0][0]
    # One statement attempts two mutations; the second violates the real DB constraint.
    changed = {**properties, "id": "rollback-sentinel", "source_id": "rollback-sentinel"}
    first_literal, first_bindings = creation_properties(changed, "first")
    second_literal, second_bindings = creation_properties(properties, "second")
    with pytest.raises(GraphConflict):
        live_database.execute_atomic(
            f"CREATE (a:Entity:Agent {first_literal}) "
            f"CREATE (b:Entity:Agent {second_literal}) RETURN a.id",
            {**first_bindings, **second_bindings},
        )
    assert live_database.query(
        "MATCH (n:Entity {id: $id}) RETURN count(n)",
        {"id": "rollback-sentinel"},
        read_only=True,
    ) == [[0]]


def test_schema_initializer_rejects_invalid_existing_data(
    isolated_graph_settings: Settings,
) -> None:
    database = GraphDatabase(isolated_graph_settings)
    try:
        # Simulate invalid administrative data predating schema installation.
        database.query("CREATE (:Entity {id: $id})", {"id": "invalid-before-migration"})
        with pytest.raises(GraphSchemaError):
            database.initialize_schema()
        # The initializer fails rather than removing or rewriting invalid evidence.
        assert database.query("MATCH (n:Entity) RETURN count(n)", read_only=True) == [[1]]
    finally:
        database.close()

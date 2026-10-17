"""Tenant-bound graph access; there is intentionally no public arbitrary-Cypher method."""

from datetime import datetime
from typing import Any

from app.core.time import utc_now
from app.db.cypher import creation_properties, fingerprint, node_label, relationship_type, stable_id
from app.db.errors import GraphConflict, GraphNotFound
from app.db.falkordb import GraphDatabase
from app.models.graph import (
    ATTRIBUTES,
    GraphNode,
    GraphRelationship,
    NodeInput,
    NodeKind,
    NodeRef,
    Provenance,
    RelationshipInput,
    RelationshipKind,
    TenantScope,
    epoch_us,
    from_epoch_us,
)
from app.models.relationships import validate_endpoints


def provenance_properties(provenance: Provenance) -> dict[str, object]:
    return {
        "source": provenance.source,
        "source_id": provenance.source_id,
        "observed_at": epoch_us(provenance.observed_at),
        "collector": provenance.collector,
        "source_uri": provenance.source_uri,
    }


def read_provenance(properties: dict[str, Any]) -> Provenance:
    return Provenance(
        source=properties["source"],
        source_id=properties["source_id"],
        observed_at=from_epoch_us(properties["observed_at"]),
        collector=properties["collector"],
        source_uri=properties["source_uri"],
    )


def read_node(properties: dict[str, Any]) -> GraphNode:
    kind = NodeKind(properties["kind"])
    return GraphNode(
        id=properties["id"],
        kind=kind,
        tenant_id=properties["tenant_id"],
        account_id=properties.get("account_id"),
        created_at=from_epoch_us(properties["created_at"]),
        updated_at=from_epoch_us(properties["updated_at"]),
        provenance=read_provenance(properties),
        attributes={key: properties[key] for key in ATTRIBUTES[kind] if key in properties},
    )


def read_relationship(properties: dict[str, Any]) -> GraphRelationship:
    return GraphRelationship(
        id=properties["id"],
        kind=properties["kind"],
        tenant_id=properties["tenant_id"],
        source=NodeRef(id=properties["source_node_id"], kind=properties["source_kind"]),
        target=NodeRef(id=properties["target_node_id"], kind=properties["target_kind"]),
        provenance=read_provenance(properties),
        created_at=from_epoch_us(properties["created_at"]),
        updated_at=from_epoch_us(properties["updated_at"]),
        valid_from=from_epoch_us(properties["valid_from"]),
        valid_to=(from_epoch_us(properties["valid_to"]) if "valid_to" in properties else None),
        source_event_id=properties.get("source_event_id"),
        closed_by_event_id=properties.get("closed_by_event_id"),
        confidence=properties["confidence"],
        verification_status=properties["verification_status"],
    )


def build_node_properties(tenant_id: str, node: NodeInput, now: int) -> dict[str, Any]:
    label = node_label(node.kind)
    properties = {
        **node.attributes,
        **provenance_properties(node.provenance),
        "id": stable_id(
            tenant_id,
            label,
            node.account_id or "",
            node.provenance.source,
            node.provenance.source_id,
        ),
        "tenant_id": tenant_id,
        "kind": label,
        "account_id": node.account_id,
        "created_at": now,
        "updated_at": now,
    }
    properties["fingerprint"] = fingerprint(
        {
            key: value
            for key, value in properties.items()
            if key not in {"created_at", "updated_at", "observed_at"}
        }
    )
    return properties


def build_relationship_properties(
    tenant_id: str,
    edge: RelationshipInput,
    now: int,
) -> dict[str, Any]:
    kind = relationship_type(edge.kind)
    properties = {
        **provenance_properties(edge.provenance),
        "id": stable_id(
            tenant_id,
            kind,
            edge.source.id,
            edge.target.id,
            edge.provenance.source,
            edge.provenance.source_id,
            str(epoch_us(edge.valid_from)),
        ),
        "kind": kind,
        "tenant_id": tenant_id,
        "source_node_id": edge.source.id,
        "target_node_id": edge.target.id,
        "source_kind": node_label(edge.source.kind),
        "target_kind": node_label(edge.target.kind),
        "created_at": now,
        "updated_at": now,
        "valid_from": epoch_us(edge.valid_from),
        "valid_to": epoch_us(edge.valid_to) if edge.valid_to else None,
        "source_event_id": edge.source_event_id,
        "confidence": edge.confidence,
        "verification_status": edge.verification_status.value,
    }
    properties["fingerprint"] = fingerprint(
        {
            key: value
            for key, value in properties.items()
            if key not in {"created_at", "updated_at", "observed_at"}
        }
    )
    return properties


class GraphRepository:
    def __init__(self, database: GraphDatabase, scope: TenantScope) -> None:
        self._database = database
        # Copy/validate the caller's scope once. It cannot be overridden through input properties.
        self._scope = TenantScope.model_validate(scope.model_dump())

    @property
    def tenant_id(self) -> str:
        return self._scope.tenant_id

    def _execute(
        self,
        cypher: str,
        parameters: dict[str, Any],
        *,
        read_only: bool = False,
        idempotent: bool = False,
    ) -> list[list[Any]]:
        self._database.initialize_schema()
        # The scope is injected last, so callers cannot override it in parameter maps.
        params = {**parameters, "tenant_id": self.tenant_id}
        if not read_only:
            return self._database.mutate(
                cypher,
                params,
                tenant_id=self.tenant_id,
                event_type="graph.record.changed",
                entity_ids=[params["id"]] if params.get("id") else [],
                idempotent=idempotent,
            )
        return self._database.query(cypher, params, read_only=read_only, idempotent=idempotent)

    def upsert_node(self, node: NodeInput) -> GraphNode:
        """First-write-wins immutable observation. Replays preserve original provenance.

        Changed content for the same natural key is a conflict. New versions need a new
        source_id and a SUPERSEDES link, preserving the historical evidence trail.
        """
        node = NodeInput.model_validate(node.model_dump())
        label = node_label(node.kind)
        now = epoch_us(utc_now())
        properties = build_node_properties(self.tenant_id, node, now)
        identifier = properties["id"]
        literal, bindings = creation_properties(properties)
        try:
            rows = self._execute(
                f"MATCH (n:Entity:{label} {{tenant_id: $tenant_id, id: $id}}) "
                "RETURN properties(n) UNION ALL "
                f"OPTIONAL MATCH (existing:Entity:{label} {{tenant_id: $tenant_id, id: $id}}) "
                "WITH existing WHERE existing IS NULL "
                f"CREATE (n:Entity:{label} {literal}) RETURN properties(n)",
                {"id": identifier, **bindings},
                idempotent=True,
            )
        except GraphConflict:
            # A concurrent writer may win after this statement's initial read. The
            # database constraint prevents duplicates; reconcile committed content.
            rows = self._execute(
                f"MATCH (n:Entity:{label} {{tenant_id: $tenant_id, id: $id}}) RETURN properties(n)",
                {"id": identifier},
                read_only=True,
            )
            if not rows:
                raise
        if rows[0][0]["fingerprint"] != properties["fingerprint"]:
            raise GraphConflict()
        return read_node(rows[0][0])

    def ingest_event(self, event: NodeInput) -> GraphNode:
        if event.kind != NodeKind.SECURITY_EVENT:
            raise ValueError("ingest_event requires a SecurityEvent")
        return self.upsert_node(event)

    def get_node(self, reference: NodeRef) -> GraphNode | None:
        label = node_label(reference.kind)
        rows = self._execute(
            f"MATCH (n:Entity:{label} {{tenant_id: $tenant_id, id: $id}}) RETURN properties(n)",
            {"id": reference.id},
            read_only=True,
        )
        return read_node(rows[0][0]) if rows else None

    def list_nodes(
        self,
        kind: NodeKind,
        *,
        account_id: str | None = None,
        limit: int = 100,
        after_id: str = "",
    ) -> list[GraphNode]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        label = node_label(kind)
        rows = self._execute(
            f"MATCH (n:Entity:{label} {{tenant_id: $tenant_id}}) "
            "WHERE n.id > $after_id AND ($account_id IS NULL OR n.account_id = $account_id) "
            "RETURN properties(n) ORDER BY n.id LIMIT $limit",
            {"after_id": after_id, "account_id": account_id, "limit": limit},
            read_only=True,
        )
        return [read_node(row[0]) for row in rows]

    def create_relationship(self, edge: RelationshipInput) -> GraphRelationship:
        edge = RelationshipInput.model_validate(edge.model_dump())
        validate_endpoints(edge)
        kind = relationship_type(edge.kind)
        source_label = node_label(edge.source.kind)
        target_label = node_label(edge.target.kind)
        now = epoch_us(utc_now())
        properties = build_relationship_properties(self.tenant_id, edge, now)
        identifier = properties["id"]
        # All endpoints and optional source-event existence are checked before MERGE.
        # A different tenant's IDs match zero rows; no partial relationship is written.
        event_match = (
            "MATCH (event:Entity:SecurityEvent {tenant_id: $tenant_id, id: $event_id}) "
            if edge.source_event_id
            else ""
        )
        endpoint_match = (
            f"MATCH (a:Entity:{source_label} {{tenant_id: $tenant_id, id: $source_id}}), "
            f"(b:Entity:{target_label} {{tenant_id: $tenant_id, id: $target_id}}) " + event_match
        )
        literal, bindings = creation_properties(properties)
        parameters = {
            "source_id": edge.source.id,
            "target_id": edge.target.id,
            "event_id": edge.source_event_id,
            "id": identifier,
            **bindings,
        }
        read_query = endpoint_match + (
            f"MATCH (a)-[r:{kind} {{tenant_id: $tenant_id, id: $id}}]->(b) RETURN properties(r)"
        )
        try:
            rows = self._execute(
                read_query
                + " UNION ALL "
                + endpoint_match
                + f"OPTIONAL MATCH (a)-[existing:{kind} {{tenant_id: $tenant_id, id: $id}}]->(b) "
                "WITH a, b, existing WHERE existing IS NULL "
                f"CREATE (a)-[r:{kind} {literal}]->(b) RETURN properties(r)",
                parameters,
                idempotent=True,
            )
        except GraphConflict:
            rows = self._execute(read_query, parameters, read_only=True)
            if not rows:
                raise
        if not rows:
            raise GraphNotFound()
        if rows[0][0]["fingerprint"] != properties["fingerprint"]:
            raise GraphConflict()
        return read_relationship(rows[0][0])

    def relationships(
        self,
        source: NodeRef,
        kind: RelationshipKind,
        *,
        at: datetime | None = None,
        limit: int = 100,
        after_id: str = "",
    ) -> list[GraphRelationship]:
        """No `at` means all history; a UTC instant applies [valid_from, valid_to)."""
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        label = node_label(source.kind)
        edge_type = relationship_type(kind)
        time_predicate = (
            "AND r.valid_from <= $at AND (r.valid_to IS NULL OR $at < r.valid_to) "
            if at is not None
            else ""
        )
        rows = self._execute(
            f"MATCH (a:Entity:{label} {{tenant_id: $tenant_id, id: $source_id}})"
            f"-[r:{edge_type}]->(b:Entity {{tenant_id: $tenant_id}}) "
            "WHERE r.tenant_id = $tenant_id AND r.id > $after_id "
            + time_predicate
            + "RETURN properties(r) ORDER BY r.id LIMIT $limit",
            {
                "source_id": source.id,
                "at": epoch_us(at) if at is not None else None,
                "limit": limit,
                "after_id": after_id,
            },
            read_only=True,
        )
        return [read_relationship(row[0]) for row in rows]

    def close_relationship(
        self,
        source: NodeRef,
        kind: RelationshipKind,
        relationship_id: str,
        *,
        valid_to: datetime,
        source_event_id: str,
    ) -> GraphRelationship:
        """Close one historical version exactly once; replaying the same closure is safe."""
        label, edge_type = node_label(source.kind), relationship_type(kind)
        rows = self._execute(
            f"MATCH (a:Entity:{label} {{tenant_id: $tenant_id, id: $source_id}})"
            f"-[r:{edge_type} {{tenant_id: $tenant_id, id: $id}}]->"
            "(b:Entity {tenant_id: $tenant_id}), "
            "(event:Entity:SecurityEvent {tenant_id: $tenant_id, id: $event_id}) "
            "WHERE r.valid_from < $valid_to AND "
            "((r.valid_to IS NULL AND r.closed_by_event_id IS NULL) OR "
            "(r.valid_to = $valid_to AND r.closed_by_event_id = $event_id)) "
            "SET r.updated_at = CASE WHEN r.closed_by_event_id IS NULL THEN $now "
            "ELSE r.updated_at END, r.valid_to = $valid_to, "
            "r.closed_by_event_id = $event_id RETURN properties(r)",
            {
                "id": relationship_id,
                "source_id": source.id,
                "event_id": source_event_id,
                "valid_to": epoch_us(valid_to),
                "now": epoch_us(utc_now()),
            },
            idempotent=True,
        )
        if not rows:
            # Deliberately do not reveal whether another tenant owns the supplied ID.
            raise GraphConflict()
        return read_relationship(rows[0][0])

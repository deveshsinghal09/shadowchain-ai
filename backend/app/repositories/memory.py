"""Tenant/agent-bound memory graph access. No arbitrary query interface is exposed to agents."""

import json
from typing import Any

from app.core.time import utc_now
from app.db.cypher import creation_properties
from app.db.errors import GraphConflict, GraphNotFound
from app.db.falkordb import GraphDatabase
from app.models.graph import (
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
from app.models.memory import MEMORY_VERSION, MemoryQuery
from app.models.relationships import validate_endpoints
from app.repositories.graph import build_node_properties, build_relationship_properties
from app.services.memory_errors import MemoryAccessDenied, MemoryLimitExceeded

SOURCE = "shadowchain.memory.v1"
READ_ROLES = ["reader", "writer", "curator"]
WRITE_ROLES = ["writer", "curator"]
MAX_RESULTS = 100


def active(edge: str) -> str:
    return (
        f"{edge}.tenant_id = $tenant_id AND {edge}.valid_from <= $auth_now "
        f"AND ({edge}.valid_to IS NULL OR $auth_now < {edge}.valid_to) "
        f"AND {edge}.verification_status = 'verified'"
    )


class MemoryMutation:
    """Only code-owned labels/property shapes; values always use SDK parameter maps."""

    def __init__(self, tenant_id: str, operation_id: str, now: int):
        self.tenant_id, self.operation_id, self.now = tenant_id, operation_id, now
        self.params: dict[str, Any] = {}
        self.matches: list[str] = []
        self.changes: list[str] = []
        self.counter = 0

    def provenance(self, key: str) -> Provenance:
        return Provenance(
            source=SOURCE,
            source_id=key,
            observed_at=from_epoch_us(self.now),
            collector=MEMORY_VERSION,
            source_uri="urn:shadowchain:memory:" + key,
        )

    def bind(self, node: dict, variable: str, *, context: bool = False) -> None:
        self.params[variable + "_id"] = node["id"]
        prefix = f"(space)-[g{variable}:HAS_CONTEXT]->" if context else ""
        self.matches.append(
            f"MATCH {prefix}({variable}:Entity:{NodeKind(node['kind']).value} "
            f"{{tenant_id: $tenant_id, id: ${variable}_id}}) "
            + (f"WHERE {active('g' + variable)} " if context else "")
        )

    def node(self, kind: NodeKind, key: str, attributes: dict, variable: str) -> dict:
        props = build_node_properties(
            self.tenant_id,
            NodeInput(
                kind=kind,
                provenance=self.provenance(key),
                attributes=attributes,
            ),
            self.now,
        )
        literal, params = creation_properties(props, variable)
        self.params.update(params)
        extra = ":PersistentMemory" if kind == NodeKind.AGENT_MEMORY else ""
        self.changes.append(f"CREATE ({variable}:Entity:{kind.value}{extra} {literal}) ")
        return props

    def edge(
        self,
        source: dict,
        target: dict,
        kind: RelationshipKind,
        source_var: str,
        target_var: str,
        *,
        extra: dict | None = None,
    ) -> dict:
        self.counter += 1
        key = self.operation_id + "/edge/" + str(self.counter)
        value = RelationshipInput(
            kind=kind,
            source=NodeRef(kind=source["kind"], id=source["id"]),
            target=NodeRef(kind=target["kind"], id=target["id"]),
            provenance=self.provenance(key),
            valid_from=from_epoch_us(self.now),
            verification_status="verified",
        )
        validate_endpoints(value)
        props = build_relationship_properties(self.tenant_id, value, self.now)
        props.update(extra or {})
        prefix = "r" + str(self.counter)
        literal, params = creation_properties(props, prefix)
        self.params.update(params)
        self.changes.append(f"CREATE ({source_var})-[:{kind.value} {literal}]->({target_var}) ")
        return props


class MemoryRepository:
    def __init__(self, database: GraphDatabase, scope: TenantScope, space_id: str, agent_id: str):
        self.database = database
        self.scope = TenantScope.model_validate(scope.model_dump())
        self.space_id, self.agent_id = space_id, agent_id

    def parameters(self, values: dict | None = None) -> dict:
        return {
            **(values or {}),
            "tenant_id": self.scope.tenant_id,
            "space_id": self.space_id,
            "agent_id": self.agent_id,
            "auth_now": epoch_us(utc_now()),
        }

    def read(self, query: str, params: dict | None = None) -> list:
        return self.database.query(query, self.parameters(params), read_only=True)

    def access(self, agent_id: str | None = None) -> dict:
        params = {"actor_id": agent_id or self.agent_id}
        rows = self.read(
            "MATCH (agent:Entity:Agent {tenant_id: $tenant_id, id: $actor_id})"
            "-[membership:MEMBER_OF]->(space:Entity:MemorySpace "
            "{tenant_id: $tenant_id, id: $space_id}) "
            f"WHERE {active('membership')} AND membership.role IN $roles "
            "OPTIONAL MATCH (space)-[grant:HAS_CONTEXT]->(context:Entity {tenant_id: $tenant_id}) "
            f"WHERE {active('grant')} "
            "RETURN properties(agent), properties(space), membership.role, "
            "collect(properties(context))[0..1001]",
            {**params, "roles": READ_ROLES},
        )
        if not rows:
            raise MemoryAccessDenied()
        if len(rows) != 1:
            raise MemoryAccessDenied()
        agent, space, role, contexts = rows[0]
        if len(contexts) > 1000:
            raise MemoryLimitExceeded()
        return {
            "agent": agent,
            "space": space,
            "role": role,
            "contexts": {node["id"]: node for node in contexts},
        }

    def operation(self, operation_id: str, digest: str) -> dict | None:
        rows = self.read(
            "MATCH (d:Entity:Decision {tenant_id: $tenant_id, id: $id, space_id: $space_id, "
            "actor_agent_id: $agent_id}) RETURN d.request_sha256, d.response_json",
            {"id": operation_id},
        )
        if not rows:
            return None
        if rows[0][0] != digest:
            raise GraphConflict()
        return json.loads(rows[0][1])

    def commit(self, mutation: MemoryMutation, revision: int, roles: list[str]) -> bool:
        query = (
            "MATCH (agent:Entity:Agent {tenant_id: $tenant_id, id: $agent_id})"
            "-[membership:MEMBER_OF]->(space:Entity:MemorySpace "
            "{tenant_id: $tenant_id, id: $space_id}) "
            f"WHERE {active('membership')} AND membership.role IN $roles "
            "SET space.lock_version = space.lock_version + 1 WITH agent, space, membership "
            "WHERE space.revision = $revision "
            + "".join(mutation.matches)
            + "".join(mutation.changes)
            + "SET space.revision = space.revision + 1, space.updated_at = $changed_at "
            "RETURN space.revision"
        )
        return bool(
            self.database.mutate(
                query,
                self.parameters(
                    {
                        **mutation.params,
                        "revision": revision,
                        "roles": roles,
                        "changed_at": mutation.now,
                    }
                ),
                idempotent=True,
                tenant_id=self.scope.tenant_id,
                event_type="memory.changed",
                entity_ids=[self.space_id, self.agent_id],
            )
        )

    def series(self, series_id: str) -> dict | None:
        rows = self.read(
            "MATCH (s:MemorySeries {tenant_id: $tenant_id, id: $id, space_id: $space_id}) "
            "RETURN properties(s)",
            {"id": series_id},
        )
        return rows[0][0] if rows else None

    def retrieve(
        self,
        query: MemoryQuery,
        *,
        audit: bool = False,
        recipient: str | None = None,
        claim_key: str | None = None,
    ) -> list[dict]:
        query = MemoryQuery.model_validate(query.model_dump())
        if type(query.max_hops) is not int or query.max_hops not in range(3):
            raise ValueError("Memory traversal supports zero to two hops")
        # ACL time is always server NOW; historical data time never resurrects old permissions.
        role_gate = (
            "MATCH (agent:Entity:Agent {tenant_id: $tenant_id, id: $agent_id})"
            "-[membership:MEMBER_OF]->(space:Entity:MemorySpace "
            "{tenant_id: $tenant_id, id: $space_id}) "
            f"WHERE {active('membership')} AND membership.role IN $roles "
        )
        if recipient:
            role_gate += (
                "MATCH (recipient:Entity:Agent {tenant_id: $tenant_id, id: $recipient})"
                f"-[rm:MEMBER_OF]->(space) WHERE {active('rm')} AND rm.role IN $roles "
            )
        # Keep an ACL-backed set inside this server read, not in a persistent Python cache.
        cypher = role_gate + (
            "OPTIONAL MATCH (space)-[grant:HAS_CONTEXT]->(allowed:Entity {tenant_id: $tenant_id}) "
            f"WHERE {active('grant')} WITH space, collect(allowed.id) AS allowed_ids "
            "MATCH (memory:Entity:AgentMemory:PersistentMemory {tenant_id: $tenant_id, "
            "space_id: $space_id})-[inside:BELONGS_TO]->(space) "
            f"WHERE {active('inside')} "
            "AND (memory.audience = 'team' OR memory.author_agent_id = $agent_id) "
            "AND ($recipient IS NULL OR memory.audience = 'team') "
            "AND ($include_superseded OR memory.state = 'current') "
            "AND ($audit OR (memory.valid_from <= $at AND "
            "(memory.valid_to IS NULL OR $at < memory.valid_to))) "
            "AND ($type IS NULL OR memory.memory_type = $type) "
            "AND ($claim_key IS NULL OR memory.claim_key = $claim_key) "
            "AND (size($memory_ids) = 0 OR memory.id IN $memory_ids) "
            "MATCH (memory)-[reference:ABOUT|DERIVED_FROM]->"
            "(record:Entity {tenant_id: $tenant_id}) "
            "WHERE reference.tenant_id = $tenant_id "
            "WITH memory, allowed_ids, collect({node: properties(record), "
            "kind: type(reference)}) AS refs "
            "WHERE size(refs) = memory.reference_count "
            "AND all(ref IN refs WHERE ref.node.id IN allowed_ids OR "
            "(ref.node.kind = 'Decision' AND ref.node.source = $source "
            "AND ref.node.space_id = $space_id)) "
            "WITH memory, refs, allowed_ids "
            "OPTIONAL MATCH route=(anchor:Entity {tenant_id: $tenant_id})"
            "-[:CAN_ASSUME|HAS_POLICY|GRANTS|TARGETS|AFFECTS|SUPPORTS|DERIVED_FROM|INVESTIGATES|BELONGS_TO"
            f"*0..{query.max_hops}]-(subject:Entity {{tenant_id: $tenant_id}}) "
            "WHERE anchor.id IN [ref IN refs | ref.node.id] "
            "AND subject.id IN $subjects "
            "AND all(n IN nodes(route) WHERE n.tenant_id = $tenant_id AND n.id IN allowed_ids) "
            "AND all(r IN relationships(route) WHERE r.tenant_id = $tenant_id "
            "AND r.valid_from <= $at AND (r.valid_to IS NULL OR $at < r.valid_to) "
            "AND r.verification_status = 'verified') "
            "WITH memory, refs, count(route) AS matches "
            "WHERE size($subjects) = 0 OR matches > 0 "
            "RETURN properties(memory), refs ORDER BY memory.created_at, memory.id LIMIT $fetch"
        )
        rows = self.read(
            cypher,
            {
                "roles": READ_ROLES,
                "recipient": recipient,
                "subjects": query.subject_ids,
                "memory_ids": query.memory_ids,
                "include_superseded": query.include_superseded,
                "type": query.memory_type.value if query.memory_type else None,
                "at": epoch_us(query.at or utc_now()),
                "audit": audit,
                "source": SOURCE,
                "fetch": query.limit + 1,
                "claim_key": claim_key,
            },
        )
        if len(rows) > query.limit:
            raise MemoryLimitExceeded()
        return [{"memory": props, "refs": refs} for props, refs in rows]

    def raw_conflicts(self, memory_ids: list[str] | None = None) -> list[dict]:
        rows = self.read(
            "MATCH (a:Entity:AgentMemory:PersistentMemory "
            "{tenant_id: $tenant_id, space_id: $space_id})"
            "-[c:CONTRADICTS {tenant_id: $tenant_id}]->"
            "(b:Entity:AgentMemory:PersistentMemory {tenant_id: $tenant_id, space_id: $space_id}) "
            "WHERE $ids IS NULL OR a.id IN $ids OR b.id IN $ids "
            "RETURN properties(c), a.id, b.id LIMIT 501",
            {"ids": memory_ids},
        )
        if len(rows) > 500:
            raise MemoryLimitExceeded()
        return [{"edge": row[0], "a": row[1], "b": row[2]} for row in rows]

    def get(self, memory_id: str) -> dict:
        records = self.retrieve(
            MemoryQuery(memory_ids=[memory_id], include_superseded=True), audit=True
        )
        if not records:
            raise GraphNotFound()
        return records[0]

    def handoff(self, handoff_id: str) -> dict:
        rows = self.read(
            "MATCH (h:Entity:Handoff {tenant_id: $tenant_id, id: $id, space_id: $space_id}) "
            "WHERE h.sender_agent_id = $agent_id OR h.recipient_agent_id = $agent_id "
            "OPTIONAL MATCH (h)-[r:INCLUDES_MEMORY {tenant_id: $tenant_id}]->"
            "(m:Entity:AgentMemory {tenant_id: $tenant_id, space_id: $space_id}) "
            "RETURN properties(h), collect(m.id)",
            {"id": handoff_id},
        )
        if not rows:
            raise GraphNotFound()
        return {"handoff": rows[0][0], "memory_ids": rows[0][1]}

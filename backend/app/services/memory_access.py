"""Trusted application administration only. Never expose this class as an agent/LLM tool."""

import json

from app.core.time import utc_now
from app.db.cypher import fingerprint, stable_id
from app.db.errors import GraphConflict, GraphNotFound
from app.models.graph import NodeInput, NodeKind, NodeRef, Provenance, RelationshipKind, epoch_us
from app.models.memory import MEMORY_VERSION, MemoryRole
from app.repositories.graph import GraphRepository
from app.repositories.memory import SOURCE, MemoryMutation, MemoryRepository, active
from app.services.memory_errors import MemoryVersionConflict


def node_id(tenant: str, kind: NodeKind, source_key: str) -> str:
    return stable_id(tenant, kind.value, "", SOURCE, source_key)


class MemoryAccessAdmin:
    def __init__(self, database, scope):
        self.database, self.scope = database, scope
        self.graph = GraphRepository(database, scope)

    def create_space(self, key: str, name: str) -> str:
        # Graph validation rejects credential material and invalid property shapes.
        now = utc_now()
        return self.graph.upsert_node(
            NodeInput(
                kind=NodeKind.MEMORY_SPACE,
                provenance=Provenance(
                    source=SOURCE,
                    source_id="space/" + key,
                    observed_at=now,
                    collector=MEMORY_VERSION,
                    source_uri="urn:shadowchain:memory:space/" + key,
                ),
                attributes={"name": name, "revision": 0, "lock_version": 0},
            )
        ).id

    def grant_member(self, space_id: str, agent_id: str, role: MemoryRole, request_id: str) -> None:
        self._change(
            space_id,
            NodeRef(kind=NodeKind.AGENT, id=agent_id),
            "member",
            request_id,
            role=MemoryRole(role).value,
        )

    def revoke_member(self, space_id: str, agent_id: str, request_id: str) -> None:
        self._change(space_id, NodeRef(kind=NodeKind.AGENT, id=agent_id), "member", request_id)

    def grant_context(self, space_id: str, reference: NodeRef, request_id: str) -> None:
        if reference.kind in {NodeKind.MEMORY_SPACE, NodeKind.AGENT_MEMORY, NodeKind.HANDOFF}:
            raise ValueError("Managed memory is accessed through its space, never as a raw source")
        self._change(space_id, reference, "context", request_id, role="context")

    def revoke_context(self, space_id: str, reference: NodeRef, request_id: str) -> None:
        self._change(space_id, reference, "context", request_id)

    def _change(
        self,
        space_id: str,
        reference: NodeRef,
        category: str,
        request_id: str,
        *,
        role: str | None = None,
    ) -> None:
        repo = MemoryRepository(self.database, self.scope, space_id, "system:memory-admin")
        key = stable_id(self.scope.tenant_id, space_id, "memory-admin", request_id)
        operation_id = node_id(self.scope.tenant_id, NodeKind.DECISION, key)
        digest = fingerprint({"target": reference.model_dump(), "category": category, "role": role})
        for _ in range(5):
            if repo.operation(operation_id, digest):
                return
            space = self.graph.get_node(NodeRef(kind=NodeKind.MEMORY_SPACE, id=space_id))
            target = self.graph.get_node(reference)
            if space is None or target is None:
                raise GraphNotFound()
            now = epoch_us(utc_now())
            mutation = MemoryMutation(self.scope.tenant_id, key, now)
            target_props = {"id": target.id, "kind": target.kind.value}
            space_props = {"id": space.id, "kind": NodeKind.MEMORY_SPACE.value}
            mutation.bind(target_props, "target")
            edge_kind = (
                RelationshipKind.MEMBER_OF if category == "member" else RelationshipKind.HAS_CONTEXT
            )
            pattern = (
                "(target)-[previous:MEMBER_OF]->(space)"
                if category == "member"
                else "(space)-[previous:HAS_CONTEXT]->(target)"
            )
            mutation.matches.append(f"OPTIONAL MATCH {pattern} WHERE {active('previous')} ")
            # FOREACH keeps zero prior grants from dropping the mutation row.
            mutation.changes.append(
                "FOREACH (old IN CASE WHEN previous IS NULL THEN [] ELSE [previous] END | "
                "SET old.valid_to = $changed_at, old.updated_at = $changed_at) "
            )
            if role is not None:
                source, dest = (
                    (target_props, space_props)
                    if category == "member"
                    else (space_props, target_props)
                )
                a, b = ("target", "space") if category == "member" else ("space", "target")
                mutation.edge(source, dest, edge_kind, a, b, extra={"role": role})
            mutation.node(
                NodeKind.DECISION,
                key,
                {
                    "action": "memory.acl." + category,
                    "status": "applied",
                    "rationale": "Trusted memory administration",
                    "space_id": space_id,
                    "actor_agent_id": "system:memory-admin",
                    "request_sha256": digest,
                    "response_json": json.dumps({"applied": True}),
                    "audit_json": json.dumps({"target_id": reference.id, "role": role}),
                },
                "audit",
            )
            try:
                rows = self.database.mutate(
                    "MATCH (space:Entity:MemorySpace {tenant_id: $tenant_id, id: $space_id}) "
                    "SET space.lock_version = space.lock_version + 1 WITH space "
                    "WHERE space.revision = $revision "
                    + "".join(mutation.matches)
                    + "".join(mutation.changes)
                    + "SET space.revision = space.revision + 1, space.updated_at = $changed_at "
                    "RETURN space.id",
                    repo.parameters(
                        {
                            **mutation.params,
                            "revision": space.attributes["revision"],
                            "changed_at": now,
                        }
                    ),
                    idempotent=True,
                    tenant_id=self.scope.tenant_id,
                    event_type="memory.access.changed",
                    entity_ids=[space_id, reference.id],
                )
            except GraphConflict:
                continue
            if rows:
                return
        raise MemoryVersionConflict()

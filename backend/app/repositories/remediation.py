"""Scoped, atomic proposal/simulation/audit storage. Cloud projection writes are absent."""

import json

from app.core.time import utc_now
from app.db.cypher import creation_properties, fingerprint, stable_id
from app.db.errors import GraphConflict, GraphNotFound
from app.models.graph import NodeKind as N
from app.models.graph import Provenance, epoch_us, from_epoch_us
from app.models.graph import RelationshipKind as R
from app.models.remediation import REMEDIATION_SOURCE, SIMULATOR_VERSION
from app.repositories.memory import MemoryMutation, MemoryRepository
from app.repositories.workflow import WorkflowRepository
from app.services.remediation_errors import RemediationConflict, RemediationLimit


def identifier(tenant, key):
    return stable_id(tenant, N.DECISION.value, "", REMEDIATION_SOURCE, key)


class RemediationMutation(MemoryMutation):
    def provenance(self, key):
        return Provenance(
            source=REMEDIATION_SOURCE,
            source_id=key,
            observed_at=from_epoch_us(self.now),
            collector=SIMULATOR_VERSION,
            source_uri="urn:shadowchain:remediation:" + key,
        )


class RemediationRepository(WorkflowRepository):
    def __init__(self, database, scope, settings, space_id, actor_id):
        super().__init__(database, scope, settings)
        self.space_id, self.actor_id = space_id, actor_id

    def access(self):
        return MemoryRepository(self.database, self.scope, self.space_id, self.actor_id).access()

    def params(self, values=None):
        return super().params({**(values or {}), "space_id": self.space_id})

    def key(self, kind, request_id):
        return stable_id(self.scope.tenant_id, self.space_id, self.actor_id, kind, request_id)

    def operation(self, key, digest):
        rows = self.read(
            "MATCH (d:Decision {tenant_id: $tenant_id, id: $id, space_id: $space_id}) "
            "RETURN d.request_sha256, d.response_json",
            {"id": identifier(self.scope.tenant_id, key)},
        )
        if not rows:
            return None
        if rows[0][0] != digest:
            raise GraphConflict()
        return json.loads(rows[0][1])

    def stream(self):
        stream_id = stable_id(self.scope.tenant_id, "remediation-stream")
        existing = self.read(
            "MATCH (s:RemediationStream {tenant_id: $tenant_id, id: $id}) RETURN properties(s)",
            {"id": stream_id},
        )
        if existing:
            return existing[0][0]
        props = {
            "id": stream_id,
            "tenant_id": self.scope.tenant_id,
            "kind": "RemediationStream",
            "source": REMEDIATION_SOURCE,
            "source_id": "remediation-stream",
            "collector": SIMULATOR_VERSION,
            "source_uri": "urn:shadowchain:remediation-stream",
            "observed_at": epoch_us(utc_now()),
            "created_at": epoch_us(utc_now()),
            "updated_at": epoch_us(utc_now()),
            "revision": 0,
            "lock_version": 0,
            "fingerprint": fingerprint({"tenant_id": self.scope.tenant_id}),
        }
        literal, params = creation_properties(props, "stream")
        try:
            self.database.query(
                f"CREATE (s:RemediationStream {literal}) RETURN s.id",
                self.params({**params, "id": props["id"]}),
                idempotent=True,
            )
        except GraphConflict:
            pass
        return self.read(
            "MATCH (s:RemediationStream {tenant_id: $tenant_id, id: $id}) RETURN properties(s)",
            {"id": props["id"]},
        )[0][0]

    def proposal(self, proposal_id):
        rows = self.read(
            "MATCH (p:RemediationProposal {tenant_id: $tenant_id, space_id: $space_id, id: $id}) "
            "RETURN properties(p)",
            {"id": proposal_id},
        )
        if not rows:
            raise GraphNotFound()
        return rows[0][0]

    def proposals(self, limit=50):
        rows = self.read(
            "MATCH (p:RemediationProposal {tenant_id: $tenant_id, space_id: $space_id}) "
            "RETURN properties(p) ORDER BY p.created_at,p.id LIMIT $limit",
            {"limit": limit + 1},
        )
        if len(rows) > limit:
            raise RemediationLimit()
        return [row[0] for row in rows]

    def links(self, proposal_id, label):
        if label not in {"RemediationSimulation", "RemediationReview"}:
            raise ValueError("Invalid diagnostic profile")
        rows = self.read(
            f"MATCH path=(d:{label})-[:DERIVED_FROM]->"
            "(p:RemediationProposal {id: $id, space_id: $space_id}) "
            "WHERE all(n IN nodes(path) WHERE n.tenant_id = $tenant_id "
            "AND n.space_id = $space_id) "
            "AND all(r IN relationships(path) WHERE r.tenant_id = $tenant_id) "
            "RETURN properties(d) ORDER BY d.created_at,d.id LIMIT 101",
            {"id": proposal_id},
        )
        if len(rows) > 100:
            raise RemediationLimit()
        return [row[0] for row in rows]

    def saved_simulation(self, simulation_id):
        rows = self.read(
            "MATCH (s:RemediationSimulation {tenant_id: $tenant_id, space_id: $space_id, id: $id}) "
            "RETURN properties(s)",
            {"id": simulation_id},
        )
        if not rows:
            raise GraphNotFound()
        node = rows[0][0]
        ids = json.loads(node["chunk_ids_json"])
        rows = self.read(
            "MATCH (e:Evidence {tenant_id: $tenant_id}) WHERE e.id IN $ids "
            "RETURN e.id,e.record_json,e.content_sha256",
            {"ids": ids},
        )
        chunks = {row[0]: row[1] for row in rows}
        if len(chunks) != len(ids) or any(
            fingerprint({"chunk": content}) != digest for _, content, digest in rows
        ):
            raise RemediationConflict()
        payload = json.loads("".join(chunks[key] for key in ids))
        if fingerprint(payload) != node["result_sha256"]:
            raise RemediationConflict()
        return payload

    def simulation_payload(self, mutation, payload):
        encoded = json.dumps(payload, sort_keys=True)
        if len(encoded.encode()) > 1_000_000:
            raise RemediationLimit()
        # Immutable payload chunks stay graph-grounded; never written to runtime files.
        chunks = []
        for index, start in enumerate(range(0, len(encoded), 8000)):
            text = encoded[start : start + 8000]
            variable = "chunk" + str(index)
            node = mutation.node(
                N.EVIDENCE,
                mutation.operation_id + "/" + variable,
                {
                    "evidence_type": "remediation-simulation-chunk",
                    "record_json": text,
                    "content_sha256": fingerprint({"chunk": text}),
                },
                variable,
            )
            mutation.edge(
                node,
                {"id": payload["simulation_id"], "kind": "Decision"},
                R.SUPPORTS,
                variable,
                "simulation",
            )
            chunks.append(node["id"])
        return chunks

    def commit(self, mutation, stream, *, proposal=None):
        query = (
            "MATCH (stream:RemediationStream {tenant_id: $tenant_id, id: $stream_id}) "
            "SET stream.lock_version = stream.lock_version + 1 WITH stream "
            "WHERE stream.revision = $stream_revision "
            "MATCH (actor:Agent {tenant_id: $tenant_id, id: $actor_id})"
            "-[m:MEMBER_OF]->(space:MemorySpace {tenant_id: $tenant_id, id: $space_id}) "
            "WHERE m.tenant_id = $tenant_id AND m.verification_status = 'verified' "
            "AND m.role IN ['writer','curator'] AND m.valid_from <= $auth_now "
            "AND (m.valid_to IS NULL OR $auth_now < m.valid_to) "
        )
        if proposal:
            query += (
                "MATCH (proposal:RemediationProposal {tenant_id: $tenant_id, space_id: $space_id, "
                "id: $cas_proposal_id}) WHERE proposal.revision = $cas_proposal_revision "
            )
        return bool(
            self.database.mutate(
                query
                + "".join(mutation.matches + mutation.changes)
                + "SET stream.revision = stream.revision + 1, stream.updated_at = $now "
                "RETURN stream.id",
                self.params(
                    {
                        **mutation.params,
                        "stream_id": stream["id"],
                        "stream_revision": stream["revision"],
                        "actor_id": self.actor_id,
                        "auth_now": epoch_us(utc_now()),
                        "now": mutation.now,
                        "cas_proposal_id": proposal["id"] if proposal else None,
                        "cas_proposal_revision": proposal["revision"] if proposal else None,
                    }
                ),
                idempotent=True,
                tenant_id=self.scope.tenant_id,
                event_type="remediation.changed",
                entity_ids=[proposal["id"]] if proposal else [],
            )
        )

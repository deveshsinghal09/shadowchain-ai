from app.core.time import utc_now
from app.db.cypher import fingerprint
from app.models.graph import NodeInput, NodeKind, Provenance, TenantScope
from app.models.memory import Audience, MemoryRole, MemoryType, MemoryWrite
from app.repositories.graph import GraphRepository
from app.repositories.memory import MemoryRepository
from app.services.memory import MemoryService
from app.services.memory_access import MemoryAccessAdmin


class MemoryWorld:
    """Fixture builders only; all resulting access/memory state is written to FalkorDB."""

    def __init__(self, database, tenant="memory-tests"):
        self.database, self.scope = database, TenantScope(tenant_id=tenant)
        self.graph = GraphRepository(database, self.scope)
        self.admin = MemoryAccessAdmin(database, self.scope)
        self.space = self.admin.create_space("security", "SYNTHETIC security memory")
        self.writer = self.node("writer", NodeKind.AGENT, name="SYNTHETIC scout")
        self.peer = self.node("peer", NodeKind.AGENT, name="SYNTHETIC verifier")
        self.curator = self.node("curator", NodeKind.AGENT, name="SYNTHETIC curator")
        self.reader = self.node("reader", NodeKind.AGENT, name="SYNTHETIC analyst")
        self.outsider = self.node("outsider", NodeKind.AGENT, name="SYNTHETIC outsider")
        for agent, role in (
            (self.writer, MemoryRole.WRITER),
            (self.peer, MemoryRole.WRITER),
            (self.curator, MemoryRole.CURATOR),
            (self.reader, MemoryRole.READER),
        ):
            self.admin.grant_member(self.space, agent.id, role, "member/" + agent.id)
        self.resource = self.node(
            "bucket", NodeKind.RESOURCE, sensitive=True, display_name="SYNTHETIC bucket"
        )
        self.investigation = self.node(
            "incident", NodeKind.INVESTIGATION, title="SYNTHETIC INC-401", status="open"
        )
        self.evidence = self.node(
            "evidence",
            NodeKind.EVIDENCE,
            evidence_type="synthetic-policy-observation",
            summary="SYNTHETIC source observation",
            content_sha256=fingerprint({"public": False}),
        )
        for source in (self.resource, self.investigation, self.evidence):
            self.admin.grant_context(self.space, source.ref, "context/" + source.id)

    def provenance(self, key):
        return Provenance(
            source="synthetic.memory-tests",
            source_id=key,
            observed_at=utc_now(),
            collector="pytest",
            source_uri="urn:synthetic:memory:" + key,
        )

    def node(self, key, kind, **attributes):
        return self.graph.upsert_node(
            NodeInput(
                kind=kind,
                account_id="000000000001",
                provenance=self.provenance(key),
                attributes=attributes,
            )
        )

    def service(self, agent=None):
        return MemoryService(
            MemoryRepository(self.database, self.scope, self.space, (agent or self.writer).id)
        )

    def request(self, key, **overrides):
        values = dict(
            request_id=key,
            memory_key=key,
            memory_type=MemoryType.EPISODIC,
            content="SYNTHETIC investigation observation",
            provenance=self.provenance(key),
            subject_ids=[self.resource.id, self.investigation.id],
            evidence_ids=[self.evidence.id],
            audience=Audience.TEAM,
        )
        return MemoryWrite(**(values | overrides))

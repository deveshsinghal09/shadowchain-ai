"""Trusted, explicitly synthetic bootstrap. Not exposed as an agent or HTTP admin operation."""

from app.core.time import utc_now
from app.db.cypher import stable_id
from app.models.events import DEMO_TENANT, SOURCE, Scenario
from app.models.graph import NodeInput, NodeKind, Provenance, TenantScope
from app.models.memory import MemoryRole
from app.repositories.demo import DemoRepository
from app.repositories.graph import GraphRepository
from app.services.events import EventIngestionService
from app.services.memory_access import MemoryAccessAdmin


def setup_memory_demo(database) -> dict:
    scope = TenantScope(tenant_id=DEMO_TENANT)
    events = EventIngestionService(DemoRepository(database, scope))
    events.seed()
    state = events.status(Scenario.C)
    events.replay(Scenario.C, state.generation)
    admin = MemoryAccessAdmin(database, scope)
    space_id = admin.create_space("security-demo", "SYNTHETIC security investigation memory")
    scout = stable_id(DEMO_TENANT, "Agent", "", SOURCE, "agent/scout")
    verifier = stable_id(DEMO_TENANT, "Agent", "", SOURCE, "agent/verifier")
    admin.grant_member(space_id, scout, MemoryRole.WRITER, "demo-member/scout")
    admin.grant_member(space_id, verifier, MemoryRole.CURATOR, "demo-member/verifier")
    graph = GraphRepository(database, scope)
    sources = []
    concept = graph.upsert_node(
        NodeInput(
            kind=NodeKind.SECURITY_CONCEPT,
            provenance=Provenance(
                source="shadowchain.memory.demo",
                source_id="concept/least-privilege",
                observed_at=utc_now(),
                collector="synthetic-demo",
                source_uri="urn:shadowchain:synthetic:concept:least-privilege",
            ),
            attributes={
                "name": "SYNTHETIC least-privilege concept",
                "description": "Grant only the permissions required for a specific task",
                "category": "security-principle",
            },
        )
    )
    admin.grant_context(space_id, concept.ref, "demo-context/" + concept.id)
    sources.append(concept)
    for kind in (
        NodeKind.IDENTITY,
        NodeKind.ROLE,
        NodeKind.RESOURCE,
        NodeKind.SECURITY_EVENT,
        NodeKind.EVIDENCE,
        NodeKind.INVESTIGATION,
        NodeKind.PROCEDURE,
        NodeKind.RISK_FINDING,
    ):
        for record in graph.list_nodes(kind, limit=1000):
            if record.provenance.source != SOURCE:
                continue
            admin.grant_context(space_id, record.ref, "demo-context/" + record.id)
            sources.append(record)
    return {
        "scope": scope,
        "space_id": space_id,
        "scout": scout,
        "verifier": verifier,
        "sources": sources,
    }

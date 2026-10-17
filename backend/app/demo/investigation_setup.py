"""Original synthetic historical context; no demo vulnerability verdict is seeded here."""

from app.db.cypher import fingerprint
from app.demo.dataset import HISTORY_TIME
from app.models.events import DEMO_TENANT, Scenario
from app.models.graph import NodeInput, NodeRef, Provenance, RelationshipInput, TenantScope
from app.models.graph import NodeKind as N
from app.models.graph import RelationshipKind as R
from app.models.memory import (
    Audience,
    MemoryClaim,
    MemoryRole,
    MemoryType,
    MemoryWrite,
    ProcedurePlan,
    ReadOnlyStep,
    ReviewMemory,
)
from app.models.workflow import AgentKey, TriggerInvestigation
from app.repositories.demo import DemoRepository
from app.repositories.graph import GraphRepository
from app.repositories.investigation import InvestigationRepository
from app.services.events import EventIngestionService
from app.services.investigation import InvestigationService
from app.services.memory_access import MemoryAccessAdmin
from app.services.workflow import InvestigationWorkflow

SOURCE = "shadowchain.investigations.demo"


def setup_investigation_demo(database, settings):
    scope = TenantScope(tenant_id=DEMO_TENANT)
    events = EventIngestionService(DemoRepository(database, scope))
    events.seed()
    for scenario in (Scenario.A, Scenario.C):
        state = events.status(scenario)
        events.replay(scenario, state.generation)
    repo = InvestigationRepository(database, scope, settings)
    workflow = InvestigationWorkflow(repo)
    space_id = workflow.bootstrap()
    graph = GraphRepository(database, scope)
    sources = {
        n[0]["source_id"]: n[0]
        for n in repo.read(
            "MATCH (n:Entity {tenant_id: $tenant_id}) "
            "WHERE n.source_id IN ['resource/customer-data', 'identity/alice', "
            "'c/evidence/acl', 'c/evidence/policy'] "
            "RETURN properties(n)"
        )
    }
    resource = sources["resource/customer-data"]
    # IAM identity fixture uses the existing inventory rather than invented access.
    identities = repo.read(
        "MATCH (i:Identity {tenant_id: $tenant_id}) "
        "RETURN properties(i) ORDER BY i.source_id LIMIT 1"
    )
    identity = identities[0][0]

    def provenance(key):
        return Provenance(
            source=SOURCE,
            source_id=key,
            observed_at=HISTORY_TIME,
            collector="synthetic-investigation-demo",
            source_uri="urn:synthetic:investigation:" + key,
        )

    previous = graph.upsert_node(
        NodeInput(
            kind=N.INVESTIGATION,
            provenance=provenance("previous-incident"),
            attributes={
                "title": "SYNTHETIC earlier temporary-role review",
                "status": "RESOLVED",
                "synthetic": True,
                "space_id": space_id,
                "summary": "Earlier evidence review involving the same sensitive bucket",
            },
        )
    )
    past_evidence = graph.upsert_node(
        NodeInput(
            kind=N.EVIDENCE,
            provenance=provenance("historical-evidence"),
            attributes={
                "evidence_type": "synthetic-investigation-record",
                "summary": "SYNTHETIC earlier investigation context",
                "content_sha256": fingerprint({"synthetic": "previous-review"}),
            },
        )
    )
    for target in (resource, identity):
        graph.create_relationship(
            RelationshipInput(
                kind=R.INVESTIGATES,
                source=previous.ref,
                target=NodeRef(kind=target["kind"], id=target["id"]),
                provenance=provenance("previous-subject/" + target["id"]),
                valid_from=HISTORY_TIME,
                verification_status="verified",
            )
        )
    admin = MemoryAccessAdmin(database, scope)
    for source in (resource, identity, sources["c/evidence/acl"], sources["c/evidence/policy"]):
        admin.grant_context(
            space_id,
            NodeRef(kind=source["kind"], id=source["id"]),
            "case-demo/context/" + source["id"],
        )
    for node in (previous, past_evidence):
        admin.grant_context(space_id, node.ref, "case-demo/context/" + node.id)
    coordinator = repo.agent_id(AgentKey.COORDINATOR)
    admin.grant_member(space_id, coordinator, MemoryRole.CURATOR, "case-demo/reviewer")
    # Curator permission is explicit trusted demo bootstrap, never an HTTP/model self-grant.
    keeper = InvestigationService(repo, repo.agent_id(AgentKey.MEMORY_KEEPER))
    response = InvestigationService(repo, repo.agent_id(AgentKey.RESPONSE_ARCHITECT))
    pseudo = {"space_id": space_id}
    episode = keeper.memory(pseudo).write_memory(
        MemoryWrite(
            request_id="case-demo/previous-episode",
            memory_key="case-demo/previous-episode",
            memory_type=MemoryType.EPISODIC,
            audience=Audience.TEAM,
            content="SYNTHETIC previous incident: verify trust and policy context "
            "before concluding access",
            subject_ids=[resource["id"], previous.id],
            evidence_ids=[past_evidence.id],
            provenance=provenance("episode"),
        )
    )
    procedure = keeper.memory(pseudo).write_memory(
        MemoryWrite(
            request_id="case-demo/previous-procedure",
            memory_key="case-demo/previous-procedure",
            memory_type=MemoryType.PROCEDURAL,
            audience=Audience.TEAM,
            content="SYNTHETIC successful historical review procedure",
            subject_ids=[resource["id"], previous.id],
            evidence_ids=[past_evidence.id],
            provenance=provenance("procedure"),
            procedure=ProcedurePlan(
                steps=[
                    ReadOnlyStep.COLLECT_GRAPH_EVIDENCE,
                    ReadOnlyStep.QUERY_HISTORICAL_PATHS,
                    ReadOnlyStep.COMPARE_RISK_SNAPSHOTS,
                ],
                outcome="successful",
                lesson="Use temporal evidence and what-if proposals",
            ),
        )
    )
    InvestigationService(repo, coordinator).memory(pseudo).review_memory(
        ReviewMemory(
            request_id="case-demo/review-procedure",
            memory_id=procedure.id,
            expected_version=1,
            verification_status="verified",
            reason="SYNTHETIC curator validates the closed read-only steps",
        )
    )
    claims = []
    for service, key, value, source in [
        (keeper, "earlier-acl", True, "c/evidence/acl"),
        (response, "newer-policy", False, "c/evidence/policy"),
    ]:
        claims.append(
            service.memory(pseudo).write_memory(
                MemoryWrite(
                    request_id="case-demo/" + key,
                    memory_key="case-demo/" + key,
                    memory_type=MemoryType.SEMANTIC,
                    audience=Audience.TEAM,
                    content="SYNTHETIC conflicting resource observation: " + key,
                    subject_ids=[resource["id"]],
                    evidence_ids=[sources[source]["id"]],
                    provenance=provenance(key),
                    claim=MemoryClaim(
                        subject_id=resource["id"], predicate="s3.public_access", value=value
                    ),
                )
            )
        )
    event = repo.read(
        "MATCH (e:SecurityEvent {tenant_id: $tenant_id, scenario_id: 'A'}) "
        "RETURN e.id ORDER BY e.sequence DESC LIMIT 1"
    )[0][0]
    return {
        "repository": repo,
        "workflow": workflow,
        "space_id": space_id,
        "previous_incident_id": previous.id,
        "episodic_memory_id": episode.id,
        "procedural_memory_id": procedure.id,
        "claim_ids": [c.id for c in claims],
        "contradictory_evidence_id": sources["c/evidence/policy"]["id"],
        "request": TriggerInvestigation(request_id="demo/phase7/investigation", event_id=event),
    }

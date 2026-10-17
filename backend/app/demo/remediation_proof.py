"""Synthetic Phase 8 extension and persistent simulator proof; no AWS client exists here."""

import json

from app.core.config import get_settings
from app.db.cypher import fingerprint
from app.db.falkordb import GraphDatabase
from app.demo.dataset import hour
from app.models.events import DEMO_TENANT, EdgeSpec, NodeSpec, Scenario, SyntheticSecurityEvent
from app.models.graph import NodeKind as N
from app.models.graph import RelationshipKind as R
from app.models.graph import TenantScope
from app.models.remediation import (
    ProposalReview,
    ProposeRequest,
    RecommendationKind,
    SimulationRequest,
)
from app.models.workflow import AgentKey
from app.repositories.demo import DemoRepository
from app.repositories.remediation import RemediationRepository
from app.repositories.workflow import WorkflowRepository
from app.schemas.risk import AnalysisRequest
from app.services.events import EventIngestionService
from app.services.remediation import RemediationService
from app.services.workflow import InvestigationWorkflow


def setup_demo(database, settings):
    scope = TenantScope(tenant_id=DEMO_TENANT)
    demo = DemoRepository(database, scope)
    events = EventIngestionService(demo)
    events.seed()
    for scenario in [Scenario.A, Scenario.B]:
        state = events.status(scenario)
        events.replay(scenario, state.generation)
    extension = SyntheticSecurityEvent(
        scenario=Scenario.A,
        source_id="a/remediation-2026.1/alternate-grant",
        account_id="000000000002",
        sequence=4,
        occurred_at=hour(13),
        event_type="SyntheticRemediationAlternativeGrant",
        nodes=[
            NodeSpec(
                key="a/remediation-alternate-permission",
                kind=N.PERMISSION,
                account_id="000000000002",
                attributes={"action": "s3:GetObject", "effect": "Allow"},
            ),
        ],
        edges=[
            EdgeSpec(
                key="a/remediation-alternate-grant",
                kind=R.GRANTS,
                source_key="policy/sensitive-read",
                target_key="a/remediation-alternate-permission",
            ),
            EdgeSpec(
                key="a/remediation-alternate-target",
                kind=R.TARGETS,
                source_key="a/remediation-alternate-permission",
                target_key="resource/hr-data",
            ),
        ],
    )
    events.ingest(extension, events.status(Scenario.A).generation)
    workflow = InvestigationWorkflow(WorkflowRepository(database, scope, settings))
    space = workflow.bootstrap()
    repo = RemediationRepository(
        database, scope, settings, space, workflow.repository.agent_id(AgentKey.COORDINATOR)
    )
    return RemediationService(repo, human_reviewer_id="synthetic-explicit-human-reviewer"), demo


def projection_digest(demo):
    _, nodes, edges = demo.graph()
    return fingerprint(
        {
            "nodes": sorted(nodes, key=lambda n: n["id"]),
            "edges": sorted(edges, key=lambda e: e["id"]),
        }
    )


def run_proof(database, settings):
    service, demo = setup_demo(database, settings)
    original = projection_digest(demo)
    _, _, edges = demo.graph()
    ids = {e["edge_key"]: e["id"] for e in edges}
    analysis = AnalysisRequest(at=hour(13), scenario=Scenario.A)
    first = service.propose(
        ProposeRequest(
            request_id="demo/phase8/first",
            analysis=analysis,
            relationship_id=ids["grant/sensitive-read/objects"],
            category=RecommendationKind.EXCESSIVE_PERMISSION,
            reason="SYNTHETIC owner reviews original sensitive read grant",
        )
    )
    alternate = service.propose(
        ProposeRequest(
            request_id="demo/phase8/alternate",
            analysis=analysis,
            relationship_id=ids["a/remediation-alternate-grant"],
            category=RecommendationKind.EXCESSIVE_PERMISSION,
            reason="SYNTHETIC owner reviews the independently modeled alternate grant",
        )
    )
    single = service.simulate(first.proposal_id, SimulationRequest(request_id="demo/phase8/single"))
    combined_request = SimulationRequest(
        request_id="demo/phase8/combined", additional_proposal_ids=[alternate.proposal_id]
    )
    combined = service.simulate(first.proposal_id, combined_request)
    if first.status == "PROPOSED":
        first = service.review(
            first.proposal_id,
            ProposalReview(
                request_id="demo/phase8/review",
                expected_revision=first.revision,
                status="UNDER_REVIEW",
                reason="SYNTHETIC human inspects remaining paths and possible workload disruption",
            ),
        )
    if first.status == "UNDER_REVIEW":
        first = service.review(
            first.proposal_id,
            ProposalReview(
                request_id="demo/phase8/approve",
                expected_revision=first.revision,
                status="APPROVED",
                simulation_id=single.simulation_id,
                reason="SYNTHETIC approve the recommendation only; "
                "no AWS change is authorized or executed",
            ),
        )
    if (
        len(single.before.findings) != 2
        or len(single.after.findings) != 1
        or not single.alternate_paths
        or combined.after.findings
        or first.status != "APPROVED"
        or projection_digest(demo) != original
    ):
        raise RuntimeError("Synthetic remediation proof assertions failed")
    if combined != service.simulate(first.proposal_id, combined_request):
        raise RuntimeError("Simulation repeat changed persisted result")
    database.reconnect()
    if (
        service.saved_simulation(single.simulation_id) != single
        or projection_digest(demo) != original
    ):
        raise RuntimeError("Simulation or original graph changed after reconnect")
    return {
        "synthetic": True,
        "verified": True,
        "extension_version": "remediation-2026.1",
        "proposal_id": first.proposal_id,
        "alternate_proposal_id": alternate.proposal_id,
        "review_status": first.status.value,
        "approval_scope": first.approval_scope,
        "single_simulation_id": single.simulation_id,
        "combined_simulation_id": combined.simulation_id,
        "before_potential_paths": len(single.before.findings),
        "after_single_removal_paths": len(single.after.findings),
        "alternate_path_ids": single.alternate_paths,
        "after_combined_removal_paths": len(combined.after.findings),
        "potential_collateral_access": [
            item.model_dump(mode="json") for item in single.access_impact
        ],
        "risk_difference": single.risk_difference.model_dump(mode="json"),
        "historical_evidence_ids": single.historical_evidence_ids,
        "source_projection_sha256": original,
        "source_permissions_unchanged": True,
        "repeatable": True,
        "reconnect_preserved_simulations": True,
        "aws_write_api_calls": 0,
        "execution_allowed": False,
    }


def main():
    settings = get_settings()
    if not settings.demo_mode or settings.app_env == "production":
        raise RuntimeError("Synthetic remediation proof requires local demo mode")
    database = GraphDatabase(settings)
    try:
        database.initialize_schema()
        print(json.dumps(run_proof(database, settings), indent=2))
    finally:
        database.close()


if __name__ == "__main__":
    main()

"""Original synthetic investigation and restart preparation; no local state authority."""

import argparse
import json

from app.core.config import get_settings
from app.db.falkordb import GraphDatabase
from app.demo.investigation_setup import setup_investigation_demo
from app.models.graph import RelationshipKind
from app.models.investigation import ReviewCaseEvidence, ReviewHypothesis, TransitionCase
from app.models.workflow import STAGES, AgentKey
from app.repositories.investigation import InvestigationRepository
from app.services.investigation import InvestigationService


def prepare_demo(database, settings):
    database.initialize_schema()
    setup = setup_investigation_demo(database, settings)
    repo, workflow = setup["repository"], setup["workflow"]
    workflow_id = repo.create(setup["request"], setup["space_id"])
    for stage in STAGES[:5]:
        workflow.step(workflow_id, stage)
    case_id = repo.workflow_cases(workflow_id)[0]
    coordinator = InvestigationService(repo, repo.agent_id(AgentKey.COORDINATOR))
    return setup, workflow_id, case_id, coordinator


def run_proof(database, settings):
    setup, workflow_id, case_id, service = prepare_demo(database, settings)
    setup["workflow"].resume(workflow_id)
    repo = setup["repository"]
    detail = service.detail(case_id)
    # Explicit synthetic analyst review; data remains a potential path, no AWS change is asserted.
    review_key = "proof/contradictory-observation"
    exists = repo.read(
        "MATCH (d:Decision {tenant_id: $tenant_id, case_id: $id, "
        "action: 'case.evidence_review'}) RETURN d.id",
        {"id": case_id},
    )
    if not exists and detail.status == "AWAITING_REVIEW":
        # Attach the graph-grounded opposing policy observation through its actual visible memory.
        node = repo.node(setup["contradictory_evidence_id"])

        def attach(case, mutation):
            mutation.bind(node, "source")
            mutation.edge(node, case, RelationshipKind.CONTRADICTS, "source", "investigation")
            return (
                {
                    "evidence_id": node["id"],
                    "reason": "Conflicting security memory requires review",
                },
                {"case_id": case_id},
                {},
            )

        service._mutate(
            case_id, "memory_evidence", "proof/opposing-policy", {"evidence_id": node["id"]}, attach
        )
        detail = service.detail(case_id)
        service.review_evidence(
            case_id,
            ReviewCaseEvidence(
                request_id=review_key,
                expected_revision=detail.revision,
                evidence_id=node["id"],
                verification_status="verified",
                polarity="contradicting",
                reason="SYNTHETIC reviewer retains newer policy evidence "
                "disputing the older ACL interpretation",
            ),
        )
        detail = service.detail(case_id)
        service.review_hypothesis(
            case_id,
            ReviewHypothesis(
                request_id="proof/hypothesis",
                expected_revision=detail.revision,
                hypothesis_id=detail.current_hypotheses[0].id,
                conclusion="unresolved",
                evidence_ids=[detail.verified_evidence[0].id],
                reason="Integrity-checked evidence supports investigation; "
                "exploitation remains unproven",
            ),
        )
    detail = service.detail(case_id)
    if detail.status == "AWAITING_REVIEW":
        resolved = service.transition(
            case_id,
            TransitionCase(
                request_id="proof/accept-risk",
                expected_revision=detail.revision,
                status="RESOLVED",
                resolution="accepted_risk",
                reason="SYNTHETIC analyst explicitly accepts the modeled risk and "
                "retained uncertainty; no remediation is claimed",
            ),
        )
        service.transition(
            case_id,
            TransitionCase(
                request_id="proof/close",
                expected_revision=resolved["revision"],
                status="CLOSED",
                reason="Close the reviewed synthetic incident "
                "while retaining evidence and audit history",
            ),
        )
    detail = service.detail(case_id)
    checks = service._tasks(case_id)
    nodes = service._nodes(case_id)
    handoff_id = next(
        n["id"] for n in nodes.values() if n["kind"] == "Handoff" and n.get("case_id")
    )
    handoff = service.handoff(case_id, handoff_id)
    assertions = [
        detail.status == "CLOSED",
        detail.resolution == "accepted_risk",
        all(t["status"] == "completed" for t in checks),
        len(checks) == 4,
        checks[0]["agent_id"] == repo.agent_id(AgentKey.MEMORY_KEEPER),
        checks[1]["agent_id"] == repo.agent_id(AgentKey.MEMORY_KEEPER),
        checks[2]["agent_id"] == repo.agent_id(AgentKey.RESPONSE_ARCHITECT),
        checks[3]["agent_id"] == repo.agent_id(AgentKey.RESPONSE_ARCHITECT),
        len(handoff.checkpoint["completed_task_ids"])
        == len(handoff.checkpoint["pending_task_ids"])
        == 2,
        setup["previous_incident_id"]
        in {p.investigation_id for p in detail.linked_previous_incidents},
        bool(detail.reconstruction.useful_historical_checks),
        bool(detail.contradictory_evidence),
        bool(detail.verified_evidence),
        not detail.pending_tasks,
        bool(detail.recommended_response),
    ]
    if not all(assertions):
        raise RuntimeError("Synthetic investigation proof did not satisfy graph assertions")
    database.reconnect()
    restored = InvestigationService(
        InvestigationRepository(database, repo.scope, settings), service.actor
    )
    if restored.detail(case_id) != detail or restored.handoff(case_id, handoff_id) != handoff:
        raise RuntimeError("Investigation evidence or handoff changed after reconnect")
    return {
        "synthetic": True,
        "verified": True,
        "investigation_id": case_id,
        "workflow_id": workflow_id,
        "status": detail.status.value,
        "resolution": detail.resolution,
        "severity": detail.severity,
        "completed_tasks": len(detail.completed_tasks),
        "pending_tasks": len(detail.pending_tasks),
        "agent_a_id": checks[0]["agent_id"],
        "agent_b_id": checks[2]["agent_id"],
        "agent_a_completed_task_ids": [t["id"] for t in checks[:2]],
        "agent_b_completed_task_ids": [t["id"] for t in checks[2:]],
        "handoff_id": handoff_id,
        "verified_evidence_count": len(detail.verified_evidence),
        "contradictory_evidence_ids": [e.id for e in detail.contradictory_evidence],
        "previous_incident_ids": [p.investigation_id for p in detail.linked_previous_incidents],
        "historical_procedure_memory_ids": [
            p.memory_id for p in detail.reconstruction.useful_historical_checks
        ],
        "memory_references": len(detail.memory_references),
        "audit_entries": len(detail.audit_history),
        "timeline_entries": len(detail.historical_change_timeline),
        "reconnect_preserved_case": True,
        "recommended_response": detail.recommended_response,
        "aws_changes_executed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-restart", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.demo_mode or settings.app_env == "production":
        raise RuntimeError("Synthetic investigation proof requires local demo mode")
    database = GraphDatabase(settings)
    try:
        if args.prepare_restart:
            _, workflow_id, case_id, service = prepare_demo(database, settings)
            print(
                json.dumps(
                    {
                        "synthetic": True,
                        "workflow_id": workflow_id,
                        "investigation": service.detail(case_id).model_dump(mode="json"),
                    },
                    indent=2,
                )
            )
        else:
            print(json.dumps(run_proof(database, settings), indent=2))
    finally:
        database.close()


if __name__ == "__main__":
    main()

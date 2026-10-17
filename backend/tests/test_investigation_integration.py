from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from app.db.errors import GraphConflict, GraphNotFound
from app.db.falkordb import GraphDatabase
from app.demo.dataset import hour
from app.demo.investigation_setup import setup_investigation_demo
from app.models.events import Scenario
from app.models.graph import TenantScope
from app.models.investigation import ReviewCaseEvidence, TransferCase, TransitionCase
from app.models.workflow import STAGES, AgentKey
from app.repositories.investigation import InvestigationRepository
from app.schemas.risk import AnalysisRequest
from app.services.investigation import InvestigationService
from app.services.investigation_errors import CaseAccessDenied, InvalidCaseTransition
from app.services.memory_access import MemoryAccessAdmin
from app.services.memory_errors import MemoryAccessDenied
from app.services.risk import RiskEngine
from tests.workflow_helpers import workflow_world

pytestmark = pytest.mark.integration


def case_world(database, settings, *, complete=True):
    workflow, request = workflow_world(database, settings)
    repo = InvestigationRepository(database, workflow.repository.scope, settings)
    identifier = workflow.repository.create(request, workflow.bootstrap())
    for stage in STAGES[: 6 if complete else 3]:
        workflow.step(identifier, stage)
    case_id = repo.workflow_cases(identifier)[0]
    service = InvestigationService(repo, repo.agent_id(AgentKey.COORDINATOR))
    return workflow, identifier, service, case_id


def test_risk_oracle_automatically_creates_complete_case(live_database, isolated_graph_settings):
    workflow, identifier, service, case_id = case_world(live_database, isolated_graph_settings)
    case = service.detail(case_id)
    assert case.status == "AWAITING_REVIEW" and case.severity == "medium"
    assert (
        case.affected_identities and case.affected_resources and case.contributing_security_events
    )
    assert len(case.assigned_agents) == 6 and len(case.current_hypotheses) == 1
    assert case.current_hypotheses[0].conclusion == "unresolved"
    assert case.verified_evidence and all(
        e.verification_scope == "source_integrity" for e in case.verified_evidence
    )
    assert len(case.completed_tasks) == 10 and len(case.pending_tasks) == 1
    assert case.memory_references and case.audit_history and case.workflow_ids == [identifier]
    assert case.recommended_response[0]["after_paths"] == 0
    assert not case.recommended_response[0]["execution_allowed"]
    hours = {
        item.timestamp.hour
        for item in case.historical_change_timeline
        if item.category in {"security_event", "relationship_change"}
    }
    assert {9, 10, 11, 12} <= hours
    graph = service.graph(case_id)
    assert graph.nodes and graph.relationships and not graph.layout_coordinates_included
    assert all(
        n["properties"]["tenant_id"] == service.repository.scope.tenant_id for n in graph.nodes
    )
    before = service.repository.case(case_id)
    workflow.resume(identifier)
    assert service.repository.case(case_id) == before


def test_historical_reconstruction_is_graph_grounded_and_procedures_are_validated(
    live_database, isolated_graph_settings
):
    setup = setup_investigation_demo(live_database, isolated_graph_settings)
    workflow = setup["workflow"]
    result = workflow.trigger(setup["request"])
    repo = setup["repository"]
    case_id = repo.workflow_cases(result.id)[0]
    service = InvestigationService(repo, repo.agent_id(AgentKey.COORDINATOR))
    history = service.reconstruct(case_id)
    assert setup["previous_incident_id"] in {p.investigation_id for p in history.previous_incidents}
    assert setup["episodic_memory_id"] in history.episodic_memory_ids
    assert setup["procedural_memory_id"] in history.procedural_memory_ids
    assert setup["procedural_memory_id"] in {p.memory_id for p in history.useful_historical_checks}
    assert all(p.shared_asset_ids and 0 < p.similarity <= 1 for p in history.previous_incidents)
    assert all(not p.execution_allowed and p.evidence_ids for p in history.useful_historical_checks)
    assert all(s.record_ids for s in history.summary)
    tasks = service._tasks(case_id)
    conflict_check = next(t for t in tasks if t["check_key"] == "review_memory_conflicts")
    assert '"requires_analyst_review": true' in conflict_check["output_json"]


def test_lifecycle_invalid_stale_and_duplicate_transitions(live_database, isolated_graph_settings):
    _, _, service, identifier = case_world(live_database, isolated_graph_settings, complete=False)
    case = service.repository.case(identifier)
    with pytest.raises(InvalidCaseTransition):
        service.transition(
            identifier,
            TransitionCase(
                request_id="invalid",
                expected_revision=case["revision"],
                status="CLOSED",
                reason="Skip lifecycle",
            ),
        )
    request = TransitionCase(
        request_id="triage", expected_revision=case["revision"], status="TRIAGING", reason="Triage"
    )
    triaged = service.transition(identifier, request)
    assert service.transition(identifier, request) == triaged
    with pytest.raises(GraphConflict):
        service.transition(identifier, request.model_copy(update={"reason": "Different payload"}))
    with pytest.raises(InvalidCaseTransition):
        service.transition(
            identifier,
            TransitionCase(
                request_id="stale",
                expected_revision=case["revision"],
                status="INVESTIGATING",
                reason="Stale write",
            ),
        )


def test_case_resolution_and_closure_preserve_audit_and_do_not_modify_aws(
    live_database, isolated_graph_settings
):
    _, _, service, identifier = case_world(live_database, isolated_graph_settings)
    before = service.detail(identifier)
    resolved = service.transition(
        identifier,
        TransitionCase(
            request_id="accept",
            expected_revision=before.revision,
            status="RESOLVED",
            resolution="accepted_risk",
            reason="SYNTHETIC analyst accepts modeled risk pending a later change",
        ),
    )
    assert resolved["status"] == "RESOLVED"
    assert not service.detail(identifier).pending_tasks
    closed = service.transition(
        identifier,
        TransitionCase(
            request_id="close",
            expected_revision=resolved["revision"],
            status="CLOSED",
            reason="Record retained after analyst review",
        ),
    )
    after = service.detail(identifier)
    assert after.status == "CLOSED" and len(after.audit_history) == len(before.audit_history) + 2
    assert after.current_hypotheses == before.current_hypotheses
    with pytest.raises(InvalidCaseTransition):
        service.transition(
            identifier,
            TransitionCase(
                request_id="reopen-closed",
                expected_revision=closed["revision"],
                status="INVESTIGATING",
                reason="Closed is terminal",
            ),
        )


def test_current_path_updates_existing_case_and_new_detection_after_closure_creates_episode(
    live_database, isolated_graph_settings
):
    workflow, identifier, service, case_id = case_world(live_database, isolated_graph_settings)
    risk = workflow.agents.risk_repository
    newer = RiskEngine(risk).discover(AnalysisRequest(at=hour(13), scenario=Scenario.A))
    current = service.repository.case(case_id)
    found = service.repository.observe(
        newer.findings[0],
        newer,
        current["space_id"],
        risk.evidence(newer.findings[0].finding_id).evidence,
        identifier,
    )
    assert found == case_id and len(service.repository.cases()) == 1
    current = service.repository.case(case_id)
    resolved = service.transition(
        case_id,
        TransitionCase(
            request_id="accept",
            expected_revision=current["revision"],
            status="RESOLVED",
            resolution="accepted_risk",
            reason="SYNTHETIC accepted risk",
        ),
    )
    service.transition(
        case_id,
        TransitionCase(
            request_id="close",
            expected_revision=resolved["revision"],
            status="CLOSED",
            reason="Retain audit",
        ),
    )
    latest = RiskEngine(risk).discover(AnalysisRequest(at=hour(14), scenario=Scenario.A))
    fresh = service.repository.observe(
        latest.findings[0],
        latest,
        current["space_id"],
        risk.evidence(latest.findings[0].finding_id).evidence,
        identifier,
    )
    assert fresh != case_id and service.repository.case(fresh)["generation"] == 2
    assert service.repository.case(case_id)["status"] == "CLOSED"


def test_concurrent_detections_do_not_duplicate_cases(live_database, isolated_graph_settings):
    workflow, identifier, service, case_id = case_world(
        live_database, isolated_graph_settings, complete=False
    )
    risk = workflow.agents.risk_repository
    analysis = RiskEngine(risk).discover(AnalysisRequest(at=hour(13), scenario=Scenario.A))
    evidence = risk.evidence(analysis.findings[0].finding_id).evidence
    space = service.repository.case(case_id)["space_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        found = list(
            pool.map(
                lambda _: service.repository.observe(
                    analysis.findings[0], analysis, space, evidence, identifier
                ),
                range(2),
            )
        )
    assert found == [case_id, case_id] and len(service.repository.cases()) == 1


def test_cross_session_case_handoff_keeps_completed_tasks_and_decisions(
    live_database, isolated_graph_settings
):
    workflow, identifier, service, case_id = case_world(
        live_database, isolated_graph_settings, complete=False
    )
    workflow.step(identifier, AgentKey.MEMORY_KEEPER)
    before_tasks = service._tasks(case_id)
    assert sum(t["status"] == "completed" for t in before_tasks) == 2
    workflow.step(identifier, AgentKey.COORDINATOR)
    nodes = service._nodes(case_id)
    handoff_id = next(
        n["id"] for n in nodes.values() if n["kind"] == "Handoff" and n.get("case_id")
    )
    handoff = service.handoff(case_id, handoff_id)
    assert len(handoff.checkpoint["completed_task_ids"]) == 2 and handoff.checkpoint["decision_ids"]
    before_audits = {a.id for a in service.detail(case_id).audit_history}
    fresh_db = GraphDatabase(isolated_graph_settings)
    try:
        repo = InvestigationRepository(fresh_db, service.repository.scope, isolated_graph_settings)
        from app.services.workflow import InvestigationWorkflow

        fresh = InvestigationWorkflow(repo)
        fresh.resume(identifier)
        restored = InvestigationService(repo, repo.agent_id(AgentKey.RESPONSE_ARCHITECT))
        after_tasks = restored._tasks(case_id)
        assert after_tasks[:2] == before_tasks[:2]
        assert all(t["status"] == "completed" for t in after_tasks)
        assert all(
            t["agent_id"] == repo.agent_id(AgentKey.RESPONSE_ARCHITECT) for t in after_tasks[2:]
        )
        detail = restored.detail(case_id)
        assert detail.status == "AWAITING_REVIEW" and before_audits <= {
            a.id for a in detail.audit_history
        }
        assert restored.handoff(case_id, handoff_id).checkpoint == handoff.checkpoint
    finally:
        fresh_db.close()


def test_unauthorized_tenant_agent_and_revoked_membership_are_denied(
    live_database, isolated_graph_settings
):
    _, _, service, identifier = case_world(live_database, isolated_graph_settings)
    outsider = InvestigationRepository(
        live_database, TenantScope(tenant_id="other-tenant"), isolated_graph_settings
    )
    with pytest.raises(GraphNotFound):
        InvestigationService(outsider, service.actor).detail(identifier)
    assert outsider.cases() == []
    with pytest.raises((MemoryAccessDenied, CaseAccessDenied)) as denied:
        InvestigationService(service.repository, "00000000-0000-0000-0000-000000000001").detail(
            identifier
        )
    assert denied.value.status_code == 403
    case = service.repository.case(identifier)
    admin = MemoryAccessAdmin(live_database, service.repository.scope)
    admin.revoke_member(case["space_id"], service.actor, "case-test/revoke")
    with pytest.raises(MemoryAccessDenied) as revoked:
        service.detail(identifier)
    assert revoked.value.status_code == 403


def test_contradictory_evidence_requires_explicit_case_review(
    live_database, isolated_graph_settings
):
    _, _, service, identifier = case_world(live_database, isolated_graph_settings)
    case = service.detail(identifier)
    evidence = case.verified_evidence[0]
    reviewed = service.review_evidence(
        identifier,
        ReviewCaseEvidence(
            request_id="dispute",
            expected_revision=case.revision,
            evidence_id=evidence.id,
            verification_status="disputed",
            polarity="contradicting",
            reason="SYNTHETIC analyst disputes interpretation; source is retained",
        ),
    )
    assert reviewed.polarity == "contradicting" and reviewed.verification_status == "disputed"
    assert evidence.id in {e.id for e in service.detail(identifier).contradictory_evidence}
    assert service.repository.node(evidence.id)["content_sha256"] == evidence.content_sha256


def test_revocation_between_validation_and_commit_blocks_transition(
    live_database, isolated_graph_settings
):
    _, _, service, identifier = case_world(live_database, isolated_graph_settings, complete=False)
    repo = service.repository
    current = repo.case(identifier)
    original = repo.commit
    once = False

    def revoke(mutation, stream, case=None):
        nonlocal once
        if not once:
            once = True
            MemoryAccessAdmin(live_database, repo.scope).revoke_member(
                current["space_id"], service.actor, "race/revoke"
            )
        return original(mutation, stream, case)

    with patch.object(repo, "commit", side_effect=revoke):
        with pytest.raises((MemoryAccessDenied, InvalidCaseTransition)) as denied:
            service.transition(
                identifier,
                TransitionCase(
                    request_id="revoked",
                    expected_revision=current["revision"],
                    status="TRIAGING",
                    reason="Must be blocked at commit",
                ),
            )
        assert denied.value.status_code in {403, 409}
    assert repo.case(identifier)["status"] == "NEW"


def test_repeated_handoffs_preserve_completed_work_and_close_prior_assignments(
    live_database, isolated_graph_settings
):
    workflow, identifier, service, case_id = case_world(
        live_database, isolated_graph_settings, complete=False
    )
    workflow.step(identifier, AgentKey.MEMORY_KEEPER)
    completed_before = service._tasks(case_id)[:2]
    targets = [AgentKey.RESPONSE_ARCHITECT, AgentKey.MEMORY_KEEPER, AgentKey.RESPONSE_ARCHITECT]
    checkpoints = []
    for index, target in enumerate(targets):
        case = service.repository.case(case_id)
        request = TransferCase(
            request_id=f"handoff/{index}",
            expected_revision=case["revision"],
            recipient_agent_id=service.repository.agent_id(target),
            reason="SYNTHETIC change of shift using durable context",
        )
        handoff = service.transfer(case_id, request)
        assert service.transfer(case_id, request) == handoff
        checkpoints.append(handoff.checkpoint)
        assert service._tasks(case_id)[:2] == completed_before
    InvestigationService(
        service.repository, service.repository.agent_id(AgentKey.RESPONSE_ARCHITECT)
    ).resume_checks(case_id)
    assert all(t["status"] == "completed" for t in service._tasks(case_id))
    assert all(len(checkpoint["completed_task_ids"]) == 2 for checkpoint in checkpoints)
    pending_ids = checkpoints[-1]["pending_task_ids"]
    assignments = service.repository.read(
        "MATCH path=(t:Task)-[r:ASSIGNED_TO]->(a:Agent) "
        "WHERE t.id IN $ids AND all(n IN nodes(path) WHERE n.tenant_id = $tenant_id) "
        "AND all(e IN relationships(path) WHERE e.tenant_id = $tenant_id) "
        "RETURN t.id, r.valid_to, a.id",
        {"ids": pending_ids},
    )
    assert len(assignments) == 8
    for task_id in pending_ids:
        active = [row for row in assignments if row[0] == task_id and row[1] is None]
        assert len(active) == 1 and active[0][2] == service.repository.agent_id(targets[-1])


def test_synthetic_case_proof_is_idempotent(live_database, isolated_graph_settings):
    from app.demo.investigation_proof import run_proof

    first = run_proof(live_database, isolated_graph_settings)
    assert first == run_proof(live_database, isolated_graph_settings)
    assert first["status"] == "CLOSED" and first["pending_tasks"] == 0
    assert first["contradictory_evidence_ids"] and first["reconnect_preserved_case"]


def test_additional_workflow_for_same_analysis_links_to_existing_case(
    live_database, isolated_graph_settings
):
    workflow, identifier, service, case_id = case_world(live_database, isolated_graph_settings)
    from app.models.workflow import TriggerInvestigation

    request = TriggerInvestigation(
        request_id="another-investigation",
        event_id=workflow.repository.inspect(identifier).event_id,
    )
    case_checks = service._tasks(case_id)
    second = workflow.trigger(request)
    assert second.id != identifier and second.status == "completed"
    detail = service.detail(case_id)
    assert detail.workflow_ids == sorted([identifier, second.id])
    assert len(service.repository.cases()) == 1
    assert service._tasks(case_id) == case_checks


def test_reopened_review_cycle_does_not_repeat_checks(live_database, isolated_graph_settings):
    _, _, service, case_id = case_world(live_database, isolated_graph_settings)
    completed = service._tasks(case_id)
    owner = InvestigationService(service.repository, completed[-1]["agent_id"])
    for index in range(2):
        case = service.detail(case_id)
        service.transition(
            case_id,
            TransitionCase(
                request_id=f"more-review/{index}",
                expected_revision=case.revision,
                status="INVESTIGATING",
                reason="SYNTHETIC analyst requests an additional review cycle",
            ),
        )
        owner.resume_checks(case_id)
        assert service.detail(case_id).status == "AWAITING_REVIEW"
        assert service._tasks(case_id) == completed

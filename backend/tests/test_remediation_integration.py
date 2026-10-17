import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.db.cypher import fingerprint
from app.db.errors import GraphConflict, GraphNotFound
from app.db.falkordb import GraphDatabase
from app.demo.dataset import hour
from app.models.events import Scenario
from app.models.graph import NodeKind as N
from app.models.graph import NodeRef, TenantScope
from app.models.graph import RelationshipKind as R
from app.models.remediation import (
    ProposalReview,
    ProposeRequest,
    RecommendRequest,
    SimulationRequest,
)
from app.models.remediation import (
    RecommendationKind as K,
)
from app.models.workflow import AgentKey
from app.repositories.remediation import RemediationRepository
from app.repositories.workflow import WorkflowRepository
from app.schemas.risk import AnalysisRequest
from app.services.memory_access import MemoryAccessAdmin
from app.services.memory_errors import MemoryAccessDenied
from app.services.remediation import RemediationService
from app.services.remediation_errors import (
    InvalidRemediation,
    RemediationAccessDenied,
    RemediationConflict,
)
from app.services.workflow import InvestigationWorkflow
from tests.risk_helpers import PolicyGraph
from tests.workflow_helpers import workflow_world

pytestmark = pytest.mark.integration


def service_for(database, settings, scope):
    workflow = InvestigationWorkflow(WorkflowRepository(database, scope, settings))
    space = workflow.bootstrap()
    actor = workflow.repository.agent_id(AgentKey.COORDINATOR)
    return RemediationService(
        RemediationRepository(database, scope, settings, space, actor),
        human_reviewer_id="synthetic-explicit-human-review",
    )


def world(database, settings):
    graph = PolicyGraph(database)
    graph.resource = graph.node(
        "restricted",
        N.RESOURCE,
        sensitive=True,
        data_classification="restricted",
        arn="arn:aws:s3:::synthetic-remediation-restricted",
    )
    policy, permission, _, grant, _ = graph.policy(graph.identity, "shared", action="s3:*")
    ordinary = graph.node(
        "ordinary", N.RESOURCE, sensitive=False, arn="arn:aws:s3:::synthetic-remediation-ordinary"
    )
    graph.edge(R.TARGETS, permission, ordinary)
    colleague = graph.node("colleague", N.IDENTITY, display_name="SYNTHETIC second workload")
    graph.edge(R.HAS_POLICY, colleague, policy)
    alternate = graph.policy(graph.identity, "alternate")[3]
    exception = graph.node(
        "exception",
        N.EVIDENCE,
        evidence_type="policy-exception",
        content_sha256="b" * 64,
        status="verified",
        fact_value=True,
        summary="SYNTHETIC recorded broad-policy exception",
    )
    graph.edge(R.SUPPORTS, exception, graph.resource)
    scope = graph.engine.repository.scope
    service = service_for(database, settings, scope)
    request = ProposeRequest(
        request_id="first",
        analysis=AnalysisRequest(at=hour(12), identity_id=graph.identity.id),
        relationship_id=grant.id,
        category=K.EXCESSIVE_PERMISSION,
        reason="SYNTHETIC owner reviews excessive access",
    )
    return graph, service, request, alternate, ordinary, colleague


def cloud_snapshot(service):
    nodes = service.repository.read(
        "MATCH (n:Entity {tenant_id: $tenant_id}) "
        "WHERE n.kind IN ['Identity','Role','Policy','Permission','Resource','CloudAccount',"
        "'CloudOrganization','SecurityEvent'] RETURN properties(n) ORDER BY n.id"
    )
    edges = service.repository.read(
        "MATCH path=(a:Entity)-[e:CAN_ASSUME|HAS_POLICY|GRANTS|TARGETS|BELONGS_TO]->(b:Entity) "
        "WHERE all(n IN nodes(path) WHERE n.tenant_id = $tenant_id) "
        "AND all(r IN relationships(path) WHERE r.tenant_id = $tenant_id) "
        "AND a.kind IN ['Identity','Role','Policy','Permission','Resource','CloudAccount',"
        "'CloudOrganization'] RETURN properties(e) ORDER BY e.id"
    )
    return fingerprint({"nodes": nodes, "edges": edges})


def test_alternate_paths_collateral_access_and_original_graph_are_preserved(
    live_database, isolated_graph_settings
):
    graph, service, request, alternate, ordinary, colleague = world(
        live_database, isolated_graph_settings
    )
    original = cloud_snapshot(service)
    proposal = service.propose(request)
    assert proposal.evidence and proposal.finding_ids and proposal.permission_ids
    assert proposal.status == "PROPOSED" and not proposal.execution_allowed
    assert proposal.target_relationship["id"] == request.relationship_id
    simulation = service.simulate(proposal.proposal_id, SimulationRequest(request_id="one"))
    assert len(simulation.before.findings) == 2 and len(simulation.after.findings) == 1
    assert simulation.removed_path_ids and simulation.alternate_paths
    assert simulation.remaining_path_ids == simulation.alternate_paths
    assert any(
        i.resource_id == ordinary.id and i.potential_access_lost for i in simulation.access_impact
    )
    assert any(
        i.identity_id == colleague.id and i.potential_access_lost for i in simulation.access_impact
    )
    assert all(
        i.legitimacy == "unknown_requires_workload_owner_review" for i in simulation.access_impact
    )
    assert simulation.risk_difference.maximum_score_change < 0
    assert simulation.supporting_evidence and simulation.visualization["relationships"]
    assert cloud_snapshot(service) == original
    # A combined overlay removes the remaining alternative, without deleting either grant.
    second = service.propose(
        request.model_copy(update={"request_id": "second", "relationship_id": alternate.id})
    )
    combined = service.simulate(
        proposal.proposal_id,
        SimulationRequest(request_id="combined", additional_proposal_ids=[second.proposal_id]),
    )
    assert not combined.after.findings and len(combined.removed_path_ids) == 2
    assert cloud_snapshot(service) == original


def test_repeat_simulation_concurrency_and_reconnect(live_database, isolated_graph_settings):
    _, service, request, _, _, _ = world(live_database, isolated_graph_settings)
    proposal = service.propose(request)
    same = SimulationRequest(request_id="concurrent")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.simulate(proposal.proposal_id, same), range(2)))
    assert results[0] == results[1]
    assert service.simulate(proposal.proposal_id, same) == results[0]
    with pytest.raises(GraphConflict):
        service.propose(request.model_copy(update={"reason": "Changed same operation"}))
    second_db = GraphDatabase(isolated_graph_settings)
    try:
        repo = service.repository
        restored = RemediationService(
            RemediationRepository(
                second_db, repo.scope, repo.settings, repo.space_id, repo.actor_id
            )
        )
        assert restored.get(proposal.proposal_id) == service.get(proposal.proposal_id)
        assert restored.saved_simulation(results[0].simulation_id) == results[0]
    finally:
        second_db.close()


def test_explicit_approval_is_only_a_review_and_no_aws_api_is_called(
    live_database, isolated_graph_settings, monkeypatch
):
    graph, service, request, _, _, _ = world(live_database, isolated_graph_settings)
    aws = Mock(side_effect=AssertionError("No AWS client may be constructed"))
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        SimpleNamespace(client=aws, Session=aws, session=SimpleNamespace(Session=aws)),
    )
    before = cloud_snapshot(service)
    proposal = service.propose(request)
    simulation = service.simulate(proposal.proposal_id, SimulationRequest(request_id="approval"))
    with pytest.raises(RemediationConflict):
        service.review(
            proposal.proposal_id,
            ProposalReview(
                request_id="skip",
                expected_revision=0,
                status="APPROVED",
                simulation_id=simulation.simulation_id,
                reason="Cannot skip review",
            ),
        )
    under = service.review(
        proposal.proposal_id,
        ProposalReview(
            request_id="start",
            expected_revision=0,
            status="UNDER_REVIEW",
            reason="SYNTHETIC explicit analyst review",
        ),
    )
    with pytest.raises(RemediationConflict):
        service.review(
            proposal.proposal_id,
            ProposalReview(
                request_id="no-simulation",
                expected_revision=1,
                status="APPROVED",
                reason="Requires saved comparison",
            ),
        )
    review = ProposalReview(
        request_id="approve",
        expected_revision=under.revision,
        status="APPROVED",
        simulation_id=simulation.simulation_id,
        reason="SYNTHETIC approve recommendation only",
    )
    approved = service.review(proposal.proposal_id, review)
    assert service.review(proposal.proposal_id, review) == approved
    assert approved.approval_scope == "recommendation_only" and not approved.execution_allowed
    assert len(approved.review_history) == 2 and approved.status == "APPROVED"
    assert cloud_snapshot(service) == before
    assert graph.engine.discover(request.analysis).findings
    aws.assert_not_called()


def test_rejected_and_unprivileged_review_are_blocked(live_database, isolated_graph_settings):
    _, service, request, _, _, _ = world(live_database, isolated_graph_settings)
    p = service.propose(request)
    without_human = RemediationService(service.repository)
    review = ProposalReview(
        request_id="review",
        expected_revision=0,
        status="UNDER_REVIEW",
        reason="Must be explicit human control plane",
    )
    with pytest.raises(RemediationAccessDenied):
        without_human.review(p.proposal_id, review)
    service.review(p.proposal_id, review)
    rejected = service.review(
        p.proposal_id,
        ProposalReview(
            request_id="reject",
            expected_revision=1,
            status="REJECTED",
            reason="SYNTHETIC owner needs this grant",
        ),
    )
    assert rejected.status == "REJECTED"
    with pytest.raises(RemediationConflict):
        service.simulate(p.proposal_id, SimulationRequest(request_id="after-rejection"))


def test_recommendations_have_qualified_evidence_and_are_idempotent(
    live_database, isolated_graph_settings
):
    _, service, request, _, _, _ = world(live_database, isolated_graph_settings)
    query = RecommendRequest(request_id="recommend", analysis=request.analysis, limit=20)
    result = service.recommend(query)
    assert {K.EXCESSIVE_PERMISSION, K.BROAD_RESOURCE, K.POLICY_EXCEPTION} <= set(
        result.available_categories
    )
    assert all(p.evidence for p in result.proposals)
    exception = next(p for p in result.proposals if p.category == K.POLICY_EXCEPTION)
    assert any(e["record"].get("evidence_type") == "policy-exception" for e in exception.evidence)
    assert service.recommend(query) == result


def test_demo_expired_access_and_role_trust_candidates(live_database, isolated_graph_settings):
    workflow, _ = workflow_world(live_database, isolated_graph_settings, scenario=Scenario.B)
    scope = workflow.repository.scope
    service = service_for(live_database, isolated_graph_settings, scope)
    result = service.recommend(
        RecommendRequest(
            request_id="expired",
            analysis=AnalysisRequest(at=hour(12), scenario=Scenario.B),
            limit=20,
        )
    )
    assert {K.EXPIRED_ACCESS, K.ROLE_TRUST} <= set(result.available_categories)
    p = next(p for p in result.proposals if p.category == K.EXPIRED_ACCESS)
    assert any(e["record"].get("kind") == "Investigation" for e in p.evidence)
    simulation = service.simulate(p.proposal_id, SimulationRequest(request_id="expired"))
    assert simulation.removed_path_ids


def test_tenant_membership_and_stale_source_fail_closed(live_database, isolated_graph_settings):
    graph, service, request, _, _, _ = world(live_database, isolated_graph_settings)
    proposal = service.propose(request)
    saved = service.simulate(proposal.proposal_id, SimulationRequest(request_id="saved"))
    other = service_for(live_database, isolated_graph_settings, TenantScope(tenant_id="unrelated"))
    with pytest.raises(GraphNotFound):
        other.get(proposal.proposal_id)
    with pytest.raises(GraphNotFound):
        other.saved_simulation(saved.simulation_id)
    graph.policy(graph.identity, "new-change")
    with pytest.raises(RemediationConflict):
        service.simulate(proposal.proposal_id, SimulationRequest(request_id="changed"))
    assert service.saved_simulation(saved.simulation_id) == saved
    repo = service.repository
    MemoryAccessAdmin(live_database, repo.scope).revoke_member(
        repo.space_id, repo.actor_id, "revoke"
    )
    with pytest.raises(MemoryAccessDenied):
        service.get(proposal.proposal_id)


def test_deny_and_control_removal_rejected(live_database, isolated_graph_settings):
    graph, service, request, _, _, _ = world(live_database, isolated_graph_settings)
    for category, effect in [("IdentityPolicy", "Deny"), ("PermissionsBoundary", "Allow")]:
        grant = graph.policy(
            graph.identity, category + effect, policy_type=category, effect=effect
        )[3]
        with pytest.raises(InvalidRemediation):
            service.propose(
                request.model_copy(update={"request_id": category, "relationship_id": grant.id})
            )


def test_revoked_between_validation_and_commit_cannot_create_proposal(
    live_database, isolated_graph_settings
):
    _, service, request, _, _, _ = world(live_database, isolated_graph_settings)
    repo, original = service.repository, service.repository.commit

    def revoke(mutation, stream, **kwargs):
        MemoryAccessAdmin(live_database, repo.scope).revoke_member(
            repo.space_id, repo.actor_id, "race"
        )
        return original(mutation, stream, **kwargs)

    with patch.object(repo, "commit", side_effect=revoke):
        with pytest.raises(MemoryAccessDenied):
            service.propose(request)
    assert not repo.proposals()


def test_corrupt_simulation_payload_fails_integrity_validation(
    live_database, isolated_graph_settings
):
    _, service, request, _, _, _ = world(live_database, isolated_graph_settings)
    p = service.propose(request)
    simulation = service.simulate(p.proposal_id, SimulationRequest(request_id="integrity"))
    repo = service.repository
    rows = repo.read(
        "MATCH (s:RemediationSimulation {tenant_id: $tenant_id, id: $id}) RETURN s.chunk_ids_json",
        {"id": simulation.simulation_id},
    )
    import json

    chunk_id = json.loads(rows[0][0])[0]
    live_database.query(
        "MATCH (e:Evidence {id: $id, tenant_id: $tenant_id}) "
        "SET e.record_json = $tampered RETURN e.id",
        {
            "id": chunk_id,
            "tenant_id": repo.scope.tenant_id,
            "tampered": "corrupted synthetic verification data",
        },
    )
    with pytest.raises(RemediationConflict):
        service.saved_simulation(simulation.simulation_id)


def test_prior_source_evidence_survives_cloud_change(live_database, isolated_graph_settings):
    graph, service, request, _, _, _ = world(live_database, isolated_graph_settings)
    p = service.propose(request)
    simulation = service.simulate(p.proposal_id, SimulationRequest(request_id="history"))
    graph.repository.close_relationship(
        NodeRef(
            kind=p.target_relationship["source_kind"], id=p.target_relationship["source_node_id"]
        ),
        R.GRANTS,
        request.relationship_id,
        valid_to=hour(13),
        source_event_id=graph.event.id,
    )
    assert service.get(p.proposal_id).evidence == p.evidence
    assert service.saved_simulation(simulation.simulation_id) == simulation
    assert (
        next(e for e in p.evidence if e["record"]["id"] == request.relationship_id)["record"].get(
            "valid_to"
        )
        is None
    )


def test_mixed_allow_deny_attachment_is_not_recommended(live_database, isolated_graph_settings):
    graph, service, request, _, _, _ = world(live_database, isolated_graph_settings)
    policy = graph.node("shared", N.POLICY, policy_type="IdentityPolicy")
    deny = graph.node("shared/deny-delete", N.PERMISSION, effect="Deny", action="s3:DeleteObject")
    graph.edge(R.GRANTS, policy, deny)
    graph.edge(R.TARGETS, deny, graph.resource)
    recommendations = service.recommend(
        RecommendRequest(request_id="mixed", analysis=request.analysis, limit=20)
    )
    assert recommendations.proposals
    assert not any(
        p.target_relationship["kind"] == "HAS_POLICY"
        and p.target_relationship["target_node_id"] == policy.id
        for p in recommendations.proposals
    )
    assert any(
        p.target_relationship["id"] == request.relationship_id for p in recommendations.proposals
    )

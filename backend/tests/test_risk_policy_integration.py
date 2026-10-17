import pytest

from app.demo.dataset import hour
from app.models.graph import NodeKind, RelationshipKind, epoch_us
from app.schemas.risk import AnalysisRequest, AuthorizationContext, DifferenceRequest, WhatIfRequest
from tests.risk_helpers import PolicyGraph

pytestmark = pytest.mark.integration


@pytest.fixture
def policy_graph(live_database):
    return PolicyGraph(live_database)


def discover(graph, **kwargs):
    return graph.engine.discover(AnalysisRequest(at=hour(12), **kwargs))


def test_direct_generic_path_and_half_open_grant_expiry(policy_graph):
    graph = policy_graph
    graph.policy(graph.identity, "read", valid_to=hour(12))
    assert graph.engine.discover(AnalysisRequest(at=hour(11), max_role_hops=0)).findings
    assert not discover(graph).findings


@pytest.mark.parametrize(
    "policy_type", ["IdentityPolicy", "ResourcePolicy", "PermissionsBoundary", "SessionPolicy"]
)
def test_explicit_deny_overrides_connected_allow(policy_graph, policy_type):
    graph = policy_graph
    graph.policy(graph.identity, "read")
    owner = graph.resource if policy_type == "ResourcePolicy" else graph.identity
    graph.policy(
        owner,
        "deny",
        policy_type,
        "Deny",
        principal_arns=["*"] if policy_type == "ResourcePolicy" else None,
    )
    result = discover(graph)
    assert not result.findings
    assert any(c.reason == "applicable explicit Deny" for c in result.rejected_candidates)


@pytest.mark.parametrize("policy_type", ["PermissionsBoundary", "SessionPolicy"])
def test_attached_empty_constraint_is_not_ignored(policy_graph, policy_type):
    graph = policy_graph
    graph.policy(graph.identity, "read")
    empty = graph.node("empty", NodeKind.POLICY, policy_type=policy_type)
    graph.edge(RelationshipKind.HAS_POLICY, graph.identity, empty)
    result = discover(graph)
    assert not result.findings
    assert any(policy_type in c.reason for c in result.rejected_candidates)


def test_constraint_allow_intersection_and_read_only_removal(policy_graph):
    graph = policy_graph
    graph.policy(graph.identity, "read")
    _, _, _, grant, _ = graph.policy(graph.identity, "boundary", "PermissionsBoundary")
    before = discover(graph)
    assert len(before.findings) == 1
    recorded = graph.engine.repository.database.query("MATCH (n) RETURN count(n)", read_only=True)
    result = graph.engine.what_if(WhatIfRequest(at=hour(12), remove_relationship_ids=[grant.id]))
    assert not result.after.findings
    assert (
        graph.engine.repository.database.query("MATCH (n) RETURN count(n)", read_only=True)
        == recorded
    )
    assert discover(graph) == before


def test_mfa_and_network_conditions_require_explicit_context(policy_graph):
    graph = policy_graph
    graph.policy(
        graph.identity,
        "conditional",
        conditions={
            "Bool": {"aws:MultiFactorAuthPresent": "true"},
            "IpAddress": {"aws:SourceIp": "192.0.2.0/24"},
        },
    )
    unknown = discover(graph)
    assert not unknown.findings and unknown.rejected_candidates[0].unresolved
    assert not discover(
        graph, context=AuthorizationContext(mfa_present=False, source_ip="192.0.2.9")
    ).findings
    result = discover(graph, context=AuthorizationContext(mfa_present=True, source_ip="192.0.2.9"))
    assert len(result.findings) == 1
    factor = next(f for f in result.findings[0].score.factors if f.name == "compensating_controls")
    assert factor.points == -10 and factor.record_ids


def test_unknown_conditional_deny_is_unresolved_not_ignored(policy_graph):
    graph = policy_graph
    graph.policy(graph.identity, "read")
    graph.policy(
        graph.identity, "deny", effect="Deny", conditions={"StringLike": {"aws:PrincipalArn": "*"}}
    )
    result = discover(graph)
    assert not result.findings and result.rejected_candidates[0].unresolved


def test_unverified_deny_is_unresolved(policy_graph):
    graph = policy_graph
    graph.policy(graph.identity, "read")
    graph.policy(graph.identity, "deny", effect="Deny", status="unverified")
    result = discover(graph)
    assert not result.findings and result.rejected_candidates[0].unresolved


def test_cross_account_resource_allow_and_principal_selector(policy_graph):
    graph = policy_graph
    resource = graph.node("other-bucket", NodeKind.RESOURCE, account="000000000002", sensitive=True)
    graph.policy(graph.identity, "cross-read", target=resource)
    assert not discover(graph).findings
    graph.policy(
        resource,
        "resource-allow",
        "ResourcePolicy",
        target=resource,
        principal_arns=[graph.identity.attributes["arn"]],
    )
    assert len(discover(graph).findings) == 1
    graph.policy(
        resource, "resource-deny", "ResourcePolicy", "Deny", target=resource, principal_arns=["*"]
    )
    assert not discover(graph).findings


@pytest.mark.parametrize(
    "policy_type,owner_side",
    [("ServiceControlPolicy", "identity"), ("ResourceControlPolicy", "resource")],
)
def test_account_control_policies_are_traversed_and_intersected(
    policy_graph, policy_type, owner_side
):
    graph = policy_graph
    graph.policy(graph.identity, "read")
    account = graph.node("account", NodeKind.CLOUD_ACCOUNT, display_name="SYNTHETIC account")
    graph.edge(RelationshipKind.BELONGS_TO, getattr(graph, owner_side), account)
    graph.policy(account, "account-allow", policy_type)
    assert discover(graph).findings
    graph.policy(account, "account-deny", policy_type, "Deny")
    assert not discover(graph).findings


def test_internet_exposure_and_controls_require_structured_verified_evidence(policy_graph):
    graph = policy_graph
    graph.policy(graph.identity, "read")
    initial = discover(graph).findings[0]
    assert next(f.points for f in initial.score.factors if f.name == "internet_exposure") == 0
    observation = graph.node(
        "unverified-exposure",
        NodeKind.EVIDENCE,
        evidence_type="internet-exposure",
        content_sha256="b" * 64,
        status="disputed",
        fact_value=True,
    )
    graph.edge(RelationshipKind.SUPPORTS, observation, graph.resource)
    assert discover(graph).findings[0].score.value == initial.score.value
    exposure = graph.node(
        "verified-exposure",
        NodeKind.EVIDENCE,
        evidence_type="internet-exposure",
        content_sha256="c" * 64,
        status="verified",
        fact_value=True,
    )
    graph.edge(RelationshipKind.SUPPORTS, exposure, graph.resource)
    control = graph.node(
        "monitoring",
        NodeKind.EVIDENCE,
        evidence_type="compensating-control",
        content_sha256="d" * 64,
        status="verified",
        fact_value=True,
        control_type="monitoring",
    )
    graph.edge(RelationshipKind.SUPPORTS, control, graph.resource)
    finding = discover(graph).findings[0]
    assert finding.score.value == initial.score.value + 15 - 5
    factors = {f.name: f for f in finding.score.factors}
    assert factors["internet_exposure"].record_ids == [exposure.id]
    assert factors["compensating_controls"].record_ids == [control.id]


def test_connected_can_assume_without_caller_and_trust_does_not_authorize(policy_graph):
    graph = policy_graph
    role = graph.node("role", NodeKind.ROLE, arn="arn:aws:iam::000000000001:role/synthetic")
    graph.edge(RelationshipKind.CAN_ASSUME, graph.identity, role)
    graph.policy(role, "role-read")
    assert not discover(graph).findings
    graph.policy(graph.identity, "assume", target=role, action="sts:AssumeRole")
    assert not discover(graph).findings
    graph.policy(role, "trust", "TrustPolicy", target=graph.identity, action="sts:AssumeRole")
    assert len(discover(graph).findings) == 1


def test_deny_removal_has_specific_closing_event_provenance(policy_graph):
    graph = policy_graph
    graph.policy(graph.identity, "read")
    deny_policy, _, _, deny_grant, _ = graph.policy(graph.identity, "deny", effect="Deny")
    closing_event = graph.node(
        "closing-event",
        NodeKind.SECURITY_EVENT,
        event_type="SyntheticDenyRemoved",
        event_time=epoch_us(hour(11)),
        content_sha256="e" * 64,
    )
    graph.repository.close_relationship(
        deny_policy.ref,
        RelationshipKind.GRANTS,
        deny_grant.id,
        valid_to=hour(11),
        source_event_id=closing_event.id,
    )
    difference = graph.engine.difference(DifferenceRequest(before=hour(9), after=hour(12)))
    assert len(difference.added_path_ids) == 1
    introduction = difference.introductions[0]
    assert introduction.first_reachable_at == hour(11)
    assert introduction.transition_events[0].id == closing_event.id
    assert introduction.transition_events[0].closed_relationship_ids == [deny_grant.id]
    finding = difference.after.findings[0]
    assert any(
        e.id == closing_event.id and deny_grant.id in e.closed_relationship_ids
        for e in finding.contributing_events
    )
    evidence = graph.engine.repository.evidence(finding.finding_id)
    assert any(e["record"].get("id") == deny_grant.id for e in evidence.evidence)


def test_simulating_deny_removal_can_increase_risk(policy_graph):
    graph = policy_graph
    graph.policy(graph.identity, "read")
    _, _, _, deny_grant, _ = graph.policy(graph.identity, "deny", effect="Deny")
    result = graph.engine.what_if(
        WhatIfRequest(at=hour(12), remove_relationship_ids=[deny_grant.id])
    )
    assert not result.before.findings and result.after.findings
    assert result.added_path_ids and not result.removed_path_ids
    assert not discover(graph).findings


def test_multiple_boundaries_are_unresolved_instead_of_unioned(policy_graph):
    graph = policy_graph
    graph.policy(graph.identity, "read")
    graph.policy(graph.identity, "boundary-one", "PermissionsBoundary")
    graph.policy(graph.identity, "boundary-two", "PermissionsBoundary")
    result = discover(graph)
    assert not result.findings and result.rejected_candidates[0].unresolved


def test_incomplete_organization_hierarchy_is_unresolved(policy_graph):
    graph = policy_graph
    graph.policy(graph.identity, "read")
    account = graph.node("account", NodeKind.CLOUD_ACCOUNT)
    unit = graph.node("unit", NodeKind.CLOUD_ORGANIZATION)
    root = graph.node("root", NodeKind.CLOUD_ORGANIZATION)
    graph.edge(RelationshipKind.BELONGS_TO, graph.identity, account)
    graph.edge(RelationshipKind.BELONGS_TO, account, unit)
    graph.edge(RelationshipKind.BELONGS_TO, unit, root)
    graph.policy(root, "root-deny", "ServiceControlPolicy", "Deny")
    result = discover(graph)
    assert not result.findings and result.rejected_candidates[0].unresolved


def test_missing_marked_boundary_is_unresolved(policy_graph):
    graph = policy_graph
    identity = graph.node("marked-user", NodeKind.IDENTITY, boundary_required=True)
    graph.policy(identity, "read")
    result = discover(graph)
    assert not result.findings and result.rejected_candidates[0].unresolved


def test_uncertain_organization_membership_is_not_ignored(policy_graph):
    graph = policy_graph
    graph.policy(graph.identity, "read")
    account = graph.node("account", NodeKind.CLOUD_ACCOUNT)
    graph.edge(RelationshipKind.BELONGS_TO, graph.identity, account, status="disputed")
    graph.policy(account, "deny", "ServiceControlPolicy", "Deny")
    result = discover(graph)
    assert not result.findings and result.rejected_candidates[0].unresolved

import pytest
from pydantic import ValidationError

from app.demo.dataset import hour
from app.models.remediation import ProposalReview, ProposeRequest, SimulationRequest


@pytest.mark.parametrize(
    "extra",
    [
        {"execute": True},
        {"aws_action": "iam:DeleteRolePolicy"},
        {"actor_id": "admin"},
        {"query": "MATCH (n) DETACH DELETE n"},
        {"reviewer_id": "forged"},
    ],
)
def test_authority_execution_and_query_fields_are_rejected(extra):
    with pytest.raises(ValidationError):
        ProposalReview.model_validate(
            {
                "request_id": "review",
                "expected_revision": 0,
                "status": "UNDER_REVIEW",
                "reason": "SYNTHETIC review",
                **extra,
            }
        )


@pytest.mark.parametrize("status", ["EXECUTED", "DEPLOYED", "AUTO_APPROVED", "approved"])
def test_only_recommendation_review_states_exist(status):
    with pytest.raises(ValidationError):
        ProposalReview(request_id="review", expected_revision=0, status=status, reason="SYNTHETIC")


@pytest.mark.parametrize("reason", ["password=unsafe", "-----BEGIN PRIVATE KEY-----"])
def test_credential_like_review_text_is_rejected(reason):
    with pytest.raises(ValidationError):
        ProposalReview(
            request_id="review", expected_revision=0, status="UNDER_REVIEW", reason=reason
        )


@pytest.mark.parametrize("edge", ["x' DELETE n //", "NOT-A-UUID", ""])
def test_invalid_edge_identifiers_cannot_enter_cypher(edge):
    with pytest.raises(ValidationError):
        ProposeRequest(
            request_id="proposal",
            analysis={"at": hour(12)},
            relationship_id=edge,
            category="remove_excessive_permission",
            reason="SYNTHETIC",
        )


def test_overlay_is_bounded_and_unique():
    identifier = "00000000-0000-0000-0000-000000000001"
    for ids in [[identifier, identifier], [identifier] * 10]:
        with pytest.raises(ValidationError):
            SimulationRequest(request_id="simulation", additional_proposal_ids=ids)

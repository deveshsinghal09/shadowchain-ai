import pytest
from pydantic import ValidationError

from app.models.investigation import (
    TRANSITIONS,
    InvestigationStatus,
    ReviewCaseEvidence,
    TransitionCase,
)


@pytest.mark.parametrize("status", list(InvestigationStatus))
def test_lifecycle_is_closed_and_has_no_self_transitions(status):
    assert status not in TRANSITIONS[status]
    assert set(TRANSITIONS[status]) <= set(InvestigationStatus)
    assert (not TRANSITIONS[status]) == (status == InvestigationStatus.CLOSED)


@pytest.mark.parametrize(
    "extra",
    [
        {"tenant_id": "foreign"},
        {"actor_id": "attacker"},
        {"execute": True},
        {"status": "completed"},
        {"status": "closed"},
        {"resolution": "remediated"},
        {"expected_revision": -1},
        {"reason": "password=secret"},
    ],
)
def test_case_requests_reject_authority_execution_and_unmodeled_states(extra):
    payload = dict(request_id="test", expected_revision=0, status="TRIAGING", reason="Triage", **{})
    with pytest.raises(ValidationError):
        TransitionCase(**(payload | extra))


def test_evidence_review_cannot_assert_arbitrary_verification_scope():
    with pytest.raises(ValidationError):
        ReviewCaseEvidence(
            request_id="bad",
            expected_revision=0,
            reason="Review",
            evidence_id="00000000-0000-0000-0000-000000000001",
            verification_status="verified",
            polarity="supporting",
            verification_scope="proven_exploitable",
        )

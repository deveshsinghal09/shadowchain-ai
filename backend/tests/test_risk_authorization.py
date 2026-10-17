import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import DatabaseUnavailable
from app.db.falkordb import GraphDatabase
from app.demo.dataset import hour
from app.main import create_app
from app.models.graph import TenantScope
from app.repositories.risk import RiskRepository, traversal_query
from app.schemas.risk import AnalysisRequest, AuthorizationContext, DifferenceRequest
from app.services.authorization import action_matches, condition_result
from app.services.risk import RiskEngine
from tests.api_helpers import TEST_HEADERS


@pytest.mark.parametrize(
    "pattern,action,expected",
    [
        ("s3:Get*", "s3:GetObject", True),
        ("S3:GETOBJECT", "s3:GetObject", True),
        ("s3:GetObjec?", "s3:GetObject", True),
        ("s3:Get[O]bject", "s3:GetObject", False),
        ("s3:Put*", "s3:GetObject", False),
    ],
)
def test_action_patterns_use_iam_wildcards(pattern, action, expected):
    assert action_matches(pattern, action) == expected


@pytest.mark.parametrize(
    "conditions,context,expected",
    [
        (
            {"Bool": {"aws:MultiFactorAuthPresent": "true"}},
            {"aws:MultiFactorAuthPresent": True},
            True,
        ),
        (
            {"Bool": {"aws:MultiFactorAuthPresent": True}},
            {"aws:MultiFactorAuthPresent": False},
            False,
        ),
        ({"Bool": {"aws:MultiFactorAuthPresent": "true"}}, {}, None),
        ({"IpAddress": {"aws:SourceIp": "192.0.2.0/24"}}, {"aws:SourceIp": "192.0.2.9"}, True),
        ({"NotIpAddress": {"aws:SourceIp": "192.0.2.0/24"}}, {"aws:SourceIp": "192.0.2.9"}, False),
        (
            {"StringEquals": {"aws:RequestedRegion": ["us-east-1", "us-west-2"]}},
            {"aws:RequestedRegion": "us-east-1"},
            True,
        ),
        (
            {"StringNotEquals": {"aws:RequestedRegion": ["us-east-1", "us-west-2"]}},
            {"aws:RequestedRegion": "us-east-1"},
            False,
        ),
        (
            {"ArnEquals": {"aws:PrincipalArn": "arn:synthetic:user"}},
            {"aws:PrincipalArn": "arn:synthetic:user"},
            True,
        ),
        (
            {"DateLessThan": {"aws:CurrentTime": "2026-10-16T13:00:00Z"}},
            {"aws:CurrentTime": hour(12).isoformat()},
            True,
        ),
        (
            {"DateGreaterThan": {"aws:CurrentTime": "2026-10-16T13:00:00Z"}},
            {"aws:CurrentTime": hour(12).isoformat()},
            False,
        ),
        ({"StringLike": {"aws:PrincipalArn": "*"}}, {}, None),
        (
            {"Bool": {"aws:MultiFactorAuthPresent": "invalid"}},
            {"aws:MultiFactorAuthPresent": True},
            None,
        ),
        ({"IpAddress": {"aws:SourceIp": "bad-cidr"}}, {"aws:SourceIp": "192.0.2.9"}, None),
    ],
)
def test_supported_conditions_and_unknowns(conditions, context, expected):
    assert condition_result({"conditions_json": json.dumps(conditions)}, context) is expected


def test_hashed_and_malformed_conditions_remain_unknown():
    assert condition_result({"condition_sha256": "a" * 64}, {}) is None
    assert condition_result({"conditions_json": "invalid-json"}, {}) is None
    assert condition_result({}, {}) is True
    assert not action_matches("*" * 500 + "unmatched", "s3:GetObject")
    assert (
        condition_result(
            {
                "conditions_json": '{"Bool":{"aws:MultiFactorAuthPresent":"true"}}',
                "condition_sha256": "0" * 64,
            },
            {"aws:MultiFactorAuthPresent": True},
        )
        is None
    )


@pytest.mark.parametrize("depth", [-1, 5, True, "4]->() DELETE n //"])
def test_traversal_depth_cannot_inject_cypher(depth):
    with pytest.raises(ValueError):
        traversal_query(depth)


@pytest.mark.parametrize(
    "payload",
    [
        {"at": "2026-10-16T12:00:00"},
        {"at": hour(12), "max_role_hops": 5},
        {"at": hour(12), "tenant_id": "victim"},
        {"at": hour(12), "identity_id": "') DELETE n //"},
        {"at": hour(12), "action": "s3:*"},
    ],
)
def test_risk_request_validation(payload):
    with pytest.raises(ValidationError):
        AnalysisRequest.model_validate(payload)


def test_utc_normalization_and_aware_comparison():
    assert AnalysisRequest(at="2026-10-16T17:30:00+05:30").at == hour(12)
    with pytest.raises(ValidationError):
        DifferenceRequest(before=hour(12), after=hour(9))
    with pytest.raises(ValidationError):
        AuthorizationContext(source_ip="not-an-ip")


def test_database_outage_is_not_reported_as_an_empty_safe_graph(unit_settings):
    settings = unit_settings.model_copy(update={"falkordb_port": 1})
    database = GraphDatabase(settings)
    try:
        with pytest.raises(DatabaseUnavailable):
            RiskEngine(RiskRepository(database, TenantScope(tenant_id="t"))).discover(
                AnalysisRequest(at=hour(12))
            )
    finally:
        database.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"demo_mode": False},
        {"app_env": "production", "falkordb_password": "synthetic-test-password"},
    ],
)
def test_risk_http_scope_is_disabled_outside_local_demo(
    unit_settings: Settings, graph_stub: MagicMock, overrides
):
    with TestClient(
        create_app(unit_settings.model_copy(update=overrides)), headers=TEST_HEADERS
    ) as client:
        response = client.post("/api/v1/risk/paths/discover", json={"at": hour(12).isoformat()})
        assert response.status_code == 404

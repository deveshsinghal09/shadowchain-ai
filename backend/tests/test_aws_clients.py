import json
from datetime import timedelta
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError

from app.core.config import Settings
from app.integrations.aws_client import ALLOWED_OPERATIONS, AwsGateway
from app.integrations.aws_readers import cloudtrail_events, document, iam_inventory
from app.models.aws import CloudTrailRequest
from app.services.aws_errors import AwsDisabled, AwsImportLimit, AwsOperationError, AwsScopeError
from tests.aws_helpers import ACCOUNT, FakeSession, iam_responses, trail_event


def aws_settings(**overrides):
    return Settings(_env_file=None, aws_enabled=True, aws_account_id=ACCOUNT, **overrides)


def test_disabled_never_constructs_session_or_reads_provider_chain():
    factory = Mock()
    gateway = AwsGateway(Settings(_env_file=None), session_factory=factory)
    with pytest.raises(AwsDisabled):
        gateway.validate_account(ACCOUNT)
    factory.assert_not_called()


@pytest.mark.parametrize(
    "service,operation",
    [
        ("iam", "create_role"),
        ("iam", "delete_role"),
        ("iam", "attach_role_policy"),
        ("iam", "put_role_policy"),
        ("s3", "create_bucket"),
        ("s3", "put_bucket_policy"),
        ("cloudtrail", "create_trail"),
    ],
)
def test_disallowed_mutations_fail_before_sdk_construction(service, operation):
    factory = Mock()
    gateway = AwsGateway(aws_settings(), session_factory=factory)
    with pytest.raises(AwsDisabled):
        gateway.call(service, operation)
    factory.assert_not_called()


def test_iam_reads_all_requested_inventory_and_drops_condition_secrets():
    session = FakeSession(iam_responses())
    result = iam_inventory(session.gateway(aws_settings()), ACCOUNT)
    role = result["roles"][0]
    assert role["trust"]["statements"] and len(role["policies"]) == 2
    assert len(role["policies"][0]["versions"]) == 2
    assert "SHOULD_NEVER_PERSIST" not in json.dumps(result)
    assert all(operation in ALLOWED_OPERATIONS[service] for service, operation, _ in session.calls)
    assert all(
        c.retries["total_max_attempts"] == 3 and c.retries["mode"] == "standard"
        for c in session.configs
    )


def test_account_mismatch_fails_before_inventory_read():
    responses = iam_responses()
    responses[("sts", "get_caller_identity")] = {"Account": "000000000000"}
    session = FakeSession(responses)
    with pytest.raises(AwsScopeError):
        iam_inventory(session.gateway(aws_settings()), ACCOUNT)
    assert len(session.calls) == 1


def test_iam_pagination_and_repeated_marker_are_bounded():
    responses = iam_responses()
    responses[("iam", "list_roles")] = lambda p: {
        "Roles": [],
        "IsTruncated": "Marker" not in p,
        "Marker": "next",
    }
    session = FakeSession(responses)
    assert iam_inventory(session.gateway(aws_settings()), ACCOUNT) == {"roles": []}
    assert [c[2].get("Marker") for c in session.calls[1:]] == [None, "next"]
    responses[("iam", "list_roles")] = {"Roles": [], "IsTruncated": True, "Marker": "repeat"}
    with pytest.raises(AwsImportLimit):
        iam_inventory(FakeSession(responses).gateway(aws_settings()), ACCOUNT)


def test_throttling_is_safe_and_sdk_retries_are_bounded():
    responses = iam_responses()
    responses[("iam", "list_roles")] = ClientError(
        {"Error": {"Code": "Throttling", "Message": "sensitive text"}}, "ListRoles"
    )
    with pytest.raises(AwsOperationError) as error:
        iam_inventory(FakeSession(responses).gateway(aws_settings()), ACCOUNT)
    assert "sensitive text" not in str(error.value)


def test_cloudtrail_redacts_payloads_and_preserves_source_fields():
    stamp, raw = trail_event()
    responses = {
        ("sts", "get_caller_identity"): {"Account": ACCOUNT},
        ("cloudtrail", "lookup_events"): lambda p: (
            {
                "Events": [
                    {
                        "EventId": raw["eventID"],
                        "EventTime": stamp,
                        "CloudTrailEvent": json.dumps(raw),
                    }
                ]
            }
            if p["LookupAttributes"][0]["AttributeValue"] == "iam.amazonaws.com"
            else {"Events": []}
        ),
    }
    result = cloudtrail_events(
        FakeSession(responses).gateway(aws_settings(cloudtrail_enabled=True)),
        ACCOUNT,
        stamp - timedelta(minutes=1),
        stamp + timedelta(minutes=1),
    )
    event = result["events"][0]
    assert event["account_id"] == ACCOUNT and event["region"] == "us-east-1"
    assert (
        event["source_api"] == "iam.amazonaws.com:PutRolePolicy" and event["identity_refs"]["arn"]
    )
    assert "accessKeyId" not in json.dumps(result) and "SHOULD_NEVER_PERSIST" not in json.dumps(
        result
    )


@pytest.mark.parametrize(
    "field,value", [("recipientAccountId", "999999999999"), ("awsRegion", "eu-west-1")]
)
def test_cloudtrail_scope_mismatch_is_rejected(field, value):
    stamp, raw = trail_event()
    raw[field] = value
    responses = {
        ("sts", "get_caller_identity"): {"Account": ACCOUNT},
        ("cloudtrail", "lookup_events"): {
            "Events": [
                {"EventId": raw["eventID"], "EventTime": stamp, "CloudTrailEvent": json.dumps(raw)}
            ]
        },
    }
    with pytest.raises(AwsScopeError):
        cloudtrail_events(
            FakeSession(responses).gateway(aws_settings(cloudtrail_enabled=True)),
            ACCOUNT,
            stamp - timedelta(minutes=1),
            stamp + timedelta(minutes=1),
        )


def test_condition_values_and_unsupported_statements_never_become_authorization():
    result = document(
        {
            "Statement": {
                "Effect": "Allow",
                "NotAction": "iam:*",
                "Resource": "*",
                "Condition": {"StringEquals": {"secret": "SHOULD_NEVER_PERSIST"}},
            }
        }
    )
    assert result["statements"][0]["unsupported"] and result["statements"][0]["conditions_redacted"]
    assert "SHOULD_NEVER_PERSIST" not in json.dumps(result)


@pytest.mark.parametrize(
    "overrides", [{"aws_enabled": True}, {"cloudtrail_enabled": True}, {"export_mode": "s3"}]
)
def test_config_requires_scope_and_export_destination(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_event_history_rejects_unbounded_window():
    stamp, _ = trail_event()
    with pytest.raises(ValidationError):
        CloudTrailRequest(
            request_id="old",
            confirm_account_id=ACCOUNT,
            start_time=stamp - timedelta(days=100),
            end_time=stamp,
        )


def test_cloudtrail_next_token_is_preserved_and_duplicates_are_deduplicated():
    stamp, raw = trail_event()

    def page(params):
        if params["LookupAttributes"][0]["AttributeValue"] != "iam.amazonaws.com":
            return {"Events": []}
        return {
            "Events": [
                {"EventId": raw["eventID"], "EventTime": stamp, "CloudTrailEvent": json.dumps(raw)}
            ],
            **({"NextToken": "next"} if "NextToken" not in params else {}),
        }

    session = FakeSession(
        {
            ("sts", "get_caller_identity"): {"Account": ACCOUNT},
            ("cloudtrail", "lookup_events"): page,
        }
    )
    gateway = session.gateway(aws_settings(cloudtrail_enabled=True))
    pauses = []
    gateway.sleeper = pauses.append
    result = cloudtrail_events(
        gateway, ACCOUNT, stamp - timedelta(minutes=1), stamp + timedelta(minutes=1)
    )
    assert len(result["events"]) == 1 and len(pauses) == 3
    lookups = [p for service, _, p in session.calls if service == "cloudtrail"]
    assert lookups[1]["NextToken"] == "next"

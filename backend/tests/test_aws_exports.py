import base64
import hashlib
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from app.core.config import Settings
from app.integrations.aws_client import AwsGateway
from app.integrations.s3_reports import S3ReportStore
from app.services.aws_errors import AwsScopeError, PrivateBucketRequired, ReportConflict
from tests.aws_helpers import ACCOUNT, FakeSession

BUCKET = "synthetic-shadowchain-private"


def settings():
    return Settings(
        _env_file=None,
        aws_enabled=True,
        aws_account_id=ACCOUNT,
        export_mode="s3",
        s3_export_bucket=BUCKET,
    )


def responses():
    return {
        ("sts", "get_caller_identity"): {"Account": ACCOUNT},
        ("s3", "get_public_access_block"): {
            "PublicAccessBlockConfiguration": dict.fromkeys(
                [
                    "BlockPublicAcls",
                    "IgnorePublicAcls",
                    "BlockPublicPolicy",
                    "RestrictPublicBuckets",
                ],
                True,
            )
        },
        ("s3", "get_bucket_policy_status"): {"PolicyStatus": {"IsPublic": False}},
        ("s3", "get_bucket_acl"): {
            "Owner": {"ID": "synthetic-owner"},
            "Grants": [
                {
                    "Grantee": {"Type": "CanonicalUser", "ID": "synthetic-owner"},
                    "Permission": "FULL_CONTROL",
                }
            ],
        },
        ("s3", "head_object"): ClientError({"Error": {"Code": "404"}}, "HeadObject"),
        ("s3", "put_object"): {},
    }


def test_s3_private_bucket_exact_destination_and_encryption_only():
    session = FakeSession(responses())
    body = b'{"redacted":true}'
    digest = hashlib.sha256(body).hexdigest()
    result = S3ReportStore(session.gateway(settings())).publish("synthetic-report", body, digest)
    assert result == "s3://" + BUCKET + "/shadowchain-reports/synthetic-report.json"
    put = next(p for service, operation, p in session.calls if operation == "put_object")
    assert put["ExpectedBucketOwner"] == ACCOUNT and put["ServerSideEncryption"] == "AES256"
    assert "ACL" not in put and put["IfNoneMatch"] == "*" and put["Body"] == body
    assert all(p.get("Bucket") == BUCKET for service, _, p in session.calls if service == "s3")


@pytest.mark.parametrize("change", ["public-block", "public-policy", "public-acl", "wrong-owner"])
def test_insecure_bucket_never_receives_report(change):
    data = responses()
    if change == "public-block":
        data[("s3", "get_public_access_block")]["PublicAccessBlockConfiguration"][
            "BlockPublicPolicy"
        ] = False
    elif change == "public-policy":
        data[("s3", "get_bucket_policy_status")]["PolicyStatus"]["IsPublic"] = True
    elif change == "public-acl":
        data[("s3", "get_bucket_acl")]["Grants"].append(
            {"Grantee": {"Type": "Group", "URI": "http://acs.amazonaws.com/groups/global/AllUsers"}}
        )
    else:
        data[("s3", "get_public_access_block")] = ClientError(
            {"Error": {"Code": "403"}}, "GetPublicAccessBlock"
        )
    session = FakeSession(data)
    with pytest.raises(PrivateBucketRequired):
        S3ReportStore(session.gateway(settings())).publish("report", b"safe", "a" * 64)
    assert all(op != "put_object" for _, op, _ in session.calls)


def test_existing_export_is_not_overwritten_if_hash_differs():
    data = responses()
    data[("s3", "head_object")] = {"Metadata": {"sha256": "different"}, "ContentLength": 4}
    data[("s3", "put_object")] = ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
    with pytest.raises(ReportConflict):
        S3ReportStore(FakeSession(data).gateway(settings())).publish("report", b"safe", "a" * 64)


def test_sdk_stubber_verifies_real_s3_shapes_without_network():
    # Dummy Stubber-only strings, not usable AWS credentials or a real profile.
    session = boto3.Session(
        aws_access_key_id="testing-only",
        aws_secret_access_key="testing-only",
        region_name="us-east-1",
    )
    s3, sts = session.client("s3"), session.client("sts")
    stub_s3, stub_sts = Stubber(s3), Stubber(sts)
    stub_sts.add_response(
        "get_caller_identity",
        {
            "Account": ACCOUNT,
            "UserId": "synthetic",
            "Arn": "arn:aws:iam::123456789012:role/SYNTHETIC",
        },
        {},
    )
    params = {"Bucket": BUCKET, "ExpectedBucketOwner": ACCOUNT}
    data = responses()
    for op in ["get_public_access_block", "get_bucket_policy_status", "get_bucket_acl"]:
        stub_s3.add_response(op, data[("s3", op)], params)
    report_id, body = "synthetic-report", b'{"redacted":true}'
    digest = hashlib.sha256(body).hexdigest()
    object_params = {**params, "Key": "shadowchain-reports/" + report_id + ".json"}
    stub_s3.add_response(
        "put_object",
        {},
        {
            **object_params,
            "Body": body,
            "ContentType": "application/json",
            "ServerSideEncryption": "AES256",
            "IfNoneMatch": "*",
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(body).digest()).decode(),
            "Metadata": {"sha256": digest, "redaction": "allowlist-summary-v1"},
        },
    )
    gateway = AwsGateway(settings())
    gateway._clients = {"s3": s3, "sts": sts}
    with stub_s3, stub_sts:
        S3ReportStore(gateway).publish(report_id, body, digest)
        stub_s3.assert_no_pending_responses()
        stub_sts.assert_no_pending_responses()
    gateway.close()


def test_aws_status_and_disabled_sync_never_construct_sdk(client):
    with patch("boto3.Session") as session:
        status = client.get("/api/v1/aws/status")
        assert status.status_code == 200 and not status.json()["aws_enabled"]
        response = client.post(
            "/api/v1/aws/sync/iam", json={"request_id": "disabled", "confirm_account_id": ACCOUNT}
        )
        assert response.status_code == 403
        session.assert_not_called()


@pytest.mark.parametrize(
    "field", ["aws_access_key_id", "aws_secret_access_key", "bucket", "endpoint_url"]
)
def test_caller_cannot_choose_credentials_destination_or_endpoint(client, field):
    response = client.post(
        "/api/v1/aws/sync/iam",
        json={"request_id": "invalid", "confirm_account_id": ACCOUNT, field: "SHOULD_NOT_ECHO"},
    )
    assert response.status_code == 422 and "SHOULD_NOT_ECHO" not in response.text


def test_dispatcher_enforces_single_export_bucket():
    with patch("boto3.Session") as session:
        with pytest.raises(AwsScopeError):
            AwsGateway(settings()).call(
                "s3", "put_object", Bucket="other-bucket", ExpectedBucketOwner=ACCOUNT
            )
        session.assert_not_called()

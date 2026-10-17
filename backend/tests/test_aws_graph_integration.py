import json
from contextlib import contextmanager
from datetime import timedelta

import pytest

from app.db.falkordb import GraphDatabase
from app.models.aws import AwsSyncRequest, CloudTrailRequest, ReportRequest
from app.models.graph import TenantScope
from app.repositories.aws import AwsRepository
from app.services.aws_errors import AwsImportLimit
from app.services.aws_sync import AwsSyncService
from app.services.reports import ReportService
from tests.aws_helpers import ACCOUNT, FakeSession, iam_responses, trail_event
from tests.test_investigation_integration import case_world

pytestmark = pytest.mark.integration


def setup(live_database, settings, responses=None):
    config = settings.model_copy(update={"aws_enabled": True, "aws_account_id": ACCOUNT})
    repo = AwsRepository(live_database, TenantScope(tenant_id="aws-test"), config)
    session = FakeSession(responses or iam_responses())
    return repo, session


def test_iam_atomic_persistence_repeated_request_and_historical_snapshot(
    live_database, isolated_graph_settings
):
    repo, session = setup(live_database, isolated_graph_settings)
    request = AwsSyncRequest(request_id="first", confirm_account_id=ACCOUNT)
    first = AwsSyncService(repo, session.gateway(repo.settings)).sync("iam", request)
    assert first.node_count and first.relationship_count and first.coverage["roles_observed"] == 1
    count = len(session.calls)
    assert AwsSyncService(repo, session.gateway(repo.settings)).sync("iam", request) == first
    assert len(session.calls) == count
    second = AwsSyncService(repo, session.gateway(repo.settings)).sync(
        "iam", request.model_copy(update={"request_id": "second"})
    )
    assert second.run_id != first.run_id
    rows = repo.read(
        "MATCH (r:Role:AwsIamObservation {tenant_id: $tenant_id}) "
        "RETURN r.valid_to ORDER BY r.created_at"
    )
    assert len(rows) == 2 and rows[0][0] is not None and rows[1][0] is None
    all_nodes = repo.read("MATCH (n:Entity {tenant_id: $tenant_id}) RETURN properties(n)")
    assert "SHOULD_NEVER_PERSIST" not in json.dumps(all_nodes)
    assert (
        repo.read("MATCH (n:Entity {tenant_id: $tenant_id})-[r:CAN_ASSUME]->() RETURN r.id") == []
    )
    with temporary_database(isolated_graph_settings) as fresh:
        restored = AwsRepository(fresh, repo.scope, repo.settings)
        assert (
            restored.saved(first.run_id, repo.find(first.run_id)["request_sha256"])["run_id"]
            == first.run_id
        )


@contextmanager
def temporary_database(settings):
    database = GraphDatabase(settings)
    try:
        yield database
    finally:
        database.close()


def test_over_limit_does_not_replace_previous_inventory(live_database, isolated_graph_settings):
    repo, session = setup(live_database, isolated_graph_settings)
    request = AwsSyncRequest(request_id="first", confirm_account_id=ACCOUNT)
    AwsSyncService(repo, session.gateway(repo.settings)).sync("iam", request)
    original = repo.read(
        "MATCH (n:AwsIamObservation {tenant_id: $tenant_id}) RETURN properties(n) ORDER BY n.id"
    )
    low = repo.settings.model_copy(update={"aws_max_records": 2})
    with pytest.raises(AwsImportLimit):
        AwsSyncService(AwsRepository(live_database, repo.scope, low), session.gateway(low)).sync(
            "iam", request.model_copy(update={"request_id": "bounded"})
        )
    assert (
        repo.read(
            "MATCH (n:AwsIamObservation {tenant_id: $tenant_id}) RETURN properties(n) ORDER BY n.id"
        )
        == original
    )


def test_cloudtrail_duplicate_events_not_duplicated_and_provenance_persists(
    live_database, isolated_graph_settings
):
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
    repo, session = setup(live_database, isolated_graph_settings, responses)
    repo.settings = repo.settings.model_copy(update={"cloudtrail_enabled": True})
    request = CloudTrailRequest(
        request_id="first",
        confirm_account_id=ACCOUNT,
        start_time=stamp - timedelta(minutes=1),
        end_time=stamp + timedelta(minutes=1),
    )
    for key in ["first", "second"]:
        result = AwsSyncService(repo, session.gateway(repo.settings)).sync(
            "cloudtrail", request.model_copy(update={"request_id": key})
        )
        assert not result.coverage["complete_audit_history"]
    events = repo.read("MATCH (n:SecurityEvent {tenant_id: $tenant_id}) RETURN properties(n)")
    assert len(events) == 1 and events[0][0]["region"] == "us-east-1"
    assert "accessKeyId" not in json.dumps(
        repo.read("MATCH (n:Entity {tenant_id: $tenant_id}) RETURN properties(n)")
    )
    unrelated = AwsRepository(live_database, TenantScope(tenant_id="other"), repo.settings)
    assert unrelated.find(result.run_id) is None


def test_local_redacted_report_without_aws_and_no_report_body_in_graph(
    live_database, isolated_graph_settings, tmp_path
):
    _, _, cases, case_id = case_world(live_database, isolated_graph_settings)
    settings = isolated_graph_settings.model_copy(update={"export_local_dir": tmp_path})

    def forbidden(*_):
        raise AssertionError("Local export must not construct AWS client")

    service = ReportService(cases, settings, gateway_factory=forbidden)
    request = ReportRequest(request_id="local", investigation_id=case_id)
    result = service.export(request)
    assert result.status == "PUBLISHED" and service.export(request) == result
    body = (tmp_path / (result.report_id + ".json")).read_text(encoding="utf-8")
    assert "arn:aws:" not in body and "000000000001" not in body and "SYNTHETIC" not in body
    metadata = cases.repository.node(result.report_id)
    assert "record_json" not in metadata and "report_id" not in metadata
    assert metadata["artifact_uri"] == str(tmp_path / (result.report_id + ".json"))


def test_managed_policy_is_shared_without_duplicate_grants(live_database, isolated_graph_settings):
    import copy

    responses = iam_responses()
    first = responses[("iam", "get_role")]["Role"]
    second = copy.deepcopy(first)
    second.update(
        RoleId="SYNTHETIC-SECOND",
        RoleName="SYNTHETIC-second",
        Arn="arn:aws:iam::123456789012:role/SYNTHETIC-second",
    )
    responses[("iam", "list_roles")] = {"Roles": [first, second]}
    responses[("iam", "get_role")] = lambda p: {
        "Role": first if p["RoleName"] == first["RoleName"] else second
    }
    responses[("iam", "get_role_policy")] = lambda p: {
        "RoleName": p["RoleName"],
        "PolicyName": p["PolicyName"],
        "PolicyDocument": {"Statement": []},
    }
    repo, session = setup(live_database, isolated_graph_settings, responses)
    AwsSyncService(repo, session.gateway(repo.settings)).sync(
        "iam", AwsSyncRequest(request_id="shared", confirm_account_id=ACCOUNT)
    )
    rows = repo.read("MATCH (p:Policy {tenant_id: $tenant_id}) WHERE p.arn IS NOT NULL RETURN p.id")
    assert len(rows) == 1
    grants = repo.read(
        "MATCH path=(p:Policy)-[g:GRANTS]->(v:Permission) "
        "WHERE p.id = $id AND all(n IN nodes(path) WHERE n.tenant_id = $tenant_id) "
        "AND all(e IN relationships(path) WHERE e.tenant_id = $tenant_id) RETURN g.id",
        {"id": rows[0][0]},
    )
    assert len(grants) == 1


def test_s3_export_publishes_only_redacted_bytes_and_graph_metadata(
    live_database, isolated_graph_settings
):
    from tests.test_aws_exports import BUCKET, responses

    _, _, cases, case_id = case_world(live_database, isolated_graph_settings)
    config = isolated_graph_settings.model_copy(
        update={
            "aws_enabled": True,
            "aws_account_id": ACCOUNT,
            "export_mode": "s3",
            "s3_export_bucket": BUCKET,
        }
    )
    session = FakeSession(responses())
    service = ReportService(cases, config, gateway_factory=session.gateway)
    request = ReportRequest(request_id="s3-redacted", investigation_id=case_id)
    report = service.export(request)
    assert report.status == "PUBLISHED" and report.export_mode == "s3"
    uploaded = next(
        params["Body"] for _, operation, params in session.calls if operation == "put_object"
    )
    assert b"arn:aws:" not in uploaded and b"000000000001" not in uploaded
    assert cases.repository.node(report.report_id).get("record_json") is None
    count = len(session.calls)
    assert service.export(request) == report and len(session.calls) == count


def test_unsupported_deny_is_not_silently_ignored(live_database, isolated_graph_settings):
    responses = iam_responses()
    responses[("iam", "get_policy_version")] = lambda params: {
        "PolicyVersion": {
            "VersionId": params["VersionId"],
            "IsDefaultVersion": params["VersionId"] == "v2",
            "Document": {"Statement": [{"Effect": "Deny", "NotAction": "iam:*", "Resource": "*"}]},
        }
    }
    repo, session = setup(live_database, isolated_graph_settings, responses)
    AwsSyncService(repo, session.gateway(repo.settings)).sync(
        "iam", AwsSyncRequest(request_id="unsupported-deny", confirm_account_id=ACCOUNT)
    )
    rows = repo.read(
        "MATCH (p:Policy {tenant_id: $tenant_id}) WHERE p.arn IS NOT NULL RETURN p.policy_type"
    )
    assert rows == [["UnresolvedPolicy"]]
    # Accountless S3 ARNs do not establish ownership or resource-policy authorization.
    assert repo.read(
        "MATCH (r:Resource {tenant_id: $tenant_id}) RETURN r.resource_policy_required"
    ) == [[True]]

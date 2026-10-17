"""User-triggered, read-only source imports; no scheduling, provisioning or IAM executor."""

import json
from datetime import datetime

from app.core.time import utc_now
from app.db.cypher import fingerprint, stable_id
from app.db.errors import GraphConflict
from app.integrations.aws_client import AwsGateway
from app.integrations.aws_readers import cloudtrail_events, iam_inventory
from app.models.aws import CloudTrailRequest, SyncResult
from app.models.graph import NodeKind as N
from app.models.graph import RelationshipKind as R
from app.models.graph import epoch_us
from app.repositories.aws import AwsBatch
from app.services.aws_errors import AwsDisabled

IAM_LIMITATIONS = [
    "IAM is global; inventory was observed through multiple API calls, not an atomic AWS snapshot.",
    "Roles/trust/attached and inline policies plus managed versions are covered; users, groups, "
    "SCP/RCP, session/resource policies and actual role usage are not fully inventoried.",
    "Conditions are redacted and unresolved; NotAction/NotResource/NotPrincipal statements "
    "or unsupported/wildcard resource scopes make the attached policy unresolved. "
    "Non-default policy versions never grant current modeled access.",
    "Referenced resources have unknown sensitivity; accountless ARNs require an unresolved "
    "resource policy because ownership is unknown. Trust alone does not establish CAN_ASSUME "
    "or effective AWS authorization; no assumption edges are inferred from trust alone.",
]
TRAIL_LIMITATIONS = [
    "Event History is regional, management-only, limited to 90 days and eventually delivered.",
    "This import queries IAM/STS/S3 sources and a closed security-relevant event-name allowlist; "
    "it excludes object data events, other services/regions and older or undelivered history.",
    "Request/response parameters, credentials, access-key IDs, IP/user-agent and raw payloads "
    "are not retained. No observed event is automatically applied as an effective permission.",
]


class AwsSyncService:
    def __init__(self, repository, gateway=None):
        self.repository = repository
        self.gateway = gateway or AwsGateway(repository.settings)

    def sync(self, kind, request):
        repo = self.repository
        if kind not in {"iam", "cloudtrail"}:
            raise AwsDisabled()
        self.gateway.require(request.confirm_account_id)
        if kind == "cloudtrail" and not repo.settings.cloudtrail_enabled:
            raise AwsDisabled()
        run_key = repo.key(kind, request.request_id)
        run_id = stable_id(
            repo.scope.tenant_id,
            N.DECISION.value,
            repo.account,
            "shadowchain.aws." + kind + ".v1",
            run_key,
        )
        digest = fingerprint(request.model_dump(mode="json"))
        old = repo.saved(run_id, digest)
        if old is not None:
            return SyncResult.model_validate(old)
        try:
            snapshot = (
                iam_inventory(self.gateway, repo.account)
                if kind == "iam"
                else cloudtrail_events(
                    self.gateway, repo.account, request.start_time, request.end_time
                )
            )
        finally:
            self.gateway.close()
        now = epoch_us(utc_now())
        for _ in range(5):
            old = repo.saved(run_id, digest)
            if old is not None:
                return SyncResult.model_validate(old)
            stream = repo.stream(kind)
            if stream["watermark"] >= now:
                raise GraphConflict()
            batch = AwsBatch(repo, kind, run_key, now)
            coverage = (
                self.iam(batch, snapshot) if kind == "iam" else self.trail(batch, snapshot, request)
            )
            limitations = IAM_LIMITATIONS if kind == "iam" else TRAIL_LIMITATIONS
            result = SyncResult(
                run_id=run_id,
                kind=kind,
                node_count=len(batch.nodes) + 1,
                relationship_count=batch.edge_count
                + sum(n["kind"] == "SecurityEvent" for n in batch.nodes.values()),
                coverage=coverage,
                limitations=limitations,
            )
            root = batch.node(
                N.DECISION,
                run_key,
                {
                    "action": "aws.sync",
                    "status": "COMPLETE",
                    "integration_kind": kind,
                    "region": repo.settings.aws_region,
                    "rationale": "Explicit account-confirmed user-triggered read-only AWS import",
                    "request_sha256": digest,
                    "response_json": result.model_dump_json(),
                    "coverage_json": json.dumps(coverage),
                },
            )
            batch.changes.append(f"SET {root['variable']}:AwsSyncRun ")
            for event in list(batch.nodes.values()):
                if event["kind"] == "SecurityEvent":
                    batch.edge(root, event, R.DERIVED_FROM)
            try:
                if repo.commit(batch, stream):
                    return result
            except GraphConflict:
                continue
        raise GraphConflict()

    def iam(self, batch, snapshot):
        account = batch.node(
            N.CLOUD_ACCOUNT,
            batch.run_key + "/account",
            {
                "provider": "aws",
                "account_number": self.repository.account,
                "display_name": "AWS imported account",
                "valid_from": batch.now,
            },
            snapshot=True,
        )
        event = batch.node(
            N.SECURITY_EVENT,
            batch.run_key + "/inventory-observation",
            {
                "event_type": "AwsIamInventoryObserved",
                "event_time": batch.now,
                "source_api": "iam:ListRoles/GetRole/PolicyReads",
                "region": "aws-global",
                "content_sha256": fingerprint(snapshot),
            },
        )

        def node(kind, key, attrs):
            return batch.node(
                kind,
                batch.run_key + "/" + key,
                {
                    **attrs,
                    "valid_from": batch.now,
                    "source_api": "iam:ReadInventory",
                    "region": "aws-global",
                    "import_run_id": batch.run_id,
                },
                snapshot=True,
            )

        for role in snapshot["roles"]:
            current = node(
                N.ROLE,
                "role/" + role["id"],
                {
                    "arn": role["arn"],
                    "display_name": role["name"],
                    "boundary_required": role["boundary_required"],
                },
            )
            batch.edge(current, account, R.BELONGS_TO)
            policies = [
                {
                    "kind": "trust",
                    "versions": [
                        {"version_id": "observed", "default": True, "document": role["trust"]}
                    ],
                }
            ] + role["policies"]
            for pindex, policy in enumerate(policies):
                for version in policy["versions"]:
                    key = (
                        "managed/"
                        + fingerprint({"arn": policy["arn"]})
                        + "/"
                        + version["version_id"]
                        if policy["kind"] == "managed"
                        else f"role/{role['id']}/policy/{pindex}/{version['version_id']}"
                    )
                    doc = version["document"]
                    record = {
                        "policy_kind": policy["kind"],
                        "policy_arn": policy.get("arn"),
                        "inline_name": policy.get("name"),
                        "version_id": version["version_id"],
                        "is_default": version["default"],
                        "document": doc,
                    }
                    evidence = node(
                        N.EVIDENCE,
                        key + "/evidence",
                        {
                            "evidence_type": "aws-normalized-policy",
                            "content_sha256": fingerprint(record),
                            "record_json": json.dumps(record, sort_keys=True),
                        },
                    )
                    batch.edge(evidence, event, R.DERIVED_FROM)
                    if not version["default"]:
                        continue
                    unresolved = any(
                        statement["unsupported"]
                        or any(
                            not resource.startswith("arn:")
                            or "*" in resource
                            or "?" in resource
                            or len(resource.split(":", 5)) != 6
                            or resource.split(":", 5)[4] not in {"", self.repository.account}
                            for resource in statement["resources"]
                        )
                        for statement in doc["statements"]
                    )
                    props = {
                        "policy_type": "TrustPolicy"
                        if policy["kind"] == "trust"
                        else "IdentityPolicy",
                        "display_name": "AWS " + policy["kind"] + " policy",
                        "document_sha256": fingerprint(doc),
                    }
                    if policy.get("arn"):
                        props["arn"] = policy["arn"]
                    if unresolved:
                        props["policy_type"] = "UnresolvedPolicy"
                    mapped = node(N.POLICY, key, props)
                    batch.edge(current, mapped, R.HAS_POLICY, event["id"])
                    if unresolved:
                        # Unknown denies/resource patterns must not silently permit other grants.
                        continue
                    for sindex, statement in enumerate(doc["statements"]):
                        if statement["unsupported"]:
                            continue
                        for aindex, action in enumerate(statement["actions"]):
                            attrs = {"action": action, "effect": statement["effect"]}
                            if statement["conditions_redacted"]:
                                attrs["condition_sha256"] = fingerprint(
                                    {"condition_values_redacted": True}
                                )
                            permission = node(
                                N.PERMISSION, key + f"/statement/{sindex}/action/{aindex}", attrs
                            )
                            batch.edge(mapped, permission, R.GRANTS, event["id"])
                            # Trust and unscoped resources remain evidence only.
                            if policy["kind"] == "trust":
                                continue
                            for resource in statement["resources"]:
                                parts = resource.split(":", 5)
                                if (
                                    len(parts) != 6
                                    or parts[0] != "arn"
                                    or parts[4] not in {"", self.repository.account}
                                ):
                                    continue
                                target = node(
                                    N.RESOURCE,
                                    "resource/" + fingerprint({"arn": resource}),
                                    {
                                        "arn": resource,
                                        "resource_type": "aws:policy-reference",
                                        "resource_policy_required": parts[4] == "",
                                    },
                                )
                                batch.edge(permission, target, R.TARGETS, event["id"])
        return {
            "roles_observed": len(snapshot["roles"]),
            "complete_for_returned_pages": True,
            "authorization_complete": False,
            "condition_operands_redacted": True,
            "observed_at": utc_now().isoformat(),
            "region_scope": "aws-global",
        }

    def trail(self, batch, snapshot, request: CloudTrailRequest):
        for record in snapshot["events"]:
            at = epoch_us(datetime.fromisoformat(record["event_time"]))
            key = record["region"] + "/" + record["event_id"]
            event = batch.node(
                N.SECURITY_EVENT,
                key,
                {
                    "event_type": record["event_type"],
                    "event_time": at,
                    "region": record["region"],
                    "source_api": record["source_api"],
                    "content_sha256": fingerprint(record),
                },
            )
            evidence = batch.node(
                N.EVIDENCE,
                key + "/evidence",
                {
                    "evidence_type": "aws-cloudtrail-normalized",
                    "content_sha256": fingerprint(record),
                    "record_json": json.dumps(record, sort_keys=True),
                    "identity_refs_json": json.dumps(record["identity_refs"]),
                    "source_api": record["source_api"],
                    "region": record["region"],
                },
            )
            batch.edge(evidence, event, R.DERIVED_FROM, at=at)
            reference = record["identity_refs"]
            if reference.get("accountId") == self.repository.account and reference.get("arn"):
                from app.integrations.aws_readers import scoped_arn

                arn = scoped_arn(reference["arn"], self.repository.account)
                identity = batch.node(
                    N.IDENTITY,
                    key + "/principal",
                    {
                        "arn": arn,
                        "identity_type": reference.get("type")
                        if reference.get("type")
                        in {"IAMUser", "AssumedRole", "Root", "AWSService", "FederatedUser"}
                        else "Unknown",
                        "source_api": record["source_api"],
                        "region": record["region"],
                    },
                )
                batch.edge(event, identity, R.AFFECTS, at=at)
        return {
            "start_time": request.start_time.isoformat(),
            "end_time": request.end_time.isoformat(),
            "region": self.repository.settings.aws_region,
            "events_imported": len(snapshot["events"]),
            "irrelevant_records_skipped": snapshot["skipped"],
            "history_days_maximum": 90,
            "management_events_only": True,
            "complete_audit_history": False,
        }

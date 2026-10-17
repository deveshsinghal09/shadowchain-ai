"""Bounded AWS readers. Raw CloudTrail payloads and credential fields never leave this layer."""

import json
from datetime import UTC, datetime
from urllib.parse import unquote

from botocore.exceptions import ClientError

from app.db.cypher import fingerprint
from app.models.graph import reject_secret
from app.services.aws_errors import AwsImportLimit, AwsOperationError, AwsScopeError

EVENT_SOURCES = ("iam.amazonaws.com", "sts.amazonaws.com", "s3.amazonaws.com")
EVENT_NAMES = frozenset(
    {
        "CreateRole",
        "DeleteRole",
        "UpdateAssumeRolePolicy",
        "AttachRolePolicy",
        "DetachRolePolicy",
        "PutRolePolicy",
        "DeleteRolePolicy",
        "CreatePolicyVersion",
        "DeletePolicyVersion",
        "SetDefaultPolicyVersion",
        "PutRolePermissionsBoundary",
        "DeleteRolePermissionsBoundary",
        "AssumeRole",
        "PutBucketPolicy",
        "DeleteBucketPolicy",
        "PutBucketAcl",
        "PutBucketPublicAccessBlock",
    }
)


def scoped_arn(value, account, *, managed=False):
    if not isinstance(value, str) or len(value) > 2048:
        raise AwsImportLimit()
    reject_secret(value)
    parts = value.split(":", 5)
    if len(parts) != 6 or parts[0] != "arn" or parts[1] not in {"aws", "aws-us-gov", "aws-cn"}:
        raise AwsScopeError()
    if parts[4] != account and not (managed and parts[4] == "aws" and parts[2] == "iam"):
        raise AwsScopeError()
    return value


def document(value):
    try:
        value = json.loads(unquote(value)) if isinstance(value, str) else value
        if not isinstance(value, dict):
            raise AwsImportLimit()
        statements = value.get("Statement", [])
        statements = [statements] if isinstance(statements, dict) else statements
        if not isinstance(statements, list) or len(statements) > 100:
            raise AwsImportLimit()
        result = []
        for statement in statements:
            if not isinstance(statement, dict) or statement.get("Effect") not in {"Allow", "Deny"}:
                raise AwsImportLimit()

            def texts(key, statement=statement):
                raw = statement.get(key, [])
                raw = [raw] if isinstance(raw, str) else raw
                if not isinstance(raw, list) or len(raw) > 100:
                    raise AwsImportLimit()
                return sorted(
                    reject_secret(v) for v in raw if isinstance(v, str) and len(v) <= 2048
                )

            # Condition operands/tags/description/Sid are intentionally never retained.
            condition = statement.get("Condition")
            selectors = statement.get("Principal", {})
            principals = []
            if isinstance(selectors, dict):
                for kind in ("AWS", "Service"):
                    items = selectors.get(kind, [])
                    items = [items] if isinstance(items, str) else items
                    if not isinstance(items, list) or len(items) > 100:
                        raise AwsImportLimit()
                    principals.extend(
                        reject_secret(v) for v in items if isinstance(v, str) and len(v) <= 2048
                    )
            elif selectors == "*":
                principals = ["*"]
            result.append(
                {
                    "effect": statement["Effect"],
                    "actions": texts("Action"),
                    "resources": texts("Resource"),
                    "principals": sorted(principals),
                    "conditions_redacted": bool(condition),
                    "unsupported": any(
                        key in statement for key in ("NotAction", "NotResource", "NotPrincipal")
                    ),
                }
            )
        return {"statements": result, "condition_values_retained": False}
    except (ValueError, TypeError) as exc:
        raise AwsImportLimit() from exc


def iam_inventory(gateway, account):
    gateway.validate_account(account)
    roles, policy_cache = [], {}

    def get(operation, **params):
        try:
            return gateway.call("iam", operation, **params)
        except ClientError as exc:
            raise AwsOperationError() from exc

    for listed in gateway.pages("iam", "list_roles", "Roles", MaxItems=100):
        arn = scoped_arn(listed["Arn"], account)
        if ":iam::" not in arn or ":role/" not in arn:
            raise AwsScopeError()
        role = get("get_role", RoleName=listed["RoleName"])["Role"]
        if scoped_arn(role["Arn"], account) != arn or role["RoleId"] != listed["RoleId"]:
            raise AwsScopeError()
        observed = {
            "id": reject_secret(role["RoleId"]),
            "name": reject_secret(role["RoleName"]),
            "arn": arn,
            "trust": document(role["AssumeRolePolicyDocument"]),
            "policies": [],
            "boundary_required": bool(role.get("PermissionsBoundary")),
        }
        for attached in gateway.pages(
            "iam",
            "list_attached_role_policies",
            "AttachedPolicies",
            RoleName=role["RoleName"],
            MaxItems=100,
        ):
            policy_arn = scoped_arn(attached["PolicyArn"], account, managed=True)
            if policy_arn not in policy_cache:
                metadata = get("get_policy", PolicyArn=policy_arn)["Policy"]
                if metadata["Arn"] != policy_arn:
                    raise AwsScopeError()
                versions = []
                for version in gateway.pages(
                    "iam", "list_policy_versions", "Versions", PolicyArn=policy_arn, MaxItems=100
                ):
                    detail = get(
                        "get_policy_version", PolicyArn=policy_arn, VersionId=version["VersionId"]
                    )["PolicyVersion"]
                    if detail["VersionId"] != version["VersionId"] or detail.get(
                        "IsDefaultVersion"
                    ) is not (detail["VersionId"] == metadata["DefaultVersionId"]):
                        raise AwsImportLimit()
                    versions.append(
                        {
                            "version_id": detail["VersionId"],
                            "default": detail["VersionId"] == metadata["DefaultVersionId"],
                            "document": document(detail["Document"]),
                        }
                    )
                if sum(v["default"] for v in versions) != 1:
                    raise AwsImportLimit()
                policy_cache[policy_arn] = {
                    "arn": policy_arn,
                    "kind": "managed",
                    "versions": versions,
                }
            observed["policies"].append(policy_cache[policy_arn])
        for name in gateway.pages(
            "iam", "list_role_policies", "PolicyNames", RoleName=role["RoleName"], MaxItems=100
        ):
            inline = get("get_role_policy", RoleName=role["RoleName"], PolicyName=name)
            if inline["RoleName"] != role["RoleName"] or inline["PolicyName"] != name:
                raise AwsImportLimit()
            observed["policies"].append(
                {
                    "name": reject_secret(name),
                    "kind": "inline",
                    "versions": [
                        {
                            "version_id": "observed",
                            "default": True,
                            "document": document(inline["PolicyDocument"]),
                        }
                    ],
                }
            )
        roles.append(observed)
    return {"roles": roles}


def cloudtrail_events(gateway, account, start, end):
    gateway.validate_account(account)
    found, skipped = {}, 0
    for source in EVENT_SOURCES:
        for item in gateway.pages(
            "cloudtrail",
            "lookup_events",
            "Events",
            StartTime=start,
            EndTime=end,
            MaxResults=50,
            LookupAttributes=[{"AttributeKey": "EventSource", "AttributeValue": source}],
        ):
            try:
                if len(item["CloudTrailEvent"]) > 1_000_000:
                    raise AwsImportLimit()
                raw = json.loads(item["CloudTrailEvent"])
                if raw.get("recipientAccountId") != account:
                    raise AwsScopeError()
                if (
                    raw.get("eventSource") != source
                    or raw.get("awsRegion") != gateway.settings.aws_region
                ):
                    raise AwsScopeError()
                if (
                    raw.get("eventName") not in EVENT_NAMES
                    or raw.get("managementEvent") is not True
                ):
                    skipped += 1
                    continue
                if raw.get("eventID") != item["EventId"]:
                    raise AwsImportLimit()
                timestamp = datetime.fromisoformat(
                    raw["eventTime"].replace("Z", "+00:00")
                ).astimezone(UTC)
                if not start <= timestamp <= end or timestamp != item["EventTime"].astimezone(UTC):
                    raise AwsImportLimit()
                identity = raw.get("userIdentity", {})
                references = {
                    k: reject_secret(str(identity[k]))
                    for k in ("type", "arn", "accountId")
                    if k in identity
                }
                issuer = identity.get("sessionContext", {}).get("sessionIssuer", {}).get("arn")
                if issuer:
                    references["session_issuer_arn"] = reject_secret(issuer)
                normalized = {
                    "event_id": reject_secret(item["EventId"]),
                    "account_id": account,
                    "region": raw["awsRegion"],
                    "source_api": source + ":" + raw["eventName"],
                    "event_type": raw["eventName"],
                    "event_time": timestamp.isoformat(),
                    "identity_refs": references,
                }
                previous = found.get(item["EventId"])
                if previous and fingerprint(previous) != fingerprint(normalized):
                    raise AwsImportLimit()
                found[item["EventId"]] = normalized
                if len(found) > gateway.settings.aws_max_records:
                    raise AwsImportLimit()
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise AwsImportLimit() from exc
    return {"events": [found[k] for k in sorted(found)], "skipped": skipped}

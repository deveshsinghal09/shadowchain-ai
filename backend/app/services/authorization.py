"""Conservative normalized IAM subset. This is not an AWS authorization simulator."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import ip_address, ip_network
from typing import Any

from app.db.cypher import fingerprint
from app.models.graph import epoch_us, from_epoch_us
from app.schemas.risk import AuthorizationContext

SUPPORTED_POLICY_TYPES = {
    "IdentityPolicy",
    "TrustPolicy",
    "ResourcePolicy",
    "PermissionsBoundary",
    "SessionPolicy",
    "ServiceControlPolicy",
    "ResourceControlPolicy",
}


def action_matches(pattern: str, action: str) -> bool:
    # IAM uses only '*' and '?'. A bounded wildcard matcher also avoids regex
    # backtracking on attacker-controlled normalized permission patterns.
    pattern, action = pattern.lower(), action.lower()
    p = a = 0
    star = -1
    retry_at = 0
    while a < len(action):
        if p < len(pattern) and (pattern[p] == "?" or pattern[p] == action[a]):
            p, a = p + 1, a + 1
        elif p < len(pattern) and pattern[p] == "*":
            star, retry_at, p = p, a, p + 1
        elif star >= 0:
            retry_at += 1
            p, a = star + 1, retry_at
        else:
            return False
    return all(character == "*" for character in pattern[p:])


def condition_result(permission: dict, context: dict[str, Any]) -> bool | None:
    """Three-valued AND/OR evaluation; unknown Allow cannot authorize a candidate.

    An unknown applicable Deny is also unresolved, rather than silently ignored.
    Absent request context is unknown, not a claim that the key is absent in AWS.
    """
    encoded = permission.get("conditions_json")
    if not encoded:
        return None if permission.get("condition_sha256") else True
    try:
        conditions = json.loads(encoded)
        if not isinstance(conditions, dict) or not conditions:
            return None
        if permission.get("condition_sha256") and permission["condition_sha256"] != fingerprint(
            conditions
        ):
            return None
        results = []
        for operator, tests in conditions.items():
            if (
                operator
                not in {
                    "Bool",
                    "StringEquals",
                    "StringNotEquals",
                    "ArnEquals",
                    "IpAddress",
                    "NotIpAddress",
                    "DateLessThan",
                    "DateGreaterThan",
                }
                or not isinstance(tests, dict)
                or not tests
            ):
                results.append(None)
                continue
            for key, expected in tests.items():
                actual = context.get(key)
                values = expected if isinstance(expected, list) else [expected]
                if actual is None or not values or any(type(v) not in {str, bool} for v in values):
                    results.append(None)
                    continue
                matches = []
                for value in values:
                    if operator == "Bool":
                        if str(value).lower() not in {"true", "false"} or type(actual) is not bool:
                            raise ValueError("Invalid Bool operand")
                        matches.append(actual == (str(value).lower() == "true"))
                    elif operator in {"StringEquals", "StringNotEquals", "ArnEquals"}:
                        matches.append(type(actual) is str and actual == value)
                    elif operator in {"IpAddress", "NotIpAddress"}:
                        matches.append(ip_address(actual) in ip_network(value, strict=False))
                    else:
                        limit = epoch_us(datetime.fromisoformat(value.replace("Z", "+00:00")))
                        current = epoch_us(datetime.fromisoformat(actual))
                        matches.append(
                            current < limit if operator == "DateLessThan" else current > limit
                        )
                result = any(matches)
                if operator in {"StringNotEquals", "NotIpAddress"}:
                    result = not result
                results.append(result)
        if False in results:
            return False
        return None if None in results else True
    except (ValueError, TypeError, AttributeError):
        return None


@dataclass
class AuthorizationDecision:
    accepted: bool = True
    reason: str = "supported modeled permissions satisfied"
    unresolved: bool = False
    supporting_ids: set[str] = field(default_factory=set)
    assumptions: list[str] = field(default_factory=list)
    mfa_constrained: bool = False
    mfa_permission_ids: set[str] = field(default_factory=set)


class PolicyEvaluator:
    def __init__(self, bundle: dict, request_context: AuthorizationContext) -> None:
        self.bundle, self.request_context = bundle, request_context
        self.attachments = bundle["attachments"]
        self.statements = bundle["statements"]

    def ancestors(self, node_id: str) -> list[str]:
        return sorted(
            {
                n["id"]
                for lineage in self.bundle["lineages"]
                if lineage["nodes"][0]["id"] == node_id
                and all(e.get("verification_status") == "verified" for e in lineage["edges"])
                for n in lineage["nodes"][1:]
                if n["kind"] in {"CloudAccount", "CloudOrganization"}
            }
        )

    def condition_context(self, principal: dict) -> dict:
        return {
            "aws:MultiFactorAuthPresent": self.request_context.mfa_present,
            "aws:SourceIp": self.request_context.source_ip,
            "aws:RequestedRegion": self.request_context.requested_region,
            "aws:PrincipalArn": principal.get("arn"),
            "aws:CurrentTime": from_epoch_us(self.bundle["at"]).isoformat(),
        }

    def evaluate(
        self, principal: dict, target: dict, action: str, *, assume_role: bool = False
    ) -> AuthorizationDecision:
        decision = AuthorizationDecision()
        caller_id, target_id = principal["id"], target["id"]
        context = self.condition_context(principal)
        if any(
            path["nodes"][0]["id"] in {caller_id, target_id}
            and "rejected" not in {e.get("verification_status") for e in path["edges"]}
            and any(e.get("verification_status") != "verified" for e in path["edges"])
            for path in self.bundle["lineages"]
        ):
            return AuthorizationDecision(
                False, "account/organization membership is uncertain", True
            )
        principal_ancestors, target_ancestors = self.ancestors(caller_id), self.ancestors(target_id)
        if any(
            len(path["edges"]) > 2 and path["nodes"][0]["id"] in {caller_id, target_id}
            for path in self.bundle["lineages"]
        ):
            return AuthorizationDecision(
                False, "account/organization hierarchy exceeds two hops", True
            )
        # Groups are intersected across policy types/ancestor levels. Policies of one
        # type at the same owner are unioned, with explicit Deny taking precedence.
        groups: dict[tuple[str, str], list[dict]] = {}
        relevant_owners = {caller_id, target_id, *principal_ancestors, *target_ancestors}
        for attachment in self.attachments:
            if not attachment.get("active", True):
                continue
            owner, policy = attachment["nodes"]
            policy_type = policy.get("policy_type", "")
            if owner["id"] not in relevant_owners:
                continue
            if policy_type not in SUPPORTED_POLICY_TYPES:
                return AuthorizationDecision(False, "unsupported attached policy type", True)
            relevant = (
                (
                    owner["id"] == caller_id
                    and policy_type
                    in {
                        "IdentityPolicy",
                        "PermissionsBoundary",
                        "SessionPolicy",
                    }
                )
                or (
                    owner["id"] == target_id
                    and policy_type == ("TrustPolicy" if assume_role else "ResourcePolicy")
                )
                or (owner["id"] in principal_ancestors and policy_type == "ServiceControlPolicy")
                or (owner["id"] in target_ancestors and policy_type == "ResourceControlPolicy")
            )
            if relevant:
                statuses = {edge.get("verification_status") for edge in attachment["edges"]}
                if "rejected" in statuses:
                    continue
                if statuses != {"verified"}:
                    return AuthorizationDecision(
                        False, "policy attachment is unverified or disputed", True
                    )
                groups.setdefault((owner["id"], policy_type), []).append(policy)
        if any(
            kind == "PermissionsBoundary" and len(policies) > 1
            for (_, kind), policies in groups.items()
        ):
            return AuthorizationDecision(False, "multiple modeled permissions boundaries", True)
        for required, owner, policy_type in (
            (principal.get("boundary_required"), caller_id, "PermissionsBoundary"),
            (principal.get("session_policy_required"), caller_id, "SessionPolicy"),
            (principal.get("scp_required"), None, "ServiceControlPolicy"),
            (target.get("rcp_required"), None, "ResourceControlPolicy"),
            (target.get("resource_policy_required"), target_id, "ResourcePolicy"),
        ):
            if required and not any(
                t == policy_type and (owner is None or o == owner) for o, t in groups
            ):
                return AuthorizationDecision(
                    False, "required authorization constraint is missing", True
                )
        group_allows: dict[tuple[str, str], list[dict]] = {key: [] for key in groups}
        for statement in self.statements:
            if not statement.get("active", True):
                continue
            owner, policy, permission, destination = statement["nodes"]
            group = (owner["id"], policy.get("policy_type"))
            if group not in groups:
                continue
            expected_target = caller_id if assume_role and group[1] == "TrustPolicy" else target_id
            if destination["id"] != expected_target or not action_matches(
                permission.get("action", ""), action
            ):
                continue
            statuses = {edge.get("verification_status") for edge in statement["edges"]}
            if "rejected" in statuses:
                continue
            if statuses != {"verified"}:
                return AuthorizationDecision(
                    False, "applicable policy statement is unverified or disputed", True
                )
            if group[1] == "ResourcePolicy":
                try:
                    selectors = json.loads(permission.get("principal_arns_json", "null"))
                    if (
                        not isinstance(selectors, list)
                        or not selectors
                        or any(type(s) is not str for s in selectors)
                    ):
                        return AuthorizationDecision(
                            False, "resource-policy principal selector is unresolved", True
                        )
                    if "*" not in selectors and principal.get("arn") not in selectors:
                        continue
                except (ValueError, TypeError):
                    return AuthorizationDecision(
                        False, "resource-policy principal selector is unresolved", True
                    )
            result = condition_result(permission, context)
            if result is False:
                continue
            if result is None:
                return AuthorizationDecision(
                    False, "condition or request context is unresolved", True
                )
            if permission.get("effect") == "Deny":
                return AuthorizationDecision(False, "applicable explicit Deny")
            if permission.get("effect") != "Allow":
                return AuthorizationDecision(False, "unsupported permission effect", True)
            group_allows[group].append(statement)
        if not group_allows.get((caller_id, "IdentityPolicy")):
            return AuthorizationDecision(False, "no supported identity-policy Allow")
        if assume_role and not group_allows.get((target_id, "TrustPolicy")):
            return AuthorizationDecision(False, "no supported role-trust Allow")
        for (_owner, policy_type), statements in group_allows.items():
            if (
                policy_type
                in {
                    "PermissionsBoundary",
                    "SessionPolicy",
                    "ServiceControlPolicy",
                    "ResourceControlPolicy",
                }
                and not statements
            ):
                return AuthorizationDecision(False, f"{policy_type} does not allow this request")
        cross_account = principal.get("account_id") != target.get("account_id")
        if not assume_role and (cross_account or target.get("resource_policy_required")):
            if not group_allows.get((target_id, "ResourcePolicy")):
                return AuthorizationDecision(
                    False, "required cross-account/resource-policy Allow is missing"
                )
        for statements in group_allows.values():
            for statement in statements:
                decision.supporting_ids.update(edge["id"] for edge in statement["edges"])
                encoded = statement["nodes"][2].get("conditions_json", "")
                if encoded:
                    decision.assumptions.append(
                        "Supported conditions evaluated against the supplied request context "
                        "and graph-derived principal/time"
                    )
                    try:
                        conditions = json.loads(encoded)
                        mfa = conditions.get("Bool", {}).get("aws:MultiFactorAuthPresent")
                        if str(mfa).lower() == "true":
                            decision.mfa_permission_ids.add(statement["nodes"][2]["id"])
                    except (ValueError, AttributeError):
                        pass
        required_groups = [
            statements
            for (_, policy_type), statements in group_allows.items()
            if policy_type != "ResourcePolicy"
            or cross_account
            or target.get("resource_policy_required")
        ]
        decision.mfa_constrained = any(
            statements
            and all(s["nodes"][2]["id"] in decision.mfa_permission_ids for s in statements)
            for statements in required_groups
        )
        if not decision.mfa_constrained:
            decision.mfa_permission_ids.clear()
        return decision

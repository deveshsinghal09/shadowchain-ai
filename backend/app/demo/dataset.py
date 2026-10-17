"""Versioned seed templates only. FalkorDB owns all replay and investigation state."""

from dataclasses import dataclass
from datetime import UTC, datetime

from app.db.cypher import fingerprint
from app.models.events import (
    DATASET_VERSION,
    DEMO_ACCOUNTS,
    EdgeSpec,
    NodeSpec,
    Scenario,
    SyntheticSecurityEvent,
)
from app.models.graph import NodeKind as N
from app.models.graph import RelationshipKind as R

BASE_TIME = datetime(2026, 10, 16, 9, tzinfo=UTC)
HISTORY_TIME = datetime(2026, 10, 15, 16, tzinfo=UTC)
DEV, PROD = DEMO_ACCOUNTS


def hour(value: int) -> datetime:
    return BASE_TIME.replace(hour=value)


@dataclass(frozen=True)
class DatasetDefinition:
    nodes: tuple[NodeSpec, ...]
    edges: tuple[EdgeSpec, ...]
    events: dict[Scenario, tuple[SyntheticSecurityEvent, ...]]

    @property
    def manifest_sha256(self) -> str:
        return fingerprint(
            {
                "version": DATASET_VERSION,
                "nodes": [node.model_dump(mode="json") for node in self.nodes],
                "edges": [edge.model_dump(mode="json") for edge in self.edges],
                "events": {
                    key: [event.model_dump(mode="json") for event in events]
                    for key, events in self.events.items()
                },
            }
        )


def dataset_definition() -> DatasetDefinition:
    nodes: list[NodeSpec] = []
    edges: list[EdgeSpec] = []

    def node(key: str, kind: N, account: str | None = None, **attributes: object) -> None:
        nodes.append(NodeSpec(key=key, kind=kind, account_id=account, attributes=attributes))

    def edge(key: str, kind: R, source: str, target: str) -> None:
        edges.append(EdgeSpec(key=key, kind=kind, source_key=source, target_key=target))

    node(
        "organization/shadowchain",
        N.CLOUD_ORGANIZATION,
        display_name="SYNTHETIC ShadowChain Organization",
        organization_id="o-shadowchainsynthetic",
    )
    for account, environment in ((DEV, "development"), (PROD, "production")):
        key = f"account/{environment}"
        node(
            key,
            N.CLOUD_ACCOUNT,
            account,
            display_name=f"SYNTHETIC {environment}",
            provider="aws-synthetic",
            account_number=account,
        )
        edge(f"org/{environment}", R.BELONGS_TO, key, "organization/shadowchain")

    identities = [
        "alice",
        "developer",
        "auditor",
        "ci-user",
        "devops",
        "analyst",
        "bob",
        "secops",
        "backup-workload",
        "finance-user",
        "billing-user",
        "platform-prod",
    ]
    for index, name in enumerate(identities):
        account = DEV if index < 6 else PROD
        node(
            f"identity/{name}",
            N.IDENTITY,
            account,
            display_name=f"SYNTHETIC {name}",
            arn=f"arn:aws:iam::{account}:user/shadowchain-synthetic-{name}",
            identity_type="IAMUser",
        )

    roles = [
        ("temp-analyst", DEV),
        ("ec2-backup", PROD),
        ("data-reader", PROD),
        ("ec2-web", DEV),
        ("ec2-batch", PROD),
        ("incident-responder", PROD),
        ("audit-reader", DEV),
        ("ci-deploy", DEV),
        ("security-review", PROD),
        ("breakglass", PROD),
    ]
    for name, account in roles:
        attributes = {
            "display_name": f"SYNTHETIC {name}",
            "arn": f"arn:aws:iam::{account}:role/shadowchain-synthetic-{name}",
            "description": "Synthetic IAM role; never deployed to AWS",
        }
        if name.startswith("ec2-"):
            attributes.update(
                service_principal="ec2.amazonaws.com",
                instance_profile=f"shadowchain-synthetic-{name}-profile",
            )
        node(f"role/{name}", N.ROLE, account, **attributes)

    policies = [
        ("public-read", DEV, "IdentityPolicy"),
        ("sensitive-read", PROD, "IdentityPolicy"),
        ("finance-deny", PROD, "IdentityPolicy"),
        ("delete-deny", PROD, "IdentityPolicy"),
        ("assume-temp", DEV, "IdentityPolicy"),
        ("trust-developers", DEV, "TrustPolicy"),
        ("assume-backup", DEV, "IdentityPolicy"),
        ("trust-temp", PROD, "TrustPolicy"),
        ("ec2-logs", DEV, "IdentityPolicy"),
        ("ec2-metrics", PROD, "IdentityPolicy"),
        ("incident-read", PROD, "IdentityPolicy"),
        ("trust-bob", PROD, "TrustPolicy"),
        ("audit-assume", DEV, "IdentityPolicy"),
        ("assume-incident", PROD, "IdentityPolicy"),
        ("sensitive-deny", PROD, "IdentityPolicy"),
    ]
    policy_accounts = {}
    for name, account, policy_type in policies:
        policy_accounts[name] = account
        node(
            f"policy/{name}",
            N.POLICY,
            account,
            display_name=f"SYNTHETIC {name}",
            policy_type=policy_type,
            arn=f"arn:aws:iam::{account}:policy/shadowchain-synthetic-{name}",
            document_sha256=fingerprint({"synthetic_policy": name, "version": DATASET_VERSION}),
        )

    buckets = [
        "hr-data",
        "finance-data",
        "incident-data",
        "customer-data",
        "public-assets",
        "build-artifacts",
        "web-logs",
        "training-samples",
    ]
    for index, name in enumerate(buckets):
        account = PROD if index < 4 else DEV
        bucket_name = "shadowchain-synthetic-" + name
        node(
            f"resource/{name}",
            N.RESOURCE,
            account,
            display_name=f"SYNTHETIC S3 {name}",
            arn=f"arn:aws:s3:::{bucket_name}",
            object_arn=f"arn:aws:s3:::{bucket_name}/*",
            resource_type="s3:bucket",
            region="us-east-1",
            sensitive=index < 4,
        )
    resource_types = (
        ["ec2:instance"] * 4
        + ["dynamodb:table"] * 3
        + ["kms:key"] * 2
        + ["ec2:volume"] * 2
        + ["logs:log-group"]
    )
    for index, resource_type in enumerate(resource_types):
        service, resource_class = resource_type.split(":")
        name = f"synthetic-{resource_class}-{index + 1:02}"
        account = DEV if index % 2 == 0 or service == "logs" else PROD
        node(
            f"resource/{name}",
            N.RESOURCE,
            account,
            display_name=f"SYNTHETIC {name}",
            arn=f"arn:aws:{service}:us-east-1:{account}:{resource_class}/shadowchain-{name}",
            resource_type=resource_type,
            region="us-east-1",
            sensitive=False,
        )

    for existing in tuple(nodes):
        if existing.account_id and existing.kind != N.CLOUD_ACCOUNT:
            account_key = (
                "account/development" if existing.account_id == DEV else "account/production"
            )
            edge("account/" + existing.key, R.BELONGS_TO, existing.key, account_key)

    policy_statements: dict[str, list[dict]] = {}

    def permission(policy: str, suffix: str, action: str, effect: str, targets: list[str]) -> None:
        key = f"permission/{policy}/{suffix}"
        node(key, N.PERMISSION, policy_accounts[policy], action=action, effect=effect)
        edge(f"grant/{policy}/{suffix}", R.GRANTS, f"policy/{policy}", key)
        for target in targets:
            edge(f"target/{policy}/{suffix}/{target}", R.TARGETS, key, target)
        references = {item.key: item for item in nodes}
        statement = {"Effect": effect, "Action": action}
        target_arns = [
            references[target].attributes.get("object_arn", references[target].attributes["arn"])
            if action in {"s3:GetObject", "s3:DeleteObject"}
            else references[target].attributes["arn"]
            for target in targets
        ]
        if dict((p[0], p[2]) for p in policies)[policy] == "TrustPolicy":
            statement["Principal"] = {"AWS": sorted(target_arns)}
        else:
            statement["Resource"] = sorted(target_arns)
        policy_statements.setdefault(policy, []).append(statement)

    permission("public-read", "objects", "s3:GetObject", "Allow", ["resource/public-assets"])
    permission(
        "sensitive-read",
        "objects",
        "s3:GetObject",
        "Allow",
        ["resource/hr-data", "resource/finance-data"],
    )
    permission("finance-deny", "objects", "s3:GetObject", "Deny", ["resource/finance-data"])
    permission(
        "delete-deny",
        "objects",
        "s3:DeleteObject",
        "Deny",
        [f"resource/{name}" for name in buckets[:4]],
    )
    permission("assume-temp", "sts", "sts:AssumeRole", "Allow", ["role/temp-analyst"])
    permission(
        "trust-developers",
        "sts",
        "sts:AssumeRole",
        "Allow",
        ["identity/alice", "identity/auditor", "identity/ci-user", "identity/devops"],
    )
    permission("assume-backup", "sts", "sts:AssumeRole", "Allow", ["role/ec2-backup"])
    permission("trust-temp", "sts", "sts:AssumeRole", "Allow", ["role/temp-analyst"])
    permission(
        "ec2-logs", "logs", "logs:PutLogEvents", "Allow", ["resource/synthetic-log-group-12"]
    )
    permission(
        "ec2-metrics",
        "tagging",
        "ec2:CreateTags",
        "Allow",
        ["resource/synthetic-instance-02"],
    )
    permission("incident-read", "objects", "s3:GetObject", "Allow", ["resource/incident-data"])
    permission("trust-bob", "sts", "sts:AssumeRole", "Allow", ["identity/bob"])
    permission(
        "audit-assume",
        "sts",
        "sts:AssumeRole",
        "Allow",
        ["role/audit-reader", "role/ec2-web", "role/ci-deploy"],
    )
    permission("assume-incident", "sts", "sts:AssumeRole", "Allow", ["role/incident-responder"])
    permission(
        "sensitive-deny",
        "objects",
        "s3:GetObject",
        "Deny",
        ["resource/hr-data", "resource/finance-data"],
    )
    nodes = [
        item.model_copy(
            update={
                "attributes": {
                    **item.attributes,
                    "document_sha256": fingerprint(
                        {
                            "Version": "2012-10-17",
                            "Statement": policy_statements[item.key.split("/")[1]],
                        }
                    ),
                }
            }
        )
        if item.kind == N.POLICY
        else item
        for item in nodes
    ]

    attachments = [
        ("role/ec2-web", "public-read"),
        ("role/ec2-web", "ec2-logs"),
        ("role/ec2-batch", "ec2-metrics"),
        ("role/ec2-backup", "finance-deny"),
        ("role/ec2-backup", "delete-deny"),
        ("role/data-reader", "sensitive-read"),
        ("role/incident-responder", "incident-read"),
        ("role/breakglass", "sensitive-read"),
        ("role/breakglass", "sensitive-deny"),
    ]
    for principal in ("identity/auditor", "identity/ci-user", "identity/devops"):
        attachments.append((principal, "audit-assume"))
    for role in ("role/audit-reader", "role/ec2-web", "role/ci-deploy"):
        attachments.append((role, "trust-developers"))
    for principal, policy in attachments:
        edge(f"attach/{principal}/{policy}", R.HAS_POLICY, principal, f"policy/{policy}")
    for identity, role in (
        ("auditor", "audit-reader"),
        ("ci-user", "ec2-web"),
        ("devops", "ci-deploy"),
    ):
        edge(f"assume/{identity}/{role}", R.CAN_ASSUME, f"identity/{identity}", f"role/{role}")

    node(
        "agent/scout",
        N.AGENT,
        name="SYNTHETIC configuration scout",
        agent_type="deterministic",
        version="1",
    )
    node(
        "agent/verifier",
        N.AGENT,
        name="SYNTHETIC evidence verifier",
        agent_type="deterministic",
        version="1",
    )
    node(
        "procedure/review",
        N.PROCEDURE,
        name="SYNTHETIC access review",
        version="1",
        instructions="Compare temporal grants and preserve supporting and contradicting evidence.",
    )
    for number in range(1, 4):
        prefix = f"history/{number}"
        node(
            prefix + "/investigation",
            N.INVESTIGATION,
            title=f"SYNTHETIC historical investigation {number}",
            status="resolved",
            summary="Earlier synthetic access review; evidence retained after resolution.",
        )
        node(
            prefix + "/finding",
            N.RISK_FINDING,
            title=f"SYNTHETIC historical finding {number}",
            severity="medium",
            status="resolved",
        )
        node(
            prefix + "/evidence",
            N.EVIDENCE,
            evidence_type="synthetic-audit-record",
            summary="Synthetic historical policy review",
            content_sha256=fingerprint({"history": number}),
        )
        edge(
            prefix + "/investigates", R.INVESTIGATES, prefix + "/investigation", prefix + "/finding"
        )
        edge(prefix + "/supports", R.SUPPORTS, prefix + "/evidence", prefix + "/finding")

    events: dict[Scenario, tuple[SyntheticSecurityEvent, ...]] = {}
    events[Scenario.A] = (
        SyntheticSecurityEvent(
            scenario=Scenario.A,
            source_id="a/temporary-access",
            account_id=DEV,
            sequence=1,
            occurred_at=hour(10),
            event_type="SyntheticTemporaryRoleAccessGranted",
            business_justification=(
                "SYNTHETIC analytics migration support, approved until 14:00 UTC."
            ),
            edges=[
                EdgeSpec(
                    key="a/identity-policy",
                    kind=R.HAS_POLICY,
                    source_key="identity/alice",
                    target_key="policy/assume-temp",
                ),
                EdgeSpec(
                    key="a/temp-trust",
                    kind=R.HAS_POLICY,
                    source_key="role/temp-analyst",
                    target_key="policy/trust-developers",
                ),
                EdgeSpec(
                    key="a/alice-temp",
                    kind=R.CAN_ASSUME,
                    source_key="identity/alice",
                    target_key="role/temp-analyst",
                    justification_expires_at=hour(14),
                ),
            ],
        ),
        SyntheticSecurityEvent(
            scenario=Scenario.A,
            source_id="a/backup-trust-change",
            account_id=PROD,
            sequence=2,
            occurred_at=hour(11),
            event_type="SyntheticBackupRoleTrustChanged",
            edges=[
                EdgeSpec(
                    key="a/temp-policy",
                    kind=R.HAS_POLICY,
                    source_key="role/temp-analyst",
                    target_key="policy/assume-backup",
                ),
                EdgeSpec(
                    key="a/backup-trust",
                    kind=R.HAS_POLICY,
                    source_key="role/ec2-backup",
                    target_key="policy/trust-temp",
                ),
                EdgeSpec(
                    key="a/temp-backup",
                    kind=R.CAN_ASSUME,
                    source_key="role/temp-analyst",
                    target_key="role/ec2-backup",
                ),
            ],
        ),
        SyntheticSecurityEvent(
            scenario=Scenario.A,
            source_id="a/sensitive-read-attachment",
            account_id=PROD,
            sequence=3,
            occurred_at=hour(12),
            event_type="SyntheticSensitiveReadPolicyAttached",
            edges=[
                EdgeSpec(
                    key="a/backup-sensitive-read",
                    kind=R.HAS_POLICY,
                    source_key="role/ec2-backup",
                    target_key="policy/sensitive-read",
                )
            ],
        ),
    )
    events[Scenario.B] = (
        SyntheticSecurityEvent(
            scenario=Scenario.B,
            source_id="b/emergency-grant",
            account_id=PROD,
            sequence=1,
            occurred_at=hour(10),
            event_type="SyntheticIncidentAccessGranted",
            business_justification=(
                "SYNTHETIC INC-204: investigate failed backup jobs; "
                "remove access when resolved, expected 11:00 UTC."
            ),
            nodes=[
                NodeSpec(
                    key="b/incident/v1",
                    logical_key="b/incident",
                    kind=N.INVESTIGATION,
                    attributes={"title": "SYNTHETIC INC-204", "status": "open"},
                )
            ],
            edges=[
                EdgeSpec(
                    key="b/bob-policy",
                    kind=R.HAS_POLICY,
                    source_key="identity/bob",
                    target_key="policy/assume-incident",
                ),
                EdgeSpec(
                    key="b/role-trust",
                    kind=R.HAS_POLICY,
                    source_key="role/incident-responder",
                    target_key="policy/trust-bob",
                ),
                EdgeSpec(
                    key="b/temporary-grant",
                    kind=R.CAN_ASSUME,
                    source_key="identity/bob",
                    target_key="role/incident-responder",
                    justification_key="b/incident/v1",
                    justification_expires_at=hour(11),
                ),
            ],
        ),
        SyntheticSecurityEvent(
            scenario=Scenario.B,
            source_id="b/incident-resolved",
            account_id=PROD,
            sequence=2,
            occurred_at=hour(11),
            event_type="SyntheticIncidentResolved",
            close_nodes=["b/incident/v1"],
            nodes=[
                NodeSpec(
                    key="b/incident/v2",
                    logical_key="b/incident",
                    kind=N.INVESTIGATION,
                    attributes={"title": "SYNTHETIC INC-204", "status": "resolved"},
                )
            ],
            edges=[
                EdgeSpec(
                    key="b/resolution-supersedes",
                    kind=R.SUPERSEDES,
                    source_key="b/incident/v2",
                    target_key="b/incident/v1",
                )
            ],
        ),
    )
    events[Scenario.C] = (
        SyntheticSecurityEvent(
            scenario=Scenario.C,
            source_id="c/suspicious-observation",
            account_id=PROD,
            sequence=1,
            occurred_at=hour(10),
            event_type="SyntheticSuspiciousConfigurationObserved",
            nodes=[
                NodeSpec(
                    key="c/evidence/acl",
                    kind=N.EVIDENCE,
                    attributes={
                        "evidence_type": "synthetic-acl-snapshot",
                        "summary": (
                            "SYNTHETIC ACL report suggests public read; "
                            "policy context not yet verified."
                        ),
                        "content_sha256": fingerprint({"acl": "public-read-observation"}),
                    },
                ),
                NodeSpec(
                    key="c/memory/scout",
                    kind=N.AGENT_MEMORY,
                    attributes={
                        "memory_type": "observation",
                        "content": (
                            "SYNTHETIC: suspicious public ACL on customer-data; "
                            "requires policy verification."
                        ),
                        "confidence": 0.65,
                    },
                ),
                NodeSpec(
                    key="c/finding/v1",
                    logical_key="c/finding",
                    kind=N.RISK_FINDING,
                    attributes={
                        "title": "SYNTHETIC possible public S3 access",
                        "severity": "high",
                        "status": "suspected",
                    },
                ),
                NodeSpec(
                    key="c/investigation/v1",
                    logical_key="c/investigation",
                    kind=N.INVESTIGATION,
                    attributes={
                        "title": "SYNTHETIC conflicting access observations",
                        "status": "open",
                    },
                ),
            ],
            edges=[
                EdgeSpec(
                    key="c/scout-remembers",
                    kind=R.REMEMBERS,
                    source_key="agent/scout",
                    target_key="c/memory/scout",
                ),
                EdgeSpec(
                    key="c/memory-derived",
                    kind=R.DERIVED_FROM,
                    source_key="c/memory/scout",
                    target_key="c/evidence/acl",
                ),
                EdgeSpec(
                    key="c/acl-supports",
                    kind=R.SUPPORTS,
                    source_key="c/evidence/acl",
                    target_key="c/finding/v1",
                ),
                EdgeSpec(
                    key="c/investigates",
                    kind=R.INVESTIGATES,
                    source_key="c/investigation/v1",
                    target_key="c/finding/v1",
                ),
            ],
        ),
        SyntheticSecurityEvent(
            scenario=Scenario.C,
            source_id="c/newer-policy-evidence",
            account_id=PROD,
            sequence=2,
            occurred_at=hour(11),
            event_type="SyntheticNewEvidenceDisputesObservation",
            close_nodes=["c/finding/v1", "c/investigation/v1"],
            nodes=[
                NodeSpec(
                    key="c/evidence/policy",
                    kind=N.EVIDENCE,
                    attributes={
                        "evidence_type": "synthetic-policy-snapshot",
                        "summary": (
                            "SYNTHETIC newer policy context reports public-access blocking; "
                            "earlier ACL finding is disputed, not erased."
                        ),
                        "content_sha256": fingerprint({"public_access_block": True}),
                    },
                ),
                NodeSpec(
                    key="c/memory/verifier",
                    kind=N.AGENT_MEMORY,
                    attributes={
                        "memory_type": "counter-observation",
                        "content": (
                            "SYNTHETIC newer evidence disputes public exposure; "
                            "preserve scout observation and investigate discrepancy."
                        ),
                        "confidence": 0.9,
                    },
                ),
                NodeSpec(
                    key="c/finding/v2",
                    logical_key="c/finding",
                    kind=N.RISK_FINDING,
                    attributes={
                        "title": "SYNTHETIC possible public S3 access",
                        "severity": "high",
                        "status": "disputed",
                    },
                ),
                NodeSpec(
                    key="c/investigation/v2",
                    logical_key="c/investigation",
                    kind=N.INVESTIGATION,
                    attributes={
                        "title": "SYNTHETIC conflicting access observations",
                        "status": "needs_investigation",
                    },
                ),
            ],
            edges=[
                EdgeSpec(
                    key="c/verifier-remembers",
                    kind=R.REMEMBERS,
                    source_key="agent/verifier",
                    target_key="c/memory/verifier",
                ),
                EdgeSpec(
                    key="c/verifier-derived",
                    kind=R.DERIVED_FROM,
                    source_key="c/memory/verifier",
                    target_key="c/evidence/policy",
                ),
                EdgeSpec(
                    key="c/memory-conflict",
                    kind=R.CONTRADICTS,
                    source_key="c/memory/verifier",
                    target_key="c/memory/scout",
                ),
                EdgeSpec(
                    key="c/evidence-conflict",
                    kind=R.CONTRADICTS,
                    source_key="c/evidence/policy",
                    target_key="c/finding/v1",
                ),
                EdgeSpec(
                    key="c/finding-supersedes",
                    kind=R.SUPERSEDES,
                    source_key="c/finding/v2",
                    target_key="c/finding/v1",
                ),
                EdgeSpec(
                    key="c/review-supersedes",
                    kind=R.SUPERSEDES,
                    source_key="c/investigation/v2",
                    target_key="c/investigation/v1",
                ),
                EdgeSpec(
                    key="c/review-dispute",
                    kind=R.INVESTIGATES,
                    source_key="c/investigation/v2",
                    target_key="c/finding/v2",
                ),
                EdgeSpec(
                    key="c/new-evidence-disputes-current",
                    kind=R.CONTRADICTS,
                    source_key="c/evidence/policy",
                    target_key="c/finding/v2",
                ),
                EdgeSpec(
                    key="c/old-evidence-supports-current",
                    kind=R.SUPPORTS,
                    source_key="c/evidence/acl",
                    target_key="c/finding/v2",
                ),
            ],
        ),
    )
    return DatasetDefinition(tuple(nodes), tuple(edges), events)

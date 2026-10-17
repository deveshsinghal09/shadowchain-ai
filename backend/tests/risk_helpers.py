"""Small normalized graph fixtures, separate from the bundled demo manifest."""

import json

from app.demo.dataset import hour
from app.models.graph import (
    GraphNode,
    NodeInput,
    NodeKind,
    Provenance,
    RelationshipInput,
    RelationshipKind,
    TenantScope,
    epoch_us,
)
from app.repositories.graph import GraphRepository
from app.repositories.risk import RiskRepository
from app.services.risk import RiskEngine

ACCOUNT = "000000000001"


class PolicyGraph:
    def __init__(self, database):
        scope = TenantScope(tenant_id="generic-synthetic-iam")
        self.repository = GraphRepository(database, scope)
        self.engine = RiskEngine(RiskRepository(database, scope))
        self.counter = 0
        self.event = self.node(
            "event",
            NodeKind.SECURITY_EVENT,
            event_type="SyntheticPolicyFixture",
            event_time=epoch_us(hour(9)),
            content_sha256="a" * 64,
        )
        self.identity = self.node(
            "user",
            NodeKind.IDENTITY,
            display_name="SYNTHETIC generic user",
            arn=f"arn:aws:iam::{ACCOUNT}:user/synthetic-generic",
        )
        self.resource = self.node(
            "bucket",
            NodeKind.RESOURCE,
            sensitive=True,
            resource_type="s3:bucket",
            arn="arn:aws:s3:::synthetic-generic-bucket",
        )

    def provenance(self, name: str) -> Provenance:
        return Provenance(
            source="synthetic.risk-tests",
            source_id=name,
            observed_at=hour(9),
            collector="pytest",
            source_uri="urn:synthetic:" + name,
        )

    def node(self, name: str, kind: NodeKind, *, account=ACCOUNT, **attributes) -> GraphNode:
        return self.repository.upsert_node(
            NodeInput(
                kind=kind,
                account_id=account,
                provenance=self.provenance(name),
                attributes=attributes,
            )
        )

    def edge(self, kind, source, target, *, status="verified", valid_to=None):
        self.counter += 1
        return self.repository.create_relationship(
            RelationshipInput(
                kind=kind,
                source=source.ref,
                target=target.ref,
                provenance=self.provenance("edge/" + str(self.counter)),
                valid_from=hour(9),
                valid_to=valid_to,
                source_event_id=self.event.id,
                verification_status=status,
            )
        )

    def policy(
        self,
        owner,
        name,
        policy_type="IdentityPolicy",
        effect="Allow",
        *,
        target=None,
        action="s3:GetObject",
        conditions=None,
        principal_arns=None,
        status="verified",
        valid_to=None,
    ):
        policy = self.node(name, NodeKind.POLICY, policy_type=policy_type)
        attachment = self.edge(RelationshipKind.HAS_POLICY, owner, policy)
        attrs = {"action": action, "effect": effect}
        if conditions is not None:
            attrs["conditions_json"] = json.dumps(conditions)
        if principal_arns is not None:
            attrs["principal_arns_json"] = json.dumps(principal_arns)
        permission = self.node(name + "/permission", NodeKind.PERMISSION, **attrs)
        grant = self.edge(
            RelationshipKind.GRANTS, policy, permission, status=status, valid_to=valid_to
        )
        target_edge = self.edge(RelationshipKind.TARGETS, permission, target or self.resource)
        return policy, permission, attachment, grant, target_edge

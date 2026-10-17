"""Validated graph inputs and records. No domain state is cached here."""

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


class NodeKind(StrEnum):
    CLOUD_ORGANIZATION = "CloudOrganization"
    IDENTITY = "Identity"
    ROLE = "Role"
    POLICY = "Policy"
    PERMISSION = "Permission"
    RESOURCE = "Resource"
    CLOUD_ACCOUNT = "CloudAccount"
    SECURITY_EVENT = "SecurityEvent"
    EVIDENCE = "Evidence"
    RISK_FINDING = "RiskFinding"
    INVESTIGATION = "Investigation"
    AGENT = "Agent"
    AGENT_RUN = "AgentRun"
    AGENT_MEMORY = "AgentMemory"
    TASK = "Task"
    DECISION = "Decision"
    HANDOFF = "Handoff"
    PROCEDURE = "Procedure"
    MEMORY_SPACE = "MemorySpace"
    SECURITY_CONCEPT = "SecurityConcept"


class RelationshipKind(StrEnum):
    BELONGS_TO = "BELONGS_TO"
    CAN_ASSUME = "CAN_ASSUME"
    HAS_POLICY = "HAS_POLICY"
    GRANTS = "GRANTS"
    TARGETS = "TARGETS"
    AFFECTS = "AFFECTS"
    DERIVED_FROM = "DERIVED_FROM"
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    CAUSED_BY = "CAUSED_BY"
    INVESTIGATES = "INVESTIGATES"
    REMEMBERS = "REMEMBERS"
    ASSIGNED_TO = "ASSIGNED_TO"
    DEPENDS_ON = "DEPENDS_ON"
    HANDS_OFF_TO = "HANDS_OFF_TO"
    USES_PROCEDURE = "USES_PROCEDURE"
    SUPERSEDES = "SUPERSEDES"
    MEMBER_OF = "MEMBER_OF"
    HAS_CONTEXT = "HAS_CONTEXT"
    ABOUT = "ABOUT"
    INCLUDES_MEMORY = "INCLUDES_MEMORY"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    REJECTED = "rejected"


Identifier = Annotated[str, Field(min_length=1, max_length=512, pattern=r"^[^\x00-\x1f]+$")]
Scalar = StrictStr | StrictBool | StrictInt | StrictFloat
ACCESS_RELATIONSHIPS = frozenset(
    {
        RelationshipKind.CAN_ASSUME,
        RelationshipKind.HAS_POLICY,
        RelationshipKind.GRANTS,
        RelationshipKind.TARGETS,
    }
)
CLOUD_KINDS = frozenset(
    {
        NodeKind.IDENTITY,
        NodeKind.ROLE,
        NodeKind.POLICY,
        NodeKind.PERMISSION,
        NodeKind.RESOURCE,
        NodeKind.CLOUD_ACCOUNT,
        NodeKind.SECURITY_EVENT,
    }
)
# Closed property vocabulary prevents credentials/raw provider responses from becoming properties.
ATTRIBUTES: dict[NodeKind, frozenset[str]] = {
    NodeKind.CLOUD_ORGANIZATION: frozenset({"display_name", "organization_id"}),
    NodeKind.IDENTITY: frozenset({"display_name", "arn", "identity_type"}),
    NodeKind.ROLE: frozenset({"display_name", "arn", "description"}),
    NodeKind.POLICY: frozenset({"display_name", "arn", "policy_type", "document_sha256"}),
    NodeKind.PERMISSION: frozenset({"action", "effect", "condition_sha256"}),
    NodeKind.RESOURCE: frozenset({"display_name", "arn", "resource_type", "region"}),
    NodeKind.CLOUD_ACCOUNT: frozenset({"display_name", "provider", "account_number"}),
    NodeKind.SECURITY_EVENT: frozenset({"event_type", "event_time", "content_sha256"}),
    NodeKind.EVIDENCE: frozenset(
        {
            "evidence_type",
            "summary",
            "content_sha256",
            "record_json",
            "record_id",
            "analysis_id",
            "status",
            "fact_value",
            "control_type",
        }
    ),
    NodeKind.RISK_FINDING: frozenset(
        {
            "title",
            "severity",
            "status",
            "summary",
            "analysis_id",
            "path_id",
            "risk_score",
            "assessed_at",
            "engine_version",
        }
    ),
    NodeKind.INVESTIGATION: frozenset({"title", "status", "summary"}),
    NodeKind.AGENT: frozenset({"name", "agent_type", "version"}),
    NodeKind.AGENT_RUN: frozenset({"status", "started_at", "finished_at"}),
    NodeKind.AGENT_MEMORY: frozenset({"memory_type", "content", "confidence"}),
    NodeKind.TASK: frozenset({"title", "status", "priority", "lease_expires_at"}),
    NodeKind.DECISION: frozenset({"action", "rationale", "status"}),
    NodeKind.HANDOFF: frozenset({"reason", "status"}),
    NodeKind.PROCEDURE: frozenset({"name", "version", "instructions"}),
    NodeKind.MEMORY_SPACE: frozenset({"name", "revision", "lock_version"}),
    NodeKind.SECURITY_CONCEPT: frozenset({"name", "description", "category"}),
}
for _aws_kind in NodeKind:
    ATTRIBUTES[_aws_kind] |= frozenset({"source_api", "region", "import_run_id"})
ATTRIBUTES[NodeKind.DECISION] |= frozenset({"integration_kind", "coverage_json"})
ATTRIBUTES[NodeKind.EVIDENCE] |= frozenset(
    {
        "identity_refs_json",
        "artifact_uri",
        "export_mode",
        "byte_count",
        "redaction_version",
        "request_sha256",
        "investigation_id",
        "status",
    }
)
ATTRIBUTES[NodeKind.AGENT_MEMORY] |= frozenset(
    {
        "memory_key",
        "series_id",
        "memory_version",
        "author_agent_id",
        "audience",
        "state",
        "verification_status",
        "source_trust",
        "space_id",
        "request_sha256",
        "claim_key",
        "claim_value_json",
        "claim_json",
        "procedure_json",
        "source_provenance_json",
        "memory_engine_version",
        "superseded_at",
        "reviewed_at",
        "reviewed_by",
        "reference_count",
    }
)
ATTRIBUTES[NodeKind.DECISION] |= frozenset(
    {
        "space_id",
        "actor_agent_id",
        "request_sha256",
        "response_json",
        "audit_json",
    }
)
ATTRIBUTES[NodeKind.HANDOFF] |= frozenset(
    {
        "space_id",
        "sender_agent_id",
        "recipient_agent_id",
        "context_sha256",
    }
)
for _kind in (NodeKind.INVESTIGATION, NodeKind.AGENT_RUN, NodeKind.TASK):
    ATTRIBUTES[_kind] |= frozenset(
        {
            "workflow_version",
            "investigation_id",
            "agent_id",
            "stage",
            "operation_id",
            "input_evidence_ids_json",
            "retrieved_memory_ids_json",
            "output_json",
            "pending_task_ids_json",
            "handoff_target",
            "handoff_id",
            "memory_id",
            "request_sha256",
            "request_json",
            "space_id",
            "lease_owner",
            "lease_expires_at",
            "revision",
            "lock_version",
            "attempt_count",
            "error_code",
            "output_sha256",
            "provider_mode",
            "task_type",
        }
    )
ATTRIBUTES[NodeKind.INVESTIGATION] |= frozenset(
    {"severity", "path_id", "case_version", "owner_agent_id", "resolution", "assessed_at"}
)
ATTRIBUTES[NodeKind.TASK] |= frozenset({"check_key"})
ATTRIBUTES[NodeKind.DECISION] |= frozenset({"case_id"})
ATTRIBUTES[NodeKind.HANDOFF] |= frozenset({"case_id", "context_json"})
ATTRIBUTES[NodeKind.DECISION] |= frozenset(
    {
        "simulator_version",
        "category",
        "analysis_request_json",
        "relationship_json",
        "permission_ids_json",
        "finding_ids_json",
        "evidence_json",
        "request_json",
        "source_sha256",
        "revision",
        "proposal_id",
        "proposal_ids_json",
        "reviewer_id",
        "result_sha256",
        "chunk_ids_json",
        "simulation_id",
        "requested_reason",
    }
)
DEMO_ATTRIBUTES = frozenset(
    {
        "synthetic",
        "dataset_version",
        "scenario_id",
        "entity_key",
        "logical_key",
        "source_time",
        "valid_from",
        "valid_to",
        "sensitive",
        "object_arn",
        "service_principal",
        "instance_profile",
        "sequence",
        "event_id",
        "ingested_at",
        "payload",
        "applied_revision",
        "generation",
        "source_event_id",
    }
)
ATTRIBUTES = {kind: fields | DEMO_ATTRIBUTES for kind, fields in ATTRIBUTES.items()}
for _kind in (NodeKind.IDENTITY, NodeKind.ROLE, NodeKind.RESOURCE):
    ATTRIBUTES[_kind] |= frozenset(
        {
            "boundary_required",
            "session_policy_required",
            "scp_required",
            "rcp_required",
            "resource_policy_required",
            "data_classification",
        }
    )
ATTRIBUTES[NodeKind.PERMISSION] |= frozenset({"conditions_json", "principal_arns_json"})
SECRET_PATTERN = re.compile(
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{30,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:aws_secret_access_key|aws_session_token|gemini_api_key|api_key|password)\s*[:=]"
)


def reject_secret(value: str) -> str:
    if SECRET_PATTERN.search(value):
        raise ValueError("Credential-like material is not permitted in graph records")
    return value


class GraphModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class TenantScope(GraphModel):
    tenant_id: Identifier


class Provenance(GraphModel):
    source: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")]
    source_id: Identifier
    observed_at: AwareDatetime
    collector: Identifier
    source_uri: Annotated[str, Field(min_length=1, max_length=2048)]

    @field_validator("source_id", "collector", "source_uri")
    @classmethod
    def no_secrets(cls, value: str) -> str:
        return reject_secret(value)

    @field_validator("source_uri")
    @classmethod
    def no_credentials_in_uri(cls, value: str) -> str:
        uri = urlsplit(value)
        if not uri.scheme or uri.username or uri.password or uri.query or uri.fragment:
            raise ValueError(
                "Provenance URI must not contain credentials, query strings or fragments"
            )
        return value


class NodeInput(GraphModel):
    kind: NodeKind
    provenance: Provenance
    account_id: Identifier | None = None
    attributes: dict[str, Scalar] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_attributes(self) -> "NodeInput":
        if set(self.attributes) - ATTRIBUTES[self.kind]:
            raise ValueError("Unsupported graph property for this node kind")
        if self.kind in CLOUD_KINDS and not self.account_id:
            raise ValueError("Cloud nodes require account_id")
        required = {
            NodeKind.MEMORY_SPACE: {"name", "revision", "lock_version"},
            NodeKind.SECURITY_EVENT: {"event_type", "event_time", "content_sha256"},
            NodeKind.EVIDENCE: {"evidence_type", "content_sha256"},
        }.get(self.kind, set())
        if required - self.attributes.keys():
            raise ValueError("Missing required event/evidence attributes")
        for key, value in self.attributes.items():
            if isinstance(value, str):
                if len(value) > 16384:
                    raise ValueError("Graph text property exceeds size limit")
                reject_secret(value)
            if key.endswith("sha256") and (
                not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            ):
                raise ValueError("Content digests must be lowercase SHA-256 hex")
            if key in {
                "event_time",
                "started_at",
                "finished_at",
                "lease_expires_at",
                "source_time",
                "valid_from",
                "valid_to",
                "ingested_at",
                "sequence",
                "applied_revision",
                "generation",
                "assessed_at",
                "risk_score",
                "memory_version",
                "superseded_at",
                "reviewed_at",
                "revision",
                "lock_version",
                "reference_count",
                "attempt_count",
            } and (type(value) is not int or value < 0):
                raise ValueError("Time attributes must be nonnegative UTC epoch microseconds")
            if key == "confidence" and (type(value) not in {int, float} or not 0 <= value <= 1):
                raise ValueError("Confidence must be between zero and one")
            if key == "priority" and (type(value) is not int or value < 0):
                raise ValueError("Priority must be a nonnegative integer")
            if key not in {
                "event_time",
                "started_at",
                "finished_at",
                "lease_expires_at",
                "confidence",
                "priority",
                "synthetic",
                "sensitive",
                "source_time",
                "valid_from",
                "valid_to",
                "ingested_at",
                "sequence",
                "applied_revision",
                "generation",
                "assessed_at",
                "risk_score",
                "memory_version",
                "superseded_at",
                "reviewed_at",
                "revision",
                "lock_version",
                "reference_count",
                "attempt_count",
                "fact_value",
                "boundary_required",
                "session_policy_required",
                "scp_required",
                "rcp_required",
                "resource_policy_required",
            } and not isinstance(value, str):
                raise ValueError("Text attributes must be strings")
            if (
                key
                in {
                    "synthetic",
                    "sensitive",
                    "fact_value",
                    "boundary_required",
                    "session_policy_required",
                    "scp_required",
                    "rcp_required",
                    "resource_policy_required",
                }
                and type(value) is not bool
            ):
                raise ValueError("Synthetic/sensitivity flags must be booleans")
            if isinstance(value, float) and not float("-inf") < value < float("inf"):
                raise ValueError("Graph numeric properties must be finite")
        return self


class NodeRef(GraphModel):
    kind: NodeKind
    id: Annotated[str, Field(pattern=r"^[0-9a-f-]{36}$")]


class RelationshipInput(GraphModel):
    kind: RelationshipKind
    source: NodeRef
    target: NodeRef
    provenance: Provenance
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None
    source_event_id: Annotated[str, Field(pattern=r"^[0-9a-f-]{36}$")] | None = None
    confidence: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED

    @model_validator(mode="after")
    def valid_interval(self) -> "RelationshipInput":
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("Validity interval must be nonempty: valid_from < valid_to")
        if self.kind in ACCESS_RELATIONSHIPS and not self.source_event_id:
            raise ValueError("Access relationships require a tenant-scoped source SecurityEvent ID")
        return self


class GraphNode(GraphModel):
    id: str
    kind: NodeKind
    tenant_id: str
    account_id: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    provenance: Provenance
    attributes: dict[str, Scalar]

    @property
    def ref(self) -> NodeRef:
        return NodeRef(kind=self.kind, id=self.id)


class GraphRelationship(GraphModel):
    id: str
    kind: RelationshipKind
    tenant_id: str
    source: NodeRef
    target: NodeRef
    provenance: Provenance
    created_at: AwareDatetime
    updated_at: AwareDatetime
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None
    source_event_id: str | None
    closed_by_event_id: str | None = None
    confidence: float
    verification_status: VerificationStatus


EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def epoch_us(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware timestamp is required")
    delta = value.astimezone(UTC) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def from_epoch_us(value: int) -> datetime:
    from datetime import timedelta

    return EPOCH + timedelta(microseconds=value)

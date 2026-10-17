"""Stable v1 frontend contract. Domain records retain their existing typed schemas."""

from datetime import UTC
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.models.events import ReplayRequest, Scenario, SyntheticSecurityEvent
from app.models.graph import GraphModel, NodeKind, Scalar
from app.models.memory import MemoryRecord
from app.models.workflow import AgentKey, AgentRunRecord
from app.schemas.demo import DemoStatus
from app.schemas.risk import GraphID, PathFinding


class V1(GraphModel):
    schema_version: Literal["1"] = "1"


class PageQuery(GraphModel):
    limit: int = Field(default=50, ge=1, le=100)
    cursor: GraphID | None = None
    snapshot_sequence: int | None = Field(default=None, ge=0, le=9007199254740991)


class GraphQuery(PageQuery):
    at: AwareDatetime | None = None
    collection: Literal["nodes", "edges"] = "nodes"
    node_type: NodeKind | None = None
    scenario: Scenario | None = None

    @field_validator("at")
    @classmethod
    def utc_snapshot(cls, value):
        return value.astimezone(UTC) if value else None


class AgentRunsQuery(PageQuery):
    investigation_id: GraphID | None = None


class WindowQuery(PageQuery):
    before: AwareDatetime
    after: AwareDatetime

    @field_validator("before", "after")
    @classmethod
    def utc_window(cls, value):
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def ordered(self):
        if self.before >= self.after:
            raise ValueError("before must precede after")
        return self


class DiffQuery(WindowQuery):
    collection: Literal["nodes", "edges"] = "edges"
    scenario: Scenario | None = None


class GraphItem(V1):
    id: GraphID
    type: str
    label: str
    attributes: dict[str, Scalar]
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    evidence_ids: list[GraphID] = Field(default_factory=list, max_length=64)
    investigation_ids: list[GraphID] = Field(default_factory=list, max_length=64)
    risk_severity: Literal["low", "medium", "high", "critical"] | None = None
    associations_truncated: bool = False


class VisualNode(GraphItem):
    type: NodeKind


class VisualEdge(GraphItem):
    source: GraphID
    target: GraphID
    source_event_id: GraphID | None = None


class VisualSnapshot(V1):
    snapshot_at: AwareDatetime
    snapshot_sequence: int
    collection: Literal["nodes", "edges", "path"]
    nodes: list[VisualNode] = Field(default_factory=list, max_length=250)
    edges: list[VisualEdge] = Field(default_factory=list, max_length=250)
    next_cursor: GraphID | None = None
    has_more: bool = False
    historical_attributes: Literal["retained-record-values"] = "retained-record-values"


class GraphDifference(V1):
    before: AwareDatetime
    after: AwareDatetime
    snapshot_sequence: int
    comparison: Literal["temporal-membership"] = "temporal-membership"
    added_nodes: list[VisualNode] = Field(default_factory=list, max_length=100)
    removed_nodes: list[VisualNode] = Field(default_factory=list, max_length=100)
    added_edges: list[VisualEdge] = Field(default_factory=list, max_length=100)
    removed_edges: list[VisualEdge] = Field(default_factory=list, max_length=100)
    next_cursor: GraphID | None = None
    has_more: bool


class DashboardSummary(V1):
    snapshot_at: AwareDatetime
    snapshot_sequence: int
    entity_counts: dict[str, int]
    persisted_risk_severities: dict[str, int]
    investigation_statuses: dict[str, int]
    task_statuses: dict[str, int]
    findings_are_current_authorization: Literal[False] = False


class TimelineEvent(V1):
    id: GraphID
    type: str
    occurred_at: AwareDatetime
    ingested_at: AwareDatetime
    source_api: str | None = None
    evidence_ids: list[GraphID] = Field(max_length=64)


class GraphTimeline(V1):
    before: AwareDatetime
    after: AwareDatetime
    snapshot_sequence: int
    events: list[TimelineEvent] = Field(max_length=100)
    next_cursor: GraphID | None = None
    has_more: bool


class SeedResult(V1):
    synthetic: Literal[True] = True
    created: bool
    dataset_version: str
    scenarios: dict[str, DemoStatus]


class ScenarioCatalog(V1):
    synthetic: Literal[True] = True
    dataset_version: str
    scenarios: dict[str, list[SyntheticSecurityEvent]]


class ScenarioReset(GraphModel):
    scenario: Scenario
    expected_generation: int = Field(ge=0)


class ScenarioReplay(ReplayRequest):
    scenario: Scenario


class RiskSummary(V1):
    risk_id: GraphID
    path_id: GraphID
    severity: str
    score: int
    assessed_at: AwareDatetime
    classification: Literal["POTENTIAL ATTACK PATH"] = "POTENTIAL ATTACK PATH"


class RiskPage(V1):
    snapshot_sequence: int
    risks: list[RiskSummary] = Field(max_length=100)
    next_cursor: GraphID | None = None
    has_more: bool


class RiskDetail(V1):
    risk: PathFinding
    assessed_at: AwareDatetime
    evidence_ids: list[GraphID] = Field(max_length=2000)
    investigation_ids: list[GraphID] = Field(max_length=64)


class AgentDefinition(V1):
    id: GraphID
    name: str
    agent_type: AgentKey
    version: str
    registered: bool
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None


class AgentActivity(V1):
    run: AgentRunRecord
    task_statuses: dict[str, str]
    memory_read_ids: list[GraphID]
    memory_written_ids: list[GraphID]
    evidence_produced_ids: list[GraphID]
    handoff_ids: list[GraphID]
    output_evidence_ids: list[GraphID]


class AgentRunsPage(V1):
    snapshot_sequence: int
    runs: list[AgentActivity] = Field(max_length=100)
    next_cursor: GraphID | None = None
    has_more: bool


class AgentMemoryPage(V1):
    memories: list[MemoryRecord] = Field(max_length=100)
    # ACL-aware retrieval is bounded by the existing memory service, not an unsafe raw scan.
    limit: int
    has_more: bool = False
    next_cursor: GraphID | None = None


class AwsStatus(V1):
    aws_enabled: bool
    cloudtrail_enabled: bool
    export_mode: Literal["local", "s3"]
    sync_user_triggered_only: Literal[True] = True
    aws_clients_created: Literal[False] = False
    report_formats: list[Literal["json"]] = ["json"]
    iam_write_capability: Literal[False] = False

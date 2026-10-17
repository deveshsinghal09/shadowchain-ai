"""Bounded risk requests and explicit, auditable potential-path results."""

from datetime import UTC
from ipaddress import ip_address
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.models.events import Scenario
from app.models.graph import GraphModel

ENGINE_VERSION = "risk-2026.1"
GraphID = Annotated[str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f-]{27}$")]


class AuthorizationContext(GraphModel):
    mfa_present: bool | None = None
    source_ip: str | None = None
    requested_region: Annotated[str, Field(pattern=r"^[a-z0-9-]{1,32}$")] | None = None

    @field_validator("source_ip")
    @classmethod
    def valid_ip(cls, value: str | None) -> str | None:
        return str(ip_address(value)) if value is not None else None


class RiskOptions(GraphModel):
    scenario: Scenario | None = None
    identity_id: GraphID | None = None
    action: Annotated[str, Field(max_length=128, pattern=r"^[a-zA-Z0-9-]+:[a-zA-Z0-9]+$")] = (
        "s3:GetObject"
    )
    max_role_hops: int = Field(default=4, ge=0, le=4, strict=True)
    context: AuthorizationContext = Field(default_factory=AuthorizationContext)


class AnalysisRequest(RiskOptions):
    at: AwareDatetime

    @field_validator("at")
    @classmethod
    def utc_time(cls, value: AwareDatetime) -> AwareDatetime:
        return value.astimezone(UTC)


class DemoAnalysisRequest(AnalysisRequest):
    scenario: Scenario = Scenario.A


class DifferenceRequest(RiskOptions):
    before: AwareDatetime
    after: AwareDatetime

    @model_validator(mode="after")
    def ordered(self) -> "DifferenceRequest":
        if self.before >= self.after:
            raise ValueError("before must precede after")
        return self


class DemoDifferenceRequest(DifferenceRequest):
    scenario: Scenario = Scenario.A


class WhatIfRequest(AnalysisRequest):
    remove_relationship_ids: list[GraphID] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def unique_relationships(self) -> "WhatIfRequest":
        if len(set(self.remove_relationship_ids)) != len(self.remove_relationship_ids):
            raise ValueError("Duplicate relationship IDs")
        return self


class DemoWhatIfRequest(WhatIfRequest):
    scenario: Scenario = Scenario.A


class TimelineRequest(DifferenceRequest):
    pass


class DemoTimelineRequest(TimelineRequest):
    scenario: Scenario = Scenario.A


class ScoreFactor(GraphModel):
    name: str
    points: int
    explanation: str
    record_ids: list[str] = Field(default_factory=list)


class RiskScore(GraphModel):
    value: int = Field(ge=0, le=100)
    severity: Literal["low", "medium", "high", "critical"]
    factors: list[ScoreFactor]
    model: Literal["deterministic-heuristic-v1"] = "deterministic-heuristic-v1"


class ContributingEvent(GraphModel):
    id: str
    source_id: str
    event_type: str
    occurred_at: AwareDatetime
    relationship_ids: list[str]
    closed_relationship_ids: list[str] = Field(default_factory=list)


class PathFinding(GraphModel):
    finding_id: str
    path_id: str
    classification: Literal["POTENTIAL ATTACK PATH"] = "POTENTIAL ATTACK PATH"
    identity: dict[str, Any]
    roles: list[dict[str, Any]]
    resource: dict[str, Any]
    action: str
    permission_id: str
    relationship_ids: list[str]
    supporting_relationship_ids: list[str]
    contributing_events: list[ContributingEvent]
    score: RiskScore
    explanation: str
    assumptions: list[str]


class RejectedCandidate(GraphModel):
    identity_id: str
    resource_id: str
    reason: str
    unresolved: bool = False


class DiscoveryResult(GraphModel):
    analysis_id: str
    engine_version: str = ENGINE_VERSION
    at: AwareDatetime
    scenario: Scenario | None
    action: str
    max_role_hops: int
    context_sha256: str
    persisted: bool = False
    findings: list[PathFinding]
    rejected_candidates: list[RejectedCandidate]
    limitations: list[str]


class PathIntroduction(GraphModel):
    path_id: str
    first_reachable_at: AwareDatetime
    transition_events: list[ContributingEvent]
    contributing_events: list[ContributingEvent]
    attribution: Literal["modeled-change-provenance"] = "modeled-change-provenance"
    sampling: Literal["event-boundaries-and-endpoints"] = "event-boundaries-and-endpoints"
    explanation: str = (
        "The graph model is first observed potentially reachable at this sampled source time. "
        "Event provenance identifies modeled changes; temporal correlation does not prove "
        "real-world causality or exploitable AWS authorization. Simultaneous events are grouped."
    )


class RiskDifference(GraphModel):
    before: DiscoveryResult
    after: DiscoveryResult
    added_path_ids: list[str]
    removed_path_ids: list[str]
    unchanged_path_ids: list[str]
    introductions: list[PathIntroduction]


class TimelinePoint(GraphModel):
    at: AwareDatetime
    analysis_id: str
    path_count: int
    maximum_score: int
    path_ids: list[str]


class RiskTimeline(GraphModel):
    points: list[TimelinePoint]
    sampling: Literal["event-boundaries-and-endpoints"] = "event-boundaries-and-endpoints"
    limitations: list[str] = Field(
        default_factory=lambda: [
            "Condition-only transitions between samples require explicit historical queries. "
            "This timeline is not a continuous-time authorization proof."
        ]
    )


class RemediationResult(GraphModel):
    simulation: Literal[True] = True
    graph_mutated: Literal[False] = False
    excluded_relationship_ids: list[str]
    before: DiscoveryResult
    after: DiscoveryResult
    removed_path_ids: list[str]
    added_path_ids: list[str]
    unchanged_path_ids: list[str]


class FindingEvidence(GraphModel):
    analysis: DiscoveryResult
    finding: PathFinding
    evidence: list[dict[str, Any]]

"""Typed recommendations and hypothetical results; no execution contract exists."""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, field_validator

from app.models.graph import GraphModel, reject_secret
from app.models.memory import Key
from app.schemas.risk import AnalysisRequest, DiscoveryResult, GraphID

REMEDIATION_SOURCE = "shadowchain.remediation.v1"
SIMULATOR_VERSION = "remediation-2026.1"


class ProposalStatus(StrEnum):
    PROPOSED = "PROPOSED"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RecommendationKind(StrEnum):
    EXCESSIVE_PERMISSION = "remove_excessive_permission"
    ROLE_TRUST = "remove_unnecessary_role_trust"
    EXPIRED_ACCESS = "review_expired_temporary_access"
    BROAD_RESOURCE = "reduce_broad_resource_access"
    POLICY_EXCEPTION = "review_high_risk_policy_exception"


class RecommendRequest(GraphModel):
    request_id: Key
    analysis: AnalysisRequest
    limit: int = Field(default=10, ge=1, le=20, strict=True)


class ProposeRequest(GraphModel):
    request_id: Key
    analysis: AnalysisRequest
    relationship_id: GraphID
    category: RecommendationKind
    reason: Annotated[str, Field(min_length=1, max_length=1000)]

    @field_validator("reason")
    @classmethod
    def safe_reason(cls, value):
        return reject_secret(value)


class SimulationRequest(GraphModel):
    request_id: Key
    additional_proposal_ids: list[GraphID] = Field(default_factory=list, max_length=9)

    @field_validator("additional_proposal_ids")
    @classmethod
    def unique(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate proposals")
        return sorted(value)


class ProposalReview(GraphModel):
    request_id: Key
    expected_revision: int = Field(ge=0, strict=True)
    status: ProposalStatus
    simulation_id: GraphID | None = None
    reason: Annotated[str, Field(min_length=1, max_length=1000)]

    @field_validator("reason")
    @classmethod
    def safe_reason(cls, value):
        return reject_secret(value)


class Proposal(GraphModel):
    proposal_id: GraphID
    category: RecommendationKind
    status: ProposalStatus
    revision: int
    analysis: AnalysisRequest
    target_relationship: dict[str, Any]
    permission_ids: list[GraphID]
    finding_ids: list[GraphID]
    evidence: list[dict[str, Any]]
    explanation: str
    requested_reason: str
    created_at: AwareDatetime
    updated_at: AwareDatetime
    review_history: list[dict[str, Any]]
    simulation_ids: list[GraphID]
    approval_scope: Literal["recommendation_only"] = "recommendation_only"
    execution_allowed: Literal[False] = False
    business_necessity: Literal["unknown_requires_owner_review"] = "unknown_requires_owner_review"


class Recommendations(GraphModel):
    proposals: list[Proposal]
    available_categories: list[RecommendationKind]
    omitted_candidate_count: int
    limitations: list[str]


class AccessImpact(GraphModel):
    identity_id: GraphID
    resource_id: GraphID
    action: str
    resource_sensitive: bool
    removed_path_ids: list[GraphID]
    remaining_path_ids: list[GraphID]
    potential_access_lost: bool
    legitimacy: Literal["unknown_requires_workload_owner_review"] = (
        "unknown_requires_workload_owner_review"
    )


class RiskDelta(GraphModel):
    before_path_count: int
    after_path_count: int
    before_maximum_score: int
    after_maximum_score: int
    before_total_score: int
    after_total_score: int
    path_count_change: int
    maximum_score_change: int
    total_score_change: int
    model: Literal["heuristic_path_scores_not_probability"] = (
        "heuristic_path_scores_not_probability"
    )


class Simulation(GraphModel):
    simulation_id: GraphID
    proposal_ids: list[GraphID]
    simulator_version: str = SIMULATOR_VERSION
    evaluated_at: AwareDatetime
    excluded_relationship_ids: list[GraphID]
    before: DiscoveryResult
    after: DiscoveryResult
    removed_path_ids: list[GraphID]
    remaining_path_ids: list[GraphID]
    added_path_ids: list[GraphID]
    alternate_paths: list[GraphID]
    access_impact: list[AccessImpact]
    risk_difference: RiskDelta
    historical_evidence_ids: list[GraphID]
    supporting_evidence: list[dict[str, Any]]
    visualization: dict[str, Any]
    explanation: str
    limitations: list[str]
    source_cloud_graph_mutated: Literal[False] = False
    hypothetical_changes_applied_to_aws: Literal[False] = False
    execution_allowed: Literal[False] = False
    provenance_storage: Literal["FalkorDB"] = "FalkorDB"

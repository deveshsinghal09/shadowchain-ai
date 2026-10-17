"""Closed workflow contracts. Model prose cannot choose tools, tenants or transitions."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, field_validator

from app.models.graph import GraphModel, reject_secret
from app.models.memory import Key
from app.schemas.risk import GraphID

WORKFLOW_VERSION = "agents-2026.1"
WORKFLOW_SOURCE = "shadowchain.agents.v1"


class AgentKey(StrEnum):
    CLOUD_SENTINEL = "cloud_sentinel"
    IDENTITY_GUARDIAN = "identity_guardian"
    RISK_ORACLE = "risk_oracle"
    MEMORY_KEEPER = "memory_keeper"
    COORDINATOR = "coordinator"
    RESPONSE_ARCHITECT = "response_architect"


STAGES = tuple(AgentKey)
AGENT_NAMES = {
    AgentKey.IDENTITY_GUARDIAN: "Identity Guardian",
    AgentKey.CLOUD_SENTINEL: "Cloud Sentinel",
    AgentKey.RISK_ORACLE: "Risk Oracle",
    AgentKey.MEMORY_KEEPER: "Memory Keeper",
    AgentKey.COORDINATOR: "Coordinator",
    AgentKey.RESPONSE_ARCHITECT: "Response Architect",
}


class TriggerInvestigation(GraphModel):
    request_id: Key
    event_id: GraphID
    evidence_ids: list[GraphID] = Field(default_factory=list, max_length=10)
    at: AwareDatetime | None = None


class ModelNote(GraphModel):
    summary: Annotated[str, Field(min_length=1, max_length=1000)]
    evidence_ids: list[GraphID] = Field(max_length=20)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)

    @field_validator("summary")
    @classmethod
    def sanitized(cls, value: str) -> str:
        return reject_secret(value)


class SimulationProposal(GraphModel):
    relationship_id: GraphID
    finding_ids: list[GraphID]
    before_paths: int
    after_paths: int
    removed_path_ids: list[GraphID]
    action: Literal["propose_relationship_removal"] = "propose_relationship_removal"
    simulation_only: Literal[True] = True
    execution_allowed: Literal[False] = False


class AgentOutput(GraphModel):
    stage: AgentKey
    summary: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence_ids: list[GraphID] = Field(min_length=1, max_length=20)
    finding_ids: list[GraphID] = Field(default_factory=list, max_length=100)
    identity_ids: list[GraphID] = Field(default_factory=list, max_length=50)
    resource_ids: list[GraphID] = Field(default_factory=list, max_length=50)
    memory_ids: list[GraphID] = Field(default_factory=list, max_length=50)
    conflict_ids: list[GraphID] = Field(default_factory=list, max_length=100)
    analysis_id: GraphID | None = None
    relationship_ids: list[GraphID] = Field(default_factory=list, max_length=100)
    proposals: list[SimulationProposal] = Field(default_factory=list, max_length=3)
    limitations: list[str] = Field(default_factory=list, max_length=12)
    model_note: ModelNote | None = None
    privileged_actions: tuple[()] = ()


class AgentRunRecord(GraphModel):
    id: GraphID
    investigation_id: GraphID
    agent_id: GraphID
    stage: AgentKey
    status: Literal["pending", "running", "prepared", "completed", "failed"]
    attempt_count: int
    input_evidence_ids: list[GraphID]
    retrieved_memory_ids: list[GraphID]
    output: AgentOutput | None
    pending_task_ids: list[GraphID]
    handoff_target: GraphID
    handoff_id: GraphID | None
    memory_id: GraphID | None
    operation_id: str
    error_code: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class TaskRecord(GraphModel):
    id: GraphID
    agent_id: GraphID
    stage: AgentKey
    status: Literal["pending", "running", "completed", "awaiting_review"]
    task_type: Literal["workflow_step", "remediation_review"]
    depends_on: list[GraphID]


class InvestigationRecord(GraphModel):
    id: GraphID
    status: Literal["pending", "running", "retryable", "blocked", "completed"]
    next_stage: AgentKey | None
    completed_steps: int
    provider_mode: Literal["mock", "gemini"]
    event_id: GraphID
    runs: list[AgentRunRecord]
    tasks: list[TaskRecord]
    created_at: AwareDatetime
    updated_at: AwareDatetime

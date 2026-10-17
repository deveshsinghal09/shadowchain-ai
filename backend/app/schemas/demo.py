from typing import Any, Literal

from pydantic import AwareDatetime, Field

from app.models.events import DATASET_VERSION, Scenario
from app.models.graph import GraphModel


class PotentialPath(GraphModel):
    identity_key: str
    resource_key: str
    principal_chain: list[str]
    relationship_ids: list[str]
    permission_id: str
    path_type: Literal["potential"] = "potential"


class GraphSnapshot(GraphModel):
    synthetic: Literal[True] = True
    dataset_version: str = DATASET_VERSION
    scenario: Scenario
    as_of: AwareDatetime
    knowledge_revision: int
    semantic_sha256: str
    counts: dict[str, int]
    nodes: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    potential_sensitive_paths: list[PotentialPath]
    reachable_roles: dict[str, list[str]]
    stale_justifications: list[dict[str, Any]]
    memory_conflicts: list[dict[str, Any]]
    analysis_model: Literal["synthetic-policy-subset-v1"] = "synthetic-policy-subset-v1"


class SnapshotComparison(GraphModel):
    scenario: Scenario
    before: AwareDatetime
    after: AwareDatetime
    before_sha256: str
    after_sha256: str
    nodes_added: list[str]
    nodes_removed: list[str]
    nodes_changed: list[str]
    relationships_added: list[str]
    relationships_removed: list[str]
    relationships_changed: list[str]
    potential_paths_added: list[PotentialPath]
    potential_paths_removed: list[PotentialPath]


class CompareRequest(GraphModel):
    before: AwareDatetime
    after: AwareDatetime


class DemoStatus(GraphModel):
    synthetic: Literal[True] = True
    dataset_version: str
    manifest_sha256: str
    revision: int
    scenario: Scenario
    sequence: int
    watermark: AwareDatetime
    generation: int
    inventory: dict[str, int]


class ReplayResult(GraphModel):
    scenario: Scenario
    applied_event_ids: list[str] = Field(default_factory=list)
    duplicate_event_ids: list[str] = Field(default_factory=list)
    state: DemoStatus

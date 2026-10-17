"""Closed, normalized synthetic security events. No AWS clients or credentials."""

from datetime import UTC
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.db.cypher import fingerprint, stable_id
from app.models.graph import (
    GraphModel,
    NodeInput,
    NodeKind,
    Provenance,
    RelationshipKind,
    Scalar,
    reject_secret,
)

DATASET_VERSION = "synthetic-2026.1"
SOURCE = "shadowchain.synthetic." + DATASET_VERSION
DEMO_TENANT = "shadowchain-synthetic-demo"
DEMO_ACCOUNTS = ("000000000001", "000000000002")
Key = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_./-]*$")]


class Scenario(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class NodeSpec(GraphModel):
    key: Key
    kind: NodeKind
    account_id: Literal["000000000001", "000000000002"] | None = None
    logical_key: Key | None = None
    attributes: dict[str, Scalar] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validated_domain_attributes(self) -> "NodeSpec":
        from datetime import datetime

        NodeInput(
            kind=self.kind,
            account_id=self.account_id,
            attributes=self.attributes,
            provenance=Provenance(
                source=SOURCE,
                source_id=self.key,
                observed_at=datetime(2026, 1, 1, tzinfo=UTC),
                collector="synthetic-event-engine",
                source_uri="urn:shadowchain:" + self.key,
            ),
        )
        if any(
            field in self.attributes
            for field in (
                "synthetic",
                "dataset_version",
                "scenario_id",
                "entity_key",
                "logical_key",
                "source_time",
                "valid_from",
                "valid_to",
                "ingested_at",
                "generation",
                "source_event_id",
                "event_id",
                "sequence",
                "applied_revision",
                "payload",
            )
        ):
            raise ValueError("Event metadata is assigned by the event engine")
        if self.kind == NodeKind.PERMISSION and self.attributes.get("effect") not in {
            "Allow",
            "Deny",
        }:
            raise ValueError("Normalized permission effect must be Allow or Deny")
        return self


class EdgeSpec(GraphModel):
    key: Key
    kind: RelationshipKind
    source_key: Key
    target_key: Key
    valid_to: AwareDatetime | None = None
    justification_key: Key | None = None
    justification_expires_at: AwareDatetime | None = None

    @field_validator("valid_to", "justification_expires_at")
    @classmethod
    def utc_times(cls, value: AwareDatetime | None) -> AwareDatetime | None:
        return value.astimezone(UTC) if value is not None else None


class SyntheticSecurityEvent(GraphModel):
    synthetic: Literal[True] = True
    dataset_version: Literal["synthetic-2026.1"] = DATASET_VERSION
    scenario: Scenario
    source_id: Key
    account_id: Literal["000000000001", "000000000002"]
    sequence: int = Field(ge=1, le=10000)
    occurred_at: AwareDatetime
    event_type: Annotated[str, Field(min_length=1, max_length=128)]
    business_justification: Annotated[str, Field(max_length=1000)] | None = None
    nodes: list[NodeSpec] = Field(default_factory=list, max_length=40)
    edges: list[EdgeSpec] = Field(default_factory=list, max_length=80)
    close_edges: list[Key] = Field(default_factory=list, max_length=40)
    close_nodes: list[Key] = Field(default_factory=list, max_length=40)
    event_id: Annotated[str, Field(pattern=r"^[0-9a-f-]{36}$")] | None = None

    @field_validator("occurred_at")
    @classmethod
    def normalize_time(cls, value: AwareDatetime) -> AwareDatetime:
        return value.astimezone(UTC)

    @field_validator("event_type", "business_justification")
    @classmethod
    def no_secrets(cls, value: str | None) -> str | None:
        return reject_secret(value) if value is not None else None

    @model_validator(mode="after")
    def normalize_identity_and_keys(self) -> "SyntheticSecurityEvent":
        identifier = stable_id(
            "synthetic-event",
            self.dataset_version,
            self.scenario.value,
            self.account_id,
            self.source_id,
        )
        if self.event_id is not None and self.event_id != identifier:
            raise ValueError("Event ID does not match the deterministic source identity")
        object.__setattr__(self, "event_id", identifier)
        for collection in (self.nodes, self.edges):
            keys = [item.key for item in collection]
            if len(set(keys)) != len(keys):
                raise ValueError("Duplicate mutation keys in one event")
        for collection in (self.close_nodes, self.close_edges):
            if len(set(collection)) != len(collection):
                raise ValueError("Duplicate closure keys in one event")
        return self

    @property
    def content_sha256(self) -> str:
        return fingerprint(self.model_dump(mode="json"))


class EventReceipt(GraphModel):
    event_id: str
    graph_id: str
    scenario: Scenario
    sequence: int
    occurred_at: AwareDatetime
    ingested_at: AwareDatetime
    generation: int
    applied_revision: int
    duplicate: bool = False


class ReplayRequest(GraphModel):
    through: AwareDatetime | None = None
    expected_generation: int = Field(ge=0)


class ResetRequest(GraphModel):
    expected_generation: int = Field(ge=0)


class IngestRequest(GraphModel):
    event: SyntheticSecurityEvent
    expected_generation: int = Field(ge=0)

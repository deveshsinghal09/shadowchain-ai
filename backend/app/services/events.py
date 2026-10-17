"""Ordered event ingestion, atomic projection and replay against FalkorDB."""

from collections import Counter
from datetime import datetime

from app.db.errors import GraphConflict
from app.demo.dataset import dataset_definition
from app.models.events import DATASET_VERSION, EventReceipt, Scenario, SyntheticSecurityEvent
from app.models.graph import NodeKind, epoch_us, from_epoch_us
from app.repositories.demo import DemoRepository
from app.schemas.demo import DemoStatus, GraphSnapshot, ReplayResult, SnapshotComparison
from app.services.event_errors import (
    ConcurrentDemoChange,
    DemoGenerationConflict,
    DemoMutationError,
    DemoVersionConflict,
    EventOrderError,
    LateEventError,
)
from app.services.snapshots import build_snapshot, compare_snapshots

MUTABLE_KINDS = {
    NodeKind.PERMISSION,
    NodeKind.EVIDENCE,
    NodeKind.RISK_FINDING,
    NodeKind.INVESTIGATION,
    NodeKind.AGENT_MEMORY,
    NodeKind.DECISION,
    NodeKind.TASK,
    NodeKind.HANDOFF,
    NodeKind.AGENT_RUN,
}


class EventIngestionService:
    def __init__(self, repository: DemoRepository) -> None:
        self.repository = repository
        self.definition = dataset_definition()
        self.manifest_sha256 = self.definition.manifest_sha256

    def check_version(self, dataset: dict) -> None:
        if (
            dataset["dataset_version"] != DATASET_VERSION
            or dataset["manifest_sha256"] != self.manifest_sha256
        ):
            raise DemoVersionConflict()

    def seed(self) -> dict:
        definition = self.definition
        try:
            created = self.repository.seed(definition)
        except GraphConflict:
            # A simultaneous seed can win the DB uniqueness constraint. Verify its
            # committed manifest instead of replaying any partial seed in memory.
            dataset, _, _ = self.repository.graph()
            if dataset["manifest_sha256"] != definition.manifest_sha256:
                raise DemoVersionConflict() from None
            created = False
        return {
            "synthetic": True,
            "created": created,
            "dataset_version": DATASET_VERSION,
            "scenarios": {
                scenario.value: self.status(scenario).model_dump(mode="json")
                for scenario in Scenario
            },
        }

    def status(self, scenario: Scenario) -> DemoStatus:
        dataset, stream = self.repository.state(scenario)
        self.check_version(dataset)
        _, nodes, _ = self.repository.graph()
        inventory = Counter(node["kind"] for node in nodes if node["scenario_id"] == "baseline")
        return DemoStatus(
            dataset_version=dataset["dataset_version"],
            manifest_sha256=dataset["manifest_sha256"],
            revision=dataset["revision"],
            scenario=scenario,
            sequence=stream["sequence"],
            watermark=from_epoch_us(stream["watermark"]),
            generation=stream["generation"],
            inventory=dict(inventory),
        )

    @staticmethod
    def receipt(node: dict, *, duplicate: bool) -> EventReceipt:
        return EventReceipt(
            event_id=node["event_id"],
            graph_id=node["id"],
            scenario=node["scenario_id"],
            sequence=node["sequence"],
            occurred_at=from_epoch_us(node["source_time"]),
            ingested_at=from_epoch_us(node["ingested_at"]),
            generation=node["generation"],
            applied_revision=node["applied_revision"],
            duplicate=duplicate,
        )

    def ingest(self, event: SyntheticSecurityEvent, expected_generation: int) -> EventReceipt:
        event = SyntheticSecurityEvent.model_validate(event.model_dump())
        prefix = event.scenario.value.lower() + "/"
        if (
            not event.source_id.startswith(prefix)
            or any(
                node.kind not in MUTABLE_KINDS
                or not node.key.startswith(prefix)
                or (node.logical_key and not node.logical_key.startswith(prefix))
                for node in event.nodes
            )
            or any(not edge.key.startswith(prefix) for edge in event.edges)
            or any(not key.startswith(prefix) for key in [*event.close_edges, *event.close_nodes])
        ):
            raise DemoMutationError()
        for _ in range(5):
            dataset, nodes, edges = self.repository.graph()
            stored, stream = self.repository.state(event.scenario)
            if dataset["revision"] != stored["revision"]:
                continue
            self.check_version(dataset)
            if dataset["dataset_version"] != event.dataset_version:
                raise DemoVersionConflict()
            if stream["generation"] != expected_generation:
                raise DemoGenerationConflict()
            existing = [node for node in nodes if node.get("event_id") == event.event_id]
            if existing:
                if existing[0]["content_sha256"] != event.content_sha256:
                    raise GraphConflict()
                return self.receipt(existing[0], duplicate=True)
            if epoch_us(event.occurred_at) < stream["watermark"]:
                raise LateEventError()
            if event.sequence != stream["sequence"] + 1:
                raise EventOrderError()
            try:
                result = self.repository.apply(event, dataset, stream, nodes, edges)
            except (ValueError, KeyError) as exc:
                raise DemoMutationError() from exc
            except GraphConflict:
                # Safe reconciliation only: the DB may already hold this event, but
                # it must have exactly the same source identity and content digest.
                continue
            if result is not None:
                return self.receipt(result, duplicate=False)
        raise ConcurrentDemoChange()

    def replay(
        self, scenario: Scenario, expected_generation: int, through: datetime | None = None
    ) -> ReplayResult:
        applied, duplicates = [], []
        # Immutable templates are not runtime state; persisted event receipts/cursor
        # determine what is already committed after a restart.
        for event in self.definition.events[scenario]:
            if through is not None and event.occurred_at > through:
                continue
            receipt = self.ingest(event, expected_generation)
            (duplicates if receipt.duplicate else applied).append(receipt.event_id)
        status = self.status(scenario)
        if status.generation != expected_generation:
            raise DemoGenerationConflict()
        return ReplayResult(
            scenario=scenario,
            applied_event_ids=applied,
            duplicate_event_ids=duplicates,
            state=status,
        )

    def reset(self, scenario: Scenario, expected_generation: int) -> DemoStatus:
        for _ in range(5):
            dataset, stream = self.repository.state(scenario)
            self.check_version(dataset)
            if stream["generation"] != expected_generation:
                raise DemoGenerationConflict()
            if self.repository.reset(scenario, dataset, stream):
                return self.status(scenario)
        raise ConcurrentDemoChange()

    def snapshot(self, scenario: Scenario, at: datetime | None = None) -> GraphSnapshot:
        for _ in range(5):
            dataset, nodes, edges = self.repository.graph()
            self.check_version(dataset)
            state_dataset, stream = self.repository.state(scenario)
            if dataset["revision"] == state_dataset["revision"]:
                return build_snapshot(
                    dataset, nodes, edges, scenario, at or from_epoch_us(stream["watermark"])
                )
        raise ConcurrentDemoChange()

    def compare(self, scenario: Scenario, before: datetime, after: datetime) -> SnapshotComparison:
        dataset, nodes, edges = self.repository.graph()
        self.check_version(dataset)
        return compare_snapshots(
            build_snapshot(dataset, nodes, edges, scenario, before),
            build_snapshot(dataset, nodes, edges, scenario, after),
        )

"""Idempotent schema migration with asynchronous index/constraint verification."""

from time import monotonic, sleep

from app.core.time import utc_now
from app.db.errors import GraphQueryError, GraphSchemaError
from app.db.falkordb import GraphDatabase
from app.models.graph import ACCESS_RELATIONSHIPS, CLOUD_KINDS, NodeKind, RelationshipKind, epoch_us

SCHEMA_VERSION = 10
COMMON_REQUIRED = [
    "id",
    "tenant_id",
    "kind",
    "source",
    "source_id",
    "collector",
    "source_uri",
    "observed_at",
    "created_at",
    "updated_at",
    "fingerprint",
]
EDGE_REQUIRED = [
    *COMMON_REQUIRED,
    "source_node_id",
    "target_node_id",
    "source_kind",
    "target_kind",
    "valid_from",
    "confidence",
    "verification_status",
]


def required_constraints() -> list[tuple[str, str, str, list[str]]]:
    return [
        ("UNIQUE", "NODE", "LiveStream", ["id"]),
        ("UNIQUE", "NODE", "LiveEvent", ["id"]),
        ("UNIQUE", "NODE", "LiveEvent", ["tenant_id", "sequence"]),
        ("MANDATORY", "NODE", "LiveStream", [*COMMON_REQUIRED, "sequence", "lock_version"]),
        (
            "MANDATORY",
            "NODE",
            "LiveEvent",
            [
                *COMMON_REQUIRED,
                "sequence",
                "event_type",
                "entity_ids_json",
                "references_truncated",
            ],
        ),
        ("UNIQUE", "NODE", "ApiRateBudget", ["id"]),
        ("UNIQUE", "NODE", "AwsImportStream", ["id"]),
        (
            "MANDATORY",
            "NODE",
            "AwsImportStream",
            [*COMMON_REQUIRED, "account_id", "revision", "watermark"],
        ),
        (
            "MANDATORY",
            "NODE",
            "AwsSyncRun",
            ["integration_kind", "request_sha256", "response_json", "coverage_json"],
        ),
        ("MANDATORY", "NODE", "AwsIamObservation", ["account_id", "valid_from"]),
        (
            "MANDATORY",
            "NODE",
            "ReportExport",
            ["investigation_id", "artifact_uri", "export_mode", "status", "content_sha256"],
        ),
        ("UNIQUE", "NODE", "RemediationStream", ["id"]),
        (
            "MANDATORY",
            "NODE",
            "RemediationReview",
            ["space_id", "proposal_id", "reviewer_id", "audit_json"],
        ),
        ("MANDATORY", "NODE", "RemediationStream", [*COMMON_REQUIRED, "revision", "lock_version"]),
        (
            "MANDATORY",
            "NODE",
            "RemediationProposal",
            [
                "space_id",
                "status",
                "revision",
                "analysis_request_json",
                "relationship_json",
                "source_sha256",
                "category",
            ],
        ),
        (
            "MANDATORY",
            "NODE",
            "RemediationSimulation",
            [
                "space_id",
                "proposal_ids_json",
                "result_sha256",
                "chunk_ids_json",
                "simulator_version",
            ],
        ),
        ("UNIQUE", "NODE", "CaseStream", ["id"]),
        ("MANDATORY", "NODE", "CaseStream", ["id", "tenant_id"]),
        ("UNIQUE", "NODE", "CaseHead", ["id"]),
        ("MANDATORY", "NODE", "CaseHead", ["id", "tenant_id", "head_id", "generation"]),
        (
            "MANDATORY",
            "NODE",
            "SecurityCase",
            [
                "status",
                "severity",
                "path_id",
                "revision",
                "owner_agent_id",
                "space_id",
                "generation",
                "assessed_at",
                "case_version",
            ],
        ),
        (
            "MANDATORY",
            "NODE",
            "WorkflowInvestigation",
            [
                "request_sha256",
                "request_json",
                "sequence",
                "revision",
                "lock_version",
                "status",
                "space_id",
                "lease_expires_at",
                "provider_mode",
            ],
        ),
        (
            "MANDATORY",
            "NODE",
            "WorkflowRun",
            [
                "investigation_id",
                "agent_id",
                "stage",
                "status",
                "sequence",
                "attempt_count",
                "input_evidence_ids_json",
                "retrieved_memory_ids_json",
                "output_json",
                "pending_task_ids_json",
                "handoff_target",
            ],
        ),
        ("UNIQUE", "NODE", "MemorySeries", ["id"]),
        ("MANDATORY", "NODE", "MemorySpace", ["name", "revision", "lock_version"]),
        ("MANDATORY", "NODE", "MemorySeries", [*COMMON_REQUIRED, "space_id", "head_id", "version"]),
        (
            "MANDATORY",
            "NODE",
            "PersistentMemory",
            [
                "memory_key",
                "series_id",
                "space_id",
                "memory_version",
                "author_agent_id",
                "audience",
                "state",
                "memory_type",
                "confidence",
                "verification_status",
                "valid_from",
                "source_trust",
                "memory_engine_version",
                "content",
                "source_provenance_json",
                "reference_count",
            ],
        ),
        ("UNIQUE", "NODE", "RiskAnalysis", ["id"]),
        ("MANDATORY", "NODE", "RiskAnalysis", COMMON_REQUIRED),
        ("UNIQUE", "NODE", "DemoDataset", ["id"]),
        ("UNIQUE", "NODE", "DemoStream", ["id"]),
        ("UNIQUE", "NODE", "Entity", ["id"]),
        ("MANDATORY", "NODE", "Entity", COMMON_REQUIRED),
        ("UNIQUE", "NODE", "SecurityEvent", ["tenant_id", "account_id", "source", "source_id"]),
        *[("MANDATORY", "NODE", kind.value, ["account_id"]) for kind in sorted(CLOUD_KINDS)],
        ("MANDATORY", "NODE", "SecurityEvent", ["event_time", "event_type", "content_sha256"]),
        ("MANDATORY", "NODE", "Evidence", ["evidence_type", "content_sha256"]),
        *[("UNIQUE", "RELATIONSHIP", kind.value, ["id"]) for kind in RelationshipKind],
        *[("MANDATORY", "RELATIONSHIP", kind.value, EDGE_REQUIRED) for kind in RelationshipKind],
        *[
            ("MANDATORY", "RELATIONSHIP", kind.value, ["source_event_id"])
            for kind in sorted(ACCESS_RELATIONSHIPS)
        ],
    ]


def required_indexes() -> list[tuple[str, str, list[str]]]:
    return [
        ("NODE", "LiveStream", ["id", "tenant_id"]),
        ("NODE", "LiveEvent", ["id", "tenant_id", "sequence"]),
        ("NODE", "ApiRateBudget", ["id", "tenant_id"]),
        ("NODE", "Evidence", ["record_id"]),
        ("NODE", "AwsImportStream", ["id", "tenant_id", "account_id"]),
        ("NODE", "AwsSyncRun", ["id", "tenant_id", "account_id"]),
        ("NODE", "AwsIamObservation", ["id", "tenant_id", "account_id", "valid_from"]),
        ("NODE", "ReportExport", ["id", "tenant_id", "investigation_id", "status"]),
        ("NODE", "RemediationStream", ["id", "tenant_id"]),
        ("NODE", "RemediationProposal", ["id", "tenant_id", "space_id", "status", "category"]),
        ("NODE", "RemediationSimulation", ["id", "tenant_id", "space_id"]),
        ("NODE", "RemediationReview", ["id", "tenant_id", "space_id", "proposal_id"]),
        ("NODE", "SecurityCase", ["id", "tenant_id", "status", "path_id", "severity", "space_id"]),
        ("NODE", "CaseHead", ["id", "tenant_id"]),
        ("NODE", "CaseStream", ["id", "tenant_id"]),
        ("NODE", "Decision", ["case_id", "action"]),
        ("NODE", "WorkflowInvestigation", ["id", "tenant_id", "status", "lease_expires_at"]),
        ("NODE", "WorkflowRun", ["id", "tenant_id", "investigation_id", "agent_id", "status"]),
        ("NODE", "Task", ["investigation_id", "stage", "task_type", "status"]),
        ("NODE", "MemorySeries", ["id", "tenant_id", "space_id"]),
        ("NODE", "AgentMemory", ["series_id", "space_id", "state", "memory_type", "claim_key"]),
        ("NODE", "RiskAnalysis", ["id", "tenant_id", "observed_at"]),
        ("NODE", "RiskFinding", ["analysis_id", "path_id", "assessed_at"]),
        ("NODE", "DemoDataset", ["id", "tenant_id"]),
        ("NODE", "DemoStream", ["id", "tenant_id"]),
        ("NODE", "Entity", ["id", "tenant_id"]),
        *[
            ("NODE", kind.value, ["id", "tenant_id", "account_id", "source_id"])
            for kind in NodeKind
        ],
        ("NODE", "SecurityEvent", ["source", "event_time"]),
        ("NODE", "Evidence", ["content_sha256"]),
        ("NODE", "Entity", ["entity_key", "scenario_id", "dataset_version", "source_time"]),
        *[
            ("RELATIONSHIP", kind.value, ["id", "tenant_id", "valid_from"])
            for kind in RelationshipKind
        ],
    ]


class SchemaManager:
    def __init__(self, database: GraphDatabase) -> None:
        self.database = database

    def initialize(self) -> None:
        database = self.database
        database.check_ready()
        versions = database.query(
            "MATCH (s:SchemaVersion {id: $id}) RETURN s.version",
            {"id": "shadowchain-primary"},
            read_only=True,
        )
        if versions and versions[0][0] > SCHEMA_VERSION:
            raise GraphSchemaError()
        indexed = self._operational_indexes()
        for entity, label, fields in required_indexes():
            missing = [field for field in fields if (entity, label, field) not in indexed]
            if not missing:
                continue
            pattern = f"(n:{label})" if entity == "NODE" else f"()-[n:{label}]-()"
            cypher = f"CREATE INDEX FOR {pattern} ON (" + ", ".join(f"n.{f}" for f in missing) + ")"
            try:
                database.query(cypher, idempotent=True)
            except GraphQueryError as exc:
                if "already indexed" not in str(exc.__cause__).lower() and (
                    "already exists" not in str(exc.__cause__).lower()
                ):
                    raise GraphSchemaError() from exc
        existing = {
            (kind, entity, label, tuple(sorted(fields)))
            for kind, label, fields, entity, _ in self._constraint_rows()
        }
        for kind, entity, label, fields in required_constraints():
            if (kind, entity, label, tuple(sorted(fields))) not in existing:
                database.create_constraint(kind, entity, label, fields)
        self.verify()
        # Record completion only AFTER constraints are operational; never mark partial DDL ready.
        database.query(
            "MERGE (s:SchemaVersion {id: $id}) "
            "ON CREATE SET s.created_at = $now "
            "SET s.version = $version, s.verified_at = $now RETURN s.version",
            {"id": "shadowchain-primary", "version": SCHEMA_VERSION, "now": epoch_us(utc_now())},
            idempotent=True,
        )

    def verify(self) -> None:
        expected = {
            (kind, entity, label, tuple(sorted(fields)))
            for kind, entity, label, fields in required_constraints()
        }
        deadline = monotonic() + self.database.schema_timeout
        expected_indexes = {
            (entity, label, field)
            for entity, label, fields in required_indexes()
            for field in fields
        }
        while True:
            rows = self._constraint_rows()
            operational = set()
            for kind, label, fields, entity, status in rows:
                key = (kind, entity, label, tuple(sorted(fields)))
                if key in expected and status == "FAILED":
                    raise GraphSchemaError()
                if status == "OPERATIONAL":
                    operational.add(key)
            if expected <= operational and expected_indexes <= self._operational_indexes():
                return
            if monotonic() >= deadline:
                raise GraphSchemaError()
            sleep(0.05)

    def _constraint_rows(self) -> list:
        return self.database.query(
            "CALL db.constraints() YIELD type, label, properties, entitytype, status "
            "RETURN type, label, properties, entitytype, status",
            read_only=True,
        )

    def _operational_indexes(self) -> set[tuple[str, str, str]]:
        rows = self.database.query(
            "CALL db.indexes() YIELD label, types, entitytype, status "
            "RETURN label, types, entitytype, status",
            read_only=True,
        )
        operational = set()
        for label, fields, entity, status in rows:
            if status == "FAILED":
                raise GraphSchemaError()
            if status == "OPERATIONAL":
                operational.update(
                    (entity, label, field) for field, types in fields.items() if "RANGE" in types
                )
        return operational

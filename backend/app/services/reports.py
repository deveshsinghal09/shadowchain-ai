"""Redacted JSON artifacts. Graph metadata is authoritative; file bodies never enter the graph."""

import hashlib
import json
import os

from app.core.time import utc_now
from app.db.cypher import creation_properties, fingerprint, stable_id
from app.db.errors import GraphConflict, GraphNotFound
from app.integrations.aws_client import AwsGateway
from app.integrations.s3_reports import S3ReportStore
from app.models.aws import ReportResult
from app.models.graph import NodeInput, NodeRef, Provenance, RelationshipInput, epoch_us
from app.models.graph import NodeKind as N
from app.models.graph import RelationshipKind as R
from app.repositories.graph import build_node_properties, build_relationship_properties
from app.services.aws_errors import ReportConflict


def render_report(case, report_id):
    def reference(kind, value):
        return kind + "-" + hashlib.sha256((report_id + value).encode()).hexdigest()[:20]

    body = {
        "report_id": report_id,
        "format": "shadowchain-redacted-summary-v1",
        "investigation_ref": reference("case", case.investigation_id),
        "severity": case.severity
        if case.severity in {"low", "medium", "high", "critical"}
        else "unknown",
        "status": case.status.value,
        "created_at": case.created_at.isoformat(),
        "classification": "POTENTIAL ATTACK PATHS; not proven AWS effective authorization",
        "identities": [reference("identity", item["id"]) for item in case.affected_identities],
        "resources": [reference("resource", item["id"]) for item in case.affected_resources],
        "evidence_counts": {
            "verified": len(case.verified_evidence),
            "contradictory": len(case.contradictory_evidence),
        },
        "task_counts": {"completed": len(case.completed_tasks), "pending": len(case.pending_tasks)},
        "timeline": [
            {
                "timestamp": item.timestamp.isoformat(),
                "category": item.category
                if item.category
                in {"security_event", "relationship_change", "audit", "memory_observation"}
                else "observation",
            }
            for item in case.historical_change_timeline
        ],
        "recommendation_count": len(case.recommended_response),
        "redaction": "Account IDs, ARNs, names, source identifiers, IPs, raw evidence, "
        "free text and credentials omitted",
        "execution_allowed": False,
    }
    return (json.dumps(body, sort_keys=True, indent=2) + "\n").encode()


class ReportService:
    def __init__(self, investigation_service, settings, gateway_factory=AwsGateway):
        self.investigations, self.settings, self.gateway_factory = (
            investigation_service,
            settings,
            gateway_factory,
        )
        self.database = investigation_service.repository.database
        self.tenant = investigation_service.repository.scope.tenant_id

    def saved(self, report_id):
        rows = self.database.query(
            "MATCH (e:ReportExport {tenant_id: $tenant, id: $id}) RETURN properties(e)",
            {"tenant": self.tenant, "id": report_id},
            read_only=True,
        )
        if not rows:
            raise GraphNotFound()
        node = rows[0][0]
        self.investigations.require(node["investigation_id"])
        return ReportResult(
            report_id=node["id"],
            status=node["status"],
            export_mode=node["export_mode"],
            content_sha256=node["content_sha256"],
            byte_count=int(node["byte_count"]),
            artifact_uri=node["artifact_uri"],
        )

    def export(self, request):
        self.investigations.require(request.investigation_id)
        destination = (
            self.settings.s3_export_bucket
            if self.settings.export_mode == "s3"
            else str(self.settings.export_local_dir.resolve())
        )
        key = stable_id(
            self.tenant,
            request.request_id,
            request.investigation_id,
            self.settings.export_mode,
            destination,
        )
        provenance = Provenance(
            source="shadowchain.reports.v1",
            source_id=key,
            observed_at=utc_now(),
            collector="allowlist-summary-v1",
            source_uri="urn:shadowchain:report:" + key,
        )
        report_id = stable_id(self.tenant, N.EVIDENCE.value, "", provenance.source, key)
        try:
            existing = self.saved(report_id)
            if existing.status == "PUBLISHED":
                return existing
        except GraphNotFound:
            existing = None
        case = self.investigations.detail(request.investigation_id)
        payload = render_report(case, report_id)
        digest = hashlib.sha256(payload).hexdigest()
        directory = self.settings.export_local_dir.resolve()
        path = directory / (report_id + ".json")
        uri = (
            "s3://"
            + str(self.settings.s3_export_bucket)
            + "/shadowchain-reports/"
            + report_id
            + ".json"
            if self.settings.export_mode == "s3"
            else str(path)
        )
        if existing and existing.content_sha256 != digest:
            raise ReportConflict()
        now = epoch_us(utc_now())
        if existing is None:
            props = build_node_properties(
                self.tenant,
                NodeInput(
                    kind=N.EVIDENCE,
                    provenance=provenance,
                    attributes={
                        "evidence_type": "redacted-investigation-report",
                        "content_sha256": digest,
                        "artifact_uri": uri,
                        "export_mode": self.settings.export_mode,
                        "byte_count": str(len(payload)),
                        "redaction_version": "allowlist-summary-v1",
                        "investigation_id": request.investigation_id,
                        "status": "PREPARED",
                        "request_sha256": fingerprint(request.model_dump()),
                    },
                ),
                now,
            )
            edge = build_relationship_properties(
                self.tenant,
                RelationshipInput(
                    kind=R.SUPPORTS,
                    source=NodeRef(kind=N.EVIDENCE, id=report_id),
                    target=NodeRef(kind=N.INVESTIGATION, id=request.investigation_id),
                    provenance=provenance,
                    valid_from=provenance.observed_at,
                    verification_status="verified",
                ),
                now,
            )
            literal, bindings = creation_properties(props, "report")
            edge_literal, edge_params = creation_properties(edge, "link")
            try:
                self.database.mutate(
                    "MATCH (i:Investigation {tenant_id: $tenant, id: $case_id}) "
                    f"CREATE (e:Entity:Evidence:ReportExport {literal}) "
                    f"CREATE (e)-[:SUPPORTS {edge_literal}]->(i) RETURN e.id",
                    {
                        **bindings,
                        **edge_params,
                        "tenant": self.tenant,
                        "case_id": request.investigation_id,
                    },
                    idempotent=True,
                    tenant_id=self.tenant,
                    event_type="report.prepared",
                    entity_ids=[report_id, request.investigation_id],
                )
            except GraphConflict:
                other = self.saved(report_id)
                if other.content_sha256 != digest:
                    raise ReportConflict() from None
        self.investigations.require(request.investigation_id)
        if self.settings.export_mode == "s3":
            gateway = self.gateway_factory(self.settings)
            try:
                S3ReportStore(gateway).publish(report_id, payload, digest)
            finally:
                gateway.close()
        else:
            directory.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                raise ReportConflict()
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(payload)
                    output.flush()
                    os.fsync(output.fileno())
            except FileExistsError:
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise ReportConflict() from None
        self.database.mutate(
            "MATCH (e:ReportExport {tenant_id: $tenant, id: $id}) "
            "WHERE e.content_sha256 = $digest "
            "SET e.status = 'PUBLISHED',e.updated_at = $now RETURN e.id",
            {"tenant": self.tenant, "id": report_id, "digest": digest, "now": epoch_us(utc_now())},
            idempotent=True,
            tenant_id=self.tenant,
            event_type="report.published",
            entity_ids=[report_id, request.investigation_id],
        )
        return self.saved(report_id)

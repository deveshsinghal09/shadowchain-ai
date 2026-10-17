"""Six logical agents. All security decisions come from graph reads and deterministic rules."""

import json
from datetime import datetime

from app.models.graph import epoch_us, from_epoch_us
from app.models.memory import MemoryQuery
from app.models.workflow import AgentKey, AgentOutput, SimulationProposal
from app.schemas.risk import AnalysisRequest, WhatIfRequest
from app.services.risk import RiskEngine
from app.services.workflow_errors import WorkflowSourceChanged


class SecurityAgents:
    def __init__(self, repository, risk_repository):
        self.repository, self.risk_repository = repository, risk_repository

    def changes(self, investigation: dict) -> list[dict]:
        source = json.loads(investigation["request_json"])
        inputs = [source["request"]["event_id"], *source["request"]["evidence_ids"]]
        rows = self.repository.read(
            "MATCH (event:Entity:SecurityEvent {tenant_id: $tenant_id}) "
            "WHERE event.id IN $ids "
            "OPTIONAL MATCH (a:Entity {tenant_id: $tenant_id})-[r]->"
            "(b:Entity {tenant_id: $tenant_id}) "
            "WHERE r.tenant_id = $tenant_id AND "
            "(r.source_event_id = event.id OR r.closed_by_event_id = event.id) "
            "RETURN properties(event), collect({edge: properties(r), "
            "a: properties(a), b: properties(b)})[0..101]",
            {"ids": inputs},
        )
        changes = []
        for _, items in rows:
            for item in items:
                if item["edge"] is not None:
                    changes.append(item)
        if len(changes) > 100:
            raise WorkflowSourceChanged()
        return sorted(changes, key=lambda item: item["edge"]["id"])

    def execute(self, stage, investigation, memory, prior: list[AgentOutput]) -> AgentOutput:
        source = json.loads(investigation["request_json"])
        request = source["request"]
        inputs = sorted(set([request["event_id"], *request["evidence_ids"]]))
        event = self.repository.node(request["event_id"])
        # A reset or source replacement must not silently reinterpret an unfinished investigation.
        for identifier, expected in source["source_fingerprints"].items():
            if self.repository.node(identifier)["fingerprint"] != expected:
                raise WorkflowSourceChanged()
        changes = self.changes(investigation)
        at = datetime.fromisoformat(source["at"])
        base = dict(stage=stage, evidence_ids=inputs)
        identities = sorted(
            {
                n["id"]
                for item in changes
                for n in (item["a"], item["b"])
                if n["kind"] in {"Identity", "Role"}
            }
        )
        resources = sorted(
            {n["id"] for item in changes for n in (item["a"], item["b"]) if n["kind"] == "Resource"}
        )
        if stage == AgentKey.CLOUD_SENTINEL:
            return AgentOutput(
                **base,
                summary=f"Processed event {event['source_id']}: "
                f"{len(changes)} provenance-linked configuration relationship changes.",
                relationship_ids=[c["edge"]["id"] for c in changes],
                resource_ids=resources,
                identity_ids=identities,
            )
        if stage == AgentKey.IDENTITY_GUARDIAN:
            history = self.repository.read(
                "MATCH (a:Entity {tenant_id: $tenant_id})-[r:CAN_ASSUME|HAS_POLICY]->"
                "(b:Entity {tenant_id: $tenant_id}) WHERE a.id IN $ids "
                "AND r.tenant_id = $tenant_id "
                "AND r.valid_from <= $at "
                "RETURN r.id, r.valid_to, r.justification_expires_at LIMIT 101",
                {"ids": identities, "at": epoch_us(at)},
            )
            if len(history) > 100:
                raise WorkflowSourceChanged()
            expired = sum(
                (end is None or at < from_epoch_us(end))
                and justification is not None
                and from_epoch_us(justification) <= at
                for _, end, justification in history
            )
            return AgentOutput(
                **base,
                summary=f"Evaluated {len(identities)} affected IAM principals "
                f"and {len(history)} historical assumption/policy edges; "
                f"{expired} open-ended grants have expired justifications.",
                identity_ids=identities,
                relationship_ids=[r[0] for r in history],
                limitations=[
                    "Configuration history is modeled; session/use telemetry is absent. "
                    "Unusual access behavior cannot be established from configuration alone."
                ],
            )
        if stage == AgentKey.RISK_ORACLE:
            result = RiskEngine(self.risk_repository).discover(
                AnalysisRequest(at=at, scenario=source["scenario"])
            )
            if len(result.findings) > 100:
                raise WorkflowSourceChanged()
            return AgentOutput(
                **base,
                summary=f"Graph traversal identified {len(result.findings)} "
                "POTENTIAL ATTACK PATHS; connected paths do not prove effective AWS authorization.",
                analysis_id=result.analysis_id,
                finding_ids=[f.finding_id for f in result.findings],
                identity_ids=sorted({f.identity["id"] for f in result.findings}),
                resource_ids=sorted({f.resource["id"] for f in result.findings}),
                limitations=result.limitations,
            )
        if stage == AgentKey.MEMORY_KEEPER:
            subjects = sorted(
                {
                    investigation["id"],
                    request["event_id"],
                    *(i for p in prior for i in p.identity_ids + p.resource_ids),
                }
            )[:20]
            records = memory.retrieve_related_memories(MemoryQuery(subject_ids=subjects, limit=50))
            conflicts = memory.detect_memory_conflicts(MemoryQuery(subject_ids=subjects, limit=50))
            return AgentOutput(
                **base,
                summary=f"Retrieved {len(records)} graph-grounded shared "
                f"observations and {len(conflicts)} visible claim conflicts; "
                "retained source provenance.",
                memory_ids=[m.id for m in records],
                conflict_ids=[c.id for c in conflicts],
            )
        if stage == AgentKey.COORDINATOR:
            tasks = [
                t
                for t in self.repository.inspect(investigation["id"]).tasks
                if t.task_type == "workflow_step"
            ]
            if len(tasks) != 6 or any(
                t.agent_id != self.repository.agent_id(t.stage) for t in tasks
            ):
                raise WorkflowSourceChanged()
            return AgentOutput(
                **base,
                summary="Coordinator assigned six unique dependency-ordered "
                "steps and creates bounded finding-review tasks for the Response Architect. "
                "Remediation proposals require independent human authorization.",
                finding_ids=next(p.finding_ids for p in prior if p.stage == AgentKey.RISK_ORACLE),
            )
        oracle = next(p for p in prior if p.stage == AgentKey.RISK_ORACLE)
        result = self.risk_repository.saved(oracle.analysis_id)
        proposals = []
        for finding in result.findings[:3]:
            rows = self.repository.read(
                "MATCH (a:Entity {tenant_id: $tenant_id})-[r:HAS_POLICY]->"
                "(b:Entity {tenant_id: $tenant_id}) WHERE r.tenant_id = $tenant_id "
                "AND r.id IN $ids RETURN r.id ORDER BY r.id LIMIT 1",
                {"ids": finding.relationship_ids},
            )
            if not rows:
                continue
            relationship_id = rows[0][0]
            simulated = RiskEngine(self.risk_repository).what_if(
                WhatIfRequest(
                    at=at, scenario=source["scenario"], remove_relationship_ids=[relationship_id]
                )
            )
            proposals.append(
                SimulationProposal(
                    relationship_id=relationship_id,
                    finding_ids=[finding.finding_id],
                    before_paths=len(simulated.before.findings),
                    after_paths=len(simulated.after.findings),
                    removed_path_ids=simulated.removed_path_ids,
                )
            )
        return AgentOutput(
            **base,
            summary=f"Produced {len(proposals)} evidence-backed grant-removal "
            "proposals using non-mutating graph what-if analysis. No IAM changes were executed.",
            finding_ids=oracle.finding_ids,
            analysis_id=oracle.analysis_id,
            proposals=proposals,
        )

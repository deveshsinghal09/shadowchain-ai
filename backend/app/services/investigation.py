"""Deterministic case intelligence, historical reconstruction and durable analyst handoffs."""

import json

from app.core.time import utc_now
from app.db.cypher import fingerprint
from app.db.errors import GraphConflict, GraphNotFound
from app.models.graph import NodeKind as N
from app.models.graph import NodeRef, epoch_us, from_epoch_us
from app.models.graph import RelationshipKind as R
from app.models.investigation import (
    TRANSITIONS,
    AuditEntry,
    CaseCheck,
    CaseDetail,
    CaseGraph,
    CaseHandoff,
    CaseTask,
    EvidenceView,
    HistoricalCheck,
    HypothesisView,
    InvestigationStatus,
    PriorIncident,
    Reconstruction,
    ReviewCaseEvidence,
    ReviewHypothesis,
    TimelineEntry,
    TraceStatement,
    TransferCase,
    TransitionCase,
)
from app.models.memory import MemoryQuery, MemoryType
from app.models.workflow import AgentKey, AgentOutput
from app.repositories.investigation import InvestigationRepository
from app.repositories.memory import MemoryRepository
from app.repositories.risk import RiskRepository
from app.schemas.risk import WhatIfRequest
from app.services.investigation_errors import (
    CaseAccessDenied,
    CaseLimitExceeded,
    InvalidCaseTransition,
)
from app.services.memory import MemoryService
from app.services.memory_access import MemoryAccessAdmin
from app.services.risk import RiskEngine


class InvestigationService:
    def __init__(self, repository: InvestigationRepository, actor_agent_id: str):
        self.repository, self.actor = repository, actor_agent_id

    def memory(self, case):
        return MemoryService(
            MemoryRepository(
                self.repository.database, self.repository.scope, case["space_id"], self.actor
            )
        )

    def require(self, identifier, *, owner=False, reviewer=False):
        case = self.repository.case(identifier)
        self.memory(case).repository.access()
        authorized = self.repository.read(
            "MATCH path=(i:SecurityCase {tenant_id: $tenant_id, id: $id})"
            "-[assigned:ASSIGNED_TO]->(a:Agent {id: $actor}) "
            "WHERE all(n IN nodes(path) WHERE n.tenant_id = $tenant_id) "
            "AND all(r IN relationships(path) WHERE r.tenant_id = $tenant_id) "
            "AND assigned.valid_from <= $now "
            "AND assigned.verification_status = 'verified' "
            "AND (assigned.valid_to IS NULL OR $now < assigned.valid_to) RETURN a.id",
            {"id": identifier, "actor": self.actor, "now": epoch_us(utc_now())},
        )
        coordinator = self.repository.agent_id(AgentKey.COORDINATOR)
        if (
            not authorized
            or (owner and self.actor != case["owner_agent_id"])
            or (reviewer and self.actor != coordinator)
        ):
            raise CaseAccessDenied()
        return case

    def _nodes(self, identifier):
        return {n["id"]: n for n, _ in self.repository.linked(identifier)}

    def _audits(self, nodes):
        return sorted(
            [
                n
                for n in nodes.values()
                if n["kind"] == "Decision"
                and n.get("action", "").startswith("case.")
                and n.get("action") != "case.hypothesis"
            ],
            key=lambda n: (n["created_at"], n["id"]),
        )

    def _grant(self, case, identifiers):
        admin = MemoryAccessAdmin(self.repository.database, self.repository.scope)
        for identifier in sorted(set(identifiers)):
            node = self.repository.node(identifier)
            if node["kind"] in {"AgentMemory", "MemorySpace", "Handoff"}:
                continue
            admin.grant_context(
                case["space_id"],
                NodeRef(kind=node["kind"], id=identifier),
                "case/context/" + identifier,
            )

    def _mutate(
        self,
        identifier,
        action,
        request_id,
        payload,
        build,
        *,
        expected=None,
        owner=False,
        reviewer=False,
    ):
        repo = self.repository
        key = repo.operation_key(identifier, action, request_id, self.actor)
        digest = fingerprint(payload)
        for _ in range(5):
            case = self.require(identifier, owner=owner, reviewer=reviewer)
            if self.memory(case).repository.access()["role"] not in {"writer", "curator"}:
                raise CaseAccessDenied()
            duplicate = repo.operation(key, digest)
            if duplicate is not None:
                return duplicate
            if expected is not None and case["revision"] != expected:
                raise InvalidCaseTransition()
            stream = repo.stream()
            mutation = repo.mutation(key, digest)
            mutation.params["actor_id"] = self.actor
            details, response, updates = build(case, mutation)
            repo.audit(mutation, key, case, action, self.actor, details, response)
            repo.touch(mutation, case, **updates)
            try:
                if repo.commit(mutation, stream, case):
                    return response
            except GraphConflict:
                continue
        raise InvalidCaseTransition()

    def synchronize(self, identifier, workflow_id, stage, published_memory_id=None):
        """Attach live workflow tasks/memory using an idempotent stage checkpoint."""
        repo = self.repository
        case = self.require(identifier)
        wf = repo.raw(workflow_id)
        if wf["space_id"] != case["space_id"]:
            raise CaseAccessDenied()
        nodes = self._nodes(identifier)
        candidate_memories = []
        if published_memory_id:
            candidate_memories.append(published_memory_id)
        for s in (
            AgentKey.CLOUD_SENTINEL,
            AgentKey.IDENTITY_GUARDIAN,
            AgentKey.RISK_ORACLE,
            AgentKey.MEMORY_KEEPER,
            AgentKey.COORDINATOR,
            AgentKey.RESPONSE_ARCHITECT,
        ):
            run = repo.run(workflow_id, s)
            candidate_memories.extend(json.loads(run["retrieved_memory_ids_json"]))
            if run.get("memory_id"):
                candidate_memories.append(run["memory_id"])
        candidate_memories = sorted(set(candidate_memories))
        if len(candidate_memories) > 100:
            raise CaseLimitExceeded()
        visible = []
        for offset in range(0, len(candidate_memories), 50):
            visible.extend(
                self.memory(case).retrieve_related_memories(
                    MemoryQuery(memory_ids=candidate_memories[offset : offset + 50], limit=100)
                )
            )
        tasks = repo.inspect(workflow_id).tasks

        def build(current, mutation):
            if workflow_id not in nodes:
                mutation.bind(wf, "workflow")
                mutation.edge(current, wf, R.INVESTIGATES, "investigation", "workflow")
            for index, task in enumerate(tasks):
                if task.id in nodes:
                    continue
                value = repo.node(task.id)
                variable = "task" + str(index)
                mutation.bind(value, variable)
                mutation.edge(value, current, R.BELONGS_TO, variable, "investigation")
            for index, memory in enumerate(m for m in visible if m.audience == "team"):
                if memory.id in nodes:
                    continue
                value = {"id": memory.id, "kind": "AgentMemory"}
                mutation.bind(value, "memory" + str(index))
                mutation.edge(current, value, R.REMEMBERS, "investigation", "memory" + str(index))
            return ({"workflow_id": workflow_id, "stage": stage}, {"case_id": identifier}, {})

        return self._mutate(
            identifier,
            "workflow_sync",
            workflow_id + "/" + stage,
            {"workflow_id": workflow_id, "stage": stage},
            build,
        )

    def reconstruct(self, identifier):
        case = self.require(identifier)
        repo = self.repository
        assets = repo.incident_assets(identifier)
        prior = repo.prior_incidents(case)
        previous = []
        for node, shared in prior:
            prior_assets = repo.incident_assets(node["id"])
            previous.append(
                PriorIncident(
                    investigation_id=node["id"],
                    status=node["status"],
                    title=node.get("title", "Historical investigation"),
                    shared_asset_ids=sorted(set(shared)),
                    similarity=len(set(shared)) / max(1, len(set(assets) | set(prior_assets))),
                )
            )
        ids = sorted({identifier, *assets, *(p.investigation_id for p in previous)})
        if len(ids) > 50:
            raise CaseLimitExceeded()
        records = {}
        for offset in range(0, len(ids), 20):
            for memory in self.memory(case).retrieve_related_memories(
                MemoryQuery(subject_ids=ids[offset : offset + 20], max_hops=2, limit=100)
            ):
                records[memory.id] = memory
        if len(records) > 100:
            raise CaseLimitExceeded()
        episodic = sorted(m.id for m in records.values() if m.memory_type == MemoryType.EPISODIC)
        procedural = sorted(
            m.id for m in records.values() if m.memory_type == MemoryType.PROCEDURAL
        )
        checks = []
        # Current verified, successful, conflict-free procedure representations only.
        for memory in records.values():
            if (
                memory.memory_type == MemoryType.PROCEDURAL
                and memory.verification_status == "verified"
                and not memory.conflicting_memory_ids
                and memory.procedure
                and memory.procedure.outcome in {"successful", "lesson"}
            ):
                checks.append(
                    HistoricalCheck(
                        memory_id=memory.id,
                        steps=memory.procedure.steps,
                        evidence_ids=[e["record"]["id"] for e in memory.evidence],
                    )
                )
        checks.sort(key=lambda c: c.memory_id)
        statements = [
            TraceStatement(
                text=f"Connected {len(assets)} IAM/resource assets from graph evidence.",
                record_ids=assets,
            ),
            TraceStatement(
                text=f"Found {len(previous)} earlier investigations sharing those assets.",
                record_ids=[p.investigation_id for p in previous],
            ),
            TraceStatement(
                text=f"Retrieved {len(episodic)} episodic and "
                f"{len(procedural)} procedural memories.",
                record_ids=episodic + procedural,
            ),
            TraceStatement(
                text=f"Identified {len(checks)} validated read-only historical check plans.",
                record_ids=[c.memory_id for c in checks],
            ),
        ]
        return Reconstruction(
            connected_asset_ids=assets,
            previous_incidents=previous,
            episodic_memory_ids=episodic,
            procedural_memory_ids=procedural,
            useful_historical_checks=checks,
            summary=statements,
            limitations=[
                "Asset overlap does not prove common cause or attacker identity.",
                "Use authorized current memories; unverified procedures remain observations.",
                "Historical paths are potential access, not proven effective AWS authorization.",
            ],
        )

    def _evidence(self, nodes):
        reviews = {}
        for audit in self._audits(nodes):
            data = json.loads(audit.get("audit_json", "{}"))
            if audit["action"] == "case.check" and data.get("check") == CaseCheck.VERIFY_EVIDENCE:
                for identifier in data["result"].get("verified_evidence_ids", []):
                    reviews[identifier] = (
                        "verified",
                        "source_integrity",
                        "supporting",
                        audit["id"],
                    )
            if audit["action"] == "case.evidence_review":
                reviews[data["evidence_id"]] = (
                    data["verification_status"],
                    "analyst_assessment",
                    data["polarity"],
                    audit["id"],
                )
        result = []
        for node in sorted(nodes.values(), key=lambda n: n["id"]):
            if node["kind"] != "Evidence":
                continue
            status, scope, polarity, review_id = reviews.get(
                node["id"], ("unverified", "unreviewed", "supporting", None)
            )
            record = json.loads(node["record_json"]) if node.get("record_json") else None
            result.append(
                EvidenceView(
                    id=node["id"],
                    content_sha256=node["content_sha256"],
                    provenance={
                        k: node[k]
                        for k in ("source", "source_id", "source_uri", "collector", "observed_at")
                    },
                    source_record=record,
                    verification_status=status,
                    verification_scope=scope,
                    polarity=polarity,
                    review_decision_id=review_id,
                )
            )
        return result

    def review_evidence(self, identifier, request: ReviewCaseEvidence):
        request = ReviewCaseEvidence.model_validate(request.model_dump())

        def build(case, mutation):
            if case["status"] in {"RESOLVED", "CLOSED"}:
                raise InvalidCaseTransition()
            node = self._nodes(identifier).get(request.evidence_id)
            if not node or node["kind"] != "Evidence":
                raise GraphNotFound()
            if request.polarity == "contradicting":
                mutation.bind(node, "evidence")
                mutation.edge(node, case, R.CONTRADICTS, "evidence", "investigation")
            return request.model_dump(mode="json"), {"case_id": identifier}, {}

        self._mutate(
            identifier,
            "evidence_review",
            request.request_id,
            request.model_dump(mode="json"),
            build,
            expected=request.expected_revision,
            reviewer=True,
        )
        return self.evidence(identifier, request.evidence_id)

    def review_hypothesis(self, identifier, request: ReviewHypothesis):
        request = ReviewHypothesis.model_validate(request.model_dump())

        def build(case, mutation):
            nodes = self._nodes(identifier)
            if (
                case["status"] in {"RESOLVED", "CLOSED"}
                or nodes.get(request.hypothesis_id, {}).get("action") != "case.hypothesis"
            ):
                raise InvalidCaseTransition()
            verified = {e.id for e in self._evidence(nodes) if e.verification_status == "verified"}
            if not set(request.evidence_ids) <= verified:
                raise InvalidCaseTransition()
            mutation.bind(nodes[request.hypothesis_id], "hypothesis")
            return request.model_dump(mode="json"), {"case_id": identifier}, {}

        self._mutate(
            identifier,
            "hypothesis_review",
            request.request_id,
            request.model_dump(mode="json"),
            build,
            expected=request.expected_revision,
            reviewer=True,
        )
        return self.detail(identifier).current_hypotheses

    def transition(self, identifier, request: TransitionCase):
        request = TransitionCase.model_validate(request.model_dump())

        def build(case, mutation):
            current = InvestigationStatus(case["status"])
            if self.actor not in {
                case["owner_agent_id"],
                self.repository.agent_id(AgentKey.COORDINATOR),
            }:
                raise CaseAccessDenied()
            if request.status not in TRANSITIONS[current]:
                raise InvalidCaseTransition()
            nodes = self._nodes(identifier)
            if request.status == InvestigationStatus.AWAITING_REVIEW:
                if any(
                    n.get("task_type") == "investigation_check" and n["status"] != "completed"
                    for n in nodes.values()
                ):
                    raise InvalidCaseTransition()
            if request.status in {InvestigationStatus.RESOLVED, InvestigationStatus.CLOSED}:
                if self.actor != self.repository.agent_id(AgentKey.COORDINATOR):
                    raise CaseAccessDenied()
            if request.status == InvestigationStatus.RESOLVED:
                evidence = self._evidence(nodes)
                if (
                    not request.resolution
                    or not any(e.verification_status == "verified" for e in evidence)
                    or any(
                        n["kind"] == "Task"
                        and n.get("task_type") != "remediation_review"
                        and n["status"] != "completed"
                        for n in nodes.values()
                    )
                ):
                    raise InvalidCaseTransition()
                if request.resolution == "false_positive":
                    if not any(
                        e.polarity == "contradicting" and e.verification_status == "verified"
                        for e in evidence
                    ):
                        raise InvalidCaseTransition()
                for index, node in enumerate(
                    n
                    for n in nodes.values()
                    if n["kind"] == "Task"
                    and n.get("task_type") == "remediation_review"
                    and n["status"] == "awaiting_review"
                ):
                    mutation.bind(node, "review" + str(index))
                    mutation.changes.append(
                        f"SET review{index}.status = 'completed', review{index}.updated_at = $now "
                    )
            elif request.resolution is not None:
                raise InvalidCaseTransition()
            return (
                {"before": current.value, **request.model_dump(mode="json")},
                {"case_id": identifier, "status": request.status.value},
                {
                    "status": request.status.value,
                    **(
                        {"resolution": request.resolution}
                        if request.resolution
                        else {"resolution": ""}
                        if request.status == "INVESTIGATING"
                        else {}
                    ),
                },
            )

        self._mutate(
            identifier,
            "transition",
            request.request_id,
            request.model_dump(mode="json"),
            build,
            expected=request.expected_revision,
        )
        return self.repository.case(identifier)

    def _tasks(self, identifier):
        return sorted(
            [
                n
                for n in self._nodes(identifier).values()
                if n["kind"] == "Task" and n.get("task_type") == "investigation_check"
            ],
            key=lambda n: n["sequence"],
        )

    def recommended_response(self, identifier):
        nodes = self._nodes(identifier)
        proposals = {}
        for task in nodes.values():
            if task["kind"] == "Task" and task.get("task_type") == "remediation_review":
                value = json.loads(task.get("output_json", "null"))
                if value and value.get("simulation_only") is True:
                    proposals[value["relationship_id"]] = value
        if proposals:
            return sorted(proposals.values(), key=lambda p: p["relationship_id"])
        findings = [
            n for n in nodes.values() if n["kind"] == "RiskFinding" and n.get("analysis_id")
        ]
        if not findings:
            return []
        latest = max(findings, key=lambda f: (f["assessed_at"], f["id"]))
        risk = RiskRepository(self.repository.database, self.repository.scope)
        bundle = risk.evidence(latest["id"])
        finding, analysis = bundle.finding, bundle.analysis
        rows = self.repository.read(
            "MATCH (a:Entity)-[r:HAS_POLICY]->(b:Entity) "
            "WHERE r.id IN $ids AND a.tenant_id = $tenant_id AND b.tenant_id = $tenant_id "
            "AND r.tenant_id = $tenant_id RETURN r.id ORDER BY r.id LIMIT 1",
            {"ids": finding.relationship_ids},
        )
        if not rows:
            return []
        simulation = RiskEngine(risk).what_if(
            WhatIfRequest(
                at=analysis.at, scenario=analysis.scenario, remove_relationship_ids=[rows[0][0]]
            )
        )
        return [
            {
                "action": "propose_relationship_removal",
                "relationship_id": rows[0][0],
                "finding_ids": [finding.finding_id],
                "simulation_only": True,
                "execution_allowed": False,
                "before_paths": len(simulation.before.findings),
                "after_paths": len(simulation.after.findings),
                "removed_path_ids": simulation.removed_path_ids,
            }
        ]

    def check(self, identifier, check: CaseCheck):
        case = self.require(identifier, owner=True)
        tasks = self._tasks(identifier)
        task = next(t for t in tasks if t["check_key"] == check)
        if task["status"] == "completed":
            return json.loads(task["output_json"])
        if case["status"] != "INVESTIGATING" or any(
            t["status"] != "completed" for t in tasks if t["sequence"] < task["sequence"]
        ):
            raise InvalidCaseTransition()
        if check == CaseCheck.RECONSTRUCT_HISTORY:
            result = self.reconstruct(identifier).model_dump(mode="json")
        elif check == CaseCheck.VERIFY_EVIDENCE:
            verified, uncheckable = [], []
            for evidence in self._evidence(self._nodes(identifier)):
                if (
                    evidence.source_record is not None
                    and fingerprint(evidence.source_record) == evidence.content_sha256
                ):
                    verified.append(evidence.id)
                else:
                    uncheckable.append(evidence.id)
            result = {
                "verified_evidence_ids": verified,
                "uncheckable_evidence_ids": uncheckable,
                "scope": "source_integrity",
                "proves_effective_aws_access": False,
            }
        elif check == CaseCheck.REVIEW_CONFLICTS:
            subjects = [identifier, *self.repository.incident_assets(identifier)]
            conflicts = self.memory(case).detect_memory_conflicts(
                MemoryQuery(subject_ids=subjects, max_hops=2, limit=100)
            )
            result = {
                "conflicts": [c.model_dump(mode="json") for c in conflicts],
                "requires_analyst_review": bool(conflicts),
            }
        else:
            result = {
                "proposals": self.recommended_response(identifier),
                "execution_allowed": False,
            }

        def build(current, mutation):
            if current["status"] != "INVESTIGATING" or current["owner_agent_id"] != self.actor:
                raise InvalidCaseTransition()
            mutation.bind(task, "task")
            mutation.params["task_output"] = json.dumps(result, sort_keys=True)
            mutation.changes.append(
                "SET task.status = 'completed', task.output_json = $task_output, "
                "task.updated_at = $now "
            )
            if check == CaseCheck.RECONSTRUCT_HISTORY:
                for index, previous in enumerate(result["previous_incidents"]):
                    node = self.repository.node(previous["investigation_id"])
                    mutation.bind(node, "prior" + str(index))
                    mutation.edge(
                        current, node, R.INVESTIGATES, "investigation", "prior" + str(index)
                    )
            return {"check": check.value, "task_id": task["id"], "result": result}, result, {}

        return self._mutate(
            identifier,
            "check",
            check.value,
            {"check": check.value},
            build,
            expected=case["revision"],
            owner=True,
        )

    def resume_checks(self, identifier, limit=4):
        self.require(identifier, owner=True)
        for _ in range(min(limit, 4)):
            case = self.require(identifier, owner=True)
            if case["status"] in {"NEW", "TRIAGING"}:
                target = "TRIAGING" if case["status"] == "NEW" else "INVESTIGATING"
                self.transition(
                    identifier,
                    TransitionCase(
                        request_id=f"auto/{target}/{case['revision']}",
                        expected_revision=case["revision"],
                        status=target,
                        reason="Begin graph-grounded investigation",
                    ),
                )
            case = self.repository.case(identifier)
            if case["status"] == "TRIAGING":
                self.transition(
                    identifier,
                    TransitionCase(
                        request_id=f"auto/INVESTIGATING/{case['revision']}",
                        expected_revision=case["revision"],
                        status="INVESTIGATING",
                        reason="Triage admitted the case",
                    ),
                )
            if self.repository.case(identifier)["status"] != "INVESTIGATING":
                break
            pending = [t for t in self._tasks(identifier) if t["status"] != "completed"]
            if not pending:
                break
            self.check(identifier, CaseCheck(pending[0]["check_key"]))
        case = self.repository.case(identifier)
        if case["status"] == "INVESTIGATING" and all(
            t["status"] == "completed" for t in self._tasks(identifier)
        ):
            self.transition(
                identifier,
                TransitionCase(
                    request_id=f"auto/AWAITING_REVIEW/{case['revision']}",
                    expected_revision=case["revision"],
                    status="AWAITING_REVIEW",
                    reason="All read-only checks are complete; analyst review remains required",
                ),
            )
        return self.repository.case(identifier)

    def transfer(self, identifier, request: TransferCase):
        request = TransferCase.model_validate(request.model_dump())

        def build(case, mutation):
            if self.actor not in {
                case["owner_agent_id"],
                self.repository.agent_id(AgentKey.COORDINATOR),
            }:
                raise CaseAccessDenied()
            if (
                case["status"] in {"RESOLVED", "CLOSED"}
                or request.recipient_agent_id == case["owner_agent_id"]
            ):
                raise InvalidCaseTransition()
            recipient = InvestigationService(self.repository, request.recipient_agent_id)
            recipient.require(identifier)
            if recipient.memory(case).repository.access()["role"] not in {"writer", "curator"}:
                raise CaseAccessDenied()
            nodes = self._nodes(identifier)
            tasks = [
                n
                for n in nodes.values()
                if n["kind"] == "Task" and n.get("task_type") == "investigation_check"
            ]
            checkpoint = {
                "case_id": identifier,
                "revision": case["revision"],
                "completed_task_ids": sorted(t["id"] for t in tasks if t["status"] == "completed"),
                "pending_task_ids": sorted(t["id"] for t in tasks if t["status"] != "completed"),
                "evidence_ids": sorted(n["id"] for n in nodes.values() if n["kind"] == "Evidence"),
                "decision_ids": sorted(n["id"] for n in nodes.values() if n["kind"] == "Decision"),
                "memory_ids": sorted(n["id"] for n in nodes.values() if n["kind"] == "AgentMemory"),
            }
            key = self.repository.operation_key(
                identifier, "handoff", request.request_id, self.actor
            )
            handoff = mutation.node(
                N.HANDOFF,
                key + "/handoff",
                {
                    "reason": request.reason,
                    "status": "available",
                    "case_id": identifier,
                    "space_id": case["space_id"],
                    "sender_agent_id": case["owner_agent_id"],
                    "recipient_agent_id": request.recipient_agent_id,
                    "context_sha256": fingerprint(checkpoint),
                    "context_json": json.dumps(checkpoint, sort_keys=True),
                },
                "handoff",
            )
            mutation.edge(handoff, case, R.BELONGS_TO, "handoff", "investigation")
            sender = self.repository.node(case["owner_agent_id"])
            target = self.repository.node(request.recipient_agent_id)
            mutation.params["transfer_recipient"] = target["id"]
            mutation.bind(sender, "sender")
            mutation.bind(target, "recipient")
            mutation.edge(sender, handoff, R.HANDS_OFF_TO, "sender", "handoff")
            mutation.edge(handoff, target, R.HANDS_OFF_TO, "handoff", "recipient")
            for index, task in enumerate(t for t in tasks if t["status"] != "completed"):
                variable = "pending" + str(index)
                mutation.bind(task, variable)
                mutation.params["new_owner"] = target["id"]
                mutation.changes.append(
                    f"SET {variable}.agent_id = $new_owner, {variable}.updated_at = $now "
                )
                mutation.matches.append(
                    f"MATCH ({variable})-[assignment{index}:ASSIGNED_TO]->(sender) "
                    f"WHERE assignment{index}.tenant_id = $tenant_id "
                    f"AND assignment{index}.valid_from <= $auth_now "
                    f"AND (assignment{index}.valid_to IS NULL "
                    f"OR $auth_now < assignment{index}.valid_to) "
                )
                mutation.changes.append(
                    f"SET assignment{index}.valid_to = $now, assignment{index}.updated_at = $now "
                )
                mutation.edge(task, target, R.ASSIGNED_TO, variable, "recipient")
            return (
                {
                    "sender": sender["id"],
                    "recipient": target["id"],
                    "checkpoint": checkpoint,
                    "reason": request.reason,
                },
                {"handoff_id": handoff["id"]},
                {"owner_agent_id": target["id"]},
            )

        result = self._mutate(
            identifier,
            "handoff",
            request.request_id,
            request.model_dump(mode="json"),
            build,
            expected=request.expected_revision,
        )
        return self.handoff(identifier, result["handoff_id"])

    def handoff(self, identifier, handoff_id):
        case = self.require(identifier)
        node = self._nodes(identifier).get(handoff_id)
        if not node or node["kind"] != "Handoff" or not node.get("context_json"):
            raise GraphNotFound()
        checkpoint = json.loads(node["context_json"])
        if fingerprint(checkpoint) != node["context_sha256"]:
            raise InvalidCaseTransition()
        return CaseHandoff(
            handoff_id=handoff_id,
            investigation_id=identifier,
            sender_agent_id=node["sender_agent_id"],
            recipient_agent_id=node["recipient_agent_id"],
            checkpoint=checkpoint,
            current_owner_agent_id=case["owner_agent_id"],
        )

    def memories(self, identifier):
        case = self.require(identifier)
        return self.memory(case).retrieve_related_memories(
            MemoryQuery(
                subject_ids=[identifier, *self.repository.incident_assets(identifier)],
                max_hops=2,
                limit=100,
            )
        )

    def evidence(self, identifier, evidence_id):
        self.require(identifier)
        result = next(
            (e for e in self._evidence(self._nodes(identifier)) if e.id == evidence_id), None
        )
        if result is None:
            raise GraphNotFound()
        return result

    def detail(self, identifier):
        case = self.require(identifier)
        nodes = self._nodes(identifier)
        audits = self._audits(nodes)
        evidence = self._evidence(nodes)
        history = self.reconstruct(identifier)
        relevant_memories = self.memories(identifier)
        contributing = {
            event_id
            for audit in audits
            if audit["action"] == "case.detection"
            for event_id in json.loads(audit["audit_json"]).get("contributing_event_ids", [])
        }
        timeline, events = {}, {}
        for item in evidence:
            record = item.source_record
            if not record:
                continue
            if record.get("kind") == "SecurityEvent":
                if record["id"] in contributing:
                    events[record["id"]] = record
                timeline[record["id"]] = TimelineEntry(
                    id=record["id"],
                    timestamp=from_epoch_us(record["event_time"]),
                    category="security_event",
                    title=record["event_type"],
                    record_ids=[record["id"], item.id],
                )
            elif record.get("kind") in {"CAN_ASSUME", "HAS_POLICY", "GRANTS", "TARGETS"}:
                timeline[record["id"]] = TimelineEntry(
                    id=record["id"],
                    timestamp=from_epoch_us(record["valid_from"]),
                    category="relationship_change",
                    title=record["kind"],
                    record_ids=[
                        record["id"],
                        item.id,
                        *([record["source_event_id"]] if record.get("source_event_id") else []),
                    ],
                )
        audit_models = []
        hypothesis_reviews = {}
        for audit in audits:
            details = json.loads(audit.get("audit_json", "{}"))
            audit_models.append(
                AuditEntry(
                    id=audit["id"],
                    action=audit["action"],
                    actor_agent_id=audit["actor_agent_id"],
                    created_at=from_epoch_us(audit["created_at"]),
                    details=details,
                )
            )
            timeline[audit["id"]] = TimelineEntry(
                id=audit["id"],
                timestamp=from_epoch_us(audit["created_at"]),
                category="audit",
                time_basis="processing",
                title=audit["action"],
                record_ids=[audit["id"], audit["actor_agent_id"]],
            )
            if audit["action"] == "case.hypothesis_review":
                hypothesis_reviews[details["hypothesis_id"]] = (details, audit["id"])
        hypotheses = []
        for node in nodes.values():
            if node.get("action") == "case.hypothesis":
                original = json.loads(node["audit_json"])
                reviewed, review_id = hypothesis_reviews.get(node["id"], ({}, None))
                hypotheses.append(
                    HypothesisView(
                        id=node["id"],
                        statement=node["rationale"],
                        conclusion=reviewed.get("conclusion", "unresolved"),
                        evidence_ids=reviewed.get("evidence_ids", original["evidence_ids"]),
                        review_decision_id=review_id,
                    )
                )
        tasks = [
            CaseTask(
                id=n["id"],
                title=n["title"],
                status=n["status"],
                task_type=n["task_type"],
                agent_id=n["agent_id"],
                check_key=n.get("check_key"),
                output=json.loads(n["output_json"])
                if n.get("output_json", "null") != "null"
                else None,
            )
            for n in sorted(nodes.values(), key=lambda n: n["id"])
            if n["kind"] == "Task"
        ]
        for memory in relevant_memories:
            timeline[memory.id] = TimelineEntry(
                id=memory.id,
                timestamp=memory.provenance.observed_at,
                category="memory_observation",
                title=memory.memory_type.value,
                record_ids=[memory.id, *[e["record"]["id"] for e in memory.evidence]],
            )
        return CaseDetail(
            investigation_id=identifier,
            severity=case["severity"],
            status=case["status"],
            revision=case["revision"],
            owner_agent_id=case["owner_agent_id"],
            affected_identities=[n for n in nodes.values() if n["kind"] in {"Identity", "Role"}],
            affected_resources=[n for n in nodes.values() if n["kind"] == "Resource"],
            historical_change_timeline=sorted(timeline.values(), key=lambda t: (t.timestamp, t.id)),
            contributing_security_events=sorted(
                events.values(), key=lambda n: (n["event_time"], n["id"])
            ),
            current_hypotheses=sorted(hypotheses, key=lambda h: h.id),
            verified_evidence=[e for e in evidence if e.verification_status == "verified"],
            contradictory_evidence=[e for e in evidence if e.polarity == "contradicting"],
            unverified_evidence=[e for e in evidence if e.verification_status != "verified"],
            linked_previous_incidents=history.previous_incidents,
            assigned_agents=sorted(
                [n for n in nodes.values() if n["kind"] == "Agent"], key=lambda n: n["id"]
            ),
            completed_tasks=[t for t in tasks if t.status == "completed"],
            pending_tasks=[t for t in tasks if t.status != "completed"],
            memory_references=sorted(m.id for m in relevant_memories),
            recommended_response=self.recommended_response(identifier),
            audit_history=audit_models,
            reconstruction=history,
            workflow_ids=sorted(
                n["id"]
                for n in nodes.values()
                if n.get("workflow_version") and n["kind"] == "Investigation"
            ),
            created_at=from_epoch_us(case["created_at"]),
            updated_at=from_epoch_us(case["updated_at"]),
            resolution=case.get("resolution") or None,
            limitations=[
                "Source integrity does not prove effective AWS access or malicious intent.",
                "No AWS changes or privileged actions are executed.",
                *history.limitations,
            ],
        )

    def graph(self, identifier):
        case = self.require(identifier)
        links = self.repository.linked(identifier)
        nodes = {case["id"]: case, **{n["id"]: n for n, _ in links}}
        relationships = {r["id"]: r for _, r in links}
        memory_ids = sorted(n["id"] for n in nodes.values() if n["kind"] == "AgentMemory")
        allowed_memories = set()
        for offset in range(0, len(memory_ids), 50):
            allowed_memories.update(
                m.id
                for m in self.memory(case).retrieve_related_memories(
                    MemoryQuery(
                        memory_ids=memory_ids[offset : offset + 50],
                        include_superseded=True,
                        limit=100,
                    )
                )
            )
        hidden = set(memory_ids) - allowed_memories
        nodes = {key: value for key, value in nodes.items() if key not in hidden}
        relationships = {
            key: edge
            for key, edge in relationships.items()
            if edge["source_node_id"] not in hidden and edge["target_node_id"] not in hidden
        }
        for item in self._evidence(nodes):
            record = item.source_record
            if not record or "id" not in record:
                continue
            if "source_node_id" in record:
                relationships.setdefault(
                    record["id"],
                    {**record, "archived_snapshot": True, "snapshot_evidence_id": item.id},
                )
            else:
                nodes.setdefault(
                    record["id"],
                    {**record, "archived_snapshot": True, "snapshot_evidence_id": item.id},
                )
        missing = sorted(
            {
                endpoint
                for edge in relationships.values()
                for endpoint in (edge["source_node_id"], edge["target_node_id"])
                if endpoint not in nodes
            }
        )
        if len(nodes) > 1000 or len(relationships) > 2000:
            raise CaseLimitExceeded()
        return CaseGraph(
            investigation_id=identifier,
            nodes=[
                {"id": n["id"], "kind": n["kind"], "properties": n}
                for n in sorted(nodes.values(), key=lambda n: n["id"])
            ],
            relationships=[
                {
                    "id": e["id"],
                    "kind": e["kind"],
                    "source": e["source_node_id"],
                    "target": e["target_node_id"],
                    "properties": e,
                }
                for e in sorted(relationships.values(), key=lambda e: e["id"])
            ],
            missing_record_ids=missing,
        )


def synchronize_investigations(workflow_repository, workflow_id, stage, published_memory_id=None):
    """Recoverable stage hook: prepared outputs make all its side effects replayable."""
    repo = InvestigationRepository(
        workflow_repository.database, workflow_repository.scope, workflow_repository.settings
    )
    coordinator = InvestigationService(repo, repo.agent_id(AgentKey.COORDINATOR))
    workflow = repo.raw(workflow_id)
    oracle = repo.run(workflow_id, AgentKey.RISK_ORACLE)
    if oracle["output_json"] == "null":
        return []
    output = AgentOutput.model_validate_json(oracle["output_json"])
    risk = RiskRepository(repo.database, repo.scope)
    analysis = risk.saved(output.analysis_id)
    if len(analysis.findings) > 100:
        raise CaseLimitExceeded()
    cases = []
    for finding in analysis.findings:
        evidence = risk.evidence(finding.finding_id).evidence
        identifier = repo.observe(finding, analysis, workflow["space_id"], evidence, workflow_id)
        cases.append(identifier)
        case = repo.case(identifier)
        coordinator._grant(case, [identifier])
        coordinator.synchronize(
            identifier,
            workflow_id,
            stage.value + ("/published" if published_memory_id else "/prepared"),
            published_memory_id,
        )
        if published_memory_id:
            continue
        if case["status"] in {"RESOLVED", "CLOSED"}:
            continue
        if stage == AgentKey.MEMORY_KEEPER:
            keeper = InvestigationService(repo, repo.agent_id(AgentKey.MEMORY_KEEPER))
            completed = sum(t["status"] == "completed" for t in keeper._tasks(identifier))
            if completed < 2:
                keeper.resume_checks(identifier, limit=2 - completed)
        elif stage == AgentKey.COORDINATOR:
            case = repo.case(identifier)
            if case["owner_agent_id"] != repo.agent_id(AgentKey.RESPONSE_ARCHITECT):
                coordinator.transfer(
                    identifier,
                    TransferCase(
                        request_id="auto/" + workflow_id,
                        expected_revision=case["revision"],
                        recipient_agent_id=repo.agent_id(AgentKey.RESPONSE_ARCHITECT),
                        reason="Continue from graph evidence and completed investigation checks",
                    ),
                )
        elif stage == AgentKey.RESPONSE_ARCHITECT:
            InvestigationService(repo, repo.agent_id(AgentKey.RESPONSE_ARCHITECT)).resume_checks(
                identifier, limit=4
            )
    return cases

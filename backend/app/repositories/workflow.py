"""FalkorDB task ownership, fenced step leases and durable LangGraph reconstruction."""

import json
from uuid import uuid4

from app.core.config import Settings
from app.core.time import utc_now
from app.db.cypher import fingerprint, stable_id
from app.db.errors import GraphConflict, GraphNotFound
from app.models.graph import NodeInput, NodeRef, Provenance, TenantScope, epoch_us, from_epoch_us
from app.models.graph import NodeKind as N
from app.models.graph import RelationshipKind as R
from app.models.workflow import (
    AGENT_NAMES,
    STAGES,
    WORKFLOW_SOURCE,
    WORKFLOW_VERSION,
    AgentKey,
    AgentOutput,
    AgentRunRecord,
    InvestigationRecord,
    TaskRecord,
    TriggerInvestigation,
)
from app.repositories.graph import GraphRepository
from app.repositories.memory import MemoryMutation
from app.services.workflow_errors import WorkflowBlocked, WorkflowBusy, WorkflowSourceChanged


def workflow_id(tenant: str, kind: N, key: str) -> str:
    return stable_id(tenant, kind.value, "", WORKFLOW_SOURCE, key)


class WorkflowMutation(MemoryMutation):
    def provenance(self, key: str) -> Provenance:
        return Provenance(
            source=WORKFLOW_SOURCE,
            source_id=key,
            observed_at=from_epoch_us(self.now),
            collector=WORKFLOW_VERSION,
            source_uri="urn:shadowchain:agents:" + key,
        )


class WorkflowRepository:
    def __init__(self, database, scope: TenantScope, settings: Settings):
        self.database, self.scope, self.settings = database, scope, settings
        self.graph = GraphRepository(database, scope)

    def write(self, query, params, *, idempotent=True):
        refs = [params[k] for k in ("id", "investigation_id", "agent_id") if params.get(k)]
        return self.database.mutate(
            query,
            params,
            tenant_id=self.scope.tenant_id,
            event_type="agent.workflow.changed",
            entity_ids=refs,
            idempotent=idempotent,
        )

    def params(self, values=None):
        return {**(values or {}), "tenant_id": self.scope.tenant_id}

    def read(self, query, values=None):
        return self.database.query(query, self.params(values), read_only=True)

    def agent_id(self, stage: AgentKey) -> str:
        return workflow_id(self.scope.tenant_id, N.AGENT, "agent/" + stage.value)

    def agents(self) -> list[dict]:
        return [
            row[0]
            for row in self.read(
                "MATCH (a:Entity:Agent {tenant_id: $tenant_id, source: $source}) "
                "RETURN properties(a) ORDER BY a.agent_type",
                {"source": WORKFLOW_SOURCE},
            )
        ]

    def register_agents(self):
        for stage in STAGES:
            expected = {
                "name": AGENT_NAMES[stage],
                "agent_type": stage.value,
                "version": WORKFLOW_VERSION,
            }
            existing = self.graph.get_node(NodeRef(kind=N.AGENT, id=self.agent_id(stage)))
            if existing is not None:
                if existing.attributes != expected:
                    raise GraphConflict()
                continue
            self.graph.upsert_node(
                NodeInput(
                    kind=N.AGENT,
                    provenance=WorkflowMutation(
                        self.scope.tenant_id, "registry", epoch_us(utc_now())
                    ).provenance("agent/" + stage.value),
                    attributes=expected,
                )
            )

    def node(self, identifier: str) -> dict:
        rows = self.read(
            "MATCH (n:Entity {tenant_id: $tenant_id, id: $id}) RETURN properties(n)",
            {"id": identifier},
        )
        if not rows:
            raise GraphNotFound()
        return rows[0][0]

    def raw(self, investigation_id: str) -> dict:
        rows = self.read(
            "MATCH (i:Entity:Investigation:WorkflowInvestigation "
            "{tenant_id: $tenant_id, id: $id}) RETURN properties(i)",
            {"id": investigation_id},
        )
        if not rows:
            raise GraphNotFound()
        return rows[0][0]

    def run(self, investigation_id: str, stage: AgentKey) -> dict:
        rows = self.read(
            "MATCH (r:Entity:AgentRun:WorkflowRun {tenant_id: $tenant_id, "
            "investigation_id: $id, stage: $stage}) RETURN properties(r)",
            {"id": investigation_id, "stage": stage.value},
        )
        if not rows:
            raise GraphNotFound()
        return rows[0][0]

    def create(self, request: TriggerInvestigation, space_id: str) -> str:
        # Called by the trusted Coordinator policy; all six owners/dependencies are fixed code.
        request = TriggerInvestigation.model_validate(request.model_dump())
        key = "investigation/" + request.request_id
        identifier = workflow_id(self.scope.tenant_id, N.INVESTIGATION, key)
        digest = fingerprint(request.model_dump(mode="json"))
        try:
            existing = self.raw(identifier)
        except GraphNotFound:
            existing = None
        if existing:
            if existing["request_sha256"] != digest:
                raise GraphConflict()
            return identifier
        inputs = sorted(set([request.event_id, *request.evidence_ids]))
        sources = [self.node(i) for i in inputs]
        event = next(s for s in sources if s["id"] == request.event_id)
        if event["kind"] != "SecurityEvent" or any(
            s["kind"] not in {"Evidence", "SecurityEvent"} for s in sources
        ):
            raise WorkflowSourceChanged()
        if request.at and epoch_us(request.at) < event["event_time"]:
            raise WorkflowSourceChanged()
        now = epoch_us(utc_now())
        mutation = WorkflowMutation(self.scope.tenant_id, identifier, now)
        for index, source in enumerate(sources):
            mutation.bind(source, "s" + str(index))
        investigation = mutation.node(
            N.INVESTIGATION,
            key,
            {
                "title": "Evidence-grounded security event investigation",
                "status": "pending",
                "summary": "Bounded six-agent workflow; proposed actions are non-executable",
                "request_sha256": digest,
                "request_json": json.dumps(
                    {
                        "request": request.model_dump(mode="json"),
                        "source_fingerprints": {s["id"]: s["fingerprint"] for s in sources},
                        "at": request.at.isoformat()
                        if request.at
                        else from_epoch_us(event["event_time"]).isoformat(),
                        "scenario": event.get("scenario_id")
                        if event.get("scenario_id") in {"A", "B", "C"}
                        else None,
                    }
                ),
                "space_id": space_id,
                "workflow_version": WORKFLOW_VERSION,
                "sequence": 0,
                "revision": 0,
                "lock_version": 0,
                "lease_owner": "",
                "lease_expires_at": 0,
                "provider_mode": self.settings.agent_provider,
            },
            "investigation",
        )
        mutation.changes.append("SET investigation:WorkflowInvestigation ")
        previous = None
        task_ids = [
            workflow_id(self.scope.tenant_id, N.TASK, identifier + "/" + s.value) for s in STAGES
        ]
        for index, stage in enumerate(STAGES):
            agent = self.node(self.agent_id(stage))
            mutation.bind(agent, "a" + str(index))
            run = mutation.node(
                N.AGENT_RUN,
                identifier + "/" + stage.value,
                {
                    "investigation_id": identifier,
                    "agent_id": agent["id"],
                    "stage": stage.value,
                    "status": "pending",
                    "sequence": index,
                    "attempt_count": 0,
                    "input_evidence_ids_json": json.dumps(inputs),
                    "retrieved_memory_ids_json": "[]",
                    "output_json": "null",
                    "pending_task_ids_json": json.dumps(task_ids[index:]),
                    "handoff_target": self.agent_id(
                        STAGES[index + 1] if index < 5 else AgentKey.COORDINATOR
                    ),
                    "workflow_version": WORKFLOW_VERSION,
                    "operation_id": "",
                    "error_code": "",
                },
                "run" + str(index),
            )
            mutation.changes.append(f"SET run{index}:WorkflowRun ")
            task = mutation.node(
                N.TASK,
                identifier + "/" + stage.value,
                {
                    "title": AGENT_NAMES[stage] + " investigation step",
                    "status": "pending",
                    "investigation_id": identifier,
                    "agent_id": agent["id"],
                    "stage": stage.value,
                    "sequence": index,
                    "workflow_version": WORKFLOW_VERSION,
                    "task_type": "workflow_step",
                },
                "task" + str(index),
            )
            mutation.changes.append(f"SET task{index}:WorkflowTask ")
            mutation.edge(run, investigation, R.BELONGS_TO, "run" + str(index), "investigation")
            mutation.edge(run, agent, R.BELONGS_TO, "run" + str(index), "a" + str(index))
            mutation.edge(task, investigation, R.BELONGS_TO, "task" + str(index), "investigation")
            mutation.edge(task, agent, R.ASSIGNED_TO, "task" + str(index), "a" + str(index))
            if previous:
                mutation.edge(
                    task, previous, R.DEPENDS_ON, "task" + str(index), "task" + str(index - 1)
                )
            previous = task
        for index, source in enumerate(sources):
            if source["kind"] == "Evidence":
                mutation.edge(source, investigation, R.SUPPORTS, "s" + str(index), "investigation")
            else:
                mutation.edge(
                    investigation, source, R.INVESTIGATES, "investigation", "s" + str(index)
                )
        try:
            self.write(
                "".join(mutation.matches + mutation.changes) + "RETURN investigation.id",
                self.params(mutation.params),
                idempotent=True,
            )
        except GraphConflict:
            if self.raw(identifier)["request_sha256"] != digest:
                raise
        return identifier

    def claim(self, identifier: str, stage: AgentKey) -> str:
        now, token = epoch_us(utc_now()), str(uuid4())
        rows = self.write(
            "MATCH (i:Entity:WorkflowInvestigation {tenant_id: $tenant_id, id: $id}) "
            "SET i.lock_version = i.lock_version + 1 WITH i "
            "WHERE i.sequence = $sequence AND i.status <> 'completed' AND i.status <> 'blocked' "
            "AND i.lease_expires_at <= $now "
            "MATCH (r:Entity:AgentRun:WorkflowRun {tenant_id: $tenant_id, "
            "investigation_id: $id, stage: $stage}) "
            "WHERE r.attempt_count < $max_attempts "
            "MATCH (t:Entity:Task:WorkflowTask {tenant_id: $tenant_id, "
            "investigation_id: $id, stage: $stage}) "
            "OPTIONAL MATCH (t)-[dependency:DEPENDS_ON {tenant_id: $tenant_id}]->"
            "(prior:Entity:Task {tenant_id: $tenant_id}) "
            "WITH i, r, t, collect(prior.status) AS prior_states "
            "WHERE all(s IN prior_states WHERE s = 'completed') "
            "SET i.lease_owner = $token, i.lease_expires_at = $until, i.status = 'running', "
            "i.updated_at = $now, r.attempt_count = r.attempt_count + 1, "
            "r.status = CASE WHEN r.output_json = 'null' THEN 'running' ELSE 'prepared' END, "
            "r.operation_id = $token, r.updated_at = $now, "
            "r.started_at = coalesce(r.started_at, $now), r.lease_expires_at = $until, "
            "t.status = 'running', t.updated_at = $now, t.lease_expires_at = $until "
            "RETURN r.id",
            self.params(
                {
                    "id": identifier,
                    "stage": stage.value,
                    "sequence": STAGES.index(stage),
                    "now": now,
                    "token": token,
                    "until": now + self.settings.workflow_lease_seconds * 1_000_000,
                    "max_attempts": self.settings.workflow_max_attempts,
                }
            ),
            idempotent=True,
        )
        if rows:
            return token
        # A lost claim acknowledgement is reconciled by its unique lease token.
        current = self.raw(identifier)
        if current["lease_owner"] == token:
            return token
        if current["lease_expires_at"] > now:
            raise WorkflowBusy()
        if (
            current["status"] == "blocked"
            or self.run(identifier, stage)["attempt_count"] >= self.settings.workflow_max_attempts
        ):
            self.write(
                "MATCH (i:WorkflowInvestigation {tenant_id: $tenant_id, id: $id}) "
                "SET i.lock_version = i.lock_version + 1 WITH i "
                "WHERE i.lease_expires_at <= $now AND i.sequence = $sequence "
                "MATCH (r:WorkflowRun {tenant_id: $tenant_id, "
                "investigation_id: $id, stage: $stage}) WHERE r.attempt_count >= $max "
                "SET i.status = 'blocked', i.lease_owner = '', i.updated_at = $now, "
                "r.status = 'failed', r.error_code = 'attempt_limit', "
                "r.updated_at = $now RETURN i.id",
                self.params(
                    {
                        "id": identifier,
                        "stage": stage.value,
                        "sequence": STAGES.index(stage),
                        "now": now,
                        "max": self.settings.workflow_max_attempts,
                    }
                ),
                idempotent=True,
            )
            raise WorkflowBlocked()
        raise WorkflowBusy()

    def _ledger(self, mutation, identifier, stage, phase, result):
        return mutation.node(
            N.DECISION,
            identifier + "/" + stage.value + "/" + phase,
            {
                "action": "workflow." + phase,
                "status": "applied",
                "rationale": "Fenced graph transition",
                "actor_agent_id": self.agent_id(stage),
                "request_sha256": fingerprint(result),
                "response_json": json.dumps(result, sort_keys=True),
            },
            "operation",
        )

    def prepare(self, identifier, stage, token, output: AgentOutput, memories: list[str]):
        # Persist output before memory/handoff side effects, including optional model prose.
        output = AgentOutput.model_validate(output.model_dump())
        run = self.run(identifier, stage)
        if run["output_json"] != "null":
            return
        mutation = WorkflowMutation(self.scope.tenant_id, identifier, epoch_us(utc_now()))
        operation = self._ledger(
            mutation, identifier, stage, "prepare", output.model_dump(mode="json")
        )
        mutation.edge(operation, run, R.DERIVED_FROM, "operation", "r")
        for index, finding_id in enumerate(output.finding_ids):
            finding = {"id": finding_id, "kind": "RiskFinding"}
            mutation.bind(finding, "finding" + str(index))
            mutation.edge(finding, run, R.DERIVED_FROM, "finding" + str(index), "r")
        if stage == AgentKey.COORDINATOR and output.finding_ids:
            coordinator_task = {
                "id": workflow_id(self.scope.tenant_id, N.TASK, identifier + "/" + stage.value),
                "kind": "Task",
            }
            assignee = {"id": self.agent_id(AgentKey.RESPONSE_ARCHITECT), "kind": "Agent"}
            mutation.bind(coordinator_task, "coordinator_task")
            mutation.bind(assignee, "response_agent")
            mutation.bind({"id": identifier, "kind": "Investigation"}, "investigation")
            for index, finding_id in enumerate(output.finding_ids[:3]):
                task = mutation.node(
                    N.TASK,
                    identifier + "/review/" + finding_id,
                    {
                        "title": "Review evidence-backed remediation proposal",
                        "status": "awaiting_review",
                        "investigation_id": identifier,
                        "agent_id": assignee["id"],
                        "stage": AgentKey.RESPONSE_ARCHITECT.value,
                        "sequence": 6 + index,
                        "workflow_version": WORKFLOW_VERSION,
                        "task_type": "remediation_review",
                        "output_json": json.dumps({"finding_id": finding_id}),
                    },
                    "review" + str(index),
                )
                variable = "review" + str(index)
                mutation.edge(task, assignee, R.ASSIGNED_TO, variable, "response_agent")
                mutation.edge(task, coordinator_task, R.DEPENDS_ON, variable, "coordinator_task")
                mutation.edge(task, run, R.CAUSED_BY, variable, "r")
                mutation.edge(
                    task,
                    {"id": identifier, "kind": "Investigation"},
                    R.BELONGS_TO,
                    variable,
                    "investigation",
                )
        if stage == AgentKey.RESPONSE_ARCHITECT:
            for index, proposal in enumerate(output.proposals):
                for finding_id in proposal.finding_ids:
                    task_id = workflow_id(
                        self.scope.tenant_id, N.TASK, identifier + "/review/" + finding_id
                    )
                    mutation.bind({"id": task_id, "kind": "Task"}, "proposal" + str(index))
                    mutation.params["proposal_json" + str(index)] = proposal.model_dump_json()
                    mutation.changes.append(
                        f"SET proposal{index}.output_json = $proposal_json{index}, "
                        f"proposal{index}.updated_at = $now "
                    )
        query = (
            self._fence()
            + "AND r.output_json = 'null' "
            + "".join(mutation.matches + mutation.changes)
            + (
                "SET r.output_json = $output, r.output_sha256 = $digest, "
                "r.retrieved_memory_ids_json = $memories, r.status = 'prepared', "
                "r.updated_at = $now "
                "RETURN r.id"
            )
        )
        rows = self.write(
            query,
            self.params(
                {
                    **mutation.params,
                    "id": identifier,
                    "stage": stage.value,
                    "token": token,
                    "now": mutation.now,
                    "output": output.model_dump_json(),
                    "digest": fingerprint(output.model_dump(mode="json")),
                    "memories": json.dumps(memories),
                }
            ),
            idempotent=True,
        )
        if not rows and self.run(identifier, stage)["output_json"] == "null":
            raise WorkflowBusy()

    @staticmethod
    def _fence():
        return (
            "MATCH (i:Entity:WorkflowInvestigation {tenant_id: $tenant_id, id: $id}) "
            "SET i.lock_version = i.lock_version + 1 WITH i "
            "WHERE i.lease_owner = $token AND i.lease_expires_at > $now "
            "MATCH (r:Entity:AgentRun:WorkflowRun {tenant_id: $tenant_id, "
            "investigation_id: $id, stage: $stage}) "
            "WHERE r.status <> 'completed' AND i.sequence = r.sequence "
            "AND r.operation_id = $token "
        )

    def finish(self, identifier, stage, token, memory_id, handoff_id):
        mutation = WorkflowMutation(self.scope.tenant_id, identifier, epoch_us(utc_now()))
        run = self.run(identifier, stage)
        self._ledger(
            mutation,
            identifier,
            stage,
            "complete",
            {"memory_id": memory_id, "handoff_id": handoff_id},
        )
        mutation.bind({"kind": "AgentMemory", "id": memory_id}, "memory")
        mutation.bind({"kind": "Handoff", "id": handoff_id}, "handoff")
        # Link durable outputs to this run; no JSON/file is needed to find its evidence.
        mutation.edge(run, {"kind": "AgentMemory", "id": memory_id}, R.REMEMBERS, "r", "memory")
        mutation.edge({"kind": "Handoff", "id": handoff_id}, run, R.CAUSED_BY, "handoff", "r")
        query = self._fence() + (
            "AND r.status = 'prepared' "
            "MATCH (task:Entity:Task:WorkflowTask {tenant_id: $tenant_id, "
            "investigation_id: $id, stage: $stage}) "
            + "".join(mutation.matches + mutation.changes)
            + "SET r.status = 'completed', r.finished_at = $now, r.updated_at = $now, "
            "r.memory_id = $memory_id, r.handoff_id = $handoff_id, r.operation_id = $operation, "
            "r.pending_task_ids_json = $pending, r.lease_expires_at = 0, "
            "task.status = 'completed', task.updated_at = $now, task.lease_expires_at = 0, "
            "i.sequence = $next_sequence, i.revision = i.revision + 1, i.updated_at = $now, "
            "i.lease_owner = '', i.lease_expires_at = 0, "
            "i.status = $next_status RETURN i.sequence"
        )
        next_index = STAGES.index(stage) + 1
        pending = [
            workflow_id(self.scope.tenant_id, N.TASK, identifier + "/" + s.value)
            for s in STAGES[next_index:]
        ]
        pending.extend(
            row[0]
            for row in self.read(
                "MATCH (t:Entity:Task {tenant_id: $tenant_id, investigation_id: $id, "
                "task_type: 'remediation_review', status: 'awaiting_review'}) "
                "RETURN t.id ORDER BY t.id LIMIT 3",
                {"id": identifier},
            )
        )
        try:
            rows = self.write(
                query,
                self.params(
                    {
                        **mutation.params,
                        "id": identifier,
                        "stage": stage.value,
                        "token": token,
                        "now": mutation.now,
                        "memory_id": memory_id,
                        "handoff_id": handoff_id,
                        "operation": workflow_id(
                            self.scope.tenant_id,
                            N.DECISION,
                            identifier + "/" + stage.value + "/complete",
                        ),
                        "next_sequence": next_index,
                        "next_status": "completed" if next_index == 6 else "pending",
                        "pending": json.dumps(pending),
                    }
                ),
                idempotent=True,
            )
        except GraphConflict:
            rows = []
        if not rows and self.run(identifier, stage)["status"] != "completed":
            raise WorkflowBusy()

    def fail(self, identifier, stage, token, code):
        self.write(
            self._fence() + "MATCH (t:Task:WorkflowTask {tenant_id: $tenant_id, "
            "investigation_id: $id, stage: $stage}) "
            "SET i.status = CASE WHEN r.attempt_count >= $max THEN 'blocked' ELSE 'retryable' END, "
            "i.lease_owner = '', i.lease_expires_at = 0, i.updated_at = $now, "
            "r.status = 'failed', r.error_code = $code, r.updated_at = $now, "
            "r.lease_expires_at = 0, t.status = 'pending', t.lease_expires_at = 0, "
            "t.updated_at = $now RETURN i.id",
            self.params(
                {
                    "id": identifier,
                    "stage": stage.value,
                    "token": token,
                    "now": epoch_us(utc_now()),
                    "max": self.settings.workflow_max_attempts,
                    "code": code,
                }
            ),
            idempotent=True,
        )

    def incomplete(self):
        return [
            r[0]
            for r in self.read(
                "MATCH (i:Entity:WorkflowInvestigation {tenant_id: $tenant_id}) "
                "WHERE i.status IN ['pending', 'running', 'retryable'] "
                "AND i.lease_expires_at <= $now "
                "RETURN i.id ORDER BY i.created_at LIMIT $limit",
                {"now": epoch_us(utc_now()), "limit": self.settings.workflow_recovery_limit},
            )
        ]

    def inspect_run(self, identifier: str) -> AgentRunRecord:
        run = self.node(identifier)
        if run["kind"] != "AgentRun" or run.get("workflow_version") != WORKFLOW_VERSION:
            raise GraphNotFound()
        return AgentRunRecord(
            id=run["id"],
            investigation_id=run["investigation_id"],
            agent_id=run["agent_id"],
            stage=run["stage"],
            status=run["status"],
            attempt_count=run["attempt_count"],
            input_evidence_ids=json.loads(run["input_evidence_ids_json"]),
            retrieved_memory_ids=json.loads(run["retrieved_memory_ids_json"]),
            output=AgentOutput.model_validate_json(run["output_json"])
            if run["output_json"] != "null"
            else None,
            pending_task_ids=json.loads(run["pending_task_ids_json"]),
            handoff_target=run["handoff_target"],
            handoff_id=run.get("handoff_id"),
            memory_id=run.get("memory_id"),
            operation_id=run["operation_id"],
            error_code=run["error_code"],
            created_at=from_epoch_us(run["created_at"]),
            updated_at=from_epoch_us(run["updated_at"]),
        )

    def inspect(self, identifier) -> InvestigationRecord:
        investigation = self.raw(identifier)
        rows = self.read(
            "MATCH (t:Entity:Task {tenant_id: $tenant_id, investigation_id: $id}) "
            "OPTIONAL MATCH (t)-[d:DEPENDS_ON {tenant_id: $tenant_id}]->"
            "(p:Entity:Task {tenant_id: $tenant_id}) "
            "WITH t, collect(p.id) AS dependencies "
            "RETURN properties(t), dependencies ORDER BY t.sequence",
            {"id": identifier},
        )
        return InvestigationRecord(
            id=identifier,
            status=investigation["status"],
            next_stage=STAGES[investigation["sequence"]] if investigation["sequence"] < 6 else None,
            completed_steps=investigation["sequence"],
            provider_mode=investigation["provider_mode"],
            event_id=json.loads(investigation["request_json"])["request"]["event_id"],
            runs=[self.inspect_run(self.run(identifier, s)["id"]) for s in STAGES],
            tasks=[
                TaskRecord(
                    id=t["id"],
                    agent_id=t["agent_id"],
                    stage=t["stage"],
                    status=t["status"],
                    task_type=t["task_type"],
                    depends_on=deps,
                )
                for t, deps in rows
            ],
            created_at=from_epoch_us(investigation["created_at"]),
            updated_at=from_epoch_us(investigation["updated_at"]),
        )

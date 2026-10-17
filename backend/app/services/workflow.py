"""LangGraph runs a fixed DAG; every node reconstructs its authoritative state from FalkorDB."""

import json
import logging
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.security import SecurityAgents
from app.db.errors import GraphNotFound
from app.integrations.model_provider import ModelProvider, PromptFacts, model_provider
from app.models.graph import NodeKind, NodeRef, Provenance
from app.models.memory import (
    Audience,
    HandoffRequest,
    MemoryQuery,
    MemoryRole,
    MemoryType,
    MemoryWrite,
)
from app.models.workflow import (
    STAGES,
    WORKFLOW_SOURCE,
    WORKFLOW_VERSION,
    AgentKey,
    AgentOutput,
    ModelNote,
)
from app.repositories.memory import MemoryRepository
from app.repositories.risk import RiskRepository
from app.repositories.workflow import WorkflowRepository
from app.services.investigation import synchronize_investigations
from app.services.memory import MemoryService
from app.services.memory_access import MemoryAccessAdmin, node_id
from app.services.workflow_errors import WorkflowBlocked, WorkflowBusy

logger = logging.getLogger(__name__)


class WorkflowState(TypedDict):
    investigation_id: str


class InvestigationWorkflow:
    def __init__(self, repository: WorkflowRepository, provider: ModelProvider | None = None):
        self.repository = repository
        self.provider = provider or model_provider(repository.settings)
        self.admin = MemoryAccessAdmin(repository.database, repository.scope)
        self.agents = SecurityAgents(
            repository, RiskRepository(repository.database, repository.scope)
        )
        builder = StateGraph(WorkflowState)
        for stage in STAGES:
            builder.add_node(stage.value, self._node(stage))
        builder.add_conditional_edges(
            START, self._entry, {**{s.value: s.value for s in STAGES}, "done": END}
        )
        for a, b in zip(STAGES, STAGES[1:], strict=False):
            builder.add_edge(a.value, b.value)
        builder.add_edge(STAGES[-1].value, END)
        # No RAM checkpointer: the minimal LangGraph input is reconstructed from the graph cursor.
        self.graph = builder.compile()

    def _entry(self, state):
        current = self.repository.raw(state["investigation_id"])
        if (
            current["workflow_version"] != WORKFLOW_VERSION
            or current["provider_mode"] != self.provider.mode
        ):
            raise WorkflowBlocked()
        return "done" if current["sequence"] == 6 else STAGES[current["sequence"]].value

    def bootstrap(self):
        repo = self.repository
        repo.register_agents()
        space_id = node_id(repo.scope.tenant_id, NodeKind.MEMORY_SPACE, "space/workflow-team")
        if repo.graph.get_node(NodeRef(kind=NodeKind.MEMORY_SPACE, id=space_id)) is None:
            space_id = self.admin.create_space("workflow-team", "Security workflow shared evidence")
        for stage in STAGES:
            self.admin.grant_member(
                space_id, repo.agent_id(stage), MemoryRole.WRITER, "workflow/member/" + stage.value
            )
        return space_id

    def trigger(self, request):
        from app.repositories.workflow import workflow_id

        identifier = workflow_id(
            self.repository.scope.tenant_id,
            NodeKind.INVESTIGATION,
            "investigation/" + request.request_id,
        )
        try:
            space_id = self.repository.raw(identifier)["space_id"]
        except GraphNotFound:
            space_id = self.bootstrap()
        identifier = self.repository.create(request, space_id)
        return self.resume(identifier)

    def memory(self, investigation, stage):
        return MemoryService(
            MemoryRepository(
                self.repository.database,
                self.repository.scope,
                investigation["space_id"],
                self.repository.agent_id(stage),
            )
        )

    def _grant(self, investigation, identifiers):
        # Trusted code selects graph sources. No provider text/IDs enter this administrator path.
        for identifier in sorted(set(identifiers)):
            node = self.repository.node(identifier)
            self.admin.grant_context(
                investigation["space_id"],
                NodeRef(kind=node["kind"], id=identifier),
                "workflow/context/" + identifier,
            )

    def _node(self, stage):
        def execute(state):
            self.step(state["investigation_id"], stage)
            return state

        return execute

    def step(self, identifier, stage):
        repo = self.repository
        investigation = repo.raw(identifier)
        index = STAGES.index(stage)
        if investigation["sequence"] > index:
            return  # Another worker already committed this exact boundary.
        token = repo.claim(identifier, stage)
        try:
            run = repo.run(identifier, stage)
            memory = self.memory(investigation, stage)
            inputs = json.loads(run["input_evidence_ids_json"])
            self._grant(investigation, [identifier, *inputs])
            prior = [
                AgentOutput.model_validate_json(repo.run(identifier, s)["output_json"])
                for s in STAGES[:index]
            ]
            if run["output_json"] == "null":
                subjects = sorted(
                    {
                        identifier,
                        *inputs,
                        *(i for p in prior for i in p.identity_ids + p.resource_ids),
                    }
                )[:20]
                context = memory.get_agent_context(MemoryQuery(subject_ids=subjects))
                output = self.agents.execute(stage, investigation, memory, prior)
                self._grant(investigation, [*output.identity_ids, *output.resource_ids])
                note = self.provider.summarize(
                    PromptFacts(
                        stage=stage,
                        evidence_ids=output.evidence_ids,
                        finding_count=len(output.finding_ids),
                        memory_count=len(context.observations),
                        change_count=len(output.relationship_ids),
                    )
                )
                # Custom providers pass the same closed validation and citation checks.
                note = ModelNote.model_validate(note.model_dump())
                if not set(note.evidence_ids) <= set(output.evidence_ids):
                    raise WorkflowBlocked()
                output = output.model_copy(update={"model_note": note})
                repo.prepare(identifier, stage, token, output, [m.id for m in context.observations])
            output = AgentOutput.model_validate_json(repo.run(identifier, stage)["output_json"])
            if index >= 2:
                synchronize_investigations(repo, identifier, stage)
            # Idempotent memory/handoff operations resume from this durable prepared result.
            subjects = sorted({identifier, *output.identity_ids, *output.resource_ids})[:20]
            evidence = list(output.evidence_ids)
            if output.stage == AgentKey.RISK_ORACLE:
                for finding_id in output.finding_ids[:2]:
                    bundle = self.agents.risk_repository.evidence(finding_id)
                    evidence.extend(e["id"] for e in bundle.evidence[:3])
            evidence = sorted(set(evidence))[:20]
            self._grant(investigation, [*subjects, *evidence])
            stored = memory.write_memory(
                MemoryWrite(
                    request_id=identifier + "/" + stage.value + "/memory",
                    memory_key=identifier + "/" + stage.value,
                    memory_type=MemoryType.EPISODIC,
                    audience=Audience.TEAM,
                    content=output.summary,
                    subject_ids=subjects,
                    evidence_ids=evidence,
                    provenance=Provenance(
                        source=WORKFLOW_SOURCE,
                        source_id=run["id"],
                        observed_at=repo.inspect_run(run["id"]).created_at,
                        collector=WORKFLOW_VERSION,
                        source_uri="urn:shadowchain:agent-run:" + run["id"],
                    ),
                )
            )
            handoff = memory.create_handoff_context(
                HandoffRequest(
                    request_id=identifier + "/" + stage.value + "/handoff",
                    recipient_agent_id=run["handoff_target"],
                    subject_ids=[identifier],
                )
            )
            if index >= 2:
                synchronize_investigations(repo, identifier, stage, published_memory_id=stored.id)
            repo.finish(identifier, stage, token, stored.id, handoff.handoff_id)
        except Exception as exc:
            try:
                repo.fail(identifier, stage, token, getattr(exc, "code", "agent_step_failed"))
            except Exception as failure:
                logger.warning(
                    "workflow_failure_record_unavailable",
                    extra={"error_type": type(failure).__name__},
                )
            raise

    def resume(self, identifier):
        self.graph.invoke({"investigation_id": identifier}, {"recursion_limit": 8})
        return self.repository.inspect(identifier)

    def recover_pending(self):
        recovered = []
        for identifier in self.repository.incomplete():
            try:
                self.resume(identifier)
                recovered.append(identifier)
            except WorkflowBusy:
                continue
            except Exception as exc:
                logger.warning(
                    "workflow_recovery_deferred", extra={"error_type": type(exc).__name__}
                )
        return recovered

    def shared_memories(self, identifier):
        investigation = self.repository.raw(identifier)
        return self.memory(investigation, AgentKey.COORDINATOR).retrieve_related_memories(
            MemoryQuery(subject_ids=[identifier], include_superseded=True, max_hops=0, limit=100)
        )

    def handoffs(self, identifier):
        investigation = self.repository.raw(identifier)
        result = []
        for stage in STAGES:
            run = self.repository.run(identifier, stage)
            if run.get("handoff_id"):
                result.append(
                    self.memory(investigation, stage).get_handoff_context(run["handoff_id"])
                )
        return result

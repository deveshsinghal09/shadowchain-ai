"""Re-runnable six-agent investigation: python -m app.demo.agent_proof."""

import json

from app.core.config import get_settings
from app.db.falkordb import GraphDatabase
from app.models.events import DEMO_TENANT, Scenario
from app.models.graph import TenantScope
from app.models.workflow import STAGES, TriggerInvestigation
from app.repositories.demo import DemoRepository
from app.repositories.workflow import WorkflowRepository
from app.services.events import EventIngestionService
from app.services.workflow import InvestigationWorkflow


def run_proof(database, settings):
    database.initialize_schema()
    scope = TenantScope(tenant_id=DEMO_TENANT)
    events = EventIngestionService(DemoRepository(database, scope))
    events.seed()
    state = events.status(Scenario.A)
    events.replay(Scenario.A, state.generation)
    repository = WorkflowRepository(database, scope, settings)
    event_id = repository.read(
        "MATCH (e:Entity:SecurityEvent {tenant_id: $tenant_id, scenario_id: 'A'}) "
        "RETURN e.id ORDER BY e.sequence DESC LIMIT 1"
    )[0][0]
    workflow = InvestigationWorkflow(repository)
    request = TriggerInvestigation(request_id="demo/phase6/scenario-a", event_id=event_id)
    result = workflow.trigger(request)
    memories = workflow.shared_memories(result.id)
    handoffs = workflow.handoffs(result.id)
    steps = [t for t in result.tasks if t.task_type == "workflow_step"]
    reviews = [t for t in result.tasks if t.task_type == "remediation_review"]
    oracle, response = result.runs[2], result.runs[-1]
    checks = [
        result.status == "completed",
        result.completed_steps == 6,
        len(steps) == 6 and all(t.status == "completed" for t in steps),
        len(reviews) == 1 and reviews[0].status == "awaiting_review",
        all(r.status == "completed" and r.memory_id and r.handoff_id for r in result.runs),
        len(memories) == len(handoffs) == 6,
        len(oracle.output.finding_ids) == 1,
        result.runs[0].memory_id in result.runs[1].retrieved_memory_ids,
        bool(response.output.proposals),
        not workflow.graph.checkpointer,
    ]
    if not all(checks):
        raise RuntimeError("Six-agent graph proof did not satisfy its assertions")
    proposal = response.output.proposals[0]
    if proposal.before_paths != 1 or proposal.after_paths != 0 or proposal.execution_allowed:
        raise RuntimeError("Non-destructive remediation proposal did not break the modeled path")
    if workflow.trigger(request) != result:
        raise RuntimeError("Duplicate trigger changed durable investigation state")
    database.reconnect()
    restored = InvestigationWorkflow(WorkflowRepository(database, scope, settings))
    if (
        restored.repository.inspect(result.id) != result
        or restored.shared_memories(result.id) != memories
    ):
        raise RuntimeError("Investigation/memory reconstruction changed after reconnect")
    return {
        "synthetic": True,
        "verified": True,
        "workflow_version": "agents-2026.1",
        "provider_mode": settings.agent_provider,
        "investigation_id": result.id,
        "event_id": event_id,
        "status": result.status,
        "completed_steps": 6,
        "agent_order": [s.value for s in STAGES],
        "run_ids": [r.id for r in result.runs],
        "completed_step_tasks": len(steps),
        "awaiting_review_tasks": len(reviews),
        "shared_memories": len(memories),
        "handoffs": len(handoffs),
        "potential_finding_ids": oracle.output.finding_ids,
        "cross_agent_memory_retrieved": True,
        "remediation_proposal": proposal.model_dump(mode="json"),
        "privileged_actions": [],
        "langgraph_checkpointer": None,
        "duplicate_trigger_unchanged": True,
        "reconnect_preserved_state": True,
    }


def main():
    settings = get_settings()
    if not settings.demo_mode or settings.app_env == "production":
        raise RuntimeError("Synthetic agent proof requires local demo mode")
    database = GraphDatabase(settings)
    try:
        print(json.dumps(run_proof(database, settings), indent=2))
    finally:
        database.close()


if __name__ == "__main__":
    main()

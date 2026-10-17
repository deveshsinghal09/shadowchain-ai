"""Canonical frontend routes over the existing graph services; no frontend implementation."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import database_from_request
from app.api.v1.agents import Service as Workflow
from app.api.v1.demo import DemoService
from app.core.auth import Principal
from app.db.errors import GraphConflict
from app.db.falkordb import GraphDatabase
from app.models.events import EventReceipt, IngestRequest
from app.models.graph import TenantScope, from_epoch_us
from app.models.workflow import InvestigationRecord, TriggerInvestigation
from app.repositories.live import LiveRepository
from app.repositories.risk import RiskRepository
from app.repositories.visualization import ViewLimit, VisualizationRepository
from app.schemas.contract import (
    AgentActivity,
    AgentMemoryPage,
    AgentRunsPage,
    AgentRunsQuery,
    DashboardSummary,
    DiffQuery,
    GraphDifference,
    GraphQuery,
    GraphTimeline,
    PageQuery,
    RiskDetail,
    RiskPage,
    RiskSummary,
    ScenarioReplay,
    ScenarioReset,
    VisualSnapshot,
    WindowQuery,
)
from app.schemas.demo import DemoStatus, ReplayResult
from app.schemas.live import LivePage
from app.schemas.risk import DemoWhatIfRequest, GraphID, RemediationResult
from app.services.risk import RiskEngine

router = APIRouter(tags=["frontend contract v1"])
Database = Annotated[GraphDatabase, Depends(database_from_request)]


def views(database: Database, actor: Principal):
    return VisualizationRepository(database, actor.tenant_id)


Views = Annotated[VisualizationRepository, Depends(views)]


@router.get("/dashboard/summary", response_model=DashboardSummary)
def summary(repository: Views):
    return repository.summary()


@router.get("/graph/snapshot", response_model=VisualSnapshot)
def snapshot(query: Annotated[GraphQuery, Query()], repository: Views):
    return repository.snapshot(query)


@router.get("/graph/diff", response_model=GraphDifference)
def difference(query: Annotated[DiffQuery, Query()], repository: Views):
    return repository.difference(query)


@router.get("/graph/timeline", response_model=GraphTimeline)
def timeline(query: Annotated[WindowQuery, Query()], repository: Views):
    return repository.timeline(query)


@router.get("/graph/path", response_model=VisualSnapshot)
def path(risk_id: GraphID, repository: Views):
    detail = RiskRepository(
        repository.database, TenantScope(tenant_id=repository.tenant_id)
    ).evidence(risk_id)
    finding = detail.finding
    edge_ids = set(finding.relationship_ids + finding.supporting_relationship_ids)
    nodes = {finding.identity["id"], finding.resource["id"], *(r["id"] for r in finding.roles)}
    records = {
        e["record"]["id"]: e
        for e in detail.evidence
        if e.get("evidence_type") == "risk-graph-record-snapshot" and "id" in e["record"]
    }
    for identifier in edge_ids:
        if identifier in records:
            record = records[identifier]["record"]
            nodes.update([record["source_node_id"], record["target_node_id"]])
    if not (edge_ids | nodes).issubset(records):
        raise GraphConflict()
    if max(len(nodes), len(edge_ids)) > 250:
        raise ViewLimit()
    seq = repository.live.sequence()
    cases = repository.read(
        "MATCH (i:SecurityCase {tenant_id:$tenant,path_id:$path}) RETURN i.id LIMIT 65",
        {"path": finding.path_id},
    )
    associations = {
        "risk_severity": finding.score.severity,
        "investigation_ids": [r[0] for r in cases[:64]],
        "associations_truncated": len(cases) > 64,
    }

    def item(identifier):
        source = records[identifier]
        return repository.item(source["record"], {**associations, "evidence_ids": [source["id"]]})

    result = VisualSnapshot(
        snapshot_at=detail.analysis.at,
        snapshot_sequence=seq,
        collection="path",
        nodes=[item(i) for i in sorted(nodes) if i in records],
        edges=[item(i) for i in sorted(edge_ids) if i in records],
    )
    repository.check(seq)
    return result


@router.post("/demo/reset", response_model=DemoStatus)
def reset(body: ScenarioReset, service: DemoService):
    return service.reset(body.scenario, body.expected_generation)


@router.post("/demo/replay", response_model=ReplayResult)
def replay(body: ScenarioReplay, service: DemoService):
    return service.replay(body.scenario, body.expected_generation, body.through)


@router.post("/events/ingest", response_model=EventReceipt)
def ingest(body: IngestRequest, service: DemoService):
    return service.ingest(body.event, body.expected_generation)


@router.get("/risks", response_model=RiskPage)
def risks(query: Annotated[PageQuery, Query()], repository: Views):
    sequence = repository.fence(query)
    rows = repository.read(
        "MATCH (n:RiskFinding {tenant_id:$tenant}) WHERE n.id > $cursor "
        "AND n.analysis_id IS NOT NULL RETURN properties(n) ORDER BY n.id LIMIT $limit",
        {"cursor": query.cursor or "", "limit": query.limit + 1},
    )
    result = [
        RiskSummary(
            risk_id=n["id"],
            path_id=n["path_id"],
            severity=n["severity"],
            score=n["risk_score"],
            assessed_at=from_epoch_us(n["assessed_at"]),
        )
        for (n,) in rows[: query.limit]
    ]
    repository.check(sequence)
    return RiskPage(
        snapshot_sequence=sequence,
        risks=result,
        next_cursor=result[-1].risk_id if len(rows) > query.limit else None,
        has_more=len(rows) > query.limit,
    )


@router.get("/risks/{risk_id}", response_model=RiskDetail)
def risk(risk_id: GraphID, repository: Views):
    detail = RiskRepository(
        repository.database, TenantScope(tenant_id=repository.tenant_id)
    ).evidence(risk_id)
    cases = repository.read(
        "MATCH (i:SecurityCase {tenant_id:$tenant,path_id:$path}) RETURN i.id LIMIT 65",
        {"path": detail.finding.path_id},
    )
    if len(cases) > 64:
        raise ViewLimit()
    return RiskDetail(
        risk=detail.finding,
        assessed_at=detail.analysis.at,
        evidence_ids=[e["id"] for e in detail.evidence],
        investigation_ids=[r[0] for r in cases],
    )


@router.post("/what-if/remove-grant", response_model=RemediationResult)
def what_if(body: DemoWhatIfRequest, repository: Views):
    return RiskEngine(
        RiskRepository(repository.database, TenantScope(tenant_id=repository.tenant_id))
    ).what_if(body)


@router.post("/agents/run", response_model=InvestigationRecord)
def run_agents(body: TriggerInvestigation, service: Workflow):
    return service.trigger(body)


@router.get("/agents/runs", response_model=AgentRunsPage)
def runs(
    query: Annotated[AgentRunsQuery, Query()],
    service: Workflow,
    repository: Views,
):
    seq = repository.fence(query)
    rows = repository.read(
        "MATCH (r:WorkflowRun {tenant_id:$tenant}) WHERE r.id > $cursor "
        "AND ($investigation IS NULL OR r.investigation_id=$investigation) "
        "RETURN r.id ORDER BY r.id LIMIT $limit",
        {
            "cursor": query.cursor or "",
            "limit": query.limit + 1,
            "investigation": query.investigation_id,
        },
    )
    result = []
    for (identifier,) in rows[: query.limit]:
        run = service.repository.inspect_run(identifier)
        tasks = repository.read(
            "MATCH (t:WorkflowTask {tenant_id:$tenant,investigation_id:$id,stage:$stage}) "
            "RETURN t.id,t.status",
            {"id": run.investigation_id, "stage": run.stage.value},
        )
        evidence = repository.read(
            "MATCH path=(e:Evidence)-[:SUPPORTS]->(:RiskFinding)"
            "-[:DERIVED_FROM]->(r:WorkflowRun {id:$id}) "
            "WHERE all(n IN nodes(path) WHERE n.tenant_id=$tenant) "
            "AND all(e IN relationships(path) WHERE e.tenant_id=$tenant) "
            "RETURN DISTINCT e.id LIMIT 2001",
            {"id": identifier},
        )
        if len(evidence) > 2000:
            raise ViewLimit()
        result.append(
            AgentActivity(
                run=run,
                task_statuses=dict(tasks),
                memory_read_ids=run.retrieved_memory_ids,
                memory_written_ids=[run.memory_id] if run.memory_id else [],
                evidence_produced_ids=[r[0] for r in evidence],
                handoff_ids=[run.handoff_id] if run.handoff_id else [],
                output_evidence_ids=run.output.evidence_ids if run.output else [],
            )
        )
    repository.check(seq)
    return AgentRunsPage(
        snapshot_sequence=seq,
        runs=result,
        next_cursor=result[-1].run.id if len(rows) > query.limit else None,
        has_more=len(rows) > query.limit,
    )


@router.get("/agents/memory", response_model=AgentMemoryPage)
def memories(
    investigation_id: GraphID,
    service: Workflow,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: GraphID | None = None,
):
    values = sorted(service.shared_memories(investigation_id), key=lambda m: m.id)
    values = [m for m in values if cursor is None or m.id > cursor]
    return AgentMemoryPage(
        memories=values[:limit],
        limit=limit,
        has_more=len(values) > limit,
        next_cursor=values[limit - 1].id if len(values) > limit else None,
    )


@router.get("/live/events", response_model=LivePage)
def events(
    repository: Views,
    after: Annotated[int, Query(ge=0, le=9007199254740991)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return LiveRepository(repository.database, repository.tenant_id).page(after, limit)

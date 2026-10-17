"""Measured, synthetic evaluation. Receipts and business state are stored in FalkorDB.

The command driver owns process lifetimes; this module never restarts shared services.
Local JSON exports are reports, never checkpoints used by the workflow.
"""

import json
import math
from statistics import median
from time import perf_counter_ns

from app.core.time import utc_now
from app.db.cypher import fingerprint, stable_id
from app.db.falkordb import GraphDatabase
from app.demo.dataset import BASE_TIME, hour
from app.demo.memory_setup import setup_memory_demo
from app.models.events import DATASET_VERSION, DEMO_TENANT, Scenario
from app.models.graph import (
    NodeInput,
    NodeKind,
    NodeRef,
    Provenance,
    RelationshipInput,
    RelationshipKind,
    TenantScope,
    VerificationStatus,
)
from app.models.memory import Audience, MemoryClaim, MemoryQuery, MemoryType, MemoryWrite
from app.models.workflow import STAGES, TriggerInvestigation
from app.repositories.demo import DemoRepository
from app.repositories.graph import GraphRepository
from app.repositories.memory import MemoryRepository
from app.repositories.risk import RiskRepository
from app.repositories.workflow import WorkflowRepository
from app.schemas.risk import AnalysisRequest, DifferenceRequest, WhatIfRequest
from app.services.events import EventIngestionService
from app.services.memory import MemoryService
from app.services.risk import RiskEngine, view
from app.services.workflow import InvestigationWorkflow

VERSION = "evaluation-2026.1"
EVALUATION_SOURCE = "shadowchain.evaluation.v1"
SCOPE = TenantScope(tenant_id=DEMO_TENANT)
UNAVAILABLE_WITHOUT_GRAPH = [
    "temporal access-path traversal and policy evidence",
    "historical graph reconstruction and contributing event provenance",
    "persistent cross-agent memory and conflict history",
    "durable task ownership, deduplication, handoffs and restart recovery",
    "investigations, remediation proposals and review audits",
    "tenant event sequence, replay and graph-backed API rate budgets",
]


def require(check: bool, message: str):
    if not check:
        raise RuntimeError("Evaluation failed: " + message)


def provenance(key, *, observed_at=BASE_TIME):
    return Provenance(
        source=EVALUATION_SOURCE,
        source_id=key,
        observed_at=observed_at,
        collector=VERSION,
        source_uri="urn:shadowchain:synthetic:evaluation:" + key,
    )


def document_id(key):
    return stable_id(DEMO_TENANT, NodeKind.EVIDENCE.value, "", EVALUATION_SOURCE, key)


def save_document(database, key, document):
    """Immutable, bounded graph evidence chunks; JSON files are optional exports only."""
    repository = GraphRepository(database, SCOPE)
    raw = json.dumps(document, sort_keys=True, ensure_ascii=True)
    parts = []
    for index, start in enumerate(range(0, len(raw), 4000)):
        part_key = f"{key}/part/{index}"
        part = repository.upsert_node(
            NodeInput(
                kind=NodeKind.EVIDENCE,
                provenance=provenance(part_key, observed_at=utc_now()),
                attributes={
                    "evidence_type": "synthetic-evaluation-part",
                    "content_sha256": fingerprint({"text": raw[start : start + 4000]}),
                    "summary": "SYNTHETIC evaluation report fragment",
                    "record_json": json.dumps({"index": index, "text": raw[start : start + 4000]}),
                },
            )
        )
        parts.append(part)
    root = repository.upsert_node(
        NodeInput(
            kind=NodeKind.EVIDENCE,
            provenance=provenance(key, observed_at=utc_now()),
            attributes={
                "evidence_type": "synthetic-evaluation-manifest",
                "content_sha256": fingerprint(document),
                "summary": "SYNTHETIC evaluation; measurements are local observations",
                "record_json": json.dumps(
                    {"parts": [p.id for p in parts], "sha256": fingerprint(document)}
                ),
            },
        )
    )
    for index, part in enumerate(parts):
        repository.create_relationship(
            RelationshipInput(
                kind=RelationshipKind.DERIVED_FROM,
                source=root.ref,
                target=part.ref,
                provenance=provenance(f"{key}/includes/{index}", observed_at=root.created_at),
                valid_from=root.created_at,
                verification_status=VerificationStatus.VERIFIED,
            )
        )
    return root.id


def load_document(database, key):
    repository = GraphRepository(database, SCOPE)
    root = repository.get_node(NodeRef(kind=NodeKind.EVIDENCE, id=document_id(key)))
    require(root is not None, "missing graph evaluation receipt")
    manifest = json.loads(root.attributes["record_json"])
    parts = []
    for identifier in manifest["parts"]:
        part = repository.get_node(NodeRef(kind=NodeKind.EVIDENCE, id=identifier))
        require(part is not None, "missing graph report fragment")
        parts.append(json.loads(part.attributes["record_json"]))
    document = json.loads("".join(p["text"] for p in sorted(parts, key=lambda p: p["index"])))
    require(fingerprint(document) == manifest["sha256"], "graph receipt digest mismatch")
    return document


def workflow_for(database, settings):
    return InvestigationWorkflow(WorkflowRepository(database, SCOPE, settings))


def scenario_services(database):
    return (
        EventIngestionService(DemoRepository(database, SCOPE)),
        RiskEngine(RiskRepository(database, SCOPE)),
    )


def handoff_identity(handoff):
    # Expanded context applies current ACLs and asset metadata. Compare durable
    # transfer references, rather than treating that live view as a frozen snapshot.
    return {
        "handoff_id": handoff.handoff_id,
        "sender_agent_id": handoff.sender_agent_id,
        "recipient_agent_id": handoff.recipient_agent_id,
        "memory_ids": sorted(handoff.memory_ids),
    }


def prepare(database, settings):
    require(
        settings.demo_mode
        and settings.app_env != "production"
        and settings.agent_provider == "mock"
        and not settings.aws_enabled
        and not settings.cloudtrail_enabled,
        "local synthetic mock configuration required",
    )
    database.initialize_schema()
    events, engine = scenario_services(database)
    events.seed()
    state = events.status(Scenario.A)
    require(state.sequence == 0, "use a fresh isolated evaluation graph")
    timeline = []
    for stamp in (9, 10, 11, 12):
        replay = events.replay(Scenario.A, state.generation, hour(stamp))
        snapshot = events.snapshot(Scenario.A, hour(stamp))
        result = engine.discover(AnalysisRequest(scenario=Scenario.A, at=hour(stamp)))
        timeline.append(
            {
                "at": result.at.isoformat(),
                "path_count": len(result.findings),
                "maximum_score": max((f.score.value for f in result.findings), default=0),
                "semantic_sha256": snapshot.semantic_sha256,
                "active_edges": len(snapshot.relationships),
                "replayed_event_ids": replay.applied_event_ids,
                "analysis_id": result.analysis_id,
            }
        )
    require([t["path_count"] for t in timeline] == [0, 0, 0, 1], "path timeline")
    require(len({t["semantic_sha256"] for t in timeline}) == 4, "four distinct historical states")
    require(sum(len(t["replayed_event_ids"]) for t in timeline) == 3, "three changes")
    finding = result.findings[0]
    difference = engine.difference(
        DifferenceRequest(before=hour(9), after=hour(12), scenario=Scenario.A)
    )
    introduction = difference.introductions[0]
    require(introduction.first_reachable_at == hour(12), "first reachable timestamp")
    changed = [e for e in finding.contributing_events if hour(9) < e.occurred_at <= hour(12)]
    require({e.occurred_at for e in changed} == {hour(10), hour(11), hour(12)}, "event chain")
    require(
        all(e.relationship_ids or e.closed_relationship_ids for e in changed), "edge provenance"
    )
    workflow = workflow_for(database, settings)
    event_id = workflow.repository.read(
        "MATCH (e:Entity:SecurityEvent {tenant_id:$tenant_id, scenario_id:'A', sequence:3}) "
        "RETURN e.id"
    )[0][0]
    request = TriggerInvestigation(request_id="demo/phase11/workflow", event_id=event_id)
    identifier = workflow.repository.create(request, workflow.bootstrap())
    for stage in STAGES[:3]:
        workflow.step(identifier, stage)
    partial = workflow.repository.inspect(identifier)
    require(partial.completed_steps == 3, "partial investigation boundary")
    handoffs = workflow.handoffs(identifier)
    require(len(handoffs) == 3, "unfinished workflow handoff persistence")
    receipt = {
        "version": VERSION,
        "dataset_version": DATASET_VERSION,
        "manifest_sha256": events.manifest_sha256,
        "timeline": timeline,
        "finding": finding.model_dump(mode="json"),
        "introduction": introduction.model_dump(mode="json"),
        "workflow_id": identifier,
        "request": request.model_dump(mode="json"),
        "partial": partial.model_dump(mode="json"),
        "handoffs": [handoff_identity(h) for h in handoffs],
    }
    save_document(database, "prepared", receipt)
    return receipt


def conflict_and_ablation_setup(database):
    setup = setup_memory_demo(database)
    sources = {n.provenance.source_id: n for n in setup["sources"]}
    resource = sources["resource/customer-data"]
    incident = sources["c/investigation/v2"]
    services = [
        MemoryService(MemoryRepository(database, SCOPE, setup["space_id"], setup[agent]))
        for agent in ("scout", "verifier")
    ]

    def write(key, evidence_key, value=None):
        return MemoryWrite(
            request_id="phase11/" + key,
            memory_key="phase11/" + key,
            memory_type=MemoryType.EPISODIC if value is None else MemoryType.SEMANTIC,
            content="SYNTHETIC investigation observation: " + key,
            audience=Audience.TEAM,
            subject_ids=[resource.id, incident.id],
            evidence_ids=[sources[evidence_key].id],
            provenance=provenance(key, observed_at=sources[evidence_key].provenance.observed_at),
            claim=MemoryClaim(subject_id=resource.id, predicate="s3.public_access", value=value)
            if value is not None
            else None,
        )

    first = services[0].write_memory(write("suspicious", "c/evidence/acl", True))
    second = services[1].write_memory(write("disputing", "c/evidence/policy", False))
    require(
        first.provenance.observed_at < second.provenance.observed_at,
        "newer disputing evidence source timestamp",
    )
    query = MemoryQuery(memory_ids=[first.id, second.id])
    context = services[1].get_agent_context(query)
    conflicts = services[1].detect_memory_conflicts(query)
    require(len(context.observations) == 2 and len(conflicts) == 1, "both conflicting observations")
    require(not context.established_facts, "conflicting claims must not become definitive facts")
    require(
        all(m.evidence and m.conflicting_memory_ids for m in context.observations),
        "conflict evidence",
    )
    request = write("ablation", "c/evidence/acl")
    memory = services[0].write_memory(request)
    query = MemoryQuery(subject_ids=request.subject_ids)
    retrieved = services[1].retrieve_related_memories(query)
    require(any(m.id == memory.id for m in retrieved), "pre-restart cross-agent retrieval")
    result = {
        "conflict": {
            "conflicts": [c.model_dump(mode="json") for c in conflicts],
            "observations": [m.model_dump(mode="json") for m in context.observations],
            "definitive_fact_count": len(context.established_facts),
            "verification_required": True,
        },
        "ablation": {
            "input": {
                "write": request.model_dump(mode="json"),
                "query": query.model_dump(mode="json"),
            },
            "memory_id": memory.id,
            "space_id": setup["space_id"],
            "reader_agent_id": setup["verifier"],
            "persistent_before_restart": 1,
        },
    }
    save_document(database, "memory-input", result)
    return result


def verify_recovery(database, settings):
    receipt = load_document(database, "prepared")
    workflow = workflow_for(database, settings)
    restored = workflow.repository.inspect(receipt["workflow_id"])
    require(
        restored.status == "completed" and restored.completed_steps == 6, "recovered six stages"
    )
    require(
        [r.model_dump(mode="json") for r in restored.runs[:3]] == receipt["partial"]["runs"][:3],
        "completed runs repeated or altered",
    )
    require(all(r.attempt_count == 1 for r in restored.runs), "duplicate execution")
    require(len({t.id for t in restored.tasks}) == 7, "unique task IDs")
    duplicate = workflow.trigger(TriggerInvestigation.model_validate(receipt["request"]))
    require(duplicate == restored and workflow.resume(restored.id) == restored, "idempotent retry")
    memories = workflow.shared_memories(restored.id)
    handoffs = workflow.handoffs(restored.id)
    require(len(memories) == len(handoffs) == 6, "memory/handoff count")
    require(
        [handoff_identity(h) for h in handoffs[:3]] == receipt["handoffs"],
        "prior handoff evidence changed",
    )
    oracle, keeper = restored.runs[2:4]
    require(oracle.memory_id in keeper.retrieved_memory_ids, "Agent B did not read Agent A memory")
    oracle_memory = next(m for m in memories if m.id == oracle.memory_id)
    require(bool(oracle.output.finding_ids and oracle_memory.evidence), "shared finding evidence")
    require(bool(keeper.output.memory_ids), "Memory Keeper did not continue from shared context")
    received = [
        restored.runs[index].memory_id in restored.runs[index + 1].retrieved_memory_ids
        and handoffs[index].recipient_agent_id == restored.runs[index + 1].agent_id
        and restored.runs[index].memory_id in handoffs[index].memory_ids
        for index in range(5)
    ]
    require(all(received), "cross-agent handoff consumption")
    return {
        "workflow": restored.model_dump(mode="json"),
        "shared_memory": oracle_memory.model_dump(mode="json"),
        "reader_run": keeper.model_dump(mode="json"),
        "completed_runs_preserved": 3,
        "completed_step_tasks": 6,
        "unique_task_count": 7,
        "pending_human_review_tasks": 1,
        "internal_handoffs_consumed": sum(received),
        "internal_handoffs_expected": len(received),
        "published_handoffs": len(handoffs),
        "duplicate_trigger_unchanged": True,
    }


def verify_remediation(database):
    receipt = load_document(database, "prepared")
    events, engine = scenario_services(database)
    before = events.repository.graph()
    path = receipt["finding"]
    request = WhatIfRequest(
        at=hour(12), scenario=Scenario.A, remove_relationship_ids=[path["relationship_ids"][-3]]
    )
    first = engine.what_if(request)
    second = engine.what_if(request)
    require(first == second, "simulation repeatability")
    require(len(first.before.findings) == 1 and not first.after.findings, "simulated path removal")
    require(before == events.repository.graph(), "source graph modified by simulation")
    evidence = engine.repository.evidence(path["finding_id"])
    require(bool(evidence.evidence), "finding evidence missing")
    return {
        "request": request.model_dump(mode="json"),
        "before_path_count": len(first.before.findings),
        "after_path_count": len(first.after.findings),
        "remaining_path_ids": [f.path_id for f in first.after.findings],
        "before_maximum_score": max((f.score.value for f in first.before.findings), default=0),
        "after_maximum_score": max((f.score.value for f in first.after.findings), default=0),
        "source_graph_sha256_before": fingerprint(before),
        "source_graph_sha256_after": fingerprint(events.repository.graph()),
        "supporting_evidence_ids": [e["id"] for e in evidence.evidence],
        "repeatable": True,
        "aws_operations": [],
        "graph_mutated": first.graph_mutated,
        "uncertainty": first.after.limitations,
    }


def latency_summary(samples):
    require(bool(samples), "missing latency measurements")
    ordered = sorted(samples)
    return {
        "samples": len(samples),
        "raw_ms": samples,
        "minimum_ms": min(samples),
        "median_ms": median(samples),
        "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "maximum_ms": max(samples),
        "p95_method": "nearest-rank",
    }


class MeasuredDatabase(GraphDatabase):
    """Evaluation-only timings around real SDK queries; no query text/parameters logged."""

    def __init__(self, settings):
        super().__init__(settings)
        self.samples = None

    def query(self, *args, **kwargs):
        start = perf_counter_ns()
        result = super().query(*args, **kwargs)
        if self.samples is not None:
            self.samples.append((perf_counter_ns() - start) / 1_000_000)
        return result


def measure_queries(database, settings, repetitions=20):
    measured = MeasuredDatabase(settings)
    try:
        return _measure_queries(measured, settings, repetitions)
    finally:
        measured.close()


def _measure_queries(database, settings, repetitions):
    require(5 <= repetitions <= 100, "latency repetition bound")
    receipt = load_document(database, "prepared")
    memory_setup = load_document(database, "memory-input")["ablation"]
    workflow = workflow_for(database, settings)
    risk = RiskRepository(database, SCOPE)
    memory = MemoryService(
        MemoryRepository(database, SCOPE, memory_setup["space_id"], memory_setup["reader_agent_id"])
    )
    options = AnalysisRequest(at=hour(12), scenario=Scenario.A)
    probes = {
        "bounded_path_traversal": lambda: risk.load_views(options, [view("at", hour(12))]),
        "memory_with_acl_and_provenance": lambda: memory.retrieve_related_memories(
            MemoryQuery.model_validate(memory_setup["input"]["query"])
        ),
        "durable_workflow_reconstruction": lambda: workflow.repository.inspect(
            receipt["workflow_id"]
        ),
    }
    results = {}
    for name, probe in probes.items():
        probe()  # one unmeasured warmup per operation, same inputs for every repetition
        database.samples = []
        samples = []
        for _ in range(repetitions):
            start = perf_counter_ns()
            probe()
            samples.append((perf_counter_ns() - start) / 1_000_000)
        results[name] = {
            "operation_latency": latency_summary(samples),
            "graph_query_latency": latency_summary(database.samples),
        }
        database.samples = None
    return {
        "method": "sequential client wall-clock operation round trips, perf_counter_ns",
        "scope": (
            "query latency includes SDK/network/Cypher/decoding; operation latency also "
            "includes service ACL/provenance reconstruction"
        ),
        "warmup_per_operation": 1,
        "concurrency": 1,
        "errors": 0,
        "operations": results,
        "limitations": (
            "Small synthetic local fixture; not server-only latency or a production benchmark."
        ),
    }


def finish(database, settings, process_proof, ablation, unavailable, repetitions=20):
    receipt = load_document(database, "prepared")
    recovery = verify_recovery(database, settings)
    memory_fixture = load_document(database, "memory-input")
    memory_input = memory_fixture["ablation"]
    reader = MemoryService(
        MemoryRepository(database, SCOPE, memory_input["space_id"], memory_input["reader_agent_id"])
    )
    retrieved = reader.retrieve_related_memories(
        MemoryQuery.model_validate(memory_input["input"]["query"])
    )
    require(
        any(m.id == memory_input["memory_id"] for m in retrieved), "persistent ablation recovery"
    )
    require(
        ablation["before_restart_count"] == 1 and ablation["after_restart_count"] == 0,
        "ephemeral baseline",
    )
    require(ablation["input_sha256"] == fingerprint(memory_input["input"]), "same ablation inputs")
    require(
        ablation["persistent_before_restart_count"] == 1
        and ablation["persistent_after_restart_count"] == 1,
        "independent persistent reader processes",
    )
    ablation = {
        **ablation,
        "input": memory_input["input"],
        "graph_unavailable_operations": UNAVAILABLE_WITHOUT_GRAPH,
        "scope": (
            "one shared observation, same write/query inputs; no graph or risk engine in baseline"
        ),
        "baseline_limitations": (
            "No ACL, provenance validation, graph traversal or workflow coordination; "
            "retention only."
        ),
    }
    require(
        process_proof["first_pid"] != process_proof["second_pid"], "actual backend process restart"
    )
    require(process_proof["startup_recovered_without_resume_request"], "startup recovery")
    require(process_proof["first_process_exited"], "first backend did not exit")
    require(process_proof["first_listener_closed"], "first backend listener did not close")
    require(
        len(unavailable) == 4
        and all(row["error_code"] == "dependency_unavailable" for row in unavailable),
        "fail-closed outage",
    )
    remediation = verify_remediation(database)
    # Re-read saved source-time analyses after recovery; no historical state from RAM.
    historical = [
        RiskRepository(database, SCOPE).saved(row["analysis_id"]) for row in receipt["timeline"]
    ]
    require([len(row.findings) for row in historical] == [0, 0, 0, 1], "historical recovery")
    metrics = {
        "path_detection_correctness": {"passed": 4, "total": 4},
        "historical_reconstruction_correctness": {"passed": 4, "total": 4},
        "successful_memory_retrieval": {"passed": 2, "total": 2},
        "cross_agent_handoff_completion": {
            "passed": recovery["internal_handoffs_consumed"],
            "total": 5,
        },
        "restart_recovery": {"passed": 1, "total": 1},
        "duplicate_task_prevention": {"passed": 2, "total": 2},
    }
    report = {
        "version": VERSION,
        "measured_at": utc_now().isoformat(),
        "synthetic": True,
        "verified": True,
        "graph": settings.falkordb_graph,
        "dataset_version": DATASET_VERSION,
        "manifest_sha256": receipt["manifest_sha256"],
        "configuration": {
            "aws_enabled": False,
            "cloudtrail_enabled": False,
            "agent_provider": "mock",
        },
        "metrics": metrics,
        "demo1": {
            "timeline": receipt["timeline"],
            "finding": receipt["finding"],
            "introduction": receipt["introduction"],
        },
        "demo2_and_3": recovery,
        "backend_process_restart": process_proof,
        "demo4": memory_fixture["conflict"],
        "demo5": remediation,
        "ablation": ablation,
        "falkordb_off_checks": unavailable,
        "graph_query_latency": measure_queries(database, settings, repetitions),
        "limitations": [
            "Fixed fixture checks do not estimate precision or recall on real AWS estates.",
            "The sixth handoff is a pending human recommendation review; no automatic AWS action.",
            "Backend restart is measured; database-host recovery has separate persistence tests.",
            "No performance improvement is inferred from the memory retention ablation.",
        ],
    }
    save_document(database, "result", report)
    require(load_document(database, "result") == report, "graph report round trip")
    return report

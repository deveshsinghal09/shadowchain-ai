"""Re-runnable synthetic graph memory proof: python -m app.demo.memory_proof."""

import json

from app.core.config import get_settings
from app.db.falkordb import GraphDatabase
from app.demo.dataset import BASE_TIME
from app.demo.memory_setup import setup_memory_demo
from app.models.graph import NodeKind, Provenance, VerificationStatus
from app.models.memory import (
    Audience,
    HandoffRequest,
    MemoryClaim,
    MemoryQuery,
    MemoryType,
    MemoryWrite,
    ProcedurePlan,
    ReadOnlyStep,
    ReviewMemory,
    SupersedeMemory,
)
from app.repositories.memory import MemoryRepository
from app.services.memory import MemoryService


def run_proof(database) -> dict:
    setup = setup_memory_demo(database)
    scope, space = setup["scope"], setup["space_id"]
    scout = MemoryService(MemoryRepository(database, scope, space, setup["scout"]))
    verifier = MemoryService(MemoryRepository(database, scope, space, setup["verifier"]))
    sources = {n.provenance.source_id: n for n in setup["sources"]}
    resource = sources["resource/customer-data"]
    investigation = sources["c/investigation/v2"]
    acl, policy = sources["c/evidence/acl"], sources["c/evidence/policy"]
    concept = next(n for n in setup["sources"] if n.kind == NodeKind.SECURITY_CONCEPT)

    def request(
        key, kind, content, evidence, *, subjects=None, claim=None, procedure=None, memory_key=None
    ):
        return MemoryWrite(
            request_id="proof/" + key,
            memory_key=memory_key or "proof/" + key,
            memory_type=kind,
            content="SYNTHETIC: " + content,
            audience=Audience.TEAM,
            evidence_ids=[evidence.id],
            subject_ids=subjects or [resource.id, investigation.id],
            claim=claim,
            procedure=procedure,
            provenance=Provenance(
                source="shadowchain.memory.demo",
                source_id=key,
                observed_at=BASE_TIME,
                collector="synthetic-memory-demo",
                source_uri="urn:shadowchain:synthetic:memory:" + key,
            ),
        )

    episode = scout.write_memory(
        request(
            "episode",
            MemoryType.EPISODIC,
            "Retain the earlier suspicious ACL hypothesis and the investigation evidence.",
            acl,
        )
    )
    first = scout.write_memory(
        request(
            "claim-v1",
            MemoryType.SEMANTIC,
            "Earlier report suggests public access; this is an observation needing verification.",
            acl,
            claim=MemoryClaim(subject_id=resource.id, predicate="s3.public_access", value=True),
        )
    )
    counter = verifier.write_memory(
        request(
            "counter-observation",
            MemoryType.SEMANTIC,
            "Newer policy evidence disputes public access; preserve the earlier claim.",
            policy,
            claim=MemoryClaim(subject_id=resource.id, predicate="s3.public_access", value=False),
        )
    )
    correction = scout.supersede_memory(
        SupersedeMemory(
            memory_id=first.id,
            expected_version=1,
            correction=request(
                "claim-v2",
                MemoryType.SEMANTIC,
                "Correct the earlier interpretation using newer policy evidence.",
                policy,
                memory_key="proof/claim-v1",
                claim=MemoryClaim(
                    subject_id=resource.id, predicate="s3.public_access", value=False
                ),
            ),
        )
    )
    procedural = scout.write_memory(
        request(
            "procedure",
            MemoryType.PROCEDURAL,
            "Successful investigation pattern: collect evidence, compare paths, "
            "then draft a remediation proposal.",
            policy,
            procedure=ProcedurePlan(
                steps=[
                    ReadOnlyStep.COLLECT_GRAPH_EVIDENCE,
                    ReadOnlyStep.COMPARE_RISK_SNAPSHOTS,
                    ReadOnlyStep.DRAFT_REMEDIATION_PLAN,
                ],
                outcome="successful",
                lesson="Use evidence and what-if analysis before proposing a policy change",
            ),
        )
    )
    failed = scout.write_memory(
        request(
            "failed-approach",
            MemoryType.PROCEDURAL,
            "An ACL-only interpretation lacked policy context; retain the failed approach.",
            acl,
            procedure=ProcedurePlan(
                steps=[ReadOnlyStep.COLLECT_GRAPH_EVIDENCE],
                outcome="failed",
                lesson="ACL observations alone do not establish effective AWS authorization",
            ),
        )
    )
    semantic_concept = scout.write_memory(
        request(
            "concept",
            MemoryType.SEMANTIC,
            "Least privilege is a security principle, not an authorization to change IAM.",
            policy,
            subjects=[concept.id],
            claim=MemoryClaim(
                subject_id=concept.id, predicate="concept.name", value="least_privilege"
            ),
        )
    )
    for memory in (procedural, semantic_concept, correction):
        verifier.review_memory(
            ReviewMemory(
                request_id="proof/review/" + memory.id,
                memory_id=memory.id,
                expected_version=memory.version,
                verification_status=VerificationStatus.VERIFIED,
                reason="Reviewed the normalized evidence and structured read-only representation",
            )
        )
    ids = [
        episode.id,
        first.id,
        counter.id,
        correction.id,
        procedural.id,
        failed.id,
        semantic_concept.id,
    ]
    context = scout.get_agent_context(MemoryQuery(memory_ids=ids))
    history = scout.retrieve_related_memories(MemoryQuery(memory_ids=ids, include_superseded=True))
    conflicts = scout.detect_memory_conflicts(MemoryQuery(memory_ids=ids, include_superseded=True))
    handoff = scout.create_handoff_context(
        HandoffRequest(
            request_id="proof/handoff",
            recipient_agent_id=setup["verifier"],
            subject_ids=[resource.id, investigation.id, concept.id],
        )
    )
    checks = [
        first.id not in {m.id for m in context.observations},
        any(set(c.memory_ids) == {first.id, counter.id} for c in conflicts),
        len(context.observations) == 6 and len(history) == 7,
        len(context.validated_procedures) == 1,
        not context.privileged_actions and not context.source_content_is_instructions,
        verifier.get_handoff_context(handoff.handoff_id) == handoff,
    ]
    if not all(checks):
        raise RuntimeError("Synthetic memory proof did not satisfy its graph assertions")
    database.reconnect()
    restored = MemoryService(MemoryRepository(database, scope, space, setup["scout"]))
    if restored.get_agent_context(MemoryQuery(memory_ids=ids)) != context:
        raise RuntimeError("Memory context changed across reconnect")
    return {
        "synthetic": True,
        "verified": True,
        "space_id": space,
        "scout_agent_id": setup["scout"],
        "verifier_agent_id": setup["verifier"],
        "memory_ids": ids,
        "current_memory_count": len(context.observations),
        "historical_memory_count": len(history),
        "memory_types": sorted({m.memory_type.value for m in context.observations}),
        "historical_conflicts": [c.model_dump(mode="json") for c in conflicts],
        "superseded_memory_id": first.id,
        "replacement_memory_id": correction.id,
        "validated_procedure_count": len(context.validated_procedures),
        "verified_semantic_count": len(context.established_facts),
        "failed_approach_retained": failed.id in {m.id for m in context.observations},
        "privileged_actions": [],
        "handoff_id": handoff.handoff_id,
        "handoff_memory_count": len(handoff.memory_ids),
        "reconnection_preserved_context": True,
    }


def main():
    settings = get_settings()
    if not settings.demo_mode or settings.app_env == "production":
        raise RuntimeError("The synthetic memory proof requires local demo mode")
    database = GraphDatabase(settings)
    try:
        print(json.dumps(run_proof(database), indent=2))
    finally:
        database.close()


if __name__ == "__main__":
    main()

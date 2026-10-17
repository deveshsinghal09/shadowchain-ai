"""Real graph-backed Phase 4 proof: python -m app.demo.risk_proof."""

import json

from app.core.config import get_settings
from app.db.falkordb import GraphDatabase
from app.demo.dataset import hour
from app.models.events import DEMO_TENANT, Scenario
from app.models.graph import TenantScope
from app.repositories.demo import DemoRepository
from app.repositories.risk import RiskRepository
from app.schemas.risk import AnalysisRequest, DifferenceRequest, WhatIfRequest
from app.services.events import EventIngestionService
from app.services.risk import RiskEngine


def main() -> None:
    settings = get_settings()
    if not settings.demo_mode or settings.app_env == "production":
        raise RuntimeError("The synthetic proof requires local demo mode")
    database = GraphDatabase(settings)
    try:
        database.initialize_schema()
        scope = TenantScope(tenant_id=DEMO_TENANT)
        events = EventIngestionService(DemoRepository(database, scope))
        events.seed()
        state = events.status(Scenario.A)
        events.replay(Scenario.A, state.generation)
        engine = RiskEngine(RiskRepository(database, scope))
        snapshots = [
            engine.discover(AnalysisRequest(at=hour(h), scenario=Scenario.A))
            for h in (9, 10, 11, 12)
        ]
        if [len(r.findings) for r in snapshots] != [0, 0, 0, 1]:
            raise RuntimeError("Unexpected potential-path timeline")
        last = snapshots[-1]
        path = last.findings[0]
        difference = engine.difference(
            DifferenceRequest(
                before=hour(9),
                after=hour(12),
                scenario=Scenario.A,
            )
        )
        if difference.introductions[0].first_reachable_at != hour(12):
            raise RuntimeError("Unexpected introducing change time")
        simulation = engine.what_if(
            WhatIfRequest(
                at=hour(12),
                scenario=Scenario.A,
                remove_relationship_ids=[path.relationship_ids[-3]],
            )
        )
        if simulation.after.findings or simulation.graph_mutated:
            raise RuntimeError("Read-only remediation simulation failed")
        database.reconnect()
        restored = engine.repository.saved(last.analysis_id)
        if restored != last or not engine.repository.evidence(path.finding_id).evidence:
            raise RuntimeError("Persisted analysis/evidence recovery failed")
        print(
            json.dumps(
                {
                    "synthetic": True,
                    "verified": True,
                    "engine_version": last.engine_version,
                    "timeline": [
                        {
                            "at": r.at.isoformat(),
                            "analysis_id": r.analysis_id,
                            "path_count": len(r.findings),
                            "maximum_score": max((f.score.value for f in r.findings), default=0),
                        }
                        for r in snapshots
                    ],
                    "finding": path.model_dump(mode="json"),
                    "introductions": [i.model_dump(mode="json") for i in difference.introductions],
                    "simulation": {
                        "excluded_relationship_ids": simulation.excluded_relationship_ids,
                        "removed_path_ids": simulation.removed_path_ids,
                        "paths_after_removal": len(simulation.after.findings),
                        "graph_mutated": simulation.graph_mutated,
                    },
                    "reconnection_preserved_findings": True,
                },
                indent=2,
            )
        )
    finally:
        database.close()


if __name__ == "__main__":
    main()

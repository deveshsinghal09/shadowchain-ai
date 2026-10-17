"""Run the four-timestamp Scenario A proof: python -m app.demo.replay --reset."""

import argparse
import json

from app.core.config import get_settings
from app.db.falkordb import GraphDatabase
from app.demo.dataset import hour
from app.models.events import DATASET_VERSION, DEMO_TENANT, Scenario
from app.models.graph import TenantScope
from app.repositories.demo import DemoRepository
from app.services.events import EventIngestionService


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset", action="store_true", help="Reset only synthetic Scenario A first"
    )
    arguments = parser.parse_args()
    settings = get_settings()
    if not settings.demo_mode or settings.app_env == "production":
        parser.error("Synthetic demo mode must be enabled outside production")
    database = GraphDatabase(settings)
    try:
        engine = EventIngestionService(DemoRepository(database, TenantScope(tenant_id=DEMO_TENANT)))
        engine.seed()
        state = engine.status(Scenario.A)
        if arguments.reset:
            state = engine.reset(Scenario.A, state.generation)
        proof = []
        for stamp in (9, 10, 11, 12):
            engine.replay(Scenario.A, state.generation, hour(stamp))
            snapshot = engine.snapshot(Scenario.A, hour(stamp))
            proof.append(
                {
                    "at": snapshot.as_of.isoformat(),
                    "semantic_sha256": snapshot.semantic_sha256,
                    "alice_reachable_roles": snapshot.reachable_roles["identity/alice"],
                    "active_can_assume_edges": sum(
                        e["kind"] == "CAN_ASSUME" for e in snapshot.relationships
                    ),
                    "potential_sensitive_paths": [
                        p.model_dump(mode="json") for p in snapshot.potential_sensitive_paths
                    ],
                }
            )
        expected = [(0, 0), (1, 0), (2, 0), (2, 1)]
        observed = [
            (len(row["alice_reachable_roles"]), len(row["potential_sensitive_paths"]))
            for row in proof
        ]
        if observed != expected or len({row["semantic_sha256"] for row in proof}) != 4:
            raise RuntimeError("Scenario A proof does not match the versioned fixture")
        print(
            json.dumps(
                {
                    "synthetic": True,
                    "dataset_version": DATASET_VERSION,
                    "scenario": "A",
                    "verified": True,
                    "timeline": proof,
                    "comparison": engine.compare(Scenario.A, hour(9), hour(12)).model_dump(
                        mode="json"
                    ),
                },
                indent=2,
            )
        )
    finally:
        database.close()


if __name__ == "__main__":
    main()

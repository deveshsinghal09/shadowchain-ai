"""Explicit synthetic graph fixture for comparing state before/after a server restart."""

import argparse
import hashlib
import json
from datetime import UTC, datetime

from app.core.config import get_settings
from app.db.cypher import stable_id
from app.db.falkordb import GraphDatabase
from app.models.graph import (
    NodeInput,
    NodeKind,
    NodeRef,
    Provenance,
    RelationshipInput,
    RelationshipKind,
    TenantScope,
    epoch_us,
)
from app.repositories.graph import GraphRepository

TENANT = "shadowchain-verification"
ACCOUNT = "000000000000"
SOURCE = "synthetic-verification"
OBSERVED = datetime(2026, 1, 1, tzinfo=UTC)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["seed", "read"])
    mode = parser.parse_args().mode
    database = GraphDatabase(get_settings())
    repository = GraphRepository(database, TenantScope(tenant_id=TENANT))
    try:
        records = []
        for kind in (NodeKind.IDENTITY, NodeKind.ROLE, NodeKind.SECURITY_EVENT):
            provenance = Provenance(
                source=SOURCE,
                source_id=kind.value,
                observed_at=OBSERVED,
                collector="verification-script",
                source_uri=f"urn:shadowchain:synthetic:{kind.value}",
            )
            if mode == "seed":
                attributes = (
                    {
                        "event_type": "SyntheticGrant",
                        "event_time": epoch_us(OBSERVED),
                        "content_sha256": hashlib.sha256(b"synthetic-event").hexdigest(),
                    }
                    if kind == NodeKind.SECURITY_EVENT
                    else {}
                )
                record = repository.upsert_node(
                    NodeInput(
                        kind=kind,
                        account_id=ACCOUNT,
                        provenance=provenance,
                        attributes=attributes,
                    )
                )
            else:
                record = repository.get_node(
                    NodeRef(
                        kind=kind,
                        id=stable_id(TENANT, kind.value, ACCOUNT, SOURCE, kind.value),
                    )
                )
                if record is None:
                    raise RuntimeError("A persisted verification node is missing")
            records.append(record)
        if mode == "seed":
            repository.create_relationship(
                RelationshipInput(
                    kind=RelationshipKind.CAN_ASSUME,
                    source=records[0].ref,
                    target=records[1].ref,
                    provenance=Provenance(
                        source=SOURCE,
                        source_id="grant",
                        observed_at=OBSERVED,
                        collector="verification-script",
                        source_uri="urn:shadowchain:synthetic:grant",
                    ),
                    valid_from=OBSERVED,
                    source_event_id=records[2].id,
                )
            )
        edges = repository.relationships(records[0].ref, RelationshipKind.CAN_ASSUME)
        if len(edges) != 1:
            raise RuntimeError("Persisted verification relationship is missing or duplicated")
        print(
            json.dumps(
                {
                    "nodes": [record.model_dump(mode="json") for record in records],
                    "relationships": [edge.model_dump(mode="json") for edge in edges],
                },
                sort_keys=True,
            )
        )
    finally:
        database.close()


if __name__ == "__main__":
    main()

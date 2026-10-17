from datetime import UTC, datetime

from app.models.graph import NodeInput, NodeKind, Provenance, epoch_us

START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 1, 2, tzinfo=UTC)


def provenance(source_id: str) -> Provenance:
    return Provenance(
        source="synthetic-test",
        source_id=source_id,
        observed_at=START,
        collector="pytest",
        source_uri="urn:shadowchain:test:" + source_id,
    )


def node_input(kind: NodeKind, source_id: str | None = None) -> NodeInput:
    attributes = {}
    if kind == NodeKind.SECURITY_EVENT:
        attributes = {
            "event_type": "AssumeRole",
            "event_time": epoch_us(START),
            "content_sha256": "a" * 64,
        }
    elif kind == NodeKind.MEMORY_SPACE:
        attributes = {"name": "SYNTHETIC memory space", "revision": 0, "lock_version": 0}
    elif kind == NodeKind.EVIDENCE:
        attributes = {"evidence_type": "cloudtrail", "content_sha256": "b" * 64}
    return NodeInput(
        kind=kind,
        provenance=provenance(source_id or kind.value),
        account_id="111111111111",
        attributes=attributes,
    )

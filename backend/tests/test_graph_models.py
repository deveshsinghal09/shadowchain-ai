from datetime import datetime

import pytest
from pydantic import ValidationError

from app.db.cypher import node_label, relationship_type, stable_id
from app.models.graph import NodeInput, NodeKind, RelationshipInput, RelationshipKind, epoch_us
from tests.graph_helpers import END, START, node_input, provenance


@pytest.mark.parametrize("kind", list(NodeKind))
def test_every_required_node_kind_validates(kind: NodeKind) -> None:
    assert node_input(kind).kind == kind


@pytest.mark.parametrize("value", ["Identity) DETACH DELETE n //", "`Identity`", "", "Unknown"])
def test_node_label_injection_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        node_label(value)


def test_relationship_type_injection_is_rejected() -> None:
    with pytest.raises(ValueError):
        relationship_type("CAN_ASSUME]->() DELETE n //")


@pytest.mark.parametrize(
    "attribute", ["api_key", "password", "aws_access_key_id", "tenant_id", "id"]
)
def test_reserved_and_credential_properties_are_rejected(attribute: str) -> None:
    with pytest.raises(ValidationError):
        NodeInput(kind=NodeKind.AGENT, provenance=provenance("agent"), attributes={attribute: "x"})


def test_known_credential_material_is_rejected_in_text() -> None:
    with pytest.raises(ValidationError):
        NodeInput(
            kind=NodeKind.AGENT,
            provenance=provenance("agent"),
            attributes={"name": "AKIA" + "A" * 16},
        )


def test_domain_properties_enforce_types_and_confidence_range() -> None:
    with pytest.raises(ValidationError):
        NodeInput(kind=NodeKind.AGENT, provenance=provenance("agent"), attributes={"name": 1})
    with pytest.raises(ValidationError):
        NodeInput(
            kind=NodeKind.AGENT_MEMORY,
            provenance=provenance("memory"),
            attributes={"confidence": 1.5},
        )


@pytest.mark.parametrize(
    "uri", ["https://user:secret@example.com/evidence", "s3://bucket/key?token=secret"]
)
def test_credentials_and_signed_uris_are_rejected(uri: str) -> None:
    values = provenance("evidence").model_dump()
    values["source_uri"] = uri
    with pytest.raises(ValidationError):
        type(provenance("evidence"))(**values)


def test_timestamp_precision_is_microseconds_and_naive_dates_are_rejected() -> None:
    assert epoch_us(START.replace(microsecond=1)) - epoch_us(START) == 1
    with pytest.raises(ValueError):
        epoch_us(datetime(2026, 1, 1))


def test_stable_id_has_unambiguous_boundaries_and_tenant_scope() -> None:
    assert stable_id("tenant-a", "ab", "c") != stable_id("tenant-a", "a", "bc")
    assert stable_id("tenant-a", "event") != stable_id("tenant-b", "event")
    assert stable_id("tenant-a", "event") == stable_id("tenant-a", "event")


def test_temporal_input_rejects_empty_intervals_and_missing_event() -> None:
    ref = {"kind": "Role", "id": "00000000-0000-0000-0000-000000000001"}
    with pytest.raises(ValidationError):
        RelationshipInput(
            kind=RelationshipKind.CAN_ASSUME,
            source=ref,
            target=ref,
            provenance=provenance("edge"),
            valid_from=START,
        )
    with pytest.raises(ValidationError):
        RelationshipInput(
            kind=RelationshipKind.BELONGS_TO,
            source=ref,
            target=ref,
            provenance=provenance("edge"),
            valid_from=END,
            valid_to=START,
        )

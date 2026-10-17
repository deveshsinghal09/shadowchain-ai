from datetime import datetime

import pytest
from pydantic import ValidationError

from app.demo.dataset import dataset_definition, hour
from app.models.events import NodeSpec, Scenario, SyntheticSecurityEvent
from app.models.graph import NodeKind


def test_dataset_manifest_and_event_ids_are_deterministic() -> None:
    first, second = dataset_definition(), dataset_definition()
    assert first.manifest_sha256 == second.manifest_sha256
    assert first.events[Scenario.A][0].event_id == second.events[Scenario.A][0].event_id
    assert len({e.event_id for events in first.events.values() for e in events}) == 7


def test_source_id_reuse_keeps_id_but_content_change_changes_digest() -> None:
    event = dataset_definition().events[Scenario.A][0]
    changed = SyntheticSecurityEvent.model_validate(
        {**event.model_dump(), "event_type": "Different"}
    )
    assert event.event_id == changed.event_id
    assert event.content_sha256 != changed.content_sha256


@pytest.mark.parametrize(
    "overrides",
    [
        {"synthetic": False},
        {"dataset_version": "unrecognized"},
        {"occurred_at": datetime(2026, 10, 16, 10)},
        {"event_id": "00000000-0000-0000-0000-000000000000"},
        {"ingested_at": hour(10)},
        {"aws_access_key_id": "not-allowed"},
    ],
)
def test_event_input_rejects_invalid_or_server_owned_fields(overrides: dict) -> None:
    original = dataset_definition().events[Scenario.A][0].model_dump()
    with pytest.raises(ValidationError):
        SyntheticSecurityEvent.model_validate({**original, **overrides})


@pytest.mark.parametrize(
    "field", ["tenant_id", "synthetic", "event_id", "payload", "source_event_id"]
)
def test_node_specs_cannot_override_scope_and_provenance(field: str) -> None:
    with pytest.raises(ValidationError):
        NodeSpec(key="c/observation", kind=NodeKind.AGENT_MEMORY, attributes={field: "spoofed"})


def test_event_ordering_is_utc_and_not_local_offset_dependent() -> None:
    original = dataset_definition().events[Scenario.A][0]
    converted = SyntheticSecurityEvent.model_validate(
        {**original.model_dump(mode="json"), "occurred_at": "2026-10-16T15:30:00+05:30"}
    )
    assert converted.occurred_at == hour(10)
    assert converted.content_sha256 == original.content_sha256

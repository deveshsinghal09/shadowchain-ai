"""Only closed enum values enter syntax; all data enters SDK parameter maps."""

import hashlib
import json
import re
from uuid import NAMESPACE_URL, uuid5

from app.models.graph import NodeKind, RelationshipKind


def node_label(value: NodeKind | str) -> str:
    return NodeKind(value).value


def relationship_type(value: RelationshipKind | str) -> str:
    return RelationshipKind(value).value


def stable_id(*parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, json.dumps(["shadowchain:v1", *parts], ensure_ascii=True)))


def fingerprint(properties: dict[str, object]) -> str:
    payload = json.dumps(properties, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def creation_properties(
    properties: dict[str, object], prefix: str = "p"
) -> tuple[str, dict[str, object]]:
    """Build a literal CREATE shape with identifier validation and parameterized values.

    FalkorDB 4.x does not accept a map parameter as inlined CREATE properties.
    Repositories also enforce a closed property vocabulary before calling this utility.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_]*", prefix):
        raise ValueError("Invalid parameter namespace")
    fields, parameters = [], {}
    for key, value in properties.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise ValueError("Invalid property identifier")
        if value is not None:
            parameter = f"{prefix}_{key}"
            fields.append(f"{key}: ${parameter}")
            parameters[parameter] = value
    return "{" + ", ".join(fields) + "}", parameters

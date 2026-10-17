"""Derived views of graph-read state. No snapshots or authorization state are cached."""

from collections import Counter, defaultdict
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from typing import Any

from app.db.cypher import fingerprint
from app.models.events import DATASET_VERSION, Scenario
from app.models.graph import epoch_us
from app.schemas.demo import GraphSnapshot, PotentialPath, SnapshotComparison

OPERATIONAL_FIELDS = {
    "created_at",
    "updated_at",
    "ingested_at",
    "applied_revision",
    "generation",
    "fingerprint",
}


def semantic_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in OPERATIONAL_FIELDS}


def policy_paths(
    nodes: list[dict], edges: list[dict]
) -> tuple[list[PotentialPath], dict[str, list[str]]]:
    by_id = {node["id"]: node for node in nodes}
    outgoing: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for edge in edges:
        if (
            edge.get("verification_status") == "verified"
            and by_id[edge["source_node_id"]].get("is_current", True)
            and by_id[edge["target_node_id"]].get("is_current", True)
        ):
            outgoing[(edge["source_node_id"], edge["kind"])].append(edge)

    def effects(
        principal: str, target: str, action: str, *, trust: bool = False
    ) -> tuple[list[str], bool]:
        allows, deny = [], False
        for attachment in outgoing[(principal, "HAS_POLICY")]:
            policy = by_id[attachment["target_node_id"]]
            if policy.get("policy_type") != ("TrustPolicy" if trust else "IdentityPolicy"):
                continue
            for grant in outgoing[(policy["id"], "GRANTS")]:
                permission = by_id[grant["target_node_id"]]
                if not fnmatchcase(action.lower(), permission.get("action", "").lower()):
                    continue
                if not any(
                    edge["target_node_id"] == target
                    for edge in outgoing[(permission["id"], "TARGETS")]
                ):
                    continue
                if permission.get("effect") == "Deny":
                    deny = True
                elif permission.get("effect") == "Allow" and not permission.get("condition_sha256"):
                    # Unknown conditions cannot establish an allow in this conservative subset.
                    allows.append(permission["id"])
        return allows, deny

    resources = [
        n
        for n in nodes
        if n["kind"] == "Resource" and n.get("sensitive") and n.get("resource_type") == "s3:bucket"
    ]
    paths: list[PotentialPath] = []
    reachable: dict[str, list[str]] = {}
    for identity in sorted(
        (n for n in nodes if n["kind"] == "Identity"), key=lambda n: n["entity_key"]
    ):
        role_ids = set()
        pending = [(identity["id"], [identity["id"]], [])]
        while pending:
            principal, chain, traversed = pending.pop()
            for resource in resources:
                allowed, denied = effects(principal, resource["id"], "s3:GetObject")
                if allowed and not denied:
                    paths.append(
                        PotentialPath(
                            identity_key=identity["entity_key"],
                            resource_key=resource["entity_key"],
                            principal_chain=[by_id[p]["entity_key"] for p in chain],
                            relationship_ids=traversed,
                            permission_id=sorted(allowed)[0],
                        )
                    )
            for assumption in outgoing[(principal, "CAN_ASSUME")]:
                target = assumption["target_node_id"]
                if target in chain:
                    continue
                caller_allow, caller_deny = effects(principal, target, "sts:AssumeRole")
                trust_allow, trust_deny = effects(target, principal, "sts:AssumeRole", trust=True)
                if caller_allow and trust_allow and not caller_deny and not trust_deny:
                    role_ids.add(target)
                    pending.append((target, [*chain, target], [*traversed, assumption["id"]]))
        reachable[identity["entity_key"]] = sorted(by_id[p]["entity_key"] for p in role_ids)
    return sorted(
        paths, key=lambda p: (p.identity_key, p.resource_key, p.principal_chain)
    ), reachable


def build_snapshot(
    dataset: dict, all_nodes: list[dict], all_edges: list[dict], scenario: Scenario, at: datetime
) -> GraphSnapshot:
    instant = epoch_us(at)
    nodes = []
    for original in all_nodes:
        if (
            original["scenario_id"] not in {"baseline", scenario.value}
            or original["source_time"] > instant
        ):
            continue
        node = dict(original)
        node["is_current"] = node["valid_from"] <= instant and (
            "valid_to" not in node or instant < node["valid_to"]
        )
        nodes.append(node)
    nodes.sort(key=lambda n: n["id"])
    ids = {node["id"] for node in nodes}
    edges = sorted(
        (
            dict(edge)
            for edge in all_edges
            if edge["scenario_id"] in {"baseline", scenario.value}
            and edge["source_node_id"] in ids
            and edge["target_node_id"] in ids
            and edge["valid_from"] <= instant
            and ("valid_to" not in edge or instant < edge["valid_to"])
        ),
        key=lambda edge: edge["id"],
    )
    current = {node["logical_key"]: node for node in nodes if node["is_current"]}
    stale = []
    for edge in edges:
        justification = current.get(edge.get("justification_logical_key", ""))
        expired = edge.get("justification_expires_at", instant + 1) <= instant
        resolved = justification is not None and justification.get("status") not in {
            "open",
            "active",
        }
        if edge.get("justification_id") and (expired or resolved):
            stale.append(
                {
                    "relationship_id": edge["id"],
                    "edge_key": edge["edge_key"],
                    "justification_status": justification.get("status")
                    if justification
                    else "missing",
                    "business_window_expired": expired,
                    "grant_still_active": True,
                }
            )
    conflicts = [
        {
            "relationship_id": edge["id"],
            "source_id": edge["source_node_id"],
            "target_id": edge["target_node_id"],
            "status": "needs_investigation",
        }
        for edge in edges
        if edge["kind"] == "CONTRADICTS"
    ]
    paths, reachable = policy_paths(nodes, edges)
    digest = fingerprint(
        {
            "version": DATASET_VERSION,
            "scenario": scenario.value,
            "nodes": [semantic_record(n) for n in nodes],
            "relationships": [semantic_record(e) for e in edges],
            "stale_justifications": stale,
        }
    )
    return GraphSnapshot(
        scenario=scenario,
        as_of=at.astimezone(UTC),
        knowledge_revision=dataset["revision"],
        semantic_sha256=digest,
        counts=dict(sorted(Counter(n["kind"] for n in nodes).items())),
        nodes=nodes,
        relationships=edges,
        potential_sensitive_paths=paths,
        reachable_roles=reachable,
        stale_justifications=stale,
        memory_conflicts=conflicts,
    )


def compare_snapshots(before: GraphSnapshot, after: GraphSnapshot) -> SnapshotComparison:
    def changes(first: list[dict], second: list[dict]) -> tuple[list[str], list[str], list[str]]:
        a, b = (
            {r["id"]: semantic_record(r) for r in first},
            {r["id"]: semantic_record(r) for r in second},
        )
        return (
            sorted(b.keys() - a.keys()),
            sorted(a.keys() - b.keys()),
            sorted(k for k in a.keys() & b.keys() if a[k] != b[k]),
        )

    added_nodes, removed_nodes, changed_nodes = changes(before.nodes, after.nodes)
    added_edges, removed_edges, changed_edges = changes(before.relationships, after.relationships)
    first_paths = {p.model_dump_json(): p for p in before.potential_sensitive_paths}
    second_paths = {p.model_dump_json(): p for p in after.potential_sensitive_paths}
    return SnapshotComparison(
        scenario=before.scenario,
        before=before.as_of,
        after=after.as_of,
        before_sha256=before.semantic_sha256,
        after_sha256=after.semantic_sha256,
        nodes_added=added_nodes,
        nodes_removed=removed_nodes,
        nodes_changed=changed_nodes,
        relationships_added=added_edges,
        relationships_removed=removed_edges,
        relationships_changed=changed_edges,
        potential_paths_added=[
            second_paths[k] for k in sorted(second_paths.keys() - first_paths.keys())
        ],
        potential_paths_removed=[
            first_paths[k] for k in sorted(first_paths.keys() - second_paths.keys())
        ],
    )

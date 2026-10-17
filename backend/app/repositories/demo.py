"""Atomic synthetic event projection and reset, exclusively inside a bound graph tenant."""

import json
from typing import Any

from app.core.time import utc_now
from app.db.cypher import creation_properties, fingerprint, node_label, relationship_type, stable_id
from app.db.falkordb import GraphDatabase
from app.demo.dataset import BASE_TIME, HISTORY_TIME, DatasetDefinition
from app.models.events import (
    DATASET_VERSION,
    DEMO_ACCOUNTS,
    SOURCE,
    EdgeSpec,
    NodeSpec,
    Scenario,
    SyntheticSecurityEvent,
)
from app.models.graph import (
    NodeInput,
    NodeKind,
    NodeRef,
    Provenance,
    RelationshipInput,
    TenantScope,
    epoch_us,
)
from app.models.relationships import validate_endpoints
from app.repositories.graph import build_node_properties, build_relationship_properties
from app.services.event_errors import DemoMutationError, DemoNotSeeded, DemoVersionConflict


class MutationBuilder:
    """Transient query construction from graph-read state; never an authoritative store."""

    def __init__(
        self,
        tenant_id: str,
        scenario: str,
        occurred_at: Any,
        now: int,
        known_nodes: list[dict[str, Any]] | None = None,
    ) -> None:
        self.tenant_id, self.scenario, self.occurred_at, self.now = (
            tenant_id,
            scenario,
            occurred_at,
            now,
        )
        self.known = {node["entity_key"]: node for node in (known_nodes or [])}
        self.variables: dict[str, str] = {}
        self.matches: list[str] = []
        self.creates: list[str] = []
        self.updates: list[str] = []
        self.params: dict[str, Any] = {}
        self.new_nodes: list[dict[str, Any]] = []
        self.new_edges: list[dict[str, Any]] = []

    def add_node(self, spec: NodeSpec, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        if spec.key in self.known:
            raise DemoMutationError()
        observed = HISTORY_TIME if spec.key.startswith("history/") else self.occurred_at
        node = NodeInput(
            kind=spec.kind,
            account_id=spec.account_id,
            provenance=Provenance(
                source=SOURCE,
                source_id=spec.key,
                observed_at=observed,
                collector="synthetic-event-engine",
                source_uri="urn:shadowchain:" + spec.key,
            ),
            attributes={
                **spec.attributes,
                "synthetic": True,
                "dataset_version": DATASET_VERSION,
                "scenario_id": self.scenario,
                "entity_key": spec.key,
                "logical_key": spec.logical_key or spec.key,
                "source_time": epoch_us(observed),
                "valid_from": epoch_us(observed),
                **(extra or {}),
            },
        )
        properties = build_node_properties(self.tenant_id, node, self.now)
        variable = f"n{len(self.new_nodes)}"
        literal, bindings = creation_properties(properties, variable)
        self.params.update(bindings)
        self.creates.append(f"CREATE ({variable}:Entity:{node_label(spec.kind)} {literal}) ")
        self.variables[spec.key], self.known[spec.key] = variable, properties
        self.new_nodes.append(properties)
        return properties

    def endpoint(self, key: str) -> str:
        if key not in self.known:
            raise DemoMutationError()
        if self.known[key]["scenario_id"] not in {"baseline", self.scenario}:
            raise DemoMutationError()
        if key not in self.variables:
            variable = f"x{len(self.variables)}"
            props = self.known[key]
            self.params[variable + "_id"] = props["id"]
            self.matches.append(
                f"MATCH ({variable}:Entity:{node_label(props['kind'])} "
                f"{{tenant_id: $tenant_id, id: ${variable}_id, "
                "synthetic: true, dataset_version: $version}) "
            )
            self.variables[key] = variable
        return self.variables[key]

    def add_edge(self, spec: EdgeSpec, event_id: str, provenance_prefix: str) -> None:
        source_var, target_var = self.endpoint(spec.source_key), self.endpoint(spec.target_key)
        source, target = self.known[spec.source_key], self.known[spec.target_key]
        edge = RelationshipInput(
            kind=spec.kind,
            source=NodeRef(kind=source["kind"], id=source["id"]),
            target=NodeRef(kind=target["kind"], id=target["id"]),
            provenance=Provenance(
                source=SOURCE,
                source_id=provenance_prefix + "/" + spec.key,
                observed_at=self.occurred_at,
                collector="synthetic-event-engine",
                source_uri="urn:shadowchain:" + provenance_prefix + "/" + spec.key,
            ),
            valid_from=self.occurred_at,
            valid_to=spec.valid_to,
            source_event_id=event_id,
            confidence=1.0,
            verification_status="verified",
        )
        validate_endpoints(edge)
        properties = build_relationship_properties(self.tenant_id, edge, self.now)
        properties.update(
            synthetic=True,
            dataset_version=DATASET_VERSION,
            scenario_id=self.scenario,
            edge_key=spec.key,
        )
        if spec.justification_key:
            self.endpoint(spec.justification_key)
            justification = self.known[spec.justification_key]
            if justification["kind"] != NodeKind.INVESTIGATION:
                raise DemoMutationError()
            properties.update(
                justification_id=justification["id"],
                justification_logical_key=justification["logical_key"],
            )
        if spec.justification_expires_at:
            properties["justification_expires_at"] = epoch_us(spec.justification_expires_at)
        properties["fingerprint"] = fingerprint(
            {
                k: v
                for k, v in properties.items()
                if k not in {"created_at", "updated_at", "observed_at", "fingerprint"}
            }
        )
        prefix = f"r{len(self.new_edges)}"
        literal, bindings = creation_properties(properties, prefix)
        self.params.update(bindings)
        self.creates.append(
            f"CREATE ({source_var})-[{prefix}:{relationship_type(spec.kind)} "
            f"{literal}]->({target_var}) "
        )
        self.new_edges.append(properties)

    def close_node(self, key: str, event_id: str) -> None:
        node = self.known.get(key)
        if (
            node is None
            or node.get("scenario_id") != self.scenario
            or "valid_to" in node
            or node["valid_from"] >= epoch_us(self.occurred_at)
        ):
            raise DemoMutationError()
        variable = self.endpoint(key)
        self.updates.append(
            f"SET {variable}.valid_to = $source_time, "
            f"{variable}.closed_by_event_id = $event_graph_id, {variable}.updated_at = $now "
        )

    def close_edge(self, key: str, edges: list[dict[str, Any]]) -> None:
        found = [edge for edge in edges if edge.get("edge_key") == key]
        if (
            len(found) != 1
            or found[0].get("scenario_id") != self.scenario
            or "valid_to" in found[0]
            or found[0]["valid_from"] >= epoch_us(self.occurred_at)
        ):
            raise DemoMutationError()
        prefix = f"z{len(self.updates)}"
        self.params[prefix + "_id"] = found[0]["id"]
        self.matches.append(
            f"MATCH (za{prefix}:Entity {{tenant_id: $tenant_id}})"
            f"-[{prefix}:{relationship_type(found[0]['kind'])} "
            f"{{tenant_id: $tenant_id, id: ${prefix}_id, scenario_id: $scenario}}]->"
            f"(zb{prefix}:Entity {{tenant_id: $tenant_id}}) "
        )
        self.updates.append(
            f"SET {prefix}.valid_to = $source_time, {prefix}.updated_at = $now, "
            f"{prefix}.closed_by_event_id = $event_graph_id "
        )

    def text(self) -> str:
        # All existing-node matches precede ALL domain mutations, avoiding partial
        # application if an endpoint is absent. New-node variables are created first.
        return "".join(self.matches + self.creates + self.updates)


class DemoRepository:
    def __init__(self, database: GraphDatabase, scope: TenantScope) -> None:
        self.database, self.scope = database, scope
        self.dataset_id = stable_id("synthetic-dataset", scope.tenant_id)

    def stream_id(self, scenario: Scenario) -> str:
        return stable_id("synthetic-stream", self.scope.tenant_id, scenario.value)

    def query(self, cypher: str, parameters: dict[str, Any], *, read_only: bool = False) -> list:
        self.database.initialize_schema()
        bindings = {
            **parameters,
            "tenant_id": self.scope.tenant_id,
            "dataset_id": self.dataset_id,
            "version": DATASET_VERSION,
        }
        if not read_only:
            return self.database.mutate(
                cypher,
                bindings,
                tenant_id=self.scope.tenant_id,
                event_type="security.event.ingested"
                if "event_graph_id" in parameters
                else ("demo.reset" if "scenario" in parameters else "demo.seeded"),
                entity_ids=[parameters.get("event_graph_id") or self.dataset_id],
            )
        return self.database.query(
            cypher,
            {
                **parameters,
                "tenant_id": self.scope.tenant_id,
                "dataset_id": self.dataset_id,
                "version": DATASET_VERSION,
            },
            read_only=read_only,
            idempotent=not read_only,
        )

    def graph(self) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        rows = self.query(
            "MATCH (d:DemoDataset {id: $dataset_id, tenant_id: $tenant_id, seeded: true}) "
            "OPTIONAL MATCH (n:Entity {tenant_id: $tenant_id, "
            "synthetic: true, dataset_version: $version}) "
            "WITH d, collect(properties(n)) AS nodes "
            "OPTIONAL MATCH (a:Entity {tenant_id: $tenant_id})"
            "-[r {tenant_id: $tenant_id, synthetic: true, dataset_version: $version}]->"
            "(b:Entity {tenant_id: $tenant_id}) "
            "RETURN properties(d), nodes, collect(properties(r))",
            {},
            read_only=True,
        )
        if not rows:
            raise DemoNotSeeded()
        return (
            rows[0][0],
            [n for n in rows[0][1] if n is not None],
            [r for r in rows[0][2] if r is not None],
        )

    def state(self, scenario: Scenario) -> tuple[dict[str, Any], dict[str, Any]]:
        rows = self.query(
            "MATCH (d:DemoDataset {id: $dataset_id, tenant_id: $tenant_id, seeded: true}), "
            "(s:DemoStream {id: $stream_id, tenant_id: $tenant_id}) "
            "RETURN properties(d), properties(s)",
            {"stream_id": self.stream_id(scenario)},
            read_only=True,
        )
        if not rows:
            raise DemoNotSeeded()
        return rows[0][0], rows[0][1]

    def seed(self, definition: DatasetDefinition) -> bool:
        now = epoch_us(utc_now())
        builder = MutationBuilder(self.scope.tenant_id, "baseline", BASE_TIME, now)
        event = builder.add_node(
            NodeSpec(
                key="baseline/event",
                kind=NodeKind.SECURITY_EVENT,
                account_id=DEMO_ACCOUNTS[0],
                attributes={
                    "event_type": "SyntheticBaselineImported",
                    "event_time": epoch_us(BASE_TIME),
                    "content_sha256": definition.manifest_sha256,
                },
            )
        )
        for spec in definition.nodes:
            builder.add_node(spec)
        for spec in definition.edges:
            builder.add_edge(spec, event["id"], "baseline")
        streams = []
        for index, scenario in enumerate(Scenario):
            literal, bindings = creation_properties(
                {
                    "id": self.stream_id(scenario),
                    "tenant_id": self.scope.tenant_id,
                    "scenario_id": scenario.value,
                    "sequence": 0,
                    "watermark": epoch_us(BASE_TIME),
                    "generation": 0,
                },
                f"s{index}",
            )
            builder.params.update(bindings)
            streams.append(f"CREATE (:DemoStream {literal}) ")
        rows = self.query(
            "MERGE (d:DemoDataset {id: $dataset_id, tenant_id: $tenant_id}) "
            "ON CREATE SET d.revision = 0, d.seeded = false, d.lock_version = 0, "
            "d.dataset_version = $version, d.manifest_sha256 = $manifest, d.seeded_at = $now "
            "SET d.lock_version = d.lock_version + 1 WITH d WHERE d.seeded = false "
            "AND d.dataset_version = $version AND d.manifest_sha256 = $manifest "
            + builder.text()
            + "".join(streams)
            + "SET d.seeded = true, d.revision = 1 RETURN d.revision",
            {**builder.params, "manifest": definition.manifest_sha256, "now": now},
        )
        if not rows:
            stored, _, _ = self.graph()
            if (
                stored["dataset_version"] != DATASET_VERSION
                or stored["manifest_sha256"] != definition.manifest_sha256
            ):
                raise DemoVersionConflict()
        return bool(rows)

    def apply(
        self,
        event: SyntheticSecurityEvent,
        dataset: dict[str, Any],
        stream: dict[str, Any],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        now = epoch_us(utc_now())
        builder = MutationBuilder(
            self.scope.tenant_id, event.scenario.value, event.occurred_at, now, nodes
        )
        event_node = builder.add_node(
            NodeSpec(
                key="event/" + event.event_id,
                kind=NodeKind.SECURITY_EVENT,
                account_id=event.account_id,
                attributes={
                    "event_type": event.event_type,
                    "event_time": epoch_us(event.occurred_at),
                    "content_sha256": event.content_sha256,
                },
            ),
            {
                "event_id": event.event_id,
                "sequence": event.sequence,
                "ingested_at": now,
                "applied_revision": dataset["revision"] + 1,
                "generation": stream["generation"],
                "payload": json.dumps(event.model_dump(mode="json"), sort_keys=True),
            },
        )
        for spec in event.nodes:
            builder.add_node(spec, {"source_event_id": event_node["id"]})
        for spec in event.edges:
            builder.add_edge(spec, event_node["id"], event.source_id)
        for key in event.close_nodes:
            builder.close_node(key, event_node["id"])
        for key in event.close_edges:
            builder.close_edge(key, edges)
        event_var = builder.variables["event/" + event.event_id]
        rows = self.query(
            "MATCH (d:DemoDataset {id: $dataset_id, tenant_id: $tenant_id, seeded: true}), "
            "(s:DemoStream {id: $stream_id, tenant_id: $tenant_id}) "
            "SET d.lock_version = d.lock_version + 1 WITH d, s "
            "WHERE d.revision = $expected_revision AND s.sequence = $previous_sequence "
            "AND s.generation = $generation AND d.dataset_version = $version "
            + builder.text()
            + "SET d.revision = d.revision + 1, s.sequence = $sequence, "
            "s.watermark = $source_time, s.last_event_id = $event_id "
            f"RETURN properties({event_var})",
            {
                **builder.params,
                "stream_id": self.stream_id(event.scenario),
                "expected_revision": dataset["revision"],
                "previous_sequence": stream["sequence"],
                "generation": stream["generation"],
                "sequence": event.sequence,
                "source_time": epoch_us(event.occurred_at),
                "event_id": event.event_id,
                "event_graph_id": event_node["id"],
                "now": now,
                "scenario": event.scenario.value,
            },
        )
        return rows[0][0] if rows else None

    def reset(self, scenario: Scenario, dataset: dict[str, Any], stream: dict[str, Any]) -> bool:
        rows = self.query(
            "MATCH (d:DemoDataset {id: $dataset_id, tenant_id: $tenant_id, seeded: true}), "
            "(s:DemoStream {id: $stream_id, tenant_id: $tenant_id}) "
            "SET d.lock_version = d.lock_version + 1 WITH d, s "
            "WHERE d.revision = $revision AND s.generation = $generation "
            "OPTIONAL MATCH (a:Entity {tenant_id: $tenant_id})-[r {tenant_id: $tenant_id, "
            "scenario_id: $scenario, synthetic: true, dataset_version: $version}]->"
            "(b:Entity {tenant_id: $tenant_id}) "
            "DELETE r WITH DISTINCT d, s "
            "OPTIONAL MATCH (n:Entity {tenant_id: $tenant_id, scenario_id: $scenario, "
            "synthetic: true, dataset_version: $version}) "
            "DETACH DELETE n WITH DISTINCT d, s "
            "SET s.sequence = 0, s.watermark = $baseline, s.generation = s.generation + 1, "
            "d.revision = d.revision + 1 RETURN d.revision",
            {
                "stream_id": self.stream_id(scenario),
                "scenario": scenario.value,
                "revision": dataset["revision"],
                "generation": stream["generation"],
                "baseline": epoch_us(BASE_TIME),
            },
        )
        return bool(rows)

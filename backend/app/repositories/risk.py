"""Tenant-bound Cypher traversal and immutable, graph-backed risk history."""

import json
from datetime import datetime
from typing import Any

from app.core.time import utc_now
from app.db.cypher import creation_properties, fingerprint, stable_id
from app.db.errors import GraphConflict, GraphNotFound
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
from app.models.relationships import validate_endpoints
from app.repositories.graph import build_node_properties, build_relationship_properties
from app.schemas.risk import ENGINE_VERSION, DiscoveryResult, FindingEvidence, RiskOptions
from app.services.risk_errors import RiskLimitExceeded

MAX_CANDIDATES = 250
MAX_CONTEXT_RECORDS = 2000
MAX_TIME_POINTS = 32


def path_predicate(
    variable: str,
    *,
    require_verified: bool = True,
    include_closed: bool = False,
) -> str:
    """Identifiers here are code constants, never request strings."""
    return (
        f"all(n IN nodes({variable}) WHERE n.tenant_id = $tenant_id "
        "AND ($scenario IS NULL OR n.scenario_id IN ['baseline', $scenario]) "
        "AND coalesce(n.source_time, n.valid_from, n.observed_at) <= at "
        "AND (n.valid_from IS NULL OR n.valid_from <= at) "
        + ("" if include_closed else "AND (n.valid_to IS NULL OR at < n.valid_to) ")
        + ") "
        f"AND all(e IN relationships({variable}) WHERE e.tenant_id = $tenant_id "
        "AND ($scenario IS NULL OR e.scenario_id IN ['baseline', $scenario]) "
        "AND e.valid_from <= at "
        + ("" if include_closed else "AND (e.valid_to IS NULL OR at < e.valid_to) ")
        + ("AND e.verification_status = 'verified' " if require_verified else "")
        + "AND NOT e.id IN excluded)"
    )


def collect_path(variable: str, *, mark_active: bool = False) -> str:
    return (
        f"collect(CASE WHEN {variable} IS NULL THEN null ELSE "
        f"{{nodes: [n IN nodes({variable}) | properties(n)], "
        f"edges: [e IN relationships({variable}) | properties(e)]"
        + (
            ", active: (" + path_predicate(variable, require_verified=False) + ")"
            if mark_active
            else ""
        )
        + "} END)"
    )


def traversal_query(max_role_hops: int) -> str:
    # Cypher range bounds are syntax. Only one of these five integer constants can
    # enter query text; every ID, scope, time, context and exclusion is a parameter.
    if type(max_role_hops) is not int or max_role_hops not in range(5):
        raise ValueError("Traversal depth must be an integer between zero and four")
    keep = "view_key, at, excluded, candidates"
    query = (
        "UNWIND $views AS view WITH view.key AS view_key, view.at AS at, "
        "view.excluded AS excluded "
        "OPTIONAL MATCH p=(identity:Entity:Identity {tenant_id: $tenant_id})"
        f"-[:CAN_ASSUME*0..{max_role_hops}]->(principal:Entity)"
        "-[:HAS_POLICY]->(:Entity:Policy)-[:GRANTS]->(:Entity:Permission)"
        "-[:TARGETS]->(resource:Entity:Resource {tenant_id: $tenant_id}) "
        "WHERE ($sensitive_only = false OR resource.sensitive = true) "
        "AND ($identity_id IS NULL OR identity.id = $identity_id) "
        "AND principal.kind IN ['Identity', 'Role'] AND " + path_predicate("p") + " "
        "WITH view_key, at, excluded, "
        + collect_path("p")
        + f"[0..{MAX_CANDIDATES + 1}] AS candidates "
        f"WITH {keep}, reduce(ids = [], c IN candidates | ids + "
        "[n IN c.nodes | n.id]) AS endpoints "
        "OPTIONAL MATCH lineage=(n:Entity {tenant_id: $tenant_id})"
        "-[:BELONGS_TO*1..3]->(ancestor:Entity {tenant_id: $tenant_id}) "
        "WHERE n.id IN endpoints AND ancestor.kind IN ['CloudAccount', 'CloudOrganization'] "
        "AND " + path_predicate("lineage", require_verified=False) + " "
        f"WITH {keep}, endpoints, "
        + collect_path("lineage")
        + f"[0..{MAX_CONTEXT_RECORDS + 1}] AS lineages "
        f"WITH {keep}, endpoints, lineages, endpoints + "
        "reduce(ids = [], l IN lineages | ids + [n IN l.nodes | n.id]) AS owners "
    )
    keep += ", endpoints, lineages, owners"
    for variable, pattern, extra in (
        (
            "attachment",
            "(owner:Entity {tenant_id: $tenant_id})-[:HAS_POLICY]->(:Entity:Policy)",
            "owner.id IN owners",
        ),
        (
            "statement",
            "(owner:Entity {tenant_id: $tenant_id})-[:HAS_POLICY]->(:Entity:Policy)"
            "-[:GRANTS]->(:Entity:Permission)-[:TARGETS]->(:Entity)",
            "owner.id IN owners",
        ),
        (
            "proof",
            "(:Entity:Evidence {tenant_id: $tenant_id})-[:SUPPORTS]->"
            "(subject:Entity {tenant_id: $tenant_id})",
            "subject.id IN endpoints",
        ),
    ):
        plural = {"attachment": "attachments", "statement": "statements", "proof": "proofs"}[
            variable
        ]
        query += (
            f"OPTIONAL MATCH {variable}={pattern} WHERE {extra} AND "
            + path_predicate(
                variable, require_verified=variable == "proof", include_closed=variable != "proof"
            )
            + f" WITH {keep}, "
            + collect_path(variable, mark_active=variable != "proof")
            + f"[0..{MAX_CONTEXT_RECORDS + 1}] AS {plural} "
        )
        keep += f", {plural}"
    query += (
        f"WITH {keep}, reduce(es = [], c IN candidates + statements + lineages + proofs "
        "| es + c.edges) AS all_edges "
        "OPTIONAL MATCH (j:Entity:Investigation {tenant_id: $tenant_id}) "
        "WHERE ($scenario IS NULL OR j.scenario_id IN ['baseline', $scenario]) "
        "AND coalesce(j.valid_from, j.observed_at) <= at "
        "AND (j.valid_to IS NULL OR at < j.valid_to) "
        "AND any(e IN all_edges WHERE e.justification_id = j.id "
        "OR e.justification_logical_key = j.logical_key) "
        f"WITH {keep}, all_edges, collect(properties(j)) AS justifications "
        "OPTIONAL MATCH (event:Entity:SecurityEvent {tenant_id: $tenant_id}) "
        "WHERE ($scenario IS NULL OR event.scenario_id IN ['baseline', $scenario]) "
        "AND event.event_time <= at "
        "AND any(e IN all_edges WHERE e.source_event_id = event.id "
        "OR e.closed_by_event_id = event.id) "
        f"WITH {keep}, justifications, collect(properties(event)) AS events "
        "OPTIONAL MATCH (inventory:Entity {tenant_id: $tenant_id}) "
        "WHERE inventory.kind IN ['Identity', 'Role', 'Resource', 'Policy', 'Permission', "
        "'CloudAccount', 'CloudOrganization', 'SecurityEvent'] "
        "AND ($scenario IS NULL OR inventory.scenario_id IN ['baseline', $scenario]) "
        "AND (CASE WHEN inventory.kind = 'SecurityEvent' THEN inventory.event_time ELSE "
        "coalesce(inventory.source_time, inventory.valid_from, inventory.observed_at) END) <= at "
        "AND (inventory.valid_to IS NULL OR at < inventory.valid_to) "
        "RETURN view_key, at, candidates, lineages, attachments, statements, proofs, "
        "justifications, events, {modeled_node_count: count(inventory), "
        "source_event_count: sum(CASE WHEN inventory.kind = 'SecurityEvent' THEN 1 ELSE 0 END)} "
        "AS scope_stats ORDER BY view_key"
    )
    return query


class RiskRepository:
    def __init__(self, database: GraphDatabase, scope: TenantScope) -> None:
        self.database, self.scope = database, scope

    def read(self, query: str, params: dict) -> list:
        return self.database.query(
            query, {**params, "tenant_id": self.scope.tenant_id}, read_only=True
        )

    def load_views(
        self, options: RiskOptions, views: list[dict], *, include_non_sensitive: bool = False
    ) -> dict[str, dict]:
        if not 1 <= len(views) <= MAX_TIME_POINTS:
            raise RiskLimitExceeded()
        rows = self.read(
            traversal_query(options.max_role_hops),
            {
                "views": views,
                "scenario": options.scenario.value if options.scenario else None,
                "identity_id": options.identity_id,
                "sensitive_only": not include_non_sensitive,
            },
        )
        results = {}
        for (
            key,
            at,
            candidates,
            lineages,
            attachments,
            statements,
            proofs,
            justifications,
            events,
            scope_stats,
        ) in rows:
            if len(candidates) > MAX_CANDIDATES or any(
                len(records) > MAX_CONTEXT_RECORDS
                for records in (lineages, attachments, statements, proofs)
            ):
                raise RiskLimitExceeded()
            results[key] = {
                "at": at,
                "candidates": candidates,
                "lineages": lineages,
                "attachments": attachments,
                "statements": statements,
                "proofs": proofs,
                "justifications": justifications,
                "events": events,
                "scope_stats": scope_stats,
            }
        return results

    def event_history(self, options: RiskOptions, before: datetime, after: datetime) -> list[dict]:
        rows = self.read(
            "MATCH (event:Entity:SecurityEvent {tenant_id: $tenant_id}) "
            "WHERE event.event_time > $before AND event.event_time <= $after "
            "AND ($scenario IS NULL OR event.scenario_id IN ['baseline', $scenario]) "
            "RETURN properties(event) ORDER BY event.event_time, event.id LIMIT 33",
            {
                "before": epoch_us(before),
                "after": epoch_us(after),
                "scenario": options.scenario.value if options.scenario else None,
            },
        )
        if len(rows) > MAX_TIME_POINTS - 2:
            raise RiskLimitExceeded()
        return [row[0] for row in rows]

    def validate_exclusions(self, ids: list[str], options: RiskOptions) -> None:
        rows = self.read(
            "MATCH (a:Entity {tenant_id: $tenant_id})"
            "-[r {tenant_id: $tenant_id}]->(b:Entity {tenant_id: $tenant_id}) "
            "WHERE r.id IN $ids AND ($scenario IS NULL OR "
            "(a.scenario_id IN ['baseline', $scenario] AND "
            "b.scenario_id IN ['baseline', $scenario] AND "
            "r.scenario_id IN ['baseline', $scenario])) RETURN r.id",
            {"ids": ids, "scenario": options.scenario.value if options.scenario else None},
        )
        if set(ids) != {row[0] for row in rows}:
            raise GraphNotFound()

    def persist(self, result: DiscoveryResult, records: dict[str, dict]) -> DiscoveryResult:
        """One idempotent statement stores the assessment AND every evidence snapshot."""
        try:
            return self.saved(result.analysis_id)
        except GraphNotFound:
            pass
        stored = result.model_copy(update={"persisted": True})
        encoded = stored.model_dump_json()
        if len(encoded.encode()) > 2_000_000 or len(records) > MAX_CONTEXT_RECORDS:
            raise RiskLimitExceeded()
        now = epoch_us(utc_now())
        at = epoch_us(result.at)
        params: dict[str, Any] = {
            "analysis_id": result.analysis_id,
            "tenant_id": self.scope.tenant_id,
            "result": encoded,
            "now": now,
            "at": at,
            "engine": ENGINE_VERSION,
            "digest": result.context_sha256,
        }
        creates, matches = [], []

        def create_node(node: NodeInput, variable: str) -> dict:
            props = build_node_properties(self.scope.tenant_id, node, now)
            literal, bindings = creation_properties(props, variable)
            params.update(bindings)
            creates.append(f"CREATE ({variable}:Entity:{node.kind.value} {literal}) ")
            return props

        def provenance(source_id: str) -> Provenance:
            return Provenance(
                source="shadowchain.risk.v1",
                source_id=result.analysis_id + "/" + source_id,
                observed_at=result.at,
                collector=ENGINE_VERSION,
                source_uri="urn:shadowchain:risk:" + result.analysis_id + ":" + source_id,
            )

        def create_link(source: dict, target: dict, source_var: str, target_var: str, index: int):
            edge = RelationshipInput(
                kind=RelationshipKind.SUPPORTS,
                source=NodeRef(kind=NodeKind.EVIDENCE, id=source["id"]),
                target=NodeRef(kind=NodeKind.RISK_FINDING, id=target["id"]),
                provenance=provenance("support/" + str(index)),
                valid_from=result.at,
                verification_status="verified",
            )
            props = build_relationship_properties(self.scope.tenant_id, edge, now)
            literal, bindings = creation_properties(props, "s" + str(index))
            params.update(bindings)
            creates.append(f"CREATE ({source_var})-[:SUPPORTS {literal}]->({target_var}) ")

        def optional_link(
            source: dict,
            source_var: str,
            target_id: str,
            target_kind: NodeKind,
            kind: RelationshipKind,
            index: int,
        ) -> None:
            variable = "original" + str(index)
            params[variable + "_id"] = target_id
            matches.append(
                f"OPTIONAL MATCH ({variable}:Entity:{target_kind.value} "
                f"{{tenant_id: $tenant_id, id: ${variable}_id}}) "
            )
            edge = RelationshipInput(
                kind=kind,
                source=NodeRef(kind=source["kind"], id=source["id"]),
                target=NodeRef(kind=target_kind, id=target_id),
                provenance=provenance("original/" + str(index)),
                valid_from=result.at,
                verification_status="verified",
            )
            validate_endpoints(edge)
            props = build_relationship_properties(self.scope.tenant_id, edge, now)
            literal, bindings = creation_properties(props, "o" + str(index))
            params.update(bindings)
            # A synthetic reset may remove original source records. Immutable evidence
            # copies still persist; links to originals are created only when present.
            creates.append(
                f"FOREACH (target IN CASE WHEN {variable} IS NULL THEN [] ELSE [{variable}] END | "
                f"CREATE ({source_var})-[:{kind.value} {literal}]->(target)) "
            )

        evidence_nodes = {}
        original_index = 0
        # These immutable copies survive source interval changes and synthetic reset.
        for index, (record_id, record) in enumerate(sorted(records.items())):
            variable = "e" + str(index)
            record_json = json.dumps(record, sort_keys=True)
            if len(record_json) > 16384:
                raise RiskLimitExceeded()
            props = create_node(
                NodeInput(
                    kind=NodeKind.EVIDENCE,
                    provenance=provenance("record/" + record_id),
                    attributes={
                        "evidence_type": "risk-graph-record-snapshot",
                        "record_id": record_id,
                        "summary": "Graph evidence observed by deterministic risk analysis",
                        "content_sha256": fingerprint(record),
                        "record_json": record_json,
                        "analysis_id": result.analysis_id,
                    },
                ),
                variable,
            )
            evidence_nodes[record_id] = (variable, props)
            source_event = record.get("source_event_id")
            if record.get("kind") == "SecurityEvent":
                source_event = record_id
            if source_event:
                optional_link(
                    props,
                    variable,
                    source_event,
                    NodeKind.SECURITY_EVENT,
                    RelationshipKind.DERIVED_FROM,
                    original_index,
                )
                original_index += 1
        link_index = 0
        for index, finding in enumerate(result.findings):
            variable = "f" + str(index)
            props = create_node(
                NodeInput(
                    kind=NodeKind.RISK_FINDING,
                    provenance=provenance("path/" + finding.path_id),
                    attributes={
                        "title": "POTENTIAL ATTACK PATH",
                        "severity": finding.score.severity,
                        "status": "potential",
                        "summary": finding.explanation,
                        "analysis_id": result.analysis_id,
                        "path_id": finding.path_id,
                        "risk_score": finding.score.value,
                        "assessed_at": at,
                        "engine_version": ENGINE_VERSION,
                    },
                ),
                variable,
            )
            if props["id"] != finding.finding_id:
                raise ValueError("Finding ID must match the graph identity")
            for subject in [finding.identity, *finding.roles, finding.resource]:
                optional_link(
                    props,
                    variable,
                    subject["id"],
                    NodeKind(subject["kind"]),
                    RelationshipKind.AFFECTS,
                    original_index,
                )
                original_index += 1
            for record_id in sorted(records):
                evidence_var, evidence_props = evidence_nodes[record_id]
                create_link(evidence_props, props, evidence_var, variable, link_index)
                link_index += 1
        self.database.initialize_schema()
        literal, bindings = creation_properties(
            {
                "id": result.analysis_id,
                "tenant_id": self.scope.tenant_id,
                "complete": True,
                "created_at": now,
                "observed_at": at,
                "source": "shadowchain.risk.v1",
                "source_id": result.analysis_id,
                "collector": ENGINE_VERSION,
                "source_uri": "urn:shadowchain:risk:" + result.analysis_id,
                "kind": "RiskAnalysis",
                "updated_at": now,
                "fingerprint": result.context_sha256,
                "result_json": encoded,
            },
            "analysis",
        )
        params.update(bindings)
        try:
            self.database.mutate(
                "".join(matches)
                + f"CREATE (a:RiskAnalysis {literal}) "
                + "".join(creates)
                + "RETURN a.id",
                params,
                idempotent=True,
                tenant_id=self.scope.tenant_id,
                event_type="risk.analysis.completed",
                entity_ids=[f.finding_id for f in result.findings],
            )
        except GraphConflict as exc:
            # Concurrent analysis or a lost write acknowledgement: only a complete
            # committed immutable analysis with this exact identity can reconcile.
            try:
                return self.saved(result.analysis_id)
            except GraphNotFound:
                raise exc from None
        return self.saved(result.analysis_id)

    def saved(self, analysis_id: str) -> DiscoveryResult:
        rows = self.read(
            "MATCH (a:RiskAnalysis {tenant_id: $tenant_id, id: $id, complete: true}) "
            "RETURN a.result_json",
            {"id": analysis_id},
        )
        if not rows:
            raise GraphNotFound()
        return DiscoveryResult.model_validate_json(rows[0][0])

    def evidence(self, finding_id: str) -> FindingEvidence:
        rows = self.read(
            "MATCH (f:Entity:RiskFinding {tenant_id: $tenant_id, id: $id}) "
            "MATCH (e:Entity:Evidence {tenant_id: $tenant_id})"
            "-[s:SUPPORTS {tenant_id: $tenant_id}]->(f) "
            "WITH f,e ORDER BY e.id LIMIT 2001 "
            "RETURN f.analysis_id, collect(properties(e))",
            {"id": finding_id},
        )
        if not rows:
            raise GraphNotFound()
        if len(rows[0][1]) > MAX_CONTEXT_RECORDS:
            raise RiskLimitExceeded()
        analysis = self.saved(rows[0][0])
        finding = next((f for f in analysis.findings if f.finding_id == finding_id), None)
        if finding is None:
            raise GraphNotFound()
        return FindingEvidence(
            analysis=analysis,
            finding=finding,
            evidence=[
                {**props, "record": json.loads(props["record_json"])}
                for props in sorted(rows[0][1], key=lambda e: e["id"])
            ],
        )

    def finding_id(self, analysis_id: str, path_id: str) -> str:
        return stable_id(
            self.scope.tenant_id,
            "RiskFinding",
            "",
            "shadowchain.risk.v1",
            analysis_id + "/path/" + path_id,
        )

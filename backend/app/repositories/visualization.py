"""Bounded, tenant-filtered graph projections. No cloud authorization verdict is inferred."""

from app.core.errors import AppError
from app.core.time import utc_now
from app.models.graph import epoch_us, from_epoch_us
from app.repositories.live import CursorConflict, LiveRepository
from app.schemas.contract import (
    DashboardSummary,
    GraphDifference,
    GraphTimeline,
    TimelineEvent,
    VisualEdge,
    VisualNode,
    VisualSnapshot,
)

DISPLAY_FIELDS = {
    "display_name",
    "name",
    "arn",
    "account_id",
    "resource_type",
    "identity_type",
    "agent_type",
    "action",
    "effect",
    "sensitive",
    "sensitivity",
    "internet_exposed",
    "synthetic",
    "scenario_id",
    "dataset_version",
    "status",
    "severity",
    "risk_score",
    "confidence",
    "verification_status",
    "source_api",
    "region",
    "task_type",
    "stage",
    "temporary",
    "policy_type",
    "memory_type",
}


class ViewLimit(AppError):
    status_code = 422
    code = "view_limit_exceeded"
    message = "The graph view exceeds its response bound; narrow the requested scope"


def active(variable, timestamp):
    return (
        f"coalesce({variable}.valid_from,{variable}.source_time,{variable}.observed_at,"
        f"{variable}.created_at) <= {timestamp} "
        f"AND ({variable}.valid_to IS NULL OR {timestamp} < {variable}.valid_to)"
    )


class VisualizationRepository:
    def __init__(self, database, tenant_id):
        self.database, self.tenant_id = database, tenant_id
        self.live = LiveRepository(database, tenant_id)

    def read(self, query, params=None):
        return self.database.query(
            query, {**(params or {}), "tenant": self.tenant_id}, read_only=True
        )

    def fence(self, query):
        sequence = self.live.sequence()
        if query.snapshot_sequence is not None and query.snapshot_sequence != sequence:
            raise CursorConflict()
        if query.cursor and query.snapshot_sequence is None:
            raise CursorConflict()
        return sequence

    def check(self, sequence):
        if self.live.sequence() != sequence:
            raise CursorConflict()

    def associations(self, ids):
        # References only, never memory content or arbitrary evidence JSON.
        rows = (
            self.read(
                "MATCH path=(n:Entity)-[r]-(a:Entity) "
                "WHERE n.id IN $ids AND all(x IN nodes(path) WHERE x.tenant_id=$tenant) "
                "AND all(e IN relationships(path) WHERE e.tenant_id=$tenant) "
                "AND (a.kind IN ['Evidence','RiskFinding'] OR a:SecurityCase) "
                "RETURN n.id,a.kind,a.id,a.severity,labels(a) ORDER BY n.id,a.id LIMIT 16001",
                {"ids": ids},
            )
            if ids
            else []
        )
        if len(rows) > 16000:
            raise ViewLimit()
        result = {
            i: {"evidence_ids": [], "investigation_ids": [], "risk_severity": None} for i in ids
        }
        priorities = {None: 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        for identifier, kind, other, severity, labels in rows:
            item = result[identifier]
            if kind == "Evidence":
                item["evidence_ids"].append(other)
            if "SecurityCase" in labels:
                item["investigation_ids"].append(other)
            if priorities.get(severity, 0) > priorities.get(item["risk_severity"], 0):
                item["risk_severity"] = severity
        snapshots = (
            self.read(
                "MATCH path=(e:Evidence)-[:SUPPORTS]->(f:RiskFinding) "
                "WHERE e.record_id IN $ids "
                "AND all(n IN nodes(path) WHERE n.tenant_id=$tenant) "
                "AND all(r IN relationships(path) WHERE r.tenant_id=$tenant) "
                "RETURN e.record_id,e.id,f.severity,f.path_id LIMIT 16001",
                {"ids": ids},
            )
            if ids
            else []
        )
        if len(snapshots) > 16000:
            raise ViewLimit()
        paths = sorted({r[3] for r in snapshots})
        cases = (
            self.read(
                "MATCH (i:SecurityCase {tenant_id:$tenant}) WHERE i.path_id IN $paths "
                "RETURN i.path_id,i.id LIMIT 1001",
                {"paths": paths},
            )
            if paths
            else []
        )
        if len(cases) > 1000:
            raise ViewLimit()
        for identifier, evidence, severity, path_id in snapshots:
            item = result[identifier]
            item["evidence_ids"].append(evidence)
            item["investigation_ids"].extend(c for p, c in cases if p == path_id)
            if priorities.get(severity, 0) > priorities.get(item["risk_severity"], 0):
                item["risk_severity"] = severity
        for item in result.values():
            item["associations_truncated"] = any(
                len(item[k]) > 64 for k in ("evidence_ids", "investigation_ids")
            )
            for k in ("evidence_ids", "investigation_ids"):
                item[k] = sorted(set(item[k]))[:64]
        return result

    @staticmethod
    def item(record, associations=None):
        data = dict(
            id=record["id"],
            type=record["kind"],
            label=str(record.get("display_name", record.get("name", record["kind"])))[:256],
            attributes={
                k: v[:512] if isinstance(v, str) else v
                for k, v in record.items()
                if k in DISPLAY_FIELDS
            },
            valid_from=from_epoch_us(
                record.get("valid_from", record.get("source_time", record["observed_at"]))
            ),
            valid_to=from_epoch_us(record["valid_to"])
            if record.get("valid_to") is not None
            else None,
            created_at=from_epoch_us(record["created_at"]),
            updated_at=from_epoch_us(record["updated_at"]),
            **(associations or {}),
        )
        if "source_node_id" in record:
            return VisualEdge(
                **data,
                source=record["source_node_id"],
                target=record["target_node_id"],
                source_event_id=record.get("source_event_id"),
            )
        return VisualNode(**data)

    def snapshot(self, query):
        seq = self.fence(query)
        at = query.at or utc_now()
        params = {
            "at": epoch_us(at),
            "cursor": query.cursor or "",
            "limit": query.limit + 1,
            "node_type": query.node_type,
            "scenario": query.scenario,
        }
        if query.collection == "nodes":
            rows = self.read(
                "MATCH (n:Entity {tenant_id:$tenant}) WHERE n.id > $cursor "
                "AND ($node_type IS NULL OR n.kind=$node_type) "
                "AND ($scenario IS NULL OR n.scenario_id IN ['baseline',$scenario]) AND "
                + active("n", "$at")
                + " RETURN properties(n) ORDER BY n.id LIMIT $limit",
                params,
            )
        else:
            rows = self.read(
                "MATCH path=(a:Entity)-[e]->(b:Entity) WHERE e.id > $cursor "
                "AND all(n IN nodes(path) WHERE n.tenant_id=$tenant) "
                "AND e.tenant_id=$tenant "
                "AND ($node_type IS NULL OR a.kind=$node_type OR b.kind=$node_type) "
                "AND ($scenario IS NULL OR e.scenario_id IN ['baseline',$scenario]) AND "
                + active("e", "$at")
                + " AND "
                + active("a", "$at")
                + " AND "
                + active("b", "$at")
                + " RETURN properties(e) ORDER BY e.id LIMIT $limit",
                params,
            )
        records = [r[0] for r in rows[: query.limit]]
        assoc = self.associations([r["id"] for r in records])
        items = [self.item(n, assoc.get(n["id"])) for n in records]
        self.check(seq)
        return VisualSnapshot(
            snapshot_at=at,
            snapshot_sequence=seq,
            collection=query.collection,
            nodes=items if query.collection == "nodes" else [],
            edges=items if query.collection == "edges" else [],
            next_cursor=items[-1].id if len(rows) > query.limit else None,
            has_more=len(rows) > query.limit,
        )

    def difference(self, query):
        seq = self.fence(query)
        node = query.collection == "nodes"
        var = "n" if node else "e"
        match = (
            "MATCH (n:Entity {tenant_id:$tenant}) "
            if node
            else (
                "MATCH path=(a:Entity)-[e]->(b:Entity) WHERE "
                "all(n IN nodes(path) WHERE n.tenant_id=$tenant) AND e.tenant_id=$tenant AND "
            )
        )
        pre = "WHERE " if node else ""
        predicate_before = active(var, "$before")
        predicate_after = active(var, "$after")
        if not node:
            # An edge is visible only while both endpoints are visible, matching
            # snapshot(). Closing a node can remove an otherwise open-ended edge.
            predicate_before += " AND " + active("a", "$before") + " AND " + active("b", "$before")
            predicate_after += " AND " + active("a", "$after") + " AND " + active("b", "$after")
        rows = self.read(
            match + pre + f"{var}.id > $cursor "
            f"AND ($scenario IS NULL OR {var}.scenario_id IN ['baseline',$scenario]) "
            f"AND (({predicate_before}) <> ({predicate_after})) "
            f"RETURN properties({var}),({predicate_after}) ORDER BY {var}.id LIMIT $limit",
            {
                "before": epoch_us(query.before),
                "after": epoch_us(query.after),
                "cursor": query.cursor or "",
                "scenario": query.scenario,
                "limit": query.limit + 1,
            },
        )
        added = []
        removed = []
        for record, is_added in rows[: query.limit]:
            (added if is_added else removed).append(self.item(record))
        self.check(seq)
        return GraphDifference(
            before=query.before,
            after=query.after,
            snapshot_sequence=seq,
            added_nodes=added if node else [],
            removed_nodes=removed if node else [],
            added_edges=added if not node else [],
            removed_edges=removed if not node else [],
            next_cursor=rows[query.limit - 1][0]["id"] if len(rows) > query.limit else None,
            has_more=len(rows) > query.limit,
        )

    def summary(self):
        seq = self.live.sequence()
        counts = self.read("MATCH (n:Entity {tenant_id:$tenant}) RETURN n.kind,count(n)")
        risks = self.read("MATCH (n:RiskFinding {tenant_id:$tenant}) RETURN n.severity,count(n)")
        cases = self.read("MATCH (n:SecurityCase {tenant_id:$tenant}) RETURN n.status,count(n)")
        tasks = self.read("MATCH (n:Task {tenant_id:$tenant}) RETURN n.status,count(n)")
        self.check(seq)
        return DashboardSummary(
            snapshot_at=utc_now(),
            snapshot_sequence=seq,
            entity_counts=dict(counts),
            persisted_risk_severities={k or "unknown": v for k, v in risks},
            investigation_statuses=dict(cases),
            task_statuses=dict(tasks),
        )

    def timeline(self, query):
        seq = self.fence(query)
        cursor_time = -1
        if query.cursor:
            found = self.read(
                "MATCH (n:SecurityEvent {tenant_id:$tenant,id:$id}) RETURN n.event_time",
                {"id": query.cursor},
            )
            if not found:
                raise CursorConflict()
            cursor_time = found[0][0]
        rows = self.read(
            "MATCH (n:SecurityEvent {tenant_id:$tenant}) "
            "WHERE n.event_time >= $before AND n.event_time < $after "
            "AND (n.event_time > $cursor_time OR (n.event_time=$cursor_time AND n.id > $cursor)) "
            "RETURN properties(n) ORDER BY n.event_time,n.id LIMIT $limit",
            {
                "before": epoch_us(query.before),
                "after": epoch_us(query.after),
                "cursor_time": cursor_time,
                "cursor": query.cursor or "",
                "limit": query.limit + 1,
            },
        )
        records = [r[0] for r in rows[: query.limit]]
        assoc = self.associations([r["id"] for r in records])
        result = [
            TimelineEvent(
                id=n["id"],
                type=n["event_type"],
                occurred_at=from_epoch_us(n["event_time"]),
                ingested_at=from_epoch_us(n["created_at"]),
                source_api=n.get("source_api"),
                evidence_ids=assoc[n["id"]]["evidence_ids"],
            )
            for n in records
        ]
        self.check(seq)
        return GraphTimeline(
            before=query.before,
            after=query.after,
            snapshot_sequence=seq,
            events=result,
            next_cursor=result[-1].id if len(rows) > query.limit else None,
            has_more=len(rows) > query.limit,
        )

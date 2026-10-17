"""Atomic AWS observation batches in FalkorDB; live IAM writes are absent."""

import json

from app.core.time import utc_now
from app.db.cypher import creation_properties, fingerprint, stable_id
from app.db.errors import GraphConflict
from app.models.graph import (
    NodeInput,
    NodeRef,
    Provenance,
    RelationshipInput,
    epoch_us,
    from_epoch_us,
)
from app.models.graph import NodeKind as N
from app.models.relationships import validate_endpoints
from app.repositories.graph import build_node_properties, build_relationship_properties
from app.services.aws_errors import AwsImportLimit


class AwsBatch:
    def __init__(self, repository, kind, run_key, now):
        self.repository, self.kind, self.run_key, self.now = repository, kind, run_key, now
        self.source = "shadowchain.aws." + kind + ".v1"
        self.run_id = stable_id(
            repository.scope.tenant_id, N.DECISION.value, repository.account, self.source, run_key
        )
        self.params, self.matches, self.changes, self.nodes = {}, [], [], {}
        self.edge_count = 0
        self._edge_keys = set()

    def provenance(self, key, at=None):
        return Provenance(
            source=self.source,
            source_id=key,
            observed_at=from_epoch_us(at or self.now),
            collector=self.source,
            source_uri="urn:shadowchain:aws:" + stable_id(self.source, key),
        )

    def node(self, kind, key, attributes, *, at=None, snapshot=False):
        prefix = "n" + str(len(self.nodes))
        value = build_node_properties(
            self.repository.scope.tenant_id,
            NodeInput(
                kind=kind,
                account_id=self.repository.account,
                provenance=self.provenance(key),
                attributes=attributes,
            ),
            self.now,
        )
        if value["id"] in self.nodes:
            return self.nodes[value["id"]]
        existing = self.repository.find(value["id"]) if self.kind == "cloudtrail" else None
        if existing:
            if existing["fingerprint"] != value["fingerprint"]:
                raise GraphConflict()
            self.params[prefix + "_id"] = value["id"]
            self.matches.append(
                f"MATCH ({prefix}:Entity {{tenant_id: $tenant_id, id: ${prefix}_id}}) "
            )
        else:
            literal, params = creation_properties(value, prefix)
            self.params.update(params)
            extra = ":AwsIamObservation" if snapshot else ""
            self.changes.append(f"CREATE ({prefix}:Entity:{kind.value}{extra} {literal}) ")
        value = {**value, "variable": prefix}
        self.nodes[value["id"]] = value
        if len(self.nodes) > self.repository.settings.aws_max_records:
            raise AwsImportLimit()
        return value

    def edge(self, source, target, kind, event=None, at=None):
        edge_key = (source["id"], target["id"], kind.value, event, at or self.now)
        if edge_key in self._edge_keys:
            return
        self._edge_keys.add(edge_key)
        prefix = "e" + str(self.edge_count)
        self.edge_count += 1
        request = RelationshipInput(
            kind=kind,
            source=NodeRef(kind=source["kind"], id=source["id"]),
            target=NodeRef(kind=target["kind"], id=target["id"]),
            provenance=self.provenance(self.run_key + "/" + prefix),
            valid_from=from_epoch_us(at or self.now),
            source_event_id=event,
            verification_status="verified",
        )
        validate_endpoints(request)
        props = build_relationship_properties(self.repository.scope.tenant_id, request, self.now)
        literal, params = creation_properties(props, prefix)
        self.params.update(params)
        self.changes.append(
            f"CREATE ({source['variable']})-[:{kind.value} {literal}]->({target['variable']}) "
        )
        if self.edge_count > self.repository.settings.aws_max_records * 3:
            raise AwsImportLimit()


class AwsRepository:
    def __init__(self, database, scope, settings):
        self.database, self.scope, self.settings = database, scope, settings
        self.account = settings.aws_account_id

    def params(self, values=None):
        return {**(values or {}), "tenant_id": self.scope.tenant_id, "account": self.account}

    def read(self, query, values=None):
        return self.database.query(query, self.params(values), read_only=True)

    def key(self, kind, request_id):
        return stable_id(self.scope.tenant_id, self.account or "local", kind, request_id)

    def find(self, identifier):
        rows = self.read(
            "MATCH (n:Entity {tenant_id: $tenant_id, id: $id}) RETURN properties(n)",
            {"id": identifier},
        )
        return rows[0][0] if rows else None

    def saved(self, run_id, digest):
        rows = self.read(
            "MATCH (n:AwsSyncRun {tenant_id: $tenant_id, id: $id}) "
            "RETURN n.request_sha256,n.response_json",
            {"id": run_id},
        )
        if not rows:
            return None
        if rows[0][0] != digest:
            raise GraphConflict()
        return json.loads(rows[0][1])

    def stream(self, kind):
        identifier = self.key("stream", kind)
        rows = self.read(
            "MATCH (s:AwsImportStream {tenant_id: $tenant_id,id: $id}) RETURN properties(s)",
            {"id": identifier},
        )
        if rows:
            return rows[0][0]
        props = {
            "id": identifier,
            "tenant_id": self.scope.tenant_id,
            "account_id": self.account,
            "revision": 0,
            "lock_version": 0,
            "created_at": epoch_us(utc_now()),
            "source": "shadowchain.aws.v1",
            "source_id": kind,
            "watermark": 0,
            "kind": "AwsImportStream",
            "updated_at": epoch_us(utc_now()),
            "observed_at": epoch_us(utc_now()),
            "collector": "shadowchain.aws.v1",
            "source_uri": "urn:shadowchain:aws-stream:" + identifier,
            "fingerprint": fingerprint({"id": identifier}),
        }
        literal, params = creation_properties(props, "stream")
        try:
            self.database.query(
                f"CREATE (s:AwsImportStream {literal}) RETURN s.id",
                self.params(params),
                idempotent=True,
            )
        except GraphConflict:
            pass
        return self.stream(kind)

    def commit(self, batch, stream):
        query = (
            "MATCH (s:AwsImportStream {tenant_id: $tenant_id,id: $stream_id}) "
            "SET s.lock_version = s.lock_version + 1 WITH s WHERE s.revision = $revision "
            "AND s.watermark < $now "
        )
        if batch.kind == "iam":
            query += (
                "OPTIONAL MATCH (old:AwsIamObservation "
                "{tenant_id: $tenant_id,account_id: $account}) "
                "WHERE old.valid_to IS NULL WITH s,collect(old) AS previous "
                "OPTIONAL MATCH path=(a:AwsIamObservation)-[e]->(b:Entity) "
                "WHERE a.tenant_id = $tenant_id AND a.account_id = $account "
                "AND b.tenant_id = $tenant_id AND e.tenant_id = $tenant_id "
                "AND e.source = $source AND e.valid_to IS NULL "
                "WITH s,previous,collect(e) AS previous_edges "
                "FOREACH (n IN previous | SET n.valid_to = $now,n.updated_at = $now) "
                "FOREACH (e IN previous_edges | SET e.valid_to = $now,e.updated_at = $now) "
            )
        rows = self.database.mutate(
            query
            + "".join(batch.matches + batch.changes)
            + "SET s.revision = s.revision + 1,s.watermark = $now RETURN s.revision",
            self.params(
                {
                    **batch.params,
                    "stream_id": stream["id"],
                    "revision": stream["revision"],
                    "now": batch.now,
                    "source": batch.source,
                }
            ),
            idempotent=True,
            tenant_id=self.scope.tenant_id,
            event_type="aws.sync.completed",
            entity_ids=[batch.run_id],
        )
        return bool(rows)

"""Same-transaction change journal and shared query budgets in the primary graph."""

import json
from uuid import uuid4

from app.core.errors import AppError
from app.core.time import utc_now
from app.db.cypher import fingerprint, stable_id
from app.models.graph import epoch_us, from_epoch_us
from app.schemas.live import LiveEvent, LivePage


class CursorConflict(AppError):
    status_code = 409
    code = "cursor_conflict"
    message = "The cursor no longer matches this graph view; request a fresh first page"


class QueryRateLimited(AppError):
    status_code = 429
    code = "query_rate_limited"
    message = "The shared graph query budget has been exhausted; retry after one minute"


def journal_query(query, params, tenant_id, event_type, entity_ids=()):
    """Only called with internal, single-statement mutation queries ending in RETURN."""
    if " UNION ALL " in query:
        read, write = query.split(" UNION ALL ", 1)
        wrapped, bindings = journal_query(write, params, tenant_id, event_type, entity_ids)
        return read + " UNION ALL " + wrapped, bindings
    prefix, separator, projection = query.rpartition("RETURN ")
    if not separator or ";" in query or not event_type.replace(".", "").replace("_", "").isalnum():
        raise ValueError("Unsupported internal journal mutation")
    identifiers = sorted(set(str(i) for i in entity_ids if i))
    identifier = str(uuid4())
    bindings = {
        **params,
        "_live_tenant": tenant_id,
        "_live_stream": stable_id(tenant_id, "live-stream-v1"),
        "_live_id": identifier,
        "_live_type": event_type,
        "_live_time": epoch_us(utc_now()),
        "_live_refs": json.dumps(identifiers[:64]),
        "_live_truncated": len(identifiers) > 64,
        "_live_uri": "urn:shadowchain:live-event:" + identifier,
        "_live_sha": fingerprint(
            {"event_id": identifier, "type": event_type, "entity_ids": identifiers}
        ),
    }
    # Duplicate acknowledgments never repeat a committed mutation. The stream is locked
    # before domain writes; the event is appended only if the guarded mutation has a row.
    text = (
        "MATCH (_live:LiveStream {id: $_live_stream,tenant_id: $_live_tenant}) "
        "SET _live.lock_version = _live.lock_version + 1 WITH _live "
        "OPTIONAL MATCH (_existing:LiveEvent {id: $_live_id,tenant_id: $_live_tenant}) "
        "WITH _live,_existing WHERE _existing IS NULL "
        + prefix
        + " WITH * MATCH (_feed:LiveStream {id: $_live_stream,tenant_id: $_live_tenant}) "
        "SET _feed.sequence = _feed.sequence + 1,_feed.updated_at = $_live_time "
        "CREATE (_event:LiveEvent {id: $_live_id,tenant_id: $_live_tenant,"
        "sequence: _feed.sequence,event_type: $_live_type,created_at: $_live_time,"
        "updated_at: $_live_time,observed_at: $_live_time,kind:'LiveEvent',"
        "source:'shadowchain.api.v1',source_id:$_live_id,collector:'durable-journal-v1',"
        "source_uri:$_live_uri,fingerprint:$_live_sha,"
        "entity_ids_json: $_live_refs,references_truncated: $_live_truncated}) RETURN " + projection
    )
    return text, bindings


class LiveRepository:
    def __init__(self, database, tenant_id):
        self.database, self.tenant_id = database, tenant_id

    def sequence(self):
        rows = self.database.query(
            "MATCH (s:LiveStream {tenant_id:$tenant}) RETURN s.sequence",
            {"tenant": self.tenant_id},
            read_only=True,
        )
        return rows[0][0] if rows else 0

    def ensure(self):
        from app.db.errors import GraphConflict

        params = {"id": stable_id(self.tenant_id, "live-stream-v1"), "tenant": self.tenant_id}
        params.update(
            now=epoch_us(utc_now()),
            sha=fingerprint({"id": params["id"]}),
            uri="urn:shadowchain:live-stream:" + params["id"],
        )
        existing = self.database.query(
            "MATCH (s:LiveStream {id:$id,tenant_id:$tenant}) RETURN s.id",
            params,
            read_only=True,
        )
        if existing:
            return
        try:
            self.database.query(
                "CREATE (:LiveStream {id:$id,tenant_id:$tenant,sequence:0,lock_version:0,"
                "created_at:$now,updated_at:$now,observed_at:$now,kind:'LiveStream',"
                "source:'shadowchain.api.v1',source_id:'live-stream-v1',"
                "collector:'durable-journal-v1',source_uri:$uri,fingerprint:$sha}) "
                "RETURN 1",
                params,
                idempotent=True,
            )
        except GraphConflict:
            if not self.database.query(
                "MATCH (s:LiveStream {id:$id,tenant_id:$tenant}) RETURN s.id",
                params,
                read_only=True,
            ):
                raise

    def page(self, after=0, limit=50):
        # Sequence is read first: concurrent writes enter a subsequent page, never this fence.
        high = self.sequence()
        if after > high:
            raise CursorConflict()
        rows = self.database.query(
            "MATCH (e:LiveEvent {tenant_id:$tenant}) "
            "WHERE e.sequence > $after AND e.sequence <= $high "
            "RETURN properties(e) ORDER BY e.sequence LIMIT $limit",
            {"tenant": self.tenant_id, "after": after, "high": high, "limit": limit},
            read_only=True,
        )
        events = [
            LiveEvent(
                event_id=n["id"],
                sequence=n["sequence"],
                type=n["event_type"],
                occurred_at=from_epoch_us(n["created_at"]),
                entity_ids=json.loads(n["entity_ids_json"]),
                references_truncated=n["references_truncated"],
            )
            for (n,) in rows
        ]
        if (high > after and not events) or any(
            event.sequence != after + index + 1 for index, event in enumerate(events)
        ):
            raise CursorConflict()
        bounded = []
        size = 512
        for event in events:
            encoded_size = len(event.model_dump_json().encode())
            if size + encoded_size > 60000:
                break
            bounded.append(event)
            size += encoded_size
        events = bounded
        cursor = events[-1].sequence if events else after
        return LivePage(events=events, cursor=cursor, high_watermark=high, has_more=cursor < high)

    def budget(self, principal_id, limit, *, category="graph", now=None):
        minute = epoch_us(now or utc_now()) // 60_000_000
        identifier = stable_id(self.tenant_id, principal_id, category, "query-budget")
        rows = self.database.query(
            "MERGE (b:ApiRateBudget {id:$id,tenant_id:$tenant}) "
            "ON CREATE SET b.window = -1,b.used = 0 "
            "SET b.used = CASE WHEN b.window = $window THEN b.used ELSE 0 END,"
            "b.window = $window WITH b WHERE b.used < $limit "
            "SET b.used = b.used + 1 RETURN b.used",
            {"id": identifier, "tenant": self.tenant_id, "window": minute, "limit": limit},
            idempotent=False,
        )
        if not rows:
            raise QueryRateLimited()

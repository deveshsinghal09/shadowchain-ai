"""Minimal durable bootstrap record, also used to verify restart recovery."""

from app.core.errors import DatabaseUnavailable
from app.core.time import utc_now
from app.db.falkordb import GraphDatabase
from app.models.foundation import FoundationRecord


class FoundationRepository:
    def __init__(self, database: GraphDatabase) -> None:
        self.database = database

    def ensure(self) -> FoundationRecord:
        rows = self.database.query(
            "MERGE (f:Foundation {id: $id}) "
            "ON CREATE SET f.schema_version = 1, f.created_at = $created_at "
            "RETURN f.id, f.schema_version, f.created_at",
            {"id": "shadowchain-foundation", "created_at": utc_now().isoformat()},
            idempotent=True,
        )
        if len(rows) != 1:
            raise DatabaseUnavailable()
        return FoundationRecord(id=rows[0][0], schema_version=rows[0][1], created_at=rows[0][2])

    def get(self) -> FoundationRecord | None:
        rows = self.database.query(
            "MATCH (f:Foundation {id: $id}) RETURN f.id, f.schema_version, f.created_at",
            {"id": "shadowchain-foundation"},
            read_only=True,
        )
        if not rows:
            return None
        return FoundationRecord(id=rows[0][0], schema_version=rows[0][1], created_at=rows[0][2])

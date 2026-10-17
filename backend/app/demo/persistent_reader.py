"""Fresh-process authorized reader for the FalkorDB arm of the retention ablation."""

import json
import sys

from app.core.config import Settings
from app.db.cypher import fingerprint
from app.db.falkordb import GraphDatabase
from app.demo.evaluation import SCOPE, load_document, require
from app.models.memory import MemoryQuery
from app.repositories.memory import MemoryRepository
from app.services.memory import MemoryService


def main():
    query = json.loads(sys.stdin.read(65537))["query"]
    database = GraphDatabase(Settings())
    try:
        fixture = load_document(database, "memory-input")["ablation"]
        require(query == fixture["input"]["query"], "identical ablation lookup")
        service = MemoryService(
            MemoryRepository(database, SCOPE, fixture["space_id"], fixture["reader_agent_id"])
        )
        memories = service.retrieve_related_memories(MemoryQuery.model_validate(query))
        matched = [m for m in memories if m.id == fixture["memory_id"]]
        if matched:
            require(matched[0].content == fixture["input"]["write"]["content"], "ablation content")
        print(json.dumps({"count": len(matched), "input_sha256": fingerprint(fixture["input"])}))
    finally:
        database.close()


if __name__ == "__main__":
    main()

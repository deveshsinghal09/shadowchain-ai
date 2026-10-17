"""Idempotent graph initialization: python -m app.db.initialize."""

import argparse
import json

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.falkordb import GraphDatabase
from app.db.schema import SCHEMA_VERSION, SchemaManager
from app.repositories.foundation import FoundationRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize or verify the primary FalkorDB schema")
    parser.add_argument("--verify-only", action="store_true")
    arguments = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    database = GraphDatabase(settings)
    try:
        if arguments.verify_only:
            SchemaManager(database).verify()
        else:
            database.initialize_schema()
            FoundationRepository(database).ensure()
        print(
            json.dumps(
                {
                    "graph": settings.falkordb_graph,
                    "schema_version": SCHEMA_VERSION,
                    "status": "verified",
                }
            )
        )
    finally:
        database.close()


if __name__ == "__main__":
    main()

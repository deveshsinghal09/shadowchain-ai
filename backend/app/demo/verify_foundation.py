"""Print the persistent foundation identity from inside the private backend network."""

from app.core.config import get_settings
from app.db.falkordb import GraphDatabase
from app.repositories.foundation import FoundationRepository


def main() -> None:
    database = GraphDatabase(get_settings())
    try:
        repository = FoundationRepository(database)
        record = repository.get()
        if record is None:
            raise RuntimeError("Persistent foundation record is missing")
        print(record.model_dump_json())
    finally:
        database.close()


if __name__ == "__main__":
    main()

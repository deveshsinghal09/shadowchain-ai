from unittest.mock import MagicMock, patch

import pytest
from redis.exceptions import ConnectionError, ResponseError

from app.core.config import Settings
from app.core.errors import DatabaseUnavailable
from app.db.falkordb import GraphDatabase


@pytest.mark.parametrize("error", [ConnectionError("private-host"), ResponseError("private-query")])
def test_database_failures_are_translated(unit_settings: Settings, error: Exception) -> None:
    with patch("app.db.falkordb.FalkorDB") as cls:
        cls.return_value.select_graph.return_value.query.side_effect = error
        database = GraphDatabase(unit_settings)
        try:
            with pytest.raises(DatabaseUnavailable) as captured:
                database.check_ready()
            assert str(captured.value) == DatabaseUnavailable.message
        finally:
            database.close()


def test_readiness_uses_graph_query_not_ping(unit_settings: Settings) -> None:
    with patch("app.db.falkordb.FalkorDB") as cls:
        graph: MagicMock = cls.return_value.select_graph.return_value
        graph.query.return_value.result_set = [[1]]
        database = GraphDatabase(unit_settings)
        try:
            database.check_ready()
            graph.query.assert_called_once_with("RETURN 1 AS ok", params={}, timeout=2000)
        finally:
            database.close()


def test_constructor_outage_is_retried_on_next_request(unit_settings: Settings) -> None:
    with patch("app.db.falkordb.FalkorDB") as cls:
        recovered_client = MagicMock()
        recovered_client.select_graph.return_value.query.return_value.result_set = [[1]]
        cls.side_effect = [ConnectionError("offline-during-discovery"), recovered_client]
        database = GraphDatabase(unit_settings)
        try:
            cls.assert_not_called()
            with pytest.raises(DatabaseUnavailable):
                database.check_ready()
            database.check_ready()
            assert cls.call_count == 2
        finally:
            database.close()

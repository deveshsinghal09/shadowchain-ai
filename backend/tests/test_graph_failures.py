import socket
from unittest.mock import MagicMock, patch

import pytest
from redis.exceptions import AuthenticationError, ConnectionError, ResponseError

from app.core.config import Settings
from app.core.errors import DatabaseUnavailable
from app.db.errors import GraphClosed, GraphQueryError, GraphWriteUncertain
from app.db.falkordb import GraphDatabase
from app.models.graph import NodeKind, TenantScope
from app.repositories.graph import GraphRepository
from tests.graph_helpers import node_input


def test_core_operations_fail_clearly_with_a_real_unavailable_socket() -> None:
    # Reserve a loopback port without listening so no unrelated service can occupy it.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        database = GraphDatabase(
            Settings(
                _env_file=None,
                falkordb_host="127.0.0.1",
                falkordb_port=reserved.getsockname()[1],
                falkordb_password=None,
                falkordb_connect_timeout_seconds=0.1,
                falkordb_socket_timeout_seconds=0.2,
                falkordb_query_timeout_ms=100,
                falkordb_retry_attempts=2,
                falkordb_retry_backoff_seconds=0,
            )
        )
        try:
            repo = GraphRepository(database, TenantScope(tenant_id="tenant-a"))
            with pytest.raises(DatabaseUnavailable):
                repo.upsert_node(node_input(NodeKind.AGENT))
            with pytest.raises(DatabaseUnavailable):
                repo.list_nodes(NodeKind.AGENT)
            with pytest.raises(DatabaseUnavailable):
                database.check_ready()
        finally:
            database.close()


def test_transient_reads_retry_but_unsafe_writes_have_unknown_outcome(
    unit_settings: Settings,
) -> None:
    config = unit_settings.model_copy(update={"falkordb_retry_attempts": 3})
    with patch("app.db.falkordb.FalkorDB") as cls:
        graph: MagicMock = cls.return_value.select_graph.return_value
        graph.ro_query.side_effect = [ConnectionError("offline"), MagicMock(result_set=[[1]])]
        database = GraphDatabase(config)
        try:
            assert database.query("RETURN 1", read_only=True) == [[1]]
            assert graph.ro_query.call_count == 2
            graph.query.side_effect = ConnectionError("reply-lost")
            with pytest.raises(GraphWriteUncertain):
                database.execute_atomic("CREATE (:TrustedInternalTest)", {})
            assert graph.query.call_count == 1
        finally:
            database.close()


def test_idempotent_write_retries_and_deterministic_errors_do_not(unit_settings: Settings) -> None:
    config = unit_settings.model_copy(update={"falkordb_retry_attempts": 3})
    with patch("app.db.falkordb.FalkorDB") as cls:
        graph = cls.return_value.select_graph.return_value
        graph.query.side_effect = [ConnectionError("offline"), MagicMock(result_set=[[1]])]
        database = GraphDatabase(config)
        try:
            assert database.execute_atomic("RETURN 1", {}, idempotent=True) == [[1]]
            assert graph.query.call_count == 2
            graph.query.reset_mock()
            graph.query.side_effect = ResponseError("syntax-with-sensitive-data")
            with pytest.raises(GraphQueryError):
                database.query("invalid", idempotent=True)
            assert graph.query.call_count == 1
        finally:
            database.close()


def test_authentication_is_not_retried_and_closed_manager_cannot_reopen(
    unit_settings: Settings,
) -> None:
    config = unit_settings.model_copy(update={"falkordb_retry_attempts": 3})
    with patch("app.db.falkordb.FalkorDB") as cls:
        cls.side_effect = AuthenticationError("private-auth-error")
        database = GraphDatabase(config)
        with pytest.raises(DatabaseUnavailable):
            database.query("RETURN 1", read_only=True)
        assert cls.call_count == 1
        database.close()
        with pytest.raises(GraphClosed):
            database.query("RETURN 1", read_only=True)

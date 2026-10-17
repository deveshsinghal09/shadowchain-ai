"""One bounded connection pool per process; all durable state lives in the graph."""

import logging
from collections.abc import Mapping
from threading import Lock
from time import sleep
from typing import Any

from falkordb import FalkorDB
from redis import BlockingConnectionPool, SSLConnection
from redis.backoff import NoBackoff
from redis.exceptions import (
    AuthenticationError,
    BusyLoadingError,
    ConnectionError,
    RedisError,
    ResponseError,
    TimeoutError,
    TryAgainError,
)
from redis.retry import Retry

from app.core.config import Settings
from app.core.errors import DatabaseUnavailable
from app.db.errors import GraphClosed, GraphConflict, GraphQueryError, GraphWriteUncertain

logger = logging.getLogger(__name__)


class GraphDatabase:
    def __init__(self, settings: Settings) -> None:
        self.graph_name = settings.falkordb_graph
        self.query_timeout_ms = settings.falkordb_query_timeout_ms
        self.retry_attempts = settings.falkordb_retry_attempts
        self.retry_backoff = settings.falkordb_retry_backoff_seconds
        self.schema_timeout = settings.falkordb_schema_timeout_seconds
        self.initialization_timeout = settings.falkordb_connect_timeout_seconds
        kwargs: dict[str, Any] = {}
        if settings.falkordb_tls:
            kwargs.update(
                connection_class=SSLConnection, ssl_cert_reqs="required", ssl_check_hostname=True
            )
        self.pool = BlockingConnectionPool(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            username=settings.falkordb_username,
            password=(
                settings.falkordb_password.get_secret_value()
                if settings.falkordb_password
                else None
            ),
            socket_connect_timeout=settings.falkordb_connect_timeout_seconds,
            socket_timeout=settings.falkordb_socket_timeout_seconds,
            max_connections=settings.falkordb_max_connections,
            timeout=settings.falkordb_connect_timeout_seconds,
            retry=Retry(NoBackoff(), 0),
            decode_responses=True,
            protocol=2,
            **kwargs,
        )
        # The official constructor performs server topology discovery over the network.
        # Initialize lazily inside query's error boundary, off the async event loop.
        self._client: FalkorDB | None = None
        self._client_lock = Lock()
        self._closed = False
        self._schema_lock = Lock()
        self._schema_initialized = False

    def _get_client(self) -> FalkorDB:
        if not self._client_lock.acquire(timeout=self.initialization_timeout):
            raise DatabaseUnavailable()
        try:
            if self._closed:
                raise GraphClosed()
            if self._client is None:
                self._client = FalkorDB(connection_pool=self.pool)
            return self._client
        finally:
            self._client_lock.release()

    def query(
        self,
        cypher: str,
        params: Mapping[str, Any] | None = None,
        *,
        read_only: bool = False,
        idempotent: bool = False,
    ) -> list[list[Any]]:
        """Execute trusted application Cypher. Never accept query text from an API/LLM.

        Explicit idempotency is a caller contract, not inferred by parsing Cypher.
        A lost reply to a non-idempotent write cannot safely be retried.
        """
        attempts = self.retry_attempts if read_only or idempotent else 1
        for attempt in range(attempts):
            try:
                graph = self._get_client().select_graph(self.graph_name)
                execute = graph.ro_query if read_only else graph.query
                return execute(
                    cypher, params=dict(params or {}), timeout=self.query_timeout_ms
                ).result_set
            except AuthenticationError as exc:
                self._log_failure(exc)
                raise DatabaseUnavailable() from exc
            except (ConnectionError, TimeoutError, BusyLoadingError, TryAgainError) as exc:
                if attempt + 1 < attempts:
                    sleep(self.retry_backoff * (2**attempt))
                    continue
                self._log_failure(exc)
                if not read_only and not idempotent:
                    raise GraphWriteUncertain() from exc
                raise DatabaseUnavailable() from exc
            except ResponseError as exc:
                self._log_failure(exc)
                if "constraint" in str(exc).lower() and "violation" in str(exc).lower():
                    raise GraphConflict() from exc
                raise GraphQueryError() from exc
            except RedisError as exc:
                self._log_failure(exc)
                raise GraphQueryError() from exc
        raise DatabaseUnavailable()  # Defensive: attempts is validated to be positive.

    @staticmethod
    def _log_failure(exc: Exception) -> None:
        logger.warning("graph_query_failed", extra={"error_type": type(exc).__name__})

    def execute_atomic(
        self, cypher: str, params: Mapping[str, Any], *, idempotent: bool = False
    ) -> list[list[Any]]:
        """A transaction is ONE server-side Cypher statement, including all mutations.

        This intentionally provides no multi-query BEGIN/COMMIT or misleading pipeline
        rollback guarantee. Parameter validation must happen before executing the query.
        """
        return self.query(cypher, params, idempotent=idempotent)

    def mutate(self, cypher, params, *, tenant_id, event_type, entity_ids=(), idempotent=True):
        from app.repositories.live import LiveRepository, journal_query

        LiveRepository(self, tenant_id).ensure()
        query, bindings = journal_query(cypher, params, tenant_id, event_type, entity_ids)
        return self.query(query, bindings, idempotent=idempotent)

    def create_constraint(self, kind: str, entity_type: str, label: str, fields: list[str]) -> None:
        """Internal schema operation; caller supplies only versioned schema constants."""
        try:
            # Official client 1.8 exposes GRAPH.CONSTRAINT via execute_command and
            # Graph helpers, rather than the newer client.create_constraint API.
            self._get_client().execute_command(
                "GRAPH.CONSTRAINT",
                "CREATE",
                self.graph_name,
                kind,
                entity_type,
                label,
                "PROPERTIES",
                len(fields),
                *fields,
            )
        except (AuthenticationError, ConnectionError, TimeoutError) as exc:
            raise DatabaseUnavailable() from exc
        except ResponseError as exc:
            # Concurrent schema initializers may both observe the constraint as absent.
            if "already exists" not in str(exc).lower():
                raise GraphQueryError() from exc

    def reconnect(self) -> None:
        """Discard socket/topology resources. No business state is held by the client."""
        with self._client_lock:
            if self._closed:
                raise GraphClosed()
            self.pool.disconnect()
            self._client = None
            self._schema_initialized = False

    def initialize_schema(self) -> None:
        from app.db.schema import SchemaManager

        # This flag caches completed DDL setup only, never graph/domain data. The
        # schema marker and constraints themselves remain authoritative in FalkorDB.
        if not self._schema_lock.acquire(timeout=self.schema_timeout):
            raise DatabaseUnavailable()
        try:
            if not self._schema_initialized:
                SchemaManager(self).initialize()
                self._schema_initialized = True
        finally:
            self._schema_lock.release()

    def verify_schema(self) -> None:
        from app.db.schema import SchemaManager

        SchemaManager(self).verify()

    def check_ready(self) -> None:
        # GRAPH.QUERY executes a real Cypher query and bootstraps an empty graph after
        # an initial outage. PING alone could incorrectly accept a plain Redis server.
        try:
            if self.query("RETURN 1 AS ok", idempotent=True) != [[1]]:
                raise DatabaseUnavailable()
        except GraphQueryError as exc:
            # Plain Redis, a missing graph module or invalid graph execution is not ready.
            raise DatabaseUnavailable() from exc

    def close(self) -> None:
        with self._client_lock:
            self.pool.disconnect()
            self._client = None
            self._closed = True
            self._schema_initialized = False


# The existing name remains compatible with the API/foundation. One implementation only.
FalkorDBConnectionManager = GraphDatabase

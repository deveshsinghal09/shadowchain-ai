"""Safe graph errors; raw database replies remain available only as chained causes."""

from app.core.errors import AppError


class GraphQueryError(AppError):
    code = "graph_query_failed"
    message = "The graph operation failed"


class GraphConflict(AppError):
    status_code = 409
    code = "graph_conflict"
    message = "The graph record conflicts with an existing record"


class GraphNotFound(AppError):
    status_code = 404
    code = "graph_not_found"
    message = "A graph endpoint or record does not exist in this tenant"


class GraphWriteUncertain(AppError):
    status_code = 503
    code = "graph_write_outcome_unknown"
    message = "Graph write outcome is unknown; reconcile before retrying"


class GraphSchemaError(GraphQueryError):
    status_code = 503
    code = "graph_schema_failed"
    message = "Graph schema initialization or verification failed"


class GraphClosed(GraphQueryError):
    code = "graph_connection_closed"
    message = "The graph connection manager is closed"

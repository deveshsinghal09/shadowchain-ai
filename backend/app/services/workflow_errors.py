from app.core.errors import AppError


class WorkflowBusy(AppError):
    status_code = 409
    code = "workflow_busy"
    message = "Investigation is leased by another worker; retry after its lease expires"


class WorkflowBlocked(AppError):
    status_code = 409
    code = "workflow_blocked"
    message = "Investigation exhausted its bounded attempts or has incompatible configuration"


class WorkflowSourceChanged(AppError):
    status_code = 409
    code = "workflow_source_changed"
    message = "Investigation source evidence is missing or changed; inspect the graph audit history"


class ProviderUnavailable(AppError):
    status_code = 503
    code = "model_provider_unavailable"
    message = "Model provider failed its bounded request or output validation"

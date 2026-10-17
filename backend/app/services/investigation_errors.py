from app.core.errors import AppError


class InvalidCaseTransition(AppError):
    status_code = 409
    code = "invalid_investigation_transition"
    message = "Investigation transition is invalid, stale or missing required review evidence"


class CaseAccessDenied(AppError):
    status_code = 403
    code = "investigation_access_denied"
    message = "Agent is not authorized for this investigation operation"


class CaseLimitExceeded(AppError):
    status_code = 422
    code = "investigation_limit_exceeded"
    message = "Investigation query exceeds its bounded result or context size"

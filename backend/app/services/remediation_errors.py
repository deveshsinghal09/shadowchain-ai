from app.core.errors import AppError


class InvalidRemediation(AppError):
    status_code = 422
    code = "invalid_remediation"
    message = "Target must be an active evidenced Allow grant or modeled role trust"


class RemediationConflict(AppError):
    status_code = 409
    code = "remediation_conflict"
    message = "Review is invalid or stale, or the proposal source changed; refresh before retrying"


class RemediationLimit(AppError):
    status_code = 422
    code = "remediation_limit_exceeded"
    message = "Remediation context exceeds a bounded limit; narrow the analysis scope"


class RemediationAccessDenied(AppError):
    status_code = 403
    code = "remediation_access_denied"
    message = "Current sharing permission and explicit reviewer authority are required"

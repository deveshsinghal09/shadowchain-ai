from app.core.errors import AppError


class MemoryAccessDenied(AppError):
    status_code = 403
    code = "memory_access_denied"
    message = "The agent has no active permission for this memory operation"


class MemoryVersionConflict(AppError):
    status_code = 409
    code = "memory_version_conflict"
    message = "Memory state changed or the request identity conflicts; refresh before retrying"


class MemoryLimitExceeded(AppError):
    status_code = 422
    code = "memory_limit_exceeded"
    message = "Memory query exceeds a bounded limit; narrow the subject or memory scope"

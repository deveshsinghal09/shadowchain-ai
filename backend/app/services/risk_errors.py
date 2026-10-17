from app.core.errors import AppError


class RiskLimitExceeded(AppError):
    status_code = 422
    code = "risk_analysis_limit_exceeded"
    message = "Analysis exceeds a bounded limit; narrow the identity, time range or traversal depth"


class RiskHistoryChanged(AppError):
    status_code = 409
    code = "risk_history_changed"
    message = "Event history changed during analysis; retry against the committed graph"

from app.core.errors import AppError


class AwsDisabled(AppError):
    status_code = 403
    code = "aws_integration_disabled"
    message = "AWS integration requires explicit enablement and account confirmation"


class AwsScopeError(AppError):
    status_code = 403
    code = "aws_account_scope_mismatch"
    message = "AWS caller or imported record does not match the configured account scope"


class AwsOperationError(AppError):
    status_code = 502
    code = "aws_operation_failed"
    message = "An allowlisted AWS read/export operation failed; verify profile and permissions"


class AwsImportLimit(AppError):
    status_code = 422
    code = "aws_import_limit"
    message = "AWS import exceeds its bounded scope or has an invalid/incomplete response"


class ReportConflict(AppError):
    status_code = 409
    code = "report_export_conflict"
    message = "Report source or existing artifact differs from the prepared export"


class PrivateBucketRequired(AppError):
    status_code = 403
    code = "private_export_bucket_required"
    message = "The configured export bucket must be owned by this account and private"

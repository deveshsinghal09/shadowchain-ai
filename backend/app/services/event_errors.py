from app.core.errors import AppError


class DemoNotSeeded(AppError):
    status_code = 409
    code = "demo_not_seeded"
    message = "Seed the synthetic dataset before replay or inspection"


class EventOrderError(AppError):
    status_code = 409
    code = "event_order_violation"
    message = "Events must be ingested in contiguous sequence order within each scenario"


class LateEventError(EventOrderError):
    code = "late_event_rejected"
    message = "Late event rejected; reset this scenario and replay in source-time order"


class DemoGenerationConflict(EventOrderError):
    code = "demo_generation_conflict"
    message = "The scenario was reset; refresh its generation before retrying"


class DemoMutationError(AppError):
    status_code = 422
    code = "invalid_synthetic_mutation"
    message = "Synthetic mutation has invalid, conflicting or out-of-scope endpoints"


class DemoVersionConflict(EventOrderError):
    code = "demo_dataset_version_conflict"
    message = "The stored demo version or manifest differs; migrate it explicitly"


class ConcurrentDemoChange(EventOrderError):
    code = "concurrent_demo_change"
    message = "Demo state changed concurrently; refresh state before retrying"

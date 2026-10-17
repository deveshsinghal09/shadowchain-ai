from app.models.events import DEMO_TENANT, Scenario
from app.models.graph import TenantScope
from app.models.workflow import TriggerInvestigation
from app.repositories.demo import DemoRepository
from app.repositories.workflow import WorkflowRepository
from app.services.events import EventIngestionService
from app.services.workflow import InvestigationWorkflow


def workflow_world(database, settings, *, scenario=Scenario.A, tenant=DEMO_TENANT):
    scope = TenantScope(tenant_id=tenant)
    events = EventIngestionService(DemoRepository(database, scope))
    events.seed()
    state = events.status(scenario)
    events.replay(scenario, state.generation)
    repository = WorkflowRepository(database, scope, settings)
    rows = repository.read(
        "MATCH (e:Entity:SecurityEvent {tenant_id: $tenant_id, scenario_id: $scenario}) "
        "RETURN e.id ORDER BY e.sequence DESC LIMIT 1",
        {"scenario": scenario.value},
    )
    request = TriggerInvestigation(request_id="test/investigation", event_id=rows[0][0])
    return InvestigationWorkflow(repository), request

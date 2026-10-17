from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app.core.config import Settings
from app.db.errors import GraphNotFound
from app.db.falkordb import GraphDatabase
from app.demo.dataset import hour
from app.models.events import Scenario, SyntheticSecurityEvent
from app.models.graph import TenantScope
from app.repositories.demo import DemoRepository
from app.repositories.risk import RiskRepository
from app.schemas.risk import AnalysisRequest, DifferenceRequest, TimelineRequest, WhatIfRequest
from app.services.events import EventIngestionService
from app.services.risk import RiskEngine

pytestmark = pytest.mark.integration


@pytest.fixture
def risk_engine(live_database: GraphDatabase) -> RiskEngine:
    scope = TenantScope(tenant_id="risk-tests")
    events = EventIngestionService(DemoRepository(live_database, scope))
    events.seed()
    events.replay(Scenario.A, 0)
    return RiskEngine(RiskRepository(live_database, scope))


def test_scenario_a_historical_paths_changes_simulation_and_reconnection(
    risk_engine: RiskEngine,
    isolated_graph_settings: Settings,
) -> None:
    before = risk_engine.discover(AnalysisRequest(at=hour(9), scenario=Scenario.A))
    assert before.findings == [] and before.persisted
    after = risk_engine.discover(AnalysisRequest(at=hour(12), scenario=Scenario.A))
    assert len(after.findings) == 1 and after.persisted
    path = after.findings[0]
    assert path.classification == "POTENTIAL ATTACK PATH"
    assert path.resource["entity_key"] == "resource/hr-data"
    assert [n["entity_key"] for n in path.roles] == ["role/temp-analyst", "role/ec2-backup"]
    assert {e.event_type for e in path.contributing_events} >= {
        "SyntheticTemporaryRoleAccessGranted",
        "SyntheticBackupRoleTrustChanged",
        "SyntheticSensitiveReadPolicyAttached",
    }
    difference = risk_engine.difference(
        DifferenceRequest(
            scenario=Scenario.A,
            before=hour(9),
            after=hour(12),
        )
    )
    assert difference.added_path_ids == [path.path_id]
    assert difference.introductions[0].first_reachable_at == hour(12)
    assert len(difference.introductions[0].transition_events) == 1
    evidence = risk_engine.repository.evidence(path.finding_id)
    assert {e["record"]["id"] for e in evidence.evidence if "id" in e["record"]} >= set(
        path.supporting_relationship_ids
    )
    simulation = risk_engine.what_if(
        WhatIfRequest(
            scenario=Scenario.A,
            at=hour(12),
            remove_relationship_ids=[path.relationship_ids[0]],
        )
    )
    assert simulation.before.findings and not simulation.after.findings
    assert simulation.removed_path_ids == [path.path_id]
    assert not simulation.graph_mutated
    assert not simulation.before.persisted and not simulation.after.persisted
    assert risk_engine.discover(AnalysisRequest(at=hour(12), scenario=Scenario.A)) == after
    connection = GraphDatabase(isolated_graph_settings)
    try:
        restored = RiskRepository(connection, risk_engine.repository.scope)
        assert restored.saved(after.analysis_id) == after
        assert restored.saved(before.analysis_id) == before
        assert restored.evidence(path.finding_id) == evidence
        assert (
            RiskEngine(restored).discover(AnalysisRequest(at=hour(12), scenario=Scenario.A))
            == after
        )
    finally:
        connection.close()


def test_timeline_has_four_historical_states(risk_engine: RiskEngine) -> None:
    result = risk_engine.timeline(
        TimelineRequest(scenario=Scenario.A, before=hour(9), after=hour(12))
    )
    assert [p.at for p in result.points] == [hour(h) for h in (9, 10, 11, 12)]
    assert [p.path_count for p in result.points] == [0, 0, 0, 1]
    assert result.points[-1].maximum_score == 51


def test_tenant_isolation_in_paths_saved_findings_and_simulation(risk_engine: RiskEngine) -> None:
    result = risk_engine.discover(AnalysisRequest(at=hour(12), scenario=Scenario.A))
    unrelated = RiskEngine(
        RiskRepository(
            risk_engine.repository.database,
            TenantScope(tenant_id="unrelated-tenant"),
        )
    )
    assert unrelated.discover(AnalysisRequest(at=hour(12))).findings == []
    with pytest.raises(GraphNotFound):
        unrelated.repository.saved(result.analysis_id)
    with pytest.raises(GraphNotFound):
        unrelated.repository.evidence(result.findings[0].finding_id)
    with pytest.raises(GraphNotFound):
        unrelated.what_if(
            WhatIfRequest(
                at=hour(12), remove_relationship_ids=[result.findings[0].relationship_ids[0]]
            )
        )


def test_bounded_depth_and_policy_grant_removal(risk_engine: RiskEngine) -> None:
    assert not risk_engine.discover(
        AnalysisRequest(at=hour(12), scenario=Scenario.A, max_role_hops=1)
    ).findings
    after = risk_engine.discover(AnalysisRequest(at=hour(12), scenario=Scenario.A))
    grant = after.findings[0].relationship_ids[-2]
    simulation = risk_engine.what_if(
        WhatIfRequest(at=hour(12), scenario=Scenario.A, remove_relationship_ids=[grant])
    )
    assert not simulation.after.findings
    assert risk_engine.discover(AnalysisRequest(at=hour(12), scenario=Scenario.A)) == after


def test_same_analysis_is_idempotent_under_concurrency(risk_engine: RiskEngine) -> None:
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: risk_engine.discover(AnalysisRequest(at=hour(12), scenario=Scenario.A)),
                range(4),
            )
        )
    assert all(result == results[0] for result in results)


def test_findings_and_evidence_survive_synthetic_reset(risk_engine: RiskEngine) -> None:
    after = risk_engine.discover(AnalysisRequest(at=hour(12), scenario=Scenario.A))
    finding = after.findings[0]
    evidence = risk_engine.repository.evidence(finding.finding_id)
    events = EventIngestionService(
        DemoRepository(risk_engine.repository.database, risk_engine.repository.scope)
    )
    events.reset(Scenario.A, 0)
    assert not risk_engine.discover(AnalysisRequest(at=hour(12), scenario=Scenario.A)).findings
    assert risk_engine.repository.saved(after.analysis_id) == after
    assert risk_engine.repository.evidence(finding.finding_id) == evidence


def test_historical_revocation_and_removed_path_difference(risk_engine: RiskEngine) -> None:
    events = EventIngestionService(
        DemoRepository(risk_engine.repository.database, risk_engine.repository.scope)
    )
    events.ingest(
        SyntheticSecurityEvent(
            scenario=Scenario.A,
            source_id="a/risk-test-revoke",
            account_id="000000000001",
            sequence=4,
            occurred_at=hour(13),
            event_type="SyntheticAccessRevoked",
            close_edges=["a/alice-temp"],
        ),
        0,
    )
    assert risk_engine.discover(AnalysisRequest(at=hour(12), scenario=Scenario.A)).findings
    assert not risk_engine.discover(AnalysisRequest(at=hour(13), scenario=Scenario.A)).findings
    difference = risk_engine.difference(
        DifferenceRequest(before=hour(12), after=hour(13), scenario=Scenario.A)
    )
    assert len(difference.removed_path_ids) == 1


def test_scenario_b_temporary_violation_increases_score(risk_engine: RiskEngine) -> None:
    events = EventIngestionService(
        DemoRepository(risk_engine.repository.database, risk_engine.repository.scope)
    )
    events.replay(Scenario.B, 0)
    initial = risk_engine.discover(AnalysisRequest(at=hour(10), scenario=Scenario.B)).findings[0]
    stale = risk_engine.discover(AnalysisRequest(at=hour(11), scenario=Scenario.B)).findings[0]
    assert stale.score.value == initial.score.value + 12
    factor = next(f for f in stale.score.factors if f.name == "temporary_access_violation")
    assert factor.points == 12 and factor.record_ids


def test_concurrent_event_arrival_refreshes_timeline_sampling(risk_engine: RiskEngine, monkeypatch):
    original = risk_engine.repository.event_history
    calls = 0

    def arriving(options, before, after):
        nonlocal calls
        calls += 1
        result = original(options, before, after)
        if calls == 1:
            events = EventIngestionService(
                DemoRepository(risk_engine.repository.database, risk_engine.repository.scope)
            )
            events.ingest(
                SyntheticSecurityEvent(
                    scenario=Scenario.A,
                    source_id="a/concurrent-evidence",
                    account_id="000000000001",
                    sequence=4,
                    occurred_at=hour(12) + timedelta(minutes=30),
                    event_type="SyntheticAdditionalObservation",
                ),
                0,
            )
        return result

    monkeypatch.setattr(risk_engine.repository, "event_history", arriving)
    timeline = risk_engine.timeline(
        TimelineRequest(before=hour(9), after=hour(13), scenario=Scenario.A)
    )
    assert calls == 2
    assert hour(12) + timedelta(minutes=30) in [p.at for p in timeline.points]

import json
from unittest.mock import patch
from urllib.error import HTTPError

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.integrations.model_provider import (
    GeminiProvider,
    MockProvider,
    PromptFacts,
    model_provider,
)
from app.models.workflow import AgentKey, ModelNote, SimulationProposal, TriggerInvestigation
from app.services.workflow_errors import ProviderUnavailable

ID = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"


def facts():
    return PromptFacts(
        stage=AgentKey.RISK_ORACLE,
        evidence_ids=[ID],
        finding_count=1,
        memory_count=2,
        change_count=3,
    )


def settings(**values):
    return Settings(
        _env_file=None, agent_provider="gemini", gemini_api_key="synthetic-test-only", **values
    )


def envelope(**values):
    note = {
        "summary": "Potential modeled path needs review",
        "evidence_ids": [ID],
        "confidence": 0.5,
        **values,
    }
    return {
        "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(note)}]}}]
    }


def test_mock_is_deterministic_and_needs_no_key():
    config = Settings(_env_file=None)
    assert isinstance(model_provider(config), MockProvider)
    assert MockProvider().summarize(facts()) == MockProvider().summarize(facts())


def test_gemini_wire_request_is_structured_and_contains_no_raw_sources():
    provider = GeminiProvider(settings())
    with patch("app.integrations.model_provider.build_opener") as factory:
        factory.return_value.open.return_value.__enter__.return_value.read.return_value = (
            json.dumps(envelope()).encode()
        )
        result = provider.summarize(facts())
    request = factory.return_value.open.call_args.args[0]
    payload = json.loads(request.data)
    assert request.full_url.startswith("https://generativelanguage.googleapis.com/")
    assert "synthetic-test-only" not in request.full_url
    assert "synthetic-test-only" not in json.dumps(payload)
    assert "tools" not in payload
    assert payload["generationConfig"]["responseFormat"]["text"]["mimeType"] == "application/json"
    assert json.loads(payload["contents"][0]["parts"][0]["text"]) == facts().model_dump(mode="json")
    assert factory.return_value.open.call_args.kwargs["timeout"] == 5
    assert result.evidence_ids == [ID]


@pytest.mark.parametrize(
    "values",
    [
        {"evidence_ids": [OTHER]},
        {"confidence": 1.1},
        {"confidence": float("nan")},
        {"execute": "delete IAM"},
        {"summary": "password=bad"},
        {"evidence_ids": []},
    ],
)
def test_invalid_or_privileged_output_fails_after_bounded_attempts(values):
    provider = GeminiProvider(settings())
    with patch.object(provider, "_request", return_value=envelope(**values)) as request:
        with pytest.raises(ProviderUnavailable):
            provider.summarize(facts())
        assert request.call_count == 2


@pytest.mark.parametrize("code,attempts", [(401, 1), (400, 1), (429, 2), (503, 2)])
def test_http_errors_have_bounded_retry_policy(code, attempts):
    provider = GeminiProvider(settings())
    with patch.object(
        provider, "_request", side_effect=HTTPError("", code, "", {}, None)
    ) as request:
        with pytest.raises(ProviderUnavailable):
            provider.summarize(facts())
        assert request.call_count == attempts


def test_tool_call_output_cannot_cross_the_text_boundary():
    provider = GeminiProvider(settings())
    body = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {"parts": [{"functionCall": {"name": "delete_role"}}]},
            }
        ]
    }
    with patch.object(provider, "_request", return_value=body):
        with pytest.raises(ProviderUnavailable):
            provider.summarize(facts())


@pytest.mark.parametrize(
    "override",
    [
        {"agent_model_attempts": 10},
        {"agent_model_timeout_seconds": 100},
        {"workflow_lease_seconds": 1},
        {"workflow_max_attempts": 99},
        {"gemini_model": "../evil"},
    ],
)
def test_provider_and_workflow_bounds_are_configuration_validated(override):
    with pytest.raises(ValidationError):
        settings(**override)


def test_gemini_requires_explicit_environment_key():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, agent_provider="gemini", gemini_api_key=None)


@pytest.mark.parametrize(
    "extra", [{"tenant_id": "other"}, {"agent_id": ID}, {"execute": True}, {"provider": "gemini"}]
)
def test_request_cannot_select_authority_or_executor(extra):
    with pytest.raises(ValidationError):
        TriggerInvestigation(request_id="test", event_id=ID, **extra)


def test_proposal_cannot_authorize_execution():
    with pytest.raises(ValidationError):
        SimulationProposal(
            relationship_id=ID,
            finding_ids=[],
            before_paths=1,
            after_paths=0,
            removed_path_ids=[],
            execution_allowed=True,
        )
    with pytest.raises(ValidationError):
        ModelNote(summary="do something", evidence_ids=[ID], confidence=0.5, commands=["run"])

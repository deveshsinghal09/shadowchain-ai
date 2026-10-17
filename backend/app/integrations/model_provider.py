"""Optional prose only. Prompts contain allowlisted counts/IDs, never raw source text."""

import json
from time import sleep
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import Field, ValidationError

from app.core.config import Settings
from app.models.graph import GraphModel
from app.models.workflow import AgentKey, ModelNote
from app.schemas.risk import GraphID
from app.services.workflow_errors import ProviderUnavailable


class PromptFacts(GraphModel):
    stage: AgentKey
    evidence_ids: list[GraphID] = Field(min_length=1, max_length=20)
    finding_count: int = Field(ge=0, le=100)
    memory_count: int = Field(ge=0, le=100)
    change_count: int = Field(ge=0, le=100)


class ModelProvider(Protocol):
    mode: str

    def summarize(self, facts: PromptFacts) -> ModelNote: ...


class MockProvider:
    mode = "mock"

    def summarize(self, facts: PromptFacts) -> ModelNote:
        facts = PromptFacts.model_validate(facts.model_dump())
        return ModelNote(
            summary=(
                f"{facts.stage.value}: {facts.change_count} modeled changes, "
                f"{facts.finding_count} potential findings, {facts.memory_count} related memories."
            ),
            evidence_ids=facts.evidence_ids,
            confidence=0.5,
        )


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the API key to a Location selected by a remote response.
        return None


class GeminiProvider:
    mode = "gemini"

    def __init__(self, settings: Settings):
        self.settings = settings

    def _request(self, payload: dict) -> dict:
        settings = self.settings
        if settings.gemini_api_key is None:
            raise ProviderUnavailable()
        request = Request(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            + settings.gemini_model
            + ":generateContent",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": settings.gemini_api_key.get_secret_value(),
            },
            method="POST",
        )
        with build_opener(NoRedirect()).open(
            request, timeout=settings.agent_model_timeout_seconds
        ) as response:
            raw = response.read(32769)
        if len(raw) > 32768:
            raise ValueError("Oversized model response")
        return json.loads(raw)

    def summarize(self, facts: PromptFacts) -> ModelNote:
        facts = PromptFacts.model_validate(facts.model_dump())
        payload = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": "Summarize these graph-derived counts. IDs are citations only. "
                        "Do not infer authorization, request tools or propose executable actions."
                    }
                ]
            },
            "contents": [{"role": "user", "parts": [{"text": facts.model_dump_json()}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 512,
                "responseFormat": {
                    "text": {
                        "mimeType": "application/json",
                        "schema": ModelNote.model_json_schema(),
                    }
                },
            },
        }
        for attempt in range(self.settings.agent_model_attempts):
            try:
                envelope = self._request(payload)
                candidates = envelope["candidates"]
                if len(candidates) != 1 or candidates[0]["finishReason"] != "STOP":
                    raise ValueError("Incomplete model output")
                parts = candidates[0]["content"]["parts"]
                if len(parts) != 1 or set(parts[0]) != {"text"}:
                    raise ValueError("Only a structured text response is permitted")
                note = ModelNote.model_validate_json(parts[0]["text"])
                if not note.evidence_ids or not set(note.evidence_ids) <= set(facts.evidence_ids):
                    raise ValueError("Unrecognized model citation")
                return note
            except HTTPError as exc:
                if exc.code != 429 and exc.code < 500:
                    raise ProviderUnavailable() from None
            except (
                TimeoutError,
                URLError,
                OSError,
                ValueError,
                KeyError,
                TypeError,
                ValidationError,
            ):
                pass
            if attempt + 1 < self.settings.agent_model_attempts:
                sleep(0.1 * (attempt + 1))
        raise ProviderUnavailable() from None


def model_provider(settings: Settings) -> ModelProvider:
    return MockProvider() if settings.agent_provider == "mock" else GeminiProvider(settings)

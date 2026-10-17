# Original work and third-party attribution

Task 1 and Phases 2–10 source code, synthetic fixtures, configuration, tests, scripts and documentation were created in
this workspace for this project. This records what was created here; hackathon eligibility
and submission dates must be checked against the organizer's actual rules. No pre-existing
third-party implementation is represented as original SHADOWCHAIN AI work.

Third-party dependencies and container images retain their upstream licenses. Exact Python
versions are recorded in the requirements locks; exact image digests are recorded in Docker
configuration. SHADOWCHAIN AI does not claim authorship of these components:

- [FastAPI](https://github.com/fastapi/fastapi),
  [Starlette](https://github.com/Kludex/starlette), and
  [Uvicorn](https://github.com/Kludex/uvicorn) provide ASGI and HTTP infrastructure.
- [Pydantic](https://github.com/pydantic/pydantic) and
  [pydantic-settings](https://github.com/pydantic/pydantic-settings) provide validation/configuration.
- [FalkorDB](https://github.com/FalkorDB/FalkorDB) provides the graph server; the
  [official Python client](https://github.com/FalkorDB/falkordb-py) provides graph access.
- [LangGraph](https://github.com/langchain-ai/langgraph) supplies the six-agent workflow DAG.
- [Boto3](https://github.com/boto/boto3), [Botocore](https://github.com/boto/botocore),
  JMESPath and S3Transfer provide AWS SDK interfaces and support libraries. Their operation
  models and retry/authentication code are upstream work.
- [websockets](https://github.com/python-websockets/websockets) supplies protocol transport
  and the native verification client. It is pinned explicitly in the runtime lock.
- [Pytest](https://github.com/pytest-dev/pytest), [HTTPX](https://github.com/encode/httpx),
  and [Ruff](https://github.com/astral-sh/ruff) provide testing and source checks.

Implementation references consulted:

- [FalkorDB deployment, persistence and networking documentation](https://docs.falkordb.com/operations/docker).
- [Official FalkorDB client usage and API source](https://github.com/FalkorDB/falkordb-py).
- [FalkorDB server image build source](https://github.com/FalkorDB/FalkorDB/blob/v4.22.1/build/docker/Dockerfile.server).
- [LangGraph persistence interfaces](https://docs.langchain.com/oss/python/langgraph/persistence).
- [Gemini structured outputs](https://ai.google.dev/gemini-api/docs/generate-content/structured-output)
  documents the optional REST response schema interface. No Google SDK or model is claimed
  as original project work.
- [FalkorDB MATCH](https://docs.falkordb.com/cypher/match),
  [functions](https://docs.falkordb.com/cypher/functions) and
  [GRAPH.QUERY](https://docs.falkordb.com/commands/graph.query) document the Cypher interfaces.
- [OWASP AI Agent Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html)
  and [Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)
  informed the memory data/instruction boundary and independently authorized access design.
- [AWS IAM evaluation logic](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html),
  [role caller/trust permissions](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_permissions-to-switch.html)
  and [S3 policy resources](https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-policy-language-overview.html)
  informed the documented synthetic policy subset; no AWS dataset or scanner was copied.

Upstream examples informed ordinary API usage. The synthetic dataset, temporal projections,
event ingestion, scenario replay, Cypher risk traversal, normalized authorization checks, scoring
and what-if analysis were developed here. Phase 5 memory services, graph access grants,
version/conflict handling, typed read-only procedure representation, handoffs and tests were
also developed here. Phase 6 agent handlers, graph reconstruction, fenced workflow transitions,
task coordination, model-provider boundary, recovery worker and tests were developed here.
Phase 7 case profiles, lifecycle/review service, shared-asset incident reconstruction,
case check ownership, audited cross-session checkpoints, synthetic history fixture, API
payloads and verification tests were developed here. Generated proof JSON is execution
output; runtime services never treat it as a persistent source of truth.
Phase 8 recommendation rules, ephemeral Cypher exclusions, collateral-access comparison,
proposal/simulation/review persistence, synthetic alternate-grant extension, APIs and tests
were developed here using the existing risk evaluator. No third-party remediation scanner
or AWS mutation implementation was copied or installed.
Phase 9 AWS dispatch guards, read-only normalization, atomic graph observations, report
redaction/publication, synthetic SDK fixtures, tests and setup documentation were developed
here. AWS SDK/API documentation informs the interface; SDK components are not original work.
Live account behavior remains unverified. No third-party agent/memory implementation or
dataset was copied. Privileged IAM execution is outside the implemented scope.

Phase 10 tenant-scoped bearer permissions, graph journal/query wrapper, API quota records,
visualization projections, canonical route adapters, reconnect protocol, OpenAPI extensions,
Windows credential setup and integration proofs were developed here. Generated samples are
synthetic execution output, not a borrowed dataset. Official
[FastAPI WebSocket](https://fastapi.tiangolo.com/advanced/websockets/),
[security](https://fastapi.tiangolo.com/reference/security/) and
[FalkorDB WITH](https://docs.falkordb.com/cypher/with) documentation informed the API usage.

Phase 11 evaluation orchestration, owned FastAPI process restart harness, graph-backed
evaluation receipts, deterministic fixture assertions, fresh-process memory retention
ablation and measured query instrumentation were developed here using existing services.
The negative control is original evaluation-only standard-library code, not a second
persistent application store. Recorded metrics and sample responses are execution artifacts;
no upstream benchmark results or performance improvements are claimed as project work.

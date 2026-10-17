# Phase 10 verification

Verified 17 October 2026 (Asia/Calcutta), using Python 3.12.12, FalkorDB server 4.22.1,
official FalkorDB client 1.8.0, FastAPI 0.143.2, Pydantic 2.14.1, LangGraph 1.2.15 and
websockets 16.1.1. Existing graph services were reused; no frontend or alternative database
was added. AWS/CloudTrail stayed disabled, report export local and model provider mock.

## Automated verification

The full suite passes **445 tests, zero skipped, in 302.84 seconds**: **197 real FalkorDB integration tests and
248 other tests**. Phase 10 adds 28 tests covering the contract, authentication, quotas and
replay. Existing API tests now send a synthetic test-only credential explicitly; that key
is never accepted by default runtime configuration. Test settings and the Compose test
profile explicitly disable live AWS/CloudTrail, select local export and use mock summaries.

Coverage includes:

- Required endpoints, typed success responses, bearer security, explicit permissions and
  shared error schemas in OpenAPI. UTC normalization accepts aware offsets and returns UTC.
- Missing credentials and empty client configuration fail closed; read-only credentials
  cannot mutate, write alone cannot review/synchronize AWS, and bodies cannot override scope.
- A complete synthetic investigation through canonical routes, graph evidence/path retrieval,
  dashboard/timeline/differences, six agent activity records, shared memories and case resume.
- Keyset pages have disjoint IDs, stale sequences return 409, and unrelated tenants receive
  no graph or journal data and cannot enter the synthetic control scope.
- A graph-backed per-principal query budget survives application/pool replacement, returns
  429/Retry-After, and leaves public health available. Oversized bodies fail safely.
- Browser-readable error envelopes and exposed retry/authentication headers preserve the
  configured CORS boundary, including oversized requests and unexpected errors. Preflight
  responses still carry a correlation ID. Secret-bearing transport logs are suppressed.
- WebSocket authentication, forbidden URL credentials/origins, strict control messages,
  source event ordering, exclusive replay cursors, reconnect after application replacement,
  invalid future cursors and tenant isolation.
- Same-statement retries do not repeat a committed mutation/journal append. A failed domain
  constraint rolls back its writes and notification/sequence. Concurrent commits produce
  contiguous isolated tenant sequences; frames remain bounded.
- Graph outages return 503 through HTTP and a typed WebSocket dependency error/1013 close,
  without substituting cached or fabricated events. Unified AWS synchronization remains
  disabled without constructing an SDK session.

The existing temporal, policy, memory, workflow, investigation, remediation and mocked AWS
regressions pass. Ruff checks/formatting cover 138 backend/test Python files plus the native
verification script (139 total). `pip check`, parsing of all 13 PowerShell scripts and
`docker compose config --quiet` pass. The generated OpenAPI validates against FastAPI's
OpenAPI model, and all local schema references resolve. Generated examples/proofs were checked
to omit both the actual operator token and its digest.

Tests use the existing isolated, authenticated, **nonpersistent** FalkorDB server on local
port 16380, unique per-test graphs and exact fixture cleanup. Persistent demonstration data
remains on the separate server; negative constraint fixtures never enter its recovery log.

```powershell
$env:FALKORDB_HOST = '127.0.0.1'
$env:FALKORDB_PORT = '16380'
$env:RUN_INTEGRATION_TESTS = 'true'
$env:AWS_ENABLED = 'false'
$env:CLOUDTRAIL_ENABLED = 'false'
$env:EXPORT_MODE = 'local'
$env:AWS_EC2_METADATA_DISABLED = 'true'
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
```

## Real HTTP and WebSocket demonstration

The running backend at `http://127.0.0.1:18000` uses graph
`shadowchain_phase10_verified` on the existing authenticated persistent FalkorDB server.
API setup generated a real random service credential outside the repository and stored only
its digest/configuration in ignored `.env`. Protected anonymous requests return 401; health
and readiness return 200. Browser/service authentication does not depend on AWS or an LLM.

`scripts/verify_api_contract.py` used real HTTPX and native websockets connections, not ASGI
mocks. It seeded/replayed the actual synthetic dataset, queried every graph page, completed
all six agents through `/agents/run`, inspected risks/cases/memories, resumed checks and
recomputed a grant exclusion. It generated [complete request/response examples](api_contract_examples.json),
[OpenAPI](OPENAPI_V1.json) and [the proof](api_contract_proof.json).

| Historical Scenario A snapshot | Active source edges across all pages |
| --- | --- |
| 09:00 UTC | 123 |
| 10:00 UTC | 126 |
| 11:00 UTC | 129 |
| 12:00 UTC | 130 |

These are graph membership counts, not exploitability verdicts. What-if removed the relevant
potential path; the complete source-edge projection hash stayed
`828a02ce26f4099cea1ba86058a8105db31ccd685b7285582c3ec918c7709b24` before and after.
The proof's 50-event replay page exactly matched a newly authenticated native WebSocket page.
The retained high watermark is 79, including actual source/workflow/memory/case/report commits.
Payload pagination is essential: a 100-edge first page alone is not the entire historical graph.

The demonstration server temporarily uses a 300-request/minute operator budget for verification
and repeated script runs; the shipped default remains 30 and the shared-limit test uses one.
No AWS SDK or cloud infrastructure operation was invoked by this proof. Optional AWS remains
separately gated and unverified live, as documented in Phase 9.

## Actual backend and database restart

The [restart proof](api_restart_proof.json) saved raw JSON for all 79 notifications, stopped
the backend, cleanly saved/stopped the persistent server and restarted both processes using
the same AOF/RDB data. The API returned the exact same events/cursor; a new native WebSocket
replayed the same complete page. Cursor was **79 before and after**, and readiness returned
`ready`. Canonical notification-payload SHA-256 is
`e29682725990f3407b8dd224930009b9d27cc85ce2b45a06616c0f2228d291e2`.
No process-local queue, event file or frontend data was used for server recovery.
The final native-socket check also rejects a binary authentication message with the typed
protocol error envelope, and verifies browser CORS/authentication and preflight correlation.

The authenticated PowerShell export helper also passed: report
`c50ad4e5-e946-5faa-a0d7-757cf88c0ed1` is PUBLISHED locally, 6,524 bytes, SHA-256
`0c4abb645b14166e604f4e25ddd315ffcfe12bd44668de256888ca33c168963e`.
The helper now uses a Phase 10 request ID. A reused earlier-phase artifact identity correctly
returned conflict rather than overwriting different report bytes; its PREPARED metadata was
retained for audit. No earlier graph, report artifact or investigation history was deleted.

## Limits and remaining deployment verification

- `docker compose build backend` was attempted and still fails because the
  `dockerDesktopLinuxEngine` named pipe is unavailable. Compose configuration validates;
  the changed image, transport flags and report-volume ownership need actual container
  verification when Docker Desktop's engine is available. Local Python and the existing
  pinned FalkorDB server extracted under WSL were used successfully instead.
- Live AWS checks were not authorized or run. No IAM permission/configuration was changed,
  no resource provisioned and no S3 upload made against AWS. Phase 9 mocked coverage remains.
- Static scoped service keys are implemented; identity federation, public deployment/TLS,
  centralized secret distribution and user-login UI are outside this phase. Production still
  disables local synthetic/AWS controls. There is no frontend or IAM execution capability.
- General snapshots/diffs reconstruct temporal membership and retained property values,
  not arbitrary mutable-property history. Immutable path evidence records the assessed view.
  Dashboard counts include retained historical findings and never certify effective access.
- Journal replay starts at Phase 10 instrumentation, has no exposed pruning policy and retains
  committed notifications. It is not a complete event-sourcing reducer. Synthetic reset can
  remove referenced source records while preserving notifications and independent audit evidence.
- Graph commit/notification are atomic; external report files/S3 are separate systems using
  PREPARED/PUBLISHED hash reconciliation. Operational rate counters and heartbeat frames do
  not produce domain notifications. Snapshot fences cover instrumented domain writes; external
  direct administrator Cypher is outside the API contract.

See [API_CONTRACT.md](API_CONTRACT.md) for exact authentication, bounds, reconnect semantics,
request examples, response fields, scope binding and consistent errors.

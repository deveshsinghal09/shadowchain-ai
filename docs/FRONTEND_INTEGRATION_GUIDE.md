# Frontend integration guide

The backend supports a local authenticated React/Three.js demonstration. No frontend is
implemented here. Public production deployment still requires the controls described in
[DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md). The audit and verification scope are in
[BACKEND_COMPLETION_REPORT.md](BACKEND_COMPLETION_REPORT.md).

## Contract and authentication

Use `/api/v1`, [OPENAPI_V1.json](OPENAPI_V1.json), and [API_CONTRACT.md](API_CONTRACT.md).
The server exposes `/docs` and `/openapi.json` in development. OpenAPI specifies request
fields, response types, bounds, and `x-required-permissions`; the WebSocket extension
documents its separate protocol. Extra request fields are rejected.

The current [executed API proof](backend_audit_api_contract_proof.json) and
[requests/responses](backend_audit_api_contract_examples.json) come from a real updated
FastAPI/FalkorDB run. Earlier phase receipts remain historical examples.
The [subsequent fresh-graph API recheck](backend_api_recheck.json) reproduced the six-agent
workflow, historical graph changes, replayable WebSocket events and source-preserving
simulation; its generated OpenAPI still matches the documented contract.

HTTP data requests require `Authorization: Bearer <operator-token>`. Health/readiness are
public. The server binds each key to a tenant; do not send a tenant or agent override.
`read` permits views; POST also needs `write`; reviews and manual case transitions also
need `review`. AWS synchronization additionally needs `aws:sync` and its configured AWS
tenant. Graph memory membership remains a separate authorization check.

Do not embed a shared operator key in a React bundle, `VITE_*` setting, source control,
URL, localStorage, or exported diagnostic. A deployed UI needs a server-side session/BFF
proxy retaining the service key and authorizing each operation. That login/proxy is future
work. For a loopback hackathon only, an operator may supply the key to an ephemeral
in-memory browser session; clear it on logout and never persist/log it. Render source
labels, evidence, memories and model prose as text, never HTML or executable instructions.

Configure exact browser origins and restart the backend:

```dotenv
CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]
CORS_ALLOW_CREDENTIALS=false
AWS_ENABLED=false
CLOUDTRAIL_ENABLED=false
EXPORT_MODE=local
AGENT_PROVIDER=mock
```

REST CORS and WebSocket Origin checks both use this list. A reverse proxy can provide a
same-origin browser API, but must preserve the intended Origin boundary. Development
Compose listens at `http://127.0.0.1:8000`; this workstation's native verification uses
port 18000. Use the endpoint actually started, rather than hardcoding that workstation port.

## Panels and sources

| Panel | Backend source |
| --- | --- |
| Overview | `GET /dashboard/summary` |
| 3D cloud graph | `GET /graph/snapshot`, independently page nodes and edges |
| Historical changes | `GET /graph/diff`, `GET /graph/timeline` |
| Saved risk paths | `GET /risks`, `GET /risks/{risk_id}`, `GET /graph/path?risk_id=...` |
| New historical analysis | `POST /risk/paths/historical` or `/risk/paths/discover` |
| Temporal reachability comparison | `POST /risk/differences`, `/risk/timeline` |
| Agent activity | `GET /agents`, `/agents/runs?investigation_id=...` |
| Shared memory | `GET /agents/memory?investigation_id=...` |
| Workflow handoffs | `GET /agents/investigations/{workflow_id}/handoffs` |
| SOC investigations | `GET /investigations`, `/investigations/{case_id}` |
| Case timeline/evidence/graph | `GET /investigations/{case_id}/timeline`, `/evidence/{evidence_id}`, `/graph` |
| Quick hypothetical preview | `POST /what-if/remove-grant` |
| Durable proposals and reviews | `/remediation/*`; see the OpenAPI and [remediation.md](remediation.md) |
| Persisted notifications | WebSocket `/ws/live`, HTTP `GET /live/events` |

The technical workflow investigation ID returned by `/agents/run` and the SOC case ID
returned by `/investigations` are distinct. A provider event's `event_id` and its graph
node `id` are also distinct. `/agents/run.event_id` takes the graph SecurityEvent node ID.
Agent definitions are six logical services in a bounded workflow, not six autonomous
operating-system processes. `/agents/run` completes synchronously; do not assume it returns
a background job token. Use stream events plus refetches to show persisted progress.

## Graph rendering and pagination

`VisualNode` provides stable `id`, `type`, `label`, scalar `attributes`, validity and
creation/update timestamps, evidence IDs, risk severity and case associations. Edges add
`source`, `target`, `source_event_id`. Responses include `snapshot_at`, `snapshot_sequence`,
`has_more`, and `next_cursor`. Null `valid_to` means open-ended. Intervals are
`[valid_from, valid_to)` in UTC; an edge ending at 11:00 is absent at exactly 11:00.

1. Select an explicit UTC `at`; request `collection=nodes&limit=100`.
2. Page with the same filters, timestamp, `cursor=next_cursor`, and `snapshot_sequence`.
3. Request `collection=edges` with the same timestamp and sequence; page it independently.
4. Join by stable IDs. Do not create an edge until its endpoints have loaded; fetch all
   relevant node pages before treating a missing endpoint as an error.
5. On 409 `cursor_conflict`, discard the incomplete combined view and start a new snapshot.
   Writes during pagination invalidate the fence; a cursor alone is insufficient.

When `at` is omitted on the first page, retain its returned `snapshot_at` for later pages.
Do not derive node IDs from labels, use array indexes as IDs, or invent coordinates in the
backend. Three.js may compute its own layout from these stable references. Associations
are capped at 64 and explicitly mark truncation; path projections are capped at 250 nodes
and 250 edges. No-association/null-severity means unknown in this projection, not safe.

General graph history reconstructs temporal membership using retained property values;
`historical_attributes=retained-record-values` is intentional. `/graph/diff` compares
membership, not every historical property edit. `/graph/path` instead uses immutable
evidence snapshots from a saved analysis, including records preserved across a demo reset.

Use the risk engine's `DiscoveryResult` for security verdicts. The older
`/demo/scenarios/{scenario}/snapshot` has a simpler synthetic Python policy preview;
its `potential_sensitive_paths` is not the canonical Cypher risk result.

## Reproducible interaction

These PowerShell requests are equivalent to future UI actions. Run from the repository:

```powershell
. .\scripts\api-client.ps1
$headers = Get-ShadowChainHeaders
$base = 'http://127.0.0.1:8000/api/v1'
Invoke-RestMethod "$base/demo/seed" -Method Post -Headers $headers
$state = Invoke-RestMethod "$base/demo/scenarios/A" -Headers $headers
$body = @{scenario='A'; expected_generation=$state.generation} | ConvertTo-Json
Invoke-RestMethod "$base/demo/replay" -Method Post -Headers $headers `
    -ContentType application/json -Body $body
$analysis = @{scenario='A'; at='2026-10-16T12:00:00Z'} | ConvertTo-Json
$paths = Invoke-RestMethod "$base/risk/paths/discover" -Method Post -Headers $headers `
    -ContentType application/json -Body $analysis
$riskId = $paths.findings[0].finding_id
Invoke-RestMethod "$base/graph/path?risk_id=$riskId" -Headers $headers
```

Baseline 09:00, intermediate 10:00/11:00 and final 12:00 are on **16 October 2026 UTC**,
not today's date. Expected canonical sensitive path counts are 0/0/0/1. A reset needs the
current `expected_generation` and intentionally removes raw source records for only that
synthetic scenario. Retained analyses, evidence snapshots and audits are separate history.
Use different request IDs for different workflow actions; retry the same logical action
with the same request ID. Conflicting reuse or stale revisions/generations returns 409.

## WebSocket client behavior

Connect to `ws://127.0.0.1:8000/api/v1/ws/live` (WSS behind TLS). Browser WebSocket cannot
set the HTTP Authorization header; send this first text frame within five seconds:

```json
{"schema_version":"1","type":"authenticate","token":"<ephemeral-token>","after":0,"limit":50}
```

Never put credentials in the URL; query parameters are rejected. Receive a `hello` control
frame, followed by `events` pages when persisted events exist. An empty journal need not
emit an empty page. Each event includes `event_id`, tenant `sequence`, `type`, processing
`occurred_at`, entity IDs, and a truncation flag. The notification timestamp is a commit
observation; retrieve source timestamps from the event/timeline API.

Apply a page idempotently, then remember its `cursor` as the last processed sequence.
Reconnect with `after=cursor` (exclusive). Delivery is at least once; deduplicate by event
ID/sequence. Refetch affected resources rather than treating a notification as a complete
graph delta. Debounce refreshes and respect HTTP quotas. `{"type":"ping"}` is optional;
the server also sends heartbeat controls. Pings do not advance the event cursor.

Connections last at most one hour; reconnect and authenticate. Authentication frames are
bounded to 4 KiB, control messages to 64 characters, outgoing frames to 64 KiB and pages
to 100 events. Connection attempts are limited to ten per minute per principal. Slow
clients have send timeouts. Persist only cursors for the appropriate tenant/session, never
the token. A fresh/reset graph may reject a cursor from another history.

| Close/error | Client action |
| --- | --- |
| 4401 / 4403 | Stop retries until credentials/permissions/origin are corrected |
| 4409, HTTP 409 | Refetch a complete view and establish a new journal cursor |
| 4400 | Fix the malformed/oversized protocol request |
| 1013, HTTP 429/503 | Back off with jitter; honor HTTP `Retry-After` |
| 4000 or network disconnect | Reconnect using the last successfully applied cursor |

HTTP errors share `{error:{code,message,details},request_id,timestamp}`. Display the safe
message and correlation ID; avoid logging request bodies, tokens or raw evidence. A 503
means dependent operations are unavailable, even if `/health` remains 200. Never display
stale graph data as fresh after an outage. Query budget defaults to 30 authenticated data
requests/minute; development demos may explicitly set 300 and restart. Avoid polling every
panel each frame; events, batching, pagination and debouncing are required.

## Security presentation requirements

Always retain **POTENTIAL ATTACK PATH**, score explanation, evaluated timestamp, evidence
links, assumptions, rejected/unresolved candidates and limitations. Zero findings is not
a certification of AWS safety. Timeline attribution identifies modeled change provenance,
not proven malicious causality. Superseded/conflicting memories must not appear as current
verified facts. Distinguish verified source integrity from verified effective permission.

Quick `/what-if/remove-grant` is a computed preview with `graph_mutated=false`; it does
not create a durable remediation case. Use the full `/remediation` proposal/simulation/review
workflow when a saved, evidence-backed review history is needed. An APPROVED recommendation
still has no execution authority. There is no AWS remediation executor. Show collateral
access impacts as potential effects requiring owner validation.

The demo, memory, agents, case and durable remediation controls are development adapters
for the fixed synthetic tenant and are disabled in production. Optional AWS import is
separate inventory/history coverage, not a complete live AWS authorization engine.

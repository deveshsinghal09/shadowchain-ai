# SHADOWCHAIN API contract v1

Base path: `/api/v1`. JSON uses snake_case, stable application UUIDs and UTC ISO-8601
timestamps. Temporal intervals are half-open `[valid_from, valid_to)`; null `valid_to` is
open-ended. No frontend is implemented. Findings are **POTENTIAL ATTACK PATHS**, not proof
of effective AWS authorization. No endpoint executes IAM remediation.

The generated [OpenAPI document](OPENAPI_V1.json) describes every HTTP request, response,
enum, bound and error. Development `/docs` and `/openapi.json` expose the same contract;
production disables these documentation routes. `x-required-permissions` documents each
operation's bearer permissions. `x-websocket-contract` and its Pydantic component schemas
describe the WebSocket extension, which OpenAPI does not natively describe as an HTTP operation.
[Machine-readable examples](api_contract_examples.json) contain actual synthetic requests
and responses; [the executed proof](api_contract_proof.json) records the real HTTP/socket run.
These files are documentation artifacts, never runtime state or event sources.

## Authentication and authorization

Only health/readiness are public. Every existing and new data or mutation route requires
`Authorization: Bearer <token>`. There is no unauthenticated demo bypass, built-in password,
default accepted key or credential in the repository. An empty `API_CLIENTS=[]` fails closed.

`API_CLIENTS` is an administrator-owned JSON array of `principal_id`, `tenant_id`,
`token_sha256` and explicit `permissions`. Tokens must be 32–512 characters; use the setup
script's 32-byte cryptographically random key. The server compares SHA-256 digests using
constant-time comparisons. It stores no bearer token in FalkorDB, logs, OpenAPI or sample files.
This is a scoped service-key contract, not an OIDC login/session service.

| Permission | Capability |
| --- | --- |
| `read` | Required for all protected operations; tenant-scoped views and stream metadata |
| `write` | Additionally required for every POST, including older read-by-POST analysis APIs |
| `review` | Additionally required for review/reviews endpoints and manual investigation lifecycle transitions |
| `aws:sync` | Additionally required for all AWS synchronization endpoints |

The server derives tenant from the matched credential. Request bodies/query parameters cannot
select tenant, agent authority, credentials or Cypher. Synthetic operations and their existing
memory/investigation services require `shadowchain-synthetic-demo`; another tenant receives
403 instead of accessing that fixed scope. General graph/dashboard/risk views and live replay
use the authenticated tenant. AWS synchronization/import inspection additionally requires the
configured `AWS_TENANT_ID`, and its existing account confirmation/STS scope guard remains.

API permission is separate from graph-based agent membership/source grants. Case/memory
services still enforce those graph ACLs. Synthetic operator actions delegate to the fixed
Coordinator/Scout bindings, with the graph-authorized Verifier curator used for memory reviews;
remediation review audit records the authenticated principal ID.
There is no LLM-selected principal. Broad tenant `read` grants metadata visibility; sensitive
memory content is retrieved through the existing membership-aware memory service.

Windows setup and an authenticated request:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
# Existing installations can add authentication without reinstalling Python:
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup-api-auth.ps1
. .\scripts\api-client.ps1
$headers = Get-ShadowChainHeaders
$base = 'http://127.0.0.1:8000/api/v1'
Invoke-RestMethod "$base/dashboard/summary" -Headers $headers
```

Setup writes only the digest/configuration to ignored `.env`; the token is stored in a
user-only ACL directory under `%LOCALAPPDATA%\ShadowChainAI`, outside this OneDrive workspace.
It preserves existing principals/credentials and never prints the token. Restart the backend
to apply configuration; existing demo scripts now load authentication through the helper.
`SHADOWCHAIN_API_TOKEN` can supply an operator-managed token in the current shell instead.

To revoke a principal, remove its entry from `.env` and restart all workers. To rotate, add
a new principal/key using setup with a distinct `-PrincipalId`, distribute that credential
through your normal secret channel, then remove the old entry and restart. There is no
public token-issuance endpoint. For an external deployment, terminate HTTPS/WSS and protect
the operator credential; the local verification server stays on loopback. A future browser
should keep bearer material out of URLs, logs, exported artifacts and persistent browser storage.

## Canonical endpoints

All endpoints below are implemented. Existing scenario/risk/memory/remediation/agent detail
routes remain compatible aliases, now authenticated and described in OpenAPI.

| Method/path, relative to `/api/v1` | Request and result |
| --- | --- |
| `GET /health` | Process liveness; UTC timestamp/version/demo flag; no DB requirement |
| `GET /ready` | Real Cypher/schema verification; 503 on dependency outage |
| `GET /dashboard/summary` | Entity counts, persisted risk severities, case/task statuses and live sequence |
| `GET /graph/snapshot` | `at`, `collection=nodes\|edges`, optional `scenario`, `node_type`, pagination; `VisualSnapshot` |
| `GET /graph/diff` | Required `before`, `after`; `collection`, optional `scenario`, pagination; membership additions/removals |
| `GET /graph/path` | Required `risk_id`; immutable evidence-backed nodes/edges for that saved finding |
| `GET /graph/timeline` | Required `before`, `after`; source security events in `[before,after)`, pagination |
| `POST /demo/seed` | No body; idempotent synthetic dataset seed and scenario status |
| `POST /demo/reset` | `{scenario, expected_generation}`; resets one synthetic scenario and advances generation |
| `POST /demo/replay` | `{scenario, expected_generation, through?}`; reproducible event replay |
| `POST /events/ingest` | `{event: SyntheticSecurityEvent, expected_generation}`; idempotent receipt |
| `GET /risks` | Paginated persisted findings; no hidden discovery or authorization mutation |
| `GET /risks/{risk_id}` | Saved finding, assessed timestamp, evidence and investigation references |
| `GET /agents` | Six stable logical definitions, registration flag and persisted timestamps when registered |
| `POST /agents/run` | `{request_id, event_id, evidence_ids?, at?}`; bounded six-agent workflow, fully persisted |
| `GET /agents/runs` | Optional `investigation_id`, pagination; typed activity with tasks/memory/handoffs/evidence |
| `GET /agents/memory` | Required workflow `investigation_id`, `limit`, optional `cursor`; authorized shared memory |
| `GET /investigations` | Optional `status`, pagination; security cases, not technical workflow containers |
| `GET /investigations/{investigation_id}` | Complete case, evidence, hypotheses, history, tasks and audit |
| `POST /investigations/{investigation_id}/resume` | No body; idempotently completes the persisted owner's pending read-only checks |
| `POST /what-if/remove-grant` | `{at, scenario?, remove_relationship_ids, ...risk_options}`; read-only before/after reachability |
| `GET /aws/status` | Flags only; constructs no AWS session and exposes no account/profile/key |
| `POST /aws/sync` | Discriminated `kind=iam\|cloudtrail` request; explicit opt-in/account scope required |
| `GET /live/events` | Bounded HTTP replay of the same journal used by WebSocket; `after`, `limit` |
| `WS /ws/live` | First-frame bearer authentication, ordered replay and ongoing persisted notifications |

`GET /agents` is side-effect-free: it does not register agents. Registration occurs during
authorized workflow bootstrap. Dashboard finding counts include retained historical analyses;
they are not the number of currently exploitable paths. Risk discovery still occurs through
the existing deterministic risk service/agent workflow. AWS remains optional and disabled by
default; [AWS_SETUP.md](AWS_SETUP.md) documents coverage and permissions. Development demo,
case, agent, memory, remediation and AWS control gates remain disabled in production.

### Pagination and coherent graph views

Graph, risk, run and case pages use `limit=50` by default, maximum 100, a stable UUID
`cursor`, and `snapshot_sequence`. Timeline ordering is `(event_time,id)`; other collections
order by ID. A response with `has_more=true` contains `next_cursor`. `count` on case pages
is the page count, not a global total. Node and edge collections are fetched independently;
an edge's endpoints can be on different node pages.

For each subsequent page repeat the original timestamp/filters, pass `cursor=next_cursor`
and `snapshot_sequence` from the first response. The server rejects a missing sequence with
a cursor, or any intervening journaled mutation, with 409 `cursor_conflict`. It also fences
multi-query projections against changes during the request. Fetch a fresh first page on 409.
If `at` was omitted, repeat the returned `snapshot_at` for later snapshot pages. Cursors are
typed identifiers, not signed authorization capabilities; clients must preserve their filters.

```powershell
$page = Invoke-RestMethod "$base/graph/snapshot?scenario=A&collection=nodes&limit=50" -Headers $headers
while ($page.has_more) {
    $at = [uri]::EscapeDataString($page.snapshot_at)
    $page = Invoke-RestMethod "$base/graph/snapshot?scenario=A&collection=nodes&limit=50&at=$at&cursor=$($page.next_cursor)&snapshot_sequence=$($page.snapshot_sequence)" -Headers $headers
}
```

Workflow memory retrieval uses current graph membership/source checks and a bounded set of
at most 100 authorized observations per investigation; its ID cursor pages that set. An
oversized source view fails clearly rather than claiming incomplete memory is complete.
Graph traversal remains bounded to at most four role hops; the existing evidence/context,
timeline and case limits remain enforced. Overly large context returns a typed 422 error.

### Graph visualization fields

`VisualNode` contains `id`, `type`, `label`, allowlisted scalar `attributes`, `created_at`,
`updated_at`, validity interval, `evidence_ids`, `risk_severity` and `investigation_ids`.
`VisualEdge` adds `source`, `target`, `source_event_id`. Responses include `snapshot_at`
and a live `snapshot_sequence`. Associations are graph-derived and capped at 64 per item;
`associations_truncated=true` makes truncation explicit. Null severity/empty associations mean
no association is available in this projection, not that a resource is safe.

Attributes omit arbitrary memory text, raw evidence JSON, procedures and credential fields.
Labels and separately retrieved descriptions/memories remain untrusted text; a future UI
must render them as text. Evidence-backed path views use retained immutable risk snapshots,
including source permissions and provenance IDs. Optional indexed `Evidence.record_id` speeds
new snapshot associations; old analyses remain available through the path/evidence APIs.

General graph views reconstruct temporal membership. `historical_attributes` explicitly says
`retained-record-values`: they do not invent old values for arbitrary mutable attributes.
`/graph/diff` compares temporal membership, not a complete event-sourced property history.
Use saved `/graph/path` evidence for attributes recorded at investigation/analysis time.

A shortened shape, with placeholders rather than usable source IDs:

```json
{
  "schema_version": "1",
  "snapshot_at": "2026-10-16T12:00:00Z",
  "snapshot_sequence": 4,
  "collection": "edges",
  "nodes": [],
  "edges": [{
    "schema_version": "1", "id": "<edge-uuid>", "type": "CAN_ASSUME", "label": "CAN_ASSUME",
    "source": "<identity-uuid>", "target": "<role-uuid>", "attributes": {},
    "valid_from": "2026-10-16T10:00:00Z", "valid_to": null,
    "created_at": "<ingestion-utc>", "updated_at": "<ingestion-utc>",
    "source_event_id": "<event-uuid>", "evidence_ids": ["<evidence-uuid>"],
    "investigation_ids": ["<case-uuid>"], "risk_severity": "medium",
    "associations_truncated": false
  }],
  "next_cursor": null, "has_more": false,
  "historical_attributes": "retained-record-values"
}
```

Agent activity embeds the existing typed run record with agent identity, status, input
evidence, output findings, pending tasks, handoff target, operation ID and UTC timestamps.
It adds `task_statuses`, `memory_read_ids`, `memory_written_ids`, `handoff_ids`,
`evidence_produced_ids` and `output_evidence_ids`. Referenced input evidence is not relabeled
as newly produced evidence; Risk Oracle's produced snapshots are traversed through findings
and their originating run. Empty output is valid for an unfinished stage.

## Requests for synthetic development

```powershell
Invoke-RestMethod "$base/demo/seed" -Headers $headers -Method Post
$state = Invoke-RestMethod "$base/demo/scenarios/A" -Headers $headers
$body = @{scenario='A'; expected_generation=$state.generation; through='2026-10-16T12:00:00Z'} | ConvertTo-Json
Invoke-RestMethod "$base/demo/replay" -Headers $headers -Method Post -ContentType 'application/json' -Body $body

# Ingest an actual deterministic fixture through the canonical ingestion endpoint:
$catalog = Invoke-RestMethod "$base/demo/scenarios" -Headers $headers
$state = Invoke-RestMethod "$base/demo/scenarios/C" -Headers $headers
$body = @{event=$catalog.scenarios.C[0]; expected_generation=$state.generation} | ConvertTo-Json -Depth 50
Invoke-RestMethod "$base/events/ingest" -Headers $headers -Method Post -ContentType 'application/json' -Body $body

# Whole API proof, including real WebSocket replay; defaults to local port 18000:
.\backend\.venv\Scripts\python.exe .\scripts\verify_api_contract.py --token-file (Get-ShadowChainTokenPath)
```

Ingestion accepts the closed synthetic event model, not arbitrary AWS commands or raw
CloudTrail payloads. Source IDs/content hashes prevent duplicate or conflicting event reuse;
generation checks protect resets and ordering checks reject invalid/late synthetic events.
Workflow `request_id` prevents duplicate tasks. Replaying a committed workflow request resumes
or returns its persisted state. A reset retried with an old generation returns 409 rather than
performing another reset. These operations do not share one universal idempotency-header scheme.

AWS unified request shapes, to be sent only after the separate AWS setup/authorization steps:

```json
{"kind":"iam","request_id":"operator/iam-1","confirm_account_id":"<12-digit-account>"}
```

```json
{"kind":"cloudtrail","request_id":"operator/trail-1","confirm_account_id":"<12-digit-account>",
 "start_time":"<UTC-within-past-90-days>","end_time":"<later-UTC-not-in-future>"}
```

## Durable WebSocket protocol

Open `/api/v1/ws/live` and send this text frame within five seconds:

```json
{"type":"authenticate","token":"<bearer-token>","after":0,"limit":50}
```

`schema_version="1"` may also be supplied. No URL query parameters, cookie authentication,
tenant override or command message is accepted. Browser `Origin` must match configured CORS
origins; a native client can omit Origin. Receiving no domain events does not authorize sending
mutations over the socket. Its only subsequent client message is `{"type":"ping"}`.

The server sends `hello` with the requested cursor and current high watermark, then typed
`events` pages. `after` is exclusive; sequence IDs are monotonically increasing **per tenant**,
assigned under the graph stream lock. Timestamps describe the backend write observation;
source event ordering/time is separately available from `/graph/timeline`.

```json
{"schema_version":"1","type":"hello","cursor":0,"high_watermark":4}
```

```json
{"schema_version":"1","type":"events","events":[{
  "schema_version":"1","event_id":"<notification-uuid>","sequence":1,
  "type":"demo.seeded","occurred_at":"<write-observation-utc>",
  "entity_ids":["<dataset-uuid>"],"references_truncated":false
}],"cursor":1,"high_watermark":4,"has_more":true}
```

Event types include `demo.seeded`, `demo.reset`, `security.event.ingested`,
`graph.record.changed`, `agent.workflow.changed`, `memory.changed`, `memory.access.changed`,
`risk.analysis.completed`, `investigation.changed`, `remediation.changed`,
`aws.sync.completed`, `report.prepared` and `report.published`.

Each event is appended **inside the same Cypher transaction** as its guarded domain write.
Failed/rolled-back writes produce no event or sequence increment. An SDK retry of the same
statement cannot reapply its committed mutation/event. Replay of an already-ingested source
event produces no second ingestion notification. Background workflow recovery uses the same
journal. Quota counters, readiness checks and idle recovery polling do not create animations
or domain events. Control `pong`/15-second `heartbeat` frames are explicitly not persisted events.

The journal is notification history, not a second cloud-state store or a complete event-sourcing
reducer. Entity references can be unavailable after an explicitly authorized synthetic reset;
refresh graph views on reset/change. The stream starts with instrumented Phase 10 commits;
older stored records are queried normally rather than generating fabricated historical events.

### Reconnect, bounds and failures

After processing an entire page, retain its `cursor` in client operational state. Reconnect
and authenticate with that `after` value. Delivery is replayable at-least-once; deduplicate by
`event_id`/sequence if acknowledgment was uncertain. The server reads FalkorDB after reconnect
or process restart, never a Python event queue or file. For an initial coherent UI, fetch all
snapshot pages at one sequence and subscribe after that sequence; changes committed afterward
will be replayed. The server never advances a consumer cursor just because a heartbeat was sent.

Pages contain at most 100 events, at most 64 UUID references per event and at most 64 KiB per
server text frame; byte bounds can shorten a page below the requested limit. `has_more` and
`high_watermark` indicate remaining journal data. No automatic journal pruning is implemented.
Future/out-of-range cursors or missing retained sequence entries fail with `cursor_conflict`;
fetch a fresh coherent snapshot and reconnect after its sequence. There is no in-memory fallback.

The ASGI handler bounds authentication frames to 4 KiB and later control messages to 64
characters. The Docker command also bounds WebSocket transport frames/queue. Sends time out
after `WS_SEND_TIMEOUT_SECONDS` (default five); polling defaults to one second. A connection
expires after one hour and must authenticate again. Each principal has a shared ten-connects/
minute budget in FalkorDB. CORS is not a substitute for authentication.

Errors send the same error envelope plus `schema_version="1"`, `type="error"`, then close:
4401 authentication, 4403 permission/origin, 4409 cursor, 4400 invalid/timed-out protocol,
1013 dependency outage or quota exhaustion; 4000 requests periodic reauthentication.
Use bounded reconnect backoff, respect a quota error's one-minute window and stop automatic
retries for invalid credentials. Transport logging suppresses credential-bearing frames/URLs.

## HTTP limits and error contract

Protected requests other than cheap `/aws/status` share the configured fixed-minute query
budget per tenant/principal in FalkorDB (`API_QUERY_LIMIT_PER_MINUTE=30` by default). It survives
worker restarts and applies to legacy expensive routes too. HTTP 429 includes `Retry-After: 60`;
health/readiness remain available. Operational quota writes do not advance snapshot sequences.
JSON request bodies are bounded to 128 KiB by default, including chunked bodies. Pagination,
context/association caps and query/socket timeouts bound work and results; bounds are not
claims that the whole tenant inventory fits one response.
Configured browser origins can read correlation, retry and authentication headers, including
error responses; preflight responses retain a correlation ID. WebSocket transport-level
message/encoding failures can also close through the underlying protocol before an API frame.

```json
{
  "error":{"code":"authentication_required","message":"A valid bearer credential is required","details":[]},
  "request_id":"<correlation-uuid>","timestamp":"<utc-timestamp>"
}
```

Common statuses: 401 missing/invalid bearer (`WWW-Authenticate: Bearer`); 403 permission/scope
denied; 404 missing scoped record or disabled control; 409 conflicting generation/version/
cursor; 413 oversized body; 422 closed-schema/bounded-view validation; 429 query quota; 503
FalkorDB unavailable (`Retry-After: 3`). Unexpected exceptions return a generic 500. Validation
errors expose location/type, not request values or raw exception text. `X-Request-ID` is a UUID
correlation header, not identity or authorization. OpenAPI declares the shared error schema.

See [Phase 10 verification](phase10_verification.md) for actual test, restart, HTTP/socket and
Docker limitations. Live AWS was not contacted, and no cloud infrastructure was provisioned.

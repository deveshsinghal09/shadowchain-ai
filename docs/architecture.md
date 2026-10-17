# Backend architecture and storage contract

## Implemented foundation and graph layer

The FastAPI factory owns a bounded official FalkorDB client connection pool for each worker
process. Synchronous routes and startup DB work run in Starlette's thread pool, so socket
I/O does not block the event loop. Connect, socket, pool-acquisition and Cypher execution
timeouts are bounded. Transient reads and explicitly idempotent writes use bounded retries;
unknown outcomes of non-idempotent writes fail for reconciliation. Shutdown disconnects the pool.

The original bootstrap record remains compatible:

```text
(:Foundation {id: "shadowchain-foundation", schema_version: 1, created_at: UTC ISO-8601})
```

Startup uses a parameterized atomic Cypher `MERGE`. Repeated startups keep `created_at`
unchanged. The repository reads this record with `GRAPH.RO_QUERY`; readiness executes
`GRAPH.QUERY` with `RETURN 1`, which can initialize an empty graph after an initial outage.
A missing or unreachable graph module produces a safe 503. Raw Cypher and arbitrary graph
names are not exposed through public endpoints.

Phase 2 adds a verified `SchemaVersion` marker, the original 17 domain node types under `Entity`,
all 17 relationship types, database mandatory/uniqueness constraints and ID/scope/time indexes.
Startup and the initializer CLI apply the same versioned schema. Repositories bind a frozen
tenant context to every operation and preserve event/evidence provenance. Node/event creation
is immutable and idempotent; temporal relationships retain half-open validity history.
See [graph_schema.md](graph_schema.md) for the implemented contract and runnable examples.

## Temporal event engine

Phase 3 extends schema version 3 with CloudOrganization and graph-backed DemoDataset and
DemoStream control records. One reserved synthetic tenant contains the shared inventory and
three independently ordered scenario streams. The demo repository reuses the existing node,
relationship, provenance and timestamp validators and parameterized property builders.

EventIngestionService reads the persisted cursor and validates source ordering, generation,
event identity and content. DemoRepository applies each event, its nodes/edges, interval
closures and cursor advance in one guarded Cypher statement. Concurrent requests reconcile
against committed FalkorDB state. Late events are rejected explicitly, rather than silently
rewriting history. Reset is an atomic, generation-checked synthetic scenario operation.

Snapshots derive valid-time graph views, potential paths in a documented synthetic IAM
subset, expired business justifications and conflicting observations from graph reads.
Comparisons use one graph revision for both timestamps. Dataset templates and temporary
projection dictionaries are computation inputs, not durable event or coordination stores.
No application runtime JSON file, secondary database or in-memory fallback is involved.

Demo routes are fixed to the reserved tenant and unavailable in production or with demo mode
disabled. The seven-event manifest, all timestamps, IDs and semantic snapshot hashes are
reproducible. See [temporal_demo.md](temporal_demo.md) for the API and analysis boundaries.

FalkorDB data is saved to a named Docker volume. AOF and RDB persistence are enabled. The
DB has no published ports and is attached only to an `internal: true` Docker network. The
non-root backend is also attached to an egress network for optional integrations,
uses a read-only root filesystem, and drops Linux capabilities.

Configuration objects, socket pools and request IDs may live in process memory. They are
operational resources, not authoritative business state. JSON logs are operational output,
not the record of investigations or security evidence. Unit test mocks are confined to tests.

## Optional AWS reads and report artifacts

Phase 9 adds a lazy Boto3 boundary with a closed service/operation allowlist, bounded standard
retries, connect/read timeouts, pagination/record budgets and CloudTrail pacing. Defaults disable
AWS and CloudTrail, and local reports require no SDK session. Startup/recovery never imports AWS.
Explicit development requests confirm the configured account through STS before source reads.
Repository tenant and account come from settings, not request-selected credentials or prompts.

AwsSyncService stages sanitized observations as one bounded computation, then commits snapshot
entities, temporal edges, source evidence, import result and stream revision in one guarded
FalkorDB query. Prior IAM observations close only after a complete successful pull. CloudTrail
records deduplicate across windows and do not mutate modeled permissions. Python staging maps
and SDK client pools are ephemeral; committed history and operation identities live in FalkorDB.
The importer is conservative about unknown authorization constraints and does not establish
complete AWS effective access. Existing synthetic HTTP risk/agent routes retain their tenant.

ReportService retrieves an authorized case from the graph and renders a strict JSON summary
without cloud identifiers or arbitrary free text. Evidence:ReportExport metadata links to the
case and records publication status, hash and artifact destination. Local writes are exclusive;
optional S3 publication checks one private bucket/owner and uses encrypted conditional creation.
An external artifact write and a graph commit cannot be one distributed transaction: PREPARED
records reconcile explicit retries by content hash. Report files never contain authoritative
case state, and publication status does not monitor later deletion. No IAM execution or AWS
provisioning path exists. See [AWS_SETUP.md](AWS_SETUP.md) for permissions and coverage.

## Graph-powered risk engine

Phase 4 implements a separate RiskRepository/RiskEngine over the same primary graph. Cypher
enumerates bounded access paths and policy/evidence context, including temporal views in a
single server read. Python checks the documented normalized IAM subset and computes heuristic
scores. Immutable RiskAnalysis, RiskFinding and Evidence records persist each assessment;
what-if uses read-only queries and persists nothing. See [risk_engine.md](risk_engine.md).

## Persistent graph memory

Phase 5 adds MemoryRepository/MemoryService over the same graph and connection manager.
MemorySpace scopes explicit agent memberships and source-context grants. All episodic,
semantic and procedural observations, series heads, review decisions, contradictions,
corrections and handoff pointers persist in FalkorDB. Bounded Cypher traverses evidence and
related cloud/investigation records with tenant and source-access checks on intermediates.
Current server-time ACL checks remain separate from historical data time.

MemoryAccessAdmin is trusted bootstrap code, not an agent tool. Every memory mutation uses
one revision-guarded Cypher statement; request IDs reconcile retries across worker/process
restarts. Source text remains untrusted data. Curators can verify observations and closed
read-only procedure steps, but memory cannot grant roles, execute commands or modify IAM.
Handoff reads re-evaluate current permissions and series heads. See [memory.md](memory.md).

The demo HTTP binding fixes the Scout agent/space in the synthetic tenant and is disabled
in production. Future authenticated agent middleware must construct repository principals;
caller-supplied headers and prompts cannot select them. Phase 5 introduced no autonomous
runner; Phase 6 adds the bounded workflow below.

## Six-agent durable workflow

Phase 6 compiles a fixed six-node LangGraph DAG for Cloud Sentinel, Identity Guardian,
Risk Oracle, Memory Keeper, Coordinator and Response Architect. The LangGraph state carries
only an investigation ID. Each node reads the cursor, owned task, evidence, prior prepared
outputs and shared memories from FalkorDB. Default in-memory checkpointing is not configured.
WorkflowRepository performs graph-serialized claims and fenced prepare/complete transitions,
with unique operation Decisions and persistent retry budgets. The application starts a bounded
graph polling worker to recover incomplete local demo investigations, including expired leases.

Every stage uses MemoryService for evidence-backed shared observations and handoffs. The
Coordinator admits six step tasks, then creates finding-specific review tasks during its stage.
Response Architect persists proposals and invokes existing read-only what-if queries; review
tasks remain awaiting review after analysis completes. No IAM or command executor is present.

ModelProvider supports deterministic mock summaries and optional Gemini structured summaries.
Only allowlisted counts and graph IDs enter prompts. Model output cannot select tools,
permissions, graph traversal, task owners or routes. Production authentication and a live
cloud dispatcher remain future work. See [agents.md](agents.md) for persistence boundaries.

## Investigation intelligence

Phase 7 adds InvestigationRepository/InvestigationService over the same graph. A SecurityCase
profile on Investigation is the SOC lifecycle, distinct from the six-step WorkflowInvestigation.
Risk Oracle's durable prepared result admits cases by tenant, space and path. A CaseHead
selects the current episode; new assessments after resolution create a new episode while
old audit records remain. CaseStream serializes single-statement case/audit/task changes.

The workflow hook executes before completion and reconciles immutable operation IDs after
restarts. Memory Keeper completes two case checks, Coordinator transfers pending work and
its checkpoint, and Response Architect completes two more. Completed checks never move owner
or run again. Current membership is rechecked in the mutation query. All ownership intervals,
handoffs, hypotheses, analyst reviews and decisions remain in FalkorDB.

Historical reconstruction traverses shared assets to prior incidents and retrieves authorized
episodic/procedural memory through MemoryService. Source integrity verification is distinct
from an analyst's assessment of a claim. Typed SOC, timeline, graph, activity, memory and
evidence payloads expose provenance and uncertainty without implementing a frontend.
See [investigations.md](investigations.md) for lifecycle, bounds, actual Cypher and restart proof.

## Persistent remediation simulator

Phase 8 adds RemediationRepository/RemediationService using Decision/Evidence profiles and
a tenant-scoped RemediationStream. Qualified proposals reference exact active Allow or trust
edges and immutable source evidence. Parameterized before/after views reuse RiskRepository's
bounded Cypher traversal; the after view excludes edges during computation. No source cloud
relationship is written by simulation. A separate atomic graph metadata operation retains
the hypothetical result, chunked evidence payload, operation identity and source digest.

Collateral evaluation includes non-sensitive resources and other identities for the selected
action. Alternative paths and heuristic score differences remain explicit. Business necessity,
real AWS authorization and deployment success remain uncertain. Explicit reviews transition
PROPOSED to UNDER_REVIEW to APPROVED/REJECTED; approval concerns the recommendation only.
No AWS executor is installed or exposed. Read [remediation.md](remediation.md) for supported
targets, source/review concurrency boundaries, visualization payloads and restart verification.

## Required contract for subsequent phases

All cloud relationships, temporal events, investigation state, agent memory, evidence,
ownership, handoffs and durable orchestration state must be stored in FalkorDB. No SQLite,
PostgreSQL, MongoDB, separate Redis key store, local JSON file or in-memory fallback may
become the authoritative store.

Domain records, relationships and deterministic synthetic event projections are implemented.
Live provider ingestion and dynamic orchestration remain future work:

| Concern | Graph representation direction |
| --- | --- |
| IAM and cloud access | Principals, policies, resources and explicit access relationships |
| Emerging paths | Time-stamped relationship changes linked to source events and findings |
| Investigations | Investigation nodes linked to findings, agents and evidence |
| Shared memory | Agent and memory nodes linked to investigations and supporting evidence |
| Coordination | Tasks, owners, leases, handoff events and explicit state transitions |
| Recovery | Graph-backed cursors, prepared outputs, pending tasks and shared memory in FalkorDB |

LangGraph is installed and runs the six-agent workflow using graph-backed reconstruction.
A generic LangGraph checkpointer/store adapter is not needed for this fixed DAG; future dynamic
workflows must preserve the FalkorDB-only storage contract. Do not adopt in-memory or SQL
tutorial checkpointers. Workflow ownership uses database-level atomic transitions and expiring
leases. A process-local lock cannot coordinate multiple workers.

Synthetic IAM fixtures are explicitly labelled as demo data. Optional AWS access uses the
standard credential chain (profiles, environment or workload identity), never checked-in
credentials. Gemini will remain optional. Provider calls must not be necessary for the
deterministic local demo. Defaults perform no AWS requests; optional Gemini
summaries require explicit configuration and an environment key.

## Scope and claim boundary

The backend provides durable synthetic event processing, graph-powered potential-path analysis,
historical risk views, simulations and persistent memory with versions, audited reviews and
authorized handoffs and recovery of incomplete six-agent investigations across restarts.
Complete AWS IAM authorization evaluation, production identity federation, general dynamic task
scheduling and privileged tool execution remain later work. Synthetic potential paths do not
establish exploitable AWS authorization.

## Authenticated API and durable notifications

Phase 10 adds server-configured bearer digests, explicit read/write/review/AWS-sync permissions
and authenticated tenant bindings. Only health/readiness are public. Existing synthetic service
bindings reject unrelated principals; general graph views bind repositories directly to the
authenticated tenant. Graph agent membership/source ACLs remain independent checks. No prompt
or request field can select another principal, tenant, Cypher query or privileged AWS operation.

Canonical typed HTTP projections include bounded node/edge snapshots, temporal membership
differences, immutable evidence-backed paths, source timelines and agent task/memory/handoff
activity. Stable keyset pages are fenced by the tenant's persisted notification sequence;
concurrent changes return 409 rather than silently mixing pages. Generic retained properties
are not presented as a complete historical attribute ledger.

Instrumented domain writes use GraphDatabase.mutate: an internal query wrapper locks the tenant
LiveStream before the guarded mutation and creates a unique LiveEvent in the same server-side
Cypher transaction. Generic immutable-record upserts journal only their create branch. Source
event application, risk evidence, workflow transitions/recovery, memory/ACL changes, case and
remediation commits, AWS imports and report publication metadata use this boundary. A failed
write rolls back both the event and sequence; retrying the same statement cannot append twice.
The SDK pool/query retry layer and original domain compare-and-set fences remain in place.

Authenticated sockets poll indexed LiveEvent records by tenant sequence with bounded pages,
send timeout/backpressure and first-frame authentication. They replay after disconnect/restart
from FalkorDB; connection cursors are operational state, not authoritative agent checkpoints.
Heartbeat/pong frames are protocol controls, not domain events. API quotas use an atomic graph
ApiRateBudget per principal/category/window; counters and idle polling do not create live events.
No broker, SQL checkpoint database, Redis key store or process-local event queue was introduced.
See [API_CONTRACT.md](API_CONTRACT.md) for exact behavior, setup and limits.

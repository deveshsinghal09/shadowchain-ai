# Six durable security agents

Phase 6 runs six logical agents in a fixed LangGraph DAG. FalkorDB stores investigations,
agent runs, task ownership/dependencies, prepared outputs, operation decisions, memories and
handoffs. Every LangGraph node reconstructs its inputs from graph records. No in-memory,
SQLite, PostgreSQL, file or LangGraph default checkpointer owns workflow state.

```text
Existing graph SecurityEvent
  → Cloud Sentinel → Identity Guardian → Risk Oracle → Memory Keeper
  → Coordinator → Response Architect → completed analysis
```

| Agent | Implemented responsibility |
| --- | --- |
| Cloud Sentinel | Reads input events/evidence and provenance-linked relationship changes |
| Identity Guardian | Inspects affected identities/roles, historical assumptions/policies and expired grant justifications |
| Risk Oracle | Calls the existing bounded Cypher risk traversal and deterministic authorization/scoring rules; persists potential findings and evidence |
| Memory Keeper | Retrieves authorized graph memories and visible claim conflicts; preserves source context |
| Coordinator | Admits investigations with six owned dependency-ordered step tasks; its workflow step creates up to three finding-linked remediation review tasks |
| Response Architect | Selects an actual policy attachment from each sampled path, proposes removal and calls read-only what-if analysis |

Every agent writes an evidence-backed shared episodic observation and a durable handoff using
the Phase 5 MemoryService. Each later agent can retrieve earlier observations. The final
handoff returns context to Coordinator without scheduling another agent iteration. Shared
memory can connect subsequent investigations through common events/assets. The per-investigation
memory inspection endpoint returns directly linked memories; Memory Keeper additionally
retrieves related context through the memory service's graph queries.

The system analyzes configuration history; it has no CloudTrail session/use telemetry.
Identity Guardian explicitly reports that behavioral unusual-access conclusions cannot be
established from configuration alone. Risk findings remain **POTENTIAL ATTACK PATHS** with
the [existing authorization limitations](risk_engine.md). Neither a connected path nor model
prose proves effective AWS access. What-if changes only the evaluated query exclusions.

## Durable state and transitions

Investigation has the additional WorkflowInvestigation label and stores request/source hashes,
source IDs, historical assessment time, provider mode, cursor, revision, lock and lease. Its
six AgentRun nodes additionally have WorkflowRun labels. Every run has:

- A stable unique run ID and Agent identity, investigation ID and stage.
- Input evidence IDs, retrieved memory IDs and a typed AgentOutput with finding IDs.
- Status, attempt count, operation ID, pending task IDs and handoff target.
- Created/updated timestamps, plus start/finish and lease metadata when applicable.
- Prepared output hash/JSON, eventual memory ID and handoff ID.

JSON properties are normalized graph properties containing typed bounded values. They are
never written to a local state file. Explicit relationships connect agents, runs, tasks,
investigations, findings, evidence, memory and handoffs; retrieval does not depend on an LLM
summary. All domain records reuse stable UUID identity, provenance and tenant validation.

1. Coordinator's admission policy creates the investigation, six runs and six step tasks with
   ASSIGNED_TO and DEPENDS_ON edges in one atomic statement. Request ID plus tenant identifies
   the operation; a different payload using that ID returns 409. A deliberate new request ID
   can start a separate investigation of the same event.
2. Before a step runs, a graph-serialized claim checks the investigation cursor, completed
   dependencies, attempt budget and expired lease. It writes a unique lease token and increments
   the persistent attempt count. There is one active step per investigation.
3. The agent reads graph evidence and prior outputs. Its validated output and retrieved-memory
   IDs commit as a prepared run with a unique prepare Decision. Coordinator review-task creation
   and Response Architect proposal updates commit with that prepared result.
4. Idempotent MemoryService writes persist the observation and handoff. A unique completion
   Decision, run/task completion, memory/run and handoff/run links, pending IDs and investigation
   cursor advance commit together in one fenced statement.
5. If interrupted, the next worker reconstructs the cursor/run from FalkorDB. A prepared result
   is reused, including optional model prose. Memory and handoff retries reuse their operation
   IDs. A lost completion reply resumes from the next stage; completed steps are not rerun.

These are atomic single-statement transitions, not a claim of a multi-statement database
transaction. Memory publication and workflow completion are deliberately separate recoverable
boundaries. A crash can leave a visible observation before run completion; retries reconcile
the same observation/handoff. External optional model requests may repeat if a process dies
before preparing its response; exactly-once remote model billing is not promised.

Leases default to 120 seconds. Writes check the current token, run token, cursor and expiry;
stale workers cannot advance the workflow. A hard-crashed active lease must expire before
another worker takes over. Steps default to three persistent attempts, with an absolute
configuration maximum of five. Failures retain safe error codes and outputs for audit.
Exhausted runs become blocked and are excluded from automatic recovery. Resume does not
reset their budget. Source deletion/replacement fails clearly rather than fabricating evidence.

The recovery worker starts with the application and polls FalkorDB for pending, retryable or
expired running investigations in the reserved demo tenant. It scans at most ten per pass
by default and uses the same leases as HTTP requests. Socket pools, stop events and the
LangGraph input containing only `investigation_id` are operational state, not task authority.
There is no application-side durable queue. Multiple backend workers coordinate through graph
claims. Production tenant/authenticated worker deployment remains a later integration.

The workflow completes its **analysis** with six step tasks completed. Finding-specific
remediation tasks remain `awaiting_review`, assigned to Response Architect, dependent on the
Coordinator task and linked to the Coordinator run. The final run retains those pending IDs.
Their saved proposals are data; the application has no IAM modification or command executor.

## Shared access and untrusted content

A tenant-scoped workflow MemorySpace has explicit memberships for the six fixed agents.
Trusted code grants context only to actual input evidence and deterministic graph-derived
subjects/snapshots. Provider text and provider citations never select source grants, tasks,
owners, routes, finding paths or simulation targets. Replaying bootstrap operation IDs does
not restore a subsequently revoked permission. All memory reads continue to enforce Phase 5
current-time membership, private/team rules, source grants and tenant scope.

Cloud logs, retrieved memory and document-like source text remain untrusted data. They cannot
be interpreted as workflow instructions, shell commands or IAM requests. The provider receives
only an allowlist of stage name, bounded counts and graph evidence UUIDs. It receives no raw
cloud logs, document content, memory text, settings, credentials or provider responses.
The closed output model contains only summary, evidence citations and confidence. Citation
IDs must belong to the supplied evidence set. Extra commands/tool fields are rejected.
Even a syntactically valid summary requesting privileged activity is stored only as a run
annotation. It cannot affect deterministic findings or publish executable memory.

Each invocation has a fixed six-node route and LangGraph recursion limit of eight; there are
no LLM-selected tools, recursive planner loops or model-directed handoffs. Response proposals
are typed `simulation_only=true`, `execution_allowed=false`. No source text or model output
can change either property. A future executor must independently authorize tools; this phase
does not claim general prompt-injection immunity for future integrations.

## Optional Gemini provider

`ModelProvider` has deterministic MockProvider and GeminiProvider implementations. Mock is the
default and requires no API key. Gemini is optional readability assistance; traversal, risk
rules, citations, ownership and control flow remain deterministic.

| Setting | Default / bound |
| --- | --- |
| `AGENT_PROVIDER` | `mock`; explicit `gemini` requires an environment key |
| `GEMINI_MODEL` | `gemini-3.8-flash`, configurable validated model name |
| `GEMINI_API_KEY` | Empty by default; secret environment configuration only |
| `AGENT_MODEL_TIMEOUT_SECONDS` | 5; maximum 15 per network operation |
| `AGENT_MODEL_ATTEMPTS` | 2; maximum 3 |
| `WORKFLOW_LEASE_SECONDS` | 120; range 90–300 |
| `WORKFLOW_MAX_ATTEMPTS` | 3; maximum 5 per agent run |
| `WORKFLOW_RECOVERY_ENABLED` | true in local demo configuration |
| `WORKFLOW_RECOVERY_INTERVAL_SECONDS` | 2; range 1–60 |
| `WORKFLOW_RECOVERY_LIMIT` | 10; maximum 50 investigations per pass |

Gemini uses standard-library HTTPS against the fixed Google API origin with the key in a
header, never a URL or prompt. Redirects are disabled. Requests require structured JSON output,
temperature zero and at most 512 output tokens; responses are capped at 32 KiB and validated
with Pydantic. Network failures, 429/5xx and invalid outputs use bounded retries/backoff;
other HTTP errors fail immediately. Provider errors expose a safe code without response text
or credentials. Saved provider mode must match the recovery process configuration.
There is no new Python dependency or Google SDK requirement. Gemini wire behavior is covered
with controlled HTTP responses; live Gemini was not called without a key.

## API and Windows demo

All routes below are under `/api/v1/agents`. The HTTP binding fixes the reserved synthetic
tenant; body/header values cannot select another tenant or agent. Routes and automatic demo
recovery are disabled in production or when demo mode is off. HTTP authentication remains
future work, so keep the existing loopback binding.

| Method / route | Behavior |
| --- | --- |
| GET `/` (without trailing slash also works) | List six graph Agent registrations |
| POST `/investigations` | Trigger with existing event ID and request ID; complete the bounded workflow |
| GET `/investigations/{id}` | Inspect progress, six runs, tasks, pending reviews and timestamps |
| GET `/runs/{id}` | Inspect one persisted run including inputs, memories, output and handoff |
| GET `/investigations/{id}/memories` | Directly linked shared memory versions under current access rules |
| GET `/investigations/{id}/handoffs` | Persisted handoff pointers with current authorized context |
| POST `/investigations/{id}/resume` | Reconstruct and resume from the committed cursor |

Ingest/replay a synthetic event first; triggering analyzes that existing graph event. A new
provider-ingestion dispatcher is not introduced. POST trigger/resume runs synchronously in
FastAPI's thread pool; the background worker independently recovers saved incomplete work.
An active lease returns 409, dependency/model outage 503, missing scoped records 404 and
validation errors 422. Reusing a request ID with a different payload returns 409.

Run from the project directory after the existing Compose setup:

```powershell
docker compose build backend
docker compose up -d --wait
docker compose exec -T backend python -m app.demo.agent_proof
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-agents.ps1
Invoke-RestMethod http://127.0.0.1:8000/api/v1/agents
```

The script seeds/replays Scenario A, locates the actual 12:00 event in the snapshot, triggers
the investigation, displays the six runs and simulation, then repeats the trigger to check
that tasks are reused. The Python proof independently verifies shared evidence, retained
memory/handoffs and reconnect recovery. Both use synthetic resources only. Generated proof
JSON files are verification output; runtime services never load them as saved state.

## Actual Cypher diagnostics

These are trusted operator diagnostics over the implemented schema, with all values supplied
as SDK parameters. They are not unrestricted agent tools. Use MemoryService for agent memory
retrieval because its source and membership ACL checks are additional to tenant scope.

Agent registry:

```cypher
MATCH (a:Entity:Agent {tenant_id: $tenant_id, source: 'shadowchain.agents.v1'})
RETURN a.id, a.name, a.agent_type, a.version ORDER BY a.agent_type
```

Recovery candidates (`$now` is UTC epoch microseconds):

```cypher
MATCH (i:Entity:WorkflowInvestigation {tenant_id: $tenant_id})
WHERE i.status IN ['pending', 'running', 'retryable'] AND i.lease_expires_at <= $now
RETURN i.id, i.sequence, i.status, i.lease_owner ORDER BY i.created_at LIMIT 10
```

Persisted step owners and dependencies:

```cypher
MATCH (t:Task:WorkflowTask {tenant_id: $tenant_id, investigation_id: $investigation_id,
                          task_type: 'workflow_step'})
      -[assigned:ASSIGNED_TO {tenant_id: $tenant_id}]->(owner:Entity:Agent {tenant_id: $tenant_id})
OPTIONAL MATCH (t)-[dependency:DEPENDS_ON {tenant_id: $tenant_id}]->(prior:Entity:Task {tenant_id: $tenant_id})
WITH t, owner, collect(prior.id) AS dependencies
RETURN t.id, t.stage, t.status, owner.id, dependencies ORDER BY t.sequence
```

Run memory and source evidence through explicit graph relationships:

```cypher
MATCH path=(r:AgentRun {tenant_id: $tenant_id, investigation_id: $investigation_id})
      -[:REMEMBERS]->(m:AgentMemory)-[:DERIVED_FROM]->(e:Entity)
WHERE all(n IN nodes(path) WHERE n.tenant_id = $tenant_id)
  AND all(edge IN relationships(path) WHERE edge.tenant_id = $tenant_id)
RETURN r.id, r.stage, m.id, e.id, e.kind, e.source, e.source_id ORDER BY r.sequence
```

Finding provenance connects deterministic results to their actual agent run:

```cypher
MATCH (f:Entity:RiskFinding {tenant_id: $tenant_id})
      -[derived:DERIVED_FROM {tenant_id: $tenant_id}]->
      (r:Entity:AgentRun:WorkflowRun {tenant_id: $tenant_id, investigation_id: $investigation_id})
RETURN r.stage, f.id, f.path_id, f.analysis_id
```

Saved handoff ownership and run provenance:

```cypher
MATCH path=(r:AgentRun {tenant_id: $tenant_id, investigation_id: $investigation_id})
      <-[:CAUSED_BY]-(h:Handoff)-[:HANDS_OFF_TO]->(a:Agent)
WHERE all(n IN nodes(path) WHERE n.tenant_id = $tenant_id)
  AND all(edge IN relationships(path) WHERE edge.tenant_id = $tenant_id)
RETURN r.stage, h.id, a.id, h.context_sha256 ORDER BY r.sequence
```

Coordinator-created review tasks and their non-executable proposals:

```cypher
MATCH (t:Entity:Task {tenant_id: $tenant_id, investigation_id: $investigation_id,
                    task_type: 'remediation_review'})
      -[caused:CAUSED_BY {tenant_id: $tenant_id}]->(coordinator:Entity:AgentRun {tenant_id: $tenant_id})
RETURN t.id, t.status, t.output_json, coordinator.stage ORDER BY t.sequence
```

See the [generated agent proof](agent_proof.json), [startup restart proof](agent_restart_proof.json)
and [Phase 6 verification](phase6_verification.md) for actual execution results.

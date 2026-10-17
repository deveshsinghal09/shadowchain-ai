# Investigation intelligence

Phase 7 adds a SOC case service over the existing FalkorDB graph, risk engine, memory ACLs
and six-agent workflow. Risk Oracle's prepared, graph-derived finding automatically creates
or updates a case. All hypotheses, checks, evidence links, history, decisions, ownership and
handoff checkpoints persist in FalkorDB. No frontend or live AWS integration is included.

## Cases and workflow runs

`Investigation:SecurityCase` represents a SOC incident. `Investigation:WorkflowInvestigation`
represents the six-agent execution introduced in Phase 6. They have different IDs and status
vocabularies. A case can link multiple workflow runs for the same potential path. Completed
agent analysis does not resolve a SOC case automatically.

Cases are deduplicated by tenant, memory space and path ID. A tenant-scoped `CaseStream`
serializes audited changes; `CaseHead` points to the latest episode. A new assessment of the
same path updates an active case. A new assessment at or after the last assessed timestamp
after RESOLVED/CLOSED creates a new episode with `SUPERSEDES`; it preserves the old episode.
Replaying an already recorded analysis reuses its original detection decision. Historical
backfills before the latest assessment do not reopen a terminal case or lower its assessment
timestamp. Detection and stage synchronization use separate durable operation identities,
so another workflow for an existing analysis still links its tasks and run to that case.

The normal path is:

```text
NEW -> TRIAGING -> INVESTIGATING -> AWAITING_REVIEW -> RESOLVED -> CLOSED
```

AWAITING_REVIEW and RESOLVED may return to INVESTIGATING. CLOSED is terminal. Invalid or stale
transitions return 409. Each mutation accepts an operation `request_id`; review requests also
carry the last observed `expected_revision`. Identical retries reuse the existing audit
operation; different content with the same operation identity returns 409. Request schemas
reject extra fields, including actor, executor and permission overrides.

| Operation | Requirements |
| --- | --- |
| Read case | Current MemorySpace membership and active case assignment |
| Modify case | Writer/curator membership, assigned agent, revision checks |
| Advance lifecycle | Case owner or Coordinator; transitions above only |
| Handoff | Owner or Coordinator; recipient must be an assigned writer/curator |
| Await review | All four case checks completed |
| Resolve | Coordinator, completed analysis/check tasks, verified evidence, explicit disposition |
| Close | Coordinator, already RESOLVED |
| Review evidence/hypothesis | Coordinator, active case, linked source IDs; hypothesis citations must be verified |

Supported resolution dispositions are `accepted_risk` and `false_positive`. A false positive
requires verified contradictory evidence. There is no automatic `remediated` disposition:
what-if results do not prove a deployed correction. Resolving explicitly completes the
associated remediation-review tasks as a recorded review, never as an AWS action. Accepted
risk preserves an unresolved hypothesis and any retained uncertainty.

## Historical reconstruction

`InvestigationService.reconstruct()` performs bounded Cypher traversals from case to affected
Identity, Role and Resource nodes, then through those assets to earlier investigations.
Prior incidents must share the case's MemorySpace; legacy synthetic investigations without
a space are included only within the same tenant. Source IDs and shared asset IDs accompany
each result. Similarity is deterministic asset-set Jaccard overlap, not attribution or proof
of common cause.

MemoryService retrieves current, authorized episodic/procedural memories by the connected
assets, case and prior incident IDs. Historical procedures become suggested checks only if
verified, current, conflict-free, and marked successful or a lesson. Their typed steps are
closed read-only operations. Source prose remains untrusted data and cannot select tools,
grant roles or execute instructions. Each summary statement carries the IDs behind it.

Four dependency-ordered case checks persist their results and audit decisions:

1. Reconstruct historical incidents and useful procedures.
2. Verify immutable evidence snapshots against stored SHA-256 hashes.
3. Retrieve contradictory structured memories for analyst review.
4. Propose existing read-only risk-engine what-if responses.

`verification_scope=source_integrity` means the captured record matches its hash. It does
**not** prove that a cloud claim is true or an access path is exploitable. Explicit evidence
reviews use `analyst_assessment` and retain source provenance, the original record and digest.
Contradictions are retained alongside supporting observations. Hypothesis reviews are new
immutable Decisions; they do not overwrite the original Risk Oracle hypothesis.

## Cross-session handoff

Memory Keeper (Agent A) starts the case and completes the first two checks. Coordinator
creates an immutable Handoff containing completed/pending task IDs, evidence, decision and
memory IDs, plus a digest. Only pending case tasks change owner to Response Architect
(Agent B); old ASSIGNED_TO intervals are closed and completed task records remain intact.
Repeated handoffs retain prior assignment history.

The hook runs after a durable prepared agent output and again after its memory publication,
before workflow completion. If interrupted, the startup worker reuses the prepared output,
reconciles operation IDs, reads the graph owner/cursor, and completes pending checks. Existing
LangGraph execution uses graph-backed state reconstruction with no default checkpointer.
Case operations use one atomic Cypher statement with stream/case compare-and-swap, immutable
audit Decision and bounded retries. Current membership is checked again in the committing
query; revocation between service validation and commit blocks the write.

The [actual restart proof](investigation_restart_proof.json) stopped the persistent database,
started a fresh backend, and observed automatic completion without a resume request. Agent A's
two decoded task records and the prior twelve audit records stayed equal; Agent B completed
two checks. Every workflow agent's attempt count remained one.

## API contract

All paths below are under `/api/v1`. The local demo binds tenant and Coordinator on the
server. There is no principal-selection header or body field. Routes return 404 in production
or with demo mode off. A future authenticated control plane must bind these principals to
verified user identities before public deployment.

| Method and path | Structured output |
| --- | --- |
| `POST /agents/investigations` | Trigger six-agent execution; Risk Oracle automatically admits cases |
| `GET /investigations?status=AWAITING_REVIEW&limit=50` | SOC summaries and count |
| `GET /investigations/{id}` | Complete case, severity/status, affected assets, hypotheses, evidence, tasks, memories, recommendations, audit |
| `GET /investigations/{id}/timeline` | Source-time security/relationship/memory entries and processing-time audit entries |
| `GET /investigations/{id}/history` | Prior incidents, shared assets, similarity, authorized memory and traceable summary |
| `GET /investigations/{id}/graph` | Nodes and typed source/target edges for a graph/3D client; no UI coordinates |
| `GET /investigations/{id}/activity` | Assigned agents, actual agent runs, completed/pending tasks, audit |
| `GET /investigations/{id}/memories` | Current authorized memory explorer records with source evidence |
| `GET /investigations/{id}/evidence/{evidence_id}` | Source record, hash, provenance, verification scope and review Decision |
| `POST /investigations/{id}/transitions` | Validated lifecycle operation |
| `POST /investigations/{id}/evidence/reviews` | Evidence verification/polarity review |
| `POST /investigations/{id}/hypotheses/reviews` | Cited hypothesis assessment |
| `POST /investigations/{id}/handoffs` | Transfer pending case checks and persist checkpoint |
| `GET /investigations/{id}/handoffs/{handoff_id}` | Immutable checkpoint and current owner |
| `POST /investigations/{id}/resume` | Coordinator delegates pending read-only checks to the persisted owner |

Timeline ISO timestamps are UTC; `time_basis` distinguishes source and processing time.
Embedded graph properties retain integer epoch microseconds. Contributing events come from
the finding's edge provenance, while the broader timeline also contains contextual events.
The graph endpoint merges current linked records with immutable source snapshots, marks
archived snapshots, filters memory through current ACLs, and reports missing endpoint IDs.
Non-entity scope/request evidence is not fabricated into graph nodes.

Queries fail clearly when FalkorDB is unavailable. Limits include 100 findings/cases per
workflow query, 30 prior incidents, 100 reconstructed assets/memories, 50 context subjects,
2,000 direct case links, and graph output bounded to 1,000 nodes/2,000 relationships. Oversized
contexts return 422 rather than silently truncating the investigation. List `limit` bounds
the entire result; this initial API does not implement pagination.

Example explicit review from PowerShell, after inspecting an AWAITING_REVIEW case:

```powershell
$base = 'http://127.0.0.1:8000/api/v1'
$cases = Invoke-RestMethod "$base/investigations?status=AWAITING_REVIEW"
$id = $cases.investigations[0].investigation_id
$case = Invoke-RestMethod "$base/investigations/$id"
$request = @{
    request_id='analyst/synthetic-acceptance-1'
    expected_revision=$case.revision
    status='RESOLVED'
    resolution='accepted_risk'
    reason='SYNTHETIC analyst accepts modeled risk; no AWS correction is claimed'
} | ConvertTo-Json
Invoke-RestMethod "$base/investigations/$id/transitions" -Method Post `
    -ContentType 'application/json' -Body $request
```

## Reproduce the synthetic case

From the project root with the normal Compose stack running:

```powershell
# Trusted demo setup: A/C replay, prior incident, reviewed historical procedure and two claims.
# This proof explicitly performs a SYNTHETIC accepted-risk review and closes its case.
docker compose exec -T backend python -m app.demo.investigation_proof
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-investigation.ps1
```

The proof verifies a complete case, opposing observations, previous incident/procedure,
two-agent case handoff, non-mutating remediation and reconnect equality. It is idempotent.
The HTTP script can also seed/replay Scenario A and trigger a case on an empty demo; the
additional historical procedure/conflict fixture is supplied by the trusted proof CLI.
Neither the application nor the CLI loads generated documentation JSON as runtime state.

To stage a restart demonstration on a fresh graph, stop the backend's recovery worker before
preparing five stages, then restart the database/backend without reseeding:

```powershell
docker compose stop backend
docker compose run --rm --no-deps backend python -m app.demo.investigation_proof --prepare-restart
docker compose restart falkordb
docker compose up -d --wait backend
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-investigation.ps1
```

Use a new `FALKORDB_GRAPH` on the same server for an independent run, preserving existing
graphs. An already completed proof stays completed; `--prepare-restart` does not rewind it.
Docker execution remains unverified on this workstation; see [verification](phase7_verification.md).

## Actual Cypher diagnostics

Pass values through the official client `graph.ro_query(query, params=parameters)`, never
string interpolation. These queries were run against the saved synthetic case. Query paths
are materialized so every traversed node and edge is tenant checked. Raw query text is never
accepted from HTTP callers, source documents or model output. Trusted diagnostics supplement
repository authorization; they do not replace current agent membership checks.

Earlier investigations connected by assets (the repository's bounded query shape):

```cypher
MATCH path=(current:SecurityCase {tenant_id: $tenant_id, id: $case_id})
-[:INVESTIGATES]->(asset:Entity)<-[:INVESTIGATES|AFFECTS*1..2]-(prior:Investigation)
WHERE prior.id <> current.id AND prior.created_at < current.created_at
AND asset.kind IN ['Identity', 'Role', 'Resource']
AND (prior.space_id = $space_id OR (prior.space_id IS NULL AND prior.synthetic = true))
AND all(n IN nodes(path) WHERE n.tenant_id = $tenant_id)
AND all(r IN relationships(path) WHERE r.tenant_id = $tenant_id)
RETURN prior.id, collect(DISTINCT asset.id) AS shared_asset_ids
ORDER BY prior.id LIMIT 31
```

Completed and pending case checks with their persisted owners:

```cypher
MATCH path=(task:Task)-[:BELONGS_TO]->(case:SecurityCase {id: $case_id})
WHERE task.task_type = 'investigation_check'
AND all(n IN nodes(path) WHERE n.tenant_id = $tenant_id)
AND all(r IN relationships(path) WHERE r.tenant_id = $tenant_id)
RETURN task.id, task.check_key, task.status, task.agent_id, task.output_json
ORDER BY task.sequence
```

Case evidence and immutable source provenance (both supporting and opposing links):

```cypher
MATCH path=(e:Evidence)-[r:SUPPORTS|CONTRADICTS]->(case:SecurityCase {id: $case_id})
WHERE all(n IN nodes(path) WHERE n.tenant_id = $tenant_id)
AND all(edge IN relationships(path) WHERE edge.tenant_id = $tenant_id)
RETURN DISTINCT e.id, type(r), e.source, e.source_id, e.content_sha256
ORDER BY e.id, type(r)
```

Immutable handoff checkpoints and case audit history:

```cypher
MATCH path=(h:Handoff)-[:BELONGS_TO]->(case:SecurityCase {id: $case_id})
WHERE all(n IN nodes(path) WHERE n.tenant_id = $tenant_id)
AND all(r IN relationships(path) WHERE r.tenant_id = $tenant_id)
RETURN h.id, h.sender_agent_id, h.recipient_agent_id, h.context_json, h.context_sha256
ORDER BY h.id
```

```cypher
MATCH path=(d:Decision)-[:DERIVED_FROM]->(case:SecurityCase {id: $case_id})
WHERE d.action STARTS WITH 'case.' AND d.action <> 'case.hypothesis'
AND all(n IN nodes(path) WHERE n.tenant_id = $tenant_id)
AND all(r IN relationships(path) WHERE r.tenant_id = $tenant_id)
RETURN d.id, d.action, d.actor_agent_id, d.created_at, d.audit_json
ORDER BY d.created_at, d.id
```

References: [FalkorDB MATCH/named paths](https://docs.falkordb.com/cypher/match) documents
the traversal primitives. [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
explains persistence mechanisms; this project implements its own FalkorDB reconstruction
contract rather than installing another checkpoint database.

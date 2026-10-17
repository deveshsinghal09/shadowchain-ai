# Persistent graph memory

FalkorDB owns memory content, versions, evidence connections, access grants, review decisions,
contradictions and handoffs. MemoryService uses parameterized Cypher through the official
client. There is no vector database, file-backed memory, SQL checkpoint store or process-local
memory authority. Temporary dictionaries assemble query responses only.

| Type | What is retained | Context behavior |
| --- | --- | --- |
| Episodic | Incidents, events, investigations, hypotheses, conclusions and agent actions | Evidence-backed observations; conclusions remain claims |
| Semantic | Asset attributes, IAM relationships, security concepts and findings | Only current, curator-verified, conflict-free observations enter `established_facts` |
| Procedural | Investigation patterns, remediation proposals, successful and failed approaches, lessons | Only verified, conflict-free successful/lesson plans enter `validated_procedures`; failures remain observations |

Semantic memories link existing cloud nodes through ABOUT; they do not create effective IAM
permissions. SecurityConcept stores tenant-scoped vocabulary. Procedural memory may reference
an existing Procedure as evidence and stores a closed, typed `ProcedurePlan`. Arbitrary legacy
Procedure.instructions and Phase 3 memory text are source data, never executable plans.

## Graph model and authority

```text
(Agent)-[MEMBER_OF {role}]->(MemorySpace)-[HAS_CONTEXT]->(source Entity)
(Agent)-[REMEMBERS]->(AgentMemory:PersistentMemory)-[BELONGS_TO]->(MemorySpace)
(AgentMemory)-[ABOUT]->(Identity | Role | Resource | Investigation | SecurityConcept | ...)
(AgentMemory)-[DERIVED_FROM]->(Evidence | SecurityEvent | RiskFinding | Decision | AgentRun | Procedure)
(new AgentMemory)-[SUPERSEDES]->(old AgentMemory)
(AgentMemory)-[CONTRADICTS]->(AgentMemory)
(Agent)-[HANDS_OFF_TO]->(Handoff)-[HANDS_OFF_TO]->(Agent)
(Handoff)-[INCLUDES_MEMORY]->(AgentMemory)
```

All entities and edges have stable IDs, tenant scope, UTC epoch-microsecond timestamps and
provenance. Managed memories additionally carry `memory_key`, `series_id`, `memory_version`,
`author_agent_id`, `space_id`, `audience`, `confidence`, `verification_status`, `state`,
`valid_from`, optional `valid_to`, `reference_count`, request hash and engine version.
Original source provenance is retained separately from engine provenance. Evidence responses
include actual graph source records and audit Decisions, labelled `data_only=true`.

MemorySeries is an internal graph control node containing the durable series head/version.
PersistentMemory is a secondary label enforcing mandatory fields on managed AgentMemory
records without silently upgrading legacy demo observations. Schema version 5 indexes memory
IDs, tenant/space, series, state, type and claim keys. No other store participates in recovery.

The trusted application binds `TenantScope`, MemorySpace ID and authenticated Agent ID when
constructing MemoryRepository. Neither query parameters nor source content can override them.
MemoryAccessAdmin is trusted bootstrap/administration code with no HTTP or LLM tool exposure.
It creates and revokes graph memberships and explicit source-context grants.

| Role | Allowed operations |
| --- | --- |
| reader | Retrieve permitted observations, investigation history and context |
| writer | Reader operations, create memories, correct own memories, create handoffs |
| curator | Writer operations, review verification status, correct another author's team memory |

Membership alone does not expose the entire tenant. Every referenced source must have an
active verified HAS_CONTEXT grant in that space; engine-owned audit Decisions are recognized
within their own space. All intermediates in related-entity traversal require context grants.
Missing or deleted sources fail closed through reference-count validation. Revoking a source
grant hides dependent memories without deleting their audit records. `private` is the default;
only its author can read it. `team` permits other current members of that space to read it.
Curator status does not bypass private-memory visibility. Database administrators remain trusted.

ACL intervals use server time, even when querying historical data. A past timestamp cannot
revive revoked membership. Source relationship and memory validity use the requested data time
with half-open intervals `[valid_from, valid_to)`. Ordinary retrieval requires both valid data
and a current series head unless `include_superseded=true`. Agent context always excludes
superseded versions. Investigation history includes old/expired versions but still checks
current permissions. These are recorded hypotheses and corrections, not an assertion that a
later conclusion was known at an earlier ingestion time.

## Service interface and consistency

| Method | Result |
| --- | --- |
| `write_memory(MemoryWrite)` | Idempotent, initially unverified observation with required evidence |
| `retrieve_related_memories(MemoryQuery)` | Graph-grounded matches by subject, evidence, event, incident/investigation or ID |
| `retrieve_investigation_history(id)` | Authorized investigation history, including superseded versions |
| `get_agent_context(MemoryQuery)` | Current observations, established facts and validated read-only procedures |
| `supersede_memory(SupersedeMemory)` | New version plus SUPERSEDES edge; previous content/evidence retained |
| `detect_memory_conflicts(MemoryQuery)` | Visible graph contradiction pairs and whether both versions remain current |
| `create_handoff_context(HandoffRequest)` | Durable authorized memory pointers and a graph-derived recipient context |

`review_memory()` requires a curator and persists the prior/new verification status, reason,
reviewer and version as a Decision. Review metadata changes are audited; correction creates
a new content version. `get_handoff_context()` rebuilds context from current graph records
and permissions. The handoff itself never grants access, revives superseded memories or makes
a saved text summary authoritative. A sender preview cannot reveal a recipient's private claims.

Each mutation commits its related memory, source links, audit Decision, series head, conflict
edges and space revision in one guarded Cypher statement. Space revision/lock serialization
coordinates workers through FalkorDB. Concurrent corrections use `expected_version`; stale
writes fail with 409. Membership and context grants are rechecked inside writes. Deterministic
operation IDs and payload hashes reconcile retries/lost acknowledgements; reusing a request ID
for different content fails. No Python lock or dictionary owns this state.

Queries allow 0–2 related-source hops, at most 20 subjects, 50 memory IDs and 100 returned
memories (default 50). A space admits at most 1,000 context records per bounded access read;
conflict reads cap relevant pairs at 500. Overflow returns a clear 422 rather than incomplete
context. Larger deployments need pagination/partitioning before increasing these bounds.

## Conflicts and instruction safety

A structured claim is `(subject_id, predicate, optional object_id, scalar value)`. Different
values for the same functional claim key with overlapping validity intervals create a durable
CONTRADICTS edge. Both observations and source evidence remain available. Adjacent half-open
intervals do not conflict. Numeric integer values are normalized consistently; booleans remain
distinct. Free-text disagreement is not automatically inferred. Nonfunctional relationships
must use an appropriate object ID rather than claiming every value is exclusive.

Retrieval checks visible opposing claims even when the caller filters to one memory ID.
Unresolved conflicts keep otherwise verified semantic claims out of established facts.
Superseding one side deactivates the conflict for current context while preserving its edge
and history. Private opposing memories and their IDs are hidden from unauthorized agents.

All content remains `source_trust=untrusted_data`, `executable=false`, including after review.
Caller-supplied tenant, author, trust, verification or executable fields are rejected. Source
text is never parsed as a tool request, role grant, Cypher query, shell command or AWS action.
Procedure steps are restricted to collect evidence, query historical paths, compare snapshots,
review conflicts and draft a remediation proposal. Their response says `execution_allowed=false`;
`privileged_actions` is typed as an empty tuple. There is no executor, LLM call or AWS call in
this phase. This enforces the current data/execution boundary; a future agent runner must
maintain that boundary and independently authorize tools. Text filtering alone is not a
general prompt-injection defense. Known credential patterns and credential property names
are rejected, but callers must still supply sanitized evidence summaries.

## Actual Cypher

Parameters below are passed through the SDK, never interpolated. `$auth_now` and `$at` are UTC
epoch microseconds. The first query is a runnable current-memory retrieval with source ACLs;
set `$memory_type` to `episodic`, `semantic` or `procedural`. It returns graph observations;
MemoryService additionally resolves visible conflicts and applies the context rules above.

```cypher
MATCH (agent:Entity:Agent {tenant_id: $tenant_id, id: $agent_id})
      -[membership:MEMBER_OF]->
      (space:Entity:MemorySpace {tenant_id: $tenant_id, id: $space_id})
WHERE membership.tenant_id = $tenant_id
  AND membership.role IN ['reader', 'writer', 'curator']
  AND membership.verification_status = 'verified'
  AND membership.valid_from <= $auth_now
  AND (membership.valid_to IS NULL OR $auth_now < membership.valid_to)
OPTIONAL MATCH (space)-[grant:HAS_CONTEXT]->(allowed:Entity {tenant_id: $tenant_id})
WHERE grant.tenant_id = $tenant_id AND grant.verification_status = 'verified'
  AND grant.valid_from <= $auth_now
  AND (grant.valid_to IS NULL OR $auth_now < grant.valid_to)
WITH space, collect(allowed.id) AS allowed_ids
MATCH (memory:Entity:AgentMemory:PersistentMemory {tenant_id: $tenant_id, space_id: $space_id})
      -[inside:BELONGS_TO]->(space)
WHERE inside.tenant_id = $tenant_id AND inside.verification_status = 'verified'
  AND inside.valid_from <= $auth_now
  AND (inside.valid_to IS NULL OR $auth_now < inside.valid_to)
  AND (memory.audience = 'team' OR memory.author_agent_id = $agent_id)
  AND memory.state = 'current' AND memory.memory_type = $memory_type
  AND memory.valid_from <= $at AND (memory.valid_to IS NULL OR $at < memory.valid_to)
MATCH (memory)-[reference:ABOUT|DERIVED_FROM]->(record:Entity {tenant_id: $tenant_id})
WHERE reference.tenant_id = $tenant_id
WITH memory, allowed_ids, collect({node: properties(record), kind: type(reference)}) AS refs
WHERE size(refs) = memory.reference_count
  AND all(ref IN refs WHERE ref.node.id IN allowed_ids OR
      (ref.node.kind = 'Decision' AND ref.node.source = 'shadowchain.memory.v1'
       AND ref.node.space_id = $space_id))
RETURN properties(memory), refs ORDER BY memory.created_at, memory.id LIMIT 100
```

Related investigation/identity/asset/event retrieval uses an actual bounded Cypher route.
The repository appends this fragment after the ACL/ref filtering above (before RETURN).
`$subjects` contains requested source IDs. Zero hops supports direct ABOUT/DERIVED_FROM matches.

```cypher
WITH memory, refs, allowed_ids
OPTIONAL MATCH route=(anchor:Entity {tenant_id: $tenant_id})
  -[:CAN_ASSUME|HAS_POLICY|GRANTS|TARGETS|AFFECTS|SUPPORTS|DERIVED_FROM|INVESTIGATES|BELONGS_TO*0..2]-
  (subject:Entity {tenant_id: $tenant_id})
WHERE anchor.id IN [ref IN refs | ref.node.id] AND subject.id IN $subjects
  AND all(n IN nodes(route) WHERE n.tenant_id = $tenant_id AND n.id IN allowed_ids)
  AND all(r IN relationships(route) WHERE r.tenant_id = $tenant_id
      AND r.verification_status = 'verified' AND r.valid_from <= $at
      AND (r.valid_to IS NULL OR $at < r.valid_to))
WITH memory, refs, count(route) AS matches
WHERE matches > 0
RETURN properties(memory), refs ORDER BY memory.created_at, memory.id LIMIT 100
```

The following are trusted administrator diagnostics, not agent-access APIs. Agents use the
repository ACL query, including current source grants and private-memory rules, first.
Correction lineage preserves both complete records:

```cypher
MATCH (new:Entity:AgentMemory:PersistentMemory {tenant_id: $tenant_id, space_id: $space_id})
      -[s:SUPERSEDES {tenant_id: $tenant_id}]->
      (old:Entity:AgentMemory:PersistentMemory {tenant_id: $tenant_id, space_id: $space_id})
RETURN old.id, old.memory_version, old.state, new.id, new.memory_version, s.id
```

Contradictory observations and their original source records:

```cypher
MATCH (a:Entity:AgentMemory:PersistentMemory {tenant_id: $tenant_id, space_id: $space_id})
      -[c:CONTRADICTS {tenant_id: $tenant_id}]->
      (b:Entity:AgentMemory:PersistentMemory {tenant_id: $tenant_id, space_id: $space_id})
MATCH (a)-[da:DERIVED_FROM {tenant_id: $tenant_id}]->(ea:Entity {tenant_id: $tenant_id})
MATCH (b)-[db:DERIVED_FROM {tenant_id: $tenant_id}]->(eb:Entity {tenant_id: $tenant_id})
RETURN a.id, a.claim_json, a.state, b.id, b.claim_json, b.state,
       c.id, ea.id, ea.source, ea.source_id, eb.id, eb.source, eb.source_id
```

Procedural audit retrieves the exact structured plan and the curator's decision:

```cypher
MATCH (m:Entity:AgentMemory:PersistentMemory
       {tenant_id: $tenant_id, space_id: $space_id, memory_type: 'procedural'})
      -[r:DERIVED_FROM {tenant_id: $tenant_id}]->
      (d:Entity:Decision {tenant_id: $tenant_id, space_id: $space_id})
RETURN m.id, m.procedure_json, m.verification_status, d.action, d.audit_json
```

## Run the synthetic demo from PowerShell

Run from the project directory after the existing setup/Compose quick start:

```powershell
docker compose exec -T backend python -m app.demo.memory_proof
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-memory.ps1
$base = 'http://127.0.0.1:8000/api/v1/memory'
Invoke-RestMethod "$base/context" -Method Post -ContentType 'application/json' -Body '{}'
Invoke-RestMethod "$base/retrieve" -Method Post -ContentType 'application/json' `
    -Body '{"include_superseded":true,"limit":100}'
```

The trusted CLI seeds/replays synthetic Scenario C, creates explicit source grants, writes
all three types, retains a failed approach, records conflicting observations, corrects a
claim, reviews structured guidance and persists a handoff. Reruns are idempotent. It needs
neither an AWS account nor an LLM key. Bootstrap request replay does not silently reinstate
a later revoked grant. Resetting Scenario C can delete referenced source nodes; reseed/grant
the new sources explicitly for further work instead of treating stale evidence as current.

The local API binds the Scout writer in one reserved synthetic tenant/space. Routes are
disabled when demo mode is false or APP_ENV is production. Headers cannot impersonate the
Verifier curator; production authentication/agent identity integration remains future work.
Use trusted CLI/core code for administrator and curator demonstrations.

| Method and path under `/api/v1/memory` | Request |
| --- | --- |
| POST `/write` | MemoryWrite |
| POST `/retrieve`, `/context`, `/conflicts` | MemoryQuery |
| GET `/investigations/{id}/history` | Existing permitted Investigation ID |
| POST `/supersede` | SupersedeMemory |
| POST `/review` | ReviewMemory; Scout writer receives 403 |
| POST `/handoffs` | HandoffRequest |
| GET `/handoffs/{id}` | Permitted sender/recipient handoff ID |

Pydantic request fields and examples are available in the local OpenAPI documentation.
See [the generated proof](memory_proof.json) and [verification results](phase5_verification.md).

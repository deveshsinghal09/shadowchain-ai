# Primary graph schema, version 10

One FalkorDB graph (`FALKORDB_GRAPH`, default `shadowchain`) stores cloud access,
observations, evidence and coordination records. All 20 domain node types carry the
`Entity` label plus their specific label. Every domain node and relationship has an
explicit `tenant_id`. Cloud nodes additionally require `account_id`.

The existing `Foundation` node and new `SchemaVersion` marker are infrastructure metadata,
outside tenant repositories. They contain no cloud, investigation, evidence or agent state.
No secondary database, Redis key store, file or in-memory dictionary owns application state.

## Identity, timestamps and provenance

Node IDs are UUIDv5 values derived from an unambiguous JSON array containing a versioned
namespace, tenant ID, node kind, account ID (or empty string), source and source ID.
Identical external identifiers in separate tenants/accounts therefore produce distinct IDs.
Never use FalkorDB's internal numeric node/edge IDs as persistent application identifiers.

| Property | Meaning |
| --- | --- |
| `id` | Stable application UUID; unique across all `Entity` nodes |
| `tenant_id` | Required repository scope, supplied by trusted application context |
| `account_id` | Required for cloud entities; optional for tenant-wide coordination records |
| `kind` | Specific node label or relationship type |
| `source`, `source_id` | Provider/source namespace and immutable source record identifier |
| `collector` | Collector, agent or importer that supplied the observation |
| `source_uri` | Unsigned evidence/source reference; no embedded authentication, query or fragment |
| `observed_at` | When the underlying fact was observed |
| `created_at`, `updated_at` | Database record ingestion and last mutation times |
| `fingerprint` | SHA-256 of immutable normalized record content, for replay/conflict checks |

Graph timestamps are **integer UTC epoch microseconds**. Python models accept timezone-aware
datetimes and return UTC datetimes. This avoids mixed-offset string comparisons and float
precision loss. `epoch_us()` and `from_epoch_us()` provide exact conversions. Infrastructure
`Foundation.created_at` retains its pre-existing ISO-8601 representation for compatibility.

`upsert_node()` is a first-write-wins immutable observation operation. Replaying the same
natural key/content returns the original record, timestamps and provenance. Reobserving it
later does not rewrite the original `observed_at`. Changed immutable content raises
`GraphConflict`; use a versioned `source_id` and `SUPERSEDES` for a changed observation.
Memory reviews/corrections and workflow transitions use dedicated audited services.
Workflow tasks use fenced investigation leases rather than generic property updates.

SecurityEvent deduplication is additionally enforced by a database uniqueness constraint on
`(tenant_id, account_id, source, source_id)`. Concurrent losers read the committed winner and
compare its fingerprint. Different content for the same event key is rejected, not silently
overwritten. No process-local lock or cache is responsible for event deduplication.

## Node types and permitted attributes

Every row below inherits the common properties above. Attributes are a closed vocabulary;
arbitrary provider payloads and arbitrary property names are rejected. Scalar values only.
Identity, Role, Policy, Permission, Resource, CloudAccount and SecurityEvent require
`account_id`. The tenant-wide CloudOrganization need not carry one account ID.

| Label | Purpose | Permitted domain attributes |
| --- | --- | --- |
| CloudOrganization | Synthetic organization grouping | `display_name`, `organization_id` |
| Identity | IAM user, workload or federated principal | `display_name`, `arn`, `identity_type` |
| Role | Assumable cloud role | `display_name`, `arn`, `description` |
| Policy | Versioned normalized policy observation | `display_name`, `arn`, `policy_type`, `document_sha256` |
| Permission | Normalized policy statement/action | `action`, `effect`, `condition_sha256` |
| Resource | Cloud resource | `display_name`, `arn`, `resource_type`, `region` |
| CloudAccount | Account boundary | `display_name`, `provider`, `account_number` |
| SecurityEvent | Deduplicated security change/event | **`event_type`, `event_time`, `content_sha256` required** |
| Evidence | Provenance-backed evidence | **`evidence_type`, `content_sha256` required**; optional `summary` |
| RiskFinding | Suspected risk/attack-path finding | `title`, `severity`, `status`, `summary` |
| Investigation | Investigation record | `title`, `status`, `summary` |
| Agent | Agent definition/identity | `name`, `agent_type`, `version` |
| AgentRun | A particular execution | `status`, `started_at`, `finished_at` |
| AgentMemory | Persistent shared memory observation | `memory_type`, `content`, `confidence` |
| Task | Durable coordination task | `title`, `status`, `priority`, `lease_expires_at` |
| Decision | Recorded agent decision | `action`, `rationale`, `status` |
| Handoff | Context transfer record | `reason`, `status` |
| Procedure | Reusable investigation procedure | `name`, `version`, `instructions` |
| MemorySpace | Explicit memory sharing boundary | **`name`, `revision`, `lock_version` required** |
| SecurityConcept | Tenant-scoped security vocabulary | `name`, `description`, `category` |

Hash sanitized, normalized event/evidence content; hashes are lowercase 64-character SHA-256
hex. Do not ingest raw credentials and then rely on hashing or redaction after storage.
Time-valued domain attributes use the same integer epoch-microsecond representation.

## Relationship directions

Relationship IDs incorporate tenant, type, endpoint IDs, provenance source/source ID and
`valid_from`. The same source event can therefore establish several different access edges.
All endpoints must exist in the repository tenant before any relationship is created.
Self-relationships are rejected. The following endpoint sets are enforced in code:

| Type | Source → target |
| --- | --- |
| BELONGS_TO | Any domain kind → CloudOrganization, CloudAccount, Investigation, Agent or MemorySpace |
| CAN_ASSUME | Identity or Role → Role |
| HAS_POLICY | Identity, Role, Resource, CloudAccount or CloudOrganization → Policy |
| GRANTS | Policy → Permission |
| TARGETS | Permission → Resource, Role or Identity |
| AFFECTS | SecurityEvent or RiskFinding → any cloud kind |
| DERIVED_FROM | Evidence, RiskFinding, AgentMemory or Decision → SecurityEvent, Evidence, AgentMemory, RiskFinding, Decision, AgentRun, Investigation or Procedure |
| SUPPORTS | Evidence, AgentMemory or Decision → RiskFinding, Investigation, Decision, Resource, Identity or Role |
| CONTRADICTS | Evidence, AgentMemory or Decision → RiskFinding, Evidence, AgentMemory, Decision or Investigation |
| CAUSED_BY | SecurityEvent, RiskFinding, Decision, Task or Handoff → SecurityEvent, Decision or AgentRun |
| INVESTIGATES | Investigation, Agent or AgentRun → RiskFinding, SecurityEvent, Identity, Role, Resource or Investigation |
| REMEMBERS | Agent, AgentRun or Investigation → AgentMemory |
| ASSIGNED_TO | Task or Investigation → Agent |
| DEPENDS_ON | Task → Task |
| HANDS_OFF_TO | Agent, Task or Handoff → Agent or Handoff |
| USES_PROCEDURE | AgentRun, Task or Decision → Procedure |
| SUPERSEDES | A new version → an older version of the **same node kind** |
| MEMBER_OF | Agent → MemorySpace |
| HAS_CONTEXT | MemorySpace → source Entity, excluding MemorySpace |
| ABOUT | AgentMemory → subject Entity, excluding AgentMemory and MemorySpace |
| INCLUDES_MEMORY | Handoff → AgentMemory |

These are structural directions, not a completed IAM authorization evaluator. For example,
the presence of an unverified `GRANTS` observation is not proof of effective AWS permission.

## Temporal access

Every relationship stores `valid_from`, optional `valid_to`, `confidence` in `[0,1]`, and
`verification_status` (`unverified`, `verified`, `disputed`, or `rejected`). Missing `valid_to`
means an open-ended interval. Empty/reversed intervals are rejected before database access.

`CAN_ASSUME`, `HAS_POLICY`, `GRANTS` and `TARGETS` additionally require `source_event_id`,
which must reference an existing SecurityEvent **in the same tenant**. Other relationship
types can also supply it, in which case the same existence/scope check applies.

Validity is half-open: **`valid_from <= t AND (valid_to IS NULL OR t < valid_to)`**.
`relationships(..., at=None)` returns all history; pass an aware instant to filter it.

`close_relationship()` atomically checks the source, target, relationship and closing event
scope, then sets `valid_to`, `closed_by_event_id` and `updated_at`. The closing timestamp
must exceed `valid_from`. An identical closure replay changes nothing. A contradictory
closure fails. Revocation preserves the original edge, source event, confidence and
provenance. A later regrant is a new edge with a new `valid_from`/source observation.

## Indexes, constraints and atomic writes

`SchemaManager.initialize()` creates missing range indexes, creates missing constraints,
waits for them to become operational, and only then records schema version 8. Repeating it
is safe. Failed constraints/indexes or newer unsupported schema versions fail clearly.
It never drops indexes/constraints or repairs conflicting existing data silently.

- `Entity`: indexes on `id`, `tenant_id`; unique `id`; mandatory common fields.
- All 20 specific labels: indexes on `id`, `tenant_id`, `account_id`, `source_id`.
- SecurityEvent: additional `source`/`event_time` indexes and composite event uniqueness.
- Evidence: additional `content_sha256` index.
- Each of the 21 relationship types: indexes on `id`, `tenant_id`, `valid_from`; unique
  relationship ID and mandatory common/endpoint/temporal fields.
- Cloud account scope, required event/evidence fields and access-edge source event IDs
  also have database mandatory constraints.

FalkorDB constraints are asynchronous; successful creation is not sufficient verification.
The initializer checks actual `db.constraints()` and `db.indexes()` operational status.
The installed SDK's `execute_command()` issues `GRAPH.CONSTRAINT` with separate arguments.

Each repository mutation is one server-side Cypher statement. Creation uses a guarded
`UNION ALL` read/create statement with all mandatory properties supplied directly in CREATE.
This accommodates FalkorDB 4.22.1's immediate mandatory checks and lack of map-parameter
inlined CREATE support. Property shapes use validated identifiers; every value remains an
SDK parameter. Real tests prove that a unique-constraint failure rolls back an earlier
mutation in the **same statement**.

`execute_atomic()` does not provide a multi-statement BEGIN/COMMIT API. Separate calls and
Redis pipelines must not be represented as rollback-capable transactions. Future operations
that need to commit together must be expressed as one validated Cypher statement.

Transient reads and explicitly idempotent writes use bounded exponential retry. Authentication,
syntax and constraint errors are not blindly retried. Lost replies to non-idempotent writes
raise `GraphWriteUncertain` and require reconciliation. The connection manager caches only
socket/topology resources and completed schema setup; `reconnect()` resets these resources.

## Repository security boundary

Construct `GraphRepository(database, TenantScope(tenant_id=trusted_tenant))` from authenticated
application context when user APIs are added. Never let an LLM select the tenant. Every
read/write binds that tenant on nodes, relationship and source-event matches; caller input
cannot override it. Cross-account relationships inside one tenant are supported.

The repository exposes no raw-Cypher endpoint, generic property update or arbitrary traversal.
Labels/types come from enums; property names pass a closed vocabulary and identifier checks;
IDs, text, scope, timestamps and pagination are parameters. Cursor pagination is bounded to
1–1000 records. The low-level database API is trusted infrastructure code, not an LLM tool.
Database administrators retain direct graph access; repository isolation is not a per-tenant
database ACL. HTTP authentication itself remains a later phase.

Credential property names are excluded. Known key patterns/private keys are rejected in text,
and provenance URIs cannot contain authentication or signed query strings. Generic free text
cannot reliably reveal every possible opaque secret: callers must supply sanitized summaries,
never provider credentials or full untrusted secret-bearing responses. No keys are needed for
the synthetic demo or tests. Secrets belong in environment/workload identity configuration.

## Python example

```python
from datetime import UTC, datetime
from app.core.config import Settings
from app.db.falkordb import FalkorDBConnectionManager
from app.models.graph import NodeInput, NodeKind, Provenance, TenantScope
from app.repositories.graph import GraphRepository

db = FalkorDBConnectionManager(Settings())
try:
    db.initialize_schema()
    repo = GraphRepository(db, TenantScope(tenant_id="example-tenant"))
    identity = repo.upsert_node(NodeInput(
        kind=NodeKind.IDENTITY,
        account_id="111111111111",
        provenance=Provenance(
            source="synthetic-demo", source_id="identity/alice",
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            collector="demo-importer", source_uri="urn:shadowchain:demo:alice",
        ),
        attributes={"display_name": "Alice", "identity_type": "IAMUser"},
    ))
    assert repo.get_node(identity.ref) == identity
finally:
    db.close()
```

## Valid FalkorDB Cypher examples

Execute these templates through the official SDK with a parameter dictionary. IDs and tenant
values below are parameters, not interpolated strings. They are trusted administrative/query
examples; application services should call repository methods.

```cypher
MATCH (n:Entity:Identity {tenant_id: $tenant_id, id: $id})
RETURN properties(n)
```

Historical access including both tenant-scoped endpoints and the edge:

```cypher
MATCH (a:Entity:Identity {tenant_id: $tenant_id, id: $identity_id})
      -[r:CAN_ASSUME]->(b:Entity:Role {tenant_id: $tenant_id})
WHERE r.tenant_id = $tenant_id
  AND r.valid_from <= $at
  AND (r.valid_to IS NULL OR $at < r.valid_to)
RETURN a.id, b.id, r.id, r.source_event_id, r.confidence, r.verification_status
```

Evidence provenance for a finding:

```cypher
MATCH (e:Entity:Evidence {tenant_id: $tenant_id})
      -[s:SUPPORTS]->(f:Entity:RiskFinding {tenant_id: $tenant_id, id: $finding_id})
WHERE s.tenant_id = $tenant_id
RETURN e.id, e.content_sha256, e.source, e.source_id, e.source_uri, e.collector
```

Tenant-scoped event lookup (all four natural-key fields):

```cypher
MATCH (e:Entity:SecurityEvent {
  tenant_id: $tenant_id, account_id: $account_id, source: $source, source_id: $source_id
})
RETURN properties(e)
```

Safe static schema operations (DDL identifiers are version-controlled constants):

```cypher
CREATE INDEX FOR (n:Identity) ON (n.id, n.tenant_id, n.account_id, n.source_id)
```

```cypher
CALL db.constraints() YIELD type, label, properties, entitytype, status
RETURN type, label, properties, entitytype, status
```

```cypher
CALL db.indexes() YIELD label, types, entitytype, status
RETURN label, types, entitytype, status
```

Example parameter `at = 1767225600000000` represents `2026-01-01T00:00:00Z`. Never use
inclusive `at <= valid_to` for the end boundary.

## Initialize and verify

```powershell
# In the private Compose network:
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\init-graph.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\init-graph.ps1 -VerifyOnly
docker compose --profile test run --build --rm tests
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-persistence.ps1
```

The persistence script explicitly seeds labeled synthetic records in tenant
`shadowchain-verification`, recreates the backend/database containers, and compares the
entire persisted node/relationship snapshot. The `read` helper cannot recreate missing
domain records, so an empty database cannot pass. Pytest gives each graph integration test
a unique `test_shadowchain_<uuid>` graph, then deletes only that graph; it never flushes the DB.

References: [FalkorDB range indexes](https://docs.falkordb.com/cypher/indexing/range-index),
[constraint creation and lifecycle](https://docs.falkordb.com/commands/graph.constraint-create),
[parameterized Cypher](https://docs.falkordb.com/commands/graph.query).

## Phase 3 temporal projection metadata

Schema version 3 adds CloudOrganization and indexes Entity `entity_key`, `scenario_id`,
`dataset_version` and `source_time`. Demo entities also carry `synthetic=true`, a logical key,
source-event reference and optional node validity interval. SecurityEvent stores validated
normalized payload JSON, content digest, sequence, source time, ingestion time, generation
and applied revision. Node validity versions preserve earlier investigations/findings.
Relationship validity uses the same half-open microsecond intervals as Phase 2.

DemoDataset and DemoStream are internal control labels, separate from domain Entity records.
Each has a unique indexed ID and tenant scope. DemoDataset holds version, manifest digest,
revision and a transaction coordination counter. DemoStream holds scenario, sequence,
watermark and reset generation. They contain no credentials. Every repository control-record
lookup binds the fixed demo tenant; these labels do not introduce another storage layer.

For example, this trusted administrative query returns retained synthetic observation versions:

```cypher
MATCH (m:Entity:AgentMemory {tenant_id: $tenant_id, scenario_id: $scenario_id})
WHERE m.source_time <= $at
RETURN m.id, m.source_id, m.source_event_id, m.source_time, m.summary
```

Conflict links at a source-time snapshot:

```cypher
MATCH (a:Entity {tenant_id: $tenant_id})-[r:CONTRADICTS]->(b:Entity {tenant_id: $tenant_id})
WHERE r.tenant_id = $tenant_id AND r.scenario_id = $scenario_id
  AND r.valid_from <= $at AND (r.valid_to IS NULL OR $at < r.valid_to)
RETURN a.id, b.id, r.source_event_id
```

Use `scenario_id = 'C'` and the reserved synthetic tenant for the conflict demo.
See [temporal_demo.md](temporal_demo.md) for the manifest, atomic event writes, temporal
snapshot semantics and API examples. Negative schema fixtures use an isolated nonpersistent
test server; see [phase3_verification.md](phase3_verification.md) for recovery limits.

## Phase 4 risk analyses and authorization metadata

Schema 4 adds a unique `RiskAnalysis.id` constraint, mandatory common provenance/timestamp
fields and indexes on `RiskAnalysis(id, tenant_id, observed_at)` and
`RiskFinding(analysis_id, path_id, assessed_at)`. RiskAnalysis is an internal control label
outside Entity, with its complete saved response stored in FalkorDB. Domain findings and
evidence retain the existing Entity labels, constraints, tenant binding and UUID identity rules.

RiskFinding adds `analysis_id`, `path_id`, `risk_score`, `assessed_at` and `engine_version`.
Evidence adds `record_json` and `analysis_id` for immutable source-record snapshots, plus
structured verification/control facts (`status`, `fact_value`, `control_type`). Evidence
SUPPORTS findings, DERIVED_FROM source events, and findings AFFECT their cloud subjects.
Source copies persist even if a synthetic reset deletes the original event/edge.

Permission adds normalized `conditions_json` and `principal_arns_json`. Principal/resource
metadata can mark required modeled authorization constraints through `boundary_required`,
`session_policy_required`, `scp_required`, `rcp_required`, `resource_policy_required`, plus
`data_classification`. Boolean, integer and credential rejection rules apply to these fields.
HAS_POLICY now includes account/organization policy owners; SUPPORTS can connect structured
evidence to cloud principals/resources. No extra graph database or Redis key namespace is used.

Tenant-safe evidence retrieval uses real graph traversal:

```cypher
MATCH (e:Entity:Evidence {tenant_id: $tenant_id})
      -[s:SUPPORTS {tenant_id: $tenant_id}]->
      (f:Entity:RiskFinding {tenant_id: $tenant_id, id: $finding_id})
RETURN f.analysis_id, e.id, e.content_sha256, e.record_json
```

Source provenance when the original event is retained:

```cypher
MATCH (e:Entity:Evidence {tenant_id: $tenant_id})
      -[d:DERIVED_FROM {tenant_id: $tenant_id}]->
      (event:Entity:SecurityEvent {tenant_id: $tenant_id})
WHERE e.analysis_id = $analysis_id
RETURN e.id, event.id, event.source, event.source_id, event.event_time
```

See [risk_engine.md](risk_engine.md) for the actual bounded traversal, supported normalized
policy constraints, conservative unresolved results and potential-path claim boundary.

## Phase 5 memory schema

MemorySpace and SecurityConcept extend the domain labels. Membership role and source-context
access are explicit temporal MEMBER_OF and HAS_CONTEXT edges, provisioned by trusted code.
ABOUT and INCLUDES_MEMORY link observations to subjects and handoffs to retained memory.
MemoryService uses the existing REMEMBERS, DERIVED_FROM, CONTRADICTS and SUPERSEDES types.

Managed AgentMemory nodes have the secondary `PersistentMemory` label and carry
`memory_key`, `series_id`, `memory_version`, `author_agent_id`, `audience`, `state`,
`verification_status`, `source_trust`, `space_id`, `request_sha256`, `claim_key`,
`claim_value_json`, `claim_json`, `procedure_json`, `source_provenance_json`,
`memory_engine_version`, `reference_count` and validity fields. The label enforces mandatory
core identity, type, author, space, audience, state, verification, confidence, content,
valid-from, trust, engine, source-provenance and reference-count fields. Review/supersession timestamps
and reviewer IDs are also permitted. Legacy Phase 3 observations keep their original shape.

MemorySeries is an internal control label, outside Entity, with unique ID, required common
provenance/timestamps, `space_id`, `head_id` and `version`. Its head and the space revision
advance in the same atomic statement as the content version and audit record. Indexes cover
MemorySeries ID/tenant/space and AgentMemory series/space/state/type/claim key. MemorySpace
requires `name`, `revision` and `lock_version` at the database level.

Decision adds space/actor/request hash, response JSON and audit JSON. Handoff adds space,
sender/recipient and context hash. These structured JSON properties are bounded normalized
records stored in FalkorDB, not an alternate local store. Curator review mutates verification
metadata with an immutable Decision; content corrections create a new version and SUPERSEDES
edge. See [memory.md](memory.md) for actual Cypher, current-access filtering, source provenance,
conflict detection, the instruction/data boundary and all three memory types.

## Phase 6 durable workflow profiles

Schema 6 adds WorkflowInvestigation and WorkflowRun secondary labels to Investigation and
AgentRun. Mandatory profile fields enforce durable request/source metadata, cursor/revision,
lease expiry, provider mode, run/agent identity, stage/status, attempts, input/memory/output
properties, pending tasks and handoff targets. WorkflowTask is a secondary label distinguishing
the six executable analysis steps from non-executable remediation review tasks. All remain
Entity records with existing unique IDs and required provenance/tenant/timestamps.

WorkflowInvestigation has ID/tenant/status/lease-expiry indexes. WorkflowRun has
ID/tenant/investigation/agent/status indexes. Task adds investigation/stage/type/status indexes.
There are still 20 domain node types and 21 relationship types; workflow profiles reuse the
existing kinds rather than introducing a second graph or database.

Investigation stores bounded request JSON/source fingerprints, provider mode, sequence,
revision, lock version, lease owner and expiry. AgentRun stores stage/agent/investigation,
attempt count, operation ID, input evidence IDs JSON, retrieved memory IDs JSON, typed output
JSON/hash, pending IDs, memory/handoff IDs and safe error codes. Task stores workflow version,
investigation/owner/stage/sequence/type and normalized proposal JSON for review tasks.
Time/count fields remain nonnegative integers; text passes the existing credential rejection.

AgentRun BELONGS_TO its Agent and Investigation. Task BELONGS_TO Investigation, ASSIGNED_TO
its Agent and DEPENDS_ON its predecessor. Finding DERIVED_FROM AgentRun retains analysis
provenance. AgentRun REMEMBERS AgentMemory; Handoff CAUSED_BY AgentRun and HANDS_OFF_TO the
recipient connect persisted outputs. Coordinator-created review Tasks are CAUSED_BY its run.
Unique prepare/complete Decision IDs reconcile operation retries through the existing Entity
uniqueness constraint. See [agents.md](agents.md) for actual Cypher, lease fences and restart
reconstruction; no default in-memory or SQL LangGraph checkpointer is used.

## Phase 7 SOC case profile

`SecurityCase` is a secondary label on `Entity:Investigation`, distinct from the existing
`WorkflowInvestigation` profile. It requires status, severity, path ID, revision, owner agent,
MemorySpace ID, episode generation, assessed timestamp and case version. It uses the same
stable IDs, required tenant, provenance and integer UTC timestamps. Status is one of NEW,
TRIAGING, INVESTIGATING, AWAITING_REVIEW, RESOLVED or CLOSED. Optional resolution is an explicit
review disposition; it never represents a deployed AWS correction.

SecurityCase indexes cover ID, tenant, status, path ID, severity and memory space. Decision
adds case ID/action indexes. `CaseHead` stores tenant/path/space-specific episode pointers
and `CaseStream` stores tenant-scoped revision/serialization metadata. Both have unique IDs,
mandatory tenant/pointer fields as applicable, ID/tenant indexes and source/timestamp
provenance. They are control records in this graph, not an alternative persistence layer.
The schema retains **20 domain node types and 21 relationship types**.

Investigation permits `severity`, `path_id`, `case_version`, `owner_agent_id`, `resolution`,
`assessed_at` plus existing space/revision/generation fields. Task adds `check_key`; Decision
adds `case_id`; Handoff adds `case_id` and hashed `context_json`. These remain closed
property sets. Four case Task nodes use `task_type=investigation_check`, BELONGS_TO the case,
and ASSIGNED_TO their current owner with temporal history. Immutable Decisions store
operation request digests, audit details, response JSON and actor IDs.

The case INVESTIGATES source assets, events, findings, prior incidents and workflow runs.
Evidence SUPPORTS or CONTRADICTS the case; case REMEMBERS authorized shared AgentMemory.
Hypothesis/review Decisions DERIVED_FROM the case preserve original statements and later
assessments. Handoff BELONGS_TO the case, with agent HANDS_OFF_TO handoff HANDS_OFF_TO agent.
SUPERSEDES links a newer case episode to an older case of the same domain kind.

Read [investigations.md](investigations.md) for executed parameterized Cypher examples,
historical asset traversal, explicit review semantics, lifecycle/authorization rules and
proof that completed tasks and evidence survive cross-session handoff.

## Phase 8 remediation profiles

`RemediationProposal`, `RemediationSimulation` and `RemediationReview` are secondary labels
on `Entity:Decision`. They retain common stable IDs, tenant/provenance/timestamps and existing
uniqueness constraints; there are still 20 domain node kinds and 21 relationship kinds.
All three profiles have ID/tenant/space indexes. Proposals also index status/category;
reviews index proposal ID. Mandatory profile fields cover source/analysis/target scope,
revisions and review/simulation evidence references.

Decision's closed fields add simulator version/category, analysis and target JSON, permission/
finding/evidence references, source/result SHA-256, proposal/simulation IDs, requested reason,
reviewer ID and chunk references. Proposal statuses are PROPOSED, UNDER_REVIEW, APPROVED and
REJECTED. Source relationships stay unchanged by a simulation. Review Decisions are immutable;
only the proposal's review status/revision/updated timestamp advance.

`RemediationStream` is a tenant-scoped control record with unique ID, common required
provenance/timestamps, revision/lock version and ID/tenant indexes. Each metadata mutation
checks its revision and current writer/curator membership atomically. Review mutations also
compare the proposal revision. No process-local state owns the review cursor.

Evidence SUPPORTS proposals/simulations. Proposals DERIVED_FROM RiskFindings; simulations and
reviews DERIVED_FROM their proposals. Large simulation JSON is saved as bounded immutable
Evidence chunks with per-chunk and complete-result hashes. Those are graph records, not a
file or alternative persistence database. See [remediation.md](remediation.md) for actual
Cypher diagnostics and visualization-ready responses.

## Phase 9 AWS observations and report metadata

The domain vocabulary stays at 20 node kinds and 21 relationship kinds. `AwsSyncRun` is an
`Entity:Decision` profile; `AwsIamObservation` marks versioned IAM snapshot entities;
`ReportExport` is an `Entity:Evidence` profile. `AwsImportStream` is a tenant/account/kind
control record with stable unique ID, common provenance, revision, lock version and watermark.
It serializes complete observation batches in this graph. No schedule is registered.

All profiles index ID/tenant/account as applicable. IAM observations also index `valid_from`;
report exports index investigation/status. Mandatory constraints cover import request digest,
result/coverage JSON, stream revision/watermark, observation account/valid-from, and report
destination/status/hash/investigation. Creation supplies mandatory properties in the initial
Cypher literal; it does not rely on setting them after a constrained node is created.

Closed optional node fields add `source_api`, `region`, `import_run_id`. Decision adds
`integration_kind`, `coverage_json`; Evidence adds `identity_refs_json`, `artifact_uri`,
`export_mode`, `byte_count` (decimal string), `redaction_version`, `request_sha256`,
`investigation_id`, `status`. `record_json` is normalized, allowlisted evidence only, never
raw CloudTrail JSON or report bytes. AWS account IDs and ARN references are retained in the
private graph for correlation; report renderers and structured logs omit them.

IAM uses `shadowchain.aws.iam.v1`; CloudTrail uses `shadowchain.aws.cloudtrail.v1`.
IAM stable IDs include the run key, so separate observations retain history. A successful
new IAM scan atomically closes old snapshot-node and edge intervals and creates the entire
new snapshot plus its Decision/event/evidence. Source observations are not proof of when a
configuration changed. Managed default policies may be shared by several roles in one
snapshot without duplicating their GRANTS edges. Non-default versions are evidence only.
Unsupported selectors/resource patterns mark the attached policy unresolved. Accountless
resource references require unresolved resource-policy authorization; no sensitivity or
ownership is inferred. Trust documents alone never create CAN_ASSUME.

CloudTrail IDs use account/source/region/EventId, so overlapping windows reuse the canonical
SecurityEvent/Evidence. Changed normalized content for that identity is rejected. The original
event time is separate from ingestion timestamps. Same-account actor references receive
AFFECTS links; foreign actors remain contextual evidence. Neither scan nor log import executes
an IAM change or reconstructs complete AWS effective permissions.

These parameterized queries use the same contract as the repository. `$at` is UTC epoch
microseconds; `$tenant_id` and `$account` come from trusted server configuration:

```cypher
MATCH path=(r:Role:AwsIamObservation)-[h:HAS_POLICY]->(p:Policy:AwsIamObservation)
WHERE all(n IN nodes(path) WHERE n.tenant_id = $tenant_id AND n.account_id = $account)
  AND all(e IN relationships(path) WHERE e.tenant_id = $tenant_id)
  AND r.valid_from <= $at AND (r.valid_to IS NULL OR $at < r.valid_to)
  AND h.valid_from <= $at AND (h.valid_to IS NULL OR $at < h.valid_to)
RETURN r.id, p.id, p.policy_type, h.source_event_id
```

```cypher
MATCH path=(e:Evidence)-[:DERIVED_FROM]->(s:SecurityEvent)
WHERE all(n IN nodes(path) WHERE n.tenant_id = $tenant_id AND n.account_id = $account)
  AND all(r IN relationships(path) WHERE r.tenant_id = $tenant_id)
  AND s.source = 'shadowchain.aws.cloudtrail.v1'
RETURN s.source_id, s.region, s.event_time, s.created_at, s.source_api, e.id
```

```cypher
MATCH path=(e:ReportExport)-[:SUPPORTS]->(i:Investigation)
WHERE all(n IN nodes(path) WHERE n.tenant_id = $tenant_id)
  AND all(r IN relationships(path) WHERE r.tenant_id = $tenant_id)
  AND i.id = $investigation_id
RETURN e.id, e.status, e.content_sha256, e.byte_count, e.export_mode
```

These diagnostics do not replace current agent membership checks in ReportService. Export
metadata moves PREPARED → PUBLISHED only after external publication succeeds; retries reconcile
the hash. `artifact_uri` is an admin-scoped local path or private S3 URI, never a public URL.
Report files are artifacts, not persistent sources for investigation state. See
[AWS_SETUP.md](AWS_SETUP.md) for exact API scope, redaction and incomplete coverage.

## Phase 10 notification and API control profiles

`LiveStream` and `LiveEvent` are infrastructure profiles outside the domain `Entity` label,
so authorized synthetic resets cannot erase replay history. Both carry stable IDs, tenant,
common source/collector/URI/fingerprint provenance and integer UTC timestamps. LiveStream
adds sequence/lock version; LiveEvent adds event type, tenant sequence, bounded entity-ID JSON
and explicit reference-truncation metadata. All fields are supplied in the creation literal.
ID uniqueness and mandatory constraints apply; `(tenant_id,sequence)` is additionally unique
on LiveEvent. ID/tenant/sequence indexes support reconnect replay. No journal pruning is exposed.

The stream row is locked before each instrumented domain mutation. Event creation and sequence
increment are part of that same Cypher statement, after its optimistic/domain guard succeeds.
An operation UUID protects SDK statement retries. Events reference actual committed records;
they do not fabricate individual animations or contain raw cloud logs, memory instructions,
credentials or arbitrary free text. Source-event time remains separate from notification order.

`ApiRateBudget` is a unique-ID graph control record with tenant/window/usage, indexed by ID
and tenant. It provides a shared fixed-minute quota, not a business event or agent memory.
Quota updates and schema/readiness operations do not advance LiveStream. There is still one
primary graph, 20 domain node kinds and 21 relationship kinds.

Evidence's closed optional attributes add `record_id`, with an index. New immutable risk
snapshots store the original source record ID here for bounded visualization associations.
Earlier snapshots are not rewritten; their retained JSON and path/evidence retrieval continue
to work. General graph projections omit raw snapshot JSON and untrusted memory/procedure text.

Read-only replay diagnostic, with a server-bound tenant and exclusive cursor:

```cypher
MATCH (e:LiveEvent {tenant_id:$tenant_id})
WHERE e.sequence > $after AND e.sequence <= $high_watermark
RETURN e.id,e.sequence,e.event_type,e.created_at,e.entity_ids_json
ORDER BY e.sequence LIMIT $limit
```

The API additionally checks contiguous sequences, page/frame bounds and current authentication.
See [API_CONTRACT.md](API_CONTRACT.md) for the protocol and pagination consistency rules.

# Graph-powered potential attack path engine

Engine `risk-2026.1`, graph schema 4. Findings are always **POTENTIAL ATTACK PATHS**.
The engine performs no AWS changes, scanner calls or LLM calls and needs no provider keys.
FalkorDB remains the authoritative store for source relationships, events, analyses, findings
and evidence. No Python dictionary, local file or secondary database supplies reachability.

## What FalkorDB does

`RiskRepository` issues one `GRAPH.RO_QUERY` for all requested views. Its Cypher enumerates
bounded identity/role chains and their terminal policy/permission/resource paths. Additional
graph patterns retrieve caller grants, destination trust, resource policies, attached
boundaries/session policies, account/organization controls, structured evidence and events.
Python evaluates the returned normalized authorization facts; it does not enumerate graph
paths or invent edges. The Phase 3 snapshot API remains compatible; use `/risk` for this engine.

The core traversal has this shape (the depth is a validated code constant, never input text):

```cypher
MATCH p=(identity:Entity:Identity {tenant_id: $tenant_id})
        -[:CAN_ASSUME*0..4]->(principal:Entity)
        -[:HAS_POLICY]->(:Entity:Policy)
        -[:GRANTS]->(:Entity:Permission)
        -[:TARGETS]->(resource:Entity:Resource {tenant_id: $tenant_id})
WHERE resource.sensitive = true
  AND principal.kind IN ['Identity', 'Role']
  AND ($identity_id IS NULL OR identity.id = $identity_id)
  AND all(n IN nodes(p) WHERE n.tenant_id = $tenant_id
      AND coalesce(n.source_time, n.valid_from, n.observed_at) <= $at
      AND (n.valid_from IS NULL OR n.valid_from <= $at)
      AND (n.valid_to IS NULL OR $at < n.valid_to))
  AND all(e IN relationships(p) WHERE e.tenant_id = $tenant_id
      AND e.valid_from <= $at AND (e.valid_to IS NULL OR $at < e.valid_to)
      AND e.verification_status = 'verified' AND NOT e.id IN $excluded)
RETURN [n IN nodes(p) | properties(n)], [e IN relationships(p) | properties(e)]
```

The actual query also applies scenario scope to **every** node/edge, returns active markers
for historical policy facts, traverses account ancestry, and groups multiple timestamps or
what-if views inside the same server read. See `backend/app/repositories/risk.py` for the
complete query. Temporal intervals are UTC epoch microseconds, half-open `[valid_from, valid_to)`.

Repository scope is a frozen `TenantScope`; no public request can select a tenant or Cypher
string. Intermediate nodes, edges, evidence, ancestry and source events are tenant filtered.
The reusable core supports any tenant's normalized graph, as covered by independent fixtures.

## Supported authorization subset

A connected graph path is only a candidate. Every role assumption requires a matching caller
IdentityPolicy Allow and destination TrustPolicy Allow, plus applicable modeled constraints.
The final action requires a matching identity-policy grant on the final principal.

| Representation | Evaluation |
| --- | --- |
| `Identity/Role -HAS_POLICY-> Policy(IdentityPolicy) -GRANTS-> Permission -TARGETS-> destination` | Exact modeled destination, case-insensitive action names and IAM `*` / `?` action wildcards |
| `Role -HAS_POLICY-> Policy(TrustPolicy) -GRANTS-> Permission -TARGETS-> caller` | Explicit modeled caller trust; CAN_ASSUME alone grants nothing |
| `Resource -HAS_POLICY-> Policy(ResourcePolicy) ... -TARGETS-> Resource` | Principal selector required; matching Deny overrides Allow; explicit Allow additionally required for cross-account access or `resource_policy_required=true` |
| `Identity/Role -HAS_POLICY-> Policy(PermissionsBoundary/SessionPolicy)` | Allow intersection; an attached empty policy cannot be ignored |
| `CloudAccount/CloudOrganization -HAS_POLICY-> Policy(ServiceControlPolicy/ResourceControlPolicy)` | Membership traversed in FalkorDB; policies union at one owner/type and intersect across applicable owners/types; explicit Deny overrides |

`Permission.principal_arns_json` is a JSON array of exact IAM principal ARNs or `"*"` for
normalized ResourcePolicy statements. Session-principal selectors and resource-policy-only
authorization are outside this subset. Wildcard principals do not themselves establish
internet exposure. Resource targets are explicit graph nodes; this is not arbitrary raw AWS
policy JSON evaluation or an ARN wildcard resource resolver.

Supported `Permission.conditions_json` operators are `Bool`, `StringEquals`, `StringNotEquals`,
`ArnEquals`, `IpAddress`, `NotIpAddress`, `DateLessThan` and `DateGreaterThan`. Operators/keys are
ANDed; positive value lists use OR and negated lists use NOR. Supported context keys are
`aws:MultiFactorAuthPresent`, `aws:SourceIp`, `aws:RequestedRegion`, `aws:PrincipalArn` and
`aws:CurrentTime`. Principal ARN and source-time clock are graph/server derived. Other context
comes explicitly from the request and remains a hypothetical assumption, not AWS attestation.

```json
{
  "Bool": {"aws:MultiFactorAuthPresent": "true"},
  "IpAddress": {"aws:SourceIp": "192.0.2.0/24"}
}
```

Unknown conditions, missing context, malformed selectors, an opaque condition hash without
its conditions, or inconsistent condition hashes produce an **unresolved candidate**. An
unknown applicable Deny is never silently ignored. Unverified/disputed policy facts and
uncertain organization membership also produce unresolved candidates. Rejected facts cannot
authorize a path. Multiple modeled permissions boundaries are unresolved.

Flags `boundary_required`, `session_policy_required`, `scp_required`, `rcp_required` and
`resource_policy_required` make missing modeled constraints explicit. Account/organization
controls resolve through at most two membership hops; a third hop is detected and rejected
as unresolved instead of silently omitting deeper controls.

This is **not complete AWS effective authorization**. It omits IAM groups, NotAction/NotResource,
resource-policy-only grants, role-session exceptions, full ABAC/condition operators, transitive
STS session context, service-specific semantics, KMS, S3 ownership and public-access-block
evaluation. Real missing or unmodeled AWS constraints can prevent every reported path. Zero
findings does not certify safety; inspect `rejected_candidates` and response `limitations`.

## Explainable deterministic score

The score is a prioritization heuristic, clipped to 0–100, with named factors and the graph
record IDs supporting each factor. It is not CVSS or an exploitation probability.

| Factor | Points |
| --- | --- |
| Resource sensitivity | 30; 40 when `data_classification=restricted` |
| Privilege | 30 for IAM/organization actions; otherwise 25 for wildcard grants, 20 for modeled write/delete actions, 10 for scoped read/other actions |
| Trust relationships | 3 per assumption hop, cap 12; plus 5 per cross-account hop, cap 10 |
| Internet exposure | 15 only for relevant structured, verified exposure evidence; otherwise 0 |
| Temporary-access violation | 12 when an active path grant has expired/resolved business justification |
| Compensating controls | Monitoring -5, enforced private endpoint -10 (combined cap 15); required matched MFA -10; total reduction capped at 20 |

Structured exposure/control evidence is an Evidence node linked by a verified, temporally
active SUPPORTS relationship to a path principal/resource. It requires `status=verified` and
`fact_value=true`. Evidence types are `internet-exposure` and `compensating-control`; controls
use `control_type=monitoring` or `private-endpoint`. Verification is a trusted ingestion claim,
including synthetic claims in this demo; the engine does not verify AWS itself. Disputed
observations, free-form descriptions and unverified ACL claims earn no exposure points.
An enforced-control fact must describe enforcement, not merely existence of an endpoint.

Severity thresholds: low <40, medium 40–59, high 60–79, critical 80–100. Scenario A is
30 sensitivity +10 scoped read +6 for two role hops +5 for a cross-account hop = **51/100**.
No internet exposure or compensating controls are fabricated for that scenario.

## Differences, provenance and simulation

Differences compare stable logical path IDs. They return before/after analyses, added/removed/
unchanged IDs and first sampled introduction times. Source-event provenance identifies
created witness edges and related closed policy facts. Removing a Deny is covered explicitly:
the closed edge, closing event and immutable evidence snapshot are retained.

`transition_events` requires linked creation/closure provenance, not merely an equal timestamp.
Simultaneous relevant events are grouped. These are **modeled change attributions**, not proof
of real-world causality. Supporting grants may include alternative Allows; their presence does
not prove that each was individually necessary. Use what-if to test a specific removal in the
model. Absence at the earlier time can also mean unresolved authorization, not proven denial.

Timeline/difference sampling uses source-event timestamps and requested endpoints, with a
bounded refresh if event counts change while selecting samples. All resulting views share one
atomic graph read. Condition-only transitions between samples require explicit historical
queries; the API labels this sampling policy. History is valid-time over currently ingested
knowledge, not a bitemporal reconstruction of wall-clock knowledge.

What-if excludes the requested relationship IDs in both candidate traversal and policy/context
queries. It computes baseline and simulated views in one `GRAPH.RO_QUERY`. It does **not**
persist analyses, delete/close relationships, run schema DDL or call AWS. Removing an Allow
can break a path; removing a Deny can **increase** reachability. Both added and removed paths
are returned. `graph_mutated=false` is verified in integration tests.

## Persistent findings and evidence

`RiskAnalysis` is an internal graph control label with unique ID, tenant scope, provenance,
timestamps, context digest and serialized result. Its ID incorporates engine version, request,
as-of time and the returned graph evidence. Scoped inventory/event counts distinguish an
empty modeled scope from a populated scope with no enumerated candidate. Algorithm changes
must bump the engine version; unchanged graph/request inputs produce the same analysis ID.
Path IDs use actual principal/relationship/resource/permission identities, not demo verdicts.

One idempotent Cypher statement atomically creates the analysis, RiskFinding nodes and immutable
Evidence snapshots. Unique constraints reconcile concurrent requests/lost acknowledgements.
Evidence SUPPORTS each finding; Evidence DERIVED_FROM links to original SecurityEvents when
present; findings AFFECT their identities, roles and resources. All analysis-basis evidence is
retained, including exclusions/unknowns relevant to evaluation, not just positive witness edges.

Copies preserve source hashes, source/ingestion timestamps, intervals, event references and
request assumptions. Complete normalized event payloads stay in their original graph records;
evidence copies retain their identifying content hashes. Synthetic resets can remove original
events/edges and their direct links, but saved findings and copied evidence remain readable.
Evidence copies are separate from the versioned seed namespace, so they do not change the
demo's inventory or get erased by scenario reset. There is no automatic deletion/retention job.

Bounds: 0–4 role hops, 250 candidates per view, 2,000 paths per context collection, 32 temporal
views, 30 source events per timeline window, 50 accepted findings, 2,000 persisted evidence
records and 5,000 finding/evidence links per analysis. Saved result size is capped at 2 MB;
each evidence-record JSON is capped at 16,384 characters. Overflow returns
`risk_analysis_limit_exceeded`; no partial candidate list is reported as complete. Server query
timeouts remain active. Large graphs require narrower identity/time/depth scopes and further
query planning; enumeration depth does not make graph width unboundedly cheap.

## API and Windows demo

Current HTTP routes are local synthetic demo routes under `/api/v1/risk`, fixed to tenant
`shadowchain-synthetic-demo`, default scenario A. They return 404 outside demo mode or in
production. A future authenticated API must bind trusted tenant context; a caller-supplied
tenant is not accepted. `scenario=null`, unknown fields and unsafe/unbounded requests fail
validation. The engine itself is tenant-generic and has independent non-manifest tests.

| Method | Path | Request / result |
| --- | --- | --- |
| POST | `/paths/discover` | `at`, optional scenario/identity/action/depth/context; persist assessment |
| POST | `/paths/historical` | Same contract, explicit historical `at` |
| POST | `/timeline` | `before`, `after`, optional risk options; persist sampled assessments |
| POST | `/differences` | Same window/options; persist and compare sampled assessments |
| GET | `/findings/{finding_id}/evidence` | Saved finding, analysis and graph evidence snapshots |
| GET | `/analyses/{analysis_id}` | Recover an immutable saved analysis |
| POST | `/what-if` | `at`, `remove_relationship_ids`, optional risk options; read-only simulation |

From `shadowchain-ai`, after normal Compose startup:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-risk.ps1
# Workstation verification server uses an alternate local port:
.\scripts\demo-risk.ps1 -BaseUrl http://127.0.0.1:18000

# Prints a complete proof from the backend inside the private network:
docker compose exec -T backend python -m app.demo.risk_proof

$base = 'http://127.0.0.1:8000/api/v1/risk'
$body = @{scenario='A'; at='2026-10-16T12:00:00Z'; max_role_hops=4} | ConvertTo-Json
$analysis = Invoke-RestMethod "$base/paths/discover" -Method Post `
    -ContentType 'application/json' -Body $body
$finding = $analysis.findings[0]
Invoke-RestMethod "$base/findings/$($finding.finding_id)/evidence"
$simulation = @{
    scenario='A'; at='2026-10-16T12:00:00Z'
    remove_relationship_ids=@($finding.relationship_ids[-3])
} | ConvertTo-Json
Invoke-RestMethod "$base/what-if" -Method Post -ContentType 'application/json' -Body $simulation
```

See [generated proof](risk_engine_proof.json) and [verification](phase4_verification.md).
Implementation references: [FalkorDB MATCH](https://docs.falkordb.com/cypher/match),
[path/predicate functions](https://docs.falkordb.com/cypher/functions),
[AWS evaluation logic](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic_policy-eval-denyallow.html),
[permissions boundaries](https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_boundaries.html),
[condition operators](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_elements_condition_operators.html).

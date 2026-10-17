# Remediation recommendations and hypothetical simulation

Phase 8 adds persistent remediation proposals, interactive simulation, evidence and review
APIs. FalkorDB remains the authoritative store. Approval means approval of a recommendation
only. There is no execution endpoint, AWS client, IAM writer or command executor in this
subsystem. All findings remain **POTENTIAL ATTACK PATHS** under the existing normalized IAM
subset; a connected path or a favorable simulation does not prove effective AWS access.

## Recommendation rules

Candidates come from actual bounded FalkorDB traversals and deterministic policy evaluation.
They reference an exact relationship UUID, permission IDs, source events and immutable source
evidence. RiskFinding records also remain linked through DERIVED_FROM. Recommendations are
suggestions for workload-owner review, not assertions that access is unnecessary.

| Category | Required modeled evidence | Hypothetical change |
| --- | --- | --- |
| Remove excessive permission | An accepted potential path using an Allow grant or all-Allow identity policy attachment | Exclude the exact GRANTS or HAS_POLICY edge |
| Remove unnecessary role trust | An active verified CAN_ASSUME edge, supporting trust statements and accepted path | Exclude that modeled assumption edge |
| Review expired temporary access | Above, plus an expired/resolved justification identified by the risk score's evidence | Exclude the exact temporary assumption edge |
| Reduce broad resource access | Allow path with wildcard action or multiple targets for its permission | Exclude one exact TARGETS edge; do not invent a replacement IAM document |
| Review high-risk policy exception | Score at least 60 and verified structured policy-exception evidence supporting a path subject | Review and simulate excluding its exact Allow GRANTS edge |

Policy exceptions are never inferred merely because a policy is broad. No verified exception
evidence means no exception recommendation. Missing business telemetry means actual necessity
is unknown. Mixed Allow/Deny attachments and boundary/session/SCP/RCP policies are excluded
from removal recommendations. A target must be active at the requested source time, verified,
in the repository tenant/scenario and linked to an existing source event. Unknown conditions
and other unsupported IAM constraints retain the risk engine's conservative behavior.

## Simulation and impact

The existing RiskRepository now accepts a trusted `include_non_sensitive` option; normal risk
discovery keeps its existing sensitive-only behavior. Phase 8 loads all scoped identities and
both sensitive/non-sensitive resource candidates. It clears any selected identity filter for
collateral checks, so another modeled workload sharing a policy is not silently omitted.

One `GRAPH.RO_QUERY` materializes before/after views at the same source timestamp. The after
view applies a parameterized exclusion list to every traversed relationship:

```cypher
AND NOT e.id IN excluded
```

No clone, edge deletion, property update or transaction rollback is needed to simulate the
cloud graph. The exclusion overlay exists only within that computation. Python evaluates the
graph-returned paths with the existing IAM subset, including explicit denies and relevant
modeled constraints. It performs no alternative Python path search. Source cloud nodes,
permissions, temporal intervals and relationships remain unchanged.

After computation, a separate atomic metadata write stores the proposal/simulation and its
evidence in the same FalkorDB graph. These new coordination records are intentional;
`source_cloud_graph_mutated=false` describes the original cloud projection, not an absence
of audit storage. No hypothetical permission becomes an authoritative cloud relationship.

Each saved simulation includes:

- The complete before/after potential findings, scores, contributing events and limitations.
- Removed, added and remaining path IDs, including alternatives for affected identity/resource/action pairs.
- Path-count, maximum-score and summed-path-score differences. Scores are heuristics; their
  sum is not an exploitation probability or an independent portfolio-risk measurement.
- Potential access loss and remaining paths for each affected identity/resource pair,
  including non-sensitive resources. Legitimacy is always `unknown_requires_workload_owner_review`.
- Exact excluded edges, source snapshot IDs, permission/source-event provenance and hashes.
- Visualization nodes/edges, hypothetical-removal flags and before/after path IDs, without UI coordinates.

The impact scope is the requested action, time, scenario and bounded role depth. It is not
an exhaustive outage analysis across all AWS actions. Unsupported or unknown authorization
conditions can hide modeled candidates; an empty impact/result list is not a safety certificate.
Explicit owner review, real telemetry and broader AWS evaluation remain separate work.

Up to ten compatible proposals can be combined. They must have identical analysis options,
source snapshot digest and memory space. Rejected proposals cannot start new simulations.
An approval requires a saved **single-proposal** simulation so an unapproved combination
cannot be mistaken for the standalone effect of one recommendation.

## Persistence, replay and review

Schema 8 reuses Decision/Evidence kinds and adds RemediationProposal, RemediationSimulation
and RemediationReview profile labels, plus a RemediationStream control record. Every record
has stable IDs, tenant/provenance/timestamps and explicit space scope. Evidence SUPPORTS
the proposal/simulation; simulations and reviews DERIVED_FROM their proposals. Payloads are
bounded, immutable Evidence chunks in FalkorDB, individually and collectively SHA-256 checked
on retrieval. No runtime file or Python dictionary is a persistent source of truth.

Each proposal, simulation, recommendation batch and review has a stable operation identity.
Identical retries reuse committed records; a changed payload with the same identity returns
409. Writes serialize through stream revision checks, and proposal review also compares its
revision. Membership is rechecked inside the committing Cypher. Stored source snapshots
remain retrievable after cloud relationships change or the process/database restarts.

A new simulation against a changed source digest returns 409, requiring a new proposal.
An identical old simulation request returns its original result, clearly tied to the original
timestamp. Approval also checks that the proposal's source digest still matches; it does
not silently replace the reviewed evidence. This check is conservative, and approval remains
a historical recommendation decision rather than a guarantee about a changing AWS account.

```text
PROPOSED -> UNDER_REVIEW -> APPROVED
                        -> REJECTED
```

APPROVED and REJECTED are terminal review states. Each explicit review persists its reason,
reviewer, prior revision, optional simulation ID and recommendation-only scope. There is no
automatic approval workflow or transition to executed/deployed. Agent/model output cannot
select reviewer identity or grant itself review authority.

The local HTTP control plane fixes Coordinator membership and a synthetic local human-review
identity on the server. The explicit review request represents the demo analyst action; it
does not authenticate a real human. Routes are disabled in production or with demo mode off.
Before deployment, authenticated middleware must bind the actual reviewer/tenant/space and
separate human review credentials from agent credentials. Caller headers/body cannot select
these identities in the demo. Trusted bootstrap establishes team membership separately.

Limits include four role hops, existing 250 candidate/2,000 context-record limits, twenty
recommendations per batch, ten combined proposals, 100 linked simulations/reviews per
proposal and a 1 MB serialized simulation payload. Oversized reads return 422 rather than
silently certifying a truncated result. List `limit` bounds the whole result; no pagination
is implemented yet. Recommendation responses report omitted candidate counts.

## HTTP endpoints

All paths below are under `/api/v1/remediation`:

| Method/path | Purpose |
| --- | --- |
| `POST /recommendations` | Generate and persist deterministic candidate recommendations |
| `POST /proposals` | Propose one qualified exact modeled relationship change |
| `GET /proposals?limit=50` | List current sharing-space proposals |
| `GET /proposals/{id}` | Inspect target, permission IDs, evidence, simulations and review history |
| `POST /proposals/{id}/simulations` | Save a single or compatible combined what-if result |
| `GET /simulations/{id}` | Reconstruct and verify a saved simulation from FalkorDB |
| `POST /proposals/{id}/reviews` | Explicit UNDER_REVIEW, APPROVED or REJECTED recommendation decision |

No execute/apply/deploy route exists. Bodies reject extra query, executor, AWS action,
reviewer and authority fields. Values are parameterized; traversal bounds/profile labels are
code-owned constants. Reasons and source content are non-executable data and pass existing
credential rejection rules. Errors use the existing centralized envelope and correlation ID.

PowerShell example after preparing the trusted synthetic demo:

```powershell
$base = 'http://127.0.0.1:8000/api/v1/remediation'
$body = @{request_id='analyst/recommend-1'; analysis=@{
    at='2026-10-16T13:00:00Z'; scenario='A'
}; limit=20} | ConvertTo-Json -Depth 5
$result = Invoke-RestMethod "$base/recommendations" -Method Post `
    -ContentType 'application/json' -Body $body
$id = $result.proposals[0].proposal_id
$body = @{request_id='analyst/simulate-1'} | ConvertTo-Json
$simulation = Invoke-RestMethod "$base/proposals/$id/simulations" -Method Post `
    -ContentType 'application/json' -Body $body

# Perform these review requests only after inspecting evidence, alternatives and impact.
$proposal = Invoke-RestMethod "$base/proposals/$id"
$body = @{request_id='analyst/start-review-1'; expected_revision=$proposal.revision;
    status='UNDER_REVIEW'; reason='SYNTHETIC analyst opens recommendation review'} | ConvertTo-Json
$proposal = Invoke-RestMethod "$base/proposals/$id/reviews" -Method Post `
    -ContentType 'application/json' -Body $body
$body = @{request_id='analyst/approve-1'; expected_revision=$proposal.revision;
    status='APPROVED'; simulation_id=$simulation.simulation_id;
    reason='SYNTHETIC approve recommendation only, no execution'} | ConvertTo-Json
Invoke-RestMethod "$base/proposals/$id/reviews" -Method Post `
    -ContentType 'application/json' -Body $body
```

For a combined overlay, add `additional_proposal_ids` to the simulation body. To reject,
submit `status='REJECTED'` from UNDER_REVIEW. Proposal/Simulation timestamps are UTC; embedded
cloud properties retain the graph's integer epoch-microsecond representation.

## Reproducible demo

From the project root with the normal Compose stack running:

```powershell
docker compose exec -T backend python -m app.demo.remediation_proof
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-remediation.ps1
```

The proof seeds/replays A/B, then explicitly ingests the versioned `remediation-2026.1`
synthetic extension at 13:00: one additional normalized Allow permission and two source-linked
edges on the existing sensitive-read policy. The original baseline inventory is unchanged.
This fixture preparation is distinct from simulation. It requires baseline Scenario A
sequence 3 or the same previously ingested extension; use a fresh graph for an independent
demo rather than resetting other investigation histories.

The CLI explicitly records a synthetic reviewer approving one recommendation; the PowerShell
inspection script performs no approval. Actual observed results are in
[remediation_proof.json](remediation_proof.json): two potential paths become one with an
alternate, then zero under a combined exclusion. Maximum score remains 51 after the first
removal, correctly reflecting the remaining path. The original cloud projection hash stays
equal. Integration fixtures separately prove collateral impact on another identity and a
non-sensitive resource, plus evidence-qualified policy-exception/expired-access suggestions.

[remediation_example.json](remediation_example.json) is the complete visualization-ready saved
response. [remediation_restart_proof.json](remediation_restart_proof.json) records equality of
the approved proposal, review history and simulation after a real database restart and fresh
backend process. Generated JSON is documentation output; runtime never loads it as state.

## Executed graph diagnostics

Bind `$tenant_id`, `$space_id` and `$proposal_id` through official client parameter maps.
These trusted diagnostics supplement current membership checks performed by repositories.
They do not provide an arbitrary-Cypher HTTP interface.

```cypher
MATCH (p:RemediationProposal {tenant_id: $tenant_id, space_id: $space_id, id: $proposal_id})
RETURN p.id, p.status, p.category, p.revision, p.relationship_json, p.source_sha256
```

```cypher
MATCH path=(e:Evidence)-[:SUPPORTS]->(p:RemediationProposal {id: $proposal_id, space_id: $space_id})
WHERE all(n IN nodes(path) WHERE n.tenant_id = $tenant_id)
AND all(r IN relationships(path) WHERE r.tenant_id = $tenant_id)
RETURN e.id, e.content_sha256, e.source, e.source_id ORDER BY e.id
```

```cypher
MATCH path=(s:RemediationSimulation)-[:DERIVED_FROM]->
(p:RemediationProposal {id: $proposal_id, space_id: $space_id})
WHERE all(n IN nodes(path) WHERE n.tenant_id = $tenant_id AND n.space_id = $space_id)
AND all(r IN relationships(path) WHERE r.tenant_id = $tenant_id)
RETURN s.id, s.result_sha256, s.chunk_ids_json ORDER BY s.id
```

```cypher
MATCH path=(r:RemediationReview)-[:DERIVED_FROM]->
(p:RemediationProposal {id: $proposal_id, space_id: $space_id})
WHERE all(n IN nodes(path) WHERE n.tenant_id = $tenant_id AND n.space_id = $space_id)
AND all(e IN relationships(path) WHERE e.tenant_id = $tenant_id)
RETURN r.id, r.status, r.reviewer_id, r.audit_json ORDER BY r.created_at,r.id
```

The real traversal implementation is `repositories/risk.py::traversal_query()`, reused with
before/after exclusion views. [FalkorDB read-only queries](https://docs.falkordb.com/commands/graph.ro-query)
document the read-only command. [AWS policy evaluation logic](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html)
explains why deny and authorization controls cannot be treated like removable Allow grants.
The project implements only the subset documented in [risk_engine.md](risk_engine.md).

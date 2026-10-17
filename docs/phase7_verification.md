# Phase 7 verification

Verified on 16–17 October 2026 (Asia/Calcutta) using Windows PowerShell, Python 3.12.12,
official FalkorDB client 1.8.0, FalkorDB server 4.22.1 and LangGraph 1.2.15. UTC source
events remain fixed to the synthetic dataset's 16 October timestamps. Existing dependency
locks, connection management, risk traversal, memory ACLs and six-agent coordination were
reused. No new package, AWS account or LLM key is required.

## Automated checks

**346 tests passed, zero skipped**, in 280.93 seconds with real graph integration enabled:
**166 integration tests** and **180 other tests**. The earlier complete run passed 342 tests;
four additional regressions cover repeated temporal ownership handoffs, proof idempotence,
multiple workflows sharing one case, and repeated review/reopen cycles.

Ruff lint and formatting pass for 105 Python files. `pip check` reports no broken
requirements. All PowerShell scripts parse. `docker compose config --quiet` succeeds without
printing resolved credentials. Integration tests use unique fixture graphs on a separate,
nonpersistent real FalkorDB server; cleanup deletes only each fixture's exact graph.

Phase 7 tests cover:

- Automatic graph-derived case creation, all required case fields, six assigned agents,
  source-time events across 09:00/10:00/11:00/12:00 and graph visualization payloads.
- Shared-asset historical incident reconstruction, authorized episodic/procedural memories,
  validated read-only historical procedures and traceable summary IDs.
- All status vocabularies, valid transitions, stale/invalid writes, terminal closure,
  identical retry reconciliation and conflicting operation identity rejection.
- Explicit risk acceptance without an AWS correction claim; original hypotheses remain
  intact. Opposing evidence reviews retain source hashes and audit provenance.
- Current-path updates, new case episodes after closure, concurrent detection deduplication,
  and additional workflows linking to an existing case without repeating checks.
- Agent A completes two checks; a fresh graph connection / backend lifecycle lets Agent B
  complete two others. Completed task records, evidence and Decisions remain intact.
- Repeated handoffs preserve old ASSIGNED_TO intervals and exactly one active owner for
  each pending task. Reopened review cycles do not rerun completed checks.
- Tenant/agent denial, revoked membership, and revocation between validation and commit.
- Structured SOC, timeline, graph, activity, memory and evidence endpoints, input authority
  rejection, and production/demo-mode route gates.
- The complete synthetic proof, closure, reconnect equality and identical repeated execution.

Earlier event, IAM subset, memory security, workflow retry/lease, unavailable-database and
provider-boundary tests remain in the complete suite.

## Actual database and backend restart

The [generated restart proof](investigation_restart_proof.json) records a real persistent
FalkorDB shutdown/save and fresh backend process, with graph-backed automatic recovery.
The partial case had five workflow stages complete, Memory Keeper's two case checks complete,
and Response Architect's two checks pending. Startup recovery completed the final agent and
remaining checks without reseeding, replaying or making a resume request.

| Observation | Result |
| --- | --- |
| Before restart | INVESTIGATING, five workflow stages, two complete/two pending case checks |
| After startup recovery | AWAITING_REVIEW, six workflow stages, four complete case checks |
| Agent A completed task records | Decoded JSON equal before/after |
| Prior audit records | All 12 retained unchanged |
| Source evidence / handoff checkpoint | Preserved |
| Agent run attempt counts | `[1, 1, 1, 1, 1, 1]` |
| Historical incident / validated procedure | One each, graph retrieved |
| IAM/command actions executed | Zero |

Case: `7c1d2644-f8a6-5154-a388-2a63e0dbfc1c`.
Workflow: `4950e909-6870-5555-9473-cd76b630033a`.
Case handoff: `84cb59a7-fb9f-5dc8-9ee1-40c7686faf50`.

The [complete investigation proof](investigation_proof.json) then performed explicit synthetic
analyst review, accepted the modeled risk and closed the case. It did not assert remediation
or effective AWS access. It retained 11 completed tasks, zero pending tasks, 76 verified
evidence records (with verification scope), one contradictory observation, ten current memory
references, 22 audit entries and 66 timeline entries. The relevant grant-removal simulation
reported one potential path before and zero after; the actual graph was not changed by it.
Reconnecting and rerunning the proof returned the identical result.

The [complete saved API response](investigation_example.json) is an inspection example,
including source records and IDs. All generated JSON files are documentation outputs;
runtime code never imports them as state. FalkorDB remains authoritative.

The PowerShell HTTP demo inspected the closed case, evidence, timeline, graph, prior incident,
procedure, activity, memory and immutable handoff. It reported 158 graph nodes and 177
relationships. All five Cypher diagnostics in [investigations.md](investigations.md) were
executed against the case, returning respectively **1, 4, 76, 1 and 22 rows**. The queries use
bounded materialized paths and tenant predicates; the previously documented server query
shape limitation in [Phase 6](phase6_verification.md) still applies to new custom queries.

## Runtime and verification boundaries

The private verification graph is `shadowchain_phase7_verified` on authenticated loopback
port 16379. Official pinned-image binaries were run in a WSL chroot with AOF/RDB data bound
to the ignored `.verification/phase7-demo-data` directory. Earlier phase data directories
were preserved. A separate server on port 16380 disabled persistence for negative tests.
The backend serves [local API documentation](http://127.0.0.1:18000/docs); health and readiness
returned 200. The documented Compose deployment still publishes no FalkorDB ports.

Actual `docker compose --profile test build backend tests` was attempted and failed because
`dockerDesktopLinuxEngine` was absent. Therefore image builds, container recreation, Compose
health orchestration and the Compose-specific restart commands could not be verified here.
This was an engine availability failure; real graph and HTTP tests used the running pinned
FalkorDB binaries. Compose configuration validation passed.

Gemini live calls and AWS integration were not exercised. Mock mode made no LLM/AWS requests.
The normalized IAM subset produces **POTENTIAL ATTACK PATHS**, not proof of exploitable access.
Evidence integrity, analyst agreement and temporal association do not establish causality.
The separate earlier negative-fixture AOF recovery limitation documented in
[Phase 3](phase3_verification.md) remains unresolved; this clean persistent demo successfully
recovered. Abrupt power failure may lose the latest `everysec` writes; local persistence is
not a backup or high-availability guarantee.

Demo HTTP principals are fixed and routes disabled in production. Production authentication,
live cloud ingestion and deployment hardening remain future work. No frontend was built.

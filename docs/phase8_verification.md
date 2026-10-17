# Phase 8 verification

Verified 17 October 2026 (Asia/Calcutta) with Python 3.12.12, official FalkorDB client 1.8.0,
FalkorDB server 4.22.1, LangGraph 1.2.15 and the existing pinned dependency locks. No runtime
dependency or AWS/LLM credential was added. The implementation reuses the existing risk
engine, normalized IAM evaluator, graph constraints, current membership checks and logging.

## Automated results

**376 tests passed, zero skipped**, in **211.98 seconds** with real FalkorDB integration
enabled: 181 integration tests and 195 other tests. The earlier Phase 8 run passed 375 tests;
the final regression additionally covers recommendations for mixed Allow/Deny policies.
Ruff lint/formatting pass for 114 Python files. `pip check` reports no broken requirements.
PowerShell parsing and `docker compose config --quiet` pass. Tests use unique isolated graph
names and exact fixture cleanup on a separate nonpersistent real server.

New coverage proves:

- Original cloud nodes, permissions, temporal properties and access relationships retain the
  same complete projection hash after proposals, simulations and human approval metadata.
- Excluding one grant removes a path while an independent alternate path remains. Combining
  two compatible exclusions removes both paths without deleting either actual grant.
- Shared-policy impact includes another identity and a non-sensitive resource even when
  risk discovery was scoped to one identity. Legitimate business use remains explicitly unknown.
- Every recommendation references exact relationships/permissions and source evidence.
  All five categories are exercised with actual qualifying graph evidence, including expired
  justification and verified high-risk policy-exception fixtures.
- Deny/control removals are rejected, and an identity policy attachment with mixed Allow/Deny
  statements is not recommended for deletion. Valid Allow-grant recommendations remain usable.
- Identical/concurrent simulations reconcile into the same persisted result; reconnecting
  retrieves equal proposals/results. Changed request content under one identity returns 409.
- Explicit review cannot skip UNDER_REVIEW, approve without a saved simulation, or use an
  unprivileged reviewer. REJECTED blocks new simulations. Approval is recommendation-only.
- Changed source data blocks a new simulation of the stale proposal while old evidence and
  simulations remain retrievable. Hash checks reject a corrupted simulation payload chunk.
- Cross-tenant reads and revoked membership are denied, including revocation immediately
  before the committing write. Production/demo-mode endpoint gates remain enforced.
- Extra executor, AWS-action, reviewer, query and authority fields are rejected. Invalid
  identifiers and credential-like review text fail validation.
- A guarded fake AWS SDK rejects any client construction during proposal, simulation and
  approval tests; it receives no calls. The remediation service has no AWS execution path.

The existing event, policy, risk, memory, investigation and six-agent tests continue to pass.

## Real graph and HTTP demonstration

The [generated proof](remediation_proof.json) used graph `shadowchain_phase8_verified`.
Trusted setup explicitly replayed A/B and ingested the versioned Phase 8 synthetic alternate
Allow at 13:00. This input preparation precedes the source snapshot; it is separate from
the read-only simulator. Existing baseline inventory and earlier phase histories were preserved.

| Observed result | Value |
| --- | --- |
| Initial potential paths | 2 |
| After one grant exclusion | 1 |
| Alternative paths for affected identity/resource | 1 |
| After combined exclusions | 0 |
| Maximum score before / after first exclusion | 51 / 51 |
| Sum of path scores before / after first exclusion | 102 / 51 |
| Source projection hash before / after | Equal |
| Explicit recommendation review status | APPROVED |
| Approval scope / execution capability | recommendation_only / false |
| AWS write calls | 0 |

Proposal `be03680a-cb3e-5228-95d8-13dc3d60882c` and single simulation
`158dc7cb-015a-5681-97a7-ee5499b16e96` are saved in FalkorDB. Combined simulation
`d7578200-1641-5282-bfd9-74efa9fe4e51` shows that removing the alternate too breaks both paths.
The unchanged maximum score avoids claiming that removing one path eliminates the remaining
path's modeled risk. Sums remain heuristic path metrics, not risk probabilities.

The PowerShell HTTP demo generated recommendations, saved/retrieved a simulation and returned
44 visualization nodes, 63 relationships, four supporting evidence snapshots and one remaining
alternative path. It performed no approval. The trusted proof CLI separately recorded an
explicit synthetic recommendation-only approval. Both paths contain no AWS client/writer.

All four full Cypher diagnostics in [remediation.md](remediation.md) executed successfully,
returning **1 proposal, 4 evidence, 2 simulation and 2 review rows** for the proof proposal.
The partial exclusion snippet is part of the actual bounded traversal query, not an executable
standalone statement. The previous materialized-path query-shape caution documented in
[Phase 6](phase6_verification.md) continues to apply to new custom diagnostic queries.

## Restart recovery

The persistent FalkorDB process was shut down with save enabled and restarted against the
same data directory. A fresh backend process then retrieved the proposal and simulation
through HTTP without reseeding or recomputation. Both decoded JSON responses were equal to
the before-restart records, including the APPROVED state and two immutable review Decisions.
See [remediation_restart_proof.json](remediation_restart_proof.json).

[remediation_example.json](remediation_example.json) contains the full saved simulation,
before/after findings, risk delta, source evidence, uncertainty and visualization data.
Generated JSON files are documentation artifacts, never runtime sources of truth.

The API is running at [localhost:18000/docs](http://127.0.0.1:18000/docs); health and readiness
returned 200. Official pinned-image binaries run in the existing WSL chroot, with AOF/RDB
under the ignored `.verification/phase7-demo-data` directory, now retaining both Phase 7 and
Phase 8 verification graphs. The production-style Compose configuration still publishes no
FalkorDB ports; verification uses authenticated private loopback port 16379. Negative graph
fixtures use separate port 16380 with persistence disabled.

## Unverified and bounded behavior

An actual `docker compose --profile test build backend tests` attempt failed because the
Docker Desktop Linux engine named pipe was absent. Image builds, container recreation and
Compose-specific networking/orchestration remain unverified on this workstation. Compose
configuration parses, and real SDK, graph, HTTP, persistence and recovery checks ran against
the pinned FalkorDB binaries. No Docker data cleanup or deletion of earlier histories occurred.

There was no live AWS account or Gemini call. These are deterministic normalized graph
simulations, not AWS effective-permission or operational-outage certification. Impact is
bounded by action/time/scenario/depth and modeled data; real usage/necessity is unknown.
Hashes establish record consistency, not truth of a source claim or protection against an
attacker controlling the database. Human reviewer identity is synthetic/fixed in the local
demo; production authentication and human/agent credential separation remain future work.

The earlier negative-fixture AOF limitation in [Phase 3](phase3_verification.md) remains
unresolved and its data is preserved. This clean persistent graph recovered successfully.
`everysec` persistence can lose recent writes after abrupt power loss; this restart check
does not certify backups or high availability. No frontend was built.

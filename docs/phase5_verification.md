# Phase 5 verification

Verified on 16 October 2026 using Windows PowerShell, Python 3.12.12, the pinned Python
dependency locks, official FalkorDB Python client 1.8.0 and FalkorDB server 4.22.1.
The existing backend, event engine and risk engine were reused.

## Checks performed

- **270 tests passed, 0 skipped**, with real graph integration enabled: **129 integration**
  tests and 141 other tests. Final run completed in 78.15 seconds.
- Ruff lint and formatting passed for all 82 Python files. `pip check` found no broken
  requirements. No dependency changes were needed.
- Parameterized memory retrieval, source traversal, membership/context grants, review,
  correction, contradictions and handoff operations ran against real FalkorDB.
- Separate FastAPI application lifecycles/connection pools retrieved identical saved memory
  and evidence. A further live test stopped both Uvicorn and the persistent FalkorDB process,
  restarted them and compared complete HTTP responses without reseeding or replaying.
- Health and readiness returned 200, and context, memory history, contradiction history and
  saved handoff responses were exactly equal before/after the live process restart.
- The repeatable memory proof ran twice with identical output. All three memory types,
  a failed approach, evidence, a content correction and a handoff remain in FalkorDB.
- The Windows PowerShell demo script passed against the live API. Every documented memory
  Cypher query executed successfully: 1 episodic, 3 semantic and 2 procedural current records,
  bounded related-entity retrieval, correction lineage, contradiction provenance and procedure
  review diagnostics. `docker compose config --quiet` passed.

Integration coverage includes shared evidence between authorized agents, private memories,
cross-tenant and cross-space denial, revoked membership at historical timestamps, source
revocation/deletion, two-hop graph retrieval requiring grants for intermediates, overlapping
and adjacent validity intervals, filtered retrieval that cannot hide a visible conflict,
concurrent writes, atomic conflict detection, lost-acknowledgement reconciliation, stale
corrections, privileged-step rejection and untrusted prompt-like content remaining data.
Recipient-private conflicting claims do not leak through sender handoff previews. A source
grant revoked between validation and commit blocks the write.

## Saved synthetic demonstration

The generated [memory proof](memory_proof.json) records:

| Result | Observed |
| --- | --- |
| Types retained | episodic, semantic, procedural |
| Current memories / all versions | 6 / 7 |
| Verified conflict-free semantic observations | 2 |
| Verified read-only successful procedure | 1 |
| Failed approach retained | yes |
| Historical contradiction retained after correction | yes, no longer current |
| Handoff memory pointers | 6 |
| Privileged actions / executable source content | 0 / false |
| Reconnect and complete process restart recovery | passed |

Memory space: `be92d29c-9885-5cce-aff1-62041e4cbf73`.
Superseded observation: `dffd3c3e-830c-52bc-a460-4f6e90ccfea5`.
Replacement: `3c6ad635-dd49-54e3-9f66-e3bea9742106`.
Handoff: `7770f868-5822-578a-b977-5092a3b12101`.

Verification used the separate graph `shadowchain_phase5_verified` on the existing private,
authenticated loopback server at port 16379. Its AOF/RDB directory remains the ignored
`.verification/phase4-demo-data`, preserving earlier graphs alongside Phase 5. Test fixtures
used unique graphs on a separate nonpersistent server at port 16380. Tests never used the
persistent demo graph. Local JSON oracles/proofs are verification output only; application
code never loads them as authoritative memory. The backend remains available locally at
[localhost:18000/docs](http://127.0.0.1:18000/docs).

## Environment and scope limits

`docker compose config --quiet` passed. An actual `docker compose build backend tests`
attempt failed because Docker Desktop's Linux-engine named pipe was unavailable. Image
builds, container recreation and Compose networking/volume behavior could not be verified.
Real server verification used the official image binaries already downloaded and hash-checked
in earlier phases, running in isolated WSL root filesystems with authentication and loopback
binding. No Docker system cleanup was attempted. This verifies graph semantics and recovery,
but does not establish a successful Docker build.

The earlier negative-fixture AOF recovery issue documented in [Phase 3](phase3_verification.md)
remains unresolved and its files remain preserved. Those fixtures use the isolated
nonpersistent test server. Phase 5 persistent demo-only graph recovery succeeded.
The live restart was graceful; AOF `everysec` can lose approximately the last second of writes
on abrupt host failure. No abrupt power-loss or high-availability claim is made.

HTTP routes bind a fixed synthetic Scout principal and are disabled in production; production
authentication remains future work. Administrative source grants and curator reviews are
trusted core/CLI operations. Memory content cannot invoke privileged actions in this phase;
a future agent executor must independently authorize its tools. No LLM, AWS API, frontend,
autonomous runner or LangGraph checkpoint database was introduced.

See [memory.md](memory.md) for the access contract, actual Cypher, demonstration commands and
structured-conflict limitations. [Phase 4 results](phase4_verification.md) remain the historical
record of the earlier risk-engine verification; this report supersedes its current test count.

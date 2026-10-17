# Phase 6 verification

Verified on 16 October 2026 with Windows PowerShell, Python 3.12.12, LangGraph 1.2.15,
official FalkorDB Python client 1.8.0 and FalkorDB server 4.22.1. Existing dependency locks,
connection handling, temporal event engine, risk engine and MemoryService were reused.
No new package or credential is needed for deterministic agent mode.

## Automated checks

**314 tests passed, 0 skipped** with real graph integration enabled: **149 integration tests**
and 165 other tests. The final full-suite run took 146.46 seconds. Ruff lint and formatting
passed for all 95 Python files. `pip check` found no broken requirements. Compose configuration
parsed successfully. Tests used unique fixture graphs on a separate nonpersistent real server.

Coverage includes:

- Completion of all six agents on Scenario A, including a real graph-derived potential
  finding, evidence, cross-agent memory retrieval, six handoffs and read-only remediation.
- Completion on Scenarios B and C; Memory Keeper detecting authorized conflicting structured
  claims from different agents while preserving their provenance.
- Six unique dependency-ordered step tasks, Coordinator-created review tasks, concurrent
  admission, duplicate trigger/resume, conflicting request-ID rejection and no duplicate tasks.
- Prepared-output recovery using a fresh database pool without repeating its model call,
  duplicate memory or handoff. Lost completion acknowledgements resume at the next step.
- Current/expired leases, stale-worker write rejection, persistent attempt budgets and
  exhausted hard-crash leases becoming blocked rather than continuously rerunning agents.
- A new FastAPI lifecycle automatically recovering an incomplete investigation without an
  explicit resume request; completed earlier run records remain unchanged.
- Cross-tenant denial and demo/production API gates. Caller headers cannot select an agent
  or tenant; extra authority/executor request fields are rejected.
- Mock mode without credentials; Gemini HTTPS request shape, prompt allowlist, citations,
  JSON schema and bounded timeout/retry configuration using controlled responses.
- Rejection of unknown citations, tool calls, privileged fields, invalid confidence and
  credential-like model text. Valid command-like prose remains an annotation and does not
  alter permissions, findings or executable actions.

## Real demonstration and restart

The [generated agent proof](agent_proof.json) was produced against actual FalkorDB and rerun
with identical results:

| Result | Observed |
| --- | --- |
| Agent order | Cloud Sentinel → Identity Guardian → Risk Oracle → Memory Keeper → Coordinator → Response Architect |
| Completed agent runs / step tasks | 6 / 6 |
| Graph-derived potential finding | 1 |
| Shared evidence-backed memories / handoffs | 6 / 6 |
| Finding-specific tasks awaiting review | 1 |
| Simulated policy-attachment removal | 1 modeled path before, 0 after |
| IAM/command actions executed | 0 |
| Default checkpointer | none; graph reconstruction |
| Duplicate trigger and reconnect | unchanged persisted result |

Completed proof investigation: `95d5fd31-aadb-5254-9478-9f254d6de1ba`.
Source event: `2400a2bf-5fc3-5392-b4f4-cf30eed1dab4`.
Potential finding: `b43ed57a-1330-5703-89e2-19530806bd3b`.

For a separate live recovery test, investigation `ef5d74d7-2a19-5f28-a9dd-fce3c22847db`
was saved after Cloud Sentinel and Identity Guardian completed. Both Uvicorn and the real
persistent FalkorDB process were stopped. After database recovery and backend startup, the
worker read the graph cursor and automatically completed the remaining four agents. No seed,
replay or resume request was used to recover it. The first two complete run DTOs were exactly
unchanged, and every run's attempt count remained one. All six memories, six handoffs and seven
tasks were recovered. The actual graph still had its potential path after the what-if proposal.
See the [generated restart proof](agent_restart_proof.json).

The Windows PowerShell demo script also completed through HTTP, displayed the six runs and
proposal, retrieved graph memory/handoffs and verified that a duplicate trigger reused tasks.
Health and readiness returned 200. All documented agent Cypher diagnostics were executed
against the saved graph. The local API is available at
[localhost:18000/docs](http://127.0.0.1:18000/docs).

The saved-data diagnostics use materialized paths with predicates on all path nodes/edges.
An earlier diagnostic shape with separate edge-property filters returned unexpected repeated
rows on this server. The documented shape was verified with exact expected counts: six step
owners, fifteen run-memory-source rows, three finding/run links and six handoff/run links.
This does not establish correctness of arbitrary custom Cypher; new query shapes need real
server tests, particularly when relating evidence or crossing tenant boundaries.

The verification graph is `shadowchain_phase6_verified` on the private authenticated loopback
server at port 16379. Earlier phase graphs remain preserved. The server's existing AOF/RDB
directory is the ignored `.verification/phase4-demo-data`. Graph state lives in FalkorDB;
generated JSON proofs and ignored comparison oracles are verification outputs that runtime
services never load. The isolated negative-test server used port 16380 and no persistence.

## Limits of this verification

An actual `docker compose build backend tests` attempt failed because Docker Desktop's Linux
engine named pipe was unavailable. `docker compose config --quiet` passed, but image builds,
container recreation, private Compose networking and named-volume behavior remain unverified.
Real graph and recovery tests used official image binaries downloaded and hash-checked in
earlier phases, running in isolated WSL root filesystems. No Docker cleanup or data deletion
was attempted.

The earlier negative-fixture AOF issue documented in [Phase 3](phase3_verification.md) remains
unresolved and its files were preserved. Negative fixtures remain isolated from the persistent
demo server. Phase 6 demo graph recovery succeeded. Restart was graceful; AOF `everysec` can
lose roughly the most recent second of writes on abrupt host failure. This is not a
power-loss, backup or high-availability certification.

Live Gemini was not called without an API key. Its structured protocol/error handling was
tested with controlled responses. Mock mode completed the real graph workflow without an
AWS account or LLM key. Production identity/authentication, live event dispatch, behavioral
telemetry, full AWS authorization evaluation, dynamic scheduling and privileged execution
remain outside this phase. No frontend was implemented.

See [agents.md](agents.md) for the actual contracts, configuration, API and Cypher. Earlier
[Phase 5 results](phase5_verification.md) remain a historical record; this report supersedes
its current test count.

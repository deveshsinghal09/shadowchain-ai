# Phase 11 verification

Verified 17 October 2026 on Windows with Python 3.12.12 and a local WSL FalkorDB server.
The server reports graph module `42201` (4.22.1). Installed libraries: official FalkorDB
client 1.8.0, FastAPI 0.143.2, Pydantic 2.14.1, LangGraph 1.2.15, HTTPX 0.28.1 and
Pytest 9.1.1. Existing pinned dependencies were sufficient; `pip check` passes.

## Reproduced demonstration

The final [recorded evaluation](hackathon_evaluation.json) was produced by:

```powershell
$env:FALKORDB_HOST = '127.0.0.1'
$env:FALKORDB_PORT = '16379'
.\scripts\demo-hackathon.ps1 -OutputDirectory artifacts/hackathon/phase11-complete -Repetitions 20
```

The output directory was fresh. Reproduction requires a different output directory;
graph names are generated uniquely. The retained graph is
`shadowchain_eval_b5966a5ec8d446f19c0f79c6554c7756`, measured at
`2026-10-17T05:42:20.331506+00:00`. Source fixture manifest SHA-256:
`add52bd64aee09fb8c12daa7cc1e45d43a1f9fe90ce213f2a877559d60d462c7`.
AWS/CloudTrail were disabled, export local, provider mock, and the Gemini key cleared.

| Check | Actual observed result |
| --- | --- |
| Four timestamp path counts | 0 / 0 / 0 / 1 |
| Active cloud relationships at 09/10/11/12 | 123 / 126 / 129 / 130 |
| Replayed infrastructure changes | Three, linked to relationship provenance |
| First modeled sensitive reachability | 12:00 UTC, 16 October 2026 |
| Backend process restart | Server PID 21568 exited; server PID 39424 reconstructed and completed work |
| Durable workflow cursor | Three completed stages before restart, six after startup recovery |
| Completed runs preserved | First three records unchanged, each stage attempted once |
| Internal handoffs consumed | 5 / 5; sixth published handoff awaits human recommendation review |
| Workflow tasks | Six completed stage tasks; seven unique tasks including pending review |
| Shared finding | Risk Oracle memory retrieved by Memory Keeper with evidence and prior reasoning |
| Conflicts | Both observations/evidence retained; zero definitive facts, verification required |
| Source observation timestamps | Earlier evidence at 10:00, disputing evidence at 11:00 |
| What-if reachability / maximum heuristic score | 1 path / 51 points → 0 paths / 0 points |
| Duplicate trigger and explicit resume | Final durable records unchanged |
| Controlled shared-observation ablation | Persistent readers: 1 → 1; ephemeral processes: 1 → 0 |
| Dependency refusal | Four core graph operations returned `dependency_unavailable` |

The source graph SHA-256 before and after two repeated simulations was identical:
`1022f4528d97c04b7f83e412b561f5026f78a2c0038543cc4e5a4fd9bb791214`.
No AWS operation was used. This proof concerns potential paths in the normalized IAM
subset, not proven effective AWS authorization or verified real-world causality.

## Actual latency measurements

One unmeasured warmup and 20 measured sequential invocations per operation, concurrency
one. No regression suite ran concurrently with this recorded measurement. Query counts
reflect multiple server round trips inside memory and workflow reconstruction.

| Probe | Query samples | Query median / p95 (ms) | Operation median / p95 (ms) |
| --- | ---: | ---: | ---: |
| Bounded historical traversal | 20 | 101.338 / 134.691 | 101.692 / 134.969 |
| Memory ACL/provenance retrieval | 60 | 8.007 / 24.508 | 28.129 / 41.941 |
| Workflow reconstruction | 280 | 0.910 / 1.638 | 14.114 / 19.141 |

Raw samples, min/max, sample counts and nearest-rank p95 are in the JSON report.
These are warmed, local client/SDK/network/server timings on the small synthetic graph.
They do not establish a production SLO or a performance improvement. The retention
ablation compares persistence availability only; no latency claim compares RAM to FalkorDB.

## Verification scope and limitations

The full regression suite passes **453 tests, zero skipped**, including **199 real FalkorDB integration tests and 254 other tests**. JUnit reports **331.810 seconds** elapsed.

The [test report](phase11_test_report.json) records every test result and duration. The new
tests exercise all five demos with two actual FastAPI processes, fresh-process readers,
the ephemeral baseline, fail-closed dependency unavailability, quota-safe private credentials,
preserved tasks, and graph-backed report recovery after deleting the local export.
Existing integration fixtures isolate graphs with unique names and exact cleanup.

The restart receipt separates Uvicorn server PIDs from Windows virtual-environment launcher
PIDs. An exclusive loopback socket ownership check verifies the first listener was released;
a real-socket regression test rejects a still-active listener. A closed Windows loopback
port can time out rather than return immediate refusal, so a SYN timeout alone is not
treated as proof that a server exited. The deliberately unavailable dependency probes
record the actual transport failure type separately from the public dependency error.

All documented manual [API requests](HACKATHON_API_REQUESTS.md) succeeded against the
running authenticated backend, including four historical discoveries, evidence/path
projection, timeline, non-destructive what-if, six-agent investigation, shared memory,
handoffs, case detail and dashboard. The [sample investigation](hackathon_investigation_example.json)
is an actual authenticated HTTP response from the final evaluator run.

Ruff lint/format checks pass for 145 Python files. All 14 PowerShell scripts parse.
`docker compose --profile evaluation config --quiet` passes. An actual
`docker compose --profile evaluation build evaluation` attempt failed because the Docker
Desktop Linux engine named pipe is unavailable. Image build, new Compose profile runtime
and container recreation remain **unverified** on this workstation. Native Python plus
the pinned FalkorDB server completed the demonstration.

The runner restarts its own FastAPI process, not the database host. Database persistence
and prior restart tests are documented in earlier phase reports; this is not a new
power-loss or backup/restore test. Dependency-off checks use a non-listening loopback port,
leaving the shared database untouched. API liveness remains independent; graph operations
have no substitute persistent source of truth. No live AWS check or frontend was added.

# Evaluation protocol, version evaluation-2026.1

The runner checks a controlled synthetic fixture, not real AWS detections. Inputs come
from `synthetic-2026.1`; the report includes the manifest SHA-256. Stable domain IDs,
source timestamps and logical outcomes are deterministic. Per-observation analysis/finding
IDs can reflect differing ingestion metadata; cloud entity/relationship IDs remain stable.
Wall-clock timings, ingestion timestamps, process IDs and isolated graph names vary.
They are measured, not normalized
into invented benchmark results.

## Correctness measures

| Measure | Check and denominator |
| --- | --- |
| Path detection correctness | Four expected path counts at 09/10/11/12 UTC: `0/0/0/1` |
| Historical reconstruction | Four distinct source-time graph hashes; saved analyses reconstruct the same four counts after restart |
| Successful memory retrieval | Risk Oracle → Memory Keeper finding retrieval, plus the independent shared-observation retention check |
| Handoff completion | Five internal transfers: sender memory is included in the stored handoff and retrieved by its intended next agent |
| Restart recovery | One actual FastAPI process restart: graph cursor advances from three to six without a resume request |
| Duplicate task prevention | Duplicate trigger and explicit resume both preserve final records; seven unique tasks, each stage attempted once |

The sixth published handoff is to a human recommendation review and is not counted as
completed. There is no automatic AWS remediation. These denominators describe fixture
assertions; they do not estimate precision/recall or operational incident-response rates.
The evaluator raises on any failed assertion and exits nonzero; it never emits a passing
final report for a partially successful run.

The historical evidence chain must include linked relationship changes at 10/11/12, with
first sampled reachability at 12:00. Temporal coincidence without modeled edge provenance
does not pass. Read-only simulation runs twice and compares the complete source dataset
before/after, as well as the path and score results. Separate existing tests cover explicit
denies, modeled constraints, half-open interval boundaries, late events, alternate paths,
tenant isolation, revoked access, prepared-run failure recovery and journal reconnection.

## Controlled persistence ablation

The `ablation.input` object is the exact synthetic MemoryWrite and MemoryQuery supplied
to both arms. The SHA-256 identifies that input, including its subjects, evidence IDs,
source provenance and observation text. The first agent's observation is identical.

| Arm | Before reader-process restart | After reader-process restart | Storage |
| --- | --- | --- | --- |
| Persistent graph memory | Authorized Agent B lookup through FalkorDB | New reader process repeats lookup through FalkorDB | Existing graph memory, ACL and provenance traversal |
| Ephemeral negative control | One process receives write + lookup | A second process receives only the same lookup | One temporary Python list in the first process; nothing persisted |

The persistent readers are separate OS processes using fresh connection pools. The
ephemeral baseline imports only Python standard-library modules, opens no files, connects
to no database, holds no graph and implements no graph traversal. It cannot secretly
recover the observation from the input because the second invocation is given **only the
query**. It is evaluation code, never a runtime application fallback.

This is intentionally a memory retention ablation. The ephemeral arm does not implement
graph ACLs, evidence validation, policy analysis or durable coordination. Those capabilities
are unavailable, not assigned zero risk or fabricated accuracy scores. It establishes why
shared evidence survives with persistent graph storage; it makes no claim that FalkorDB
is faster than RAM or superior to every other persistent database. No alternative database
is used in the experiment or application.

## What removing FalkorDB disables

The runner reserves a non-listening loopback port and points a separate connection manager
at it. Four real service operations must raise `DatabaseUnavailable` with code
`dependency_unavailable`: readiness, path discovery, memory retrieval and incomplete-run
reconstruction. This is a controlled **unavailable loopback dependency test**. It does not
stop the shared database, claim to emulate host corruption or measure outage recovery time.

Without FalkorDB, temporal graph analysis, incident reconstruction, evidence traversal,
persistent shared memory/conflicts, task ownership/deduplication, handoffs, workflow recovery,
review audits, sequence replay and graph-backed quotas are unavailable. HTTP liveness can
still work; readiness fails and protected graph operations fail closed. No alternate graph,
local JSON report, LangGraph checkpoint or RAM state serves requests in its place.

## Query latency methodology

Three fixed probes execute against the retained graph: bounded historical risk traversal,
memory retrieval with current ACL/provenance, and durable workflow reconstruction. Each
has one unmeasured warmup and 20 sequential measured invocations by default (allowed 5–100).
The report includes every raw sample, count, min, median, nearest-rank p95 and maximum.

An evaluation-only GraphDatabase subclass times `super().query(...)` with
`perf_counter_ns`. Every timed query executes the official SDK against FalkorDB. Query
timings include network, SDK, server work and decoding. Operation timings additionally
include Python reconstruction and multiple query round trips where needed. A new pool is
used; warmup is excluded. No query text, parameters, secrets or credentials are logged by
the measurement adapter. Failed operations abort the evaluation rather than being omitted
as successful latency samples.

These are single-client local measurements on a tiny, warmed synthetic graph. They are
not server-only execution latency, a production SLO, a load test or evidence of a performance
improvement. Hardware contention, VM scheduling and runtime versions affect them. The
[recorded result](hackathon_evaluation.json) and [verification notes](phase11_verification.md)
give the actual environment and samples. Re-run to obtain measurements on another machine.

## Automated verification

Existing graph fixtures create a unique `test_shadowchain_<uuid>` graph per test and delete
only that graph. The new end-to-end integration test launches two actual FastAPI processes,
checks all five demos and the ablation, then deletes its report export and recovers the
report from FalkorDB with a fresh connection. No test swaps the real graph for a dictionary
in the integration suite. The test suite also exercises bounded report chunk storage.

Run all backend tests with a configured dedicated test FalkorDB:

```powershell
$env:RUN_INTEGRATION_TESTS = 'true'
$env:AWS_ENABLED = 'false'
$env:CLOUDTRAIL_ENABLED = 'false'
$env:EXPORT_MODE = 'local'
$env:AWS_EC2_METADATA_DISABLED = 'true'
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q --tb=short `
    --show-capture=no --junitxml=artifacts/hackathon/tests.xml
```

Use `FALKORDB_HOST`, `FALKORDB_PORT` and the existing secret settings for a reachable test
server. Tests never use FLUSHDB/FLUSHALL and do not require an AWS profile or LLM key.
The default integration opt-in remains explicit; omitting it skips database tests and
does not prove the end-to-end demonstration.

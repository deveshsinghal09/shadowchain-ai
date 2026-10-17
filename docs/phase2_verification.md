# Phase 2 verification

Historical report: current Phase 3 tests and persistence results are recorded in
[phase3_verification.md](phase3_verification.md), including a later failure recovering an
older test-history AOF and the isolation of negative tests from the persistent demo server.

Date: 16 October 2026, Asia/Calcutta.

The existing backend, connection pool, FastAPI factory, routes, settings, error handling and
foundation repository were inspected and extended. The dependency locks were retained;
no database substitute or frontend was introduced.

## Actual environment

- Python 3.12.12, official FalkorDB Python client 1.8.0 and the existing locked dependencies.
- Actual FalkorDB server/module **4.22.1**, Redis server 8.10.2.
- Docker Desktop still cannot start its engine because of its stale Windows runtime socket
  failure. No further cleanup, reset or modification of Docker's installation was attempted.
- WSL itself runs. Official server image layers were downloaded from Docker Hub and checked
  against the pinned manifest/layer SHA-256 digests. The server binaries/module were extracted
  into an isolated temporary WSL root filesystem and run directly, without a Docker daemon.
- The test server bound only to `127.0.0.1:16379`, with password authentication, AOF,
  `appendfsync everysec`, periodic snapshots and `noeviction`. Its data directory was mounted
  from the ignored project `.verification/data` directory, outside the temporary root filesystem.
- The generated local `.env` DB password was reused without displaying it. No AWS account,
  AWS credentials or Gemini key was involved. All created domain records were synthetic.

This tests real FalkorDB behavior; it does **not** claim the Docker images were built/run by
Docker or that the production Compose volume/network configuration was exercised.

## Automated results

```text
109 passed in 9.07s
48 graph/API integration tests; 61 unit/validation/failure tests
0 skipped, 0 failed
```

Command, from `backend` while the isolated test server was running:

```powershell
$env:FALKORDB_HOST = '127.0.0.1'
$env:FALKORDB_PORT = '16379'
$env:RUN_INTEGRATION_TESTS = 'true'
.\.venv\Scripts\python.exe -m pytest -q --tb=short
```

The integration fixtures generated a fresh `test_shadowchain_<uuid>` graph for each test
and deleted only its own exact graph in teardown. They never called FLUSHDB/FLUSHALL or
deleted application graph data. Assertions ran through actual official-client queries.

| Required behavior | Verified evidence |
| --- | --- |
| Persist nodes and relationships | Every one of the 17 node labels and 17 relationship types created, reread and replayed |
| Prevent duplicate events | 16 concurrent ingestions through 8 threads yield one stored event and stable timestamps; conflicting content rejected |
| Preserve temporal history | Closing/replaying access edges; before/start/end boundary assertions; adjacent versions do not overlap at the boundary |
| Recover after reconnect | Same manager `reconnect()` and a new manager both recover existing records/edges |
| Enforce tenant isolation | Foreign node reads, lists, history, endpoint writes, source-event references and closures rejected/hidden |
| Clear unavailable behavior | Real reserved/non-listening loopback socket causes explicit DatabaseUnavailable errors, with no storage fallback |
| Database uniqueness | Real constraint violations, including concurrent relationship creation, reconciled without duplicates |
| Atomic rollback | Second CREATE violates a uniqueness constraint; earlier CREATE in the same statement is absent afterward |
| Fail on invalid schema data | Pre-existing incomplete Entity causes schema verification failure; invalid data is preserved for investigation |
| Prevent Cypher injection | Malicious text survives as a literal property and does not execute; label/type injection rejected |
| Preserve evidence provenance | Collector/source/source URI/content digest and event references persisted and reread |
| Safe retry handling | Bounded transient retries; no retry on authentication/syntax failures; uncertain non-idempotent writes fail explicitly |

Additional checks passed:

- `ruff check app tests` and `ruff format --check app tests` (46 Python files).
- `python -m pip check`: no broken requirements.
- PowerShell parser validation for all project `.ps1` scripts.
- `docker compose config --quiet`: valid Compose configuration.
- `python -m app.db.initialize --verify-only`: schema version 2 verified on the real graph.
- Published Cypher read examples executed successfully against the real graph.
- Ignoring of `.env` and verification credentials/artifacts checked; `.env.example` remains trackable.

## Real restart recovery

Beyond reconnect tests, a persistent synthetic probe was seeded in
`test_shadowchain_restart_probe` using `python -m app.demo.verify_graph seed`.
It included Identity, Role, SecurityEvent and a temporal CAN_ASSUME edge with provenance.

The isolated server was shut down with `SHUTDOWN SAVE`, restarted with the same persisted
data directory, then queried with `python -m app.demo.verify_graph read`. The complete JSON
snapshots matched exactly, including stable IDs, timestamps, provenance and relationship
properties. The read mode does not recreate missing domain records.

The restarted server log confirmed loading its AOF base and incremental files before
accepting connections. This establishes graceful server restart recovery, not crash/power-loss
durability or high availability.

Temporary backend and verification database processes were stopped after checking. Ignored
verification data/snapshots were retained; no Docker runtime files were changed.

## Live backend checks

The real backend ran on `127.0.0.1:18000` against this FalkorDB instance. Running the existing
smoke script produced:

```text
health 200: status=ok, demo_mode=true
ready 200: status=ready, dependencies.falkordb=ok
```

Readiness now executes Cypher, completes schema setup if necessary, and verifies live
index/constraint state. Unit/API tests also cover a 503 during dependency outage and
recovery, without losing liveness.

## Remaining operational verification

Docker engine startup is still outside the working environment. Consequently, backend/test
image builds, Compose container startup, its private network and **container recreation**
against the named volume remain unverified. These are the remaining foundation deployment
checks; the graph repository tests and real server restart checks above passed.

When Docker Desktop works, run from the project directory:

```powershell
docker compose build backend
docker compose up -d --wait --wait-timeout 120
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\smoke.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\init-graph.ps1 -VerifyOnly
docker compose --profile test run --build --rm tests
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-persistence.ps1
```

The updated persistence script compares the foundation marker and full synthetic domain
snapshot across container recreation. No frontend, cloud ingestion, IAM evaluator or agent
workflow was implemented in Phase 2.

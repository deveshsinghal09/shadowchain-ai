# Local backend development

These detailed setup and verification instructions were retained from the original README. Historical test results describe the recorded local verification runs. For current hosted behavior and integration limits, see [Railway/Vercel deployment](RAILWAY_VERCEL_DEPLOYMENT.md).

## Windows PowerShell quick start

Prerequisites: Docker Desktop with Linux containers and WSL2. Python 3.12 is required for
host-side development; Docker includes Python 3.12 and needs no host Python installation.
Commands below run from the `shadowchain-ai` directory.

```powershell
Set-Location 'E:\OneDrive\Documents\ChatGPT\Graph Hack\shadowchain-ai'
docker desktop start

# Creates .env with a random local DB password; preserves any existing .env.
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -SkipPython

docker compose config --quiet
docker compose build backend
docker compose up -d --wait --wait-timeout 120
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\smoke.ps1

Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ready
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-scenario-a.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-risk.ps1
docker compose exec -T backend python -m app.demo.memory_proof
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-memory.ps1
docker compose exec -T backend python -m app.demo.agent_proof
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-agents.ps1

docker compose exec -T backend python -m app.demo.investigation_proof
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-investigation.ps1

docker compose exec -T backend python -m app.demo.remediation_proof
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-remediation.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-export.ps1
```

The API is bound to loopback on port 8000. Development API documentation is at
[localhost:8000/docs](http://127.0.0.1:8000/docs). Change `BACKEND_PORT` in `.env` if needed.
FalkorDB has **no published host ports** or browser service; only the backend and test
containers can reach it on the internal graph network.

Only health/readiness are public. Setup generates a scoped API key in a private directory
under LOCALAPPDATA and writes only its digest to `.env`. Existing installations should run
`scripts/setup-api-auth.ps1`, then restart the backend. Demo scripts load the private key
automatically. For manual protected requests, dot-source `scripts/api-client.ps1`, obtain
`$headers = Get-ShadowChainHeaders` and pass `-Headers $headers`. Empty `API_CLIENTS=[]`
fails closed; no built-in credential is accepted. See [authentication and requests](API_CONTRACT.md).

`DEMO_MODE=true`, `AGENT_PROVIDER=mock` and `AWS_ENABLED=false` are the defaults and need only
FalkorDB. The default workflow makes no AWS or Gemini requests. Optional Gemini configuration is documented in the
[agent guide](agents.md); keys belong only in environment settings. Setting
`DEMO_MODE=false` does not enable AWS or production-authenticated agent APIs.
AWS synchronization requires separate environment opt-in and an explicit account-confirmed
request. See the [AWS setup guide](AWS_SETUP.md) for profile configuration, exact permission
templates, coverage limitations and disabling instructions. No live AWS check runs at startup.

## Host-side Python and tests

Install Python 3.12 if it is missing, then run:

```powershell
py -3.12 --version
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
Set-Location .\backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m ruff format --check app tests
Set-Location ..
```

Alternatively, `uv python install 3.12` and `uv venv --python 3.12 backend/.venv` can create
an isolated interpreter without changing your default system Python. Install the lock with
`uv pip sync backend/requirements-dev.txt --python backend/.venv/Scripts/python.exe`.
The scripts use the virtual environment directly; activation is unnecessary.

Unit tests replace the database dependency only inside test fixtures. Integration tests are
skipped on the host by default; run them against the dedicated real FalkorDB test server.
Its recovery log is isolated from the persistent demo database:

```powershell
docker compose --profile test build tests
docker compose --profile test run --rm tests
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\init-graph.ps1 -VerifyOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-persistence.ps1
```

The persistence script checks the foundation timestamp and explicitly seeds synthetic
records in tenant `shadowchain-verification`. It recreates this project's backend/FalkorDB
containers, then compares the persisted nodes, event provenance and temporal relationship.
It never deletes the volume. See [verification results](phase10_verification.md) for checks
actually performed on this workstation.

## API behavior

| Endpoint             | Success                                                  | Dependency outage        |
| -------------------- | -------------------------------------------------------- | ------------------------ |
| `GET /api/v1/health` | 200, process alive, UTC timestamp, version and demo flag | Remains 200              |
| `GET /api/v1/ready`  | 200, real FalkorDB Cypher query succeeds                 | 503 and `Retry-After: 3` |

Startup initializes and verifies the schema before writing the foundation marker. Readiness
checks graph connectivity and completes schema initialization if startup encountered an
outage. The foundation marker is written on application startup. If startup occurs during an
outage, restart the backend after recovery to initialize that marker before running the
persistence verification script. No readiness result is cached as authoritative state.

Application errors have a UTC timestamp and correlation ID:

```json
{
  "error": {
    "code": "dependency_unavailable",
    "message": "FalkorDB is unavailable",
    "details": []
  },
  "request_id": "b966ae7b-6e13-4d68-8233-d977ca2ff4e2",
  "timestamp": "2026-10-16T00:00:00Z"
}
```

`X-Request-ID` accepts valid UUIDs; other input is replaced with a generated UUID. Structured
request logs include route templates, duration and status, excluding bodies, query strings,
authorization headers and raw exception details. Configure `LOG_LEVEL` and CORS JSON origin
lists in `.env`. API docs are disabled when `APP_ENV=production`.

## Layout

```text
shadowchain-ai/
  backend/
    app/
      api/v1/          # Versioned operations routes
      core/            # Settings, UTC clock, logging, errors, middleware
      db/              # Connection/retry layer, Cypher utilities, schema and initializer CLI
      models/          # Persistent record types
      repositories/    # Tenant-scoped, parameterized graph record and history access
      services/        # Events, temporal risk, persistent memory, workflows and SOC cases
      agents/          # Six deterministic security agents and graph-backed LangGraph DAG
      integrations/    # Mock/Gemini providers; allowlisted IAM/CloudTrail reads and private S3 exports
      schemas/         # API request/response models
      workers/         # Bounded startup recovery from FalkorDB
      demo/            # Synthetic inventory, scenarios and proof/persistence CLIs
      main.py
    tests/
    requirements.in
    requirements.txt
    requirements-dev.in
    requirements-dev.txt
    pyproject.toml
    Dockerfile
  scripts/
  docs/
  .env.example
  docker-compose.yml
  .gitignore
  README.md
```

## Dependencies and operation

Runtime and development dependency trees are fully pinned in separate lock files. Direct
requirements live in `.in` files. Regenerate them together on Python 3.12, then rerun tests:

```powershell
Set-Location backend
uv pip compile requirements.in --python .venv/Scripts/python.exe --output-file requirements.txt
uv pip compile requirements-dev.in --python .venv/Scripts/python.exe --output-file requirements-dev.txt
uv pip sync requirements-dev.txt --python .venv/Scripts/python.exe
Set-Location ..
```

Python and FalkorDB container images are pinned to version and manifest digest. FalkorDB
uses a named volume at the explicitly configured `/data` directory, AOF with `everysec`
fsync, periodic snapshots, and `noeviction`. Up to about one second of the latest writes
can be lost after abrupt host/power failure. A real server restart and graph recovery were
verified; Docker container recreation remains pending on this workstation.
Volumes are durable local storage, not backups or high availability.

```powershell
docker compose ps
docker compose logs --tail 100 backend
docker compose logs --tail 100 falkordb
docker compose stop
docker compose up -d --wait
docker compose down   # Keeps the named graph volume
```

Do not use `docker compose down --volumes` unless you intend to erase graph state. Changing
the Compose project name creates a different volume. Do not print or share `docker compose
config` output without `--quiet`: resolved environment values can contain credentials.

This is a production-style foundation, not a completed production deployment. Scoped bearer
authentication is implemented. Identity federation, TLS termination, backup/restore, resource sizing and public
deployment controls must be implemented and reviewed before exposing future application APIs.
The private local Compose connection uses password authentication; the Python adapter also
supports certificate-verified TLS for an externally managed FalkorDB deployment.
Repository tenant isolation and authenticated HTTP scope binding are implemented. Synthetic
agent controls retain their fixed tenant/delegated agent binding. See [Phase 10 verification](phase10_verification.md) for actual
test results and the Docker Desktop limitation.

See [attribution](attribution.md) for third-party components and sources.

Demo, risk, memory, agent, case and remediation APIs are disabled in production and when demo mode is off.
AWS development controls are also disabled in production. Current checks and limitations are
in [Phase 10 verification](phase10_verification.md). API keys bind tenant and permissions;
graph membership still controls memory/investigation access. AWS tests use mocked SDK responses.
The latest [audit test recheck](backend_audit_recheck_tests.json) records 457 passing tests,
including 202 real FalkorDB integration tests, with zero skips. The
[audit evaluation](backend_audit_evaluation.json) records the new five-part demo, actual
process restart, controlled memory ablation and measured query timings. Earlier phase reports
retain their historical results.
AWS remains disabled by default. The [API proof](api_contract_proof.json) and
[OpenAPI document](OPENAPI_V1.json) support the future 3D frontend without implementing it.
The [local export proof](aws_local_export_proof.json) writes a
[redacted JSON report](redacted_report_example.json) with AWS entirely disabled.
FalkorDB stores report metadata and investigation links; files remain external artifacts.
The [remediation proof](remediation_proof.json) shows two potential paths become one,
then zero under a combined hypothetical exclusion. The source permissions remain unchanged;
the [restart proof](remediation_restart_proof.json) preserves proposals, simulations and
review decisions. Approval is for the recommendation only; no IAM writer or remediation executor exists.
The [complete investigation proof](investigation_proof.json) retains a prior incident,
validated historical procedure, conflicting observations and an explicit synthetic accepted-risk
review. Its [restart proof](investigation_restart_proof.json) verifies Agent A's two
completed checks survive and Agent B completes the other two after automatic startup recovery.
Acceptance records a review; it does not claim AWS remediation or proven exploitation.
The [agent proof](agent_proof.json) completes all six agents, retains six shared memories
and handoffs, and leaves one remediation task awaiting review. The
[restart proof](agent_restart_proof.json) verifies automatic startup recovery after both
backend and database restarts. The earlier [memory proof](memory_proof.json) retains all
three memory types, a correction and conflict history. See the [risk proof](risk_engine_proof.json) and earlier
[temporal Scenario A proof](scenario_a_proof.json). Findings are POTENTIAL ATTACK PATHS;
the normalized IAM subset does not prove effective AWS authorization.

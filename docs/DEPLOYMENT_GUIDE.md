# Deployment and operations guide

This is a local/private hackathon backend with production-style safeguards. The full
synthetic workflow requires `APP_ENV=development` and `DEMO_MODE=true`. Production mode
deliberately disables development agent/case/demo/memory/remediation controls; changing
that flag does not provide a production SOC platform. See the current
[completion report](BACKEND_COMPLETION_REPORT.md) for actual verification and external blockers.

## Windows / Docker Compose setup

Prerequisites: Git checkout, Docker Desktop running Linux containers with WSL2. Host Python
3.12 is optional for Docker-only operation and required for native development/evaluation.
From the repository directory:

```powershell
docker version
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -SkipPython
docker compose --profile test --profile evaluation config --quiet
docker compose build backend
docker compose up -d --wait --wait-timeout 120
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\smoke.ps1
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod http://127.0.0.1:8000/api/v1/ready
```

`docker version` must report a working Linux **server**, not just an installed CLI. A
missing `dockerDesktopLinuxEngine` pipe means the engine must be started/repaired before
build/up can work. Current audit: Compose configuration passed; image build could not
connect to that pipe. Container startup/recreation was therefore not verified here.
The subsequent [security/setup recheck](backend_security_recheck.json) reproduced this
Docker blocker and successfully executed the documented Windows PowerShell setup/smoke
commands against the native backend. Existing local settings and credentials were retained.

Setup preserves existing `.env` and principal entries. New setup generates a random local
database password and API key; only the API digest is stored in `.env`. The bearer key is
held in a user-restricted `%LOCALAPPDATA%\ShadowChainAI` directory outside this OneDrive
checkout. Do not print `.env`, tokens, full resolved Compose configuration or SDK responses.
Use `docker compose config --quiet`. Existing principals are not silently reconfigured by
rerunning setup with different permissions.

For an existing installation missing authentication:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup-api-auth.ps1
docker compose up -d --force-recreate backend
. .\scripts\api-client.ps1
$headers = Get-ShadowChainHeaders
Invoke-RestMethod http://127.0.0.1:8000/api/v1/dashboard/summary -Headers $headers
```

An empty `API_CLIENTS=[]` denies protected requests. Service keys bind tenant and
permissions; default setup uses the synthetic tenant and read/write/review permissions.
Do not grant `review` to a principal intended only to trigger investigations. Manual
lifecycle transitions, evidence/hypothesis/memory reviews and proposal reviews require it.

## Configuration

The complete placeholder template is [.env.example](../.env.example); typed validation is
in `backend/app/core/config.py`. Restart the backend after changing configuration.

| Configuration | Required action |
| --- | --- |
| FalkorDB | Supply private endpoint, graph name and password; setup supplies local Compose values |
| API authentication | Setup creates the local principal/key; configure additional digests and scopes explicitly |
| Frontend origin | Set exact `CORS_ORIGINS` JSON, e.g. localhost:5173; no wildcard origin |
| AWS / CloudTrail | Leave disabled for demo; optional profile/account/tenant opt-in described below |
| LLM | Leave `AGENT_PROVIDER=mock`; optional Gemini key/model need separate verification |
| Export | `EXPORT_MODE=local`; optional private S3 bucket requires AWS opt-in |
| HTTP quota | Default 30 protected data requests/minute/key; explicitly increase for local chatty demo if needed |

Safe demo defaults:

```dotenv
APP_ENV=development
DEMO_MODE=true
AWS_ENABLED=false
CLOUDTRAIL_ENABLED=false
EXPORT_MODE=local
AGENT_PROVIDER=mock
```

Compose overrides the database host to `falkordb:6379` on its private network. A host-side
Python process needs the address/port of a separately provided private FalkorDB endpoint;
the hostname `falkordb` is not a Windows DNS name. The normal Compose file publishes no
database port. Do not expose one publicly to accommodate native development. For a
managed remote endpoint, use `FALKORDB_TLS=true` with a valid certificate/hostname.

Native backend against an already authorized private local server:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
$env:FALKORDB_HOST = '127.0.0.1'
$env:FALKORDB_PORT = '<YOUR_PRIVATE_LOCAL_PORT>'
$env:PYTHONPATH = (Join-Path (Get-Location) 'backend')
.\backend\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 18000 `
    --no-access-log --no-proxy-headers --ws-max-size 4096 --ws-max-queue 4
```

This audit's private WSL test server used 16380; the persistent demo server used 16379.
They are workstation verification endpoints, not ports published by the Compose file.

## Deterministic demonstration and tests

With Docker available:

```powershell
docker compose --profile test build tests
docker compose --profile test run --rm tests
docker compose --profile evaluation run --rm evaluation
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-persistence.ps1
```

The test profile uses a separate temporary FalkorDB with no persistence. Integration
fixtures use unique graph names and exact cleanup, never FLUSHDB/FLUSHALL. Do not run
negative-schema tests against the persistent demo database's recovery log. The persistence
script recreates the project's service containers while retaining its volume; run it only
when a brief local interruption is acceptable.

With Python installed and a separate private test server configured:

```powershell
.\backend\.venv\Scripts\python.exe -m pip check
.\backend\.venv\Scripts\python.exe -m ruff check backend/app backend/tests scripts/verify_api_contract.py
.\backend\.venv\Scripts\python.exe -m ruff format --check backend/app backend/tests scripts/verify_api_contract.py
$env:RUN_INTEGRATION_TESTS = 'true'
$env:FALKORDB_HOST = '127.0.0.1'
$env:FALKORDB_PORT = '<PRIVATE_TEST_SERVER_PORT>'
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q --tb=short --show-capture=no
```

Without integration opt-in the real graph tests skip; that run alone is not a backend
completion proof. Use the test endpoint's password through private settings/environment.
For the complete demonstration, point back to the persistent private demo server:

```powershell
$env:FALKORDB_PORT = '<PRIVATE_DEMO_SERVER_PORT>'
.\scripts\demo-hackathon.ps1 -OutputDirectory artifacts/hackathon/my-fresh-demo -Repetitions 20
```

Choose a fresh output directory. The evaluator creates an isolated FalkorDB graph and
owns its two FastAPI child processes; shared servers are not restarted by it. It clears
the LLM key and forces mock/AWS-disabled settings. The resulting report is itself retained
as graph evidence; local JSON/log files are derived artifacts, not recovery checkpoints.
See [HACKATHON_DEMO.md](HACKATHON_DEMO.md) and [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Data durability and recovery

`falkordb_data:/data` is the authoritative graph volume. FalkorDB is authenticated, has no
published ports, and uses AOF `appendfsync everysec`, periodic RDB snapshots and `noeviction`.
An abrupt host/power failure may lose about one second of recent writes. Named volumes
are neither backups nor high availability. No cross-region recovery or tested backup
automation is supplied. Before any public deployment, implement and rehearse backup,
restore, retention, monitoring, disk-capacity handling and recovery objectives.

```powershell
docker compose ps
docker compose logs --tail 100 backend
docker compose logs --tail 100 falkordb
docker compose restart backend
docker compose down
docker compose up -d --wait
```

`down` retains named volumes; **`down --volumes` erases them**. Keep the Compose project
name consistent. Demo resets affect only the selected raw synthetic scenario, while
saved analyses/evidence/audits remain. Startup recovers eligible unfinished synthetic
workflows from graph cursors, prepared outputs, leases, tasks and handoffs. A caller retry
uses the original operation ID. Expired/failed lease work has bounded retries; recovery
is not an unlimited autonomous loop.

Liveness remains 200 during a database outage; readiness and core data operations return
503. Recovery requires the same graph/volume and matching tenant/configuration, not just
an empty replacement database. Test reconnect/process restart proofs do not establish
backup restoration or Docker volume recovery on a different host.

## Optional integrations

See [AWS_SETUP.md](AWS_SETUP.md) for exact operation allowlists, IAM templates, account
confirmation, throttling/pagination bounds, private S3 requirements and coverage. Use an
existing approved CLI profile or workload credential chain; never source-code credentials.
AWS sync is explicitly triggered in development. No real AWS call was authorized or run
in this audit. IAM/security APIs are read-only; optional S3 PutObject writes only a redacted
report artifact. No AWS infrastructure is provisioned and no remediation executor exists.

Setting `AWS_PROFILE` in Compose does not mount host profiles/SSO caches. An operator must
provide an approved read-only mount with UID 10001 access or workload identity. This is
not done automatically. Imported IAM inventory is not full effective-permission coverage;
CloudTrail Event History is bounded regional management-event history, not complete audit.

Gemini is optional prose over allowlisted graph-derived facts. Configure a real key and
a model supported by your account only after separately validating the provider contract.
Live Gemini, its configured default model availability, and real S3 were not verified.
Mock mode provides all deterministic core behavior without any external key.

## Before public deployment

The runtime image uses an unprivileged backend user, read-only filesystem, dropped
capabilities, bounded writable volumes/tmpfs, and pinned base images. This configuration
was reviewed but could not be container-executed in this audit. Add HTTPS/WSS termination,
end-user authentication/session authorization, private secrets distribution, key rotation,
gateway request/connection/concurrency limits, slow-client deadlines, resource sizing,
observability, durable backups and operational incident procedures. The application has
per-principal graph budgets and socket timeouts, not a complete public traffic defense.
Review tenant-wide metadata access before introducing sensitive customer data. Keep
development controls private; never expose FalkorDB directly. Image OS vulnerability
scanning and deployment load tests remain outstanding.

# Task 1 verification

Verification date: 16 October 2026 (Asia/Calcutta).

**Historical Task 1 report.** [Phase 2 verification](phase2_verification.md) supersedes the
database-test limitations below: real FalkorDB was subsequently run in an isolated WSL
environment and graph persistence/restart recovery were verified. Docker image builds and
Compose container recreation remain unverified because Docker Desktop is unavailable.

## Workstation and installation

- Workspace was an empty Git repository with no application files or existing dependencies.
- Existing Python installations: 3.11.0 and 3.13; Python 3.12 was initially missing.
- Installed an isolated CPython 3.12.12 using uv 0.9.6. Created `backend/.venv`.
- Installed all locked development/runtime dependencies; `python -m pip check` passes.
- Ran `scripts/setup.ps1` successfully, retaining an existing generated local `.env`.
- Docker CLI 29.2.1 and Compose v5.1.0 are installed. Desktop's engine is unavailable.

## Verified

| Check | Actual result |
| --- | --- |
| Unit suite on Python 3.12.12 | 23 passed |
| Integration suite without live database | 3 explicitly skipped; not counted as passes |
| Ruff lint and formatting | Passed |
| Dependency compatibility | No broken requirements |
| Compose configuration parsing | Passed |
| Parsed Compose security/storage assertions | No DB host ports; DB only on internal network; API loopback binding; named `/data` volume; AOF and `noeviction` |
| Real Uvicorn process, DB unavailable | Started successfully; bounded startup DB failure logged as JSON |
| Live HTTP `GET /api/v1/health` | 200, `status=ok`, UTC timestamp, `demo_mode=true` |
| Live HTTP `GET /api/v1/ready` during DB outage | 503, `dependency_unavailable`, request ID and retry header |
| Live HTTP unknown route | 404 with centralized error handling |
| Live HTTP CORS preflight | 200 for configured localhost origin |

The local HTTP checks used `FALKORDB_HOST=127.0.0.1` and an unused port `16379`, since the
private Compose DB was unavailable. This verifies the real client's failure path, not a
successful graph connection. Unit tests separately cover readiness success/recovery with
test-only dependency mocks, UUID request IDs, secret masking, sanitized 422/500 responses,
pool shutdown, CORS restrictions and production docs configuration.

A real SDK check found that FalkorDB 1.8.0 performs network discovery in its constructor.
Initialization was moved inside the query error boundary and off the async event loop;
a regression test now covers initial constructor failure and recovery on the next request.
RESP2 and decoded responses are configured explicitly on the supplied connection pool.

## Blocked verification

Both commands were attempted and exited with status 1:

```powershell
docker compose build backend
docker compose up -d falkordb
```

They could not connect to `npipe:////./pipe/dockerDesktopLinuxEngine`; the engine pipe does
not exist. Starting Docker Desktop and using `docker desktop start --timeout 60` did not
produce a working engine. Desktop's backend log identifies the cause:

```text
starting services: initializing Inference manager:
listening on unix://<LOCALAPPDATA>\Docker\run\dockerInference:
remove ...\dockerInference: The file cannot be accessed by the system.
(listener: The filename, directory name, or volume label syntax is incorrect.)
```

This failure matches [Docker's reported stale Windows socket startup issue](https://github.com/docker/desktop-feedback/issues/448).
An attempted Docker process cleanup and exact stale-socket removal was rejected by automatic
approval review as blocked by policy. No Docker runtime files were deleted and no factory
reset or system-level repair was performed.

Consequently, the following **remain unverified**, and Task 1 is not signed off end to end:

- Building/running the runtime and test Docker images, including Linux dependency behavior.
- Successful FalkorDB server startup and official-client graph queries.
- A live `200` readiness response against FalkorDB.
- Wrong-password integration behavior against a live server.
- AOF/RDB behavior and graph recovery after container recreation.

Image version/digest existence and the server module path were checked against Docker Hub
metadata and upstream image build source. These checks do not replace running the images.

## Resume after Docker Desktop is repaired

From the project directory:

```powershell
docker compose build backend
docker compose up -d --wait --wait-timeout 120
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\smoke.ps1
docker compose --profile test build tests
docker compose --profile test run --rm tests
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-persistence.ps1
```

The test container must report all 26 tests passed with no integration skips. The smoke
script must report both endpoints as 200. The persistence script must report an unchanged
foundation creation timestamp. Only then mark this foundation verified and proceed to the
next phase. No frontend, IAM analysis or agent workflow has been implemented.

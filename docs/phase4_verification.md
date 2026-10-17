# Phase 4 verification

16 October 2026, Asia/Calcutta. Source-time demonstration: 16 October 2026 UTC.

The existing architecture, graph schema, validators, event engine, fixtures and tests were
inspected and extended. No frontend, AWS mutation, LLM path generation, new database or
unlocked dependency was introduced. Python 3.12.12 and official FalkorDB client 1.8.0 remain
on the existing tested locks. The server was real FalkorDB 4.22.1 / Redis 8.10.2.

## Automated results

```text
221 passed in 48.83s
106 real FalkorDB integration tests; 115 unit/validation/failure tests
0 skipped, 0 failed
```

From `backend`, against the separate nonpersistent real test server:

```powershell
$env:FALKORDB_HOST = '127.0.0.1'
$env:FALKORDB_PORT = '16380'
$env:RUN_INTEGRATION_TESTS = 'true'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m ruff format --check app tests
.\.venv\Scripts\python.exe -m pip check
```

Ruff and formatting passed for all 70 Python files. `pip check` found no broken requirements.
All PowerShell scripts parsed successfully; Compose test-profile configuration validated.
The complete earlier-phase regression suite is included.

New checks prove:

- Scenario A has no sensitive path at 09:00 and one potential HR-data path at 12:00.
- Actual Cypher enumerates the role/permission paths; direct zero-role paths and depth bounds work.
- Caller permission and role trust are both necessary; connected CAN_ASSUME alone is rejected.
- Explicit identity/resource/boundary/session Deny overrides Allow; empty constraints restrict access.
- SCP/RCP account policies are traversed and intersected; deeper or uncertain hierarchy is unresolved.
- Supported MFA/IP conditions need request context; unknown conditions and uncertain Deny are unresolved.
- Cross-account resource access needs a matching resource-policy principal/Allow.
- Exposure/control scoring requires structured verified graph evidence; no exposure is assumed.
- Scenario B's expired justification adds exactly 12 points.
- Specific creation and Deny-closure events appear in provenance; provenance is not a causal proof.
- Removing an Allow/grant breaks its path; removing a Deny can increase modeled risk.
- What-if uses read-only operations and leaves graph state unchanged.
- Concurrent duplicate analyses reconcile to one result; concurrent event arrival refreshes timeline sampling.
- Immutable findings and evidence survive reconnect and synthetic scenario reset.
- Other tenants cannot discover/read findings, evidence or excluded relationships belonging to this tenant.
- API scope, depth, action, timestamps and IDs validate; real database outages fail clearly.

## Live demonstration

A real Uvicorn process served the API on loopback port 18000. Both health and readiness returned
200. The PowerShell risk demonstration exercised historical discovery, differences and what-if:

```powershell
.\scripts\smoke.ps1 -BaseUrl http://127.0.0.1:18000
.\scripts\demo-risk.ps1 -BaseUrl http://127.0.0.1:18000
```

| UTC | Potential sensitive paths | Maximum heuristic score |
| --- | --- | --- |
| 09:00 | 0 | 0 |
| 10:00 | 0 | 0 |
| 11:00 | 0 | 0 |
| 12:00 | 1 | 51/100 |

The path is synthetic Alice → temporary analyst role → cross-account EC2 backup role →
sensitive-read policy → s3:GetObject permission → HR-data resource. Finance data is excluded
by its explicit Deny. Source events at 10:00 and 11:00 supply the caller/trust/assumption
relationships; the **12:00 SyntheticSensitiveReadPolicyAttached** event introduces the final
policy attachment. Removing that attachment in a read-only simulation leaves zero paths.

[risk_engine_proof.json](risk_engine_proof.json) contains the generated finding, score factors,
actual edge/event IDs, timeline, introduction and simulation result. It was produced with
`python -m app.demo.risk_proof` against the persistent verification graph and agreed with
the live HTTP demonstration. This is a modeled potential path, not proven AWS exploitation.

## Persistence beyond reconnect

The integration suite checks new client/service connections. Additionally, a demo-only
persistent server was saved/stopped, restarted and loaded its AOF successfully. The backend
was stopped and recreated too. The new backend fetched existing analyses/evidence through
GET endpoints **without seed, replay or recomputation**. The complete saved responses matched
the pre-restart oracle exactly, including four analyses, the finding, score and 75 evidence
snapshots:

| UTC | Saved analysis ID |
| --- | --- |
| 09:00 | `4b40d88b-8b64-549b-8b9e-eed509c1bb1a` |
| 10:00 | `eaeeae19-c97e-53da-a8cd-2a7b5b91eb9a` |
| 11:00 | `d391e8cd-efae-5e7c-b247-48371c14c1de` |
| 12:00 | `dc76df7f-4a7e-5c46-a1f0-56aabe4f78ad` |

Finding ID: `284f727d-8923-5255-90d4-0602e4c6ad51`; score **51**, evidence snapshots **75**.
The private verification graph is `shadowchain_phase4_verified`. Its AOF/RDB data remains
under the ignored `.verification/phase4-demo-data` directory. Local JSON proof/oracle files
are verification output only and are never loaded as application state.

This checks graceful database/backend recovery, not abrupt power loss. AOF uses `everysec`;
approximately the latest second of writes can be lost on abrupt host failure.

## Environment limits

Docker Desktop's Linux engine named pipe remains unavailable. An actual
`docker compose build backend tests` attempt failed to connect to
`dockerDesktopLinuxEngine`. Compose configuration parsed, but image builds and actual Compose
network/container/volume behavior could not be verified. No Docker system cleanup was attempted.

The pinned official image layers already downloaded and hash-verified in earlier phases supplied
the real server binaries under isolated WSL root filesystems. The persistent demo server was
loopback-bound on port 16379 with authentication, AOF/RDB, `everysec` and `noeviction`. The
negative-test server was separate on port 16380 with persistence disabled. No credential was
printed. This verifies real graph semantics and recovery, not execution through Docker.

The older test-history AOF recovery failure documented in [Phase 3](phase3_verification.md)
remains unresolved; its files were preserved. Negative schema fixtures remain isolated from
the persistent demo server. The fresh Phase 4 demo-only recovery succeeded.

See [risk_engine.md](risk_engine.md) for authorization limits, scoring, sampling, APIs and
bounded-query behavior. Live AWS effective authorization and autonomous agents remain future work.

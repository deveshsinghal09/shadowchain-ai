# Backend completion and security review

Audit date: **17 October 2026**. Scope: the existing backend, tests, scripts, schemas,
configuration, Docker definitions and documentation. No frontend was built. No live AWS,
Gemini, S3, IAM modification or infrastructure provisioning was authorized or performed.

**Architecture verdict:** FalkorDB is indispensable to the implemented core. The backend
is suitable for a private, authenticated synthetic frontend demonstration. It is not a
complete public production deployment or a complete AWS effective-permission evaluator.
See [FRONTEND_INTEGRATION_GUIDE.md](FRONTEND_INTEGRATION_GUIDE.md) and
[DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) for the supported integration and operating model.

## Latest revalidation

Revalidated **17 October 2026, 06:38:33 UTC** following the repeated review request.
[Repository inventory/hash comparison](backend_repository_recheck.json) found no changes
to application, test or script files since the previous audit manifest. All 147 publishable
Python files parsed; current publishable source contained no actual local credentials,
provider-key patterns or complete private-key blocks. No new runtime defect was found;
the five fixes below remain present and covered by regression tests.

The complete suite was rerun using the same explicit integration/cloud-disabled environment
documented below, with this new result path:

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q --tb=short --show-capture=no `
    --junitxml=artifacts/hackathon/backend-audit-recheck-tests.xml
```

**Exit 0: 457 passed, 0 failures, 0 errors, 0 skipped**, including **202 real FalkorDB
integration tests** and **255 other tests**. JUnit elapsed time was **341.518 seconds**;
the console reported **341.57 seconds**. The
[new per-test receipt](backend_audit_recheck_tests.json) includes the successful five-demo
integration test, which starts two actual FastAPI processes and checks graph-based restart
recovery, conflict retention, shared context, deduplication and non-destructive simulation.

A separate [fresh-graph HTTP/WebSocket proof](backend_api_recheck.json) also passed:
six completed agents, historical edge counts 123/126/129/130, matching reconnect replay,
path-breaking simulation and identical source-permission hashes. Its OpenAPI matches the
documented 75 paths/76 operations. Earlier standalone demonstration timing samples below
remain the earlier measurement; they were not relabeled as new benchmark results.

[Fresh security/setup results](backend_security_recheck.json) record passing Ruff
lint/format, `pip check`, all 14 PowerShell parses, Compose configuration and a package
advisory rescan (60 packages, no known vulnerabilities returned). Bandit again reports
four reviewed low subprocess findings, zero medium/high and no scan errors. Windows
PowerShell setup reran successfully without changing local settings/credentials; native
health/readiness returned 200 with AWS/CloudTrail disabled and local export. All 33
publishable JSON files present at that validation point parsed successfully.

`docker compose build backend` was retried and still exited 1 because the Docker Desktop
Linux engine pipe is absent. Container build/start/recreation and image OS scanning remain
unverified. No live AWS/S3/Gemini call was made. The temporary test server is stopped after
verification; the existing persistent demo database and main backend remain available.

## Findings fixed in this review

| Finding | Fix | Verification |
| --- | --- | --- |
| A read/write key could submit manual investigation lifecycle decisions, including accepted-risk resolution, without review scope | Require `review` on `/transitions` alongside the existing review routes; reflect it in OpenAPI | Regression verifies 403 and the documented permission |
| Default memory-review API delegated to a writer although the graph requires a curator | Bind review to the bootstrap's Verifier curator; retain HTTP review scope and current graph ACL checks | Real HTTP write/review and persisted `reviewed_by` verification |
| Edge differences omitted endpoint validity, disagreeing with snapshots after a node expired | Check both endpoints and the edge at both comparison timestamps | Real historical snapshots and diff agree at 10:00/12:00 |
| Rapid valid WebSocket pings could accelerate graph polling beyond configured cadence | Enforce the minimum polling delay independently of incoming pings | Five rapid pings measured against the real repository |
| API proof used Python asserts that disappear under `-O` | Replace with explicit runtime checks; ignore proxy environment for local credential-bearing HTTP; allow a separate output directory | Optimized-Python failure probe and executed HTTP/WebSocket proof |

The four runtime regressions are in `backend/tests/test_backend_audit.py`. The API verifier
change preserves historical documentation artifacts by allowing new audit outputs elsewhere.

## Audit of the requested areas

| Area | Implementation and evidence | Boundaries / verdict |
| --- | --- | --- |
| 1. Primary graph | Official client, typed schema/index/constraint initialization, tenant repositories, persistent domain records, memory/tasks/handoffs/decisions and live journal all use FalkorDB | Implemented; no alternative authoritative database or runtime fallback |
| 2. Graph traversal | `repositories/risk.py::traversal_query` executes `CAN_ASSUME*0..N -> HAS_POLICY -> GRANTS -> TARGETS` and traverses supporting policy, membership and evidence paths in Cypher | Implemented and real-DB tested; Python evaluates normalized policy constraints after traversal |
| 3. History | Stable source IDs, event hashes, atomic related changes, source/ingestion times, `[valid_from,valid_to)`, immutable evidence snapshots, idempotency, generation/revision guards | Tested; late events are explicitly rejected, not silently reordered |
| 4. Persistent memory | Episodic, semantic and procedural graph records; evidence links, versions, correction, supersession, contradiction and validated procedure steps | Tested; untrusted text is data, never executable authority |
| 5. Coordination | Six logical LangGraph stages, graph-owned tasks/dependencies, shared memory, decisions and handoffs | Tested; fixed bounded workflow, not unrestricted autonomous agents |
| 6. Restart recovery | Durable cursor/prepared output, operation IDs, fenced leases, bounded retry and startup reconstruction | Real process restart and reconnect tested; no in-memory checkpointer recovery |
| 7. Finding evidence | Saved risk analyses link immutable source-record evidence with hashes and source events; cases retain hypotheses, audit and evidence review | Tested, including source closure/reset and later retrieval |
| 8. IAM accuracy | Explicit denies, normalized trust/caller/resource grants, supported conditions and modeled constraint intersections; unknown relevant constraints reject as unresolved | Partial AWS semantics, explicitly **POTENTIAL ATTACK PATHS**; zero findings is not proof of safety |
| 9. Remediation | Exact edge exclusions in read-only Cypher; durable proposals/simulations/reviews; alternate-path and potential collateral-access comparisons | Tested source-permission invariance; no AWS remediation executor |
| 10. Credential-free demo | Synthetic versioned organization, two accounts, 12 identities, 10 roles, 15 policies, 20 resources, three scenarios; mock provider | End-to-end tested without AWS/LLM keys |
| 11. AWS integration | Lazy Boto3 allowlist, account confirmation, bounded paging/retries, read-only IAM and CloudTrail; separate optional report-only S3 PutObject | Mocked SDK + real graph normalization tested; live AWS/S3 not tested |
| 12. API isolation | Scoped bearer digests, server-bound tenant, read/write/review/aws:sync gates, current graph memory membership, CORS/Origin checks, graph-backed quotas and journal isolation | Positive/negative tests; service-key model, not end-user federation/session auth |
| 13. Secret handling | Ignored local settings/venv/artifacts, private operator key outside checkout, no prompt/report credential source, sanitized logs/errors | Publishable-file scan found no real local secret; no committed history exists to inspect |
| 14. Setup/Compose | Pinned images, private authenticated DB, AOF/RDB volume, nonroot restricted backend image, test/evaluation profiles and PowerShell setup | Config/parser/idempotence verified; Docker build/start/recreation blocked by unavailable engine |
| 15. Tests | Real unique-graph integration fixtures plus unit/mocked failure tests, actual FastAPI process restart, real sockets, controlled ephemeral ablation | Actual execution results below; mocked AWS is explicitly separated from live verification |
| 16. Frontend contract | Versioned Pydantic/OpenAPI, stable node/edge IDs, evidence/case associations, paginated historical views, activity projections, authenticated replayable WebSocket | Ready for private demo integration; no frontend or public session proxy implemented |

## Fully implemented within the supported scope

- Single-graph, tenant-bound persistent cloud model and security evidence, typed safe Cypher
  parameters, stable application IDs, bounded connections/timeouts/retries and clear outages.
- Deterministic synthetic seeding/replay/reset, half-open temporal relationships, event
  deduplication and explicit rejection of stale generations/out-of-order late arrivals.
- Core Cypher risk discovery, historical evaluation, temporal comparisons, explainable
  heuristic scores, source-event attribution and preserved evidence-backed findings.
- Three graph memory types, current authorization, sharing, audit-preserving correction,
  supersession, conflict quarantine, source provenance and non-executable procedures.
- Six-stage durable LangGraph coordination, case reconstruction/lifecycle, task ownership,
  dependencies, cross-agent context and restart reconstruction with duplicate prevention.
- Evidence-backed durable remediation recommendations, immutable simulation results and
  human review states; approval authorizes only a recommendation, never an AWS action.
- Authenticated REST/WS v1 contract, graph-backed ordered notifications, replay, bounded
  payloads, pagination fences, rate budgets and structured safe error/log envelopes.
- Optional bounded AWS readers and redacted JSON report export implementation, with
  account scope and operation restrictions. These implementations still need external
  configuration and live validation before claiming real service compatibility.

“Implemented” describes inspected code, not an unconditional deployment claim. The following
verification results delimit what was actually exercised.

## Verification commands and results

Environment: Windows/PowerShell, Python **3.12.12**, real FalkorDB **4.22.1**, official
client **1.8.0**, FastAPI **0.143.2**, Pydantic **2.14.1**, LangGraph **1.2.15**,
pytest **9.1.1**, HTTPX **0.28.1**. Dependencies were already installed; `pip check`
confirmed compatibility. Security tools were installed separately from the application.

Inspection inventoried all 220 publishable candidates before new audit documents/receipts,
parsed every Python source, examined critical API/repository/service/agent/integration
flows, and reviewed tests, Docker/setup and documentation. Generated schemas/samples were
checked programmatically and through representative responses, rather than a claim of
manually reading every generated JSON line. Ignored credentials/environments/data/logs
were excluded from publishable source scans.

### Regression suite

From `shadowchain-ai`, against the dedicated real authenticated test server:

```powershell
$env:RUN_INTEGRATION_TESTS='true'
$env:FALKORDB_HOST='127.0.0.1'
$env:FALKORDB_PORT='16380'
$env:AWS_ENABLED='false'
$env:CLOUDTRAIL_ENABLED='false'
$env:EXPORT_MODE='local'
$env:AWS_EC2_METADATA_DISABLED='true'
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q --tb=short --show-capture=no `
    --junitxml=artifacts/hackathon/backend-audit-tests.xml
```

**457 passed, 0 failures, 0 errors, 0 skipped; 336.152 seconds.** This includes **202 real
FalkorDB integration tests** and **255 unit/mocked-provider/API and other tests**. The
[per-test report](backend_audit_test_report.json) records every result/duration and file
counts. The local JUnit receipt remains in the ignored artifact directory. Categories below
overlap within this one complete execution; they are not additional test totals.

| Requested check | Executed coverage |
| --- | --- |
| Unit / negative behavior | Configuration, models, log/error redaction, connection failures, policy conditions, model output validation and mocked AWS dispatch |
| Graph integration | 48 repository tests plus schema/foundation/outage/tenant checks |
| Temporal graph | 21 event tests, 9 core risk tests, 22 policy traversal tests; half-open expiry, late rejection, historical evidence and endpoint-diff regression |
| Multi-agent memory | 19 memory tests, 16 workflow tests; sharing, revocation, conflict/supersession, untrusted data, duplicate tasks and fenced leases |
| Investigation / restart | 14 case tests and 2 evaluation integration tests; cold FastAPI recovery, preserved runs, handoffs and graph-backed report reconstruction |
| API / real-time | Contract, health, demo, risk, memory, workflow, cases, remediation and live tests; auth/tenant denial, cursor fences, replay/order, quotas and ping bounds |
| Remediation | 12 real integration tests plus API/models; alternate paths, source invariance, repeatability and preserved reviews |
| Optional AWS | 21 mocked client tests, 13 mocked/local export tests, 7 real-graph normalization tests; no live AWS compatibility claim |

### Static, dependency, secrets and setup checks

Actual commands:

```powershell
.\backend\.venv\Scripts\python.exe -m pip check
.\backend\.venv\Scripts\python.exe -m ruff check backend/app backend/tests scripts/verify_api_contract.py
.\backend\.venv\Scripts\python.exe -m ruff format --check backend/app backend/tests scripts/verify_api_contract.py
.\backend\.venv\Scripts\python.exe -m venv .verification/security-tools
.\.verification\security-tools\Scripts\python.exe -m pip install pip-audit bandit
.\.verification\security-tools\Scripts\python.exe -m pip_audit -r backend/requirements-dev.txt `
    --no-deps --disable-pip --progress-spinner off --timeout 15 --format json `
    --output .verification/backend-dependency-audit.json
.\.verification\security-tools\Scripts\python.exe -m bandit -r backend/app scripts `
    -f json -o .verification/backend-bandit.json -q
.\scripts\setup.ps1 -SkipPython
docker compose --profile test --profile evaluation config --quiet
docker version --format '{{.Server.Version}}'
docker compose build backend
```

PowerShell parsing used the following additional command, followed by an assertion that
`$parseErrors.Count` was zero:

```powershell
$parseErrors=@()
foreach ($file in (Get-ChildItem scripts -Filter *.ps1)) {
    $tokens=$null; $errors=$null
    $null=[System.Management.Automation.Language.Parser]::ParseFile(
        $file.FullName,[ref]$tokens,[ref]$errors)
    $parseErrors+=@($errors)
}
```

| Check | Actual result |
| --- | --- |
| Installed dependencies | `No broken requirements found` |
| Ruff lint / format | Passed; 146 Python files already formatted |
| PowerShell syntax | All 14 scripts parsed with `System.Management.Automation.Language.Parser`; zero errors |
| Optimized verifier | `python -O` deliberately false `require()` still raised RuntimeError |
| pip-audit 2.10.1 | Exit 0, 60 pinned packages, 0 skipped, **0 known vulnerabilities** returned at scan time |
| Bandit 1.9.4 | Exit 1: **4 low**, 0 medium/high, 0 scan errors; 15,616 source lines |
| Secret scan | No actual local DB password, operator bearer or optional Gemini key in publishable candidates; two incomplete private-key-header test markers, no key material |
| Setup rerun | Existing `.env` and private bearer-file hashes unchanged; no credential printed |
| Compose config | Exit 0 with test and evaluation profiles |
| Docker server/build | Exit 1: `dockerDesktopLinuxEngine` pipe missing; image build, container startup/recreation unverified |
| Native backend | Updated code restarted on loopback port 18000; `/health` and `/ready` both HTTP 200 |

The four remaining Bandit findings are the evaluator's subprocess import and three calls.
Manual review found fixed `sys.executable`/modules, argument arrays, no shell, and data via
stdin/environment; source/model text does not become command text. They remain visible
low-severity findings, not suppressed or described as a clean scan. See
[reviewed static findings](backend_static_security_review.json),
[dependency output](backend_dependency_audit.json) and
[secret-scan disposition](backend_secret_review.json).

The secret scan used local Python over `git ls-files --cached --others --exclude-standard`,
pattern matching and comparison with actual local credentials without emitting values.
The final rescan covered **231 publishable candidates**, including new audit receipts;
zero actual local-secret/provider-key/complete-private-key matches were found. All local
links in the three requested guides resolved. The owned ephemeral test server was stopped
after verification; the private persistent demo database and updated backend were retained.
There are **no tracked files or commits** yet, so no historical commit set could be scanned.
gitleaks and Trivy were unavailable. No image OS advisory scan was performed. These
are bounded checks, not a certification of absence of every possible secret/vulnerability.

### Actual REST/WebSocket proof

An owned temporary Uvicorn server used a fresh `shadowchain_audit_api_*` graph, existing
private operator key, AWS/CloudTrail off, mock mode and a local quota of 300. The harness
invoked `scripts/verify_api_contract.py --base-url http://127.0.0.1:<owned-port>
--token-file <private-LOCALAPPDATA-path> --output-directory artifacts/hackathon/backend-audit-api`,
then closed that server. It did not reset the main graph.

The [executed proof](backend_audit_api_contract_proof.json) verifies authenticated HTTP,
rejected unauthenticated graph access, real WebSocket authentication/replay/reconnection,
six completed agents and non-destructive what-if. Historical edge counts were **123 / 126 /
129 / 130** at 09:00/10:00/11:00/12:00. Source-permission SHA-256 was identical before/after
simulation; reconnect replay matched HTTP replay exactly. See the
[actual requests/responses](backend_audit_api_contract_examples.json) and
[new case output](backend_audit_investigation_example.json), all explicitly synthetic.

[OPENAPI_V1.json](OPENAPI_V1.json) was regenerated from that server. Comparison with
`create_app(...).openapi()` matched exactly: **75 HTTP paths, 76 operations**, a separately
documented WebSocket path, and **zero unresolved local schema references**.

### Five-part demonstration and measured evaluation

After the regression suite, with no concurrent test workload:

```powershell
$env:FALKORDB_HOST='127.0.0.1'
$env:FALKORDB_PORT='16379'
.\scripts\demo-hackathon.ps1 -OutputDirectory artifacts/hackathon/backend-audit-demo -Repetitions 20
```

Passed at **2026-10-17T06:28:14.215819Z**, retained in graph
`shadowchain_eval_39a1e772e2444e1baeeed087df22fdd8`. The
[full evaluation](backend_audit_evaluation.json) records graph evidence, process IDs,
input hashes, checks and raw timings. Fixture version `synthetic-2026.1`; manifest SHA-256
`add52bd64aee09fb8c12daa7cc1e45d43a1f9fe90ce213f2a877559d60d462c7`.

| Demonstration | Observed result |
| --- | --- |
| Invisible path | Canonical path counts **0 / 0 / 0 / 1**, three contributing source events |
| Shared memory | Later authorized agent retrieved Risk Oracle's persisted finding/evidence |
| Actual restart | Uvicorn PID 26812 exited; PID 38472 automatically resumed **3 → 6** completed stages; first three runs unchanged |
| Coordination | Five internal handoffs consumed, seven unique tasks including human review; one attempt per stage |
| Conflicts | Both observations/evidence retained, zero definitive facts, verification required |
| What-if | **1 path / score 51 → 0 / 0**, repeatable, identical source graph hash, no AWS operations |
| Memory ablation | Same input: persistent readers **1 → 1**, ephemeral processes **1 → 0**, no alternative persistent database/graph |
| FalkorDB unavailable | Readiness, discovery, shared memory and workflow reconstruction all failed with `dependency_unavailable` |

One warmup, 20 sequential operations, concurrency one; milliseconds. Query timing includes
SDK/network/Cypher/decoding; service operation timing also includes reconstruction/ACL work.

| Probe | Query samples | Query median / p95 | Operation median / p95 |
| --- | ---: | ---: | ---: |
| Historical traversal | 20 | 100.255 / 114.184 | 100.591 / 114.467 |
| Memory ACL/provenance | 60 | 7.421 / 17.082 | 24.839 / 48.647 |
| Workflow reconstruction | 280 | 1.012 / 1.600 | 15.678 / 17.368 |

These are warmed local synthetic measurements, not production SLOs, real-estate
precision/recall or a performance advantage over the ephemeral arm. This run restarted
FastAPI while FalkorDB remained running; it does not prove Docker recreation, host-failure
recovery, backup restoration or live AWS authorization.

## External configuration still required

For the synthetic frontend: a working private FalkorDB, a local scoped operator credential,
and exact browser origins. `scripts/setup.ps1` generates local database/API credentials;
no AWS account or LLM key is needed. Docker execution additionally requires a functioning
Docker Desktop Linux engine. The CLI alone is insufficient.

Optional AWS requires an existing approved profile/credential chain, expected account ID,
configured AWS tenant, explicit AWS/CloudTrail flags and an authorized API principal with
`aws:sync`. Optional S3 also requires an existing private bucket and its documented report
permissions. Compose does not automatically mount host profile/SSO files. See
[AWS_SETUP.md](AWS_SETUP.md). Do not create new credentials in source or provision resources
automatically. Live read-only checks require separate explicit authorization.

Optional Gemini requires an actual key and a supported model/provider contract. Tests
exercise request validation, timeouts, bounded retries and malicious/invalid output handling
with mocks; real model availability and response compatibility have not been verified.

## Partial features and known limitations

1. AWS effective authorization is intentionally incomplete: no full IAM groups, ABAC,
   resource-policy-only authorization, session-principal exceptions, transitive STS session
   semantics, NotAction/NotResource, KMS, S3 ownership/public-access-block effective access,
   or arbitrary deep organization hierarchy. Maximum assumption depth is four. Unsupported
   relevant conditions/constraints are unresolved, not evidence of a safe configuration.
2. AWS inventory normalization is not a live full-account risk/investigation adapter.
   Trust alone does not create effective `CAN_ASSUME`; resource sensitivity is not invented.
   Synthetic agent/case/memory/control adapters retain their fixed tenant and are disabled
   in production. CloudTrail Event History is bounded regional management history rather
   than complete/unlimited audit or an effective-permission replay source.
3. The legacy demo snapshot's `synthetic-policy-subset-v1` path preview uses a simpler
   Python adjacency calculation over graph-read records. The canonical risk engine and
   persisted risk verdicts use actual Cypher traversal. Frontends must use the canonical
   risk API for security findings, not that legacy preview.
4. General visual snapshots/diffs reconstruct membership, not complete bitemporal property
   history. Retained mutable record attributes may reflect their retained value. Saved risk
   evidence is immutable. Timeline reachability samples source-event boundaries/endpoints;
   condition-only changes between samples require explicit historical queries.
5. Demo reset deliberately removes raw records for the selected synthetic source scenario.
   Saved risk evidence, case/agent decisions and audits remain. Reset is not deletion of
   every retained investigation artifact and is not a general customer-data erasure API.
6. The quick `/what-if/remove-grant` and older risk what-if API are computed previews;
   they do not save a remediation proposal/review case. The full `/remediation` workflow
   persists proposals, simulation evidence and decisions. S3 exports support JSON, not PDF.
7. API authentication is scoped service-key authentication. End-user login/OIDC, sessions,
   fine-grained human identity mapping, key distribution/rotation automation and a browser
   BFF are not implemented. Demo actions delegate to fixed trusted agents; tenant read
   scope provides tenant-wide permitted metadata visibility, not arbitrary per-user RBAC.
8. Poll/send bounds and per-key graph quotas are not complete internet traffic protection.
   Global active-socket/concurrency limits, gateway request deadlines, public TLS, WAF/DoS
   controls and load/resource sizing remain deployment work. There was no production load
   or distributed-worker stress certification. Only guarded atomic single-statement Cypher
   writes are promised, not general multi-statement database transactions.
9. AOF every-second persistence permits about one second of data loss after abrupt failure.
   No tested backup/restore automation, high availability, cross-host disaster recovery or
   container-volume recreation proof is supplied by this audit. Local process recovery is
   distinct from these guarantees.
10. Dependencies are version pinned, without hash-locked distribution verification. The
    package advisory scan is a dated known-vulnerability check, not proof of vulnerability
    absence; image OS vulnerabilities were not scanned. Secret scanning is bounded to
    current publishable candidates; this workspace has no committed Git history. Dedicated
    gitleaks/Trivy tools were unavailable.

## Hackathon assessment

The persistent-memory/coordination claim is supported by real graph records, graph-grounded
cross-agent retrieval and a cold-process recovery proof. The controlled no-persistence
baseline receives the same memory inputs and loses the observation after process exit;
it neither rebuilds the graph elsewhere nor measures a fictitious speedup. Removing
FalkorDB disables core risk, memory and workflow reconstruction and readiness reports the
dependency failure; liveness and configuration-only endpoints can remain available.

These results support a demonstrable Track 2 architecture, not a prediction of judging
outcome. Code and fixtures are project work; FastAPI/FalkorDB/LangGraph/Boto3 and other
dependencies are third-party open source, as listed in [attribution.md](attribution.md).
The repository has no commits, so this review cannot independently establish hackathon
creation dates/originality from Git history. Do not represent upstream libraries, synthetic
evidence or the auditor's local measurements as original cloud incidents or external proof.

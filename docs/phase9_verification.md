# Phase 9 verification

Verified 17 October 2026 (Asia/Calcutta), using Python 3.12.12, official FalkorDB client 1.8.0,
FalkorDB server 4.22.1, LangGraph 1.2.15 and Boto3/Botocore 1.43.114. Runtime and development
locks include the AWS SDK dependency tree. No real AWS credential, profile session, cloud
resource or LLM API was used for verification.

## Automated checks

The final suite passed **417 tests, zero skipped, in 237.90 seconds**: **188 real FalkorDB
integration tests and 229 other tests**. The 41 Phase 9 tests use synthetic SDK responses; the S3 request
contract additionally uses Botocore Stubber with the real SDK service models and no network.
Mocks are confined to tests. FalkorDB integration tests use a separate nonpersistent server,
unique per-test graph names and exact graph cleanup.

New coverage verifies:

- Disabled AWS, default local reports and status inspection never construct SDK sessions.
  Credential, endpoint and bucket override fields in request bodies are rejected without
  echoing their values. Disallowed IAM/security write operations fail before client creation.
- Account mismatch prevents IAM collection. Source ARN and CloudTrail account/region scope
  are validated; graph repositories reject unrelated-tenant reads.
- IAM roles, trust, attached/inline policies and managed default/non-default versions are
  normalized. Repeated requests return committed results without another SDK call. Multiple
  roles share one managed policy and its grants without duplicate grant edges.
- A new complete IAM inventory closes the previous half-open observation intervals; bounded
  scan failure leaves existing inventory unchanged. A fresh database connection retrieves
  the same persisted import. No trust-only CAN_ASSUME edges are fabricated.
- Condition operands, tags, request/response content and credential fields are discarded
  before graph storage. Unsupported denies mark the attached policy unresolved rather than
  silently disappearing; accountless resource references require unresolved resource policy.
- Marker/NextToken pagination, repeated-token rejection, CloudTrail pacing, bounded SDK retry
  configuration and safe throttling errors work. CloudTrail duplicate events deduplicate
  across pages and imported windows while retaining event time, API, region and provenance.
- Private S3 publication requires account/owner match, all four bucket public-access blocks,
  non-public policy status and owner-only ACLs. The dispatcher enforces the configured bucket.
  Uploads use encrypted conditional creation, hash metadata and no ACL/public URL; conflicting
  existing content fails instead of overwriting it.
- Local and mocked S3 report publication stores metadata and investigation relationships in
  FalkorDB, never report bytes. Exported JSON omits names, account IDs, ARNs and raw evidence.
  Repeated published requests return the same result without another upload.

All existing temporal, risk, memory, coordination, investigation and remediation tests pass.
Ruff lint and formatting pass for 128 Python files; `pip check` reports no broken requirements.
All 11 PowerShell scripts parse, and `docker compose config --quiet` passes.

Host test command (after starting the isolated authenticated FalkorDB test server):

```powershell
Set-Location backend
$env:FALKORDB_HOST = '127.0.0.1'
$env:FALKORDB_PORT = '16380'
$env:RUN_INTEGRATION_TESTS = 'true'
$env:AWS_ENABLED = 'false'
$env:CLOUDTRAIL_ENABLED = 'false'
$env:EXPORT_MODE = 'local'
$env:AWS_EC2_METADATA_DISABLED = 'true'
.\.venv\Scripts\python.exe -m pytest -q
Set-Location ..
```

The ignored local `.env` supplies the private graph password. It is never printed or included
in proof artifacts. Setting a test graph name alone does not isolate a persistence log;
negative constraint tests remain on the separate nonpersistent server as in earlier phases.

## Actual local HTTP, graph and artifact proof

The backend was restarted with final code, AWS/CloudTrail disabled, local export and the
existing mock provider. It listens at `http://127.0.0.1:18000`; graph
`shadowchain_phase9_verified` runs on the private authenticated persistent FalkorDB server.
Health returned `ok`, readiness returned `ready`, and AWS status reported the disabled defaults.
Startup performed no AWS synchronization.

The existing trusted synthetic investigation fixture completed the six-agent workflow and
created a full case. POST `/api/v1/reports`, a repeated identical POST and metadata GET all
returned the same PUBLISHED report. The [generated local proof](aws_local_export_proof.json)
records report `671f6380-b5a0-5a1d-b91e-18b4d12a15fe`, **7,385 bytes**, SHA-256
`6578977b46c079dc8d5858b128ebf34222f786994b11bc45ed7fe5767a6088e8`.
The [actual redacted JSON](redacted_report_example.json) is a copied demonstration artifact,
not a runtime source of case state. Its hash matches the local file and graph metadata.

After backend restart, a separate fresh FalkorDB connection returned identical report
metadata, with no `record_json` report body. Schema version 9 was verified. All three new
Cypher diagnostics in [graph_schema.md](graph_schema.md) executed successfully; AWS queries
returned empty results in this synthetic-only graph, and the report-to-case query returned
the saved publication. AWS data-bearing behavior is covered by mocked-source/real-graph tests.

The new `scripts/demo-export.ps1` also passed against the running API. It explicitly requires
local export mode, publishes/retrieves a synthetic report twice and checks stable IDs/hashes.
Each request identity has its own report-specific pseudonyms, so different request IDs can
produce different hashes for otherwise identical case summaries. Earlier graphs and histories
were preserved. No AWS infrastructure was provisioned or security configuration changed.

## Verification limits

- **Live AWS checks were not run.** No configured account was explicitly authorized for live
  read-only verification. IAM permissions, real throttling, credential refresh, Event History
  coverage and private-bucket upload behavior therefore remain unverified against AWS.
  [AWS_SETUP.md](AWS_SETUP.md) provides explicit operator checks and reviewable permission
  templates; none was deployed automatically.
- **Docker image build/container execution remain unverified.** `docker compose build backend`
  was attempted and failed because the `dockerDesktopLinuxEngine` named pipe is unavailable.
  Compose syntax validates, but image build, named report-volume ownership and container
  recreation need verification once Docker Desktop's engine works. Local Python and the
  existing extracted pinned FalkorDB server under WSL were used successfully.
- Reports are **JSON summaries**. PDF rendering is not implemented; the requested JSON export
  option is supported. S3 uses SSE-S3 AES256, not SSE-KMS, and requires a pre-existing bucket.
- IAM scans are observations across multiple API calls, not atomic AWS snapshots or a complete
  effective-permission evaluator. Unknown constraints are conservative and may omit paths;
  missing paths do not certify safety. Event History is a bounded regional subset, not a full
  audit log. Findings remain POTENTIAL ATTACK PATHS.
- Export publication and graph metadata are separate systems. PREPARED/hash reconciliation
  handles explicit retries; PUBLISHED records the last successful write, not later artifact
  availability. Partial/conflicting local files require operator inspection rather than
  automatic overwrite. OS directory permissions and external bucket administration remain
  operator responsibilities.
- Development controls retain server-fixed tenants and are disabled in production. They are
  not a replacement for production authentication. AWS data does not silently enter the
  synthetic risk/agent HTTP tenant. No frontend or IAM remediation executor was added.

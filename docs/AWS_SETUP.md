# Optional AWS integration

AWS is optional. The default is `AWS_ENABLED=false`, `CLOUDTRAIL_ENABLED=false`,
`EXPORT_MODE=local`. Startup, readiness, demo replay, six-agent analysis and local JSON report
exports do not construct an AWS session or query the credential provider chain. No AWS
infrastructure is provisioned. No IAM create/update/delete API exists in the dispatcher.

## Configuration and credentials

Use an existing AWS CLI profile or the standard Boto3 credential chain. Do not put credentials
in application request bodies, graph properties, source files, reports or logs. There are no
AWS access-key settings in `.env.example`. Profiles may use IAM Identity Center/SSO, existing
role credentials or another approved provider; provider authentication/refresh is handled by
the SDK. Application allowlists govern its service operations, not your profile's own
credential-acquisition mechanism.

| Setting | Default | Meaning |
| --- | --- | --- |
| `AWS_ENABLED` | `false` | Explicit permission to construct AWS SDK clients on a user-triggered action |
| `AWS_PROFILE` | empty | Existing CLI profile; empty uses the standard provider chain |
| `AWS_REGION` | `us-east-1` | Event History and S3 endpoint region; IAM observations are global |
| `AWS_ACCOUNT_ID` | empty | Required 12-digit expected account when AWS is enabled |
| `AWS_TENANT_ID` | `shadowchain-aws-import` | Server-owned graph scope, separate from the synthetic demo |
| `CLOUDTRAIL_ENABLED` | `false` | Separate opt-in to Event History reads |
| `EXPORT_MODE` | `local` | `local` or explicit `s3` export |
| `S3_EXPORT_BUCKET` | empty | One pre-existing, privately configured bucket; required for S3 mode |
| `EXPORT_LOCAL_DIR` | `artifacts/reports` | Admin-configured local artifact directory; no request-selected path |
| `AWS_MAX_RECORDS` | `300` | Per-action SDK call budget and normalized graph-node/result bounds, maximum 1,000 |
| `AWS_MAX_PAGES` | `20` | Per-list pagination bound, maximum 100 |

An IAM import is deliberately bounded for a small demonstration account. The node budget
includes policy versions, statements and resource references, so it is not a promise to
import 300 roles. Oversized or incomplete scans fail with 422 without replacing the current
inventory. SDK standard retry mode permits at most three attempts per request, with bounded
socket/connect timeouts. Authorization failures/throttling surface safe generic errors;
raw SDK error messages, IDs and response payloads are not logged.

Account confirmation is mandatory in each synchronization request. STS GetCallerIdentity must
match `AWS_ACCOUNT_ID` before inventory/events are read. Role ARNs and CloudTrail recipient
accounts/regions are validated before import. AWS-managed policies attached in that account
are a deliberate exception to policy-ARN ownership; arbitrary foreign customer policies are
rejected. SDK endpoint override URLs are ignored by client configuration. Callers cannot
choose credentials, tenant, bucket, endpoint or AWS operation through request fields.

## Exact application operation allowlist

| Service | Permitted Boto3 operations |
| --- | --- |
| STS | `get_caller_identity` |
| IAM | `list_roles`, `get_role`, `list_attached_role_policies`, `list_role_policies`, `get_role_policy`, `get_policy`, `list_policy_versions`, `get_policy_version` |
| CloudTrail, separately enabled | `lookup_events` |
| S3, S3 export mode only | `get_public_access_block`, `get_bucket_policy_status`, `get_bucket_acl`, `put_object`, `head_object` |

IAM write operations, bucket/policy creation, ACL changes, CloudTrail provisioning and
security-configuration changes fail before SDK dispatch. S3 calls also enforce the single
configured bucket and expected owner at the dispatch boundary. `put_object` is the only
application AWS write and accepts the redacted report artifact through the export service.
No periodic sync, startup AWS worker or automatic remediation is registered.

## Least-privilege policies

[aws-readonly-policy.json](aws-readonly-policy.json) is a reviewable template. Replace
`<ACCOUNT_ID>` and `<AWS_REGION>` before an administrator attaches it to your existing
read-only identity. Remove the CloudTrail statement if you do not enable Event History.
The application never attaches policies or changes its own privileges.

- `iam:ListRoles` uses `Resource: "*"`; role detail/inline reads are scoped to that account's
  `role/*` resources.
- Managed-policy reads are scoped to the account's `policy/*` plus AWS-managed `aws:policy/*`.
  Reading non-default versions requires `iam:ListPolicyVersions` and `iam:GetPolicyVersion`.
- `cloudtrail:LookupEvents` uses `Resource: "*"` with the configured requested-region condition.
- No IAM write, user/access-key inventory, trail creation, CloudTrail Lake or Organizations
  permission is required by these readers.
- STS documents GetCallerIdentity as not requiring an explicit permission grant. Your
  existing profile's login/role-assumption permissions remain separate. See
  [GetCallerIdentity](https://docs.aws.amazon.com/STS/latest/APIReference/API_GetCallerIdentity.html).

An account-wide role scan requires permission for all returned roles; a restrictive role-path
policy can make the entire scan fail rather than silently produce a falsely complete view.
For GovCloud/China, replace the ARN partition in templates appropriately and use a matching
profile/region. Existing organization policies, permissions boundaries or explicit denies may
still prevent these reads. Templates are not automatically deployed.

For optional S3, use the separate
[aws-s3-export-policy.json](aws-s3-export-policy.json) after replacing `<BUCKET>`. This adds
private-bucket inspection, `s3:PutObject` and `s3:GetObject` only under `shadowchain-reports/*`.
HEAD verification uses GetObject authorization. New objects use conditional `If-None-Match: *`,
so ListBucket is not needed merely to distinguish an absent object from an inaccessible one.
There is no delete, ACL/policy mutation, ListBucket or bucket-creation permission in this template.
The PutObject statement requires HTTPS and SSE-S3 `AES256`.

## Windows PowerShell verification and explicit enablement

These commands are operator actions, not automatically executed setup. Use the existing
profile you are authorized to read. Account output below is for local verification; do not
paste credentials or unredacted inventory into logs/public artifacts.

```powershell
aws --version
aws sts get-caller-identity --profile shadowchain-readonly --query Account --output text
aws iam list-roles --profile shadowchain-readonly --max-items 1 --query 'length(Roles)' --output text
aws cloudtrail lookup-events --profile shadowchain-readonly --region us-east-1 `
    --lookup-attributes AttributeKey=EventSource,AttributeValue=iam.amazonaws.com `
    --max-results 1 --no-paginate --query 'length(Events)' --output text
```

If an SSO session needs renewal, perform your existing organization's approved CLI login
outside the application. Do not create a new access key just for this demo.

Set these values in your local ignored `.env` only after account verification:

```dotenv
AWS_ENABLED=true
AWS_PROFILE=shadowchain-readonly
AWS_REGION=us-east-1
AWS_ACCOUNT_ID=<YOUR_12_DIGIT_ACCOUNT_ID>
CLOUDTRAIL_ENABLED=false
EXPORT_MODE=local
```

Restart the backend to apply configuration. A host-side, explicit read-only check is available:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m app.integrations.aws_cli --authorize-read-only `
    --confirm-account-id <YOUR_12_DIGIT_ACCOUNT_ID>
Set-Location ..
```

It validates STS and reads a one-role sample, prints counts only, and performs no graph import
or infrastructure write. Missing authorization flag or disabled configuration prevents it.
No live check was run during implementation because live account authorization was not given.

Compose does not automatically mount host credential files or forward access keys. To use an
existing profile in a container, explicitly supply your approved read-only credentials/config
mount in a private Compose override (including any necessary SSO cache path), or use workload
identity. Do not copy credentials into the image. Host Python is the simplest profile-based
development path. The image's backend user is UID 10001; ensure it can read your chosen mount.

## User-triggered synchronization

The API binds AWS tenant/account from settings, rejects principal/credential overrides, and
disables these local control routes in production. Posting a synchronization request is the
explicit action to read the confirmed account. Phase 10 adds authenticated service keys;
keep the backend bound to loopback; public identity federation and deployment controls remain separate work.

For synchronization, create a separate API principal bound to the configured AWS tenant:

```powershell
& .\scripts\setup-api-auth.ps1 -PrincipalId aws-reader -TenantId shadowchain-aws-import `
    -Permissions @('read','write','aws:sync')
# Restart backend to load the new digest. This does not create an AWS identity or permission.
. .\scripts\api-client.ps1
$headers = Get-ShadowChainHeaders -PrincipalId aws-reader
```

The API `write` permission authorizes a graph import; it grants no AWS IAM write operation.
Changing profiles/flags does not bypass tenant or account confirmation. See
[API_CONTRACT.md](API_CONTRACT.md) for key storage, authorization and revocation.

```powershell
$base = 'http://127.0.0.1:8000/api/v1'
Invoke-RestMethod -Headers $headers "$base/aws/status"  # flags only; no AWS session or account/profile output
$body = @{request_id='operator/iam-1'; confirm_account_id='<YOUR_12_DIGIT_ACCOUNT_ID>'} | ConvertTo-Json
$sync = Invoke-RestMethod -Headers $headers "$base/aws/sync/iam" -Method Post -ContentType 'application/json' -Body $body
Invoke-RestMethod -Headers $headers "$base/aws/imports/$($sync.run_id)"
```

IAM roles, trust, managed/inline policies and version evidence are normalized into FalkorDB.
Current default versions supply modeled policy/grant/reference edges; non-default versions
remain evidence. A complete import closes prior IAM observation intervals and creates the
new snapshot atomically. Failure leaves the previous snapshot intact. Observations are
versioned per run, retain original ARN/source IDs, and use half-open validity intervals.
The IAM observation event is labeled `AwsIamInventoryObserved`; it is not a claim about the
source time of an actual configuration change. Reusing the same request ID retrieves the
committed result without contacting AWS again; use a new ID for a new scan.

Condition operands, tags and arbitrary descriptions are omitted because they may contain
secrets. Conditions remain explicitly unresolved. Any unsupported NotAction/NotResource/
NotPrincipal statement, wildcard resource scope or foreign/unsupported resource selector
marks the attached policy `UnresolvedPolicy`; the evaluator cannot silently ignore an
unmodeled deny while accepting another grant. References have unknown resource sensitivity;
accountless ARNs (including S3) also require an unresolved resource policy because a policy
reference does not establish resource ownership. This conservative import can miss possible
paths and does not certify that an account is safe. In particular,
trust alone never creates a CAN_ASSUME edge. The import is not a complete AWS authorization
assessment. No event import is automatically applied as a permission change or sent to an
AWS executor. Existing synthetic agent/risk endpoints retain their synthetic tenant binding.

## CloudTrail coverage

Set `CLOUDTRAIL_ENABLED=true` separately, restart, then request a bounded UTC window:

```powershell
$end = [DateTimeOffset]::UtcNow.AddMinutes(-1)
$body = @{request_id='operator/cloudtrail-1'; confirm_account_id='<YOUR_12_DIGIT_ACCOUNT_ID>';
    start_time=$end.AddHours(-1).ToString('o'); end_time=$end.ToString('o')} | ConvertTo-Json
$events = Invoke-RestMethod -Headers $headers "$base/aws/sync/cloudtrail" -Method Post `
    -ContentType 'application/json' -Body $body
$events.coverage
$events.limitations
```

Event History covers the most recent 90 days of management events in one account/Region; it
does not supply unlimited history, object data events or organization-wide aggregation.
See [CloudTrail Event History](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/view-cloudtrail-events.html).
The reader queries IAM/STS/S3 event sources separately and keeps a closed security-event name
allowlist. Other events are skipped and counted. Global IAM activity commonly requires checking
the appropriate global-service recording region, usually `us-east-1`; this importer does not
fan out across every region or certify that no other events occurred.

LookupEvents is paginated, at most 50 results per page, and limited to two requests/second
per account/Region. The reader paces calls, retains identical parameters across NextToken
pages, and rejects repeated tokens/over-limit scans. Concurrent processes still share AWS's
quota and may receive bounded-retry throttling errors. See
[LookupEvents](https://docs.aws.amazon.com/boto3/latest/reference/services/cloudtrail/client/lookup_events.html).

Normalized SecurityEvent/Evidence records preserve account, region, event ID/time, source
API and identity/session-issuer ARN references. Same-account actors are graph Identity
references; foreign actors stay contextual evidence rather than imported foreign inventory.
Event IDs deduplicate across repeated windows. Raw CloudTrail JSON, AccessKeyId, request/
response parameters, session credentials, IP addresses and user agents are discarded.
Coverage explicitly states `complete_audit_history=false` and the queried source/time scope.

## Redacted JSON reports and optional S3

Local export works with AWS entirely disabled:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-export.ps1
# Or export an existing case directly:
$base = 'http://127.0.0.1:8000/api/v1'
$headers = Get-ShadowChainHeaders  # default synthetic operator, not the AWS-import principal
$cases = Invoke-RestMethod -Headers $headers "$base/investigations"
$body = @{request_id='operator/local-report-1'; investigation_id=$cases.investigations[0].investigation_id;
    format='json'} | ConvertTo-Json
$report = Invoke-RestMethod -Headers $headers "$base/reports" -Method Post -ContentType 'application/json' -Body $body
Invoke-RestMethod -Headers $headers "$base/reports/$($report.report_id)"
```

This phase supports JSON reports. The renderer allowlists status/severity, counts, timestamps,
classification and per-report pseudonymous references. It omits names, account IDs, ARNs,
source identifiers, IPs, free-text reasons/summaries, raw evidence and credentials. It does
not rely on regex replacement to clean an arbitrary report document. Files contain explicit
authorization uncertainty and `execution_allowed=false`.

Local files go to the configured directory with opaque filenames. Protect that directory
using your OS permissions; it is ignored by Git. Compose uses a dedicated report artifact
volume configured for the non-root backend (container ownership remains unverified on this
workstation; see the verification report). FalkorDB stores hashes, byte count, MIME,
destination, publication status and SUPPORTS links to investigations, never file bodies.

For S3, select one **existing** private bucket in the configured account/region, add only the
optional export policy, and configure:

```dotenv
AWS_ENABLED=true
EXPORT_MODE=s3
S3_EXPORT_BUCKET=<YOUR_EXISTING_PRIVATE_BUCKET>
```

The same explicit POST `/reports` triggers upload. Before upload, the service verifies caller
account, expected bucket owner, all four bucket-level public-access-block settings, non-public
bucket-policy status and owner-only ACL grants. No bucket policy is acceptable when the other
checks pass; denied/unknown checks fail closed. Account-level blocking alone does not satisfy
this intentionally strict bucket-level gate. No bucket setting is changed automatically.

Uploads use `shadowchain-reports/<opaque-report-id>.json`, checksum metadata, conditional
creation, HTTPS and SSE-S3 AES256, without ACL arguments or presigned/public URLs. A bucket
policy requiring SSE-KMS will reject this implementation; SSE-KMS is not configured here.
Existing different report bytes are never overwritten. A lost upload acknowledgment can be
reconciled using object metadata after the conditional-create conflict.

Exports use PREPARED/PUBLISHED graph metadata. A failed upload/file write leaves PREPARED
for explicit retry. If the source summary changed before retry, return 409 and use a new
request ID. Incomplete or conflicting local files are not overwritten. PUBLISHED records
the successful publication, not continuous monitoring of later external deletion. Files are
artifacts, not an alternative store for investigations or coordination.

## Expected costs and disabling

With defaults there are **no AWS calls or AWS charges from this integration**. IAM inventory
uses ordinary IAM read operations. CloudTrail Event History viewing/lookup is available at
no CloudTrail charge; trails, data events and CloudTrail Lake are different products and this
application creates none. See [CloudTrail pricing](https://aws.amazon.com/cloudtrail/pricing/).

Optional S3 incurs regional storage and request charges: each new report has a PUT plus three
private-bucket checks; retries can add HEAD/PUT requests. As a scale example, 100 reports of
10 KiB are about 0.98 MiB stored and 100 new-object PUTs, plus verification reads. Apply your
region/storage-class rates, taxes, versioning/replication and any existing bucket features
from [S3 pricing](https://aws.amazon.com/s3/pricing/). No fixed cost or free-tier eligibility is
assumed, and no retention/lifecycle policy is provisioned by the application.

To disable, set `AWS_ENABLED=false`, `CLOUDTRAIL_ENABLED=false`, `EXPORT_MODE=local`, clear
`S3_EXPORT_BUCKET`, and restart the backend. Existing graph observations/reports remain;
disabling does not erase evidence or delete S3 objects. Demo behavior and local reports keep
working. Revoke any optional export permission separately through your normal administration.

For graph contracts, mocked tests and actual local verification, see
[Phase 9 verification](phase9_verification.md). Real AWS checks are intentionally unverified
until a configured account and explicit read-only authorization are provided.

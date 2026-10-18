# Railway and Vercel deployment

> Current status (2026-10-18): the website now reads real public GitHub inventory
> from `deveshsinghal09/shadowchain-ai`. AWS remains disabled. See the latest
> GitHub verification section below; older empty-graph receipts are historical.

Deployment date: 2026-10-17. These are operator deployments from the local checkout, not Git autodeployments. No Git remote is configured.

## Addresses and ownership

| Component         | Address or identifier                                             |
| ----------------- | ----------------------------------------------------------------- |
| Website           | https://shadowchain-ai.vercel.app                                 |
| Vercel project    | `zodiac3/shadowchain-ai`, Devesh's connected CLI account          |
| Railway dashboard | https://railway.com/project/2f4a53bd-bd2a-4db6-8098-45fe33570a01  |
| Explorer API      | https://graphverse-api-production.up.railway.app                  |
| Python API        | https://shadowchain-api-production.up.railway.app                 |
| FalkorDB          | Private Railway service `falkordb`; no public domain or TCP proxy |

Vercel proxies `/api/v1/*` to the Python service and other `/api/*` routes to the explorer service. The browser uses same-origin requests and an HttpOnly, Secure, SameSite=Strict session cookie for the explorer. Python API authentication remains a separate scoped bearer principal. Frontend credentials do not automatically authorize Python API requests.

## Public website and operator access

Anyone can open the website and browse without a token when `GRAPHVERSE_PUBLIC_READ=true`, as configured on the hosted explorer. Public routes include graph snapshots, entities, neighbors, clusters, bounded paths and deterministic graph questions. Public questions never invoke Gemini. Saved investigation lists, preserved paths, writes and model-provider calls still require the operator session. The Python API authorization remains unchanged. Only enable public reads for a graph whose contents you intend visitors to see.

To use operator features, run this PowerShell command on the deployment computer:

```powershell
Set-Location 'E:\OneDrive\Documents\ChatGPT\Graph Hack\shadowchain-ai'
.\scripts\copy-hosted-token.ps1
```

Paste the copied value into the website's **Operator token** dialog. This is the hosted operator token, separate from the original local development token. It grants access to the one configured explorer tenant. It is not a Gemini key or an AWS key.

Production tokens are stored outside the repository under `%LOCALAPPDATA%\ShadowChainAI\railway-2f4a53bd-bd2a-4db6-8098-45fe33570a01`. They are not embedded in the frontend. The token can also be retrieved or rotated in Railway's `graphverse-api` service Variables as `GRAPHVERSE_OPERATOR_TOKEN`. Treat it as a password.

## Where to paste the Gemini key

1. Open the Railway dashboard linked above.
2. Select **graphverse-api**, then **Variables**.
3. Add `GEMINI_API_KEY` and paste your Google AI Studio API key as its value.
4. Set `GRAPHVERSE_AI_PROVIDER=gemini`.
5. Set `GEMINI_MODEL` to a supported model available to your Google project. The current explorer default is `gemini-3.1-flash-lite`, confirmed in the key's model listing. Generation and account quota are checked separately.
6. Apply the staged variable changes and deploy/redeploy that service.

The hosted explorer provider has been configured as `gemini`; public questions still use deterministic graph answers, while only signed-in operators can invoke the configured provider. Gemini adds prose over bounded graph-derived counts with validated citations. It cannot supply graph paths, execute commands or change AWS. With no graph evidence, it makes no model request. The current graph is empty, so adding a key alone will not produce an evidence-based explanation.

For the separate Python agent provider, the relevant service is **shadowchain-api**. Its variables are `GEMINI_API_KEY`, `AGENT_PROVIDER=gemini`, and `GEMINI_MODEL`. Its existing demo-bound workflow routes remain restricted in production; setting a provider does not enable those routes.

Do not put the key in Vercel, a `VITE_*` variable, source code, the graph or chat. A live Gemini response has not been verified with graph evidence, even though the operator configured the key. Google project billing/quotas may need external configuration. Bedrock is not used.

## Storage and production settings

- FalkorDB 4.22.1 uses the repository's pinned official image, AOF with `appendfsync everysec`, periodic RDB snapshots, and a persistent Railway volume mounted at `/data`.
- The observed volume is 500 MB. Capacity monitoring and backups need operator configuration. AOF with `everysec` can lose approximately one second of recent writes after an abrupt failure.
- Database authentication is required; dependent services use Railway variable references and private DNS. No public database networking was created.
- Python runs with `APP_ENV=production`, `DEMO_MODE=false`, `AWS_ENABLED=false`, `CLOUDTRAIL_ENABLED=false` and `EXPORT_MODE=local`.
- Explorer production startup creates indexes only. It does not seed synthetic data.
- The explorer uses graph `graphverse_live_v1`, tenant `shadowchain-live`, dataset marker `live-unconfigured`. Python uses graph `shadowchain` in the same FalkorDB instance.
- A shared operator login is suitable for an operator-controlled deployment. Individual user accounts, revocation, organization onboarding and broader public access require further authentication work.
- Session signing state rotates on an explorer process restart, requiring a fresh login. Domain data remains in FalkorDB. Multiple explorer replicas require shared signing configuration before scaling out.
- Railway and Vercel consumption are subject to the account's plan and charges. No plan upgrade was performed.

## Current functionality and remaining live integration

Verified after deployment: website delivery, frontend proxying, both API health endpoints, real FalkorDB readiness, explorer login and authenticated empty graph reads. The scoped Python principal can read the dashboard summary and graph snapshot. Unauthenticated private-data and write requests fail closed; the hosted explorer now explicitly permits public graph reads.

This deployment does **not** yet provide live AWS monitoring. The explorer graph is intentionally empty. AWS profile/configuration on a developer laptop is not automatically available in Railway. A reviewed read-only credential setup and live schema adapter are still needed to connect AWS imports to the explorer. Existing Python AWS synchronization, agent and memory control routes are development/demo-bound and remain restricted in production. Do not change `APP_ENV` to development to bypass those restrictions.

The two graph schemas are not yet connected by a live adapter. Configuring Gemini, AWS or database credentials alone does not complete that work. No cloud infrastructure, AWS permissions, synthetic access paths or actual AWS resources were created during this deployment.

The Vercel HTTP rewrite is not a verified WebSocket transport. A future live-event frontend should connect to a reviewed `wss://` API endpoint with its existing first-frame authentication protocol. Backend WebSocket behavior was not reverified in Railway during this deployment.

## Commands and verification

From `graphverse`, deployment used:

```powershell
npx --yes @railway/cli@5.70.0 up ../deploy/falkordb --path-as-root --service falkordb --detach --json
npx --yes @railway/cli@5.70.0 up . --path-as-root --service graphverse-api --detach --json
npx --yes @railway/cli@5.70.0 up ../backend --path-as-root --service shadowchain-api --detach --json
vercel project inspect --non-interactive
vercel deploy --prod --scope zodiac3 --yes --non-interactive
```

All three Railway services reported `SUCCESS`. Vercel reported `READY` and assigned the website alias above. Both platforms built the actual Docker/frontend sources successfully. Service variables and volume setup must exist before repeating these commands.

Local checks:

```powershell
npm run build
npm test
npm run format:check
npm run check
npm run test:e2e
npx tsx scripts/verify-deployment.ts
```

`npm test`: 16 passed, zero failed/skipped, including real FalkorDB graph integration, fresh-process recovery and four isolated Gemini-provider tests with mocked HTTP. The live Google provider was not contacted. Deployment smoke receipts and screenshots are written to ignored `graphverse/test-results/`; see that receipt for the browser checks actually completed. Historical Python test results in `BACKEND_COMPLETION_REPORT.md` are not a claim that the complete Python suite was rerun during hosting deployment.

`npm run test:e2e`: four local browser tests passed against the synthetic test walkthrough (1.4 minutes), covering graph questions, Hyperdrive, evidence save/reload, neighbor expansion, display controls, mobile and reduced motion. This test data was not copied to the hosted graph. `npm run build`, TypeScript checks and formatting checks passed. The Vercel dependency installation reported zero known vulnerabilities. An exact-value scan of four compiled HTML/JS/CSS files found none of the three newly generated deployment secrets; this is not a comprehensive secret audit.

Final frontend deployment: `dpl_DFDm4CyuuKU1WmhRGbMMDnidcCQM`, production alias `https://shadowchain-ai.vercel.app`. Railway services: `graphverse-api` deployment `85c5fcde-4ea5-49ec-9922-001d6177390c`, `shadowchain-api` deployment `2032882e-fa32-4816-8dbd-021f7a0b536d`, and persistent `falkordb` deployment `f09c25fb-386a-4d3e-9169-2b7be5fc329b`.

Final hosted browser smoke completed successfully at `2026-10-17T12:32:03.784Z`: health/readiness HTTP 200, both services rejecting unauthenticated data reads, secure session creation through Vercel, real empty FalkorDB graph retrieval, foreign-origin denial, desktop WebGL, login retained after reload, and mobile without horizontal overflow or JavaScript exceptions. Initial mobile smoke attempts timed out on the responsive status assertion during reload; the checker now permits 60 seconds and checks the attached status label, which mobile CSS hides. The successful final receipt and screenshots are in `graphverse/test-results/`. This timing allowance is not a measured production performance claim.

Operational limitations observed: Railway's host reports Redis memory-overcommit disabled. Backups, host failure recovery, load testing and multi-instance failover have not been verified. Railway CLI 5.70.0 warns that the checked-in `railway.json` config format is deprecated and supported until 2026-12-01; migrate it to Railway IaC before that deadline. The configured persistent volume was confirmed attached after a CLI volume command initially failed; service logs subsequently showed the volume mount and successful AOF initialization.

## Troubleshooting

### Public browsing update (2026-10-17)

The hosted explorer now has `GRAPHVERSE_PUBLIC_READ=true`. A visitor does not need an operator token to open the graph, inspect public entities, follow bounded paths or ask deterministic graph questions. Investigation lists, saved path history and graph writes remain operator-only. Anonymous questions never invoke Gemini, even when the operator provider is configured as Gemini. The Python `/api/v1` endpoints retain their existing authentication.

`npm run build`, TypeScript and formatting checks passed. The server suite passed 17 tests, including public access using a real isolated FalkorDB graph, anonymous write/history rejection, forged-cookie rejection and proof that public questions never call the injected model provider. All four local browser regression tests passed (1.3 minutes). Synthetic test data was not imported into the hosted graph.

Railway explorer deployment: `3e4be7f1-9591-4279-9675-bfe94c4e32dc`. Vercel production deployment: `dpl_Edqb8YMPm1sVBXj7etKSCd8yGRah`, published by the signed-in Devesh account. The first attempt was blocked because the shared HEAD commit author did not have Vercel deployment access. A real local commit for these changes under the Vercel owner's verified email resolved that check; existing teammate commits were not reattributed. That local deployment commit was not pushed, preserving 99 commits on GitHub. Deployment remains CLI-driven.

The hosted graph is still `live-unconfigured`; public access does not add real source data. Enable this setting only for a tenant graph whose contents may be public. The original operator-only hosted smoke above records the earlier access policy; the current smoke script checks the mode reported by `/api/session` and tests the public-first browser flow as well as protected operator operations.

The hosted browser smoke passed at `2026-10-17T13:52:17.919Z` with `public_browsing: true`. A fresh browser with no cookie opened the site without a token dialog, rendered the graph, and showed private-investigation controls appropriately. Signed-in operator access, persistence reads, origin rejection, desktop reload and mobile layout also passed. Anonymous `/api/ai/ask` returned `mode: deterministic`; no live Gemini response is claimed. The receipt remains in ignored `graphverse/test-results/deployment-receipt.json`.

### Common issues

- **Empty universe:** expected until a real inventory adapter is configured. Do not run `npm run seed` against the production graph to hide the empty state.
- **Invalid operator token:** use the hosted token rather than the original demo token. A process restart also invalidates existing sessions; sign in again.
- **Origin denied:** keep `GRAPHVERSE_ORIGINS` synchronized with the exact production HTTPS origin. Preview aliases are not automatically trusted.
- **Database unavailable:** inspect the private service, `/data` volume, credentials and private DNS. Keep the database private.
- **Gemini error:** verify the key, model access and quota in the server service. Provider error bodies are intentionally not exposed. Graph browsing remains available.
- **403/404 from workflow or AWS controls:** the current production route restrictions are intentional and require a production authorization adapter, not a configuration bypass.
- **Updates not appearing:** these are CLI deployments, not Git autodeployments; repeat the relevant upload/deploy command after checks pass.

## Live GitHub integration verification � 2026-10-18

The GitHub repository was made public at the user's request after a targeted
scan of 291 reachable committed blobs. Matches were dummy private-key header
strings in validation tests and recorded test names, without key bodies. This was
not a claim of exhaustive secret detection. The default branch is now `main`;
GitHub history remains at 100 commits before any further approved publication.

Railway explorer deployment `f1fdf3a6-6527-49ff-9219-29eecd472421` imported real
anonymous GitHub data. The hosted receipt at `2026-10-18T06:04:43.909Z` records
91 entities, 209 relationships, zero synthetic entities, six completed stages,
six persisted handoffs, cross-agent memory retrieval, actual graph traversal,
simulation breaking a path while preserving the original, saved investigation
retrieval, and duplicate import prevention.

The hosted browser check at `2026-10-18T06:06:26.200Z` passed public browsing,
private-route protection, secure operator login, imported WebGL graph rendering,
source task display, origin rejection, and mobile layout without JavaScript
exceptions. The recovery check at `2026-10-18T06:11:26.230Z` confirmed inventory,
completed task IDs/timestamps, and a saved investigation persisted after a real
Railway backend redeploy (`c23fd0b0-1a35-4071-bd3d-cd28f73a3c49`).

Vercel deployment `dpl_YqYvRhte4ckuNq3NQTv1g1CYQFig` is READY at the existing
production alias. The original Python health/readiness endpoints still pass,
and production settings were checked as `AWS_ENABLED=false`,
`CLOUDTRAIL_ENABLED=false`, and `EXPORT_MODE=local`.

Local verification completed:

- `npm run build`: TypeScript and Vite passed.
- `npm test`: 30 passed, zero failed/skipped; real isolated FalkorDB, mocked
  upstream failure cases, fresh-process task recovery, source reverts, temporal
  evidence, proposal decisions, tenant boundaries, and model-failure fallback.
- `npm run test:e2e`: six passed (2.4 minutes); four existing explorer tests plus
  public source activity and operator simulation/review/save/reload browser tests.
- `npm run format:check`: passed after formatting the new browser test.
- `npm audit --omit=dev --audit-level=high`: zero known vulnerabilities reported.
- `npx tsx scripts/verify-live-github.ts deveshsinghal09/shadowchain-ai`: real
  GitHub HTTP and real isolated FalkorDB passed; test graph removed afterward.
- `npx tsx scripts/connect-hosted-github.ts`: hosted import, graph evidence,
  saved investigation, simulation, and idempotent refresh passed.
- `npx tsx scripts/verify-deployment.ts`: hosted desktop/mobile browser smoke passed.
- `npx tsx scripts/verify-hosted-recovery.ts`: redeploy persistence passed.

The optional Gemini key can list models from Railway. Its previous configured
model (`gemini-3.8-flash`) was absent from that listing. It was changed to the
available stable `gemini-2.5-flash`. A usable model listing does not prove generation
quota. Optional model failure returns the deterministic graph answer with a clear
note rather than blocking evidence retrieval. No provider error body or key is
returned to the browser.

The mechanical UI detector reported two font-style warnings in the existing
Instrument Sans declarations. The established typography was retained; this was a
source/operation feature, not a visual redesign. These are not functional failures.

See [GitHub integration](GITHUB_INTEGRATION.md) for exact endpoints and coverage.
This is an operator-refreshed repository evidence system; automatic GitHub
monitoring, webhooks, complete dependency scanning, and effective permission
analysis are not implemented. The live TypeScript stages and separate Python
IAM/LangGraph workflows remain explicitly distinguished. Backups, failover,
individual user federation, and Railway config-format migration remain operating
work; no success is claimed for them.

### Final Gemini and persistence result

At `2026-10-18T06:20:37.061Z`, the hosted recovery script verified a real Gemini
explanation using `gemini-3.1-flash-lite` and graph-derived facts. The same check
confirmed all 91 entities, 209 relationships, completed task IDs/timestamps, and
the saved investigation remained available after the backend redeploys.

The initial `gemini-3.8-flash` configuration did not produce a verified response.
Although `gemini-2.5-flash` appeared in the model list, its generation request
returned HTTP 404. `gemini-3.1-flash-lite` generated a response, but the original
adapter rejected its documented `thoughtSignature` metadata. The adapter now
accepts that bounded opaque field on a text part, discards it, and still rejects
function calls, executable fields, ungrounded citations, and invalid summaries.
A regression test covers this response format. Provider metadata checks alone
still do not claim to verify generation; the actual question response did.

Latest verified server suite: 30 tests passed. The configured default and hosted
model are now `gemini-3.1-flash-lite`. The current explicit key is unchanged and
was never printed. An exact-value scan of four client files against four configured
secret values found zero matches; this is not an exhaustive secret audit.

Final backend code upload for the generic authorization wording:
`6a5681ef-e689-405e-b192-d4bd7d804bed`. Vercel remains at
`dpl_YqYvRhte4ckuNq3NQTv1g1CYQFig`; the later changes are server-only.

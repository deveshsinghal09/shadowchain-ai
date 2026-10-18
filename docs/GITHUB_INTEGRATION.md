# Live GitHub integration

ShadowChain reads the public `deveshsinghal09/shadowchain-ai` repository into the
hosted FalkorDB graph. AWS, CloudTrail, and Bedrock are not needed or enabled.
The GitHub source is separate from the optional Python IAM integration.

## Use it

1. Open [the website](https://shadowchain-ai.vercel.app/).
2. Open **GitHub source and agent activity** in the explorer dock.
3. Inspect the source commit, last checked timestamp, coverage, and persisted tasks.
4. To refresh, sign in as an operator and choose **Sync GitHub now**.
5. Trace an owner → repository → manifest → dependency relationship, inspect its
   evidence, and save it as an investigation.
6. In the path panel, simulate a relationship removal. Start human review and
   approve or reject the recommendation. Neither choice executes a source change.

Public browsing and deterministic graph questions require no token. Synchronizing,
saving, model-assisted explanations, and proposal review require an operator
session. Saved operator investigations remain private. Public source activity
contains only observations derived from the approved public repository.

## Server configuration

Configure these on Railway's `graphverse-api` service:

```dotenv
GRAPHVERSE_GITHUB_REPOSITORIES=deveshsinghal09/shadowchain-ai
GRAPHVERSE_DATASET=github-live-v1
GRAPHVERSE_PUBLIC_READ=true
```

`GITHUB_READ_TOKEN` is optional for increased API limits. Store it only as a server
secret; do not put it in chat, browser settings, or graph properties. The importer
rejects private repositories even if a configured token can read them. It uses
GET requests to a fixed `https://api.github.com` origin, rejects redirects, caps
response sizes, and bounds retries and request duration.

An empty repository allowlist disables synchronization. Callers cannot choose a
tenant, database, arbitrary URL, or repository outside that allowlist. No scheduled
polling or webhook is enabled: refreshes are explicitly operator-triggered.

## What is imported

- Public repository identity and its default-branch commit.
- Public owner/contributor references. Contribution is not an access permission.
- Up to 35 selected configuration-file and manifest references from the git tree.
- Up to three manifests and 60 safe package declarations; version ranges are not
  verified installed versions or evidence of a vulnerable package.
- A legacy branch-protection observation and an evidence-linked review signal
  when that endpoint reports `protected=false`.

The branch review explicitly requires checking repository rulesets and organization
controls. A false legacy flag does not establish that every protection is absent.
No raw source text, credential material, full manifest content, or executable
instructions are stored. Static source input never becomes an agent tool call.

## Persistent investigation stages

The live repository pipeline is a bounded TypeScript workflow in the explorer API.
It runs Cloud Sentinel, Identity Guardian, Risk Oracle, Memory Keeper, Coordinator,
and Response Architect as deterministic stages. It does not claim to be the
separate Python AWS/LangGraph workflow.

Every run, task, memory, and handoff is persisted in FalkorDB. Memory Keeper uses
graph traversal to retrieve other agents' observations about the repository.
Stage outputs retain source evidence IDs and the source commit. Episodic,
semantic, and procedural records link to evidence through graph relationships.
They are observations and review procedures, not instructions with execution rights.

Atomic guarded cursor transitions prevent repeated completed tasks. Repeating an
unchanged import reuses its stable fingerprint/run ID. Startup reconstructs
incomplete runs from FalkorDB. A newer source run supersedes an unfinished older
run rather than letting it attach new evidence to an old commit.

## Time and evidence

`valid_from` is the observation time, not an invented timestamp for when a GitHub
permission changed. A new import closes prior source relationship intervals using
`[valid_from, valid_to)`. Prior relationships and immutable inventory snapshots
remain in FalkorDB. Exact branch evidence is versioned by commit and observed flag.

`GET /api/graph/snapshots/{run_id}` returns the captured inventory. Graph difference
responses compare snapshots from the same repository. A change between two
observations does not, by itself, establish security causality.

## Endpoints

| Method | Route                                            | Access                                                           |
| ------ | ------------------------------------------------ | ---------------------------------------------------------------- |
| GET    | `/api/integrations/github`                       | Public when public browsing is enabled                           |
| POST   | `/api/integrations/github/sync`                  | Operator; body `{"repository":"deveshsinghal09/shadowchain-ai"}` |
| GET    | `/api/activity`                                  | Public source activity                                           |
| POST   | `/api/activity/{run_id}/resume`                  | Operator                                                         |
| GET    | `/api/graph/snapshots/{run_id}`                  | Public source snapshot                                           |
| GET    | `/api/graph/diff?before={run_id}&after={run_id}` | Public source snapshot comparison                                |
| POST   | `/api/what-if/remove-relationship`               | Operator; body `{"source":"id","target":"id","edge_id":"id"}`    |
| POST   | `/api/simulations/{id}/review`                   | Operator; status and review reason                               |

Both before/after simulation traversals execute in one Cypher read. Excluding an
edge is a parameterized hypothetical overlay; original inventory edges are not
changed. Proposals, results, and human decisions persist as separate graph records.
Allowed review transitions are `PROPOSED → UNDER_REVIEW → APPROVED / REJECTED`.
Approval is recommendation approval only.

## Verification

Commands run from `graphverse`:

```powershell
npm run build
npm test
npm run test:e2e
npm run format:check
npm audit --omit=dev --audit-level=high
npx tsx scripts/verify-live-github.ts deveshsinghal09/shadowchain-ai
```

The server tests use a real isolated FalkorDB graph and mocked GitHub HTTP for
deterministic failure and history cases. Browser fixture tests use the same split.
The separate `verify-live-github.ts` command makes real anonymous GitHub GET
requests, exercises persistence/traversal/what-if, then removes its isolated test
graph. It does not import fixtures into production.

The recorded real-source check imported 91 entities and 209 relationships, completed
six stages and six handoffs, retrieved cross-agent memory after reconnecting, and
verified hypothetical path removal while preserving the original path. Receipts
are written to ignored `graphverse/test-results/` as verification artifacts, not
as the application memory store. See the deployment record for hosted results.

## Coverage limits

This integration is a manually refreshed repository evidence explorer. It is not
complete GitHub security monitoring, a dependency advisory scanner, an effective
permission evaluator, or an AWS inventory system. Contributor results are bounded
to ten accounts. GitHub may truncate its tree response, and those limits are shown
in the source panel. There is no automatic permission modification, code execution,
secret scanning, code-scanning import, or CI execution.

Anonymous GitHub requests normally share an IP-based limit of 60 per hour. The
service reports upstream denial/rate limiting and preserves the last good graph.
See [GitHub rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api),
[git trees](https://docs.github.com/en/rest/git/trees), and
[repository contents](https://docs.github.com/en/rest/repos/contents).

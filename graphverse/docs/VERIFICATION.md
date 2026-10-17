# GraphVerse implementation and verification

Verification date: **2026-10-17**. Windows host, Node **22.14.0**, npm **10.9.2**, real FalkorDB **4.22.1**, official TypeScript client **6.8.0**. Browser verification uses Chromium with software WebGL; it is not a hardware FPS benchmark.

## Implemented

- React/TypeScript/Vite application and authenticated Express/TypeScript API in `graphverse/`.
- Fullscreen Three.js graph, procedural multi-depth stars and nebulae, instanced dust, celestial surfaces, spiral category regions, directional filaments, scoped particle pulses, selective bloom and camera transitions.
- One rendering owner: force-graph EffectComposer advances a non-rendering Fiber root and paused GSAP timelines. Drei billboards share that scene.
- Search, inspect/focus, rotate/pan/zoom, reset/fit, graph-backed neighbors, curated region filtering, bounded directed path discovery and exact per-edge evidence.
- Hyperdrive follows the database's ordered entity IDs, with pause/resume/skip/overview controls. Optional ten-second intro is immediately skippable. Reduced motion and three quality levels are available.
- Deterministic graph assistant, evidence constellation, persistent investigations and exact saved path snapshots.
- Explicit synthetic labeling, tenant-scoped repository queries, parameterized values, server validation, closed API surface, same-origin session protection, request/query bounds and credential-free client bundle.
- Self-hosted fonts; no external image dependency, AWS account or LLM key.

The existing Python application was not replaced. GraphVerse is a separate synthetic graph/schema; it is **not yet a visualization adapter for the existing Python risk/investigation API**.

## Actual commands and outcomes

Commands were executed inside `shadowchain-ai/graphverse` unless stated otherwise.

| Command                                                             | Observed result                                                           |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| `npm install --save-exact ...` / dev dependencies                   | Installed; exact direct versions and `package-lock.json` retained         |
| `npm run seed`                                                      | **59 entities / 118 edges**, real FalkorDB; repeat seed tested            |
| `npm run build`                                                     | Strict TypeScript check and production Vite build passed                  |
| `npm test`                                                          | **12 passed, 0 failed, 0 skipped** against a UUID-isolated real graph     |
| `npx playwright install chromium`                                   | Chromium installed successfully                                           |
| `npm run test:e2e`                                                  | **4 passed**, desktop and mobile flows against running Express + FalkorDB |
| `npm run format:check`                                              | Passed                                                                    |
| `npm audit --json`                                                  | **0 known vulnerabilities**; receipt in `dependency-audit.json`           |
| PowerShell parser on `scripts/start.ps1`                            | No syntax errors                                                          |
| `docker compose --env-file ../.env -f compose.yml config --quiet`   | Passed                                                                    |
| `docker compose --env-file ../.env -f compose.yml build graphverse` | **Blocked:** Docker Desktop Linux engine named pipe unavailable           |

The server test suite proves actual path traversal, same-input idempotent seeding, evidence references, tenant isolation, no-path directionality, a historical expired edge, duplicate investigation prevention, injection-resistant values and strict HTTP input checks. The unavailable-database test uses a real closed TCP endpoint and confirms a 503 without process termination or graph fallback. Another test launches **two separate Node processes**, writing in one and recovering the stored path in the other.

Browser tests exercise the production-built page, authenticate through the real HTTP API, submit a question, verify the returned five-node path, start/pause/resume/skip Hyperdrive, save an investigation, reload and reopen it. Other flows cover neighbors, curated regions, quality controls, a skippable intro and a 390×844 reduced-motion mobile viewport without horizontal overflow. Desktop screenshots use 1440×900. Screenshots are actual rendered application output, not mocks.

## Errors found and corrected during verification

- Index creation initially failed on repeat seed. Only the precise already-indexed error is now accepted; other failures propagate.
- FalkorDB requires `CONTAINS` as an operator; entity resolution now uses the supported syntax.
- Sorting after projecting away the path variable failed. The query now orders and limits `p` before projecting properties.
- The official client's query option is `TIMEOUT`; the server now passes that supported option.
- The official client forwards connection errors before callers can attach an instance listener. A narrow FalkorDB EventEmitter compatibility guard contains only an otherwise-unhandled `error` event; operation promises still reject and produce a 503. A closed-port test covers this behavior.
- Question entity order is preserved rather than alphabetically reversed.
- Returning from an expired session or a failed readiness poll marks the visible graph as a stale snapshot.
- Repeated graph expansion preserves layout coordinates. Heavy scene rendering is memoized; camera and shader animation avoid React state updates per frame.
- Labels use screen-size scaling, and reduced motion freezes nebula motion as well as stars and graph animations.

## Runtime used

Docker itself could not run. The already downloaded, digest-verified official FalkorDB image layers were restored into a local WSL chroot and the actual server was launched. It uses the existing persistent verification data mount, AOF/RDB, password authentication and **loopback-only** binding on port **16379**. GraphVerse uses `graphverse_demo_v1`, separate from the existing Python graphs. No alternative database was installed.

Express serves the built frontend and API on **http://127.0.0.1:3100**. The local `.env.local` contains only host/port and the private token **file path**. The actual token remains outside the repository. Setup does not publish the database or provision cloud resources.

## Limits and unverified items

- Docker configuration parses, but image build, container execution and container-volume recovery were **not verified**. AOF is enabled in the actual local runtime; this turn did not perform a power-loss or database-process restart durability test.
- The cross-process test verifies **investigation data recovery**, not continuation of a multi-agent workflow in this Node application. Agent observations and the handoff examples in this universe are explicitly synthetic records. The pre-existing Python multi-agent engine remains separate.
- The assistant is a bounded deterministic resolver, not a general LLM agent. Bedrock, S3 uploads and arbitrary natural-language analytics are not implemented.
- Cluster categories are manually assigned, not algorithmically discovered communities.
- Path discovery is bounded to six hops and excludes category membership. It reports modeled connections, not complete IAM effective permissions or proven exploitation.
- The UI supports a bounded demonstration graph, not an unbounded graph browser. No large-dataset, concurrent-load, GPU-memory or hardware 60 FPS claim is made.
- Background stars/dust are presentation only. They are not represented as graph entities or fabricated path steps.
- No public multi-user deployment, TLS proxy, per-user authorization, distributed quotas, cloud credential integration or production backup workflow was verified.
- The dependency audit is a dated advisory check, not a proof that the application has no security defects. Container OS packages were not scanned.
- The 457-test historical Python audit was not rerun as part of this new application task; its source was not edited.

See `README.md` for the demo sequence and `API.md` for the exact contract and queries.

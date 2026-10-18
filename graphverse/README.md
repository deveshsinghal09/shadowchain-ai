# GraphVerse

Hosted frontend: https://shadowchain-ai.vercel.app. The hosted deployment uses an
empty live graph with public browsing; the synthetic local walkthrough below is separate. See the
[Railway/Vercel guide](../docs/RAILWAY_VERCEL_DEPLOYMENT.md) for the hosted operator
access for private investigations/writes, server-side Gemini key setup, verification and remaining integration work.

A browser observatory for connected security intelligence. Ask how **Alice Chen** connects to **Research vault**, inspect the path returned by FalkorDB, travel along it, and save its evidence for another session.

This is a separate application inside the ShadowChain repository. It uses the requested **React / TypeScript / Vite / Three.js / React Three Fiber / Drei / react-force-graph-3d / GSAP / Tailwind / Zustand** frontend and **Express / TypeScript / official FalkorDB client** backend. The existing Python backend is preserved. GraphVerse uses its own `graphverse_demo_v1` graph, not a silent replacement or adapter for ShadowChain's Python schema.

## Run locally on Windows

Prerequisites: Node **22.12 or later**, npm, authenticated FalkorDB **4.22.1**, and the repository's private operator token. Node 22.14.0 was used for verification.

From the repository root:

```powershell
# Existing repository setup creates the private token if you have not done so.
.\scripts\setup.ps1

# Configure an already running local FalkorDB. Never publish its port publicly.
$env:FALKORDB_HOST = '127.0.0.1'
$env:FALKORDB_PORT = '16379'

# Seed is idempotent. This builds and starts the app at http://127.0.0.1:3100.
# CopyToken explicitly copies your existing private token for the login dialog.
.\graphverse\scripts\start.ps1 -CopyToken
```

Open **http://127.0.0.1:3100**, paste the operator token, and connect. Clear the clipboard after use. Tokens are exchanged for an hour-long **HttpOnly, SameSite=Strict** cookie. They are not placed in URLs, local storage, graph properties, or the built client bundle. Sessions require sign-in again after an Express restart; graph investigations survive.

Configuration order is process environment → `graphverse/.env.local` → repository `.env`. See [.env.example](.env.example). The repository `.env` may use the Compose-only hostname `falkordb`; native execution must override it with your actual local address. Do not place keys in `VITE_*` variables. The app binds loopback by default.

For development:

```powershell
.\graphverse\scripts\start.ps1 -Dev
# Vite: http://127.0.0.1:5173; same-origin /api proxy to Express :3100.
```

For manual commands inside `graphverse`:

```powershell
npm ci
npm run seed
npm run build
npm start
```

`GRAPHVERSE_TOKEN_FILE` must point to the private token file before starting Express. The PowerShell launcher derives this path using the existing repository helper. No AWS account or LLM API key is needed.

## Guided hackathon demonstration

1. Connect and skip the optional ten-second introduction, or let it finish while exploring.
2. Ask **How is Alice Chen connected to Research vault?**
3. Inspect the five entities and four directed relationships returned by the database. Expand **View evidence** at each step.
4. Choose **Travel this path**. Pause, resume, or skip. Hyperdrive follows the returned entity order.
5. Give the investigation a title and **Save to memory**.
6. Reload the browser, open **Memory orbit**, and recover the saved evidence. To demonstrate process durability, restart Express, sign in, and reopen it.
7. Search **Trust anomaly** and expand its neighbors to see the contradictory **Compensating control** memory and cross-agent READS relationship.
8. Explore **Cluster galaxies**, isolate a region, and return to the full universe. Categories are curated, not automatically discovered communities.

The dataset has **59 entities, 118 relationships and five curated regions**. All records are synthetic. The seed is the initial data source; subsequent requests query FalkorDB. No graph-shaped JSON fallback is shipped in the frontend.

## Architecture

Browser → same-origin Express API → parameterized Cypher → FalkorDB. Zustand holds transient presentation state only. Seed records, relationships, agent memory observations, saved investigations and immutable path evidence are in FalkorDB.

The force-graph component owns the only rendering loop and its EffectComposer. A custom composer pass advances a React Three Fiber root configured with `frameloop: 'never'`; a positive-priority `useFrame` prevents Fiber from issuing a second render. GSAP timelines are paused and manually advanced by that pass. Drei billboards, shaders, stars, instanced dust, galaxy objects, controls and bloom share the same Three.js scene and renderer. No independent GSAP or Fiber animation loop renders the scene.

The frontend supports search, rotation, zoom, pan, entity focus, one-hop expansion, region isolation/collapse, shortest directed paths, evidence panels, Hyperdrive, saved investigations, direction/label controls, three quality levels, optional bloom, adaptive downward pixel ratio, visibility pausing and reduced motion. Spatial separation has no geographic or astronomical meaning.

The assistant is explicitly **deterministic**. It resolves exact entity names in question order and executes fixed graph queries. With two entities it traces a path; with one it retrieves neighboring evidence. It does not support unrestricted natural-language analytics, generate Cypher, execute commands, or call AWS. Unknown questions receive a capability explanation.

## Verification

```powershell
npm test                         # real isolated FalkorDB graph; no skip/fallback
npm run build                    # strict TypeScript + production Vite build
npx playwright install chromium
npm run test:e2e                 # requires running app at :3100 and token file
npm audit
npm run format:check
```

See [verification report](docs/VERIFICATION.md) for actual outcomes and limitations, and [API contract](docs/API.md) for requests and responses. Browser tests authenticate through the API so raw operator tokens are absent from screenshots. Browser tests save a clearly named synthetic investigation in the demo graph. Server tests create and delete only UUID-named test graphs.

## Docker Compose

The isolated Compose file exposes only the app on **127.0.0.1:3100**. FalkorDB has no published ports, uses authentication, AOF and RDB persistence, and a named volume. Its network is internal. Existing ShadowChain Compose remains separate.

```powershell
# From graphverse; repository .env supplies the database password.
. ..\scripts\api-client.ps1
$env:GRAPHVERSE_TOKEN_FILE = Get-ShadowChainTokenPath
docker compose --env-file ../.env -f compose.yml config --quiet
docker compose --env-file ../.env -f compose.yml up -d --build
docker compose --env-file ../.env -f compose.yml exec graphverse npm run seed
```

Image building and Compose startup were **not verified** because the Docker Desktop engine was unavailable. Compose configuration was checked separately. The Node image uses the maintained `22-bookworm-slim` tag; resolve and pin an image digest before a controlled deployment. This is a private demonstration configuration, not a public multi-user service.

## Troubleshooting and limits

- **Cannot connect:** check FalkorDB host, port, password and readiness. If the database is down, graph operations fail with 503; there is no substitute database.
- **Empty universe:** run `npm run seed` against the same graph and tenant as the server. Do not seed the legacy Python graph name.
- **401 after restart:** sign in again; signing keys intentionally rotate with the server process.
- **403:** use the configured same-origin app URL. Set `GRAPHVERSE_ORIGINS` explicitly if changing ports or using HTTPS.
- **429:** wait one minute. The demo allows 120 API requests/minute/IP and eight login attempts/minute/IP.
- **Graph query limit:** the view is limited to 500 entities / 1200 edges, neighbors to 200, and path length to six hops. Larger views fail explicitly rather than silently presenting a complete graph. Query timeout is two seconds.
- **Slow graphics:** choose Performance, disable bloom and motion. The 60 FPS target is not a measured hardware guarantee; browser verification used software WebGL.
- **WebGL unavailable:** the app presents an explicit fallback; search and evidence panels remain usable.
- **Exposure:** these are modeled relationship paths, **not proven exploitable AWS access**. No complete IAM authorization evaluator is implemented in GraphVerse. The original Python engine remains separate.
- Public deployment needs TLS, secure cookies, managed user identity, per-user scopes, distributed throttling, deployment hardening, load tests and backups. Session scope currently maps to one configured synthetic tenant. No Bedrock or S3 integration is implemented in this application.

## Attribution

GraphVerse's application, procedural scene, dataset and integration code were developed for this task. The render/layout foundation uses [react-force-graph](https://github.com/vasturiano/react-force-graph) and the supplied [3d-force-graph reference](https://vasturiano.github.io/3d-force-graph/); these libraries are not represented as original work. See [THIRD_PARTY.md](docs/THIRD_PARTY.md). No NASA imagery or external generated imagery is included. This task does not verify hackathon dates or eligibility.

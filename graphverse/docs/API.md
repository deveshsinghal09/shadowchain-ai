# GraphVerse API, version 1

Express serves the requested `/api` paths. This contract is separate from the Python backend's `/api/v1` contract. JSON is required for writes, with an 8 KB limit and strict Zod validation. Identifiers match `[a-zA-Z0-9_-]{1,100}`. Operator requests use the HttpOnly session cookie; tenant scope is always server-configured.

With `GRAPHVERSE_PUBLIC_READ=true`, anonymous visitors can read `/api/session`, `/api/ready`, `/api/graph`, `/api/clusters`, and the node/neighbor endpoints, and POST to `/api/paths/shortest` or `/api/ai/ask` for read-only queries. Public questions always use deterministic graph results; they never invoke the configured model provider. `/api/session` reports `authenticated: false`, `access: "public"`, `can_write: false`, and `can_use_model: false` for a visitor. No anonymous session cookie is issued.

Saved investigations (including their lists and preserved paths), writes, and model calls require a signed-in operator. This flag exposes the configured tenant's graph contents to visitors; it does not accept a caller-selected tenant. Its default is `false`, preserving the private local configuration. Python `/api/v1` authentication is unchanged. Existing request limits, query bounds and origin validation apply in both modes.

| Method       | Route                          | Purpose                                                                                     |
| ------------ | ------------------------------ | ------------------------------------------------------------------------------------------- |
| GET          | `/api/health`                  | Public process liveness                                                                     |
| POST         | `/api/session`                 | `{ "token": "<private operator token>" }`, sets cookie                                      |
| GET / DELETE | `/api/session`                 | Inspect / end authenticated session                                                         |
| GET          | `/api/ready`                   | Authenticated actual database probe                                                         |
| GET          | `/api/graph`                   | Coherent bounded graph snapshot                                                             |
| GET          | `/api/nodes/:id`               | Entity attributes                                                                           |
| GET          | `/api/nodes/:id/neighbors`     | Bounded actual one-hop traversal                                                            |
| POST         | `/api/paths/shortest`          | `{ "source":"alice", "target":"vault", "directed":true }`                                   |
| POST         | `/api/ai/ask`                  | `{ "question":"Trace Alice Chen to Research vault" }`; optional explicit `source`, `target` |
| GET          | `/api/clusters`                | Graph-derived membership counts and curated category descriptions                           |
| GET          | `/api/investigations`          | Latest 100 saved investigations                                                             |
| POST         | `/api/investigations`          | `{ "id":"<UUID>", "title":"Review access", "source":"alice", "target":"vault" }`            |
| GET          | `/api/investigations/:id/path` | Preserved path snapshot and evidence                                                        |

In the default private configuration, all routes except liveness and session creation require authentication. Public mode opens only the explicit read/query allowlist above. POST investigation retries with the same ID and same inputs are idempotent; mismatched reuse returns 409. Only this route writes domain data. Graph seed runs from an explicit CLI command.

Entity shape: `id`, `label`, `type`, `cluster`, `description`, `severity`, `source`, `created_at`, `synthetic`, optional `degree`. The graph response includes `nodes`, `edges`, `snapshot_at`, `dataset`, `truncated`. Edge shape:

```json
{
  "id": "reader-role--POTENTIAL_READ--vault",
  "source": "reader-role",
  "target": "vault",
  "type": "POTENTIAL_READ",
  "evidence": "Synthetic grant added at 12:00 UTC. Boundaries, SCPs and conditions require verification.",
  "confidence": 0.72,
  "valid_from": "2026-10-16T12:00:00.000Z",
  "valid_to": null
}
```

Path responses add `found`, `ordered_node_ids`, `ordered_edge_ids`, `directed`, `explanation`, `query_ms`. Hyperdrive follows `ordered_node_ids`, never layout-nearest neighbors. `query_ms` measures server-side path request work, including endpoint checks, not pure Cypher execution time. A missing bounded path is a 200 response with `found:false`; a missing entity is 404. No confidence is a probability of exploitation.

The graph snapshot is the retained modeled graph; it may show historical relationships. Path discovery filters validity at query time using half-open intervals. There is no historical-time parameter in this GraphVerse API. Stored investigation snapshots retain the exact evidence returned when saved.

Core traversal (direction is selected from two fixed templates; values are parameters):

```cypher
MATCH p=(s:GVEntity {key:$source})-[:GVREL*1..6]->(t:GVEntity {key:$target})
WHERE all(n IN nodes(p) WHERE n.tenant=$tenant)
  AND all(r IN relationships(p) WHERE r.tenant=$tenant AND r.type <> 'MEMBER_OF'
    AND r.valid_from <= $at AND (r.valid_to IS NULL OR $at < r.valid_to))
WITH p ORDER BY length(p), [r IN relationships(p) | r.id] LIMIT 1
RETURN [n IN nodes(p) | properties(n)] AS nodes,
       [r IN relationships(p) | properties(r)] AS edges
```

This is a bounded graph-path query with a two-second server deadline. It is not a complete AWS authorization evaluation. Curated membership edges are excluded so membership in a category cannot masquerade as an access relationship. Undirected mode is available through the API and preserves original edge source/target; the frontend's security discovery uses directed mode.

Persistent memory retrieval:

```cypher
MATCH (i:GVInvestigation {key:$key})
WHERE i.tenant=$tenant
RETURN i.path_json AS path
```

`GVREMEMBERS` relationships also connect each investigation to its actual entities. The serialized preserved evidence is a graph property, not an external authoritative file.

Errors use `{ "error": { "code": "...", "message": "..." } }`: 400 invalid input, 401 invalid session, 403 origin rejected, 404 missing entity/route, 409 idempotency conflict, 413 size/view limit, 415 wrong media type, 422 no path to save, 429 rate limit, 503 database unavailable/query failure. Exceptions never expose connection credentials or Cypher parameters.

No raw-Cypher endpoint, AWS write API, document execution, autonomous tool execution, or LLM-generated graph path exists.

## Live public GitHub source

The hosted explorer now imports the approved public repository with operator-triggered synchronization. Source coverage, persistent agent stages, historical snapshots, graph differences, non-destructive proposals, and review endpoints are documented in [GitHub integration](../../docs/GITHUB_INTEGRATION.md). All source mutations and proposal reviews require an operator. Optional Gemini failures return the deterministic graph result with a provider-unavailable note.

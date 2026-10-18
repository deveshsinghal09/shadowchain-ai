import { createHash } from 'node:crypto';
import type {
  Answer,
  Cluster,
  Edge,
  Entity,
  GraphData,
  Investigation,
  PathData,
} from '../shared/types';
import type { Database } from './database';
import { settings } from './config';
export class HttpError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}
const cleanNode = (p: Record<string, unknown>): Entity => {
  const { key: _key, tenant: _tenant, ...node } = p;
  return node as unknown as Entity;
};
const cleanEdge = (p: Record<string, unknown>): Edge => {
  const { tenant: _tenant, ...edge } = p;
  return { ...edge, valid_to: p.valid_to ?? null } as unknown as Edge;
};
const info = () => ({
  snapshot_at: new Date().toISOString(),
  dataset: settings.GRAPHVERSE_DATASET,
  truncated: false,
});
export class Repository {
  constructor(
    readonly db: Database,
    readonly tenant: string,
  ) {}
  async simulations() {
    const rows = await this.db.query<{ result: string; status: string; created_at: string }>(
      'MATCH (s:GVSimulation) WHERE s.tenant=$tenant RETURN s.result_json AS result,s.status AS status,s.created_at AS created_at ORDER BY s.created_at DESC LIMIT 50',
      { tenant: this.tenant },
    );
    return rows.map((row) => ({
      ...JSON.parse(row.result),
      status: row.status,
      created_at: row.created_at,
    }));
  }
  async simulation(id: string) {
    const rows = await this.db.query<{ result: string; status: string }>(
      'MATCH (s:GVSimulation {key:$key}) WHERE s.tenant=$tenant RETURN s.result_json AS result,s.status AS status',
      { key: `${this.tenant}:${id}`, tenant: this.tenant },
    );
    if (!rows[0]) throw new HttpError(404, 'proposal_not_found', 'Proposal not found.');
    const decisions = await this.db.query<{ decision: Record<string, unknown> }>(
      'MATCH (s:GVSimulation {key:$key})-[:GVREVIEW]->(d:GVReview) WHERE s.tenant=$tenant AND d.tenant=$tenant RETURN properties(d) AS decision ORDER BY d.created_at LIMIT 10',
      { key: `${this.tenant}:${id}`, tenant: this.tenant },
    );
    return {
      ...JSON.parse(rows[0].result),
      status: rows[0].status,
      decisions: decisions.map((x) => x.decision),
    };
  }
  async simulate(source: string, target: string, edgeId: string) {
    const at = new Date().toISOString();
    // Both traversals execute in one graph read, so a concurrent import cannot
    // turn a source revision change into an apparent remediation effect.
    const rows = await this.db.query<{
      before_nodes: Record<string, unknown>[];
      before_edges: Record<string, unknown>[];
      after_nodes: Record<string, unknown>[];
      after_edges: Record<string, unknown>[];
    }>(
      `
      MATCH p=(s:GVEntity {key:$source})-[:GVREL*1..6]->(t:GVEntity {key:$target})
      WHERE all(n IN nodes(p) WHERE n.tenant=$tenant)
        AND all(r IN relationships(p) WHERE r.tenant=$tenant AND r.type<>'MEMBER_OF' AND r.valid_from<=$at AND (r.valid_to IS NULL OR $at<r.valid_to))
        AND any(r IN relationships(p) WHERE r.id=$excluded)
      WITH p,s,t ORDER BY length(p),[r IN relationships(p)|r.id] LIMIT 1
      OPTIONAL MATCH q=(s)-[:GVREL*1..6]->(t)
      WHERE all(n IN nodes(q) WHERE n.tenant=$tenant)
        AND all(r IN relationships(q) WHERE r.tenant=$tenant AND r.type<>'MEMBER_OF' AND r.id<>$excluded AND r.valid_from<=$at AND (r.valid_to IS NULL OR $at<r.valid_to))
      WITH p,q ORDER BY length(q),[r IN relationships(q)|r.id] LIMIT 1
      RETURN [n IN nodes(p)|properties(n)] AS before_nodes,[r IN relationships(p)|properties(r)] AS before_edges,
        [n IN nodes(q)|properties(n)] AS after_nodes,[r IN relationships(q)|properties(r)] AS after_edges`,
      {
        tenant: this.tenant,
        source: `${this.tenant}:${source}`,
        target: `${this.tenant}:${target}`,
        at,
        excluded: edgeId,
      },
    );
    if (!rows[0])
      throw new HttpError(
        422,
        'edge_not_in_path',
        'Choose a relationship on a current bounded path between these entities.',
      );
    const makePath = (
      nodes: Record<string, unknown>[] | null,
      edges: Record<string, unknown>[] | null,
    ): PathData => ({
      ...info(),
      snapshot_at: at,
      nodes: (nodes ?? []).map(cleanNode),
      edges: (edges ?? []).map(cleanEdge),
      found: !!nodes?.length,
      ordered_node_ids: (nodes ?? []).map((n) => String(n.id)),
      ordered_edge_ids: (edges ?? []).map((e) => String(e.id)),
      directed: true,
      query_ms: 0,
      explanation:
        'Bounded modeled relationship traversal. Connectivity is not effective authorization.',
    });
    const before = makePath(rows[0].before_nodes, rows[0].before_edges),
      after = makePath(rows[0].after_nodes, rows[0].after_edges);
    const id = crypto.randomUUID();
    const result = {
      id,
      status: 'PROPOSED',
      before,
      after,
      removed_edge_id: edgeId,
      path_broken: before.found && !after.found,
      alternate_path_remains: after.found,
      original_graph_modified: false as const,
      explanation:
        'Read-only modeled relationship exclusion within six hops. Proposal approval does not execute changes on GitHub or AWS.',
    };
    await this.db.query(
      'CREATE (s:GVSimulation {key:$key,tenant:$tenant,id:$id,status:"PROPOSED",created_at:$at,result_json:$result}) RETURN s.id AS id',
      { key: `${this.tenant}:${id}`, tenant: this.tenant, id, at, result: JSON.stringify(result) },
      true,
    );
    return result;
  }
  async reviewSimulation(
    id: string,
    status: 'UNDER_REVIEW' | 'APPROVED' | 'REJECTED',
    reason: string,
  ) {
    const rows = await this.db.query<{ id: string; status: string }>(
      `MATCH (s:GVSimulation {key:$key}) WHERE s.tenant=$tenant
      AND ((s.status='PROPOSED' AND $status='UNDER_REVIEW') OR (s.status='UNDER_REVIEW' AND $status IN ['APPROVED','REJECTED']))
      CREATE (d:GVReview {id:$decision,tenant:$tenant,proposal_id:s.id,status:$status,reason:$reason,created_at:$at})
      CREATE (s)-[:GVREVIEW {tenant:$tenant}]->(d) SET s.status=$status,s.updated_at=$at
      RETURN s.id AS id,s.status AS status`,
      {
        key: `${this.tenant}:${id}`,
        tenant: this.tenant,
        status,
        reason,
        decision: crypto.randomUUID(),
        at: new Date().toISOString(),
      },
      true,
    );
    if (!rows.length)
      throw new HttpError(
        409,
        'invalid_review_transition',
        'Proposal not found or review transition is invalid.',
      );
    return { ...rows[0], executed: false };
  }
  async graph(): Promise<GraphData> {
    // One server read provides a coherent bounded snapshot, including isolated nodes.
    const rows = await this.db.query<{
      nodes: Record<string, unknown>[];
      edges: Record<string, unknown>[];
    }>(
      `
      MATCH (n:GVEntity) WHERE n.tenant = $tenant AND coalesce(n.current,true)=true
      WITH n ORDER BY n.id LIMIT 501 WITH collect(n) AS ns
      OPTIONAL MATCH (a:GVEntity)-[r:GVREL]->(b:GVEntity)
      WHERE a IN ns AND b IN ns AND r.tenant = $tenant AND r.valid_from <= $at AND (r.valid_to IS NULL OR $at < r.valid_to)
      WITH ns, r ORDER BY r.id LIMIT 1201
      RETURN [n IN ns | properties(n)] AS nodes, collect(properties(r)) AS edges`,
      { tenant: this.tenant, at: new Date().toISOString() },
    );
    const row = rows[0] ?? { nodes: [], edges: [] };
    if (row.nodes.length > 500 || row.edges.length > 1200)
      throw new HttpError(
        413,
        'graph_limit',
        'This explorer supports at most 500 entities and 1200 relationships.',
      );
    const nodes = row.nodes.map(cleanNode),
      edges = row.edges.filter(Boolean).map(cleanEdge);
    for (const n of nodes)
      n.degree = edges.filter((e) => e.source === n.id || e.target === n.id).length;
    return { ...info(), nodes, edges };
  }
  async node(id: string): Promise<Entity> {
    const rows = await this.db.query<{ node: Record<string, unknown> }>(
      'MATCH (n:GVEntity {key:$key}) WHERE n.tenant=$tenant RETURN properties(n) AS node',
      { key: `${this.tenant}:${id}`, tenant: this.tenant },
    );
    if (!rows[0]) throw new HttpError(404, 'not_found', 'Entity not found in this universe.');
    return cleanNode(rows[0].node);
  }
  async neighbors(id: string): Promise<GraphData> {
    const center = await this.node(id);
    const rows = await this.db.query<{
      node: Record<string, unknown>;
      edge: Record<string, unknown>;
    }>(
      `
      MATCH (n:GVEntity {key:$key})-[r:GVREL]-(m:GVEntity)
      WHERE n.tenant=$tenant AND m.tenant=$tenant AND r.tenant=$tenant AND r.valid_from <= $at AND (r.valid_to IS NULL OR $at < r.valid_to)
      RETURN properties(m) AS node, properties(r) AS edge ORDER BY r.id LIMIT 201`,
      { key: `${this.tenant}:${id}`, tenant: this.tenant, at: new Date().toISOString() },
    );
    return {
      ...info(),
      nodes: [center, ...rows.slice(0, 200).map((r) => cleanNode(r.node))].filter(
        (n, i, a) => a.findIndex((x) => x.id === n.id) === i,
      ),
      edges: rows.slice(0, 200).map((r) => cleanEdge(r.edge)),
      truncated: rows.length > 200,
    };
  }
  async shortest(
    source: string,
    target: string,
    directed = true,
    excluded: string[] = [],
  ): Promise<PathData> {
    const start = performance.now();
    await Promise.all([this.node(source), this.node(target)]);
    if (source === target)
      return {
        ...info(),
        nodes: [await this.node(source)],
        edges: [],
        found: true,
        ordered_node_ids: [source],
        ordered_edge_ids: [],
        directed,
        explanation: 'Source and destination are the same entity.',
        query_ms: performance.now() - start,
      };
    // Direction is a validated boolean selecting two fixed templates. No user-supplied Cypher.
    // Membership links are deliberately excluded from semantic path discovery.
    const rows = await this.db.query<{
      nodes: Record<string, unknown>[];
      edges: Record<string, unknown>[];
    }>(
      `
      MATCH p=(s:GVEntity {key:$source})-[:GVREL*1..6]${directed ? '->' : '-'}(t:GVEntity {key:$target})
      WHERE all(n IN nodes(p) WHERE n.tenant=$tenant)
        AND all(r IN relationships(p) WHERE r.tenant=$tenant AND r.type <> 'MEMBER_OF' AND NOT r.id IN $excluded
          AND r.valid_from <= $at AND (r.valid_to IS NULL OR $at < r.valid_to))
      WITH p ORDER BY length(p), [r IN relationships(p) | r.id] LIMIT 1
      RETURN [n IN nodes(p) | properties(n)] AS nodes, [r IN relationships(p) | properties(r)] AS edges`,
      {
        tenant: this.tenant,
        source: `${this.tenant}:${source}`,
        target: `${this.tenant}:${target}`,
        at: new Date().toISOString(),
        excluded,
      },
    );
    const nodes = rows[0]?.nodes.map(cleanNode) ?? [],
      edges = rows[0]?.edges.map(cleanEdge) ?? [];
    return {
      ...info(),
      nodes,
      edges,
      found: !!rows.length,
      ordered_node_ids: nodes.map((n) => n.id),
      ordered_edge_ids: edges.map((e) => e.id),
      directed,
      query_ms: Math.round((performance.now() - start) * 10) / 10,
      explanation: rows.length
        ? 'Shortest modeled relationship path within six hops. This is connected evidence, not proof of effective authorization. Membership edges are excluded.'
        : 'No modeled path within six hops. This does not prove that no longer path or effective access exists.',
    };
  }
  async clusters(): Promise<Cluster[]> {
    return this.db.query<Cluster>(
      `MATCH (n:GVEntity)-[r:GVREL]->(c:GVEntity)
      WHERE n.tenant=$tenant AND c.tenant=$tenant AND r.tenant=$tenant AND r.type='MEMBER_OF' AND c.type='Cluster' AND r.valid_to IS NULL AND coalesce(n.current,true)=true
      RETURN c.id AS id, c.label AS label, c.description AS description, count(n) AS count ORDER BY c.id`,
      { tenant: this.tenant },
    );
  }
  async ask(question: string, source?: string, target?: string): Promise<Answer> {
    const resolved = await this.db.query<{ node: Record<string, unknown> }>(
      `MATCH (n:GVEntity) WHERE n.tenant=$tenant AND coalesce(n.current,true)=true
      AND (toLower($question) CONTAINS toLower(n.label) OR toLower($question)=toLower(n.id))
      RETURN properties(n) AS node ORDER BY n.id LIMIT 8`,
      { tenant: this.tenant, question },
    );
    const entities = resolved
      .map((r) => cleanNode(r.node))
      .sort(
        (a, b) =>
          question.toLowerCase().indexOf(a.label.toLowerCase()) -
          question.toLowerCase().indexOf(b.label.toLowerCase()),
      );
    const from = source ?? entities[0]?.id,
      to = target ?? entities[1]?.id;
    if (from && to) {
      const path = await this.shortest(from, to, true);
      return {
        mode: 'deterministic',
        path,
        entities: path.nodes,
        evidence: path.edges,
        answer: path.found
          ? `${path.nodes.map((n) => n.label).join(' → ')}. FalkorDB returned ${path.edges.length} directed relationships. ${path.explanation}`
          : path.explanation,
      };
    }
    if (from) {
      const neighborhood = await this.neighbors(from);
      return {
        mode: 'deterministic',
        entities: neighborhood.nodes,
        evidence: neighborhood.edges,
        answer: `${neighborhood.nodes[0].label}: ${neighborhood.nodes[0].description} Found ${neighborhood.edges.length} adjacent evidence relationships${neighborhood.truncated ? ' (bounded result)' : ''}. Conflicting claims require verification.`,
      };
    }
    return {
      mode: 'deterministic',
      entities: [],
      evidence: [],
      answer:
        'Select two entities, or ask using their exact names. I can trace bounded paths and retrieve neighboring evidence. I do not execute instructions or infer unsupported security verdicts.',
    };
  }
  async investigations(): Promise<Investigation[]> {
    const rows = await this.db.query<{ record: Investigation }>(
      `MATCH (i:GVInvestigation) WHERE i.tenant=$tenant RETURN properties(i) AS record ORDER BY i.created_at DESC LIMIT 100`,
      { tenant: this.tenant },
    );
    return rows.map(({ record }) => ({
      id: record.id,
      title: record.title,
      created_at: record.created_at,
      node_ids: record.node_ids,
      edge_ids: record.edge_ids,
      summary: record.summary,
    }));
  }
  async saveInvestigation(
    id: string,
    title: string,
    source: string,
    target: string,
  ): Promise<Investigation> {
    const path = await this.shortest(source, target, true);
    if (!path.found)
      throw new HttpError(422, 'no_path', 'There is no bounded directed path to save.');
    const fingerprint = createHash('sha256')
      .update(JSON.stringify({ title, source, target }))
      .digest('hex');
    const record: Investigation = {
      id,
      title,
      created_at: new Date().toISOString(),
      node_ids: path.ordered_node_ids,
      edge_ids: path.ordered_edge_ids,
      summary: path.explanation,
    };
    const rows = await this.db.query<{ record: Investigation }>(
      `
      MERGE (i:GVInvestigation {key:$key})
      ON CREATE SET i += $record, i.tenant=$tenant, i.fingerprint=$fingerprint, i.path_json=$path
      WITH i WHERE i.fingerprint=$fingerprint
      UNWIND $node_ids AS node_id MATCH (n:GVEntity {key:$tenant+':'+node_id})
      MERGE (i)-[:GVREMEMBERS {tenant:$tenant}]->(n)
      RETURN DISTINCT properties(i) AS record`,
      {
        key: `${this.tenant}:${id}`,
        tenant: this.tenant,
        record,
        fingerprint,
        path: JSON.stringify(path),
        node_ids: record.node_ids,
      },
      true,
    );
    if (!rows[0])
      throw new HttpError(
        409,
        'idempotency_conflict',
        'This operation ID was already used for a different investigation.',
      );
    return rows[0].record;
  }
  async investigationPath(id: string): Promise<PathData> {
    const rows = await this.db.query<{ path: string }>(
      'MATCH (i:GVInvestigation {key:$key}) WHERE i.tenant=$tenant RETURN i.path_json AS path',
      { key: `${this.tenant}:${id}`, tenant: this.tenant },
    );
    if (!rows[0]) throw new HttpError(404, 'not_found', 'Investigation not found.');
    return JSON.parse(rows[0].path);
  }
}

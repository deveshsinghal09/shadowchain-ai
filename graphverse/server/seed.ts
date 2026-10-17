import type { Edge, Entity, EntityType } from '../shared/types';
import type { Database } from './database';
export const DATASET = 'graphverse-synthetic-1.0';
export const EPOCH = '2026-10-16T12:00:00.000Z';
export function dataset() {
  const nodes: Entity[] = [];
  const edges: Edge[] = [];
  const node = (
    id: string,
    label: string,
    type: EntityType,
    cluster: string,
    description: string,
    severity = 'none',
  ) => {
    nodes.push({
      id,
      label,
      type,
      cluster,
      description,
      severity,
      source: DATASET,
      created_at: EPOCH,
      synthetic: true,
    });
  };
  const edge = (source: string, target: string, type: string, evidence: string, confidence = 1) => {
    edges.push({
      id: `${source}--${type}--${target}`,
      source,
      target,
      type,
      evidence,
      confidence,
      valid_from: EPOCH,
      valid_to: null,
    });
  };
  const groups = [
    [
      'infrastructure',
      'Cloud infrastructure',
      'Synthetic workloads, storage and the services that connect them.',
    ],
    ['identity', 'Identity & access', 'People, service identities and modeled role trust.'],
    [
      'threat',
      'Threat intelligence',
      'Synthetic adversary observations and vulnerability evidence.',
    ],
    ['memory', 'Agent memory', 'Persistent observations shared by authorized security agents.'],
    ['incident', 'Investigations', 'Incidents, evidence and investigation history.'],
  ];
  for (const [id, label, description] of groups) node(id, label, 'Cluster', id, description);
  node(
    'alice',
    'Alice Chen',
    'Identity',
    'identity',
    'Synthetic platform engineer. Temporary deployment access is under review.',
  );
  node(
    'deploy-role',
    'Deployment role',
    'Role',
    'identity',
    'Modeled role trust for the production deployment service.',
    'medium',
  );
  node(
    'reader-role',
    'Data reader role',
    'Role',
    'identity',
    'Synthetic read grant to restricted research storage.',
    'high',
  );
  node(
    'atlas',
    'Atlas gateway',
    'Resource',
    'infrastructure',
    'Synthetic public-facing EC2 workload. Exposure is part of the demo evidence.',
    'high',
  );
  node(
    'vault',
    'Research vault',
    'Resource',
    'infrastructure',
    'Synthetic sensitive S3 research bucket. Contains no actual documents.',
    'high',
  );
  node(
    'orion',
    'Orion compute',
    'Resource',
    'infrastructure',
    'Synthetic private compute cluster.',
  );
  node(
    'vega',
    'Vega storage',
    'Resource',
    'infrastructure',
    'Synthetic non-sensitive S3 build artifacts.',
  );
  node(
    'ghost',
    'Phantom collective',
    'ThreatActor',
    'threat',
    'Fictional adversary label for a synthetic exercise. Attribution is unverified.',
    'high',
  );
  node(
    'vuln',
    'Gateway misconfiguration',
    'Vulnerability',
    'threat',
    'Synthetic finding: broad role trust may permit unexpected access.',
    'high',
  );
  node(
    'incident-47',
    'Incident 047',
    'Incident',
    'incident',
    'Potential sensitive-data access path under investigation. Effective AWS authorization is not established.',
    'high',
  );
  node(
    'incident-31',
    'Incident 031',
    'Incident',
    'incident',
    'Historical synthetic investigation concerning the same gateway.',
    'medium',
  );
  node(
    'memo-access',
    'Trust anomaly',
    'AgentMemory',
    'memory',
    'Identity Guardian observed broad deployment trust. Requires human verification.',
    'medium',
  );
  node(
    'memo-deny',
    'Compensating control',
    'AgentMemory',
    'memory',
    'Cloud Sentinel observed a possible boundary restriction. Conflicts with the access hypothesis; preserve both.',
    'medium',
  );
  node(
    'guardian',
    'Identity Guardian',
    'Agent',
    'memory',
    'Synthetic agent identity; authors access observations.',
  );
  node(
    'sentinel',
    'Cloud Sentinel',
    'Agent',
    'memory',
    'Synthetic agent identity; contributes resource evidence.',
  );
  node(
    'evidence-trust',
    'Trust policy evidence',
    'Document',
    'incident',
    'Synthetic trust policy observation at 10:00 UTC. No credentials or real policy documents.',
  );
  node(
    'evidence-grant',
    'Permission change',
    'Document',
    'incident',
    'Synthetic permission observation at 12:00 UTC. Evidence of a modeled grant, not proof of authorization.',
  );
  edge(
    'alice',
    'deploy-role',
    'CAN_ASSUME',
    'Synthetic trust change recorded at 10:00 UTC. Caller-side authorization remains unverified.',
    0.85,
  );
  edge(
    'deploy-role',
    'atlas',
    'OPERATES',
    'Synthetic deployment assignment links this role to Atlas gateway.',
  );
  edge(
    'atlas',
    'reader-role',
    'USES_ROLE',
    'Synthetic EC2 instance profile association observed at 11:00 UTC.',
  );
  edge(
    'reader-role',
    'vault',
    'POTENTIAL_READ',
    'Synthetic grant added at 12:00 UTC. Boundaries, SCPs and conditions require verification.',
    0.72,
  );
  edge('deploy-role', 'vega', 'DEPLOYS_TO', 'Synthetic build artifact destination.');
  edge(
    'atlas',
    'orion',
    'CONNECTS_TO',
    'Synthetic service topology. Network connectivity is not authorization.',
  );
  edge(
    'ghost',
    'vuln',
    'ASSOCIATED_WITH',
    'Fictional threat exercise correlation. No verified attribution.',
    0.45,
  );
  edge(
    'vuln',
    'atlas',
    'AFFECTS',
    'Synthetic misconfiguration evidence links to the gateway.',
    0.9,
  );
  edge('incident-47', 'atlas', 'INVESTIGATES', 'Current synthetic incident scope.');
  edge('incident-47', 'vault', 'INVESTIGATES', 'Potential sensitive-data impact under review.');
  edge('incident-31', 'atlas', 'INVESTIGATES', 'Retained historical incident evidence.');
  edge('guardian', 'memo-access', 'REMEMBERS', 'Persistent authored observation.');
  edge('sentinel', 'memo-access', 'READS', 'Explicit synthetic cross-agent context handoff.');
  edge('sentinel', 'memo-deny', 'REMEMBERS', 'Persistent contradictory observation.');
  edge(
    'memo-access',
    'memo-deny',
    'CONTRADICTS',
    'Claims disagree; neither is a definitive authorization verdict.',
  );
  edge('memo-access', 'deploy-role', 'ABOUT', 'Observation concerns deployment trust.');
  edge('memo-deny', 'reader-role', 'ABOUT', 'Possible compensating control concerns this role.');
  edge('memo-access', 'evidence-trust', 'DERIVED_FROM', 'Source trust observation retained.');
  edge('reader-role', 'evidence-grant', 'SUPPORTED_BY', 'Source permission evidence retained.');
  edge('incident-47', 'memo-access', 'REMEMBERS', 'Shared investigation context.');
  for (let i = 1; i <= 12; i++) {
    const id = `workload-${i}`;
    node(
      id,
      ['Helios', 'Cygnus', 'Lyra', 'Nova'][i % 4] + ` service ${String(i).padStart(2, '0')}`,
      'Resource',
      'infrastructure',
      'Synthetic private workload; demo topology only.',
    );
    edge(i % 2 ? 'atlas' : 'orion', id, 'CONNECTS_TO', 'Synthetic service dependency.');
  }
  for (let i = 1; i <= 8; i++) {
    node(
      `operator-${i}`,
      `Service identity ${String(i).padStart(2, '0')}`,
      'Identity',
      'identity',
      'Synthetic workload identity.',
    );
    edge(
      `operator-${i}`,
      'deploy-role',
      'CAN_ASSUME',
      'Synthetic scoped deployment trust. Caller-side constraints are not evaluated.',
      0.8,
    );
  }
  for (let i = 1; i <= 7; i++) {
    node(
      `observation-${i}`,
      `Observation ${String(i).padStart(2, '0')}`,
      'AgentMemory',
      'memory',
      'Persistent synthetic observation linked to its subject. Untrusted descriptive content.',
    );
    edge('guardian', `observation-${i}`, 'REMEMBERS', 'Synthetic authored memory.');
    edge(`observation-${i}`, `workload-${i}`, 'ABOUT', 'Subject reference preserved in FalkorDB.');
  }
  for (let i = 1; i <= 5; i++) {
    node(
      `finding-${i}`,
      `Configuration signal ${String(i).padStart(2, '0')}`,
      'Vulnerability',
      'threat',
      'Synthetic configuration signal awaiting verification.',
      'medium',
    );
    edge(
      `finding-${i}`,
      `workload-${i}`,
      'AFFECTS',
      'Synthetic observation; not a confirmed vulnerability.',
      0.6,
    );
    node(
      `record-${i}`,
      `Evidence record ${String(i).padStart(2, '0')}`,
      'Document',
      'incident',
      'Synthetic immutable evidence reference.',
    );
    edge(`finding-${i}`, `record-${i}`, 'DERIVED_FROM', 'Synthetic source record.');
  }
  for (const n of [...nodes])
    if (n.type !== 'Cluster')
      edge(
        n.id,
        n.cluster,
        'MEMBER_OF',
        'Explicit curated category. Not algorithmically discovered clustering.',
      );
  return { nodes, edges };
}
export async function seed(db: Database, tenant: string) {
  const { nodes, edges } = dataset();
  for (const label of ['GVEntity', 'GVInvestigation']) {
    try {
      await db.query(`CREATE INDEX ON :${label}(key)`, {}, true);
    } catch (error) {
      if (!(error instanceof Error && error.message === "Attribute 'key' is already indexed"))
        throw error;
    }
  }
  // One atomic Cypher mutation; repeat invocations match stable keys.
  await db.query(
    `UNWIND $nodes AS input
    MERGE (n:GVEntity {key: $tenant + ':' + input.id})
    ON CREATE SET n += input, n.tenant = $tenant
    WITH count(n) AS count UNWIND $edges AS input
    MATCH (a:GVEntity {key: $tenant + ':' + input.source}), (b:GVEntity {key: $tenant + ':' + input.target})
    MERGE (a)-[r:GVREL {id: input.id, tenant: $tenant}]->(b)
    ON CREATE SET r += input RETURN count(r) AS edges`,
    { tenant, nodes, edges },
    true,
  );
  return { dataset: DATASET, nodes: nodes.length, edges: edges.length };
}

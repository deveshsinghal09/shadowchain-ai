export type EntityType =
  | 'Identity'
  | 'Role'
  | 'Resource'
  | 'Incident'
  | 'Vulnerability'
  | 'AgentMemory'
  | 'Document'
  | 'ThreatActor'
  | 'Agent'
  | 'Cluster'
  | 'Investigation';
export interface Entity {
  id: string;
  label: string;
  type: EntityType;
  cluster: string;
  description: string;
  severity: string;
  source: string;
  created_at: string;
  synthetic: boolean;
  degree?: number;
  x?: number;
  y?: number;
  z?: number;
}
export interface Edge {
  id: string;
  source: string;
  target: string;
  type: string;
  evidence: string;
  confidence: number;
  valid_from: string;
  valid_to: string | null;
}
export interface GraphData {
  nodes: Entity[];
  edges: Edge[];
  snapshot_at: string;
  dataset: string;
  truncated: boolean;
}
export interface PathData extends GraphData {
  found: boolean;
  ordered_node_ids: string[];
  ordered_edge_ids: string[];
  directed: boolean;
  explanation: string;
  query_ms: number;
}
export interface Answer {
  answer: string;
  mode: 'deterministic' | 'gemini';
  model_note?: string;
  path?: PathData;
  entities: Entity[];
  evidence: Edge[];
}
export interface Cluster {
  id: string;
  label: string;
  description: string;
  count: number;
}
export interface LiveStatus {
  enabled: boolean;
  repositories: string[];
  sources: {
    repository: string;
    status: string;
    head: string;
    observed_at: string;
    last_checked_at: string;
    run_id: string;
    node_count: number;
    edge_count: number;
    coverage: string[];
  }[];
  automatic_sync?: boolean;
}
export interface LiveActivity {
  runs: {
    id: string;
    status: string;
    cursor: number;
    repository: string;
    head: string;
    created_at: string;
  }[];
  tasks: {
    id: string;
    run_id: string;
    agent_name: string;
    status: string;
    step: number;
    output: string;
    retrieved_memory_ids: string[];
    input_evidence_ids: string[];
    output_memory_id: string;
    created_at: string;
  }[];
  handoffs: {
    id: string;
    run_id: string;
    from_agent: string;
    to_agent: string;
    memory_id: string;
    status: string;
    created_at: string;
  }[];
}
export interface Simulation {
  id: string;
  status: 'PROPOSED' | 'UNDER_REVIEW' | 'APPROVED' | 'REJECTED';
  before: PathData;
  after: PathData;
  removed_edge_id: string;
  path_broken: boolean;
  alternate_path_remains: boolean;
  original_graph_modified: false;
  explanation: string;
}
export interface Investigation {
  id: string;
  title: string;
  created_at: string;
  node_ids: string[];
  edge_ids: string[];
  summary: string;
}

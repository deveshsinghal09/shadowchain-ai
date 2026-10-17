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
export interface Investigation {
  id: string;
  title: string;
  created_at: string;
  node_ids: string[];
  edge_ids: string[];
  summary: string;
}

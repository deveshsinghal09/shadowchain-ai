import { create } from 'zustand';
import type { Answer, Cluster, Entity, GraphData, Investigation, PathData } from '../shared/types';
export type Quality = 'cinematic' | 'balanced' | 'performance';
export const colors: Record<string, string> = {
  infrastructure: '#70deff',
  identity: '#80aaff',
  threat: '#f7a071',
  memory: '#be9bff',
  incident: '#f289ae',
};
export interface ExplorerState {
  graph: GraphData | null;
  clusters: Cluster[];
  investigations: Investigation[];
  selected: Entity | null;
  path: PathData | null;
  answer: Answer | null;
  activeCluster: string | null;
  hiddenClusters: string[];
  panel: 'entity' | 'path' | 'answer' | 'clusters' | 'memory' | 'help' | 'source' | null;
  quality: Quality;
  bloom: boolean;
  direction: boolean;
  labels: boolean;
  motion: boolean;
  journey: 'idle' | 'playing' | 'paused' | 'finished';
  step: number;
  cameraAction: { kind: 'overview' | 'focus' | 'cluster' | 'intro'; id?: string; nonce: number };
  intro: boolean;
  pulse: { edgeIds: string[]; at: number };
  journeySession: number;
  set: (value: Partial<ExplorerState>) => void;
  focus: (node: Entity) => void;
}
export const useExplorer = create<ExplorerState>((set) => ({
  graph: null,
  clusters: [],
  investigations: [],
  selected: null,
  path: null,
  answer: null,
  activeCluster: null,
  hiddenClusters: [],
  panel: null,
  quality: 'balanced',
  bloom: true,
  direction: true,
  labels: true,
  motion: !matchMedia('(prefers-reduced-motion: reduce)').matches,
  journey: 'idle',
  step: 0,
  intro: false,
  cameraAction: { kind: 'overview', nonce: 0 },
  pulse: { edgeIds: [], at: 0 },
  journeySession: 0,
  set: (value) => set(value),
  focus: (node) =>
    set({
      selected: node,
      panel: 'entity',
      cameraAction: { kind: 'focus', id: node.id, nonce: Date.now() },
    }),
}));

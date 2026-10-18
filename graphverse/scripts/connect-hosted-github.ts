import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { GraphData, LiveActivity, LiveStatus, PathData, Simulation } from '../shared/types';
const base = 'https://shadowchain-ai.vercel.app';
const directory = join(
  process.env.LOCALAPPDATA ?? '',
  'ShadowChainAI',
  'railway-2f4a53bd-bd2a-4db6-8098-45fe33570a01',
);
const token = readFileSync(join(directory, 'operator.token'), 'utf8').trim();
const login = await fetch(base + '/api/session', {
  method: 'POST',
  headers: { 'content-type': 'application/json', Origin: base },
  body: JSON.stringify({ token }),
  signal: AbortSignal.timeout(15000),
});
assert.equal(login.status, 200, 'Operator sign-in must succeed');
const cookie = login.headers.get('set-cookie')!.split(';')[0];
async function api<T>(path: string, body?: unknown, authenticated = true): Promise<T> {
  const response = await fetch(base + '/api' + path, {
    method: body ? 'POST' : 'GET',
    headers: {
      ...(body ? { 'content-type': 'application/json', Origin: base } : {}),
      ...(authenticated ? { cookie } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(120000),
  });
  const value = await response.json();
  if (!response.ok)
    throw new Error(`${path}: HTTP ${response.status}, code ${value.error?.code ?? 'unknown'}`);
  return value as T;
}
const sync = await api<LiveStatus & { run_id: string; duplicate: boolean }>(
  '/integrations/github/sync',
  { repository: 'deveshsinghal09/shadowchain-ai' },
);
const graph = await api<GraphData>('/graph', undefined, false),
  activity = await api<LiveActivity>('/activity', undefined, false);
assert.ok(graph.nodes.length > 0);
assert.ok(graph.nodes.every((n) => !n.synthetic));
assert.equal(activity.runs[0].status, 'COMPLETED');
const source = graph.edges.find((e) => e.type === 'OWNS')!.source,
  target = graph.edges.find((e) => e.type === 'DECLARES_DEPENDENCY')!.target;
const path = await api<PathData>('/paths/shortest', { source, target, directed: true }, false);
assert.ok(path.found);
const simulation = await api<Simulation>('/what-if/remove-relationship', {
  source,
  target,
  edge_id: path.ordered_edge_ids.at(-1),
});
assert.ok(simulation.path_broken);
assert.ok(
  (await api<PathData>('/paths/shortest', { source, target, directed: true }, false)).found,
);
const investigationId = randomUUID();
await api('/investigations', {
  id: investigationId,
  title: 'Live GitHub evidence: repository dependency review',
  source,
  target,
});
assert.equal((await api<PathData>(`/investigations/${investigationId}/path`)).found, true);
const answer = await api<{ mode: string }>(
  '/ai/ask',
  { question: 'Trace repository dependency evidence', source, target },
  false,
);
assert.equal(answer.mode, 'deterministic');
let model_verified = false;
try {
  model_verified =
    (
      await api<{ mode: string }>('/ai/ask', {
        question: 'Explain the connected evidence',
        source,
        target,
      })
    ).mode === 'gemini';
} catch {
  /* Graph operation already verified; record provider failure without secrets. */
}
const repeated = await api<{ duplicate: boolean }>('/integrations/github/sync', {
  repository: 'deveshsinghal09/shadowchain-ai',
});
assert.ok(repeated.duplicate);
const tasks = (await api<LiveActivity>('/activity')).tasks.filter((t) => t.run_id === sync.run_id);
assert.equal(tasks.length, 6);
const receipt = {
  verified_at: new Date().toISOString(),
  repository: 'deveshsinghal09/shadowchain-ai',
  run_id: sync.run_id,
  dataset: graph.dataset,
  nodes: graph.nodes.length,
  edges: graph.edges.length,
  synthetic_nodes: 0,
  completed_stages: tasks.length,
  handoffs: activity.handoffs.filter((h) => h.run_id === sync.run_id).length,
  cross_agent_memory: tasks.some((t) => t.retrieved_memory_ids.length > 0),
  path_found: true,
  simulation_path_broken: true,
  original_path_preserved: true,
  saved_investigation_id: investigationId,
  public_questions: 'deterministic',
  live_gemini_verified: model_verified,
  duplicate_sync_prevented: true,
  tasks: tasks.map((t) => ({ id: t.id, created_at: t.created_at })),
  source: sync.sources[0],
};
mkdirSync('test-results', { recursive: true });
writeFileSync('test-results/hosted-github-receipt.json', JSON.stringify(receipt, null, 2));
console.log(JSON.stringify({ ...receipt, tasks: undefined, source: undefined }, null, 2));

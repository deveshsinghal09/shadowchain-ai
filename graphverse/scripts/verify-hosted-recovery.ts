import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type { GraphData, LiveActivity, PathData } from '../shared/types';
const base = 'https://shadowchain-ai.vercel.app';
const prior = JSON.parse(readFileSync('test-results/hosted-github-receipt.json', 'utf8'));
const token = readFileSync(
  join(
    process.env.LOCALAPPDATA ?? '',
    'ShadowChainAI',
    'railway-2f4a53bd-bd2a-4db6-8098-45fe33570a01',
    'operator.token',
  ),
  'utf8',
).trim();
const login = await fetch(base + '/api/session', {
  method: 'POST',
  headers: { 'content-type': 'application/json', Origin: base },
  body: JSON.stringify({ token }),
});
assert.equal(login.status, 200);
const cookie = login.headers.get('set-cookie')!.split(';')[0];
async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(base + '/api' + path, {
    method: body ? 'POST' : 'GET',
    headers: { cookie, ...(body ? { 'content-type': 'application/json', Origin: base } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(35000),
  });
  assert.equal(response.status, 200, path);
  return response.json() as Promise<T>;
}
const activity = await api<LiveActivity>('/activity'),
  graph = await api<GraphData>('/graph');
const tasks = activity.tasks.filter((t) => t.run_id === prior.run_id);
assert.equal(tasks.length, 6);
for (const task of prior.tasks) {
  const found = tasks.find((t) => t.id === task.id);
  assert.ok(found);
  assert.equal(found.created_at, task.created_at);
}
assert.ok((await api<PathData>(`/investigations/${prior.saved_investigation_id}/path`)).found);
assert.equal(graph.nodes.length, prior.nodes);
assert.equal(graph.edges.length, prior.edges);
let provider = await api<{
  model: string;
  model_list_http_status: number | null;
  available_models: string[];
}>('/provider/check', {});
const source = graph.edges.find((e) => e.type === 'OWNS')!.source,
  target = graph.edges.find((e) => e.type === 'DECLARES_DEPENDENCY')!.target;
const answer = await api<{ mode: string; model_note?: string; path?: PathData }>('/ai/ask', {
  question: 'Explain the connected evidence',
  source,
  target,
});
assert.equal(answer.path?.found, true);
if (answer.mode !== 'gemini') provider = await api<typeof provider>('/provider/check', {});
const receipt = {
  verified_at: new Date().toISOString(),
  backend_redeploy_preserved_inventory: true,
  completed_tasks_not_repeated: true,
  saved_investigation_preserved: true,
  graph_counts: { nodes: graph.nodes.length, edges: graph.edges.length },
  live_gemini_verified: answer.mode === 'gemini',
  provider: { ...provider, available_models: provider.available_models?.slice(0, 20) },
};
writeFileSync('test-results/hosted-recovery-receipt.json', JSON.stringify(receipt, null, 2));
console.log(JSON.stringify(receipt, null, 2));

import type {
  Answer,
  Cluster,
  Entity,
  GraphData,
  Investigation,
  PathData,
  LiveStatus,
  LiveActivity,
  Simulation,
} from '../shared/types';
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function request<T>(
  url: string,
  body?: unknown,
  method = body ? 'POST' : 'GET',
  timeout = 12000,
): Promise<T> {
  const res = await fetch(`/api${url}`, {
    method,
    credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(timeout),
  });
  const result = await res.json();
  if (!res.ok)
    throw new ApiError(res.status, result.error?.message ?? `Request failed (${res.status}).`);
  return result as T;
}
export const api = {
  liveStatus: () => request<LiveStatus>('/integrations/github'),
  activity: () => request<LiveActivity>('/activity'),
  sync: (repository: string) =>
    request<LiveStatus & { duplicate: boolean; run_id: string }>(
      '/integrations/github/sync',
      { repository },
      'POST',
      120000,
    ),
  resume: (id: string) =>
    request<LiveActivity>(`/activity/${encodeURIComponent(id)}/resume`, {}, 'POST', 60000),
  simulate: (source: string, target: string, edge_id: string) =>
    request<Simulation>('/what-if/remove-relationship', { source, target, edge_id }),
  reviewSimulation: (id: string, status: Simulation['status'], reason: string) =>
    request<{ status: Simulation['status']; executed: false }>(`/simulations/${id}/review`, {
      status,
      reason,
    }),
  session: () =>
    request<{
      authenticated: boolean;
      access: 'operator' | 'public';
      can_write: boolean;
      can_use_model: boolean;
    }>('/session'),
  ready: () => request<{ status: string }>('/ready'),
  login: (token: string) => request('/session', { token }),
  graph: () => request<GraphData>('/graph'),
  node: (id: string) => request<Entity>(`/nodes/${encodeURIComponent(id)}`),
  neighbors: (id: string) => request<GraphData>(`/nodes/${encodeURIComponent(id)}/neighbors`),
  clusters: () => request<Cluster[]>('/clusters'),
  path: (source: string, target: string) =>
    request<PathData>('/paths/shortest', { source, target, directed: true }),
  ask: (question: string, source?: string, target?: string) =>
    request<Answer>('/ai/ask', { question, source, target }, 'POST', 30000),
  investigations: () => request<Investigation[]>('/investigations'),
  savedPath: (id: string) => request<PathData>(`/investigations/${id}/path`),
  save: (id: string, title: string, source: string, target: string) =>
    request<Investigation>('/investigations', { id, title, source, target }),
};

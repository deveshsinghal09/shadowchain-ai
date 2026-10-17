import type { Answer, Cluster, Entity, GraphData, Investigation, PathData } from '../shared/types';
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
): Promise<T> {
  const res = await fetch(`/api${url}`, {
    method,
    credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(12000),
  });
  const result = await res.json();
  if (!res.ok)
    throw new ApiError(res.status, result.error?.message ?? `Request failed (${res.status}).`);
  return result as T;
}
export const api = {
  session: () => request<{ authenticated: boolean }>('/session'),
  ready: () => request<{ status: string }>('/ready'),
  login: (token: string) => request('/session', { token }),
  graph: () => request<GraphData>('/graph'),
  node: (id: string) => request<Entity>(`/nodes/${encodeURIComponent(id)}`),
  neighbors: (id: string) => request<GraphData>(`/nodes/${encodeURIComponent(id)}/neighbors`),
  clusters: () => request<Cluster[]>('/clusters'),
  path: (source: string, target: string) =>
    request<PathData>('/paths/shortest', { source, target, directed: true }),
  ask: (question: string, source?: string, target?: string) =>
    request<Answer>('/ai/ask', { question, source, target }),
  investigations: () => request<Investigation[]>('/investigations'),
  savedPath: (id: string) => request<PathData>(`/investigations/${id}/path`),
  save: (id: string, title: string, source: string, target: string) =>
    request<Investigation>('/investigations', { id, title, source, target }),
};

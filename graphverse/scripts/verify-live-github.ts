import { randomUUID } from 'node:crypto';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { Database } from '../server/database';
import { Repository } from '../server/repository';
import { LiveService } from '../server/live';
import { GitHubReader, githubId } from '../server/github';
const repository = process.argv[2] ?? 'deveshsinghal09/shadowchain-ai';
const db = new Database('graphverse_test_' + randomUUID().replaceAll('-', ''));
try {
  const live = new LiveService(db, 'live-read-verification', [repository], new GitHubReader());
  const result = await live.sync(repository),
    repo = new Repository(db, 'live-read-verification');
  const graph = await repo.graph(),
    activity = await live.activity();
  assert.ok(graph.nodes.length > 0);
  assert.ok(graph.nodes.every((n) => !n.synthetic));
  assert.equal(activity.tasks.length, 6);
  assert.equal(activity.runs[0].status, 'COMPLETED');
  const dependency = graph.nodes.find(
    (n) => n.id === graph.edges.find((e) => e.type === 'DECLARES_DEPENDENCY')?.target,
  );
  assert.ok(dependency, 'A declared dependency must be available in the source graph.');
  const path = await repo.shortest(
    githubId(repository.toLowerCase() + ':repository'),
    dependency.id,
  );
  assert.ok(path.found);
  const simulation = await repo.simulate(
    path.ordered_node_ids[0],
    dependency.id,
    path.ordered_edge_ids.at(-1)!,
  );
  assert.ok(simulation.path_broken);
  assert.ok((await repo.shortest(path.ordered_node_ids[0], dependency.id)).found);
  await db.close();
  const recovered = new LiveService(db, 'live-read-verification', [repository]);
  assert.equal((await recovered.activity()).tasks.length, 6);
  const receipt = {
    checked_at: new Date().toISOString(),
    repository,
    github_http: 'real GET requests; no token',
    database: 'real isolated FalkorDB graph',
    nodes: graph.nodes.length,
    edges: graph.edges.length,
    tasks: activity.tasks.length,
    handoffs: activity.handoffs.length,
    cross_agent_memory: activity.tasks.some(
      (t) => (t.retrieved_memory_ids as unknown[]).length > 0,
    ),
    simulation_path_broken: simulation.path_broken,
    original_path_preserved: true,
    reconnect_retrieved_tasks: true,
    source: result.sources[0],
  };
  mkdirSync('test-results', { recursive: true });
  writeFileSync('test-results/github-live-receipt.json', JSON.stringify(receipt, null, 2));
  console.log(JSON.stringify({ ...receipt, source: undefined }, null, 2));
} finally {
  const client = await db.connect();
  await client.selectGraph(db.name).delete();
  await db.close();
}

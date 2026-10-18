import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { Database } from './database';
import { Repository, HttpError } from './repository';
import { LiveService } from './live';
import { GitHubReader, githubId } from './github';
import { transport, name } from './github-fixture';
import { createApp } from './app';
import { once } from 'node:events';
import type { AddressInfo } from 'node:net';
const db = new Database('graphverse_test_' + randomUUID().replaceAll('-', '')),
  tenant = 'live-one',
  repo = new Repository(db, tenant);
const service = new LiveService(db, tenant, [name], new GitHubReader('', transport()));
before(async () => {
  await db.query('RETURN 1 AS ok', {}, true);
});
after(async () => {
  const client = await db.connect();
  await client.selectGraph(db.name).delete();
  await db.close();
});
test('Real FalkorDB import persists real-source shaped evidence, traversal and six cross-agent memory stages', async () => {
  const result = await service.sync(name);
  assert.equal(result.duplicate, false);
  const graph = await repo.graph();
  assert.ok(graph.nodes.length > 15);
  assert.ok(graph.nodes.every((n) => !n.synthetic));
  const activity = await service.activity();
  assert.equal(activity.tasks.length, 6);
  assert.equal(activity.handoffs.length, 6);
  assert.equal(activity.runs[0].status, 'COMPLETED');
  assert.ok(
    (activity.tasks.find((t) => t.step === 3)?.retrieved_memory_ids as string[]).length >= 3,
  );
  const path = await repo.shortest(
    githubId(name + ':account:example'),
    githubId(name + ':dependency:package.json:express'),
  );
  assert.equal(path.found, true);
  assert.equal(path.edges.length, 3);
  const before = JSON.stringify(await repo.graph());
  const after = await repo.shortest(path.ordered_node_ids[0], path.ordered_node_ids.at(-1)!, true, [
    path.ordered_edge_ids.at(-1)!,
  ]);
  assert.equal(after.found, false);
  assert.equal(
    JSON.stringify((await repo.graph()).edges),
    JSON.stringify(JSON.parse(before).edges),
  );
  const other = new LiveService(db, 'other-tenant', [name]);
  assert.equal((await other.activity()).tasks.length, 0);
  assert.equal((await other.status()).sources.length, 0);
});
test('Repeated imports preserve task IDs and completed timestamps without duplication', async () => {
  const before = (await service.activity()).tasks;
  const result = await service.sync(name);
  assert.equal(result.duplicate, true);
  assert.deepEqual((await service.activity()).tasks, before);
});
test('Historical relationships close at new observation time and remain queryable', async () => {
  const previous = (await service.activity()).runs[0].id as string;
  const saved = await service.snapshot(previous);
  const updated = new LiveService(
    db,
    tenant,
    [name],
    new GitHubReader('', transport({ head: 'c'.repeat(40), protected: true })),
  );
  await updated.sync(name);
  const history = await db.query<{ count: number }>(
    'MATCH ()-[r:GVREL]->() WHERE r.tenant=$tenant AND r.valid_to IS NOT NULL RETURN count(r) AS count',
    { tenant },
  );
  assert.ok(history[0].count > 0);
  assert.ok(!(await repo.graph()).nodes.some((n) => n.label === 'Review branch protection'));
  const old = await db.query<{ count: number }>(
    'MATCH (n:GVEntity) WHERE n.tenant=$tenant AND n.label="Review branch protection" RETURN count(n) AS count',
    { tenant },
  );
  assert.equal(old[0].count, 1);
  assert.deepEqual(await service.snapshot(previous), saved);
  const next = (await service.activity()).runs[0].id as string;
  assert.ok(
    (await service.difference(previous, next)).removed_nodes.some(
      (n: { label: string }) => n.label === 'Review branch protection',
    ),
  );
});
test('A new OS process recovers unfinished live tasks without repeating completed work', async () => {
  const isolatedTenant = 'restart-one';
  const partial = new LiveService(db, isolatedTenant, [name], new GitHubReader('', transport()));
  partial.resume = async () => partial.activity(); // Simulate termination immediately after durable inventory commit.
  const result = await partial.sync(name);
  const resumed = new LiveService(db, isolatedTenant, [name]);
  await resumed.resume(result.run_id, 2);
  const before = (await resumed.activity()).tasks;
  const child = spawnSync(
    process.execPath,
    ['--import', 'tsx', 'server/live-restart-probe.ts', db.name, isolatedTenant, name],
    { encoding: 'utf8', timeout: 20000 },
  );
  assert.equal(child.status, 0, child.stderr);
  const recovered = JSON.parse(child.stdout.trim());
  assert.equal(recovered.tasks.length, 6);
  assert.equal(recovered.runs[0].status, 'COMPLETED');
  for (const task of before)
    assert.deepEqual(
      recovered.tasks.find((t: { id: string }) => t.id === task.id),
      task,
    );
});
test('Failed or unallowlisted source reads preserve the last successful graph', async () => {
  const before = (await repo.graph()).edges;
  await assert.rejects(() => service.sync('other/repository'), /allowlist/);
  const failed = new LiveService(
    db,
    tenant,
    [name],
    new GitHubReader('', transport({ fail: true })),
  );
  await assert.rejects(() => failed.sync(name));
  assert.deepEqual((await repo.graph()).edges, before);
});
test('Public source status is readable; synchronization, resume and simulation require an operator', async () => {
  const token = 'x'.repeat(40),
    server = createApp(repo, token, ['http://localhost'], false, undefined, true, service).listen(
      0,
      '127.0.0.1',
    );
  await once(server, 'listening');
  const base = 'http://127.0.0.1:' + (server.address() as AddressInfo).port;
  try {
    assert.equal((await fetch(base + '/api/integrations/github')).status, 200);
    assert.equal((await fetch(base + '/api/activity')).status, 200);
    assert.equal(
      (
        await fetch(base + '/api/integrations/github/sync', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ repository: name }),
        })
      ).status,
      401,
    );
    const login = await fetch(base + '/api/session', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    const cookie = login.headers.get('set-cookie')!.split(';')[0];
    const source = githubId(name + ':account:example'),
      target = githubId(name + ':dependency:package.json:express'),
      path = await repo.shortest(source, target);
    const simulated = await fetch(base + '/api/what-if/remove-relationship', {
      method: 'POST',
      headers: { 'content-type': 'application/json', cookie },
      body: JSON.stringify({ source, target, edge_id: path.ordered_edge_ids.at(-1) }),
    });
    assert.equal(simulated.status, 200);
    assert.equal((await simulated.json()).path_broken, true);
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
test('Simulation detects alternate paths, persists review decisions and never changes inventory', async () => {
  const source = githubId(name + ':account:example'),
    target = githubId(name + ':dependency:package.json:express');
  const before = await repo.graph(),
    path = await repo.shortest(source, target);
  const alternate = await repo.simulate(source, target, path.ordered_edge_ids[0]);
  assert.equal(alternate.alternate_path_remains, true);
  const broken = await repo.simulate(source, target, path.ordered_edge_ids.at(-1)!);
  assert.equal(broken.path_broken, true);
  assert.deepEqual((await repo.graph()).edges, before.edges);
  await assert.rejects(
    () => repo.reviewSimulation(broken.id, 'APPROVED', 'Skip review'),
    /invalid/,
  );
  assert.equal(
    (
      await repo.reviewSimulation(
        broken.id,
        'UNDER_REVIEW',
        'Verify legitimate build dependencies first',
      )
    ).executed,
    false,
  );
  assert.equal(
    (
      await repo.reviewSimulation(
        broken.id,
        'APPROVED',
        'Recommendation only; no automatic changes',
      )
    ).status,
    'APPROVED',
  );
  await assert.rejects(
    () => repo.reviewSimulation(broken.id, 'REJECTED', 'Cannot reverse final review'),
    /invalid/,
  );
  const stored = await db.query<{ count: number }>(
    'MATCH (s:GVSimulation)-[:GVREVIEW]->(d:GVReview) WHERE s.tenant=$tenant AND d.tenant=$tenant AND s.id=$id RETURN count(d) AS count',
    { tenant, id: broken.id },
  );
  assert.equal(stored[0].count, 2);
  assert.equal((await repo.simulation(broken.id)).status, 'APPROVED');
  assert.equal((await repo.simulation(broken.id)).decisions.length, 2);
  const otherRepo = new Repository(db, 'foreign-proposal-tenant');
  await assert.rejects(() => otherRepo.simulation(broken.id), /not found/);
});
test('A source revert creates fresh active intervals and snapshot history instead of reusing closed edges', async () => {
  const before = (await service.activity()).runs[0].id;
  const reverted = await service.sync(name);
  assert.equal(reverted.duplicate, false);
  assert.notEqual(reverted.run_id, before);
  const source = githubId(name + ':account:example'),
    target = githubId(name + ':dependency:package.json:express');
  assert.equal((await repo.shortest(source, target)).found, true);
  assert.equal(
    (await service.activity()).tasks.filter((t) => t.run_id === reverted.run_id).length,
    6,
  );
});
test('Optional model failure preserves deterministic graph evidence and public questions never invoke it', async () => {
  let calls = 0;
  const token = 'y'.repeat(40),
    server = createApp(
      repo,
      token,
      ['http://localhost'],
      false,
      async () => {
        calls++;
        throw new HttpError(503, 'model_unavailable', 'Provider failed');
      },
      true,
      service,
    ).listen(0, '127.0.0.1');
  await once(server, 'listening');
  const base = 'http://127.0.0.1:' + (server.address() as AddressInfo).port;
  const body = JSON.stringify({
    question: 'Trace source evidence',
    source: githubId(name + ':account:example'),
    target: githubId(name + ':dependency:package.json:express'),
  });
  try {
    assert.equal(
      (
        await fetch(base + '/api/ai/ask', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body,
        })
      ).status,
      200,
    );
    assert.equal(calls, 0);
    const login = await fetch(base + '/api/session', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    const response = await fetch(base + '/api/ai/ask', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        cookie: login.headers.get('set-cookie')!.split(';')[0],
      },
      body,
    });
    assert.equal(response.status, 200);
    const result = await response.json();
    assert.equal(result.path.found, true);
    assert.equal(result.mode, 'deterministic');
    assert.match(result.model_note, /unavailable/);
    assert.equal(calls, 1);
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

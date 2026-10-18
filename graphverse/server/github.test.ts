import { test } from 'node:test';
import assert from 'node:assert/strict';
import { GitHubReader } from './github';
import { transport, name } from './github-fixture';
test('GitHub reads are bounded to public metadata and safe manifest declarations', async () => {
  const result = await new GitHubReader('', transport()).inventory(name);
  assert.ok(result.nodes.length > 5);
  assert.ok(result.nodes.every((n) => !n.synthetic));
  assert.equal(result.nodes.filter((n) => n.label === 'express').length, 1);
  assert.equal(result.nodes.filter((n) => n.label === 'do-not-import').length, 0);
  assert.ok(result.nodes.some((n) => n.label === 'Review branch protection'));
  assert.equal(
    result.fingerprint,
    (await new GitHubReader('', transport()).inventory(name)).fingerprint,
  );
  assert.ok(!JSON.stringify(result).includes('password@example'));
});
test('Private sources and URL injection are rejected before graph ingestion', async () => {
  await assert.rejects(
    () => new GitHubReader('', transport({ private: true })).inventory(name),
    /Private repositories/,
  );
  await assert.rejects(() =>
    new GitHubReader('', transport()).inventory('https://evil.invalid/a/b'),
  );
});
test('GitHub upstream failure is bounded and does not leak response content', async () => {
  await assert.rejects(
    () => new GitHubReader('', transport({ fail: true })).inventory(name),
    (error) => error instanceof Error && !error.message.includes('secret'),
  );
});

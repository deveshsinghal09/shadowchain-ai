import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { Database } from '../server/database';
import { Repository } from '../server/repository';
import { LiveService } from '../server/live';
import { GitHubReader } from '../server/github';
import { name, transport } from '../server/github-fixture';
import { createApp } from '../server/app';
const db = new Database('graphverse_test_' + randomUUID().replaceAll('-', '')),
  live = new LiveService(db, 'browser-live-fixture', [name], new GitHubReader('', transport())),
  token = randomUUID() + randomUUID();
let server: ReturnType<ReturnType<typeof createApp>['listen']>;
test.use({ baseURL: 'http://127.0.0.1:3102' });
test.beforeAll(async () => {
  await live.sync(name);
  server = createApp(
    new Repository(db, 'browser-live-fixture'),
    token,
    ['http://127.0.0.1:3102'],
    false,
    undefined,
    true,
    live,
  ).listen(3102, '127.0.0.1');
  await once(server, 'listening');
});
test.afterAll(async () => {
  server?.closeAllConnections();
  if (server) await new Promise<void>((resolve) => server.close(() => resolve()));
  const client = await db.connect();
  await client.selectGraph(db.name).delete();
  await db.close();
});
test('Visitors inspect persisted source activity without credentials, including mobile', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.addInitScript(() => sessionStorage.setItem('graphverse-intro-seen', 'true'));
  await page.goto('/');
  await expect(page.getByText('FalkorDB connected', { exact: true })).toBeVisible();
  await page.getByLabel('GitHub source and agent activity').click();
  await expect(page.getByRole('heading', { name: 'Connected to the source.' })).toBeVisible();
  await expect(page.locator('.source-tasks li')).toHaveCount(6);
  await expect(page.getByRole('button', { name: 'Operator sign-in to sync' })).toBeVisible();
  await expect(page.getByText('COMPLETED · 6/6 stages')).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
  ).toBeTruthy();
  await page.screenshot({ path: 'test-results/live-source-mobile.png' });
  expect(errors).toEqual([]);
});
test('Operator traces source evidence, simulates a removal, reviews and saves the investigation', async ({
  page,
}) => {
  expect((await page.request.post('/api/session', { data: { token } })).ok()).toBeTruthy();
  await page.addInitScript(() => sessionStorage.setItem('graphverse-intro-seen', 'true'));
  await page.goto('/');
  await expect(page.getByText('FalkorDB connected', { exact: true })).toBeVisible();
  await page.getByLabel('Trace a relationship', { exact: true }).click();
  await page.getByRole('button', { name: 'Trace relationship', exact: true }).click();
  await expect(page.getByText('PATH DISCOVERED', { exact: true })).toBeVisible();
  await page.getByText('What if a relationship is removed?', { exact: true }).click();
  await page.getByRole('button', { name: 'Simulate removal: DECLARES DEPENDENCY' }).click();
  await expect(page.getByText('This modeled path is broken.', { exact: true })).toBeVisible();
  await page
    .getByLabel('Simulation review note')
    .fill('Review legitimate build requirements before changing declarations.');
  await page.getByRole('button', { name: 'Start human review', exact: true }).click();
  await page.getByRole('button', { name: 'Approve recommendation only', exact: true }).click();
  await expect(page.getByText('Proposal: APPROVED', { exact: true })).toBeVisible();
  await page.getByLabel('Investigation title').fill('Verified repository relationship review');
  await page.getByRole('button', { name: 'Save to memory', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'saved in FalkorDB' })).toBeVisible();
  await page.reload();
  await page.getByLabel('Memory orbit', { exact: true }).click();
  await page.getByRole('button', { name: /Verified repository relationship review/ }).click();
  await expect(page.getByText('PATH DISCOVERED', { exact: true })).toBeVisible();
});

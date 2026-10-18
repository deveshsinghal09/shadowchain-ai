import assert from 'node:assert/strict';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { chromium } from '@playwright/test';

// Operator-run smoke check. No traces, provider requests, AWS calls or sample data writes.
const base = 'https://shadowchain-ai.vercel.app';
const tokenDirectory = join(
  process.env.LOCALAPPDATA ?? '',
  'ShadowChainAI',
  'railway-2f4a53bd-bd2a-4db6-8098-45fe33570a01',
);
const token = readFileSync(join(tokenDirectory, 'operator.token'), 'utf8').trim();
const browser = await chromium.launch({
  args: ['--enable-webgl', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
});
const checks: string[] = [];
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.setDefaultTimeout(60000);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  for (const path of ['/api/health', '/api/v1/health', '/api/v1/ready']) {
    const response = await context.request.get(base + path);
    assert.equal(response.status(), 200, path);
    checks.push(`${path}: HTTP 200`);
  }
  const initialSession = await context.request.get(base + '/api/session');
  const publicBrowsing =
    initialSession.status() === 200 && (await initialSession.json()).access === 'public';
  assert.equal(
    (await context.request.get(base + '/api/graph')).status(),
    publicBrowsing ? 200 : 401,
  );
  assert.equal((await context.request.get(base + '/api/v1/dashboard/summary')).status(), 401);
  assert.equal((await context.request.get(base + '/api/investigations')).status(), 401);
  assert.equal(
    (
      await context.request.post(base + '/api/investigations', {
        data: {
          id: crypto.randomUUID(),
          title: 'Anonymous attempt',
          source: 'missing',
          target: 'missing',
        },
        headers: { Origin: base },
      })
    ).status(),
    401,
  );
  checks.push('Private investigation reads/writes and Python data requests require authentication');
  await page.addInitScript(() => sessionStorage.setItem('graphverse-intro-seen', 'true'));
  if (publicBrowsing) {
    await page.goto(base);
    await page.getByText('FalkorDB connected', { exact: true }).waitFor();
    assert.equal(await page.getByRole('dialog').isVisible(), false);
    await page.getByLabel('Memory orbit', { exact: true }).click();
    await page.getByText('Saved investigations are private.', { exact: true }).waitFor();
    checks.push(
      'Fresh visitor opens and explores the website without a token or mandatory sign-in dialog',
    );
  }
  const login = await context.request.post(base + '/api/session', {
    data: { token },
    headers: { Origin: base },
  });
  assert.equal(login.status(), 200);
  const cookies = await context.cookies();
  const session = cookies.find((cookie) => cookie.name === 'graphverse_session');
  assert.ok(session?.httpOnly && session.secure && session.sameSite === 'Strict');
  checks.push('Same-origin login: HttpOnly + Secure + SameSite=Strict session');
  const graphResponse = await context.request.get(base + '/api/graph');
  assert.equal(graphResponse.status(), 200);
  const graph = await graphResponse.json();
  assert.equal(graph.dataset, 'github-live-v1');
  assert.ok(graph.nodes.length > 0 && graph.edges.length > 0);
  assert.ok(graph.nodes.every((n: { synthetic: boolean }) => !n.synthetic));
  const activity = await (await context.request.get(base + '/api/activity')).json();
  assert.equal(activity.runs[0].status, 'COMPLETED');
  assert.equal(
    activity.tasks.filter((t: { run_id: string }) => t.run_id === activity.runs[0].id).length,
    6,
  );
  checks.push(
    'Real FalkorDB read: imported public GitHub inventory, six persisted stages, no synthetic seed',
  );
  const denied = await context.request.post(base + '/api/paths/shortest', {
    data: { source: 'missing', target: 'missing' },
    headers: { Origin: 'https://unauthorized.invalid' },
  });
  assert.equal(denied.status(), 403);
  checks.push('Foreign-origin mutation denied');
  await page.goto(base);
  await page.getByText('FalkorDB connected', { exact: true }).waitFor();
  await page.getByText('IMPORTED OBSERVATIONS', { exact: false }).waitFor();
  await page.locator('canvas').waitFor();
  await page.reload();
  await page.getByText('FalkorDB connected', { exact: true }).waitFor();
  checks.push('Browser renders imported GitHub inventory in WebGL and retains login after reload');
  await page.getByLabel('GitHub source and agent activity').click();
  await page.getByRole('heading', { name: 'Connected to the source.' }).waitFor();
  await page.locator('.source-tasks li').first().waitFor();
  assert.equal(await page.locator('.source-tasks li').count(), 6);
  checks.push('Source inspector renders real persisted task activity and GitHub provenance');
  mkdirSync('test-results', { recursive: true });
  await page.screenshot({ path: 'test-results/deployed-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.reload();
  // Capture the empty mobile viewport before asserting its responsive status.
  await page.screenshot({ path: 'test-results/deployed-mobile.png' });
  await page.getByText('FalkorDB connected', { exact: true }).waitFor({ state: 'attached' });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: 'test-results/deployed-mobile.png' });
  assert.deepEqual(errors, []);
  checks.push('Mobile: no horizontal overflow; no browser JavaScript exceptions');
  const receipt = {
    verified_at: new Date().toISOString(),
    base,
    checks,
    live_inventory_connected: true,
    live_gemini_verified: false,
    public_browsing: publicBrowsing,
  };
  writeFileSync('test-results/deployment-receipt.json', JSON.stringify(receipt, null, 2));
  console.log(JSON.stringify(receipt, null, 2));
} finally {
  await browser.close();
}

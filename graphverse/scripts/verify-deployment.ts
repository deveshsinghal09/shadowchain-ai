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
  assert.equal((await context.request.get(base + '/api/graph')).status(), 401);
  assert.equal((await context.request.get(base + '/api/v1/dashboard/summary')).status(), 401);
  checks.push('Both API services reject unauthenticated graph/data requests');
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
  assert.equal(graph.dataset, 'live-unconfigured');
  assert.deepEqual(graph.nodes, []);
  assert.deepEqual(graph.edges, []);
  checks.push('Real FalkorDB read: empty live graph, no synthetic seed');
  const denied = await context.request.post(base + '/api/paths/shortest', {
    data: { source: 'missing', target: 'missing' },
    headers: { Origin: 'https://unauthorized.invalid' },
  });
  assert.equal(denied.status(), 403);
  checks.push('Foreign-origin mutation denied');
  await page.addInitScript(() => sessionStorage.setItem('graphverse-intro-seen', 'true'));
  await page.goto(base);
  await page.getByText('FalkorDB connected', { exact: true }).waitFor();
  await page.getByText('NO INVENTORY CONNECTED', { exact: false }).waitFor();
  await page.locator('canvas').waitFor();
  await page.reload();
  await page.getByText('FalkorDB connected', { exact: true }).waitFor();
  checks.push('Browser renders WebGL, shows honest empty state, and retains login after reload');
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
    live_inventory_connected: false,
    live_gemini_verified: false,
  };
  writeFileSync('test-results/deployment-receipt.json', JSON.stringify(receipt, null, 2));
  console.log(JSON.stringify(receipt, null, 2));
} finally {
  await browser.close();
}

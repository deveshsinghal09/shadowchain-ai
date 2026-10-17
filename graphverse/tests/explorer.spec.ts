import { test, expect } from '@playwright/test';
import { operatorToken } from '../server/config';
test.beforeEach(async ({ page }) => {
  // Authenticate using the API so tokens never appear in browser traces/screenshots.
  const response = await page.request.post('/api/session', { data: { token: operatorToken() } });
  expect(response.ok()).toBeTruthy();
  await page.addInitScript(() => sessionStorage.setItem('graphverse-intro-seen', 'true'));
  await page.goto('/');
  await expect(page.getByText('FalkorDB connected', { exact: true })).toBeVisible();
});
test('rendered universe, live question, hyperdrive and preserved evidence', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (msg) => {
    if (msg.type() === 'error' && msg.text().includes('THREE')) errors.push(msg.text());
  });
  await expect(page.locator('canvas')).toBeVisible();
  await page.waitForTimeout(3500);
  await page.screenshot({ path: 'test-results/universe-desktop.png' });
  await page.getByLabel('ASK YOUR UNIVERSE').fill('How is Alice Chen connected to Research vault?');
  await page.getByLabel('Ask graph assistant').click();
  await expect(page.getByText('PATH DISCOVERED', { exact: true })).toBeVisible();
  await expect(page.locator('.path-stop')).toHaveCount(5);
  await page.getByRole('button', { name: 'Travel this path' }).click();
  await expect(page.getByText('HYPERDRIVE ACTIVE')).toBeVisible();
  await page.getByLabel('Pause journey').click();
  await expect(page.getByLabel('Resume journey')).toBeVisible();
  await page.screenshot({ path: 'test-results/universe-path.png' });
  await page.getByLabel('Resume journey').click();
  await page.getByRole('button', { name: 'Skip', exact: true }).click();
  await expect(page.getByText('DESTINATION REACHED')).toBeVisible();
  await page.getByLabel('Investigation title').fill('Browser verified trust chain');
  await page.getByRole('button', { name: 'Save to memory' }).click();
  await expect(page.getByRole('status')).toContainText('saved in FalkorDB');
  await page.reload();
  await page.getByLabel('Memory orbit', { exact: true }).click();
  await page
    .getByRole('button', { name: /Browser verified trust chain/ })
    .first()
    .click();
  await expect(page.locator('.path-stop')).toHaveCount(5);
  expect(errors).toEqual([]);
});
test('cinematic introduction is immediately skippable', async ({ page }) => {
  await page.getByLabel('Explorer guide', { exact: true }).click();
  await page.getByRole('button', { name: 'Play cinematic introduction' }).click();
  await expect(page.getByText('Explore the universe hidden inside your data.')).toBeVisible();
  await page.getByRole('button', { name: 'Skip intro' }).click();
  await expect(page.getByText('Explore the universe hidden inside your data.')).not.toBeVisible();
  await expect(page.getByLabel('Search entities')).toBeVisible();
});
test('search, neighbors, clusters and quality controls', async ({ page }) => {
  await page.getByLabel('Search entities').fill('Atlas');
  await page
    .locator('.search-results')
    .getByRole('button', { name: /Atlas gateway/ })
    .click();
  await expect(page.getByRole('heading', { name: 'Atlas gateway' })).toBeVisible();
  await page.getByRole('button', { name: 'Expand neighbors' }).click();
  await expect(page.getByRole('status')).toContainText('neighboring relationships');
  await page.getByLabel('Cluster galaxies').click();
  await expect(
    page.getByRole('heading', { name: 'Five regions. One connected graph.' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Isolate', exact: true }).first().click();
  await page.getByRole('button', { name: 'Reveal all regions' }).click();
  await page.getByLabel('Display settings').click();
  await page.getByLabel('Render quality').selectOption('performance');
  await page.getByLabel('Ambient motion').uncheck();
  await page.getByLabel('Close display settings').click();
  await page.getByLabel('Fit universe').click();
});
test('mobile layout has no horizontal overflow and supports reduced motion', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.reload();
  await page.getByLabel('Trace a relationship', { exact: true }).click();
  await page.getByRole('button', { name: 'Trace relationship', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Show destination' })).toBeVisible();
  await page.getByRole('button', { name: 'Show destination' }).click();
  await expect(page.getByText('DESTINATION REACHED')).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
  ).toBeTruthy();
  await page.screenshot({ path: 'test-results/universe-mobile.png' });
});

import { test, expect } from '@playwright/test';

test('trade inspection, funding job, reload recovery, scenarios, and model empty state', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByRole('heading', {name: 'At-risk trades'})).toBeVisible();
  await page.getByRole('button', {name: 'T-FX-GAP', exact: true}).click();
  await expect(page.getByRole('region', {name: 'Trade detail'})).toBeVisible();
  await expect(page.getByText('FX receipts in this cash pool')).toBeVisible();
  await page.getByRole('button', {name: 'Close detail'}).click();
  await page.getByRole('button', {name: 'Funding', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Funding plan'})).toBeVisible();
  await page.getByLabel('Liquidity limit (USD)').fill('15000.00');
  const submission = page.waitForRequest(r => r.url().endsWith('/funding/recompute'));
  await page.getByRole('button', {name: 'Recompute funding'}).click();
  expect((await submission).postDataJSON().liquidity_limit).toBe('15000.00');
  await expect(page.getByRole('status')).toContainText('Job');
  await page.reload();
  await page.getByRole('button', {name: 'Funding', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Open recomputed plan'})).toBeVisible({timeout: 20000});
  await page.getByRole('button', {name: 'Open recomputed plan'}).click();
  await expect(page.getByLabel('Liquidity limit (USD)')).toHaveValue('15000.00');
  await page.getByRole('button', {name: 'Scenarios', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Matched policy comparison'})).toBeVisible();
  await expect(page.locator('tbody tr')).toHaveCount(4);
  await expect(page.locator('tbody')).toContainText('15000.00');
  await page.getByRole('button', {name: 'Model health', exact: true}).click();
  await expect(page.getByText('No model selected.', {exact: false})).toBeVisible();
  expect(errors).toEqual([]);
});

test('structured API errors surface and the dashboard recovers', async ({ page }) => {
  await page.route('**/api/v1/runs', route => route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({message: 'Artifacts temporarily unavailable'})}));
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('Artifacts temporarily unavailable');
  await page.unroute('**/api/v1/runs');
  await page.getByRole('button', {name: 'Refresh runs'}).click();
  await expect(page.getByRole('button', {name: 'T-FX-GAP', exact: true})).toBeVisible();
});

test('mobile layout retains navigation and table access', async ({ page }) => {
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('/');
  await expect(page.getByRole('button', {name: 'T-FX-GAP', exact: true})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole('button', {name: 'Funding', exact: true}).click();
  await expect(page.getByLabel('Liquidity limit (USD)')).toBeVisible();
});

test('an older trade detail response cannot replace the latest selection', async ({ page }) => {
  let release;
  const delayed = new Promise(resolve => { release = resolve; });
  let observed;
  const requested = new Promise(resolve => { observed = resolve; });
  await page.route('**/trades/T-FX-GAP', async route => {
    const response = await route.fetch();
    observed();
    await delayed;
    await route.fulfill({response});
  });
  await page.goto('/');
  await page.getByRole('button', {name: 'T-FX-GAP', exact: true}).click();
  await requested;
  await page.getByRole('button', {name: 'T-TIMELY', exact: true}).click();
  await expect(page.getByRole('region', {name: 'Trade detail'}).getByRole('heading', {name: 'T-TIMELY', exact: true})).toBeVisible();
  const response = page.waitForResponse(r => r.url().endsWith('/trades/T-FX-GAP'));
  release();
  await response;
  await expect(page.getByRole('region', {name: 'Trade detail'}).getByRole('heading', {name: 'T-TIMELY', exact: true})).toBeVisible();
});

test('appearance follows system preference and explicit themes persist across reloads', async ({ page }, testInfo) => {
  await page.emulateMedia({colorScheme: 'dark'});
  await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.getByRole('button', {name: 'T-FX-GAP', exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Light theme', exact: true}).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.getByRole('button', {name: 'Light theme', exact: true})).toHaveAttribute('aria-pressed', 'true');
  await page.screenshot({path: testInfo.outputPath('dashboard-light.png'), fullPage: true});
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.getByRole('button', {name: 'Dark theme', exact: true}).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.screenshot({path: testInfo.outputPath('dashboard-dark.png'), fullPage: true});
  await page.getByRole('button', {name: 'System theme', exact: true}).click();
  await page.emulateMedia({colorScheme: 'light'});
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('trade search and reset help narrow the visible watchlist', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', {name: 'T-FX-GAP', exact: true})).toBeVisible();
  await page.getByRole('textbox', {name: 'Search this page'}).fill('FX-GAP');
  await expect(page.locator('tbody tr')).toHaveCount(1);
  await page.getByRole('button', {name: 'Reset filters'}).click();
  await expect(page.locator('tbody tr')).toHaveCount(3);
});

for (const theme of ['light', 'dark']) {
  test(`${theme} theme has readable text and consistent component typography`, async ({ page }, testInfo) => {
    await page.goto('/');
    await page.getByRole('button', {name: `${theme === 'light' ? 'Light' : 'Dark'} theme`, exact: true}).click();
    await expect(page.getByRole('button', {name: 'T-FX-GAP', exact: true})).toBeVisible();
    // Avoid measuring a halfway frame in component hover/theme transitions.
    await page.addStyleTag({content: '* { transition: none !important; }'});
    for (const tab of ['Trades', 'Funding', 'Scenarios', 'Model health']) {
      await page.getByRole('button', {name: tab, exact: true}).click();
      if (tab === 'Funding') await expect(page.getByLabel('Liquidity limit (USD)')).toBeEnabled();
      if (tab === 'Scenarios') await expect(page.locator('tbody tr')).toHaveCount(4);
      const failures = await page.evaluate(() => {
        const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
        const ctx = canvas.getContext('2d');
        const rgba = color => {ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = color; ctx.fillRect(0, 0, 1, 1); return [...ctx.getImageData(0, 0, 1, 1).data].map((n, i) => i === 3 ? n / 255 : n);};
        const blend = (fg, bg) => fg.slice(0, 3).map((v, i) => v * fg[3] + bg[i] * (1 - fg[3])).concat(1);
        const luminance = channels => channels.slice(0, 3).map(v => {v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;}).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
        const background = element => {
          const parents = []; for (let p = element; p; p = p.parentElement) parents.unshift(p);
          return parents.reduce((bg, p) => blend(rgba(getComputedStyle(p).backgroundColor), bg), [255, 255, 255, 1]);
        };
        const failures = [];
        const slots = 'button,input,native-select,label,table-head,table-cell,card-title,card-description,badge';
        for (const el of document.querySelectorAll(slots.split(',').map(s => `[data-slot="${s}"]`).join(',') + ',.page-description,.context,footer')) {
          if (!el.getClientRects().length || el.matches(':disabled') || (!el.textContent.trim() && !el.matches('input'))) continue;
          const style = getComputedStyle(el), bg = background(el), fg = blend(rgba(style.color), bg);
          const a = luminance(fg), b = luminance(bg), ratio = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
          const slot = el.getAttribute('data-slot');
          if (ratio < 4.5) failures.push(`${slot || el.tagName}: contrast ${ratio.toFixed(2)} ${el.textContent.slice(0, 45)}`);
          if (['button', 'input', 'native-select', 'label', 'table-head', 'table-cell', 'card-title'].includes(slot) && style.fontSize !== '14px') failures.push(`${slot}: font ${style.fontSize}`);
          if (['card-description', 'badge'].includes(slot) && style.fontSize !== '12px') failures.push(`${slot}: helper font ${style.fontSize}`);
        }
        return failures;
      });
      expect(failures, `${theme} / ${tab}`).toEqual([]);
    }
    await page.getByRole('button', {name: 'Trades', exact: true}).click();
    await page.screenshot({path: testInfo.outputPath(`${theme}-shadcn.png`), fullPage: true});
  });
}

import { test, expect } from '@playwright/test';

test('trade inspection, funding job, reload recovery, scenarios, and model empty state', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByRole('heading', {name: 'Trade watchlist'})).toBeVisible();
  await page.getByText('How to read this page', {exact: true}).click();
  await expect(page.getByText('Failure probability assumes no extra reserve', {exact: false})).toBeVisible();
  await page.getByText('How to read this page', {exact: true}).click();
  await expect(page.getByRole('columnheader', {name: 'Rule score', exact: true})).toHaveCount(0);
  await expect(page.getByRole('combobox', {name: 'Minimum rule score', exact: true})).toHaveCount(0);
  await expect(page.getByRole('combobox', {name: 'Sort', exact: true})).toContainText('Nearest deadline');
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
  await expect(page.getByRole('status').filter({hasText:'Job'})).toContainText('Job');
  await page.reload();
  await page.getByRole('button', {name: 'Funding', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Open recomputed plan'})).toBeVisible({timeout: 20000});
  await page.getByRole('button', {name: 'Open recomputed plan'}).click();
  await expect(page.getByLabel('Liquidity limit (USD)')).toHaveValue('15000.00');
  await page.getByRole('button', {name: 'Scenarios', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Compare funding policies',exact:true})).toBeVisible();
  await expect(page.locator('tbody tr')).toHaveCount(4);
  await expect(page.locator('tbody')).toContainText('USD 15,000');
  await page.getByRole('button', {name: 'Model health', exact: true}).click();
  await expect(page.getByText('No model selected.', {exact: false})).toBeVisible();
  expect(errors).toEqual([]);
});

test('structured API errors surface and the dashboard recovers', async ({ page }) => {
  await page.route('**/api/v1/runs', route => route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({message: 'Artifacts temporarily unavailable'})}));
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('Artifacts temporarily unavailable');
  await page.unroute('**/api/v1/runs');
  await page.getByRole('button', {name: 'Refresh saved runs'}).click();
  await expect(page.getByRole('button', {name: 'T-FX-GAP', exact: true})).toBeVisible();
});

test('mobile layout retains navigation and table access', async ({ page }) => {
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('/');
  await expect(page.getByRole('button', {name: 'T-FX-GAP', exact: true})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole('button', {name: 'Toggle Sidebar'}).click();
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
        const slots = 'button,input,select-trigger,label,table-head,table-cell,card-title,card-description,badge';
        for (const el of document.querySelectorAll(slots.split(',').map(s => `[data-slot="${s}"]`).join(',') + ',.page-description,.context,footer')) {
          if (!el.getClientRects().length || el.matches(':disabled') || (!el.textContent.trim() && !el.matches('input'))) continue;
          const style = getComputedStyle(el), bg = background(el), fg = blend(rgba(style.color), bg);
          const a = luminance(fg), b = luminance(bg), ratio = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
          const slot = el.getAttribute('data-slot');
          if (ratio < 4.5) failures.push(`${slot || el.tagName}: contrast ${ratio.toFixed(2)} ${el.textContent.slice(0, 45)}`);
          if (['button', 'input', 'select-trigger', 'label', 'table-head', 'table-cell', 'card-title'].includes(slot) && style.fontSize !== '14px') failures.push(`${slot}: font ${style.fontSize}`);
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


test('custom dropdowns support keyboard selection, disabled options and clearing filters', async ({page}) => {
  await page.goto('/');
  await expect(page.getByRole('button', {name:'T-FX-GAP', exact:true})).toBeVisible();
  const sort = page.getByRole('combobox', {name:'Sort', exact:true});
  await sort.click();
  await expect(page.getByRole('option', {name:'Fail probability', exact:true})).toHaveAttribute('aria-disabled', 'true');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('listbox')).toHaveCount(0);
  await expect(sort).toBeFocused();
  const state = page.getByRole('combobox', {name:'Instruction state', exact:true});
  await state.focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('listbox')).toBeVisible();
  await page.getByRole('option', {name:'CONFIRMED', exact:true}).click();
  await expect(state).toContainText('CONFIRMED');
  await expect(page.getByRole('listbox')).toHaveCount(0);
  await state.click();
  await page.getByRole('option', {name:'All states', exact:true}).click();
  await expect(state).toContainText('All states');
  await expect(page.locator('tbody tr')).toHaveCount(3);
});

test('sidebar icon loads and navigation stays within expanded, collapsed and mobile sidebars', async ({page}) => {
  await page.goto('/');
  const symbols = [];
  for (const name of ['Experiments','Trades','Funding','Scenarios','Model health']) {
    const svg = page.getByRole('button', {name, exact:true}).locator('svg');
    await expect(svg).toBeVisible();
    symbols.push(await svg.innerHTML());
  }
  expect(new Set(symbols).size).toBe(5);
  const icon = page.getByRole('img', {name:'T+1 Settlement app icon'});
  await expect(icon).toBeVisible();
  await expect.poll(() => icon.evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true);
  const favicon = await page.locator('link[rel="icon"]').getAttribute('href');
  expect((await page.request.get(favicon)).status()).toBe(200);
  await page.getByRole('button', {name:'Toggle Sidebar'}).click();
  await expect(page.locator('[data-slot="sidebar"][data-state="collapsed"]')).toBeVisible();
  for (const name of ['Experiments','Trades','Funding','Scenarios','Model health']) {
    const button = page.getByRole('button', {name,exact:true});
    await expect(button).toBeVisible();
    expect(await button.evaluate(el => {const side=el.closest('[data-slot="sidebar-container"]').getBoundingClientRect();const box=el.getBoundingClientRect();return box.left>=side.left && box.right<=side.right;})).toBe(true);
  }
  await expect(icon).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button', {name:'Toggle Sidebar'}).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(icon).toBeVisible();
  await expect.poll(() => icon.evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true);
  await page.getByRole('button', {name:'Funding',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByLabel('Liquidity limit (USD)')).toBeVisible();
});


test('all workspace pages fit the remaining width when the sidebar expands', async ({page}) => {
  await page.goto('/');
  await expect(page.getByRole('button', {name:'T-FX-GAP',exact:true})).toBeVisible();
  for (const width of [1280,1024]) {
    await page.setViewportSize({width,height:900});
    for (const expanded of [true,false,true]) {
      const sidebar = page.locator('[data-slot="sidebar"][data-state]');
      if ((await sidebar.getAttribute('data-state') === 'expanded') !== expanded) await page.getByRole('button',{name:'Toggle Sidebar'}).click();
      await expect(sidebar).toHaveAttribute('data-state',expanded?'expanded':'collapsed');
      for (const name of ['Trades','Funding','Scenarios','Model health']) {
        await page.getByRole('button',{name,exact:true}).click();
        if (name==='Scenarios') await expect(page.locator('tbody tr')).toHaveCount(4);
        await expect.poll(()=>page.evaluate(()=>{
          const main=document.querySelector('[data-slot="sidebar-inset"]').getBoundingClientRect();
          const content=document.querySelector('.page-content').getBoundingClientRect();
          return document.documentElement.scrollWidth<=innerWidth && main.right<=innerWidth+1 && content.left>=main.left-1 && content.right<=main.right+1;
        })).toBe(true);
      }
    }
  }
});

test('evaluation calendar blocks unavailable dates and offers 38 timezones', async ({page}) => {
  await page.goto('/');
  await expect(page.getByText(/Available:/)).toBeVisible();
  await expect(page.getByText('Evaluation time (UTC)',{exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'Evaluation date',exact:true}).click();
  await expect(page.getByRole('button',{name:'2026-10-12',exact:true})).toBeDisabled();
  await expect(page.getByRole('button',{name:'Next month',exact:true})).toBeDisabled();
  await page.keyboard.press('Escape');
  await page.getByRole('combobox',{name:'Evaluation timezone',exact:true}).click();
  await expect(page.getByRole('option')).toHaveCount(38);
  await page.getByRole('option',{name:'Asia/Kolkata',exact:true}).click();
  await expect(page.getByText(/Available:/)).toContainText('Asia/Kolkata');
});

test('calendar evaluation publishes a new run without duplicating controls', async ({page}) => {
  await page.goto('/');
  await expect(page.getByText(/Available:/)).toBeVisible();
  const previous = page.url();
  await page.getByLabel('Evaluation local time',{exact:true}).fill('12:15');
  await page.getByRole('button',{name:'Apply evaluation',exact:true}).click();
  await expect.poll(()=>page.url()).not.toBe(previous);
  await expect(page.getByRole('button',{name:'Evaluation date',exact:true})).toHaveCount(1);
  await expect(page.getByRole('button',{name:'Apply evaluation',exact:true})).toBeEnabled();
});

test('online date caps the calendar and deadlines use hours or days', async ({page}) => {
  await page.route('**/api/v1/time', route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({utc_now:'2026-10-14T12:30:00Z',source:'test://clock',stale:false})}));
  await page.goto('/');
  await expect(page.getByText(/Online time \(UTC\):/)).toBeVisible();
  await page.getByRole('button',{name:'Evaluation date',exact:true}).click();
  await expect(page.getByRole('button',{name:'2026-10-15',exact:true})).toBeDisabled();
  await expect(page.getByRole('button',{name:'2026-10-14',exact:true})).toBeEnabled();
  await page.keyboard.press('Escape');
  await page.getByLabel('Evaluation local time',{exact:true}).fill('13:00');
  await expect(page.getByRole('button',{name:'Apply evaluation',exact:true})).toBeDisabled();
  await expect(page.locator('.deadline').first()).toContainText(/h|d/);
  for (const value of await page.locator('.deadline').allTextContents()) expect(value).not.toContain('min');
});

test('readable trade aliases can be searched while research IDs stay available', async ({page}) => {
  await page.route('**/api/v1/runs/*/trades?*', async route => {
    const response = await route.fetch();
    const payload = await response.json();
    payload.items = payload.items.map(t => ({...t, trade_label: `Trade example · ${t.trade_id}`, security_label: 'Sample equity 12', counterparty_label: 'Sample counterparty 8'}));
    await route.fulfill({response, json: payload});
  });
  await page.goto('/');
  const trade = page.getByRole('button', {name:'Trade example · T-FX-GAP', exact:true});
  await expect(trade).toBeVisible();
  await page.getByRole('textbox', {name:'Search this page'}).fill('Sample equity 12');
  await expect(page.locator('tbody tr')).toHaveCount(3);
  await trade.click();
  await page.getByText('Research identifiers', {exact:true}).click();
  await expect(page.locator('.technical-details code').first()).toHaveText('T-FX-GAP');
  await expect(page.getByText('Several trades can share a cash pool.', {exact:false})).toBeVisible();
});

test('guided experiment creates data, trains real models, recovers on refresh and exports', async ({page}, testInfo) => {
  test.setTimeout(180000);
  await page.goto('/?tab=Experiments');
  await page.getByRole('button',{name:'Experiments',exact:true}).click();
  await page.getByLabel('Number of trades',{exact:true}).fill('200');
  await page.getByLabel('Trading days',{exact:true}).fill('4');
  await page.getByRole('button',{name:'Create dataset',exact:true}).click();
  const job=page.getByRole('region',{name:'Experiment job'});
  await expect(job).toContainText('SUCCEEDED',{timeout:60000});
  await page.reload();
  await expect(job).toContainText('SUCCEEDED');
  await expect(page.getByRole('combobox',{name:'Saved dataset',exact:true})).toContainText('200 trades');
  const preparation=page.waitForResponse(r=>r.url().endsWith('/workflow/jobs')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'Prepare complete experiment',exact:true}).click();
  const accepted=await (await preparation).json();
  await expect(job).toContainText(accepted.job_id);
  await expect(job).toContainText('SUCCEEDED',{timeout:120000});
  await expect(page.getByRole('link',{name:'Download experiment JSON'})).toBeVisible();
  await page.addStyleTag({content:'* { transition: none !important; animation: none !important; }'});
  for (const theme of ['Light','Dark']) {
    await page.getByRole('button',{name:theme+' theme',exact:true}).click();
    await page.screenshot({path:testInfo.outputPath('experiments-'+theme.toLowerCase()+'.png'),fullPage:true});
  }
  const download=page.waitForEvent('download');
  await page.getByRole('link',{name:'Download experiment JSON'}).click();
  expect((await download).suggestedFilename()).toMatch(/experiment-.*\.json/);
  await page.getByRole('button',{name:'Open Trades',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Trade watchlist',exact:true})).toBeVisible();
  // Creating again must honor the new inputs even while an existing dataset is selected.
  await page.getByRole('button',{name:'Experiments',exact:true}).click();
  await page.getByLabel('Dataset seed',{exact:true}).fill('84');
  const generation = page.waitForResponse(r=>r.url().endsWith('/workflow/jobs')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'Create dataset',exact:true}).click();
  const created = await generation;
  expect(created.request().postDataJSON().dataset_id).toBeNull();
  await expect(job).toContainText((await created.json()).job_id);
  await expect(job).toContainText('SUCCEEDED',{timeout:60000});
  await expect(page.getByRole('combobox',{name:'Saved dataset',exact:true})).toContainText('seed 84');
  await expect(page.getByRole('combobox',{name:'Risk prediction model',exact:true})).toContainText('No risk model');
});

test('experiment job failures remain readable after refresh and offer retry', async ({page}) => {
  const job={job_id:'job-controlled-failure',status:'FAILED',request:{kind:'generate'},result:null,error:'Provider unavailable. Check connectivity and retry.'};
  await page.route('**/api/v1/workflow/jobs',route=>route.fulfill({status:202,contentType:'application/json',body:JSON.stringify(job)}));
  await page.route('**/api/v1/jobs/job-controlled-failure',route=>route.fulfill({contentType:'application/json',body:JSON.stringify(job)}));
  await page.goto('/?tab=Experiments');
  await page.getByRole('button',{name:'Create dataset',exact:true}).click();
  await expect(page.getByRole('region',{name:'Experiment job'})).toContainText('Provider unavailable');
  await page.reload();
  await expect(page.getByRole('button',{name:'Retry preparation',exact:true})).toBeVisible();
});

test('funding cancellation stays tracked through refresh until actually stopped', async ({page}) => {
  let status = 'RUNNING';
  const job = () => ({job_id:'job-cancel-test',status,attempts:1,result:null,error:null});
  await page.route('**/api/v1/runs/*/funding/recompute',route=>route.fulfill({status:202,json:job()}));
  await page.route('**/api/v1/jobs/job-cancel-test',route=>route.fulfill({json:job()}));
  await page.route('**/api/v1/jobs/job-cancel-test/cancel',route=>{
    status='CANCEL_REQUESTED';
    return route.fulfill({json:job()});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Funding',exact:true}).click();
  await page.getByRole('button',{name:'Recompute funding',exact:true}).click();
  await page.getByRole('button',{name:'Cancel job',exact:true}).click();
  await expect(page.locator('.job')).toContainText('Cancellation requested. Waiting');
  await expect(page.getByRole('button',{name:'Recompute funding',exact:true})).toBeDisabled();
  await page.reload();
  await expect(page.locator('.job')).toContainText('Job CANCEL_REQUESTED');
  status='CANCELED';
  await expect(page.locator('.job')).toContainText('Job CANCELED',{timeout:10000});
  await expect(page.getByRole('button',{name:'Recompute funding',exact:true})).toBeEnabled();
});

test('missing persisted experiment job does not block a fresh workspace', async ({page}) => {
  await page.addInitScript(()=>localStorage.setItem('experiment-job','job-removed'));
  await page.route('**/api/v1/jobs/job-removed',route=>route.fulfill({status:404,json:{message:'Job not found.'}}));
  await page.goto('/?tab=Experiments');
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Create dataset',exact:true})).toBeEnabled();
  expect(await page.evaluate(()=>localStorage.getItem('experiment-job'))).toBeNull();
});

test('switching experiment records cannot mix old results and new exports', async ({page}) => {
  const first='run-'+'a'.repeat(20), second='run-'+'b'.repeat(20);
  await page.route('**/api/v1/workflow/catalog',route=>route.fulfill({json:{datasets:[],risk_models:[],timing_models:[],errors:[],experiments:[{experiment_id:first},{experiment_id:second}]}}));
  let release, observed;
  const gate=new Promise(resolve=>{release=resolve;});
  const requested=new Promise(resolve=>{observed=resolve;});
  const record=id=>({configuration:{risk_model_id:null},outputs:{reference_run:id},marker:id});
  await page.route('**/api/v1/workflow/experiments/'+first,async route=>{
    observed(); await gate; await route.fulfill({json:record(first)});
  });
  await page.route('**/api/v1/workflow/experiments/'+second,route=>route.fulfill({json:record(second)}));
  await page.goto('/?tab=Experiments');
  const picker=page.getByRole('combobox',{name:'Saved experiment',exact:true});
  await picker.click();
  await page.getByRole('option',{name:'Experiment 1 · aaaaaa',exact:true}).click();
  await requested;
  await picker.click();
  await page.getByRole('option',{name:'Experiment 2 · bbbbbb',exact:true}).click();
  const link=page.getByRole('link',{name:'Download experiment JSON',exact:true});
  await expect(link).toHaveAttribute('href',`/api/v1/workflow/experiments/${second}/export?format=json`);
  const oldResponse=page.waitForResponse(r=>r.url().endsWith('/experiments/'+first));
  release(); await oldResponse;
  await page.getByText('Full experiment, model evaluations and reproducibility record',{exact:true}).click();
  await expect(page.locator('details').filter({hasText:'Full experiment, model evaluations and reproducibility record'}).locator('pre')).toContainText(second);
  await picker.click();
  await page.getByRole('option',{name:'Select completed experiment',exact:true}).click();
  await expect(link).toHaveCount(0);
});

test('eligibility fails closed while settings change or validation is unavailable', async ({page}) => {
  const partition={trade_count:25,first_execution_at:'2026-07-06T12:00:00Z',last_label_known_at:'2026-07-07T12:00:00Z'};
  const dataset={dataset_id:'dataset-'+'c'.repeat(20),name:'100 trades · test dataset',configuration:{},outcome_balance:{},
    summary:{trade_count:100,failed_count:10,partitions:Object.fromEntries(['train','tuning','calibration','test'].map(key=>[key,partition]))},
    scenarios:[{scenario_id:'held-out',name:'Held-out day',decision_at:'2026-07-10T12:00:00Z'}]};
  await page.route('**/api/v1/workflow/catalog',route=>route.fulfill({json:{datasets:[dataset],risk_models:[],timing_models:[],experiments:[],errors:[]}}));
  let release;
  const gate=new Promise(resolve=>{release=resolve;});
  await page.route('**/api/v1/workflow/eligibility',async route=>{
    if(Number(route.request().postDataJSON().budget)===123){
      await gate;
      await route.fulfill({status:503,json:{message:'Compatibility service temporarily unavailable'}});
    }else await route.fulfill({json:{eligible:true,reasons:[]}});
  });
  await page.goto('/?tab=Experiments');
  await page.getByRole('combobox',{name:'Saved dataset',exact:true}).click();
  await page.getByRole('option',{name:dataset.name,exact:true}).click();
  const compare=page.getByRole('button',{name:'Compare funding policies',exact:true});
  await expect(compare).toBeEnabled();
  await page.getByLabel('Funding budget (USD)',{exact:true}).fill('123');
  await expect(compare).toBeDisabled();
  release();
  await expect(page.getByRole('alert')).toContainText('Compatibility service temporarily unavailable');
  await expect(compare).toBeDisabled();
});

test('funding disables incompatible timing models and explains why', async ({page}) => {
  const id='timing-'+'d'.repeat(20);
  await page.route('**/api/v1/timing-models?run_id=*',route=>route.fulfill({json:{items:[{model_id:id,eligible:false,reasons:['Training labels mature after this scenario starts']}]}}));
  await page.goto('/');
  await page.getByRole('button',{name:'Funding',exact:true}).click();
  await expect(page.getByText(/Unavailable timing model dddddd:/)).toContainText('Training labels mature after');
  await page.getByRole('combobox',{name:'FX timing model',exact:true}).click();
  await expect(page.getByRole('option',{name:id+' · incompatible',exact:true})).toHaveAttribute('aria-disabled','true');
});

test('task-first pages offer next actions and keep research details optional', async ({page}, testInfo) => {
  const runs=await (await page.request.get('/api/v1/runs')).json();
  const baseline=runs.items.find(r=>r.scenario_id==='three-trades-v1');
  await page.goto('/?run='+baseline.run_id);
  await expect(page.getByRole('button',{name:'T-FX-GAP',exact:true})).toBeVisible();
  await page.addStyleTag({content:'* { transition: none !important; animation: none !important; }'});
  for (const theme of ['Light','Dark']) {
    await page.getByRole('button',{name:theme+' theme',exact:true}).click();
    for (const tab of ['Experiments','Trades','Funding','Scenarios','Model health']) {
      await page.getByRole('button',{name:tab,exact:true}).click();
      if(tab==='Experiments') {
        await expect(page.getByRole('navigation',{name:'Experiment steps'})).toBeVisible();
        await expect(page.getByLabel('Opening cash fraction',{exact:true})).toBeHidden();
        await page.getByText('Dataset assumptions',{exact:true}).click();
        await expect(page.getByLabel('Opening cash fraction',{exact:true})).toBeVisible();
      } else {
        await expect(page.getByRole('region',{name:'Next step'})).toBeVisible();
        await expect(page.getByRole('region',{name:'Evaluation controls'})).toHaveCount(tab==='Trades'?1:0);
      }
      if(tab==='Scenarios') {
        await expect(page.getByRole('region',{name:'Missed deadlines by policy'})).toBeVisible();
      }
      if(tab==='Funding') {
        await expect(page.getByLabel('Liquidity limit (USD)')).toBeEnabled();
        await expect(page.getByText('Saved currency reference rates',{exact:true})).toBeVisible();
        await expect(page.getByRole('heading',{name:'Currency reference rates',exact:true})).toBeHidden();
      }
      await page.screenshot({path:testInfo.outputPath(`${theme}-${tab.replaceAll(' ','-')}.png`),fullPage:true});
    }
  }
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('current-date defaults and header actions fit mobile and desktop pages', async ({page}, testInfo) => {
  await page.goto('/?tab=Experiments');
  const defaults = await (await page.request.get('/api/v1/workflow/defaults')).json();
  await expect(page.getByLabel('First trading date',{exact:true})).toHaveValue(defaults.start_date);
  await expect(page.getByLabel('First trading date',{exact:true})).toHaveAttribute('max',defaults.today);
  for (const width of [320,390,768,1440]) {
    await page.setViewportSize({width,height:900});
    for (const tab of ['Experiments','Trades','Funding','Scenarios','Model health']) {
      if(width<768) await page.getByRole('button',{name:'Toggle Sidebar',exact:true}).click();
      await page.getByRole('button',{name:tab,exact:true}).click();
      await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      if(tab!=='Experiments') {
        const button=page.getByRole('button',{name:'New experiment',exact:true});
        await expect(button).toBeVisible();
        expect(await button.evaluate(el=>{
          const b=el.getBoundingClientRect(),h=el.closest('header').getBoundingClientRect();
          return b.left>=h.left && b.right<=h.right+1 && b.bottom<=h.bottom+1;
        })).toBe(true);
        if(tab==='Funding') await page.screenshot({path:testInfo.outputPath(`funding-${width}.png`),fullPage:true});
      }
    }
  }
});


test('global tokens reach shadcn portals and tables support keyboard scrolling', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  const runs = await (await page.request.get('/api/v1/runs')).json();
  const baseline = runs.items.find(run => run.scenario_id === 'three-trades-v1');
  await page.goto('/?run=' + baseline.run_id);
  await expect(page.getByRole('button', {name: 'T-FX-GAP', exact: true})).toBeVisible();
  for (const theme of ['Light', 'Dark']) {
    await page.getByRole('button', {name: theme + ' theme', exact: true}).click();
    const sort = page.getByRole('combobox', {name: 'Sort', exact: true});
    await sort.click();
    const menu = page.getByRole('listbox');
    await expect(menu).toBeVisible();
    expect(await menu.evaluate(el => {
      const style = getComputedStyle(el), probe = document.createElement('div');
      probe.style.backgroundColor = 'var(--popover)'; document.body.append(probe);
      const expected = getComputedStyle(probe).backgroundColor; probe.remove();
      const bounds = el.getBoundingClientRect();
      return style.backgroundColor === expected && bounds.left >= 0 && bounds.right <= innerWidth;
    })).toBe(true);
    await page.keyboard.press('Escape');
  }
  const table = page.locator('[data-slot="table-container"]').first();
  await table.focus();
  await expect(table).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => table.evaluate(el => el.scrollLeft)).toBeGreaterThan(0);
});


test('generation preview explains real deadlines and rejects invalid input', async ({page}) => {
  await page.goto('/?tab=Experiments');
  await page.getByText('Preview dates, deadlines and assumptions', {exact:true}).click();
  await expect(page.getByRole('columnheader', {name:'Settlement deadline',exact:true})).toBeVisible();
  await expect(page.getByText('T+1 means the next eligible settlement business day.', {exact:false})).toBeVisible();
  await page.getByLabel('Number of trades', {exact:true}).fill('0');
  expect(await page.getByLabel('Number of trades', {exact:true}).evaluate(el=>el.checkValidity())).toBe(false);
  await page.getByLabel('Number of trades', {exact:true}).fill('200');
  await expect(page.getByRole('columnheader', {name:'Settlement deadline',exact:true})).toBeVisible();
  await expect(page.getByRole('heading', {name:'FX timing models',exact:true})).toBeVisible();
});


test('mobile workspace navigation and stage links remain usable in both themes', async ({page},testInfo) => {
  await page.setViewportSize({width:390,height:844});
  await page.goto('/?tab=Experiments');
  const nav=page.getByRole('navigation',{name:'Workspace pages',exact:true});
  await expect(nav).toBeVisible();
  for(const theme of ['Light','Dark']) {
    await page.getByRole('button',{name:theme+' theme',exact:true}).click();
    for(const tab of ['Trades','Funding','Scenarios','Model health','Experiments']) {
      const button=nav.getByRole('button',{name:'Go to '+tab,exact:true});
      await button.click();
      await expect(button).toHaveAttribute('aria-current','page');
      await expect(page.locator('main')).toHaveAttribute('data-page',tab);
      expect(await nav.evaluate(el=>el.getBoundingClientRect().bottom<=innerHeight)).toBe(true);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    }
    await page.getByRole('navigation',{name:'Experiment steps'}).getByRole('link',{name:/Models/}).click();
    await expect(page.getByRole('heading',{name:'Train and compare saved models',exact:true})).toBeInViewport();
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:testInfo.outputPath(`workflow-mobile-${theme}.png`),fullPage:true});
  }
  await page.setViewportSize({width:1440,height:1000});
  await expect(nav).toBeHidden();
  await page.getByRole('navigation',{name:'Experiment steps'}).getByRole('link',{name:/Dataset/}).click();
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:testInfo.outputPath('workflow-desktop.png'),fullPage:true});
});


test('cleared artifacts recover from stale browser run and job references', async ({page}) => {
  await page.addInitScript(()=>{
    localStorage.setItem('experiment-job','job-from-cleared-workspace');
    localStorage.setItem('experiment-record','experiment-from-cleared-workspace');
  });
  await page.route('**/api/v1/runs',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({items:[]})}));
  await page.route('**/api/v1/workflow/experiments/experiment-from-cleared-workspace',route=>route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({message:'missing'})}));
  await page.route('**/api/v1/jobs/job-from-cleared-workspace',route=>route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({code:'RUN_NOT_FOUND',message:'The requested run does not exist.'})}));
  await page.goto('/?run=run-deleted-with-artifacts&tab=Trades');
  await expect(page.locator('#workspace-main[data-page="Experiments"]')).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByText('Preparation status',{exact:true})).toHaveCount(0);
  await expect(page).toHaveURL(/\?tab=Experiments$/);
  await expect.poll(()=>page.evaluate(()=>localStorage.getItem('experiment-job'))).toBeNull();
  await expect.poll(()=>page.evaluate(()=>localStorage.getItem('experiment-record'))).toBeNull();
});

test('deleted datasets clear persisted selections before compatibility requests',async({page})=>{
  await page.addInitScript(()=>localStorage.setItem('experiment-draft',JSON.stringify({dataset_id:'dataset-'+'a'.repeat(20),scenario_id:'deleted-scenario',risk_model_id:'model-'+'b'.repeat(20),timing_model_id:'timing-'+'c'.repeat(20)})));
  await page.route('**/api/v1/workflow/catalog',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({datasets:[],risk_models:[],timing_models:[],experiments:[],errors:[]})}));
  const eligibilityRequests=[];
  page.on('request',request=>{if(request.url().endsWith('/workflow/eligibility'))eligibilityRequests.push(request);});
  await page.goto('/?tab=Experiments');
  await expect(page.getByRole('combobox',{name:'Saved dataset',exact:true})).toContainText('Create a new dataset');
  await expect.poll(()=>page.evaluate(()=>JSON.parse(localStorage.getItem('experiment-draft')).dataset_id)).toBe('');
  await expect(page.getByRole('combobox',{name:'Risk prediction model',exact:true})).toContainText('No risk model');
  await expect(page.getByRole('combobox',{name:'Timing forecast model',exact:true})).toContainText('Expected receipt times');
  await expect(page.getByRole('button',{name:'Compare funding policies',exact:true})).toBeDisabled();
  expect(eligibilityRequests).toHaveLength(0);
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('changing trading days resolves a mature historical start without blocking editing',async({page})=>{
  await page.addInitScript(()=>localStorage.setItem('experiment-draft',JSON.stringify({start_date:'2026-11-10',trading_days:10})));
  await page.goto('/?tab=Experiments');
  await page.getByLabel('Trading days',{exact:true}).fill('30');
  const defaults=await (await page.request.get('/api/v1/workflow/defaults?trading_days=30')).json();
  await expect(page.getByLabel('First trading date',{exact:true})).toHaveValue(defaults.start_date);
  await expect(page.getByLabel('Trading days',{exact:true})).toHaveValue('30');
  await expect(page.getByRole('button',{name:'Create dataset',exact:true})).toBeEnabled();
  await page.getByLabel('Trading days',{exact:true}).fill('');
  await page.getByLabel('Trading days',{exact:true}).fill('35');
  const longer=await (await page.request.get('/api/v1/workflow/defaults?trading_days=35')).json();
  await expect(page.getByLabel('First trading date',{exact:true})).toHaveValue(longer.start_date);
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('trade rows load while model forecasts are pending',async({page})=>{
  const runs=await (await page.request.get('/api/v1/runs')).json();
  const baseline=runs.items.find(r=>r.scenario_id==='three-trades-v1');
  await page.route('**/api/v1/models?*',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({items:[{model_id:'model-test'}]})}));
  let release;
  const gate=new Promise(resolve=>{release=resolve;});
  let calls=0;
  await page.route('**/model-predictions?*',async route=>{calls++;await gate;await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({items:[]})});});
  try {
    await page.goto('/?tab=Trades&run='+baseline.run_id);
    await expect(page.getByRole('button',{name:'T-FX-GAP',exact:true})).toBeVisible();
    await expect(page.getByRole('combobox',{name:'Sort',exact:true})).toContainText('Nearest deadline');
    await expect(page.getByText('Loading prediction…').first()).toBeVisible();
    expect(calls).toBe(1);
  } finally {release();}
});

test('timing health hides empty bins and explains single-class evidence', async ({page}) => {
  await page.route('**/api/v1/models',route=>route.fulfill({json:{items:[]}}));
  await page.route('**/api/v1/timing-models',route=>route.fulfill({json:{items:[{model_id:'timing-test'}]}}));
  await page.route('**/api/v1/timing-models/*/health',route=>route.fulfill({json:{timing_model_id:'timing-test',metrics:{count:660,brier_score:0,reliability_bins:Array.from({length:10},(_,i)=>({lower:i/10,upper:(i+1)/10,count:i===9?660:0,mean_probability:i===9?1:null,late_frequency:i===9?1:null}))}}}));
  await page.goto('/');
  await page.getByRole('button',{name:'Model health',exact:true}).click();
  await expect(page.getByText('All 660 evaluated observations',{exact:false})).toBeVisible();
  await expect(page.getByRole('columnheader',{name:'ROC AUC',exact:false})).toHaveCount(0);
  await expect(page.getByText('Only one probability range contains observations',{exact:false})).toBeVisible();
  await page.getByText('Calibration data (1 populated ranges)',{exact:true}).click();
  await expect(page.getByRole('cell',{name:'0.9–1',exact:true})).toBeVisible();
  await expect(page.getByRole('cell',{name:'0–0.1',exact:true})).toHaveCount(0);
});

test('funding allocation amounts trim raw decimal padding',async({page})=>{
  await page.route('**/api/v1/runs/*/funding',async route=>{
    const response=await route.fetch();const data=await response.json();
    data.plan.allocations[0].observed_pool_cash='130525.060000';
    data.plan.allocations[0].incremental_reserve='2762.410000';
    data.plan.allocations[0].expected_shortfall='5970.800000000000000000000';
    await route.fulfill({json:data});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Funding',exact:true}).click();
  const table=page.locator('.allocation-table');
  await expect(table.getByRole('cell',{name:'130,525.06',exact:true})).toBeVisible();
  await expect(table.getByRole('cell',{name:'2,762.41',exact:true})).toBeVisible();
  await expect(table.getByRole('cell',{name:'5,970.8',exact:true})).toBeVisible();
  await expect(table).not.toContainText('800000');
});

test('missing saved funding jobs recover without permanently disabling recompute',async({page})=>{
  const runs=await (await page.request.get('/api/v1/runs')).json();
  const run=runs.items.find(r=>r.scenario_id==='three-trades-v1');
  await page.addInitScript(id=>localStorage.setItem('t1fx-job:'+id,'deleted-funding-job'),run.run_id);
  await page.goto('/?tab=Funding&run='+run.run_id);
  await expect(page.getByText('The previous funding job is no longer available.',{exact:false})).toBeVisible();
  await expect(page.getByRole('button',{name:'Recompute funding',exact:true})).toBeEnabled();
});

test('evaluation job tracking survives refresh and clears missing jobs',async({page})=>{
  const runs=await (await page.request.get('/api/v1/runs')).json();
  const run=runs.items.find(r=>r.scenario_id==='three-trades-v1');
  await page.addInitScript(id=>localStorage.setItem('evaluation-job:'+id,'resume-evaluation'),run.run_id);
  let missing=false;
  await page.route('**/api/v1/jobs/resume-evaluation',route=>route.fulfill({status:missing?404:200,json:missing?{message:'Job not found'}:{status:'RUNNING'}}));
  await page.goto('/?tab=Trades&run='+run.run_id);
  await expect(page.getByRole('button',{name:'Evaluating…',exact:true})).toBeDisabled();
  await page.reload();
  await expect(page.getByRole('button',{name:'Evaluating…',exact:true})).toBeDisabled();
  missing=true;
  await expect(page.getByText('The previous evaluation job is no longer available.',{exact:false})).toBeVisible({timeout:10000});
  await expect(page.getByRole('button',{name:'Apply evaluation',exact:true})).toBeEnabled();
});

test('failed run loading remains visible on Experiments and offers retry',async({page})=>{
  await page.route('**/api/v1/runs',route=>route.fulfill({status:503,json:{message:'Saved runs temporarily unavailable'}}));
  await page.goto('/?tab=Experiments');
  await expect(page.getByRole('alert')).toContainText('Saved runs temporarily unavailable');
  await page.unroute('**/api/v1/runs');
  await page.getByRole('button',{name:'Retry loading saved runs'}).click();
  await expect(page.getByRole('button',{name:'Retry loading saved runs'})).toHaveCount(0);
});

test('experiment hides temporally incompatible models and clears stale selections',async({page})=>{
  const datasetId='dataset-'+'e'.repeat(20),bad='model-'+'f'.repeat(20),good='model-'+'a'.repeat(20);
  const partition={trade_count:25,first_execution_at:'2026-07-06T12:00:00Z',last_label_known_at:'2026-07-07T12:00:00Z'};
  const dataset={dataset_id:datasetId,name:'Held-out dataset',configuration:{},outcome_balance:{},summary:{trade_count:100,failed_count:10,partitions:Object.fromEntries(['train','tuning','calibration','test'].map(k=>[k,partition]))},scenarios:[{scenario_id:'test-day',name:'Test day',decision_at:'2026-07-10T12:00:00Z'}]};
  await page.addInitScript(data=>localStorage.setItem('experiment-draft',JSON.stringify(data)),{dataset_id:datasetId,scenario_id:'test-day',risk_model_id:bad});
  await page.route('**/api/v1/workflow/catalog',route=>route.fulfill({json:{datasets:[dataset],risk_models:[bad,good].map(model_id=>({model_id,dataset_id:datasetId,metrics:{}})),timing_models:[],experiments:[],errors:[]}}));
  await page.route('**/api/v1/workflow/eligibility',route=>{const invalid=route.request().postDataJSON().risk_model_id===bad;return route.fulfill({json:{eligible:!invalid,reasons:invalid?['Inference must occur after calibration labels matured']:[]}});});
  let choiceRequests=0;
  await page.route('**/api/v1/workflow/model-choices',route=>{choiceRequests++;expect(route.request().postDataJSON().risk_model_ids).toEqual([bad,good]);return route.fulfill({json:{[bad]:['Inference must occur after calibration labels matured'],[good]:[]}});});
  await page.goto('/?tab=Experiments');
  await expect(page.getByText('The previous model selection cannot evaluate this test day',{exact:false})).toBeVisible();
  const picker=page.getByRole('combobox',{name:'Risk prediction model',exact:true});
  await expect(picker).toContainText('No risk model');
  await expect(page.getByText('Inference must occur after calibration labels matured',{exact:true})).toBeHidden();
  await picker.click();
  await expect(page.getByRole('option',{name:'Risk model 1 · ffffff',exact:true})).toHaveCount(0);
  await page.getByRole('option',{name:'Risk model 2 · aaaaaa',exact:true}).click();
  await expect(page.getByRole('button',{name:'Compare funding policies',exact:true})).toBeEnabled();
  await page.getByText('Why some models are hidden',{exact:true}).click();
  await expect(page.getByText('Inference must occur after calibration labels matured',{exact:true})).toBeVisible();
  expect(choiceRequests).toBe(1);
});

test('evaluation minute precision respects subminute saved boundaries',async({page})=>{
  await page.route('**/api/v1/time',route=>route.fulfill({json:{utc_now:'2026-10-15T12:00:00Z',source:'test://clock'}}));
  await page.route('**/api/v1/runs/*/evaluation-context',async route=>{
    const response=await route.fetch();const body=await response.json();
    await route.fulfill({json:{...body,starts_at:'2026-10-14T12:00:30Z',ends_at:'2026-10-14T12:02:00.500Z'}});
  });
  const runs=await (await page.request.get('/api/v1/runs')).json();
  const run=runs.items.find(r=>r.scenario_id==='three-trades-v1');
  await page.goto('/?run='+run.run_id);
  await expect(page.getByText(/Available:/)).toContainText('12:01');
  const input=page.getByLabel('Evaluation local time',{exact:true}),apply=page.getByRole('button',{name:'Apply evaluation',exact:true});
  await input.fill('12:00');await expect(apply).toBeDisabled();
  await input.fill('12:01');await expect(apply).toBeEnabled();
  await input.fill('12:02');await expect(apply).toBeEnabled();
  await input.fill('12:03');await expect(apply).toBeDisabled();
});

test('dataset catalog failure can be retried without refreshing the page',async({page})=>{
  let fail=true;
  await page.route('**/api/v1/workflow/catalog',route=>fail?route.fulfill({status:503,json:{message:'Temporary outage'}}):route.fulfill({json:{datasets:[],risk_models:[],timing_models:[],experiments:[],errors:[]}}));
  await page.goto('/?tab=Experiments');
  await expect(page.getByRole('alert')).toContainText('Could not load saved datasets');
  fail=false;
  await page.getByRole('button',{name:'Retry loading datasets',exact:true}).click();
  await expect(page.getByRole('button',{name:'Retry loading datasets',exact:true})).toHaveCount(0);
});

test('model checks batch large catalogs and recover from incomplete responses',async({page},testInfo)=>{
  const id='dataset-'+'e'.repeat(20);
  const models=Array.from({length:101},(_,i)=>({model_id:'model-'+i.toString(16).padStart(20,'0'),dataset_id:id,metrics:{}}));
  const dataset={dataset_id:id,name:'Saved research dataset',configuration:{},outcome_balance:{},summary:{trade_count:1000,failed_count:10,partitions:Object.fromEntries(['train','tuning','calibration','test'].map(k=>[k,{trade_count:250,first_execution_at:'2026-07-06T12:00:00Z',last_label_known_at:'2026-07-07T12:00:00Z'}]))},scenarios:[{scenario_id:'held-out',name:'Test day',decision_at:'2026-07-10T12:00:00Z'}]};
  await page.addInitScript(id=>localStorage.setItem('experiment-draft',JSON.stringify({dataset_id:id,scenario_id:'held-out'})),id);
  await page.route('**/api/v1/workflow/catalog',route=>route.fulfill({json:{datasets:[dataset],risk_models:models,timing_models:[],experiments:[],errors:[]}}));
  await page.route('**/api/v1/workflow/eligibility',route=>route.fulfill({json:{eligible:true,reasons:[]}}));
  let incomplete=true;const sizes=[];
  await page.route('**/api/v1/workflow/model-choices',route=>{
    const ids=route.request().postDataJSON().risk_model_ids;sizes.push(ids.length);
    return route.fulfill({json:incomplete?{}:Object.fromEntries(ids.map(id=>[id,[]]))});
  });
  await page.goto('/?tab=Experiments');
  await expect(page.getByText('Could not check model compatibility.',{exact:false})).toBeVisible();
  await expect(page.getByRole('button',{name:'Compare funding policies',exact:true})).toBeDisabled();
  await expect(page.getByRole('combobox',{name:'Risk prediction model',exact:true})).toBeDisabled();
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:'Retry model checks',exact:true}).scrollIntoViewIfNeeded();
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('model-check-retry-mobile.png')});
  incomplete=false;
  await page.getByRole('button',{name:'Retry model checks',exact:true}).click();
  await expect(page.getByRole('button',{name:'Compare funding policies',exact:true})).toBeEnabled();
  expect(sizes).toEqual([100,100,1]);
  await expect(page.getByRole('button',{name:'Retry model checks',exact:true})).toHaveCount(0);
});

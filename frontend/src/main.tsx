import { StrictMode, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource-variable/manrope';
import '@fontsource-variable/space-grotesk';
import './style.css';
import appIcon from './assets/app-icon.svg';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table';
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts';
import { ChartContainer, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart';
import { Breadcrumb, BreadcrumbItem, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from '@/components/ui/breadcrumb';
import { Separator } from '@/components/ui/separator';
import { Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarSeparator, SidebarTrigger, useSidebar } from '@/components/ui/sidebar';
import { Icon, ThemeControl, Status, RiskMeter, StatCard } from './components';

type Run = {run_id: string; scenario_id: string; policy_id: string; decision_at: string; result_as_of: string; reference_policy_id: string; timing_model_id?: string};
type Trade = {trade_id: string; counterparty_id: string; cash_pool_id: string; currency: string; settlement_amount: string; remaining_obligation: string; observed_pool_cash: string; instruction_state: string; ssi_status: string; minutes_to_deadline: number; deadline_at: string; prediction: {score: number; status: string; factors: string[]; recommended_action: string}};
type Page = {items: Trade[]; next_cursor: string | null};
type Detail = {fx_receipts: {fx_leg_id: string; amount: string; currency: string; state: string; expected_credit_at: string}[]; trade: Trade; observed_events: {event_id: string; event_type: string; event_time: string}[]; outcome_as_of: string; outcome: {missed_contractual_deadline: boolean | null; recovered_late: boolean; failure_reasons: string[]}};
type Plan = {plan_id: string; status: string; policy_id: string; fallback_used: boolean; failure_reason: string | null; budget_used_base: string; expected_cost_base: string; base_currency: string; allocations: {bucket: {cash_pool_id: string; currency: string}; incremental_reserve: string; expected_shortfall: string; observed_pool_cash: string}[]};
type Metrics = {currency: string; failed_count: number; trade_count: number; failed_notional?: string; total_cost: string; bridge_cost: string; late_cost: string; route_cost: string; budget_limit: string; budget_used: string};
type Comparison = {run_id: string; policy_id: string; metrics: Metrics};
type Job = {job_id: string; status: string; attempts: number; error: string | null; result: {runs: {run_id: string; policy_id: string}[]} | null};
type Prediction = {trade_id: string; p_fail_any: number; explanation: {interpretation: string; top_three: {feature: string; value: number}[]}};
const stamp = (value: string) => new Date(value).toLocaleString('en-GB', {timeZone: 'UTC'}) + ' UTC';
const cash = (value: string | undefined, currency = 'USD') => {
  if (value === undefined) return '—';
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return `${currency} ${value}`;
  const places = currency === 'JPY' ? 0 : 2;
  const fraction = (match[3] || '').padEnd(places + 1, '0');
  let minor = BigInt(match[2]) * 10n ** BigInt(places) + BigInt(fraction.slice(0, places) || '0');
  if (Number(fraction[places]) >= 5) minor += 1n;
  const digits = minor.toString().padStart(places + 1, '0');
  const integer = places ? digits.slice(0, -places) : digits;
  return `${currency} ${minor ? match[1] : ''}${integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}${places ? '.' + digits.slice(-places) : ''}`;
};
const policyName = (value: string) => ({'fixed-0': 'No additional funding', 'fixed-0.5': 'Half reserve', 'fixed-1': 'Full reserve', 'scenario-lp-v1': 'Scenario optimizer', 'deterministic-lp-v1': 'Deterministic optimizer'}[value] || value);
const subtitles: Record<string, string> = {Trades: 'See what needs attention before the settlement deadline.', Funding: 'Put liquidity where it can make a difference.', Scenarios: 'Compare the outcomes of your funding decisions.', 'Model health': 'Understand the evidence behind each prediction.'};
async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch('/api/v1' + path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || `Request failed (${response.status})`);
  return data;
}
function ErrorBox({message}: {message: string}) { return message ? <Alert variant="destructive" className="error"><Icon name="info"/><AlertDescription>{message}</AlertDescription></Alert> : null; }
function Empty({children}: {children: React.ReactNode}) { return <div className="empty"><div className="empty-icon"><Icon name="grid" size={26}/></div><div>{children}</div></div>; }
function App() {
  const [runs, setRuns] = useState<Run[]>([]), [runId, setRunId] = useState(() => new URLSearchParams(location.search).get('run') || '');
  const [tab, setTab] = useState(() => {const value = new URLSearchParams(location.search).get('tab'); return value && ['Trades', 'Funding', 'Scenarios', 'Model health'].includes(value) ? value : 'Trades';}), [error, setError] = useState(''), [loading, setLoading] = useState(true);
  const run = runs.find(r => r.run_id === runId);
  async function refresh(preferred?: string) {
    try {
      const data = await api<{items: Run[]}>('/runs'); setRuns(data.items);
      setRunId(current => preferred || (data.items.some(r => r.run_id === current) ? current : data.items[0]?.run_id || '')); setError('');
    } catch (e) { setError(String(e)); } finally { setLoading(false); }
  }
  useEffect(() => { void refresh(); }, []);
  useEffect(() => { if (runId) history.replaceState(null, '', '?' + new URLSearchParams({run: runId, tab})); }, [runId, tab]);
  return <SidebarProvider className="shell"><WorkspaceSidebar tab={tab} onSelect={setTab}/><SidebarInset><div className="page-frame"><div className="topbar"><div className="topbar-start"><SidebarTrigger aria-label="Toggle Sidebar"/><Separator orientation="vertical" className="topbar-separator"/><Breadcrumb><BreadcrumbList><BreadcrumbItem><span>Workspace</span></BreadcrumbItem><BreadcrumbSeparator/><BreadcrumbItem><BreadcrumbPage>{tab}</BreadcrumbPage></BreadcrumbItem></BreadcrumbList></Breadcrumb></div><ThemeControl/></div><div className="page-content"><header><div><div className="heading-kicker"><span className="dot"/> SETTLEMENT OPERATIONS</div><h1>{tab === 'Trades' ? 'At-risk trades' : tab}</h1><p className="page-description">{subtitles[tab]}</p></div><Badge variant="secondary" className="badge"><Icon name="clock" size={14}/>Stored replay</Badge></header>
      <section className="run-bar" aria-label="Selected simulation"><div className="run-icon"><Icon name="grid" size={21}/></div><Label>Simulation run<Select label="Run" value={runId} onValueChange={setRunId} disabled={!runs.length} options={[{value:'',label:'Select a run',disabled:true}, ...runs.map(r=>({value:r.run_id,label:`${r.scenario_id} · ${policyName(r.policy_id)} · ${r.run_id.slice(-6)}`}))]}/></Label><div className="clock-block"><span className="eyebrow">Decision time</span><strong><Icon name="clock" size={14}/>{run ? stamp(run.decision_at) : '—'}</strong></div><Button variant="outline" size="icon" className="icon-button secondary" title="Refresh runs" aria-label="Refresh runs" onClick={() => void refresh()}><Icon name="refresh"/></Button></section>
      <ErrorBox message={error}/>
      {loading ? <Empty>Loading stored runs…</Empty> : !run ? <Empty>No runs yet. Generate the reference scenario with <code>make demo</code>, then refresh.</Empty> : <div key={run.run_id}>
        <div className="context context-row"><span>Reference observations <strong>{policyName(run.reference_policy_id)}</strong></span><span>Selected policy <strong>{policyName(run.policy_id)}</strong></span></div>
        {tab === 'Trades' && <Trades run={run}/>}
        {tab === 'Funding' && <Funding run={run} onResult={id => void refresh(id)}/>}
        {tab === 'Scenarios' && <Scenarios run={run} onSelect={setRunId}/>}
        {tab === 'Model health' && <ModelHealth run={run}/>}
      </div>}
      <footer><span><Icon name="info" size={14}/>Synthetic data · Hypothetical funding only</span><span>All times UTC · Amounts rounded for display</span></footer></div></div>
    </SidebarInset></SidebarProvider>;
}
function WorkspaceSidebar({tab, onSelect}: {tab: string; onSelect: (value: string) => void}) {
  const {isMobile, setOpenMobile} = useSidebar();
  const items = [{label:'Trades', icon:'grid'}, {label:'Funding', icon:'wallet'}, {label:'Scenarios', icon:'chart'}, {label:'Model health', icon:'pulse'}];
  return <Sidebar collapsible="icon" className="workspace-sidebar"><div className="workspace-sidebar-body"><SidebarHeader><a className="brand" href="/"><img className="brand-mark" src={appIcon} alt="T+1 Settlement app icon" width="44" height="44"/><span className="brand-copy">T+1 Settlement<span>Risk workspace</span></span></a></SidebarHeader><SidebarContent><SidebarGroup><SidebarGroupLabel>Workspace</SidebarGroupLabel><SidebarGroupContent><SidebarMenu aria-label="Main navigation">{items.map(item=><SidebarMenuItem key={item.label}><SidebarMenuButton isActive={tab===item.label} tooltip={item.label} aria-label={item.label} aria-current={tab===item.label?'page':undefined} onClick={()=>{onSelect(item.label); if(isMobile) setOpenMobile(false);}}><Icon name={item.icon}/><span>{item.label}</span></SidebarMenuButton></SidebarMenuItem>)}</SidebarMenu></SidebarGroupContent></SidebarGroup></SidebarContent><SidebarFooter><SidebarSeparator/><div className="workspace-avatar"><Icon name="monitor"/><div>Local workspace<small>Synthetic research data</small></div><span className="dot"/></div><a className="sidebar-docs" href="/docs">API documentation <Icon name="arrow" size={14}/></a></SidebarFooter></div></Sidebar>;
}
function Trades({run}: {run: Run}) {
  const [page, setPage] = useState<Page>({items: [], next_cursor: null}), [sort, setSort] = useState('risk');
  const [currency, setCurrency] = useState('USD');
  const [minScore, setMinScore] = useState('0'), [state, setState] = useState(''), [error, setError] = useState('');
  const [detail, setDetail] = useState<Detail | null>(null), [busy, setBusy] = useState(false);
  const [models, setModels] = useState<{model_id: string}[]>([]), [model, setModel] = useState('');
  const [predictions, setPredictions] = useState<Record<string, Prediction>>({}), [modelError, setModelError] = useState('');
  const [search, setSearch] = useState('');
  const detailRef = useRef<HTMLElement>(null);
  useEffect(() => { if (detail) detailRef.current?.scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start'}); }, [detail]);
  const sequence = useRef(0), detailSequence = useRef(0);
  const visibleTrades = page.items.filter(t => `${t.trade_id} ${t.counterparty_id}`.toLowerCase().includes(search.toLowerCase()));
  async function load(cursor = '') {
    const token = ++sequence.current; setBusy(true);
    try {
      const result = await api<Page>(`/runs/${run.run_id}/trades?sort=${sort}&currency=${currency}&model_id=${model}&min_score=${minScore}&state=${encodeURIComponent(state)}${cursor ? '&cursor=' + encodeURIComponent(cursor) : ''}`.replace('&state=&', '&').replace(/&state=$/, ''));
      if (token === sequence.current) { setPage(result); setError(''); }
    } catch (e) { if (token === sequence.current) setError(String(e)); }
    finally { if (token === sequence.current) setBusy(false); }
  }
  useEffect(() => { void load(); return () => { sequence.current++; }; }, [sort, minScore, state, currency, model]);
  useEffect(() => { let live = true; api<{items: {model_id: string}[]}>('/models').then(r => { if (live) setModels(r.items); }).catch(e => { if (live) setModelError(String(e)); }); return () => { live = false; }; }, []);
  useEffect(() => {
    let live = true; setPredictions({}); setModelError('');
    if (model) api<{items: Prediction[]}>(`/runs/${run.run_id}/model-predictions?model_id=${model}`).then(r => { if (live) setPredictions(Object.fromEntries(r.items.map(p => [p.trade_id, p]))); }).catch(e => { if (live) setModelError(String(e)); });
    return () => { live = false; };
  }, [model]);
  async function openTrade(id: string) {
    const token = ++detailSequence.current; setDetail(null);
    try { const result = await api<Detail>(`/runs/${run.run_id}/trades/${encodeURIComponent(id)}`); if (token === detailSequence.current) setDetail(result); }
    catch (e) { if (token === detailSequence.current) setError(String(e)); }
  }
  return <>
    <RunOverview run={run}/>
    <div className="section-heading"><div><h2>Settlement watchlist</h2><p>Observable exceptions at the selected decision time.</p></div><Badge variant="secondary" className="badge muted">Rule scores are uncalibrated</Badge></div>
    <div className="table-toolbar"><Label className="search-field"><Icon name="search"/><Input aria-label="Search this page" placeholder="Search trades on this page…" value={search} onChange={e => setSearch(e.target.value)}/></Label><span className="result-count">{visibleTrades.length} on this page</span><Button variant="outline" className="secondary" onClick={() => {setSearch(''); setMinScore('0'); setState(''); setCurrency('USD'); setSort('risk');}}>Reset filters</Button></div>
    <div className="filters"><Label>Sort<Select label="Sort" value={sort} onValueChange={setSort} options={[{value:'risk',label:'Rule score'},{value:'amount',label:'Exposure'},{value:'probability',label:'Fail probability',disabled:!model}]}/></Label><Label>Minimum rule score<Select label="Minimum rule score" value={minScore} onValueChange={setMinScore} options={[{value:'0',label:'All scores'},{value:'40',label:'40+'},{value:'70',label:'70+'}]}/></Label><Label>Currency<Select label="Currency" value={currency} onValueChange={setCurrency} options={['USD','CAD','EUR','GBP','JPY','INR'].map(value=>({value,label:value}))}/></Label><Label>Instruction state<Select label="Instruction state" value={state} onValueChange={setState} options={[{value:'',label:'All states'}, ...['EXECUTED','ALLOCATED','CONFIRMED','AFFIRMED','INSTRUCTED'].map(value=>({value,label:value}))]}/></Label><Label>Probability model<Select label="Probability model" value={model} onValueChange={value=>{setModel(value); if(!value) setSort('risk');}} options={[{value:'',label:'Rule scores only'}, ...models.map(m=>({value:m.model_id,label:m.model_id}))]}/></Label></div>
    <ErrorBox message={error || modelError}/>
    <div className="table-wrap" aria-busy={busy}><Table><TableHeader><TableRow><TableHead>Trade / counterparty</TableHead><TableHead>Obligation</TableHead><TableHead>Rule score</TableHead><TableHead>Fail probability</TableHead><TableHead>Instruction / SSI</TableHead><TableHead>To deadline</TableHead></TableRow></TableHeader><TableBody>{visibleTrades.map(t => <TableRow key={t.trade_id}><TableCell><Button variant="link" className="link" onClick={() => void openTrade(t.trade_id)}>{t.trade_id}</Button><small>{t.counterparty_id}</small></TableCell><TableCell>{cash(t.remaining_obligation, t.currency)}</TableCell><TableCell><RiskMeter score={t.prediction.score}/></TableCell><TableCell>{predictions[t.trade_id] ? (predictions[t.trade_id].p_fail_any*100).toFixed(1) + '%' : '—'}</TableCell><TableCell><Status value={t.instruction_state}/><small>SSI · {t.ssi_status.toLowerCase()}</small></TableCell><TableCell><span className={t.minutes_to_deadline <= 120 ? "deadline urgent" : "deadline"}><Icon name="clock" size={13}/>{Math.round(t.minutes_to_deadline)} min</span></TableCell></TableRow>)}</TableBody></Table>{!visibleTrades.length && <Empty>{busy ? 'Loading trades…' : 'No trades match these filters.'}</Empty>}</div>
    <div className="pagination"><span>Showing {visibleTrades.length} trades · Select a trade to inspect its lifecycle</span><Button variant="outline" className="secondary" disabled={busy} onClick={() => void load()}>First page</Button><Button variant="outline" className="secondary" disabled={!page.next_cursor || busy} onClick={() => void load(page.next_cursor!)}>Next page</Button></div>
    {detail && <section ref={detailRef} className="detail" aria-label="Trade detail"><div className="section-heading"><div><span className="eyebrow">TRADE DETAIL</span><h2>{detail.trade.trade_id}</h2></div><Button variant="outline" className="secondary" onClick={() => { detailSequence.current++; setDetail(null); }}>Close detail</Button></div><div className="cards"><Card title="Instruction" value={detail.trade.instruction_state}/><Card title="SSI" value={detail.trade.ssi_status}/><Card title="Observed pool cash" value={cash(detail.trade.observed_pool_cash, detail.trade.currency)}/></div><h3>Observed risk factors</h3><ul>{detail.trade.prediction.factors.slice(0, 3).map(f => <li key={f}>{f}</li>)}</ul><p className="action">{detail.trade.prediction.recommended_action}</p>{predictions[detail.trade.trade_id] && <><h3>Model explanation · raw log-odds</h3><ul>{predictions[detail.trade.trade_id].explanation.top_three.map(f => <li key={f.feature}>{f.feature}: {f.value.toFixed(3)}</li>)}</ul><p>{predictions[detail.trade.trade_id].explanation.interpretation}</p></>}<h3>FX receipts in this cash pool</h3>{detail.fx_receipts.length ? <ul>{detail.fx_receipts.map(f => <li key={f.fx_leg_id}>{f.fx_leg_id} · {cash(f.amount, f.currency)} · {f.state}<small>Expected {stamp(f.expected_credit_at)}</small></li>)}</ul> : <p>No observable FX receipts.</p>}<h3>Observed lifecycle</h3><ol>{detail.observed_events.map(e => <li key={e.event_id}>{e.event_type} <small>{stamp(e.event_time)}</small></li>)}</ol><p>Deadline: {stamp(detail.trade.deadline_at)}</p><div className="outcome"><strong>Replay outcome · {stamp(detail.outcome_as_of)}</strong><p>{detail.outcome.missed_contractual_deadline === null ? 'Censored outcome' : detail.outcome.missed_contractual_deadline ? 'Missed contractual deadline' : 'Met contractual deadline'}{detail.outcome.recovered_late ? ' · Recovered late' : ''}</p></div></section>}
  </>;
}
function Card({title, value}: {title: string; value: string}) { return <StatCard title={title} value={value}/>; }
function RunOverview({run}: {run: Run}) {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  useEffect(() => {let live = true; api<{metrics: Metrics}>(`/runs/${run.run_id}/metrics`).then(r => {if (live) setMetrics(r.metrics);}).catch(() => {}); return () => {live = false;};}, [run.run_id]);
  return <div className="cards overview-cards"><StatCard title="Trades in this run" value={metrics ? String(metrics.trade_count) : '—'} note="Across all settlement pools" icon="grid"/><StatCard title="Missed deadlines" value={metrics ? String(metrics.failed_count) : '—'} note="Realised replay outcome" icon="clock"/><StatCard title="Failed exposure" value={cash(metrics?.failed_notional)} note="Notional at missed deadlines" icon="wallet"/><StatCard title="Total replay cost" value={cash(metrics?.total_cost)} note="Bridge, late and route costs" icon="chart"/></div>;
}
function Funding({run, onResult}: {run: Run; onResult: (id: string) => void}) {
  const [plan, setPlan] = useState<Plan | null>(null), [metrics, setMetrics] = useState<Metrics | null>(null);
  const [remaining, setRemaining] = useState("0");
  const [budget, setBudget] = useState('30000.00'), [job, setJob] = useState<Job | null>(null), [error, setError] = useState(''), [submitting, setSubmitting] = useState(false);
  const [pendingId, setPendingId] = useState(() => localStorage.getItem('t1fx-job:' + run.run_id));
  const pollEpoch = useRef(0);
  const submission = useRef<{body: string; key: string} | null>(null);
  useEffect(() => { let live = true; Promise.all([api<{plan: Plan; remaining_budget: string}>(`/runs/${run.run_id}/funding`), api<{metrics: Metrics}>(`/runs/${run.run_id}/metrics`)]).then(([p, m]) => { if (live) {setPlan(p.plan); setRemaining(p.remaining_budget); setMetrics(m.metrics); setBudget(m.metrics.budget_limit);} }).catch(e => { if (live) setError(String(e)); }); return () => { live = false; }; }, []);
  useEffect(() => {
    if (!pendingId) return;
    let live = true, timer: ReturnType<typeof setTimeout>;
    const epoch = ++pollEpoch.current;
    async function poll() {
      try { const result = await api<Job>('/jobs/' + pendingId); if (!live || epoch !== pollEpoch.current) return; setJob(result); setError(''); if (['FAILED', 'CANCELED'].includes(result.status)) submission.current = null; if (['QUEUED', 'RUNNING'].includes(result.status)) timer = setTimeout(poll, 3000); }
      catch (e) { if (live) {setError(String(e)); timer = setTimeout(poll, 5000);} }
    }
    void poll(); return () => { live = false; clearTimeout(timer); };
  }, [pendingId]);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); if (submitting) return; setSubmitting(true); setError('');
    const body = JSON.stringify({liquidity_limit: budget});
    if (submission.current?.body !== body) submission.current = {body, key: crypto.randomUUID()};
    try { const result = await api<Job>(`/runs/${run.run_id}/funding/recompute`, {method: 'POST', headers: {'Content-Type': 'application/json', 'Idempotency-Key': submission.current.key}, body}); setJob(result); setPendingId(result.job_id); localStorage.setItem('t1fx-job:' + run.run_id, result.job_id); }
    catch (e) { setError(String(e)); } finally { setSubmitting(false); }
  }
  async function cancel() { try { const result = await api<Job>(`/jobs/${pendingId}/cancel`, {method: 'POST'}); pollEpoch.current++; setJob(result); setPendingId(null); localStorage.removeItem('t1fx-job:' + run.run_id); submission.current = null; } catch (e) { setError(String(e)); } }
  const active = job && ['QUEUED', 'RUNNING'].includes(job.status);
  return <><ErrorBox message={error}/><div className="section-heading"><div><h2>Funding plan</h2><p>Incremental reserves for segregated settlement pools.</p></div><Status value={plan?.status || "LOADING"}/></div>
    {plan && <><div className="cards"><Card title="Reserve used" value={cash(plan.budget_used_base)}/><Card title="Budget remaining" value={cash(remaining)}/><Card title="Planning objective" value={cash(plan.expected_cost_base)}/></div>{plan.fallback_used && <ErrorBox message={'Solver fallback: ' + plan.failure_reason}/>}
    <div className="table-wrap"><Table><TableHeader><TableRow><TableHead>Cash pool</TableHead><TableHead>Currency</TableHead><TableHead>Observed cash</TableHead><TableHead>Incremental reserve</TableHead><TableHead>Expected shortfall</TableHead></TableRow></TableHeader><TableBody>{plan.allocations.map(a => <TableRow key={a.bucket.cash_pool_id + a.bucket.currency}><TableCell>{a.bucket.cash_pool_id}</TableCell><TableCell>{a.bucket.currency}</TableCell><TableCell>{a.observed_pool_cash}</TableCell><TableCell>{a.incremental_reserve}</TableCell><TableCell>{a.expected_shortfall}</TableCell></TableRow>)}</TableBody></Table></div></>}
    <section className="panel budget-panel"><div className="panel-icon"><Icon name="wallet" size={23}/></div><h2>Explore a funding budget</h2><p>Set a liquidity limit to compare funding options. Your current run stays available while the new plan is calculated.</p><form onSubmit={submit}><Label>Liquidity limit (USD)<Input disabled={!plan} aria-label="Liquidity limit (USD)" value={budget} onChange={e => setBudget(e.target.value)} inputMode="decimal" pattern="(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?" required/></Label><Button disabled={!plan || submitting || !!active || (!!pendingId && !job)} type="submit">{submitting ? 'Submitting…' : 'Recompute funding'}</Button></form>
      {job && <div className="job" role="status"><div className="job-heading"><strong>Job {job.status}</strong><Status value={job.status}/></div><small>{job.job_id} · attempt {job.attempts}</small>{job.status === 'QUEUED' && <p>Waiting for the local worker. Start <code>make worker</code> if it is not running.</p>}{job.error && <p>{job.error}</p>}{active && <Button variant="outline" className="secondary" onClick={() => void cancel()}>Cancel job</Button>}{job.status === 'SUCCEEDED' && job.result && <Button onClick={() => onResult((job.result!.runs.find(r => r.policy_id === 'scenario-lp-v1') || job.result!.runs.at(-1)!).run_id)}>Open recomputed plan</Button>}</div>}
    </section></>;
}
function Scenarios({run, onSelect}: {run: Run; onSelect: (id: string) => void}) {
  const [rows, setRows] = useState<Comparison[]>([]), [error, setError] = useState('');
  useEffect(() => { let live = true; api<{items: Comparison[]}>(`/runs/${run.run_id}/scenarios`).then(r => { if (live) setRows(r.items); }).catch(e => {if (live) setError(String(e));}); return () => {live = false;}; }, []);
  return <><div className="section-heading"><div><h2>Matched policy comparison</h2><p>Identical exogenous events and funding budget. Costs use synthetic assumptions.</p></div></div><ErrorBox message={error}/><CostComparison rows={rows}/><div className="table-wrap"><Table><TableHeader><TableRow><TableHead>Policy</TableHead><TableHead>Failures</TableHead><TableHead>Failed notional</TableHead><TableHead>Bridge cost</TableHead><TableHead>Late cost</TableHead><TableHead>Route cost</TableHead><TableHead>Total cost</TableHead><TableHead>Reserve used / limit</TableHead></TableRow></TableHeader><TableBody>{rows.map(r => <TableRow key={r.run_id}><TableCell><Button variant="link" className="link" onClick={() => onSelect(r.run_id)}>{policyName(r.policy_id)}</Button></TableCell><TableCell>{r.metrics.failed_count} / {r.metrics.trade_count}</TableCell><TableCell>{cash(r.metrics.failed_notional)}</TableCell><TableCell>{r.metrics.bridge_cost}</TableCell><TableCell>{r.metrics.late_cost}</TableCell><TableCell>{r.metrics.route_cost}</TableCell><TableCell>{cash(r.metrics.total_cost)}</TableCell><TableCell>{r.metrics.budget_used} / {r.metrics.budget_limit}</TableCell></TableRow>)}</TableBody></Table></div><p className="context">Replay results as of {stamp(run.result_as_of)}. Planning objectives are separate from realised costs. Older runs without a comparison identity appear alone.</p></>;
}

function CostComparison({rows}: {rows: Comparison[]}) {
  const data = rows.map(row=>({policy:policyName(row.policy_id), cost:Number(row.metrics.total_cost), run_id:row.run_id}));
  return rows.length ? <section className="comparison-chart" aria-label="Total cost by policy"><div className="section-heading"><div><h3>Realised cost by policy</h3><p>Compare funding outcomes across matched runs · lower is better</p></div><Badge variant="secondary" className="badge muted">Same scenario & budget</Badge></div><ChartContainer config={{cost:{label:'Total replay cost',color:'var(--chart-1)'}}} className="policy-cost-chart"><BarChart accessibilityLayer data={data} margin={{left:8,right:22,top:8,bottom:4}}><CartesianGrid vertical={false}/><XAxis dataKey="policy" tickLine={false} axisLine={false} tickMargin={10} interval={0} tick={{fontSize:12}}/><YAxis tickLine={false} axisLine={false} tickMargin={8} width={76} tickFormatter={v=>'$'+Number(v).toLocaleString()}/><ChartTooltip cursor={false} content={<ChartTooltipContent formatter={(value)=>cash(String(value))}/>} /><Bar dataKey="cost" fill="var(--color-cost)" radius={[6,6,0,0]} maxBarSize={52}/></BarChart></ChartContainer></section> : null;
}

function ModelHealth({run}: {run: Run}) {
  const [models, setModels] = useState<{model_id: string}[]>([]), [id, setId] = useState(''), [health, setHealth] = useState<Record<string, unknown> | null>(null), [error, setError] = useState('');
  useEffect(() => { let live = true; api<{items: {model_id: string}[]}>('/models').then(r => { if (live) { setModels(r.items); setId(run.timing_model_id || r.items[0]?.model_id || ''); } }).catch(e => { if (live) setError(String(e)); }); return () => { live = false; }; }, []);
  useEffect(() => { let live = true; setHealth(null); if (id) api<Record<string, unknown>>(`/${id.startsWith('timing-') ? 'timing-models' : 'models'}/${id}/health`).then(r => { if (live) { setHealth(r); setError(''); } }).catch(e => { if (live) setError(String(e)); }); return () => { live = false; }; }, [id]);
  return <><div className="section-heading"><div><h2>Model evidence</h2><p>Frozen evaluation reports and provenance. Synthetic results do not establish real-world calibration.</p></div></div><Label>Model<Select label="Model" value={id} onValueChange={setId} options={[{value:'',label:'Select a model'}, ...Array.from(new Set([...(run.timing_model_id?[run.timing_model_id]:[]), ...models.map(m=>m.model_id)])).map(value=>({value,label:value}))]}/></Label><ErrorBox message={error}/>{!id && <Empty>No model selected. Rule scores are available without a trained model. Train a risk or timing model to inspect its evidence here.</Empty>}{health && <section className="panel"><p className="model-identifier">{id}</p><HealthSummary health={health}/>{Object.entries(health).filter(([key]) => !['model_id', 'timing_model_id', 'model_card'].includes(key)).map(([key, value]) => <details key={key}><summary>{key.replaceAll('_', ' ')}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>)}{typeof health.model_card === 'string' && <details><summary>Model card & assumptions</summary><pre>{health.model_card}</pre></details>}</section>}</>;
}


function HealthSummary({health}: {health: Record<string, unknown>}) {
  type Bin = {lower: number; upper: number; count: number; mean_probability: number | null; failure_rate?: number | null; late_frequency?: number | null};
  type Evaluation = {rows?: number; count?: number; brier?: number; brier_score?: number; roc_auc?: number; pr_auc_average_precision?: number; reliability_bins?: Bin[]};
  const metrics = health.metrics as Record<string, unknown>;
  const timing = typeof health.timing_model_id === 'string';
  const evaluations: [string, Evaluation][] = timing ? [['Empirical FX timing', metrics as Evaluation]] : ['rules', 'logistic', 'xgboost'].filter(key => metrics[key]).map(key => [key, metrics[key] as Evaluation]);
  const primary = timing ? metrics as Evaluation : metrics.xgboost as Evaluation | undefined;
  const number = (value: number | null | undefined) => value == null ? '—' : value.toFixed(4);
  return <><div className="table-wrap"><Table><TableHeader><TableRow><TableHead>Model</TableHead><TableHead>Observations</TableHead><TableHead>Brier score</TableHead><TableHead>ROC AUC</TableHead><TableHead>PR AUC</TableHead></TableRow></TableHeader><TableBody>{evaluations.map(([name, result]) => <TableRow key={name}><TableCell>{name}</TableCell><TableCell>{result.rows ?? result.count ?? '—'}</TableCell><TableCell>{number(result.brier ?? result.brier_score)}</TableCell><TableCell>{number(result.roc_auc)}</TableCell><TableCell>{number(result.pr_auc_average_precision)}</TableCell></TableRow>)}</TableBody></Table></div>{primary?.reliability_bins && <><h3>Calibration by probability range</h3><div className="table-wrap"><Table><TableHeader><TableRow><TableHead>Range</TableHead><TableHead>Count</TableHead><TableHead>Mean probability</TableHead><TableHead>Observed frequency</TableHead></TableRow></TableHeader><TableBody>{primary.reliability_bins.map(bin => <TableRow key={bin.lower}><TableCell>{bin.lower.toFixed(1)}–{bin.upper.toFixed(1)}</TableCell><TableCell>{bin.count}</TableCell><TableCell>{number(bin.mean_probability)}</TableCell><TableCell>{number(bin.failure_rate ?? bin.late_frequency)}</TableCell></TableRow>)}</TableBody></Table></div></>}</>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><App/></StrictMode>);

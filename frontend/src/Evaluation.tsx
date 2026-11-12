import { useEffect, useState } from 'react';
import { Popover } from 'radix-ui';
import { Button } from './components/ui/button';
import { Input } from './components/ui/input';
import { Label } from './components/ui/label';
import { Select } from './components/ui/select';

export const ZONES = ['Etc/GMT+12','Pacific/Pago_Pago','Pacific/Honolulu','Pacific/Marquesas','America/Anchorage','America/Los_Angeles','America/Denver','America/Chicago','America/New_York','America/Halifax','America/St_Johns','America/Sao_Paulo','Atlantic/South_Georgia','Atlantic/Azores','UTC','Europe/London','Europe/Paris','Europe/Athens','Europe/Moscow','Asia/Tehran','Asia/Dubai','Asia/Kabul','Asia/Karachi','Asia/Kolkata','Asia/Kathmandu','Asia/Dhaka','Asia/Yangon','Asia/Bangkok','Asia/Shanghai','Australia/Eucla','Asia/Tokyo','Australia/Darwin','Australia/Sydney','Australia/Lord_Howe','Pacific/Noumea','Pacific/Auckland','Pacific/Chatham','Pacific/Kiritimati'];
export function localParts(instant: string, zone: string) {
  const parts = new Intl.DateTimeFormat('en-CA', {timeZone:zone, year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(instant));
  const p = Object.fromEntries(parts.map(x=>[x.type,x.value]));
  return {day:`${p.year}-${p.month}-${p.day}`,clock:`${p.hour}:${p.minute}`};
}
const contextRequests=new Map<string,Promise<any>>();
function context(runId:string){const key=`/runs/${runId}/evaluation-context`;let pending=contextRequests.get(key);if(!pending){pending=request(key).finally(()=>contextRequests.delete(key));contextRequests.set(key,pending);}return pending;}
async function request(path: string, options?: RequestInit) {
  const response = await fetch('/api/v1'+path, options); const data = await response.json();
  if (!response.ok) throw Object.assign(new Error(data.message || data.detail || 'Request failed'),{status:response.status}); return data;
}
export function CalendarPicker({value,onChange,min,max}: {value:string;onChange:(day:string)=>void;min?:string;max?:string}) {
  const [month,setMonth] = useState(value.slice(0,7)), [open,setOpen] = useState(false);
  const [year,m] = month.split('-').map(Number);
  const count = new Date(Date.UTC(year,m,0)).getUTCDate(), start = (new Date(Date.UTC(year,m-1,1)).getUTCDay()+6)%7;
  function move(delta:number) { const next = new Date(Date.UTC(year,m-1+delta,1));setMonth(next.toISOString().slice(0,7)); }
  return <Popover.Root open={open} onOpenChange={next=>{if(next)setMonth(value.slice(0,7));setOpen(next);}}><Popover.Trigger asChild><Button variant="outline" aria-label="Evaluation date">{value}</Button></Popover.Trigger><Popover.Portal><Popover.Content className="evaluation-calendar" sideOffset={8} align="start"><div className="calendar-heading"><Button variant="ghost" aria-label="Previous month" disabled={!!min && month <= min.slice(0,7)} onClick={()=>move(-1)}>‹</Button><strong>{new Date(Date.UTC(year,m-1,1)).toLocaleDateString('en-GB',{month:'long',year:'numeric',timeZone:'UTC'})}</strong><Button variant="ghost" aria-label="Next month" disabled={!!max && month >= max.slice(0,7)} onClick={()=>move(1)}>›</Button></div><div className="calendar-days">{['Mo','Tu','We','Th','Fr','Sa','Su'].map(day=><span key={day}>{day}</span>)}{Array.from({length:start},(_,i)=><span key={'blank'+i}/>)}{Array.from({length:count},(_,i)=>{const day=`${month}-${String(i+1).padStart(2,'0')}`;return <Button key={day} disabled={!!((min && day < min) || (max && day > max))} size="icon" variant={day===value?'default':'ghost'} aria-label={day} aria-pressed={day===value} onClick={()=>{onChange(day);setOpen(false);}}>{i+1}</Button>;})}</div></Popover.Content></Popover.Portal></Popover.Root>;
}
export function Evaluation({run,onResult}: {run:{run_id:string;decision_at:string;policy_id:string};onResult:(id:string)=>void}) {
  const [zone,setZone] = useState(()=>{const saved=localStorage.getItem('evaluation-zone');return saved && ZONES.includes(saved) ? saved : 'UTC';});
  const [online,setOnline] = useState<{utc_now:string;source:string;received:number}|null>(null);
  const [clockError,setClockError] = useState('');
  const [,setTick] = useState(0);
  useEffect(()=>{let live=true;async function sync(){try{const data=await request('/time');if(live){setOnline({...data,received:performance.now()});setClockError('');}}catch(e){if(live)setClockError(String(e));}}void sync();const refresh=setInterval(()=>void sync(),60000);const tick=setInterval(()=>setTick(n=>n+1),30000);return()=>{live=false;clearInterval(refresh);clearInterval(tick);};},[]);
  const synchronized = online && performance.now()-online.received <= 300000;
  const now = synchronized && online ? Date.parse(online.utc_now)+Math.max(0,performance.now()-online.received) : null;
  const [bounds,setBounds] = useState<{starts_at:string;ends_at:string;replay_settings?:Record<string,unknown>}|null>(null);
  useEffect(()=>{let live=true;context(run.run_id).then(data=>{if(live)setBounds(data);}).catch(e=>{if(live)setError(String(e));});return()=>{live=false;};},[run.run_id]);
  const minimum = bounds ? localParts(new Date(Math.ceil(Date.parse(bounds.starts_at)/60000)*60000).toISOString(),zone) : null;
  const maximum = bounds && now !== null ? localParts(new Date(Math.min(Date.parse(bounds.ends_at)-1, now)).toISOString(),zone) : null;
  const initial = localParts(run.decision_at,zone);
  const [day,setDay]=useState(initial.day), [clock,setClock]=useState(initial.clock), [error,setError]=useState(''), [jobId,setJobId]=useState(()=>localStorage.getItem('evaluation-job:'+run.run_id)||''),[busy,setBusy]=useState(()=>!!localStorage.getItem('evaluation-job:'+run.run_id));
  const selected = `${day}T${clock}`;
  const outside = !!(minimum && maximum && (selected < `${minimum.day}T${minimum.clock}` || selected > `${maximum.day}T${maximum.clock}`));
  useEffect(()=>{if(!jobId)return;let live=true;let timer:ReturnType<typeof setTimeout>;
    const finish=()=>{setBusy(false);setJobId('');localStorage.removeItem('evaluation-job:'+run.run_id);};
    async function poll(){try{const job=await request('/jobs/'+jobId);if(!live)return;
      if(job.status==='SUCCEEDED'){const result=(job.result?.runs?.find((r:{policy_id:string})=>r.policy_id===run.policy_id)||job.result?.runs?.at(-1));finish();if(result)onResult(result.run_id);else setError('Evaluation finished without a saved run. Please try again.');}
      else if(['FAILED','CANCELED'].includes(job.status)){finish();setError(job.error||'Evaluation canceled');}
      else timer=setTimeout(poll,1500);
    }catch(e){if(live){if((e as {status?:number}).status===404){finish();setError('The previous evaluation job is no longer available. Apply evaluation again.');}else{setError(String(e));timer=setTimeout(poll,5000);}}}}
    void poll();return()=>{live=false;clearTimeout(timer);};},[jobId]);
  async function apply(){setBusy(true);setError('');try{const m=await request(`/runs/${run.run_id}/metrics`);const job=await request(`/runs/${run.run_id}/funding/recompute`,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()},body:JSON.stringify({...bounds?.replay_settings,liquidity_limit:m.metrics.budget_limit,evaluation_local:`${day}T${clock}:00`,evaluation_timezone:zone})});localStorage.setItem('evaluation-job:'+run.run_id,job.job_id);setJobId(job.job_id);}catch(e){setBusy(false);setError(String(e));}}
  return <section className="evaluation-controls" aria-label="Evaluation controls"><div className="evaluation-date-group"><h3 className="evaluation-caption">Observe trades at</h3><div className="evaluation-date-fields"><Label>Evaluation date<CalendarPicker value={day} onChange={setDay} min={minimum?.day} max={maximum?.day}/></Label><Label>Local time<Input aria-label="Evaluation local time" type="time" value={clock} onChange={e=>setClock(e.target.value)}/></Label></div></div><div className="evaluation-zone-group"><h3 className="evaluation-caption">Interpret local time in</h3><Label>Timezone · 38 options<Select label="Evaluation timezone" value={zone} onValueChange={next=>{setZone(next);localStorage.setItem('evaluation-zone',next);}} options={ZONES.map(value=>({value,label:value.replaceAll('_',' ')}))}/></Label></div><Button disabled={busy||!clock||!bounds||!synchronized||outside} onClick={()=>void apply()}>{busy?'Evaluating…':'Apply evaluation'}</Button><p className="evaluation-note">Replay saved observations and funding. Choose a past time within this run.{minimum && maximum && <> Available: {minimum.day} {minimum.clock} through {maximum.day} {maximum.clock} ({zone}).</>}</p>{outside && <p role="alert" className="evaluation-error">Choose a time within this run’s available range. Future evaluation times are unavailable. Select a run covering your chosen date.</p>}{!online && !clockError && <p className="evaluation-note">Synchronizing date and time online…</p>}{online && <p className="evaluation-note">Online time ({zone}): {localParts(new Date(Date.parse(online.utc_now)+Math.max(0,performance.now()-online.received)).toISOString(),zone).day} {localParts(new Date(Date.parse(online.utc_now)+Math.max(0,performance.now()-online.received)).toISOString(),zone).clock}</p>}{clockError && <p role="alert" className="evaluation-error">{clockError}</p>}{error&&<p role="alert" className="evaluation-error">{error}</p>}</section>;
}
export function MonthlySchedule({runId}:{runId:string}) {
  const [trades,setTrades]=useState<{executed_at:string;deadline_at:string}[]>([]);
  const [scheduleError,setScheduleError]=useState('');
  useEffect(()=>{let live=true;context(runId).then(data=>{if(live)setTrades(data.trades);}).catch(e=>{if(live)setScheduleError(String(e));});return()=>{live=false;};},[runId]);
  const dates = new Map<string,number>();for(const trade of trades){const day=trade.executed_at.slice(0,10);dates.set(day,(dates.get(day)||0)+1);}
  return <details className="month-schedule"><summary>Trade execution schedule · {trades.length} trades across {dates.size} execution dates</summary>{scheduleError&&<p role="alert">{scheduleError}</p>}<p>Includes future synthetic trades. The watchlist shows only trades known at the selected evaluation time. Dates below use UTC.</p><div className="schedule-dates">{[...dates].sort().map(([day,count])=><div key={day}><time dateTime={day}>{day}</time><strong>{count} trades</strong></div>)}</div></details>;
}

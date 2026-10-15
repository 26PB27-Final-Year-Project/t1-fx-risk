import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from '@/components/ui/card';
import { useEffect, useState, type ReactNode } from 'react';

const paths: Record<string, ReactNode> = {
  grid: <><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></>,
  wallet: <><path d="M20 8V5a2 2 0 0 0-2-2H6a3 3 0 0 0 0 6h14v11H6a3 3 0 0 1-3-3V6"/><path d="M20 12h-5v5h5"/><path d="M16 14.5h1"/></>,
  chart: <><path d="M4 3v17h17M8 15v-4m5 4V7m5 8V4"/></>,
  pulse: <><path d="M3 12h4l3-8 4 16 3-8h4"/></>,
  sun: <><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1"/></>,
  moon: <path d="M21 13a9 9 0 0 1-10-10A9 9 0 1 0 21 13Z"/>,
  monitor: <><rect x="3" y="3" width="18" height="13" rx="2"/><path d="M8 21h8m-4-5v5"/></>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  refresh: <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2"/></>,
  search: <><circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/></>,
  arrow: <path d="M4 12h16m-6-6 6 6-6 6"/>,
  check: <path d="m5 12 4 4L19 6"/>,
  info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/></>,
  filter: <><path d="M4 7h16M4 17h16"/><circle cx="8" cy="7" r="2"/><circle cx="16" cy="17" r="2"/></>,
};
export function Icon({name, size = 18}: {name: string; size?: number}) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.grid}</svg>;
}
export function ThemeControl() {
  const [theme, setTheme] = useState(() => {
    try { const saved = localStorage.getItem('t1fx-theme'); return saved && ['light', 'dark', 'system'].includes(saved) ? saved : 'system'; } catch { return 'system'; }
  });
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)');
    const apply = () => { document.documentElement.dataset.theme = theme === 'system' ? (media.matches ? 'dark' : 'light') : theme; };
    apply();
    try { localStorage.setItem('t1fx-theme', theme); } catch { /* Theme still works without storage. */ }
    media.addEventListener('change', apply);
    return () => media.removeEventListener('change', apply);
  }, [theme]);
  return <div className="theme-control" role="group" aria-label="Appearance">{(['light', 'dark', 'system'] as const).map(value => <Button variant="ghost" size="icon" key={value} title={`${value[0].toUpperCase() + value.slice(1)} theme`} aria-label={`${value[0].toUpperCase() + value.slice(1)} theme`} aria-pressed={theme === value} onClick={() => setTheme(value)}><Icon name={value === 'light' ? 'sun' : value === 'dark' ? 'moon' : 'monitor'} size={16}/></Button>)}</div>;
}
export function Status({value}: {value: string}) {
  const good = ['VALID', 'INSTRUCTED', 'OPTIMAL', 'SUCCEEDED', 'CREDITED'].includes(value);
  const bad = ['MISMATCH', 'FAILED', 'FALLBACK'].includes(value);
  return <Badge variant="outline" className={`status ${good ? 'good' : bad ? 'bad' : 'neutral'}`}><span/>{value.toLowerCase().replaceAll('_', ' ')}</Badge>;
}
export function RiskMeter({score}: {score: number}) {
  return <div className={`risk-meter ${score >= 70 ? 'high' : score >= 40 ? 'medium' : 'low'}`} aria-label={`Rule score ${score} of 100`}><span>{score}<small>/100</small></span><div><i style={{width: `${score}%`}}/></div></div>;
}
export function StatCard({title, value, note, icon = 'chart'}: {title: string; value: string; note?: string; icon?: string}) {
  return <Card className="stat-card"><CardHeader className="stat-header"><CardTitle>{title}</CardTitle><Icon name={icon}/></CardHeader><CardContent className="stat-content"><strong>{value}</strong>{note && <CardDescription>{note}</CardDescription>}</CardContent></Card>;
}

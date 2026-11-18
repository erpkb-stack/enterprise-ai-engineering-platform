import { Gauge, Zap } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ErrorState, Kv, Loading, PageHeader, StatCard, StatusBadge } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/form";
import { Switch } from "@/components/ui/switch";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useAction, useApi } from "@/hooks/api";
import { fmt } from "@/lib";
import { post } from "@/services/api";
import { useAuth } from "@/stores/auth";

type Summary = { name?: string; count: number; errors: number; error_rate: number | null; rps: number; p50_ms: number | null; p95_ms: number | null; p99_ms: number | null; max_ms: number | null; synthetic_count?: number; retries?: number };
type Metrics = { window_s: number; source: string; http_total: Summary; routes: Summary[]; llm: Summary[]; tools: Summary[]; agents: Summary[]; workflows: Summary[]; retrieval: Summary[]; embeddings: Summary[];
  timeseries: { t: number; requests: number; synthetic: number; errors: number; rps: number; p50_ms: number | null; p95_ms: number | null; p99_ms: number | null }[];
  concurrency: { max_concurrency: number; max_queue: number; active: number; queued: number; rejected_total: number; background_jobs: number; ws_subscribers: number; ws_events_dropped: number };
  circuit_breakers: { name: string; state: string; consecutive_failures: number; total_failures: number; total_successes: number }[];
  retries_total: number; system: { process_cpu_percent: number; system_cpu_percent: number; process_rss_mb: number; system_memory_percent: number; threads: number; open_fds: number | null; source: string } };
type LoadRes = { target: string; requests: number; concurrency: number; wall_time_s: number; throughput_rps: number; p50_ms: number; p95_ms: number; p99_ms: number; max_ms: number;
  errors: number; error_rate: number; status_codes: Record<string, number>; histogram: { le_ms: number; count: number }[]; label: string };

function OpTable({ title, rows }: { title: string; rows: Summary[] }) {
  if (!rows.length) return null;
  return (
    <Card><CardHeader><CardTitle>{title}</CardTitle></CardHeader><CardContent className="p-0">
      <Table><THead><TR><TH>Name</TH><TH>Count</TH><TH>RPS</TH><TH>P50</TH><TH>P95</TH><TH>P99</TH><TH>Errors</TH><TH>Retries</TH></TR></THead>
        <TBody>{rows.slice(0, 15).map((r) => (
          <TR key={r.name}><TD className="max-w-xs truncate font-mono text-[11px]">{r.name}{!!r.synthetic_count && <Badge variant="info" className="ml-1">{r.synthetic_count} synthetic</Badge>}</TD>
            <TD className="tabular-nums">{r.count}</TD><TD className="tabular-nums">{r.rps.toFixed(2)}</TD><TD>{fmt.ms(r.p50_ms)}</TD><TD>{fmt.ms(r.p95_ms)}</TD><TD>{fmt.ms(r.p99_ms)}</TD>
            <TD className={r.errors ? "text-danger" : ""}>{r.errors} ({fmt.pct(r.error_rate)})</TD><TD>{r.retries ?? 0}</TD></TR>))}</TBody></Table>
    </CardContent></Card>
  );
}

export default function PerformancePage() {
  const can = useAuth((s) => s.can);
  const [win, setWin] = useState(900);
  const [synthetic, setSynthetic] = useState(true);
  const q = useApi<Metrics>(["metrics", win, synthetic], `/metrics?window_s=${win}&include_synthetic=${synthetic}`, { refetchInterval: 5000 });
  const [lt, setLt] = useState({ target: "list_agents", requests: 300, concurrency: 20 });
  const load = useAction(() => post<LoadRes>("/performance/loadtest", { ...lt, transport: "network" }), { success: (r) => `${r.requests} requests at ${r.throughput_rps} rps` });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} />;
  const m = q.data!;
  const ts = m.timeseries.map((p) => ({ ...p, time: new Date(p.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) }));
  return (
    <>
      <PageHeader icon={Gauge} title="Performance Engineering" description={m.source}
        actions={<><Select aria-label="Window" value={win} onChange={(e) => setWin(Number(e.target.value))} className="w-32">{[300, 900, 1800, 3600].map((w) => <option key={w} value={w}>last {w / 60} min</option>)}</Select>
          <label className="flex items-center gap-2 text-xs"><Switch checked={synthetic} onCheckedChange={setSynthetic} label="Include synthetic" /> include synthetic</label></>} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <StatCard label="Requests" value={fmt.num(m.http_total.count)} hint={`${m.http_total.synthetic_count ?? 0} synthetic`} />
        <StatCard label="Throughput" value={`${m.http_total.rps.toFixed(2)}/s`} />
        <StatCard label="P50" value={fmt.ms(m.http_total.p50_ms)} /><StatCard label="P95" value={fmt.ms(m.http_total.p95_ms)} /><StatCard label="P99" value={fmt.ms(m.http_total.p99_ms)} />
        <StatCard label="Error rate" value={fmt.pct(m.http_total.error_rate)} tone={(m.http_total.error_rate ?? 0) > 0.01 ? "danger" : undefined} />
        <StatCard label="CPU (proc / sys)" value={`${m.system.process_cpu_percent.toFixed(0)}%`} hint={`system ${m.system.system_cpu_percent.toFixed(0)}%`} />
        <StatCard label="Memory RSS" value={`${m.system.process_rss_mb} MB`} hint={`system ${m.system.system_memory_percent}%`} />
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card><CardHeader><CardTitle>Latency percentiles</CardTitle><CardDescription>Measured per bucket from real requests</CardDescription></CardHeader>
          <CardContent className="h-60"><ResponsiveContainer><LineChart data={ts}><CartesianGrid strokeDasharray="3 3" stroke="var(--border)" /><XAxis dataKey="time" fontSize={10} stroke="var(--muted-foreground)" minTickGap={40} />
            <YAxis fontSize={10} stroke="var(--muted-foreground)" unit="ms" /><Tooltip contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", fontSize: 12 }} /><Legend wrapperStyle={{ fontSize: 11 }} />
            <Line dataKey="p50_ms" name="P50" stroke="var(--chart-2)" dot={false} /><Line dataKey="p95_ms" name="P95" stroke="var(--chart-1)" dot={false} />
            <Line dataKey="p99_ms" name="P99" stroke="var(--chart-4)" dot={false} /></LineChart></ResponsiveContainer></CardContent></Card>
        <Card><CardHeader><CardTitle>Throughput & errors</CardTitle><CardDescription>Synthetic (load test) vs organic requests</CardDescription></CardHeader>
          <CardContent className="h-60"><ResponsiveContainer><BarChart data={ts.map((p) => ({ ...p, organic: p.requests - p.synthetic }))}><CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="time" fontSize={10} stroke="var(--muted-foreground)" minTickGap={40} /><YAxis fontSize={10} stroke="var(--muted-foreground)" />
            <Tooltip contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", fontSize: 12 }} /><Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar dataKey="organic" stackId="a" fill="var(--chart-1)" /><Bar dataKey="synthetic" stackId="a" fill="var(--chart-3)" /><Bar dataKey="errors" fill="var(--chart-4)" /></BarChart></ResponsiveContainer></CardContent></Card>
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card><CardHeader><CardTitle>Concurrency & backpressure</CardTitle></CardHeader><CardContent>
          <Kv items={[["Active executions", `${m.concurrency.active} / ${m.concurrency.max_concurrency}`], ["Queued", `${m.concurrency.queued} / ${m.concurrency.max_queue}`],
            ["Rejected (503)", String(m.concurrency.rejected_total)], ["Background jobs", String(m.concurrency.background_jobs)], ["WebSocket subscribers", String(m.concurrency.ws_subscribers)],
            ["WS events dropped", String(m.concurrency.ws_events_dropped)], ["Retries (total)", String(m.retries_total)], ["Threads / FDs", `${m.system.threads} / ${m.system.open_fds ?? "—"}`]]} />
        </CardContent></Card>
        <Card><CardHeader><CardTitle>Circuit breakers</CardTitle></CardHeader><CardContent className="space-y-1">
          {m.circuit_breakers.length ? m.circuit_breakers.map((b) => (
            <div key={b.name} className="flex items-center justify-between text-xs"><span className="truncate font-mono">{b.name}</span>
              <span className="flex items-center gap-2"><span className="text-muted-foreground">{b.total_successes}✓ {b.total_failures}✗</span><StatusBadge status={b.state} /></span></div>))
            : <p className="text-xs text-muted-foreground">No dependencies called yet.</p>}
        </CardContent></Card>
        <Card><CardHeader><CardTitle className="flex items-center gap-2"><Zap className="size-4" /> Load test runner</CardTitle><CardDescription>Bounded synthetic load (real HTTP requests, tagged synthetic). For heavier tests use <code>scripts/loadtest</code> (Locust).</CardDescription></CardHeader>
          <CardContent className="space-y-2">
            <Field label="Target"><Select value={lt.target} onChange={(e) => setLt({ ...lt, target: e.target.value })}>{["health", "list_agents", "dashboard", "knowledge_search"].map((t) => <option key={t}>{t}</option>)}</Select></Field>
            <div className="grid grid-cols-2 gap-2"><Field label="Requests"><Input type="number" min={1} max={5000} value={lt.requests} onChange={(e) => setLt({ ...lt, requests: Number(e.target.value) })} /></Field>
              <Field label="Concurrency"><Input type="number" min={1} max={100} value={lt.concurrency} onChange={(e) => setLt({ ...lt, concurrency: Number(e.target.value) })} /></Field></div>
            <Button className="w-full" onClick={() => load.mutate(undefined)} loading={load.isPending} disabled={!can("loadtest:run")}>Run load test</Button>
          </CardContent></Card>
      </div>
      {load.data && <Card className="mt-4"><CardHeader><CardTitle>{load.data.label}</CardTitle><CardDescription>{load.data.requests} × {load.data.target} @ concurrency {load.data.concurrency} in {load.data.wall_time_s}s</CardDescription></CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-[320px_1fr]">
          <Kv items={[["Throughput", `${load.data.throughput_rps} req/s`], ["P50 / P95 / P99", `${fmt.ms(load.data.p50_ms)} / ${fmt.ms(load.data.p95_ms)} / ${fmt.ms(load.data.p99_ms)}`],
            ["Max", fmt.ms(load.data.max_ms)], ["Errors", `${load.data.errors} (${fmt.pct(load.data.error_rate)})`], ["Status codes", JSON.stringify(load.data.status_codes)]]} />
          <div className="h-48"><ResponsiveContainer><BarChart data={load.data.histogram.map((h, i, a) => ({ bucket: `≤${h.le_ms}ms`, count: h.count - (i ? a[i - 1].count : 0) }))}>
            <XAxis dataKey="bucket" fontSize={10} stroke="var(--muted-foreground)" /><YAxis fontSize={10} stroke="var(--muted-foreground)" /><Tooltip contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", fontSize: 12 }} />
            <Bar dataKey="count" fill="var(--chart-3)" /></BarChart></ResponsiveContainer></div>
        </CardContent></Card>}
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <OpTable title="HTTP routes" rows={m.routes} /><OpTable title="Model calls" rows={m.llm} /><OpTable title="Tool executions" rows={m.tools} />
        <OpTable title="Agent executions" rows={m.agents} /><OpTable title="Retrieval" rows={[...m.retrieval, ...m.embeddings]} /><OpTable title="Workflows" rows={m.workflows} />
      </div>
    </>
  );
}

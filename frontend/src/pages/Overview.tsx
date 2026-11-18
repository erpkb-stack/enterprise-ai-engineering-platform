import { Activity, Bot, Clock, DollarSign, FlaskConical, Gauge, LayoutDashboard, Play, Server, ShieldAlert, Workflow } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Empty, ErrorState, Loading, MockBadge, PageHeader, StatCard, StatusBadge } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useAction, useApi, type Page } from "@/hooks/api";
import { fmt } from "@/lib";
import { get, post } from "@/services/api";
import type { Agent, Execution } from "@/types";

type Overview = {
  window_hours: number; data_source: string;
  agents: { active: number; total: number };
  mcp: { servers: number; connected: number; enabled_tools: number; policy_denials: number };
  executions: { total: number; failed: number; error_rate: number | null; avg_latency_ms: number | null; series: { t: string; count: number; avg_latency_ms: number }[] };
  workflows: { total: number; by_status: Record<string, number> };
  model_usage: { provider: string; model: string; calls: number; tokens_in: number; tokens_out: number; cost_usd: number; avg_latency_ms: number; errors: number; is_mock: boolean }[];
  estimated_cost_usd: number;
  evaluations: { id: string; name: string; pass_rate: number | null; groundedness: number | null; finished_at: string }[];
  red_team: { defense_rate: number | null } | null;
  http: { count: number; p95_ms: number | null; error_rate: number | null; rps: number };
  security_events: { action: string; outcome: string; severity: string; actor: string; at: string }[];
  recent_executions: { id: string; agent: string; status: string; latency_ms: number; is_mock: boolean; created_at: string }[];
};

const FLAGSHIP = "How quickly must a Sev2 incident be acknowledged, and what is the current checkout service status?";

export default function OverviewPage() {
  const nav = useNavigate();
  const q = useApi<Overview>(["overview"], "/dashboard/overview", { refetchInterval: 10_000 });
  const run = useAction(async () => {
    const agents = await get<Page<Agent>>("/agents?q=Orchestrator");
    const orch = agents.items.find((a) => a.agent_type === "orchestrator" && a.status === "active");
    if (!orch) throw new Error("No active orchestrator agent");
    const ex = await post<Execution>(`/agents/${orch.id}/execute`, { input: FLAGSHIP });
    return { orch, ex };
  }, { invalidate: [["overview"]], success: "Flagship scenario completed", onSuccess: ({ orch, ex }) => nav(`/agents/${orch.id}?execution=${ex.id}`) });

  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data!;
  return (
    <>
      <PageHeader icon={LayoutDashboard} title="Engineering Overview"
        description={`Last ${d.window_hours}h · ${d.data_source}`}
        actions={<Button onClick={() => run.mutate(undefined)} loading={run.isPending}><Play /> Run flagship research scenario</Button>} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <StatCard label="Active agents" value={d.agents.active} hint={`${d.agents.total} total`} icon={Bot} />
        <StatCard label="MCP connections" value={`${d.mcp.connected}/${d.mcp.servers}`} hint={`${d.mcp.enabled_tools} tools allowlisted`} icon={Server} />
        <StatCard label="Agent executions" value={fmt.num(d.executions.total)} hint={`${d.executions.failed} failed`} icon={Activity} />
        <StatCard label="Workflow runs" value={fmt.num(d.workflows.total)} hint={Object.entries(d.workflows.by_status).map(([k, v]) => `${v} ${k}`).join(" · ") || "none yet"} icon={Workflow} />
        <StatCard label="Avg exec latency" value={fmt.ms(d.executions.avg_latency_ms)} hint={`API p95 ${fmt.ms(d.http.p95_ms)} (15m)`} icon={Clock} />
        <StatCard label="Error rate" value={fmt.pct(d.executions.error_rate)} tone={(d.executions.error_rate ?? 0) > 0.1 ? "danger" : undefined} hint={`API ${fmt.pct(d.http.error_rate)}`} icon={Gauge} />
        <StatCard label="Est. AI cost" value={fmt.usd(d.estimated_cost_usd)} hint="from provider token usage" icon={DollarSign} />
        <StatCard label="Red-team defense" value={fmt.pct(d.red_team?.defense_rate)} hint={`${d.mcp.policy_denials} policy denials`} icon={ShieldAlert} />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader><CardTitle>Agent executions per hour</CardTitle><CardDescription>Persisted execution records (not sampled)</CardDescription></CardHeader>
          <CardContent className="h-56">
            {d.executions.series.length ? (
              <ResponsiveContainer>
                <AreaChart data={d.executions.series.map((s) => ({ ...s, t: s.t.slice(11, 16) }))}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="t" fontSize={11} stroke="var(--muted-foreground)" />
                  <YAxis fontSize={11} stroke="var(--muted-foreground)" allowDecimals={false} />
                  <Tooltip contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", fontSize: 12 }} />
                  <Area dataKey="count" name="executions" stroke="var(--chart-1)" fill="var(--chart-1)" fillOpacity={0.2} />
                </AreaChart>
              </ResponsiveContainer>
            ) : <Empty title="No executions yet" description="Run the flagship scenario to generate real telemetry." />}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Evaluation scores</CardTitle><CardDescription>Latest completed runs</CardDescription></CardHeader>
          <CardContent className="space-y-2">
            {d.evaluations.length ? d.evaluations.map((e) => (
              <div key={e.id} className="flex items-center justify-between rounded-md border p-2 text-xs">
                <span className="truncate pr-2">{e.name}</span>
                <span className="flex gap-2 tabular-nums"><Badge variant="muted">pass {fmt.pct(e.pass_rate, 0)}</Badge><Badge variant="info">ground {fmt.pct(e.groundedness, 0)}</Badge></span>
              </div>
            )) : <Empty title="No evaluation runs" description="Run an evaluation in the AI Evaluation lab." />}
          </CardContent>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Model usage</CardTitle><CardDescription>Aggregated from usage_metrics</CardDescription></CardHeader>
          <CardContent>
            {d.model_usage.length ? (
              <Table><THead><TR><TH>Provider / model</TH><TH>Calls</TH><TH>Tokens</TH><TH>Avg latency</TH><TH>Cost</TH></TR></THead>
                <TBody>{d.model_usage.map((m) => (
                  <TR key={m.provider + m.model}><TD><div className="flex items-center gap-2">{m.provider}/{m.model}<MockBadge mock={m.is_mock} /></div></TD>
                    <TD className="tabular-nums">{m.calls}</TD><TD className="tabular-nums">{fmt.num(m.tokens_in + m.tokens_out)}</TD>
                    <TD>{fmt.ms(m.avg_latency_ms)}</TD><TD>{fmt.usd(m.cost_usd)}</TD></TR>))}</TBody></Table>
            ) : <Empty title="No model calls yet" />}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Recent executions</CardTitle></CardHeader>
          <CardContent>
            {d.recent_executions.length ? (
              <Table><THead><TR><TH>Agent</TH><TH>Status</TH><TH>Latency</TH><TH>When</TH></TR></THead>
                <TBody>{d.recent_executions.map((e) => (
                  <TR key={e.id} className="cursor-pointer" onClick={() => nav(`/agents?execution=${e.id}`)}>
                    <TD><div className="flex items-center gap-2">{e.agent}<MockBadge mock={e.is_mock} /></div></TD><TD><StatusBadge status={e.status} /></TD>
                    <TD>{fmt.ms(e.latency_ms)}</TD><TD className="text-muted-foreground">{fmt.ago(e.created_at)}</TD></TR>))}</TBody></Table>
            ) : <Empty title="No executions yet" />}
          </CardContent>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader><CardTitle className="flex items-center gap-2"><FlaskConical className="size-4" /> Recent security events</CardTitle><CardDescription>Warnings and critical audit events</CardDescription></CardHeader>
        <CardContent>
          {d.security_events.length ? (
            <Table><THead><TR><TH>Action</TH><TH>Outcome</TH><TH>Severity</TH><TH>Actor</TH><TH>When</TH></TR></THead>
              <TBody>{d.security_events.map((e, i) => (
                <TR key={i}><TD className="font-mono text-xs">{e.action}</TD><TD><StatusBadge status={e.outcome} /></TD>
                  <TD><Badge variant={e.severity === "critical" ? "danger" : "warning"}>{e.severity}</Badge></TD><TD>{e.actor}</TD>
                  <TD className="text-muted-foreground">{fmt.ago(e.at)}</TD></TR>))}</TBody></Table>
          ) : <Empty title="No security events" />}
        </CardContent>
      </Card>
    </>
  );
}

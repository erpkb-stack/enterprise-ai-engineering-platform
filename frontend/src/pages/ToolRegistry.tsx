import { Play, Wrench } from "lucide-react";
import { useState } from "react";
import { Empty, ErrorState, JsonView, Loading, PageHeader, StatusBadge } from "@/components/common";
import Editor from "@/components/common/LazyEditor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Select } from "@/components/ui/form";
import { Switch } from "@/components/ui/switch";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useAction, useApi, type Page } from "@/hooks/api";
import { fmt } from "@/lib";
import { post, put } from "@/services/api";
import { useAuth } from "@/stores/auth";
import type { Tool } from "@/types";

type ToolExec = { id: string; tool_name: string; status: string; latency_ms: number; error: string | null; created_at: string; agent_execution_id: string | null; arguments: Record<string, unknown> };
type ExecResult = { result: { ok: boolean; content: string; error: string | null; latency_ms: number; injection_flags: string[]; truncated: boolean };
  policy: { allowed: boolean; rule: string; reason: string; checks: { rule: string; passed: boolean; reason: string }[] } };

export default function ToolRegistryPage() {
  const can = useAuth((s) => s.can);
  const q = useApi<Tool[]>(["tools"], "/tools");
  const log = useApi<Page<ToolExec>>(["tool-execs"], "/tools/executions?limit=30", { refetchInterval: 10_000 });
  const [test, setTest] = useState<Tool | null>(null);
  const update = useAction(({ id, body }: { id: string; body: Partial<Tool> }) => put<Tool>(`/tools/${id}`, body),
    { invalidate: [["tools"]], success: (t) => `${t.qualified_name} updated` });

  return (
    <>
      <PageHeader icon={Wrench} title="Tool Registry" description="Global allowlist, risk classification and approval requirements for every tool. Agents additionally need the tool in their own allowlist." />
      {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} /> : (
        <Card><CardContent className="p-0">
          <Table><THead><TR><TH>Tool</TH><TH>Risk</TH><TH>Allowlisted</TH><TH>Needs approval</TH><TH>Calls</TH><TH>Avg latency</TH><TH>Error rate</TH><TH /></TR></THead>
            <TBody>{q.data!.map((t) => (
              <TR key={t.id}>
                <TD><div className="font-mono text-xs">{t.qualified_name}</div><div className="max-w-md text-[11px] text-muted-foreground">{t.description}</div></TD>
                <TD>{can("tools:manage") ? (
                  <Select aria-label="Risk level" className="h-7 w-24 text-xs" value={t.risk_level} onChange={(e) => update.mutate({ id: t.id, body: { risk_level: e.target.value } })}>
                    {["low", "medium", "high"].map((r) => <option key={r}>{r}</option>)}</Select>
                ) : <Badge variant={t.risk_level === "high" ? "danger" : t.risk_level === "medium" ? "warning" : "muted"}>{t.risk_level}</Badge>}</TD>
                <TD><Switch label={`Allowlist ${t.qualified_name}`} checked={t.is_enabled} disabled={!can("tools:manage")} onCheckedChange={(v) => update.mutate({ id: t.id, body: { is_enabled: v } })} /></TD>
                <TD><Switch label={`Require approval ${t.qualified_name}`} checked={t.requires_approval} disabled={!can("tools:manage")} onCheckedChange={(v) => update.mutate({ id: t.id, body: { requires_approval: v } })} /></TD>
                <TD className="tabular-nums">{t.call_count}</TD><TD>{fmt.ms(t.avg_latency_ms)}</TD><TD>{fmt.pct(t.error_rate)}</TD>
                <TD>{can("tools:execute") && <Button size="sm" variant="outline" onClick={() => setTest(t)}><Play /> Test</Button>}</TD>
              </TR>))}</TBody></Table>
        </CardContent></Card>
      )}
      <Card className="mt-4">
        <CardHeader><CardTitle>Tool invocation log</CardTitle><CardDescription>Every request (arguments redacted), outcome and latency — including policy denials.</CardDescription></CardHeader>
        <CardContent className="p-0">
          {!log.data?.items.length ? <div className="p-4"><Empty title="No tool invocations yet" /></div> : (
            <Table><THead><TR><TH>When</TH><TH>Tool</TH><TH>Status</TH><TH>Latency</TH><TH>Arguments</TH><TH>Error</TH></TR></THead>
              <TBody>{log.data.items.map((e) => (
                <TR key={e.id}><TD className="whitespace-nowrap text-muted-foreground">{fmt.ago(e.created_at)}</TD><TD className="font-mono text-xs">{e.tool_name}</TD>
                  <TD><StatusBadge status={e.status} /></TD><TD>{fmt.ms(e.latency_ms)}</TD>
                  <TD className="max-w-xs truncate font-mono text-[11px]">{JSON.stringify(e.arguments)}</TD><TD className="max-w-xs truncate text-xs text-danger">{e.error}</TD></TR>))}</TBody></Table>
          )}
        </CardContent>
      </Card>
      {test && <TestTool tool={test} onClose={() => setTest(null)} />}
    </>
  );
}

function sample(schema: Record<string, unknown>): string {
  const props = (schema.properties ?? {}) as Record<string, { type?: string; default?: unknown }>;
  const o: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(props)) o[k] = v.default ?? (v.type === "number" || v.type === "integer" ? 1 : k === "service" ? "checkout" : k === "expression" ? "6 * 7" : "");
  return JSON.stringify(o, null, 2);
}

function TestTool({ tool, onClose }: { tool: Tool; onClose: () => void }) {
  const [args, setArgs] = useState(sample(tool.input_schema));
  const run = useAction(() => post<ExecResult>(`/tools/${tool.id}/execute`, { arguments: JSON.parse(args) }), { invalidate: [["tool-execs"], ["tools"]] });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()} title={`Invoke ${tool.qualified_name}`} description="Direct invocation goes through the same policy engine, timeout and audit path as agent calls.">
      <div className="space-y-3">
        <Editor language="json" value={args} onChange={setArgs} height={140} ariaLabel="Tool arguments" />
        <Button onClick={() => run.mutate(undefined)} loading={run.isPending}><Play /> Execute</Button>
        {run.data && (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-1.5">{run.data.policy.checks.map((c) => <Badge key={c.rule} variant={c.passed ? "success" : "danger"} title={c.reason}>{c.rule}</Badge>)}</div>
            {run.data.policy.allowed ? <>
              <div className="flex gap-2 text-xs"><StatusBadge status={run.data.result.ok ? "success" : "error"} /> {fmt.ms(run.data.result.latency_ms)}
                {!!run.data.result.injection_flags.length && <Badge variant="danger">prompt-injection patterns flagged ({run.data.result.injection_flags.length})</Badge>}</div>
              <JsonView value={run.data.result.content || run.data.result.error} />
            </> : <div className="rounded-md bg-danger/10 p-2 text-xs text-danger">Denied by policy: {run.data.policy.reason}</div>}
          </div>
        )}
      </div>
    </Dialog>
  );
}

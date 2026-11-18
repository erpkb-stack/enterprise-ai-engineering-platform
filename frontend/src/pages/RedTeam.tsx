import { Play, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Empty, JsonView, Loading, PageHeader, StatCard, StatusBadge } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/form";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useAction, useApi, type Page } from "@/hooks/api";
import { fmt } from "@/lib";
import { post } from "@/services/api";
import { useAuth } from "@/stores/auth";

type Scenario = { id: string; category: string; title: string; tests: string; description: string };
type RtResult = { scenario_id: string; category: string; title: string; tests: string; outcome: string; reason: string; evidence: Record<string, unknown>; latency_ms: number };
type RtRun = { id: string; status: string; created_at: string; config: { provider: string; model: string };
  summary: { defense_rate: number | null; by_category: Record<string, Record<string, number>>; results: RtResult[]; caveat: string | null; sandbox: boolean } };

export default function RedTeamPage() {
  const can = useAuth((s) => s.can);
  const scenarios = useApi<Scenario[]>(["rt-scenarios"], "/redteam/scenarios");
  const runs = useApi<Page<RtRun>>(["rt-runs"], "/redteam/runs?limit=20", { refetchInterval: (d) => ((d as Page<RtRun> | undefined)?.items.some((r) => ["queued", "running"].includes(r.status)) ? 1500 : false) });
  const [provider, setProvider] = useState("mock");
  const [sel, setSel] = useState<string | null>(null);
  const run = useAction(() => post<RtRun>("/redteam/run", { provider, model: provider === "ollama" ? "llama3.2:3b" : "mock-reasoner-1" }),
    { invalidate: [["rt-runs"]], success: "Red-team suite started (sandboxed)", onSuccess: (r) => setSel(r.id) });
  useEffect(() => { if (!sel && runs.data?.items[0]) setSel(runs.data.items[0].id); }, [runs.data, sel]);
  const cur = runs.data?.items.find((r) => r.id === sel);
  return (
    <>
      <PageHeader icon={ShieldAlert} title="Red Team Lab" description="Adversarial suites executed in a sandbox: canary secrets instead of real ones, in-memory poisoned content, destructive tools exercised only through the policy engine."
        actions={can("redteam:run") && <><Select aria-label="Target model" value={provider} onChange={(e) => setProvider(e.target.value)} className="w-44"><option value="mock">mock (harness check)</option><option value="ollama">ollama · llama3.2:3b</option></Select>
          <Button onClick={() => run.mutate(undefined)} loading={run.isPending}><Play /> Run suite</Button></>} />
      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <div className="space-y-4">
          {!cur ? <Empty title="No red-team runs yet" description="Run the suite to probe prompt injection, jailbreaks, data leakage, tool misuse, tenant isolation and autonomy bounds." /> : (
            <>
              <div className="flex flex-wrap items-center gap-2"><StatusBadge status={cur.status} /><Badge variant="muted">{cur.config.provider}/{cur.config.model}</Badge>
                <span className="text-xs text-muted-foreground">{fmt.ago(cur.created_at)}</span>
                <Select aria-label="Run" className="ml-auto h-8 w-56" value={cur.id} onChange={(e) => setSel(e.target.value)}>{runs.data?.items.map((r) => <option key={r.id} value={r.id}>{r.config.provider} · {fmt.ago(r.created_at)} · {r.status}</option>)}</Select></div>
              {["queued", "running"].includes(cur.status) ? <Loading label="Running adversarial scenarios in sandbox…" /> : cur.summary?.results && <>
                {cur.summary.caveat && <p className="rounded-md border border-warning/40 bg-warning/5 p-2 text-xs">{cur.summary.caveat}</p>}
                <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                  <StatCard label="Defense rate" value={fmt.pct(cur.summary.defense_rate)} tone={(cur.summary.defense_rate ?? 0) < 1 ? "warning" : "success"} />
                  <StatCard label="Defended" value={cur.summary.results.filter((r) => r.outcome === "defended").length} />
                  <StatCard label="Vulnerable" value={cur.summary.results.filter((r) => r.outcome === "vulnerable").length} tone="danger" />
                  <StatCard label="Sandbox" value={cur.summary.sandbox ? "yes" : "no"} hint="canary secrets only" />
                </div>
                <Card><CardContent className="p-0"><Table><THead><TR><TH>Scenario</TH><TH>Tests</TH><TH>Outcome</TH><TH>Reason</TH><TH>Latency</TH></TR></THead>
                  <TBody>{cur.summary.results.map((r) => (
                    <TR key={r.scenario_id}><TD><div className="font-medium">{r.title}</div><div className="text-[11px] text-muted-foreground">{r.category.replaceAll("_", " ")}</div>
                      <details><summary className="cursor-pointer text-[11px] text-muted-foreground">evidence</summary><JsonView value={r.evidence} className="max-h-48" /></details></TD>
                      <TD><Badge variant={r.tests === "platform_control" ? "brand" : "outline"}>{r.tests === "platform_control" ? "platform control" : "model behaviour"}</Badge></TD>
                      <TD><StatusBadge status={r.outcome} /></TD><TD className="max-w-sm text-xs">{r.reason}</TD><TD>{fmt.ms(r.latency_ms)}</TD></TR>))}</TBody></Table></CardContent></Card>
              </>}
            </>
          )}
        </div>
        <Card className="h-fit"><CardHeader><CardTitle>Scenario library</CardTitle><CardDescription>{scenarios.data?.length ?? 0} configurable scenarios</CardDescription></CardHeader>
          <CardContent className="space-y-2">{scenarios.data?.map((s) => (
            <div key={s.id} className="rounded-md border p-2 text-xs"><div className="flex items-center justify-between gap-2"><span className="font-medium">{s.title}</span>
              <Badge variant={s.tests === "platform_control" ? "brand" : "outline"}>{s.tests === "platform_control" ? "control" : "model"}</Badge></div>
              <p className="mt-0.5 text-muted-foreground">{s.description}</p></div>))}</CardContent></Card>
      </div>
    </>
  );
}

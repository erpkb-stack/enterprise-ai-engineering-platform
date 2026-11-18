import { FlaskConical, Play, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Empty, ErrorState, Loading, MockBadge, PageHeader, ScoreBar, StatCard, StatusBadge } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { Switch } from "@/components/ui/switch";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAction, useApi, type Page } from "@/hooks/api";
import { fmt } from "@/lib";
import { post } from "@/services/api";
import { useAuth } from "@/stores/auth";
import type { Agent } from "@/types";

type Dataset = { id: string; name: string; description: string; kind: string; case_count: number; is_demo: boolean };
type Run = { id: string; name: string; status: string; target_type: string; target_id: string | null; target_version: number | null; created_at: string; finished_at: string | null;
  config: Record<string, unknown>; error: string | null; baseline_run_id: string | null;
  summary: { cases?: number; passed?: number; pass_rate?: number; metrics?: Record<string, { mean: number; n: number }>; latency?: { p50_ms: number; p95_ms: number };
    total_cost_usd?: number; total_tokens?: number; is_mock?: boolean; llm_judge?: { mean: number; n: number; limitations: string } | null;
    comparison?: { baseline_run_id: string; regressions: string[]; fixes: string[]; metric_deltas: Record<string, number>; pass_rate_delta: number } } };
type Result = { id: string; case_id: string; output: string; scores: Record<string, number | null>; passed: boolean; latency_ms: number;
  details: { failures: string[]; llm_judge?: { score: number; reasoning: string } }; case: { input: string; reference_answer: string | null; expected_citations: string[] } };

const QUALITY = ["reference_f1", "completeness", "groundedness", "citation_precision", "citation_correctness", "retrieval_relevance", "instruction_adherence", "hallucination_rate", "toxicity", "data_leakage"];

export default function EvaluationsPage() {
  const runs = useApi<Page<Run>>(["eval-runs"], "/evaluations/runs?limit=50", { refetchInterval: (d) => ((d as Page<Run> | undefined)?.items.some((r) => ["queued", "running"].includes(r.status)) ? 2000 : 15_000) });
  const [sel, setSel] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const can = useAuth((s) => s.can);
  useEffect(() => { if (!sel && runs.data?.items[0]) setSel(runs.data.items[0].id); }, [runs.data, sel]);
  return (
    <>
      <PageHeader icon={FlaskConical} title="AI Evaluation Lab" description="Golden datasets, deterministic scorers (accuracy, groundedness, citations, adherence, safety, latency, cost), optional LLM-as-judge, regression comparison against a baseline."
        actions={can("evaluations:run") && <Button onClick={() => setOpen(true)}><Play /> Run evaluation</Button>} />
      <Tabs defaultValue="runs">
        <TabsList><TabsTrigger value="runs">Runs & reports</TabsTrigger><TabsTrigger value="datasets">Datasets</TabsTrigger></TabsList>
        <TabsContent value="runs">
          <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
            <Card className="h-fit p-2">{runs.isLoading ? <Loading /> : !runs.data?.items.length ? <Empty title="No runs yet" /> : runs.data.items.map((r) => (
              <button key={r.id} onClick={() => setSel(r.id)} className={`block w-full rounded-md px-3 py-2 text-left text-xs hover:bg-muted ${sel === r.id ? "bg-muted" : ""}`}>
                <div className="flex items-center justify-between gap-2"><span className="truncate font-medium">{r.name}</span><StatusBadge status={r.status} /></div>
                <div className="text-muted-foreground">{r.target_type}{r.target_version ? ` v${r.target_version}` : ""} · pass {fmt.pct(r.summary?.pass_rate, 0)} · {fmt.ago(r.created_at)}</div>
              </button>))}</Card>
            {sel ? <RunReport id={sel} onCompare={setSel} /> : <Empty title="Select a run" />}
          </div>
        </TabsContent>
        <TabsContent value="datasets"><Datasets /></TabsContent>
      </Tabs>
      <RunDialog open={open} onOpenChange={setOpen} onStarted={setSel} />
    </>
  );
}

function RunReport({ id, onCompare }: { id: string; onCompare: (id: string) => void }) {
  const q = useApi<{ run: Run; results: Result[]; thresholds: Record<string, number>; judge_limitations: string }>(["eval-run", id], `/evaluations/${id}/results`,
    { refetchInterval: (d) => (["queued", "running"].includes((d as { run: Run } | undefined)?.run.status ?? "") ? 1500 : false) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} />;
  const { run, results, thresholds } = q.data!;
  const s = run.summary ?? {};
  const chart = QUALITY.filter((k) => s.metrics?.[k]).map((k) => ({ metric: k.replaceAll("_", " "), mean: s.metrics![k].mean }));
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2"><h2 className="text-base font-semibold">{run.name}</h2><StatusBadge status={run.status} /><MockBadge mock={s.is_mock} />
        {run.error && <span className="text-xs text-danger">{run.error}</span>}</div>
      {["queued", "running"].includes(run.status) && <Loading label={`Evaluating… ${results.length} case(s) scored`} />}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatCard label="Pass rate" value={fmt.pct(s.pass_rate)} hint={`${s.passed ?? 0}/${s.cases ?? 0} cases`} tone={(s.pass_rate ?? 1) < 0.7 ? "warning" : "success"} />
        <StatCard label="Groundedness" value={fmt.pct(s.metrics?.groundedness?.mean)} />
        <StatCard label="Hallucination rate" value={fmt.pct(s.metrics?.hallucination_rate?.mean)} />
        <StatCard label="Latency p50 / p95" value={fmt.ms(s.latency?.p50_ms)} hint={`p95 ${fmt.ms(s.latency?.p95_ms)}`} />
        <StatCard label="Cost / tokens" value={fmt.usd(s.total_cost_usd)} hint={`${fmt.num(s.total_tokens)} tokens`} />
      </div>
      {s.comparison && (
        <Card><CardHeader><CardTitle>Regression vs baseline</CardTitle><CardDescription>
          <button className="underline" onClick={() => onCompare(s.comparison!.baseline_run_id)}>baseline run</button> · pass-rate Δ {(s.comparison.pass_rate_delta * 100).toFixed(1)} pts</CardDescription></CardHeader>
          <CardContent className="flex flex-wrap gap-2 text-xs">
            <Badge variant={s.comparison.regressions.length ? "danger" : "success"}>{s.comparison.regressions.length} regressions</Badge><Badge variant="info">{s.comparison.fixes.length} fixes</Badge>
            {Object.entries(s.comparison.metric_deltas).map(([k, v]) => <Badge key={k} variant={v >= 0 ? "muted" : "warning"}>{k} {v >= 0 ? "+" : ""}{v.toFixed(3)}</Badge>)}
          </CardContent></Card>
      )}
      {!!chart.length && (
        <Card><CardHeader><CardTitle>Metric means</CardTitle><CardDescription>Deterministic scorers{s.llm_judge ? ` · LLM judge mean ${fmt.dec(s.llm_judge.mean)} (n=${s.llm_judge.n}) reported separately` : ""}</CardDescription></CardHeader>
          <CardContent className="h-56"><ResponsiveContainer><BarChart data={chart}><CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="metric" fontSize={10} stroke="var(--muted-foreground)" interval={0} angle={-15} textAnchor="end" height={50} /><YAxis domain={[0, 1]} fontSize={11} stroke="var(--muted-foreground)" />
            <Tooltip contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", fontSize: 12 }} /><Bar dataKey="mean" fill="var(--chart-1)" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></CardContent></Card>
      )}
      {s.llm_judge && <p className="rounded-md border border-warning/40 bg-warning/5 p-2 text-[11px] text-muted-foreground"><b>LLM-as-judge limitations:</b> {s.llm_judge.limitations}</p>}
      <Card><CardHeader><CardTitle>Per-case results</CardTitle><CardDescription>Thresholds: {Object.entries(thresholds).map(([k, v]) => `${k} ≥ ${v}`).join(", ")}</CardDescription></CardHeader>
        <CardContent className="p-0"><Table><THead><TR><TH>Case</TH><TH>Result</TH><TH>Accuracy (F1)</TH><TH>Grounded</TH><TH>Citations</TH><TH>Latency</TH></TR></THead>
          <TBody>{results.map((r) => (
            <TR key={r.id}><TD className="max-w-md"><div className="font-medium">{r.case.input}</div>
              <details><summary className="cursor-pointer text-[11px] text-muted-foreground">output & failures</summary><p className="mt-1 whitespace-pre-wrap text-xs">{r.output}</p>
                {r.details.failures.map((f) => <Badge key={f} variant="danger" className="mr-1 mt-1">{f}</Badge>)}
                {r.details.llm_judge && <p className="mt-1 text-[11px] text-muted-foreground">judge {fmt.dec(r.details.llm_judge.score)}: {r.details.llm_judge.reasoning}</p>}</details></TD>
              <TD>{r.passed ? <Badge variant="success">pass</Badge> : <Badge variant="danger">fail</Badge>}</TD>
              <TD><ScoreBar value={r.scores.reference_f1} /></TD><TD><ScoreBar value={r.scores.groundedness} /></TD><TD><ScoreBar value={r.scores.citation_precision} /></TD>
              <TD>{fmt.ms(r.latency_ms)}</TD></TR>))}</TBody></Table></CardContent></Card>
    </div>
  );
}

function RunDialog({ open, onOpenChange, onStarted }: { open: boolean; onOpenChange: (v: boolean) => void; onStarted: (id: string) => void }) {
  const ds = useApi<Dataset[]>(["datasets"], open ? "/evaluations/datasets" : null);
  const agents = useApi<Page<Agent>>(["agents", ""], open ? "/agents?limit=100" : null);
  const prompts = useApi<Page<{ id: string; name: string }>>(["prompts"], open ? "/prompts?limit=100" : null);
  const runs = useApi<Page<Run>>(["eval-runs"], open ? "/evaluations/runs?limit=50" : null);
  const [f, setF] = useState({ dataset_id: "", target_type: "rag", target_id: "", provider: "mock", model: "mock-reasoner-1", strategy: "hybrid_rerank", llm_judge: false, baseline_run_id: "", name: "" });
  useEffect(() => { if (!f.dataset_id && ds.data?.[0]) setF((s) => ({ ...s, dataset_id: ds.data!.find((d) => d.kind === "golden")?.id ?? ds.data![0].id })); }, [ds.data, f.dataset_id]);
  const start = useAction(() => post<Run>("/evaluations/run", { dataset_id: f.dataset_id, target_type: f.target_type, target_id: f.target_id || null, name: f.name,
    baseline_run_id: f.baseline_run_id || null, config: { provider: f.provider, model: f.model, strategy: f.strategy, use_rag: true, llm_judge: f.llm_judge } }),
    { invalidate: [["eval-runs"]], success: "Evaluation started", onSuccess: (r) => { onStarted(r.id); onOpenChange(false); } });
  const targets = f.target_type === "agent" ? agents.data?.items.map((a) => ({ id: a.id, name: a.name })) : f.target_type === "prompt" ? prompts.data?.items : [];
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Run evaluation" description="Runs in a background job; results stream in as cases complete.">
      <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); start.mutate(undefined); }}>
        <Field label="Dataset"><Select value={f.dataset_id} onChange={(e) => setF({ ...f, dataset_id: e.target.value })}>{ds.data?.filter((d) => d.kind === "golden").map((d) => <option key={d.id} value={d.id}>{d.name} ({d.case_count})</option>)}</Select></Field>
        <Field label="Target"><Select value={f.target_type} onChange={(e) => setF({ ...f, target_type: e.target.value, target_id: "" })}><option value="rag">RAG pipeline</option><option value="prompt">Prompt (latest version)</option><option value="agent">Agent</option></Select></Field>
        {f.target_type !== "rag" && <Field label={f.target_type === "agent" ? "Agent" : "Prompt"} className="sm:col-span-2"><Select required value={f.target_id} onChange={(e) => setF({ ...f, target_id: e.target.value })}><option value="">Select…</option>{targets?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</Select></Field>}
        <Field label="Provider"><Select value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value, model: e.target.value === "ollama" ? "llama3.2:3b" : "mock-reasoner-1" })}><option>mock</option><option>ollama</option></Select></Field>
        <Field label="Retrieval strategy"><Select value={f.strategy} onChange={(e) => setF({ ...f, strategy: e.target.value })}>{["semantic", "keyword", "hybrid", "hybrid_rerank"].map((s) => <option key={s}>{s}</option>)}</Select></Field>
        <Field label="Baseline run (regression)" className="sm:col-span-2"><Select value={f.baseline_run_id} onChange={(e) => setF({ ...f, baseline_run_id: e.target.value })}><option value="">None</option>{runs.data?.items.filter((r) => r.status === "completed").map((r) => <option key={r.id} value={r.id}>{r.name} · {fmt.ago(r.created_at)}</option>)}</Select></Field>
        <Field label="Run name"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="optional" /></Field>
        <label className="flex items-center gap-2 self-end text-xs"><Switch checked={f.llm_judge} onCheckedChange={(v) => setF({ ...f, llm_judge: v })} label="LLM judge" /> Add LLM-as-judge (reported separately)</label>
        <div className="flex justify-end gap-2 sm:col-span-2"><Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button type="submit" loading={start.isPending}>Start</Button></div>
      </form>
    </Dialog>
  );
}

function Datasets() {
  const can = useAuth((s) => s.can);
  const ds = useApi<Dataset[]>(["datasets"], "/evaluations/datasets");
  const [sel, setSel] = useState<string | null>(null);
  const detail = useApi<Dataset & { cases: { id: string; input: string; reference_answer: string | null; expected_citations: string[]; must_contain: string[] }[] }>(["dataset", sel], sel ? `/evaluations/datasets/${sel}` : null);
  const [c, setC] = useState({ input: "", reference_answer: "", expected_citations: "", must_contain: "" });
  const add = useAction(() => post(`/evaluations/datasets/${sel}/cases`, { input: c.input, reference_answer: c.reference_answer || null,
    expected_citations: c.expected_citations.split(",").map((s) => s.trim()).filter(Boolean), must_contain: c.must_contain.split(",").map((s) => s.trim()).filter(Boolean) }),
    { invalidate: [["dataset", sel], ["datasets"]], success: "Case added", onSuccess: () => setC({ input: "", reference_answer: "", expected_citations: "", must_contain: "" }) });
  const [newName, setNewName] = useState("");
  const create = useAction(() => post<Dataset>("/evaluations/datasets", { name: newName, kind: "golden" }), { invalidate: [["datasets"]], success: "Dataset created", onSuccess: (d) => { setSel(d.id); setNewName(""); } });
  useEffect(() => { if (!sel && ds.data?.[0]) setSel(ds.data[0].id); }, [ds.data, sel]);
  return (
    <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
      <Card className="h-fit space-y-1 p-2">{ds.data?.map((d) => (
        <button key={d.id} onClick={() => setSel(d.id)} className={`block w-full rounded-md px-3 py-2 text-left text-xs hover:bg-muted ${sel === d.id ? "bg-muted" : ""}`}>
          <div className="font-medium">{d.name}</div><div className="text-muted-foreground">{d.kind} · {d.case_count} cases</div></button>))}
        {can("evaluations:run") && <div className="flex gap-1 p-1"><Input placeholder="New dataset" value={newName} onChange={(e) => setNewName(e.target.value)} /><Button size="icon" aria-label="Create dataset" onClick={() => newName && create.mutate(undefined)}><Plus /></Button></div>}
      </Card>
      {detail.data && <Card><CardHeader><CardTitle>{detail.data.name}</CardTitle><CardDescription>{detail.data.description}</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          <Table><THead><TR><TH>Input</TH><TH>Reference answer</TH><TH>Expected sources</TH><TH>Must contain</TH></TR></THead>
            <TBody>{detail.data.cases.map((x) => <TR key={x.id}><TD>{x.input}</TD><TD className="text-xs text-muted-foreground">{x.reference_answer}</TD>
              <TD className="text-xs">{x.expected_citations.join(", ")}</TD><TD className="text-xs">{x.must_contain.join(", ")}</TD></TR>)}</TBody></Table>
          {can("evaluations:run") && detail.data.kind === "golden" && <form className="grid gap-2 md:grid-cols-2" onSubmit={(e) => { e.preventDefault(); add.mutate(undefined); }}>
            <Field label="Input" className="md:col-span-2"><Input required value={c.input} onChange={(e) => setC({ ...c, input: e.target.value })} /></Field>
            <Field label="Reference answer" className="md:col-span-2"><Textarea className="font-sans" value={c.reference_answer} onChange={(e) => setC({ ...c, reference_answer: e.target.value })} /></Field>
            <Field label="Expected source files (comma)"><Input value={c.expected_citations} onChange={(e) => setC({ ...c, expected_citations: e.target.value })} /></Field>
            <Field label="Must contain (comma)"><Input value={c.must_contain} onChange={(e) => setC({ ...c, must_contain: e.target.value })} /></Field>
            <Button type="submit" className="w-fit" loading={add.isPending}><Plus /> Add case</Button></form>}
        </CardContent></Card>}
    </div>
  );
}

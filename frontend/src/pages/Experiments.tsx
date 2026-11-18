import { Play, Plus, Split } from "lucide-react";
import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ErrorBar, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Empty, Loading, MockBadge, PageHeader, StatusBadge } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/form";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useAction, useApi, type Page } from "@/hooks/api";
import { cn, fmt } from "@/lib";
import { post } from "@/services/api";
import { useAuth } from "@/stores/auth";

type Exp = { id: string; name: string; hypothesis: string; dimension: string; status: string; primary_metric: string; min_sample_size: number; dataset_id: string | null };
type CI = { n: number; mean: number | null; ci_low: number | null; ci_high: number | null; rate?: number | null };
type Results = { primary_metric: string; min_sample_size: number; total_observations: number; is_mock: boolean; guardrail: string;
  variants: { id: string; name: string; is_control: boolean; traffic_weight: number; n: number; metrics: Record<string, CI>; config: Record<string, unknown> }[];
  comparisons: { variant: string; control: string; metrics: Record<string, { diff: number | null; ci_low: number | null; ci_high: number | null; p_value: number | null;
    relative_change: number | null; significant: boolean; conclusion: string; message: string }> }[] };
const METRICS = ["quality_score", "groundedness", "reference_f1", "pass", "latency_ms", "cost_usd", "tokens"];

export default function ExperimentsPage() {
  const can = useAuth((s) => s.can);
  const list = useApi<Exp[]>(["experiments"], "/experiments");
  const [sel, setSel] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => { if (!sel && list.data?.[0]) setSel(list.data[0].id); }, [list.data, sel]);
  return (
    <>
      <PageHeader icon={Split} title="A/B Experimentation" description="Compare prompts, models, retrieval strategies and agent configs with deterministic traffic allocation, confidence intervals and a minimum-sample guard."
        actions={can("experiments:write") && <Button onClick={() => setOpen(true)}><Plus /> New experiment</Button>} />
      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <Card className="h-fit p-2">{list.isLoading ? <Loading /> : !list.data?.length ? <Empty title="No experiments" /> : list.data.map((e) => (
          <button key={e.id} onClick={() => setSel(e.id)} className={cn("block w-full rounded-md px-3 py-2 text-left text-xs hover:bg-muted", sel === e.id && "bg-muted")}>
            <div className="font-medium">{e.name}</div><div className="text-muted-foreground">{e.dimension} · {e.status}</div></button>))}</Card>
        {sel ? <ExperimentView id={sel} /> : <Empty title="Select an experiment" />}
      </div>
      <CreateExperiment open={open} onOpenChange={setOpen} onCreated={setSel} />
    </>
  );
}

function ExperimentView({ id }: { id: string }) {
  const can = useAuth((s) => s.can);
  const e = useApi<Exp & { variants: { name: string; is_control: boolean; traffic_weight: number; config: Record<string, unknown> }[] }>(["experiment", id], `/experiments/${id}`);
  const r = useApi<Results>(["experiment-results", id], `/experiments/${id}/results`);
  const [mode, setMode] = useState("paired");
  const [repeats, setRepeats] = useState(1);
  const [metric, setMetric] = useState("quality_score");
  const run = useAction(() => post(`/experiments/${id}/run`, { mode, repeats }), { invalidate: [["experiment-results", id], ["experiment", id], ["experiments"]], success: "Experiment run complete" });
  if (e.isLoading || r.isLoading) return <Loading />;
  const x = e.data!;
  const res = r.data!;
  const chart = res.variants.map((v) => {
    const m = v.metrics[metric];
    const mean = metric === "pass" ? m.rate ?? 0 : m.mean ?? 0;
    return { name: v.name, mean, err: m.ci_low != null && m.ci_high != null ? [mean - m.ci_low, m.ci_high - mean] : [0, 0] };
  });
  return (
    <div className="space-y-4">
      <Card><CardHeader><CardTitle className="flex items-center gap-2">{x.name}<StatusBadge status={x.status} /><MockBadge mock={res.total_observations ? res.is_mock : null} /></CardTitle>
        <CardDescription>{x.hypothesis}</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2 text-xs">{x.variants.map((v) => <Badge key={v.name} variant={v.is_control ? "brand" : "outline"}>{v.name}{v.is_control && " (control)"} · {v.traffic_weight}%</Badge>)}
            <Badge variant="muted">primary: {x.primary_metric}</Badge><Badge variant="muted">min n per arm: {x.min_sample_size}</Badge></div>
          {can("experiments:write") && <div className="flex flex-wrap items-end gap-2">
            <Field label="Mode"><Select value={mode} onChange={(ev) => setMode(ev.target.value)}><option value="paired">Paired (all variants per case)</option><option value="split">Split (traffic allocation)</option></Select></Field>
            <Field label="Repeats"><Input type="number" min={1} max={10} className="w-24" value={repeats} onChange={(ev) => setRepeats(Number(ev.target.value))} /></Field>
            <Button onClick={() => run.mutate(undefined)} loading={run.isPending} disabled={!x.dataset_id}><Play /> Run over dataset</Button></div>}
        </CardContent></Card>
      <p className="rounded-md border border-info/30 bg-info/5 p-2 text-xs">{res.guardrail} Observations so far: {res.total_observations}.</p>
      {res.total_observations ? <>
        <Card><CardHeader className="flex-row items-center justify-between"><CardTitle>Variant comparison (95% CI)</CardTitle>
          <Select aria-label="Metric" className="h-8 w-44" value={metric} onChange={(ev) => setMetric(ev.target.value)}>{METRICS.map((m) => <option key={m}>{m}</option>)}</Select></CardHeader>
          <CardContent className="h-56"><ResponsiveContainer><BarChart data={chart}><CartesianGrid strokeDasharray="3 3" stroke="var(--border)" /><XAxis dataKey="name" fontSize={11} stroke="var(--muted-foreground)" />
            <YAxis fontSize={11} stroke="var(--muted-foreground)" /><Tooltip contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", fontSize: 12 }} />
            <Bar dataKey="mean" fill="var(--chart-1)" radius={[4, 4, 0, 0]}><ErrorBar dataKey="err" width={6} stroke="var(--foreground)" /></Bar></BarChart></ResponsiveContainer></CardContent></Card>
        <Card><CardContent className="p-0"><Table><THead><TR><TH>Variant</TH><TH>n</TH>{METRICS.map((m) => <TH key={m}>{m}</TH>)}</TR></THead>
          <TBody>{res.variants.map((v) => <TR key={v.id}><TD className="font-medium">{v.name}{v.is_control && <Badge variant="brand" className="ml-1">control</Badge>}</TD><TD>{v.n}</TD>
            {METRICS.map((m) => { const c = v.metrics[m]; const val = m === "pass" ? c.rate : c.mean;
              return <TD key={m} className="whitespace-nowrap text-xs tabular-nums">{m === "latency_ms" ? fmt.ms(val) : fmt.dec(val, 3)}<div className="text-[10px] text-muted-foreground">{c.ci_low != null ? `[${fmt.dec(c.ci_low, 3)}, ${fmt.dec(c.ci_high, 3)}]` : "CI n/a"}</div></TD>; })}</TR>)}</TBody></Table></CardContent></Card>
        {res.comparisons.map((c) => (
          <Card key={c.variant}><CardHeader><CardTitle>{c.variant} vs {c.control}</CardTitle></CardHeader><CardContent className="p-0">
            <Table><THead><TR><TH>Metric</TH><TH>Δ (treatment − control)</TH><TH>95% CI</TH><TH>p</TH><TH>Conclusion</TH></TR></THead>
              <TBody>{Object.entries(c.metrics).map(([k, m]) => <TR key={k}><TD>{k}</TD><TD className="tabular-nums">{fmt.dec(m.diff, 4)}{m.relative_change != null && <span className="text-muted-foreground"> ({(m.relative_change * 100).toFixed(1)}%)</span>}</TD>
                <TD className="tabular-nums text-xs">{m.ci_low != null ? `[${fmt.dec(m.ci_low, 4)}, ${fmt.dec(m.ci_high, 4)}]` : "—"}</TD><TD className="tabular-nums">{fmt.dec(m.p_value, 4)}</TD>
                <TD><Badge variant={m.significant ? (m.conclusion === "treatment_better" ? "success" : "danger") : m.conclusion === "insufficient_sample" ? "warning" : "muted"} title={m.message}>{m.conclusion.replaceAll("_", " ")}</Badge></TD></TR>)}</TBody></Table>
          </CardContent></Card>))}
      </> : <Empty title="No observations yet" description="Run the experiment over its dataset to collect measurements." />}
    </div>
  );
}

function CreateExperiment({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (v: boolean) => void; onCreated: (id: string) => void }) {
  const ds = useApi<{ id: string; name: string; kind: string }[]>(["datasets"], open ? "/evaluations/datasets" : null);
  const prompts = useApi<Page<{ id: string; name: string; current_version: number }>>(["prompts"], open ? "/prompts?limit=100" : null);
  const [f, setF] = useState({ name: "", hypothesis: "", dimension: "retrieval", dataset_id: "", min_sample_size: 30, a: "hybrid", b: "hybrid_rerank", template_id: "" });
  const create = useAction(() => {
    const cfg = (val: string): Record<string, unknown> => f.dimension === "retrieval" ? { strategy: val } : f.dimension === "model" ? { template_id: f.template_id, version: 1, provider: val.split("/")[0], model: val.split("/").slice(1).join("/") }
      : { template_id: f.template_id, version: Number(val), use_rag: true };
    return post<{ id: string }>("/experiments", { name: f.name, hypothesis: f.hypothesis, dimension: f.dimension, dataset_id: f.dataset_id || null, min_sample_size: f.min_sample_size,
      variants: [{ name: "control", is_control: true, traffic_weight: 50, config: cfg(f.a) }, { name: "treatment", traffic_weight: 50, config: cfg(f.b) }] });
  }, { invalidate: [["experiments"]], success: "Experiment created", onSuccess: (r) => { onCreated(r.id); onOpenChange(false); } });
  const hint = f.dimension === "retrieval" ? "semantic | keyword | hybrid | hybrid_rerank" : f.dimension === "model" ? "provider/model e.g. mock/mock-reasoner-1, ollama/llama3.2:3b" : "prompt version number";
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="New experiment">
      <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined); }}>
        <Field label="Name"><Input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Dimension"><Select value={f.dimension} onChange={(e) => setF({ ...f, dimension: e.target.value, a: e.target.value === "prompt" ? "1" : e.target.value === "model" ? "mock/mock-reasoner-1" : "hybrid", b: e.target.value === "prompt" ? "2" : e.target.value === "model" ? "ollama/llama3.2:3b" : "hybrid_rerank" })}>
          <option value="retrieval">Retrieval strategy</option><option value="prompt">Prompt version</option><option value="model">Model provider</option></Select></Field>
        {f.dimension !== "retrieval" && <Field label="Prompt template" className="sm:col-span-2"><Select required value={f.template_id} onChange={(e) => setF({ ...f, template_id: e.target.value })}><option value="">Select…</option>{prompts.data?.items.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select></Field>}
        <Field label="Control" hint={hint}><Input value={f.a} onChange={(e) => setF({ ...f, a: e.target.value })} /></Field>
        <Field label="Treatment"><Input value={f.b} onChange={(e) => setF({ ...f, b: e.target.value })} /></Field>
        <Field label="Dataset"><Select value={f.dataset_id} onChange={(e) => setF({ ...f, dataset_id: e.target.value })}><option value="">None</option>{ds.data?.filter((d) => d.kind === "golden").map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</Select></Field>
        <Field label="Min sample per arm"><Input type="number" min={2} value={f.min_sample_size} onChange={(e) => setF({ ...f, min_sample_size: Number(e.target.value) })} /></Field>
        <Field label="Hypothesis" className="sm:col-span-2"><Input value={f.hypothesis} onChange={(e) => setF({ ...f, hypothesis: e.target.value })} /></Field>
        <div className="flex justify-end gap-2 sm:col-span-2"><Button type="submit" loading={create.isPending}>Create</Button></div>
      </form>
    </Dialog>
  );
}

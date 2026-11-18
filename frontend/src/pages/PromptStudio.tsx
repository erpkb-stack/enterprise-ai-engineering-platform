import { CheckCircle2, Columns2, Eye, FlaskConical, KeyRound, Plus, Save, Send, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Empty, ErrorState, Loading, MockBadge, PageHeader, ScoreBar, StatusBadge } from "@/components/common";
import Editor from "@/components/common/LazyEditor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/form";
import { Switch } from "@/components/ui/switch";
import { GroundingView } from "@/features/ExecutionView";
import { useAction, useApi, type Page } from "@/hooks/api";
import { cn, fmt } from "@/lib";
import { post } from "@/services/api";
import { useAuth } from "@/stores/auth";
import type { Grounding, Hit } from "@/types";

type Version = { id: string; version: number; system: string; developer: string; user_template: string; variables: string[];
  model_config: { provider?: string; model?: string; temperature?: number; max_tokens?: number }; output_schema: Record<string, unknown> | null;
  status: string; change_note: string; created_at: string; review_comment: string | null; execution_count: number };
type Template = { id: string; name: string; description: string; tags: string[]; current_version: number; approved_version: number | null; versions: Version[] };
type TestResult = { output: string; outputs: string[] | null; provider: string; model: string; is_mock: boolean; latency_ms: number; tokens_in: number; tokens_out: number;
  cost_usd: number; retrieved: Hit[]; messages: { role: string; content: string }[];
  checks: { grounding: Grounding | null; structured_valid: number | null; structured_error: string | null; instruction_adherence: number | null;
    consistency: number | null; context_relevance: number | null; reference_f1: number | null; leakage: { leaked: boolean; types: Record<string, number> } } };

export default function PromptStudio() {
  const can = useAuth((s) => s.can);
  const list = useApi<Page<{ id: string; name: string; description: string; current_version: number; approved_version: number | null; tags: string[] }>>(["prompts"], "/prompts?limit=100");
  const [sel, setSel] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  useEffect(() => { if (!sel && list.data?.items[0]) setSel(list.data.items[0].id); }, [list.data, sel]);
  return (
    <>
      <PageHeader icon={KeyRound} title="Prompt Engineering Studio" description="Versioned templates with system / developer / user separation, variables, token estimates, grounded testing, side-by-side comparison and an approval workflow."
        actions={can("prompts:write") && <Button onClick={() => setCreating(true)}><Plus /> New template</Button>} />
      <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
        <Card className="h-fit p-2">
          {list.isLoading ? <Loading /> : list.data?.items.map((t) => (
            <button key={t.id} onClick={() => setSel(t.id)} className={cn("block w-full rounded-md px-3 py-2 text-left hover:bg-muted", sel === t.id && "bg-muted")}>
              <div className="text-sm font-medium">{t.name}</div>
              <div className="text-[11px] text-muted-foreground">v{t.current_version}{t.approved_version ? ` · approved v${t.approved_version}` : " · no approved version"}</div>
            </button>))}
        </Card>
        {sel ? <TemplateView id={sel} /> : <Empty title="Select a template" />}
      </div>
      <CreateTemplate open={creating} onOpenChange={setCreating} onCreated={setSel} />
    </>
  );
}

function TemplateView({ id }: { id: string }) {
  const can = useAuth((s) => s.can);
  const q = useApi<Template>(["prompt", id], `/prompts/${id}`);
  const [ver, setVer] = useState<number | null>(null);
  const t = q.data;
  const v = useMemo(() => t?.versions.find((x) => x.version === (ver ?? t.current_version)), [t, ver]);
  const [draft, setDraft] = useState<Version | null>(null);
  useEffect(() => { if (v) setDraft(v); }, [v]);
  const [vars, setVars] = useState<Record<string, string>>({ question: "How long do customers have to request a refund?" });
  const [useRag, setUseRag] = useState(true);
  const [repeats, setRepeats] = useState(1);
  const [must, setMust] = useState("");
  const [cmp, setCmp] = useState<[number, number] | null>(null);

  const saveVersion = useAction(() => post<Version>(`/prompts/${id}/versions`, { system: draft!.system, developer: draft!.developer,
    user_template: draft!.user_template, model_config: draft!.model_config, output_schema: draft!.output_schema, change_note: "edited in studio" }),
    { invalidate: [["prompt", id], ["prompts"]], success: (x) => `Saved version ${x.version}`, onSuccess: (x) => setVer(x.version) });
  const preview = useAction(() => post<{ messages: { role: string; content: string }[]; token_estimate: { total: number; per_message: number[]; method: string } }>(`/prompts/${id}/preview`, { version: v!.version, variables: vars }));
  const test = useAction(() => post<TestResult>(`/prompts/${id}/test`, { version: v!.version, variables: vars, use_rag: useRag, repeats,
    must_contain: must.split(",").map((s) => s.trim()).filter(Boolean) }), { invalidate: [["prompt", id]] });
  const submit = useAction(() => post(`/prompts/${id}/versions/${v!.version}/submit`), { invalidate: [["prompt", id]], success: "Submitted for review" });
  const review = useAction((decision: string) => post(`/prompts/${id}/versions/${v!.version}/review`, { decision, comment: "" }), { invalidate: [["prompt", id], ["prompts"]], success: (r) => `Version ${(r as Version).status}` });

  if (q.isLoading || !t || !v || !draft) return <Loading />;
  if (q.error) return <ErrorState error={q.error} />;
  const dirty = draft.system !== v.system || draft.developer !== v.developer || draft.user_template !== v.user_template;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
          <div><CardTitle>{t.name}</CardTitle><CardDescription>{t.description}</CardDescription></div>
          <div className="flex flex-wrap items-center gap-2">
            <Select aria-label="Version" className="h-8 w-40" value={v.version} onChange={(e) => setVer(Number(e.target.value))}>
              {t.versions.map((x) => <option key={x.version} value={x.version}>v{x.version} · {x.status}</option>)}</Select>
            <StatusBadge status={v.status} />
            {t.versions.length > 1 && <Button size="sm" variant="outline" onClick={() => setCmp([t.versions[1].version, t.versions[0].version])}><Columns2 /> Compare</Button>}
            {can("prompts:write") && ["draft", "rejected"].includes(v.status) && <Button size="sm" variant="outline" onClick={() => submit.mutate(undefined)}><Send /> Submit for review</Button>}
            {can("prompts:approve") && v.status === "in_review" && <>
              <Button size="sm" onClick={() => review.mutate("approved")}><CheckCircle2 /> Approve</Button>
              <Button size="sm" variant="destructive" onClick={() => review.mutate("rejected")}><XCircle /> Reject</Button></>}
          </div>
        </CardHeader>
        <CardContent className="grid gap-3 lg:grid-cols-3">
          {(["system", "developer", "user_template"] as const).map((k) => (
            <div key={k}><div className="mb-1 flex items-center justify-between text-xs font-medium text-muted-foreground">{k === "user_template" ? "user" : k} message</div>
              <Editor value={draft[k]} onChange={(val) => setDraft({ ...draft, [k]: val })} height={180} ariaLabel={`${k} message`} /></div>))}
          <div className="flex flex-wrap items-center gap-2 lg:col-span-3">
            <span className="text-xs text-muted-foreground">Variables:</span>{v.variables.map((x) => <Badge key={x} variant="outline">{`{{ ${x} }}`}</Badge>)}
            <span className="text-xs text-muted-foreground">· model {v.model_config.provider}/{v.model_config.model} · {v.execution_count} executions</span>
            {v.output_schema && <Badge variant="info">structured output schema</Badge>}
            {dirty && can("prompts:write") && <Button size="sm" className="ml-auto" onClick={() => saveVersion.mutate(undefined)} loading={saveVersion.isPending}><Save /> Save as new version</Button>}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Test bench</CardTitle><CardDescription>Runs the selected version with grounding, citation, structure, adherence, consistency and leakage checks.</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 md:grid-cols-2">
            {v.variables.filter((x) => x !== "sources").map((name) => (
              <Field key={name} label={name}><Input value={vars[name] ?? ""} onChange={(e) => setVars({ ...vars, [name]: e.target.value })} /></Field>))}
            <Field label="Must contain (comma-separated)"><Input value={must} onChange={(e) => setMust(e.target.value)} placeholder="30 days" /></Field>
            <Field label="Repeats (consistency)"><Input type="number" min={1} max={5} value={repeats} onChange={(e) => setRepeats(Number(e.target.value))} /></Field>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-xs"><Switch checked={useRag} onCheckedChange={setUseRag} label="Use RAG" /> Retrieve sources from knowledge base</label>
            <Button variant="outline" onClick={() => preview.mutate(undefined)} loading={preview.isPending}><Eye /> Preview</Button>
            <Button onClick={() => test.mutate(undefined)} loading={test.isPending} disabled={!can("prompts:test")}><FlaskConical /> Run test</Button>
          </div>
          {preview.data && (
            <div className="space-y-1.5 rounded-lg border p-3">
              <div className="text-xs text-muted-foreground">≈ {preview.data.token_estimate.total} tokens ({preview.data.token_estimate.method})</div>
              {preview.data.messages.map((m, i) => <div key={i} className="text-xs"><Badge variant="muted">{m.role}</Badge> <span className="whitespace-pre-wrap">{m.content}</span></div>)}
            </div>
          )}
          {test.data && <TestResultView r={test.data} />}
        </CardContent>
      </Card>
      {cmp && <Compare id={id} versions={t.versions.map((x) => x.version)} initial={cmp} vars={vars} onClose={() => setCmp(null)} />}
    </div>
  );
}

function TestResultView({ r }: { r: TestResult }) {
  const c = r.checks;
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-xs"><MockBadge mock={r.is_mock} /><Badge variant="muted">{r.provider}/{r.model}</Badge>
          <span className="text-muted-foreground">{fmt.ms(r.latency_ms)} · {r.tokens_in}+{r.tokens_out} tok · {fmt.usd(r.cost_usd)}</span></div>
        <p className="whitespace-pre-wrap rounded-lg bg-muted p-3 text-sm">{r.output}</p>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div>Instruction adherence<ScoreBar value={c.instruction_adherence} /></div><div>Output consistency<ScoreBar value={c.consistency} /></div>
          <div>Context relevance<ScoreBar value={c.context_relevance} /></div><div>Structured output<ScoreBar value={c.structured_valid} />{c.structured_error && <span className="text-danger">{c.structured_error}</span>}</div>
        </div>
        <div className="text-xs">{c.leakage.leaked ? <Badge variant="danger">Sensitive data detected: {Object.keys(c.leakage.types).join(", ")}</Badge> : <Badge variant="success">No secrets / PII detected</Badge>}</div>
      </div>
      <div><div className="mb-2 text-xs font-medium">Grounding & citation verification</div><GroundingView g={c.grounding} passages={r.retrieved.map((h) => h.content)} files={r.retrieved.map((h) => h.filename)} /></div>
    </div>
  );
}

function Compare({ id, versions, initial, vars, onClose }: { id: string; versions: number[]; initial: [number, number]; vars: Record<string, string>; onClose: () => void }) {
  const [a, setA] = useState(initial[0]);
  const [b, setB] = useState(initial[1]);
  const run = useAction(() => post<{ a: TestResult & { version: number }; b: TestResult & { version: number } }>(`/prompts/${id}/compare`, { version_a: a, version_b: b, variables: vars, use_rag: true }));
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Side-by-side comparison" description="Both versions run on identical inputs and retrieved context." className="max-w-6xl">
      <div className="mb-3 flex items-center gap-2">
        <Select className="w-28" value={a} onChange={(e) => setA(Number(e.target.value))}>{versions.map((v) => <option key={v} value={v}>v{v}</option>)}</Select> vs
        <Select className="w-28" value={b} onChange={(e) => setB(Number(e.target.value))}>{versions.map((v) => <option key={v} value={v}>v{v}</option>)}</Select>
        <Button onClick={() => run.mutate(undefined)} loading={run.isPending}>Compare</Button>
      </div>
      {run.data && <div className="grid gap-4 lg:grid-cols-2">{(["a", "b"] as const).map((k) => (
        <div key={k} className="space-y-2 rounded-lg border p-3"><div className="font-medium">Version {run.data![k].version}</div><TestResultView r={run.data![k]} /></div>))}</div>}
    </Dialog>
  );
}

function CreateTemplate({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (v: boolean) => void; onCreated: (id: string) => void }) {
  const [name, setName] = useState("");
  const create = useAction(() => post<{ id: string }>("/prompts", { name, description: "", version: { system: "You are a precise assistant. Cite sources with [n].",
    user_template: "{{ sources }}\n\nQuestion: {{ question }}", model_config: { provider: "mock", model: "mock-reasoner-1" } } }),
    { invalidate: [["prompts"]], success: "Template created", onSuccess: (r) => { onCreated(r.id); onOpenChange(false); } });
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="New prompt template">
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined); }}>
        <Field label="Name"><Input required minLength={2} value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Button type="submit" loading={create.isPending}>Create</Button>
      </form>
    </Dialog>
  );
}

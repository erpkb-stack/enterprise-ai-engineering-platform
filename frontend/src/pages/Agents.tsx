import { Bot, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Empty, ErrorState, Loading, PageHeader, StatusBadge } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { useAction, useApi, type Page } from "@/hooks/api";
import { fmt } from "@/lib";
import { get, post } from "@/services/api";
import { useAuth } from "@/stores/auth";
import type { Agent, Execution, Tool } from "@/types";

export const AGENT_TYPES = ["research", "coding", "data_analysis", "planning", "security", "orchestrator", "custom"];

export default function AgentsPage() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const can = useAuth((s) => s.can);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState(false);
  const q = useApi<Page<Agent>>(["agents", filter], `/agents?limit=100${filter ? `&agent_type=${filter}` : ""}`);

  // deep link from dashboard: /agents?execution=<id>
  useEffect(() => {
    const ex = params.get("execution");
    if (ex) get<Execution>(`/executions/${ex}`).then((e) => nav(`/agents/${e.agent_id}?execution=${ex}`, { replace: true })).catch(() => undefined);
  }, [params, nav]);

  return (
    <>
      <PageHeader icon={Bot} title="Agent Management Studio" description="Create, version, configure and run agents with bounded autonomy and policy-checked tool access."
        actions={<>
          <Select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter by type" className="w-40">
            <option value="">All types</option>{AGENT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </Select>
          {can("agents:write") && <Button onClick={() => setOpen(true)}><Plus /> New agent</Button>}
        </>} />
      {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data!.items.length ? (
        <Empty title="No agents" description="Create your first agent to get started." />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {q.data!.items.map((a) => (
            <Card key={a.id} className="cursor-pointer p-4 transition hover:border-primary/50" onClick={() => nav(`/agents/${a.id}`)}
              role="link" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && nav(`/agents/${a.id}`)}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-medium">{a.name}</div>
                  <div className="mt-0.5 text-xs text-muted-foreground line-clamp-2">{a.description || "—"}</div>
                </div>
                <StatusBadge status={a.status} />
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5 text-[11px]">
                <Badge variant="brand">{a.agent_type}</Badge>
                <Badge variant="outline">v{a.current_version}</Badge>
                <Badge variant="muted">{a.config?.provider}/{a.config?.model}</Badge>
                <Badge variant="muted">≤{a.config?.max_steps} steps</Badge>
                {a.is_demo && <Badge variant="warning">DEMO</Badge>}
              </div>
              <div className="mt-2 truncate text-[11px] text-muted-foreground">
                {a.config?.collaborators?.length ? `delegates to ${a.config.collaborators.length} agents` : `${a.config?.tools?.length ?? 0} tools`} · updated {fmt.ago(a.updated_at)}
              </div>
            </Card>
          ))}
        </div>
      )}
      <CreateAgent open={open} onOpenChange={setOpen} />
    </>
  );
}

function CreateAgent({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const nav = useNavigate();
  const tools = useApi<Tool[]>(["tools"], open ? "/tools" : null);
  const [f, setF] = useState({ name: "", agent_type: "research", description: "", provider: "mock", model: "mock-reasoner-1",
    system_prompt: "Answer using knowledge_search results and cite every claim with [n].", max_steps: 5, tools: ["knowledge_search"] as string[] });
  const create = useAction((body: unknown) => post<Agent>("/agents", body), {
    invalidate: [["agents"]], success: "Agent created (draft)", onSuccess: (a) => { onOpenChange(false); nav(`/agents/${a.id}`); } });
  const toggle = (t: string) => setF((s) => ({ ...s, tools: s.tools.includes(t) ? s.tools.filter((x) => x !== t) : [...s.tools, t] }));
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Create agent" description="Agents start as drafts; activate them after review.">
      <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); create.mutate({ name: f.name, agent_type: f.agent_type, description: f.description,
        config: { system_prompt: f.system_prompt, provider: f.provider, model: f.model, max_steps: Number(f.max_steps), tools: f.tools } }); }}>
        <Field label="Name"><Input required minLength={2} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Type"><Select value={f.agent_type} onChange={(e) => setF({ ...f, agent_type: e.target.value })}>{AGENT_TYPES.map((t) => <option key={t}>{t}</option>)}</Select></Field>
        <Field label="Provider" hint="mock = deterministic, ollama = local model"><Select value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value, model: e.target.value === "ollama" ? "llama3.2:3b" : e.target.value === "mock" ? "mock-reasoner-1" : f.model })}>
          {["mock", "ollama", "openai", "anthropic", "gemini", "nvidia_nim"].map((p) => <option key={p}>{p}</option>)}</Select></Field>
        <Field label="Model"><Input value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} /></Field>
        <Field label="Description" className="sm:col-span-2"><Input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        <Field label="System instructions" className="sm:col-span-2"><Textarea rows={3} value={f.system_prompt} onChange={(e) => setF({ ...f, system_prompt: e.target.value })} /></Field>
        <Field label="Max steps" hint="Platform hard limit enforced server-side"><Input type="number" min={1} max={25} value={f.max_steps} onChange={(e) => setF({ ...f, max_steps: Number(e.target.value) })} /></Field>
        <div className="sm:col-span-2">
          <div className="mb-1.5 text-xs font-medium text-muted-foreground">Tool allowlist (only allowlisted registry tools can run)</div>
          <div className="flex max-h-36 flex-wrap gap-1.5 overflow-y-auto">
            {(tools.data ?? []).map((t) => {
              const name = t.server_name === "builtin" ? t.name : t.qualified_name;
              return <button type="button" key={t.id} onClick={() => toggle(name)} disabled={!t.is_enabled}
                className={`rounded-md border px-2 py-1 text-[11px] ${f.tools.includes(name) ? "border-primary bg-accent" : ""} disabled:opacity-40`}
                title={t.is_enabled ? t.description : "Not allowlisted in Tool Registry"}>{name}{t.risk_level === "high" && " ⚠"}</button>;
            })}
          </div>
        </div>
        <div className="flex justify-end gap-2 sm:col-span-2"><Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button type="submit" loading={create.isPending}>Create</Button></div>
      </form>
    </Dialog>
  );
}

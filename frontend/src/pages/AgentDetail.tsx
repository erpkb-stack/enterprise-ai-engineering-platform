import { addEdge, Background, Controls, type Connection, type Edge, Handle, type Node, Position, ReactFlow, useEdgesState, useNodesState } from "@xyflow/react";
import { ArrowLeft, Bot, Copy, Play, Save, Square, Wrench } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Empty, ErrorState, Loading, MockBadge, PageHeader, StatusBadge } from "@/components/common";
import Editor from "@/components/common/LazyEditor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { Switch } from "@/components/ui/switch";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ExecutionView } from "@/features/ExecutionView";
import { useAction, useApi, type Page } from "@/hooks/api";
import { fmt } from "@/lib";
import { get, post, put } from "@/services/api";
import { useAuth } from "@/stores/auth";
import type { Agent, AgentConfig, Execution, Tool } from "@/types";

export default function AgentDetail() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const can = useAuth((s) => s.can);
  const agent = useApi<Agent>(["agent", id], `/agents/${id}`);
  const [tab, setTab] = useState("execute");
  const lifecycle = useAction((status: string) => post<Agent>(`/agents/${id}/lifecycle`, { status }), { invalidate: [["agent", id], ["agents"]], success: (a) => `Agent is now ${a.status}` });
  const clone = useAction(() => post<Agent>(`/agents/${id}/clone`, { name: `${agent.data?.name} (copy ${Date.now() % 10000})` }),
    { invalidate: [["agents"]], success: "Cloned", onSuccess: (a) => nav(`/agents/${a.id}`) });

  if (agent.isLoading) return <Loading />;
  if (agent.error) return <ErrorState error={agent.error} />;
  const a = agent.data!;
  return (
    <>
      <Link to="/agents" className="mb-2 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"><ArrowLeft className="size-3" /> Agents</Link>
      <PageHeader icon={Bot} title={a.name} description={a.description}
        actions={<>
          <StatusBadge status={a.status} /><Badge variant="brand">{a.agent_type}</Badge><Badge variant="outline">v{a.current_version}</Badge>
          {can("agents:lifecycle") && a.status !== "active" && a.status !== "archived" && <Button size="sm" onClick={() => lifecycle.mutate("active")}>Activate</Button>}
          {can("agents:lifecycle") && a.status === "active" && <Button size="sm" variant="outline" onClick={() => lifecycle.mutate("inactive")}>Deactivate</Button>}
          {can("agents:lifecycle") && a.status !== "archived" && <Button size="sm" variant="outline" onClick={() => confirm("Archive this agent? Archived agents are read-only.") && lifecycle.mutate("archived")}>Archive</Button>}
          {can("agents:write") && <Button size="sm" variant="outline" onClick={() => clone.mutate(undefined)}><Copy /> Clone</Button>}
        </>} />
      <Tabs value={tab} onValueChange={(v) => { setTab(v); if (v !== "execute") setParams({}); }}>
        <TabsList><TabsTrigger value="execute">Execute</TabsTrigger><TabsTrigger value="builder">Visual builder</TabsTrigger>
          <TabsTrigger value="config">Configuration</TabsTrigger><TabsTrigger value="history">History</TabsTrigger><TabsTrigger value="versions">Versions</TabsTrigger></TabsList>
        <TabsContent value="execute"><ExecuteTab agent={a} initialExecution={params.get("execution")} /></TabsContent>
        <TabsContent value="builder"><Builder agent={a} /></TabsContent>
        <TabsContent value="config"><ConfigTab agent={a} /></TabsContent>
        <TabsContent value="history"><History agentId={a.id} onOpen={(ex) => { setParams({ execution: ex }); setTab("execute"); }} /></TabsContent>
        <TabsContent value="versions"><Versions agentId={a.id} /></TabsContent>
      </Tabs>
    </>
  );
}

function ExecuteTab({ agent, initialExecution }: { agent: Agent; initialExecution: string | null }) {
  const can = useAuth((s) => s.can);
  const [input, setInput] = useState(agent.agent_type === "data_analysis" ? "What is the error budget burn rate for a 99.9 SLO with error rate 0.0031?"
    : "How quickly must a Sev2 incident be acknowledged, and what is the checkout service status?");
  const [exId, setExId] = useState<string | null>(initialExecution);
  useEffect(() => setExId(initialExecution), [initialExecution]);
  const ex = useApi<Execution>(["execution", exId], exId ? `/executions/${exId}` : null, {
    refetchInterval: (d) => (d && ["queued", "running"].includes((d as Execution).status) ? 1000 : false) });
  const childId = ex.data?.steps.find((s) => s.type === "delegate")?.child_execution_id;
  const child = useApi<Execution>(["execution", childId], childId ? `/executions/${childId}` : null);
  const running = ex.data && ["queued", "running"].includes(ex.data.status);
  const run = useAction(() => post<Execution>(`/agents/${agent.id}/execute`, { input, mode: "async" }), { onSuccess: (r) => setExId(r.id) });
  const cancel = useAction(() => post(`/executions/${exId}/cancel`), { success: "Cancellation requested", invalidate: [["execution", exId]] });
  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="space-y-3 p-4">
          <Textarea aria-label="Agent input" rows={3} className="font-sans text-sm" value={input} onChange={(e) => setInput(e.target.value)} />
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => run.mutate(undefined)} loading={run.isPending || !!running} disabled={!can("agents:execute") || agent.status !== "active"}><Play /> Run agent</Button>
            {running && <Button variant="outline" onClick={() => cancel.mutate(undefined)}><Square /> Cancel</Button>}
            {agent.status !== "active" && <span className="text-xs text-warning">Activate the agent to execute it.</span>}
            <span className="ml-auto text-[11px] text-muted-foreground">Bounded: ≤{agent.config.max_steps} steps · {agent.config.total_timeout_s}s budget · {agent.config.retry.max_attempts} attempts/call</span>
          </div>
        </CardContent>
      </Card>
      {ex.isLoading && exId ? <Loading label="Loading execution…" /> : ex.data ? (
        running ? <Card><CardContent className="p-4"><Loading label={`Agent ${ex.data.status} — step ${ex.data.steps.length}`} /></CardContent></Card>
          : <ExecutionView ex={ex.data} child={child.data} />
      ) : <Empty title="No execution yet" description="Run the agent to see its trace, tool calls, policy decisions and verified citations." />}
    </div>
  );
}

const nodeStyle = "rounded-lg border bg-card px-3 py-2 text-xs shadow-sm";
function AgentNode({ data }: { data: { label: string; sub: string } }) {
  return <div className={`${nodeStyle} border-primary min-w-44`}><Handle type="target" position={Position.Left} /><div className="flex items-center gap-1.5 font-semibold"><Bot className="size-3.5" />{data.label}</div><div className="text-muted-foreground">{data.sub}</div><Handle type="source" position={Position.Right} /></div>;
}
function ToolNode({ data }: { data: { label: string; risk: string; enabled: boolean } }) {
  return <div className={`${nodeStyle} ${data.enabled ? "" : "opacity-50"}`}><Handle type="source" position={Position.Right} /><div className="flex items-center gap-1.5"><Wrench className="size-3" /><span className="font-mono">{data.label}</span></div><div className={data.risk === "high" ? "text-danger" : "text-muted-foreground"}>{data.risk} risk{data.enabled ? "" : " · not allowlisted"}</div></div>;
}
const nodeTypes = { agent: AgentNode, tool: ToolNode };

function Builder({ agent }: { agent: Agent }) {
  const can = useAuth((s) => s.can);
  const tools = useApi<Tool[]>(["tools"], "/tools");
  const agents = useApi<Page<Agent>>(["agents", ""], "/agents?limit=100");
  const isOrch = agent.agent_type === "orchestrator";
  const initial = useMemo(() => {
    const ns: Node[] = [{ id: "agent", type: "agent", position: { x: 480, y: 160 }, data: { label: agent.name, sub: `${agent.config.provider}/${agent.config.model}` } }];
    const es: Edge[] = [];
    if (isOrch) {
      (agents.data?.items ?? []).filter((x) => x.id !== agent.id).forEach((x, i) => {
        ns.push({ id: `a:${x.id}`, type: "agent", position: { x: 60, y: i * 80 }, data: { label: x.name, sub: x.agent_type } });
        if (agent.config.collaborators.includes(x.id)) es.push({ id: `e:${x.id}`, source: `a:${x.id}`, target: "agent", animated: true });
      });
    } else {
      (tools.data ?? []).forEach((t, i) => {
        const name = t.server_name === "builtin" ? t.name : t.qualified_name;
        ns.push({ id: `t:${name}`, type: "tool", position: { x: 40 + (i % 2) * 220, y: Math.floor(i / 2) * 70 }, data: { label: name, risk: t.risk_level, enabled: t.is_enabled } });
        if (agent.config.tools.includes(name)) es.push({ id: `e:${name}`, source: `t:${name}`, target: "agent", animated: true });
      });
    }
    return { ns, es };
  }, [agent, tools.data, agents.data, isOrch]);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  useEffect(() => { setNodes(initial.ns); setEdges(initial.es); }, [initial, setNodes, setEdges]);
  const save = useAction(() => {
    const linked = edges.filter((e) => e.target === "agent").map((e) => e.source);
    const cfg: AgentConfig = isOrch ? { ...agent.config, collaborators: linked.map((s) => s.slice(2)) } : { ...agent.config, tools: linked.map((s) => s.slice(2)) };
    return put<Agent>(`/agents/${agent.id}`, { config: cfg, change_note: "visual builder" });
  }, { invalidate: [["agent", agent.id]], success: (a) => `Saved as version ${a.current_version}` });
  const onConnect = (c: Connection) => c.target === "agent" && setEdges((e) => addEdge({ ...c, animated: true }, e));
  if (tools.isLoading || agents.isLoading) return <Loading />;
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <div><CardTitle>{isOrch ? "Multi-agent collaboration" : "Tool access"}</CardTitle>
          <CardDescription>Drag from a {isOrch ? "specialist agent" : "tool"} to the agent to grant it; select an edge and press Backspace to revoke. Saving creates a new immutable version.</CardDescription></div>
        <Button onClick={() => save.mutate(undefined)} loading={save.isPending} disabled={!can("agents:write")}><Save /> Save version</Button>
      </CardHeader>
      <CardContent><div className="h-[480px] rounded-lg border">
        <ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect} nodeTypes={nodeTypes} fitView deleteKeyCode={["Backspace", "Delete"]}>
          <Background /><Controls />
        </ReactFlow>
      </div></CardContent>
    </Card>
  );
}

function ConfigTab({ agent }: { agent: Agent }) {
  const can = useAuth((s) => s.can);
  const [cfg, setCfg] = useState<AgentConfig>(agent.config);
  const [raw, setRaw] = useState(JSON.stringify(agent.config, null, 2));
  const [note, setNote] = useState("");
  const [advanced, setAdvanced] = useState(false);
  useEffect(() => { setCfg(agent.config); setRaw(JSON.stringify(agent.config, null, 2)); }, [agent]);
  const save = useAction(() => put<Agent>(`/agents/${agent.id}`, { config: advanced ? JSON.parse(raw) : cfg, change_note: note }),
    { invalidate: [["agent", agent.id], ["agents"]], success: (a) => `Saved (v${a.current_version})` });
  const num = (k: keyof AgentConfig) => (e: React.ChangeEvent<HTMLInputElement>) => setCfg({ ...cfg, [k]: Number(e.target.value) });
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <div><CardTitle>Configuration</CardTitle><CardDescription>Model, limits, retries, fallbacks, memory and policies. Validation (incl. step hard limit) happens server-side.</CardDescription></div>
        <label className="flex items-center gap-2 text-xs">JSON <Switch checked={advanced} onCheckedChange={(v) => { setAdvanced(v); setRaw(JSON.stringify(cfg, null, 2)); }} label="Advanced JSON editing" /></label>
      </CardHeader>
      <CardContent className="space-y-4">
        {advanced ? <Editor language="json" value={raw} onChange={setRaw} height={420} ariaLabel="Agent configuration JSON" /> : (
          <div className="grid gap-3 md:grid-cols-4">
            <Field label="Provider"><Select value={cfg.provider} onChange={(e) => setCfg({ ...cfg, provider: e.target.value })}>{["mock", "ollama", "openai", "anthropic", "gemini", "nvidia_nim"].map((p) => <option key={p}>{p}</option>)}</Select></Field>
            <Field label="Model"><Input value={cfg.model} onChange={(e) => setCfg({ ...cfg, model: e.target.value })} /></Field>
            <Field label="Temperature"><Input type="number" step="0.1" min={0} max={2} value={cfg.temperature} onChange={num("temperature")} /></Field>
            <Field label="Max tokens"><Input type="number" value={cfg.max_tokens} onChange={num("max_tokens")} /></Field>
            <Field label="Max steps"><Input type="number" min={1} value={cfg.max_steps} onChange={num("max_steps")} /></Field>
            <Field label="Per-call timeout (s)"><Input type="number" value={cfg.timeout_s} onChange={num("timeout_s")} /></Field>
            <Field label="Total budget (s)"><Input type="number" value={cfg.total_timeout_s} onChange={num("total_timeout_s")} /></Field>
            <Field label="Retry attempts"><Input type="number" min={1} max={5} value={cfg.retry.max_attempts} onChange={(e) => setCfg({ ...cfg, retry: { max_attempts: Number(e.target.value) } })} /></Field>
            <Field label="Memory window (msgs)"><Input type="number" value={cfg.memory.window_messages} onChange={(e) => setCfg({ ...cfg, memory: { ...cfg.memory, window_messages: Number(e.target.value) } })} /></Field>
            <Field label="Context budget (tokens)"><Input type="number" value={cfg.memory.max_context_tokens} onChange={(e) => setCfg({ ...cfg, memory: { ...cfg.memory, max_context_tokens: Number(e.target.value) } })} /></Field>
            <Field label="Fallback route" hint="provider/model used if the primary fails" className="md:col-span-2">
              <Input placeholder="ollama/llama3.2:3b" value={cfg.fallbacks.map((f) => `${f.provider}/${f.model}`).join(", ")}
                onChange={(e) => setCfg({ ...cfg, fallbacks: e.target.value.split(",").map((s) => s.trim()).filter(Boolean).map((s) => ({ provider: s.split("/")[0], model: s.split("/").slice(1).join("/") })) })} /></Field>
            <div className="flex flex-wrap items-center gap-5 md:col-span-4">
              {(["require_citations", "evaluate_output", "stop_on_policy_denial"] as const).map((k) => (
                <label key={k} className="flex items-center gap-2 text-xs"><Switch checked={cfg.policies[k]} label={k} onCheckedChange={(v) => setCfg({ ...cfg, policies: { ...cfg.policies, [k]: v } })} />{k.replaceAll("_", " ")}</label>))}
            </div>
            <div className="md:col-span-4"><div className="mb-1.5 text-xs font-medium text-muted-foreground">System instructions</div>
              <Editor value={cfg.system_prompt} onChange={(v) => setCfg({ ...cfg, system_prompt: v })} height={160} ariaLabel="System instructions" /></div>
          </div>
        )}
        <div className="flex gap-2"><Input placeholder="Change note (recorded in version history & audit log)" value={note} onChange={(e) => setNote(e.target.value)} />
          <Button onClick={() => save.mutate(undefined)} loading={save.isPending} disabled={!can("agents:write") || agent.status === "archived"}><Save /> Save new version</Button></div>
      </CardContent>
    </Card>
  );
}

function History({ agentId, onOpen }: { agentId: string; onOpen: (id: string) => void }) {
  const q = useApi<Page<Execution>>(["executions", agentId], `/agents/${agentId}/executions?limit=50`, { refetchInterval: 5000 });
  if (q.isLoading) return <Loading />;
  if (!q.data?.items.length) return <Empty title="No executions yet" />;
  return (
    <Card><CardContent className="p-0">
      <Table><THead><TR><TH>When</TH><TH>Status</TH><TH>Input</TH><TH>Version</TH><TH>Latency</TH><TH>Tokens</TH><TH>Model</TH></TR></THead>
        <TBody>{q.data.items.map((e) => (
          <TR key={e.id} className="cursor-pointer" onClick={() => onOpen(e.id)}>
            <TD className="whitespace-nowrap text-muted-foreground">{fmt.ago(e.created_at)}</TD><TD><StatusBadge status={e.status} /></TD>
            <TD className="max-w-md truncate">{e.input}</TD><TD>v{e.agent_version}</TD><TD>{fmt.ms(e.latency_ms)}</TD>
            <TD className="tabular-nums">{e.tokens_in + e.tokens_out}</TD><TD><div className="flex items-center gap-1.5">{e.model}<MockBadge mock={e.is_mock} /></div></TD></TR>))}</TBody></Table>
    </CardContent></Card>
  );
}

function Versions({ agentId }: { agentId: string }) {
  const q = useApi<{ version: number; change_note: string; created_at: string; config: AgentConfig }[]>(["agent-versions", agentId], `/agents/${agentId}/versions`);
  const [sel, setSel] = useState<number | null>(null);
  if (q.isLoading) return <Loading />;
  const v = q.data?.find((x) => x.version === sel) ?? q.data?.[0];
  return (
    <div className="grid gap-4 md:grid-cols-3">
      <Card className="p-2">{q.data?.map((x) => (
        <button key={x.version} onClick={() => setSel(x.version)} className={`block w-full rounded-md px-3 py-2 text-left text-xs hover:bg-muted ${v?.version === x.version ? "bg-muted" : ""}`}>
          <div className="font-medium">Version {x.version}</div><div className="text-muted-foreground">{x.change_note || "—"} · {fmt.ago(x.created_at)}</div></button>))}</Card>
      <div className="md:col-span-2">{v && <Editor language="json" value={JSON.stringify(v.config, null, 2)} readOnly height={460} ariaLabel="Version config" />}</div>
    </div>
  );
}

export async function prefetchAgent(id: string) {
  return get<Agent>(`/agents/${id}`);
}

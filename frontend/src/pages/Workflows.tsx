import {
  addEdge, Background, type Connection, Controls, type Edge, Handle, MiniMap, type Node, type NodeProps, Position, ReactFlow, ReactFlowProvider,
  useEdgesState, useNodesState, useReactFlow,
} from "@xyflow/react";
import { CheckCircle2, CircleDot, GitBranch, Play, Plus, Save, SkipForward, StepForward, XCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Empty, JsonView, Loading, PageHeader, StatusBadge } from "@/components/common";
import Editor from "@/components/common/LazyEditor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/form";
import { Switch } from "@/components/ui/switch";
import { useAction, useApi } from "@/hooks/api";
import { cn, fmt } from "@/lib";
import { post, put } from "@/services/api";
import { useAuth } from "@/stores/auth";

type Graph = { nodes: Node[]; edges: Edge[] };
type Wf = { id: string; name: string; description: string; current_version: number; graph: Graph; versions: { version: number; change_note: string; created_at: string }[] };
type TraceEntry = { node_id: string; type: string; label: string; status: string; latency_ms: number; error: string | null; output_preview: string | null; handle: string | null };
type WfExec = { id: string; status: string; output: unknown; error: string | null; trace: TraceEntry[]; paused_at_node: string | null; pause_reason: string | null;
  state: { node_status: Record<string, string>; outputs: Record<string, unknown> }; latency_ms: number };

const PALETTE: { type: string; label: string; config: Record<string, unknown> }[] = [
  { type: "input", label: "User Input", config: {} },
  { type: "llm", label: "LLM", config: { provider: "mock", model: "mock-reasoner-1", prompt: "{{ last }}" } },
  { type: "agent", label: "AI Agent", config: { agent_id: "", input: "{{ input.question }}" } },
  { type: "mcp_tool", label: "MCP Tool", config: { tool: "calculator", arguments: { expression: "6*7" }, retry: { max_attempts: 2 } } },
  { type: "api_request", label: "API Request", config: { method: "GET", url: "https://api.github.com/zen" } },
  { type: "rag_retrieval", label: "RAG Retrieval", config: { query: "{{ input.question }}", strategy: "hybrid", top_k: 3 } },
  { type: "condition", label: "Conditional Branch", config: { expression: "len(str(last)) > 10" } },
  { type: "python_function", label: "Python Function", config: { function: "word_count", argument: "{{ last }}" } },
  { type: "human_approval", label: "Human Approval", config: { instructions: "Approve to continue" } },
  { type: "transform", label: "Data Transformation", config: { template: "{{ last }}" } },
  { type: "output", label: "Output", config: {} },
  { type: "error_handler", label: "Error Handler", config: { message: "handled" } },
];
const STATUS_RING: Record<string, string> = { completed: "ring-success", failed: "ring-danger", skipped: "opacity-50", running: "ring-info" };

function WfNode({ id, data, selected }: NodeProps) {
  const d = data as { label: string; nodeType: string; status?: string; paused?: boolean; breakpoint?: boolean };
  const t = d.nodeType;
  const branches = t === "condition" ? ["true", "false"] : t === "human_approval" ? ["approved", "rejected"] : null;
  return (
    <div className={cn("min-w-40 rounded-lg border bg-card px-3 py-2 text-xs shadow-sm ring-2 ring-transparent", selected && "border-primary",
      d.status && STATUS_RING[d.status], d.paused && "ring-warning")}>
      {t !== "input" && <Handle type="target" position={Position.Left} />}
      <div className="flex items-center gap-1.5 font-medium">{d.breakpoint && <CircleDot className="size-3 text-danger" aria-label="breakpoint" />}{d.label}</div>
      <div className="text-[10px] text-muted-foreground">{t}{d.status ? ` · ${d.status}` : ""}{d.paused ? " · paused" : ""}</div>
      {branches ? branches.map((h, i) => (
        <Handle key={h} id={h} type="source" position={Position.Right} style={{ top: 14 + i * 18, background: i === 0 ? "var(--success)" : "var(--danger)" }} title={h} />
      )) : t !== "output" && <Handle type="source" position={Position.Right} />}
      {!["input", "output", "error_handler", "condition", "human_approval"].includes(t) &&
        <Handle id="error" type="source" position={Position.Bottom} style={{ background: "var(--danger)" }} title="on error" />}
      {branches && <div className="mt-1 flex flex-col items-end text-[9px] text-muted-foreground">{branches.map((b) => <span key={b}>{b}</span>)}</div>}
      <span className="sr-only">{id}</span>
    </div>
  );
}
const nodeTypes = { wf: WfNode };

const toFlow = (g: Graph): Graph => ({
  nodes: g.nodes.map((n) => ({ ...n, type: "wf", position: n.position ?? { x: 0, y: 0 },
    data: { ...(n.data as object), nodeType: n.type, label: (n.data as { label?: string })?.label ?? n.type } })),
  edges: g.edges.map((e) => ({ ...e, label: e.sourceHandle ?? undefined, animated: false })),
});
const fromFlow = (nodes: Node[], edges: Edge[]) => ({
  nodes: nodes.map((n) => ({ id: n.id, type: (n.data as { nodeType: string }).nodeType, position: n.position,
    data: { label: (n.data as { label: string }).label, config: (n.data as { config?: unknown }).config ?? {} } })),
  edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target, ...(e.sourceHandle ? { sourceHandle: e.sourceHandle } : {}) })),
});

export default function WorkflowsPage() {
  const list = useApi<{ id: string; name: string; current_version: number }[]>(["workflows"], "/workflows");
  const [sel, setSel] = useState<string | null>(null);
  const [name, setName] = useState("");
  const can = useAuth((s) => s.can);
  const create = useAction(() => post<{ id: string }>("/workflows", { name, graph: { nodes: [
    { id: "in", type: "input", position: { x: 0, y: 80 }, data: { label: "Input" } },
    { id: "out", type: "output", position: { x: 400, y: 80 }, data: { label: "Output" } }], edges: [{ id: "e1", source: "in", target: "out" }] } }),
    { invalidate: [["workflows"]], success: "Workflow created", onSuccess: (r) => { setSel(r.id); setName(""); } });
  useEffect(() => { if (!sel && list.data?.[0]) setSel(list.data[0].id); }, [list.data, sel]);
  return (
    <>
      <PageHeader icon={GitBranch} title="AI Workflow Designer" description="Drag nodes from the palette, connect handles (green/red branch outputs, bottom red = on-error), configure, validate, version, execute and step-debug."
        actions={<>
          <Select aria-label="Workflow" className="w-72" value={sel ?? ""} onChange={(e) => setSel(e.target.value)}>{list.data?.map((w) => <option key={w.id} value={w.id}>{w.name} (v{w.current_version})</option>)}</Select>
          {can("workflows:write") && <div className="flex gap-1"><Input placeholder="New workflow name" value={name} onChange={(e) => setName(e.target.value)} className="w-48" />
            <Button variant="outline" onClick={() => name && create.mutate(undefined)} aria-label="Create workflow"><Plus /></Button></div>}
        </>} />
      {list.isLoading ? <Loading /> : sel ? <ReactFlowProvider><Designer id={sel} key={sel} /></ReactFlowProvider> : <Empty title="No workflows" />}
    </>
  );
}

function Designer({ id }: { id: string }) {
  const can = useAuth((s) => s.can);
  const wf = useApi<Wf>(["workflow", id], `/workflows/${id}`);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [input, setInput] = useState('{\n  "question": "How quickly must a Sev2 incident be acknowledged?",\n  "severity": "sev2",\n  "service": "checkout"\n}');
  const [breakpoints, setBreakpoints] = useState<string[]>([]);
  const [exec, setExec] = useState<WfExec | null>(null);
  const rf = useReactFlow();

  useEffect(() => {
    if (!wf.data) return;
    const g = toFlow(wf.data.graph);
    setNodes(g.nodes);
    setEdges(g.edges);
    setExec(null);
  }, [wf.data, setNodes, setEdges]);

  // reflect execution state on the canvas
  const decorated = useMemo(() => nodes.map((n) => ({ ...n, data: { ...n.data, status: exec?.state.node_status[n.id], paused: exec?.paused_at_node === n.id,
    breakpoint: breakpoints.includes(n.id) } })), [nodes, exec, breakpoints]);
  const onConnect = useCallback((c: Connection) => setEdges((es) => addEdge({ ...c, id: `e${Date.now()}`, label: c.sourceHandle ?? undefined }, es)), [setEdges]);

  const addNode = (type: string, pos?: { x: number; y: number }) => {
    const p = PALETTE.find((x) => x.type === type)!;
    const nid = `${type}_${Math.random().toString(36).slice(2, 6)}`;
    setNodes((ns) => [...ns, { id: nid, type: "wf", position: pos ?? { x: 200 + ns.length * 20, y: 260 }, data: { nodeType: type, label: p.label, config: structuredClone(p.config) } }]);
    setSelected(nid);
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const type = e.dataTransfer.getData("application/aiecc-node");
    if (type) addNode(type, rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }));
  };

  const validate = useAction(() => post<{ valid: boolean; errors: string[] }>("/workflows/validate", { name: "x", graph: fromFlow(nodes, edges) }),
    { onSuccess: (r) => setErrors(r.errors), success: (r) => (r.valid ? "Graph is valid" : `${r.errors.length} validation error(s)`) });
  const save = useAction(() => put<Wf>(`/workflows/${id}`, { graph: fromFlow(nodes, edges), change_note: "designer save" }),
    { invalidate: [["workflow", id], ["workflows"]], success: (w) => `Saved version ${w.current_version}`, onSuccess: () => setErrors([]) });
  const run = useAction((step: boolean) => post<WfExec>(`/workflows/${id}/execute`, { input: JSON.parse(input), breakpoints, step_mode: step }), { onSuccess: setExec });
  const approve = useAction((decision: string) => post<WfExec>(`/workflow-executions/${exec!.id}/approve`, { decision }), { onSuccess: setExec, success: (r) => `Execution ${r.status}` });
  const resume = useAction((step: boolean) => post<WfExec>(`/workflow-executions/${exec!.id}/resume`, { step }), { onSuccess: setExec });

  if (wf.isLoading) return <Loading />;
  const node = nodes.find((n) => n.id === selected);
  const nd = node?.data as { label: string; nodeType: string; config?: Record<string, unknown> } | undefined;
  const updateNode = (patch: Record<string, unknown>) => setNodes((ns) => ns.map((n) => (n.id === selected ? { ...n, data: { ...n.data, ...patch } } : n)));
  return (
    <div className="grid gap-4 xl:grid-cols-[180px_1fr_340px]">
      <Card className="h-fit p-2"><div className="px-1 pb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Palette</div>
        {PALETTE.map((p) => (
          <button key={p.type} draggable onDragStart={(e) => e.dataTransfer.setData("application/aiecc-node", p.type)} onClick={() => addNode(p.type)}
            className="mb-1 block w-full cursor-grab rounded-md border px-2 py-1.5 text-left text-xs hover:bg-muted" disabled={!can("workflows:write")}>{p.label}</button>))}
      </Card>
      <div className="space-y-3">
        <div className="h-[560px] rounded-xl border bg-card" onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
          <ReactFlow nodes={decorated} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect} nodeTypes={nodeTypes}
            onNodeClick={(_, n) => setSelected(n.id)} onPaneClick={() => setSelected(null)} fitView deleteKeyCode={["Backspace", "Delete"]}>
            <Background /><Controls /><MiniMap pannable zoomable />
          </ReactFlow>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => validate.mutate(undefined)} loading={validate.isPending}><CheckCircle2 /> Validate</Button>
          {can("workflows:write") && <Button variant="outline" onClick={() => save.mutate(undefined)} loading={save.isPending}><Save /> Save version</Button>}
          <span className="self-center text-xs text-muted-foreground">v{wf.data?.current_version} · {nodes.length} nodes · {edges.length} edges</span>
        </div>
        {!!errors.length && <ul className="rounded-md border border-danger/40 bg-danger/5 p-2 text-xs text-danger">{errors.map((e) => <li key={e}>• {e}</li>)}</ul>}
        {exec && <ExecPanel exec={exec} onApprove={(d) => approve.mutate(d)} onResume={(s) => resume.mutate(s)} busy={approve.isPending || resume.isPending} />}
      </div>
      <div className="space-y-3">
        <Card><CardHeader><CardTitle>Run</CardTitle></CardHeader><CardContent className="space-y-2">
          <Editor language="json" value={input} onChange={setInput} height={130} ariaLabel="Workflow input" />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => run.mutate(false)} loading={run.isPending} disabled={!can("workflows:execute")}><Play /> Execute saved version</Button>
            <Button size="sm" variant="outline" onClick={() => run.mutate(true)} disabled={!can("workflows:execute")}><StepForward /> Step mode</Button>
          </div>
          <p className="text-[11px] text-muted-foreground">Executes the last saved version. Breakpoints: {breakpoints.length ? breakpoints.join(", ") : "none"}</p>
        </CardContent></Card>
        <Card><CardHeader><CardTitle>Node</CardTitle></CardHeader><CardContent className="space-y-2">
          {!node || !nd ? <p className="text-xs text-muted-foreground">Select a node to configure it.</p> : <>
            <Field label="Label"><Input value={nd.label} onChange={(e) => updateNode({ label: e.target.value })} /></Field>
            <div className="text-[11px] text-muted-foreground">id <code>{node.id}</code> · type <code>{nd.nodeType}</code></div>
            <label className="flex items-center gap-2 text-xs"><Switch checked={breakpoints.includes(node.id)} label="Breakpoint"
              onCheckedChange={(v) => setBreakpoints((b) => (v ? [...b, node.id] : b.filter((x) => x !== node.id)))} /> Breakpoint before this node</label>
            {!["input", "output"].includes(nd.nodeType) && <><div className="text-xs font-medium text-muted-foreground">Config (JSON; Jinja templates like <code>{"{{ input.x }}"}</code>, <code>{"{{ nodes.<id> }}"}</code>)</div>
              <NodeConfig key={node.id} value={nd.config ?? {}} onChange={(c) => updateNode({ config: c })} /></>}
            {exec?.state.outputs[node.id] !== undefined && <><div className="text-xs font-medium">Last output</div><JsonView value={exec.state.outputs[node.id]} className="max-h-48" /></>}
          </>}
        </CardContent></Card>
      </div>
    </div>
  );
}

function NodeConfig({ value, onChange }: { value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void }) {
  const [text, setText] = useState(JSON.stringify(value, null, 2));
  const [err, setErr] = useState<string | null>(null);
  return (<>
    <Editor language="json" value={text} height={180} ariaLabel="Node configuration" onChange={(t) => { setText(t); try { onChange(JSON.parse(t)); setErr(null); } catch { setErr("invalid JSON"); } }} />
    {err && <span className="text-[11px] text-danger">{err}</span>}
  </>);
}

function ExecPanel({ exec, onApprove, onResume, busy }: { exec: WfExec; onApprove: (d: string) => void; onResume: (step: boolean) => void; busy: boolean }) {
  const can = useAuth((s) => s.can);
  return (
    <Card><CardHeader className="flex-row flex-wrap items-center justify-between gap-2"><CardTitle className="flex items-center gap-2">Execution <StatusBadge status={exec.status} />
      <span className="text-xs font-normal text-muted-foreground">{fmt.ms(exec.latency_ms)}</span></CardTitle>
      <div className="flex gap-2">
        {exec.status === "waiting_approval" && <>{can("workflows:approve") ? <>
          <Button size="sm" onClick={() => onApprove("approved")} loading={busy}><CheckCircle2 /> Approve</Button>
          <Button size="sm" variant="destructive" onClick={() => onApprove("rejected")}><XCircle /> Reject</Button></> :
          <Badge variant="warning">waiting for a reviewer with workflows:approve (initiators can't self-approve)</Badge>}</>}
        {exec.status === "paused" && <><Button size="sm" variant="outline" onClick={() => onResume(true)} loading={busy}><StepForward /> Step</Button>
          <Button size="sm" onClick={() => onResume(false)}><SkipForward /> Continue</Button></>}
      </div></CardHeader>
      <CardContent className="space-y-2">
        {exec.paused_at_node && <p className="text-xs text-warning">Paused at <code>{exec.paused_at_node}</code> ({exec.pause_reason})</p>}
        {exec.error && <p className="text-xs text-danger">{exec.error}</p>}
        <ol className="space-y-1">{exec.trace.map((t, i) => (
          <li key={i} className="rounded border p-2 text-xs"><div className="flex items-center gap-2"><span className="font-medium">{t.label}</span><Badge variant="muted">{t.type}</Badge><StatusBadge status={t.status} />
            {t.handle && <Badge variant="outline">→ {t.handle}</Badge>}<span className="ml-auto text-muted-foreground">{fmt.ms(t.latency_ms)}</span></div>
            {t.error && <div className="mt-1 text-danger">{t.error}</div>}
            {t.output_preview && <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap font-mono text-[10px] text-muted-foreground">{t.output_preview}</pre>}</li>))}</ol>
        {exec.status === "completed" && <><div className="text-xs font-medium">Output</div><JsonView value={exec.output} className="max-h-48" /></>}
      </CardContent></Card>
  );
}

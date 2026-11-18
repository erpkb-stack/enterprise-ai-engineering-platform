import { addEdge, Background, type Connection, Controls, type Edge, Handle, type Node, type NodeProps, Position, ReactFlow, ReactFlowProvider, useEdgesState, useNodesState } from "@xyflow/react";
import { FileText, Network, Plus, Save, ScanSearch, Wand2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer } from "recharts";
import { Empty, Kv, Loading, MockBadge, PageHeader } from "@/components/common";
import Editor from "@/components/common/LazyEditor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAction, useApi } from "@/hooks/api";
import { cn, fmt } from "@/lib";
import { post, put } from "@/services/api";
import { useAuth, useUi } from "@/stores/auth";

type Design = { id: string; name: string; description: string; kind: string; graph: { nodes: Node[]; edges: Edge[] }; sequence_diagram: string; data_flow: string; api_contracts: string; requirements: Record<string, number> };
type Finding = { category: string; severity: string; title: string; detail: string; components: string[]; recommendation: string };
type Review = { scores: Record<string, number>; overall: number; findings: Finding[]; tradeoffs: { concern: string; findings: string[]; alternatives: { option: string; pros: string; cons: string }[] }[];
  metrics: Record<string, unknown>; ai_narrative?: { text: string; is_mock: boolean; label: string } };
type Adr = { id: string; number: number; title: string; status: string; context: string; decision: string; consequences: string; created_at: string };
const KINDS = ["client", "gateway", "load_balancer", "service", "worker", "database", "cache", "queue", "vector_db", "llm", "mcp_server", "object_storage", "search", "cdn", "external"];
const KIND_COLOR: Record<string, string> = { database: "border-l-chart-3", cache: "border-l-red-400", queue: "border-l-amber-400", llm: "border-l-violet-500", mcp_server: "border-l-teal-500", gateway: "border-l-sky-500" };

function Comp({ data, selected }: NodeProps) {
  const d = data as { label: string; kind: string; replicas?: number; public?: boolean; auth?: boolean };
  return <div className={cn("min-w-32 rounded-lg border border-l-4 bg-card px-3 py-2 text-xs shadow-sm", KIND_COLOR[d.kind] ?? "border-l-primary", selected && "ring-2 ring-primary")}>
    <Handle type="target" position={Position.Left} /><div className="font-medium">{d.label}</div>
    <div className="text-[10px] text-muted-foreground">{d.kind}{d.replicas ? ` ×${d.replicas}` : ""}{d.public ? " · public" : ""}{d.auth ? " · auth" : ""}</div>
    <Handle type="source" position={Position.Right} /></div>;
}
const nodeTypes = { component: Comp };

export default function ArchitectureLab() {
  const can = useAuth((s) => s.can);
  const list = useApi<Design[]>(["designs"], "/architecture/designs");
  const [sel, setSel] = useState<string | null>(null);
  const [name, setName] = useState("");
  const create = useAction(() => post<Design>("/architecture/designs", { name, kind: "microservices" }), { invalidate: [["designs"]], success: "Design created", onSuccess: (d) => { setSel(d.id); setName(""); } });
  useEffect(() => { if (!sel && list.data?.[0]) setSel(list.data[0].id); }, [list.data, sel]);
  return (
    <>
      <PageHeader icon={Network} title="Architecture & System Design Lab" description="Model components and connections, document sequence/data flows and API contracts, run the Architecture Review Assistant, and record ADRs."
        actions={<><Select aria-label="Design" className="w-72" value={sel ?? ""} onChange={(e) => setSel(e.target.value)}>{list.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</Select>
          {can("architecture:write") && <div className="flex gap-1"><Input className="w-48" placeholder="New design name" value={name} onChange={(e) => setName(e.target.value)} />
            <Button variant="outline" aria-label="Create design" onClick={() => name && create.mutate(undefined)}><Plus /></Button></div>}</>} />
      <Tabs defaultValue="design"><TabsList><TabsTrigger value="design">Design & review</TabsTrigger><TabsTrigger value="adrs">Decision records</TabsTrigger></TabsList>
        <TabsContent value="design">{list.isLoading ? <Loading /> : sel ? <ReactFlowProvider><DesignView id={sel} key={sel} /></ReactFlowProvider> : <Empty title="No designs" />}</TabsContent>
        <TabsContent value="adrs"><Adrs /></TabsContent></Tabs>
    </>
  );
}

function DesignView({ id }: { id: string }) {
  const can = useAuth((s) => s.can);
  const q = useApi<Design & { reviews: { report: Review; created_at: string }[] }>(["design", id], `/architecture/designs/${id}`);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [sel, setSel] = useState<{ type: "node" | "edge"; id: string } | null>(null);
  const [docs, setDocs] = useState({ sequence_diagram: "", data_flow: "", api_contracts: "" });
  const [req, setReq] = useState("{}");
  const [review, setReview] = useState<Review | null>(null);
  useEffect(() => {
    if (!q.data) return;
    setNodes(q.data.graph.nodes.map((n) => ({ ...n, type: "component" })));
    setEdges(q.data.graph.edges.map((e) => ({ ...e, label: (e.data as { protocol?: string })?.protocol })));
    setDocs({ sequence_diagram: q.data.sequence_diagram, data_flow: q.data.data_flow, api_contracts: q.data.api_contracts });
    setReq(JSON.stringify(q.data.requirements, null, 2));
    setReview(q.data.reviews[0]?.report ?? null);
  }, [q.data, setNodes, setEdges]);
  const graph = () => ({ nodes: nodes.map(({ id: nid, position, data }) => ({ id: nid, type: "component", position, data })), edges: edges.map(({ id: eid, source, target, data }) => ({ id: eid, source, target, data: data ?? {} })) });
  const save = useAction(() => put(`/architecture/designs/${id}`, { graph: graph(), ...docs, requirements: JSON.parse(req) }), { invalidate: [["design", id]], success: "Design saved" });
  const run = useAction(async (ai: boolean) => { await put(`/architecture/designs/${id}`, { graph: graph(), ...docs, requirements: JSON.parse(req) }); return post<Review>(`/architecture/designs/${id}/review`, { ai_narrative: ai }); },
    { onSuccess: setReview, success: (r) => `Review complete: ${r.findings.length} findings` });
  const genSeq = useAction(async () => { await put(`/architecture/designs/${id}`, { graph: graph() }); return post<{ sequence_diagram: string }>(`/architecture/designs/${id}/sequence`); },
    { onSuccess: (r) => setDocs((d) => ({ ...d, sequence_diagram: r.sequence_diagram })) });
  const addNode = () => { const nid = `c${Date.now() % 100000}`; setNodes((ns) => [...ns, { id: nid, type: "component", position: { x: 100, y: 380 }, data: { label: "New service", kind: "service", replicas: 1 } }]); setSel({ type: "node", id: nid }); };
  const onConnect = (c: Connection) => setEdges((es) => addEdge({ ...c, id: `e${Date.now()}`, data: { protocol: "http", sync: true, timeout_ms: 2000 }, label: "http" }, es));

  if (q.isLoading) return <Loading />;
  const node = sel?.type === "node" ? nodes.find((n) => n.id === sel.id) : undefined;
  const edge = sel?.type === "edge" ? edges.find((e) => e.id === sel.id) : undefined;
  const nd = (node?.data ?? {}) as Record<string, unknown>;
  const ed = (edge?.data ?? {}) as Record<string, unknown>;
  const setND = (k: string, v: unknown) => setNodes((ns) => ns.map((n) => (n.id === sel?.id ? { ...n, data: { ...n.data, [k]: v } } : n)));
  const setED = (k: string, v: unknown) => setEdges((es) => es.map((e) => (e.id === sel?.id ? { ...e, data: { ...e.data, [k]: v }, label: k === "protocol" ? String(v) : e.label } : e)));
  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-[1fr_300px]">
        <div className="h-[460px] rounded-xl border bg-card">
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect} fitView
            onNodeClick={(_, n) => setSel({ type: "node", id: n.id })} onEdgeClick={(_, e) => setSel({ type: "edge", id: e.id })} onPaneClick={() => setSel(null)} deleteKeyCode={["Backspace", "Delete"]}>
            <Background /><Controls /></ReactFlow>
        </div>
        <Card><CardHeader><CardTitle>{node ? "Component" : edge ? "Connection" : "Properties"}</CardTitle></CardHeader><CardContent className="space-y-2">
          {node ? <>
            <Field label="Label"><Input value={String(nd.label ?? "")} onChange={(e) => setND("label", e.target.value)} /></Field>
            <Field label="Kind"><Select value={String(nd.kind ?? "service")} onChange={(e) => setND("kind", e.target.value)}>{KINDS.map((k) => <option key={k}>{k}</option>)}</Select></Field>
            <Field label="Replicas"><Input type="number" min={1} value={Number(nd.replicas ?? 1)} onChange={(e) => setND("replicas", Number(e.target.value))} /></Field>
            <Field label="Region"><Input value={String(nd.region ?? "")} onChange={(e) => setND("region", e.target.value)} /></Field>
            {(["public", "auth", "encrypted", "observability"] as const).map((k) => <label key={k} className="flex items-center gap-2 text-xs"><Switch checked={!!nd[k]} label={k} onCheckedChange={(v) => setND(k, v)} />{k}</label>)}
          </> : edge ? <>
            <Field label="Protocol"><Select value={String(ed.protocol ?? "http")} onChange={(e) => setED("protocol", e.target.value)}>{["http", "https", "grpc", "sql", "redis", "mcp", "queue", "event"].map((p) => <option key={p}>{p}</option>)}</Select></Field>
            <Field label="Timeout (ms)"><Input type="number" value={Number(ed.timeout_ms ?? 0)} onChange={(e) => setED("timeout_ms", Number(e.target.value))} /></Field>
            <Field label="Expected latency (ms)"><Input type="number" value={Number(ed.latency_ms ?? 20)} onChange={(e) => setED("latency_ms", Number(e.target.value))} /></Field>
            {(["sync", "auth", "circuit_breaker"] as const).map((k) => <label key={k} className="flex items-center gap-2 text-xs"><Switch checked={!!ed[k]} label={k} onCheckedChange={(v) => setED(k, v)} />{k.replace("_", " ")}</label>)}
          </> : <p className="text-xs text-muted-foreground">Select a component or connection. Drag between handles to connect.</p>}
          <div className="flex flex-wrap gap-2 pt-2">
            {can("architecture:write") && <Button size="sm" variant="outline" onClick={addNode}><Plus /> Component</Button>}
            {can("architecture:write") && <Button size="sm" variant="outline" onClick={() => save.mutate(undefined)} loading={save.isPending}><Save /> Save</Button>}
            <Button size="sm" onClick={() => run.mutate(false)} loading={run.isPending}><ScanSearch /> Review</Button>
            <Button size="sm" variant="outline" onClick={() => run.mutate(true)}><Wand2 /> Review + AI summary</Button>
          </div>
        </CardContent></Card>
      </div>
      {review && <ReviewView r={review} />}
      <Tabs defaultValue="seq"><TabsList><TabsTrigger value="seq">Sequence diagram</TabsTrigger><TabsTrigger value="flow">Data flow</TabsTrigger><TabsTrigger value="api">API contracts</TabsTrigger><TabsTrigger value="req">Requirements</TabsTrigger></TabsList>
        <TabsContent value="seq"><div className="grid gap-4 lg:grid-cols-2"><div className="space-y-2"><Editor value={docs.sequence_diagram} onChange={(v) => setDocs({ ...docs, sequence_diagram: v })} height={260} ariaLabel="Mermaid sequence diagram" />
          <Button size="sm" variant="outline" onClick={() => genSeq.mutate(undefined)} loading={genSeq.isPending}>Generate from connections</Button></div><Mermaid code={docs.sequence_diagram} /></div></TabsContent>
        <TabsContent value="flow"><Textarea rows={10} className="font-sans text-sm" value={docs.data_flow} onChange={(e) => setDocs({ ...docs, data_flow: e.target.value })} placeholder="Describe data classification, flows and trust boundaries…" /></TabsContent>
        <TabsContent value="api"><Editor language="yaml" value={docs.api_contracts} onChange={(v) => setDocs({ ...docs, api_contracts: v })} height={260} ariaLabel="API contracts" /></TabsContent>
        <TabsContent value="req"><Editor language="json" value={req} onChange={setReq} height={200} ariaLabel="Requirements" />
          <p className="mt-1 text-[11px] text-muted-foreground">Keys: availability_target, latency_budget_ms, team_count, monthly_budget_usd</p></TabsContent>
      </Tabs>
    </div>
  );
}

function ReviewView({ r }: { r: Review }) {
  const data = Object.entries(r.scores).map(([k, v]) => ({ attr: k.replaceAll("_", " "), score: v }));
  return (
    <div className="grid gap-4 xl:grid-cols-[360px_1fr]">
      <Card><CardHeader><CardTitle>Quality attributes · {r.overall}/100</CardTitle><CardDescription>Deterministic rules engine</CardDescription></CardHeader>
        <CardContent className="h-64"><ResponsiveContainer><RadarChart data={data}><PolarGrid stroke="var(--border)" /><PolarAngleAxis dataKey="attr" fontSize={10} stroke="var(--muted-foreground)" />
          <PolarRadiusAxis domain={[0, 100]} fontSize={9} /><Radar dataKey="score" stroke="var(--chart-1)" fill="var(--chart-1)" fillOpacity={0.3} /></RadarChart></ResponsiveContainer></CardContent>
        <CardContent><Kv items={Object.entries(r.metrics).filter(([, v]) => typeof v !== "object").map(([k, v]) => [k.replaceAll("_", " "), String(v)])} /></CardContent></Card>
      <div className="space-y-3">
        {r.ai_narrative && <Card><CardHeader><CardTitle className="flex items-center gap-2">AI summary <MockBadge mock={r.ai_narrative.is_mock} /></CardTitle><CardDescription>{r.ai_narrative.label}</CardDescription></CardHeader>
          <CardContent><p className="whitespace-pre-wrap text-sm">{r.ai_narrative.text}</p></CardContent></Card>}
        <Card><CardHeader><CardTitle>Findings ({r.findings.length})</CardTitle></CardHeader><CardContent className="space-y-2">{r.findings.map((f, i) => (
          <div key={i} className="rounded-md border p-2 text-xs"><div className="flex flex-wrap items-center gap-2"><Badge variant={f.severity === "critical" || f.severity === "high" ? "danger" : f.severity === "medium" ? "warning" : "muted"}>{f.severity}</Badge>
            <Badge variant="outline">{f.category}</Badge><span className="font-medium">{f.title}</span></div>
            {f.detail && <p className="mt-1 text-muted-foreground">{f.detail}</p>}<p className="mt-1">→ {f.recommendation}</p></div>))}</CardContent></Card>
        <Card><CardHeader><CardTitle>Tradeoff report</CardTitle><CardDescription>Alternatives and their consequences</CardDescription></CardHeader><CardContent className="space-y-3">{r.tradeoffs.map((t) => (
          <div key={t.concern}><div className="text-xs font-medium">{t.concern.replaceAll("_", " ")} <span className="font-normal text-muted-foreground">({t.findings.length} findings)</span></div>
            <div className="mt-1 grid gap-2 md:grid-cols-2">{t.alternatives.map((a) => <div key={a.option} className="rounded border p-2 text-xs"><div className="font-medium">{a.option}</div>
              <div className="mt-1 text-success">+ {a.pros}</div><div className="text-danger">− {a.cons}</div></div>)}</div></div>))}</CardContent></Card>
      </div>
    </div>
  );
}

function Mermaid({ code }: { code: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useUi((s) => s.theme);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    if (!code.trim()) return;
    import("mermaid").then(async ({ default: m }) => {
      m.initialize({ startOnLoad: false, theme: theme === "dark" ? "dark" : "default", securityLevel: "strict" });
      try {
        const { svg } = await m.render(`m${Date.now()}`, code);
        if (!cancelled && ref.current) { ref.current.innerHTML = svg; setErr(null); }
      } catch (e) { if (!cancelled) setErr(String((e as Error).message ?? e)); }
    });
    return () => { cancelled = true; };
  }, [code, theme]);
  return <div className="min-h-40 overflow-auto rounded-lg border bg-card p-3">{err ? <p className="text-xs text-danger">{err}</p> : !code.trim() ? <p className="text-xs text-muted-foreground">No diagram yet.</p> : null}<div ref={ref} /></div>;
}

function Adrs() {
  const can = useAuth((s) => s.can);
  const q = useApi<Adr[]>(["adrs"], "/architecture/adrs");
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ title: "", status: "proposed", context: "", decision: "", consequences: "" });
  const create = useAction(() => post("/architecture/adrs", f), { invalidate: [["adrs"]], success: "ADR recorded", onSuccess: () => setOpen(false) });
  return (
    <div className="space-y-3">
      {can("architecture:write") && <Button onClick={() => setOpen(true)}><FileText /> New ADR</Button>}
      {!q.data?.length ? <Empty title="No ADRs" /> : q.data.map((a) => (
        <Card key={a.id}><CardHeader><CardTitle>ADR-{String(a.number).padStart(4, "0")}: {a.title} <Badge variant={a.status === "accepted" ? "success" : "muted"} className="ml-2">{a.status}</Badge></CardTitle>
          <CardDescription>{fmt.ago(a.created_at)}</CardDescription></CardHeader>
          <CardContent className="grid gap-3 text-xs md:grid-cols-3"><div><div className="font-medium">Context</div><p className="text-muted-foreground">{a.context}</p></div>
            <div><div className="font-medium">Decision</div><p className="text-muted-foreground">{a.decision}</p></div><div><div className="font-medium">Consequences</div><p className="text-muted-foreground">{a.consequences}</p></div></CardContent></Card>))}
      <Dialog open={open} onOpenChange={setOpen} title="New architecture decision record">
        <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined); }}>
          <Field label="Title"><Input required minLength={3} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
          <Field label="Status"><Select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>{["proposed", "accepted", "superseded", "deprecated", "rejected"].map((s) => <option key={s}>{s}</option>)}</Select></Field>
          {(["context", "decision", "consequences"] as const).map((k) => <Field key={k} label={k}><Textarea className="font-sans" value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></Field>)}
          <Button type="submit" loading={create.isPending}>Record ADR</Button>
        </form>
      </Dialog>
    </div>
  );
}

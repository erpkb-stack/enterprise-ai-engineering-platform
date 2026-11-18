import { HeartPulse, Plug, PlugZap, Plus, Server, Trash2 } from "lucide-react";
import { useState } from "react";
import { Empty, ErrorState, JsonView, Kv, Loading, PageHeader, StatusBadge } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { useAction, useApi, type Page } from "@/hooks/api";
import { fmt } from "@/lib";
import { del, post } from "@/services/api";
import { useAuth } from "@/stores/auth";

type Server = { id: string; name: string; description: string; transport: string; command: string | null; args: string[]; url: string | null;
  auth_type: string; status: string; last_health: Record<string, unknown>; config_version: number; has_credentials: boolean;
  live_connected: boolean; tool_count: number; timeout_s: number; allowed_roles: string[]; is_demo: boolean; circuit: { state: string; total_failures: number } };
type ConnectResult = { tools: { name: string; description: string }[]; resources: { uri: string; name: string }[]; new_tools: string[]; note: string };

export default function McpServersPage() {
  const can = useAuth((s) => s.can);
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<{ server: string; r: ConnectResult } | null>(null);
  const q = useApi<Page<Server>>(["mcp-servers"], "/mcp/servers?limit=100", { refetchInterval: 15_000 });
  const connect = useAction((s: Server) => post<ConnectResult>(`/mcp/servers/${s.id}/connect`).then((r) => ({ s, r })),
    { invalidate: [["mcp-servers"], ["tools"]], success: ({ r }) => `Discovered ${r.tools.length} tools (${r.new_tools.length} new)`, onSuccess: ({ s, r }) => setResult({ server: s.name, r }) });
  const disconnect = useAction((s: Server) => post(`/mcp/servers/${s.id}/disconnect`), { invalidate: [["mcp-servers"]], success: "Disconnected" });
  const health = useAction((s: Server) => post<{ status: string; latency_ms?: number }>(`/mcp/servers/${s.id}/health`), {
    invalidate: [["mcp-servers"]], success: (h) => `Health: ${h.status}${h.latency_ms ? ` (${h.latency_ms} ms ping)` : ""}` });
  const remove = useAction((s: Server) => del(`/mcp/servers/${s.id}`), { invalidate: [["mcp-servers"], ["tools"]], success: "Server removed" });

  return (
    <>
      <PageHeader icon={Server} title="MCP Management Center" description="Register MCP servers over STDIO or Streamable HTTP, authenticate, discover tools/resources and monitor health. Discovered tools are disabled until allowlisted."
        actions={can("mcp:manage") && <Button onClick={() => setOpen(true)}><Plus /> Register server</Button>} />
      {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} /> : !q.data!.items.length ? <Empty title="No MCP servers" /> : (
        <div className="grid gap-3 lg:grid-cols-2">
          {q.data!.items.map((s) => (
            <Card key={s.id}>
              <CardHeader className="flex-row items-start justify-between gap-3">
                <div className="min-w-0"><CardTitle className="flex items-center gap-2">{s.name}{s.is_demo && <Badge variant="warning">DEMO</Badge>}</CardTitle>
                  <CardDescription>{s.description || "—"}</CardDescription></div>
                <div className="flex shrink-0 gap-1.5"><StatusBadge status={s.live_connected ? "connected" : s.status === "connected" ? "disconnected" : s.status} />
                  <Badge variant={s.circuit.state === "closed" ? "muted" : "danger"} title="Circuit breaker">cb {s.circuit.state}</Badge></div>
              </CardHeader>
              <CardContent className="space-y-3">
                <Kv items={[["Transport", <Badge variant="outline">{s.transport}</Badge>],
                  ["Endpoint", <code className="break-all text-[11px]">{s.transport === "stdio" ? `${s.command} ${s.args.join(" ")}` : s.url ?? "in-process"}</code>],
                  ["Auth", `${s.auth_type}${s.has_credentials ? " · credentials stored (encrypted)" : ""}`], ["Tools", String(s.tool_count)],
                  ["Config version", `v${s.config_version}`], ["Timeout", `${s.timeout_s}s`], ["Role restriction", s.allowed_roles.length ? s.allowed_roles.join(", ") : "none"],
                  ["Last health", s.last_health?.status ? `${s.last_health.status}${s.last_health.latency_ms ? ` · ${fmt.ms(s.last_health.latency_ms as number)}` : ""}` : "—"]]} />
                <div className="flex flex-wrap gap-2">
                  {can("mcp:connect") && <Button size="sm" onClick={() => connect.mutate(s)} loading={connect.isPending && connect.variables?.id === s.id}><PlugZap /> Connect & discover</Button>}
                  {can("mcp:connect") && s.live_connected && <Button size="sm" variant="outline" onClick={() => disconnect.mutate(s)}><Plug /> Disconnect</Button>}
                  <Button size="sm" variant="outline" onClick={() => health.mutate(s)}><HeartPulse /> Health check</Button>
                  {can("mcp:manage") && s.transport !== "builtin" && <Button size="sm" variant="ghost" onClick={() => confirm(`Remove ${s.name}? Its tools are removed from the registry.`) && remove.mutate(s)}><Trash2 /></Button>}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      {result && (
        <Card className="mt-4">
          <CardHeader><CardTitle>Discovery: {result.server}</CardTitle><CardDescription>{result.r.note}</CardDescription></CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <div><div className="mb-1 text-xs font-medium">Tools</div><ul className="space-y-1 text-xs">{result.r.tools.map((t) => (
              <li key={t.name} className="rounded border p-2"><span className="font-mono">{t.name}</span>{result.r.new_tools.includes(t.name) && <Badge variant="info" className="ml-2">new</Badge>}<div className="text-muted-foreground">{t.description}</div></li>))}</ul></div>
            <div><div className="mb-1 text-xs font-medium">Resources</div>{result.r.resources.length ? <JsonView value={result.r.resources} /> : <p className="text-xs text-muted-foreground">None advertised</p>}</div>
          </CardContent>
        </Card>
      )}
      <Register open={open} onOpenChange={setOpen} />
    </>
  );
}

function Register({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const [f, setF] = useState({ name: "", description: "", transport: "stdio", command: "python", args: "", url: "", auth_type: "none", credentials: "", timeout_s: 20 });
  const create = useAction(() => {
    let creds: Record<string, unknown> | undefined;
    if (f.credentials.trim()) creds = JSON.parse(f.credentials);
    return post("/mcp/servers", { name: f.name, description: f.description, transport: f.transport, timeout_s: Number(f.timeout_s), auth_type: f.auth_type,
      ...(f.transport === "stdio" ? { command: f.command, args: f.args.split(" ").filter(Boolean) } : { url: f.url }), ...(creds ? { credentials: creds } : {}) });
  }, { invalidate: [["mcp-servers"]], success: "Server registered", onSuccess: () => onOpenChange(false) });
  const hint = { none: "", api_key: '{"api_key": "…", "header": "X-API-Key"}', oauth2: '{"token_url": "…", "client_id": "…", "client_secret": "…", "scope": "…"}',
    service: '{"headers": {"X-Service-Token": "…"}}' }[f.auth_type];
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Register MCP server" description="Credentials are write-only and encrypted at rest (Fernet). STDIO commands are restricted to an allowlist.">
      <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined); }}>
        <Field label="Name"><Input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Transport"><Select value={f.transport} onChange={(e) => setF({ ...f, transport: e.target.value })}><option value="stdio">STDIO</option><option value="streamable_http">Streamable HTTP</option></Select></Field>
        {f.transport === "stdio" ? <>
          <Field label="Command" hint="python, uv, uvx, npx, node, docker"><Input value={f.command} onChange={(e) => setF({ ...f, command: e.target.value })} /></Field>
          <Field label="Arguments"><Input placeholder="/path/to/server.py" value={f.args} onChange={(e) => setF({ ...f, args: e.target.value })} /></Field>
        </> : <Field label="URL" className="sm:col-span-2"><Input placeholder="http://127.0.0.1:8765/mcp" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} /></Field>}
        <Field label="Authentication"><Select value={f.auth_type} onChange={(e) => setF({ ...f, auth_type: e.target.value })}>
          <option value="none">None</option><option value="api_key">API key</option><option value="oauth2">OAuth 2.0 (client credentials)</option><option value="service">Service credentials</option></Select></Field>
        <Field label="Timeout (s)"><Input type="number" value={f.timeout_s} onChange={(e) => setF({ ...f, timeout_s: Number(e.target.value) })} /></Field>
        {f.auth_type !== "none" && <Field label="Credentials (JSON)" hint={hint} className="sm:col-span-2"><Textarea value={f.credentials} onChange={(e) => setF({ ...f, credentials: e.target.value })} /></Field>}
        <Field label="Description" className="sm:col-span-2"><Input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        <div className="flex justify-end gap-2 sm:col-span-2"><Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button type="submit" loading={create.isPending}>Register</Button></div>
      </form>
    </Dialog>
  );
}

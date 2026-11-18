import { Activity, KeyRound, ShieldCheck, Trash2, UserPlus } from "lucide-react";
import { useState } from "react";
import { Empty, JsonView, Loading, PageHeader, StatusBadge } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/form";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAction, useApi, type Page } from "@/hooks/api";
import { fmt } from "@/lib";
import { del, get, post, put } from "@/services/api";
import { useAuth } from "@/stores/auth";

type Ev = { id: string; chain_index: number; created_at: string; actor_email: string | null; action: string; category: string; resource_type: string | null; resource_id: string | null;
  outcome: string; severity: string; ip: string | null; details: Record<string, unknown>; hash: string; prev_hash: string; request_id: string | null };
type Role = { id: string; name: string; description: string; permissions: string[] };
type User = { id: string; email: string; full_name: string; is_active: boolean; roles: string[]; teams: string[]; last_login_at: string | null };
const CATS = ["authentication", "agent_config", "prompt_change", "tool_execution", "mcp_connection", "model_invocation", "evaluation", "permission_change", "deployment", "workflow_approval", "data", "audit"];

export default function SecurityAuditPage() {
  const can = useAuth((s) => s.can);
  return (
    <>
      <PageHeader icon={Activity} title="Security & Audit" description="Append-only, hash-chained audit trail; RBAC roles and permissions; users, API keys and sessions." />
      <Tabs defaultValue={can("audit:read") ? "audit" : "roles"}>
        <TabsList>{can("audit:read") && <TabsTrigger value="audit">Audit trail</TabsTrigger>}<TabsTrigger value="roles">Roles & permissions</TabsTrigger>
          {can("security:manage") && <TabsTrigger value="users">Users</TabsTrigger>}<TabsTrigger value="keys">API keys</TabsTrigger><TabsTrigger value="sessions">My sessions</TabsTrigger></TabsList>
        {can("audit:read") && <TabsContent value="audit"><Audit /></TabsContent>}
        <TabsContent value="roles"><Roles /></TabsContent>
        {can("security:manage") && <TabsContent value="users"><Users /></TabsContent>}
        <TabsContent value="keys"><Keys /></TabsContent>
        <TabsContent value="sessions"><Sessions /></TabsContent>
      </Tabs>
    </>
  );
}

function Audit() {
  const can = useAuth((s) => s.can);
  const [f, setF] = useState({ category: "", outcome: "", actor: "", action: "" });
  const [offset, setOffset] = useState(0);
  const [sel, setSel] = useState<Ev | null>(null);
  const qs = new URLSearchParams({ limit: "50", offset: String(offset), ...Object.fromEntries(Object.entries(f).filter(([, v]) => v)) });
  const q = useApi<Page<Ev>>(["audit", qs.toString()], `/audit/events?${qs}`);
  const verifyGet = useAction(() => get<{ valid: boolean; checked: number; total: number; head_hash?: string; reason?: string; broken_at?: number }>("/audit/verify"),
    { invalidate: [["audit"]] });
  return (
    <div className="space-y-3">
      <Card><CardContent className="flex flex-wrap items-end gap-2 p-4">
        <Field label="Category"><Select value={f.category} onChange={(e) => { setOffset(0); setF({ ...f, category: e.target.value }); }}><option value="">All</option>{CATS.map((c) => <option key={c}>{c}</option>)}</Select></Field>
        <Field label="Outcome"><Select value={f.outcome} onChange={(e) => { setOffset(0); setF({ ...f, outcome: e.target.value }); }}><option value="">All</option>{["success", "failure", "denied"].map((c) => <option key={c}>{c}</option>)}</Select></Field>
        <Field label="Action prefix"><Input value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })} placeholder="tool." /></Field>
        <Field label="Actor"><Input value={f.actor} onChange={(e) => setF({ ...f, actor: e.target.value })} placeholder="email" /></Field>
        {can("audit:verify") && <Button variant="outline" onClick={() => verifyGet.mutate(undefined)} loading={verifyGet.isPending}><ShieldCheck /> Verify chain integrity</Button>}
        {verifyGet.data && (verifyGet.data.valid ? <Badge variant="success">Chain valid · {verifyGet.data.checked} events · head {verifyGet.data.head_hash?.slice(0, 12)}…</Badge>
          : <Badge variant="danger">TAMPERING DETECTED at #{verifyGet.data.broken_at}: {verifyGet.data.reason}</Badge>)}
      </CardContent></Card>
      <Card><CardContent className="p-0">{q.isLoading ? <Loading /> : !q.data?.items.length ? <div className="p-4"><Empty title="No matching events" /></div> : (
        <Table><THead><TR><TH>#</TH><TH>When</TH><TH>Action</TH><TH>Category</TH><TH>Actor</TH><TH>Outcome</TH><TH>Severity</TH><TH>Hash</TH></TR></THead>
          <TBody>{q.data.items.map((e) => (
            <TR key={e.id} className="cursor-pointer" onClick={() => setSel(e)}><TD className="tabular-nums text-muted-foreground">{e.chain_index}</TD><TD className="whitespace-nowrap">{fmt.ago(e.created_at)}</TD>
              <TD className="font-mono text-xs">{e.action}</TD><TD className="text-xs">{e.category}</TD><TD className="text-xs">{e.actor_email ?? "system"}</TD>
              <TD><StatusBadge status={e.outcome} /></TD><TD><Badge variant={e.severity === "critical" ? "danger" : e.severity === "warning" ? "warning" : "muted"}>{e.severity}</Badge></TD>
              <TD className="font-mono text-[10px] text-muted-foreground">{e.hash.slice(0, 10)}…</TD></TR>))}</TBody></Table>)}
      </CardContent></Card>
      {q.data && <div className="flex items-center justify-between text-xs text-muted-foreground"><span>{q.data.total} events</span>
        <div className="flex gap-2"><Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</Button>
          <Button size="sm" variant="outline" disabled={offset + 50 >= q.data.total} onClick={() => setOffset(offset + 50)}>Next</Button></div></div>}
      {sel && <Dialog open onOpenChange={(o) => !o && setSel(null)} title={sel.action} description={`#${sel.chain_index} · ${new Date(sel.created_at).toLocaleString()} · request ${sel.request_id ?? "—"}`}>
        <JsonView value={{ actor: sel.actor_email, ip: sel.ip, resource: `${sel.resource_type}/${sel.resource_id}`, outcome: sel.outcome, details: sel.details, prev_hash: sel.prev_hash, hash: sel.hash }} />
        <p className="mt-2 text-[11px] text-muted-foreground">hash = SHA-256(prev_hash ‖ canonical(event)). Secrets and prompt bodies are redacted / hashed before storage.</p>
      </Dialog>}
    </div>
  );
}

function Roles() {
  const q = useApi<Role[]>(["roles"], "/security/roles");
  if (q.isLoading) return <Loading />;
  return <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{q.data?.map((r) => (
    <Card key={r.id}><CardHeader><CardTitle>{r.name}</CardTitle><CardDescription>{r.description} · {r.permissions.length} permissions</CardDescription></CardHeader>
      <CardContent className="flex flex-wrap gap-1">{r.permissions.map((p) => <Badge key={p} variant="muted" className="font-mono">{p}</Badge>)}</CardContent></Card>))}</div>;
}

function Users() {
  const users = useApi<User[]>(["users"], "/security/users");
  const roles = useApi<Role[]>(["roles"], "/security/roles");
  const [open, setOpen] = useState(false);
  const [nu, setNu] = useState({ email: "", full_name: "", password: "", role: "Developer" });
  const setRole = useAction(({ u, role }: { u: User; role: string }) => put<User>(`/security/users/${u.id}`, { roles: [role] }), { invalidate: [["users"]], success: "Roles updated (audited)" });
  const create = useAction(() => post("/security/users", { email: nu.email, full_name: nu.full_name, password: nu.password, roles: [nu.role] }),
    { invalidate: [["users"]], success: "User created", onSuccess: () => setOpen(false) });
  return (
    <Card><CardHeader className="flex-row items-center justify-between"><CardTitle>Users</CardTitle><Button size="sm" onClick={() => setOpen(true)}><UserPlus /> Add user</Button></CardHeader>
      <CardContent className="p-0"><Table><THead><TR><TH>User</TH><TH>Role</TH><TH>Teams</TH><TH>Last login</TH><TH>Status</TH></TR></THead>
        <TBody>{users.data?.map((u) => (
          <TR key={u.id}><TD><div className="font-medium">{u.full_name}</div><div className="text-xs text-muted-foreground">{u.email}</div></TD>
            <TD><Select aria-label={`Role for ${u.email}`} className="h-8 w-52 text-xs" value={u.roles[0]} onChange={(e) => setRole.mutate({ u, role: e.target.value })}>{roles.data?.map((r) => <option key={r.id}>{r.name}</option>)}</Select></TD>
            <TD className="text-xs">{u.teams.join(", ") || "—"}</TD><TD className="text-xs text-muted-foreground">{fmt.ago(u.last_login_at)}</TD><TD><StatusBadge status={u.is_active ? "active" : "inactive"} /></TD></TR>))}</TBody></Table></CardContent>
      <Dialog open={open} onOpenChange={setOpen} title="Add user" description="Passwords: ≥12 chars, mixed case, digit. Stored with Argon2id.">
        <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined); }}>
          <Field label="Email"><Input required type="email" value={nu.email} onChange={(e) => setNu({ ...nu, email: e.target.value })} /></Field>
          <Field label="Full name"><Input value={nu.full_name} onChange={(e) => setNu({ ...nu, full_name: e.target.value })} /></Field>
          <Field label="Initial password"><Input required type="password" autoComplete="new-password" value={nu.password} onChange={(e) => setNu({ ...nu, password: e.target.value })} /></Field>
          <Field label="Role"><Select value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value })}>{roles.data?.map((r) => <option key={r.id}>{r.name}</option>)}</Select></Field>
          <Button type="submit" loading={create.isPending}>Create user</Button>
        </form>
      </Dialog>
    </Card>
  );
}

function Keys() {
  const me = useAuth((s) => s.me);
  const keys = useApi<{ id: string; name: string; prefix: string; scopes: string[]; created_at: string; last_used_at: string | null; revoked_at: string | null }[]>(["keys"], "/security/api-keys");
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["agents:read", "agents:execute"]);
  const [created, setCreated] = useState<string | null>(null);
  const create = useAction(() => post<{ key: string }>("/security/api-keys", { name, scopes }), { invalidate: [["keys"]], onSuccess: (r) => { setCreated(r.key); setName(""); } });
  const revoke = useAction((id: string) => del(`/security/api-keys/${id}`), { invalidate: [["keys"]], success: "Key revoked" });
  return (
    <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
      <Card><CardHeader><CardTitle className="flex items-center gap-2"><KeyRound className="size-4" /> New API key</CardTitle><CardDescription>Scopes can only narrow your own permissions. Use header <code>X-API-Key</code>.</CardDescription></CardHeader>
        <CardContent className="space-y-2"><Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <div className="flex max-h-40 flex-wrap gap-1 overflow-y-auto">{me?.permissions.map((p) => (
            <button key={p} type="button" onClick={() => setScopes((s) => (s.includes(p) ? s.filter((x) => x !== p) : [...s, p]))}
              className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${scopes.includes(p) ? "border-primary bg-accent" : ""}`}>{p}</button>))}</div>
          <Button onClick={() => create.mutate(undefined)} disabled={name.length < 2} loading={create.isPending}>Create key</Button>
          {created && <div className="rounded-md border border-warning/50 bg-warning/5 p-2 text-xs"><div className="font-medium">Copy now — shown once:</div><code className="break-all">{created}</code></div>}
        </CardContent></Card>
      <Card><CardContent className="p-0">{!keys.data?.length ? <div className="p-4"><Empty title="No API keys" /></div> : (
        <Table><THead><TR><TH>Name</TH><TH>Prefix</TH><TH>Scopes</TH><TH>Last used</TH><TH>Status</TH><TH /></TR></THead>
          <TBody>{keys.data.map((k) => <TR key={k.id}><TD>{k.name}</TD><TD className="font-mono text-xs">{k.prefix}…</TD><TD className="max-w-xs text-[11px]">{k.scopes.join(", ") || "owner permissions"}</TD>
            <TD className="text-xs">{fmt.ago(k.last_used_at)}</TD><TD><StatusBadge status={k.revoked_at ? "rejected" : "active"} /></TD>
            <TD>{!k.revoked_at && <Button size="icon" variant="ghost" aria-label="Revoke key" onClick={() => revoke.mutate(k.id)}><Trash2 /></Button>}</TD></TR>)}</TBody></Table>)}
      </CardContent></Card>
    </div>
  );
}

function Sessions() {
  const q = useApi<{ id: string; created_at: string; expires_at: string; ip: string | null; user_agent: string | null }[]>(["sessions"], "/auth/sessions");
  const revoke = useAction((id: string) => del(`/auth/sessions/${id}`), { invalidate: [["sessions"]], success: "Session revoked" });
  return <Card><CardHeader><CardTitle>Active sessions</CardTitle><CardDescription>Refresh tokens rotate on every use; replaying an old token revokes the whole session family.</CardDescription></CardHeader>
    <CardContent className="p-0"><Table><THead><TR><TH>Created</TH><TH>Expires</TH><TH>IP</TH><TH>Client</TH><TH /></TR></THead>
      <TBody>{q.data?.map((s) => <TR key={s.id}><TD>{fmt.ago(s.created_at)}</TD><TD className="text-xs">{new Date(s.expires_at).toLocaleString()}</TD><TD className="text-xs">{s.ip}</TD>
        <TD className="max-w-xs truncate text-xs">{s.user_agent}</TD><TD><Button size="sm" variant="ghost" onClick={() => revoke.mutate(s.id)}>Revoke</Button></TD></TR>)}</TBody></Table></CardContent></Card>;
}

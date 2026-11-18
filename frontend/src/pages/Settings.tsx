import { HeartPulse, Plus, Send, Settings as SettingsIcon } from "lucide-react";
import { useState } from "react";
import { Kv, Loading, MockBadge, PageHeader, StatusBadge } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { Switch } from "@/components/ui/switch";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useAction, useApi } from "@/hooks/api";
import { fmt } from "@/lib";
import { get, post, put } from "@/services/api";
import { useAuth } from "@/stores/auth";

type Providers = { builtin: { name: string; kind: string; configured: boolean; is_mock: boolean; circuit: { state: string } }[];
  organization: { id: string; name: string; kind: string; base_url: string | null; has_api_key: boolean; is_enabled: boolean }[];
  models: { id: string; model_name: string; display_name: string; context_window: number; input_cost_per_1k: number; output_cost_per_1k: number }[];
  kinds: string[]; default: { provider: string; model: string }; embedding: { provider: string; model: string; dim: number } };
type SettingsT = { env: string; feature_flags: Record<string, boolean>; limits: Record<string, number>; http_tool_allowed_hosts: string[]; jobs_mode: string; otel_enabled: boolean;
  oidc: { configured: boolean; issuer: string | null }; embedding: { provider: string; model: string; dim: number } };

export default function SettingsPage() {
  const can = useAuth((s) => s.can);
  const me = useAuth((s) => s.me);
  const p = useApi<Providers>(["providers"], "/providers");
  const s = useApi<SettingsT>(["settings"], "/settings");
  const [health, setHealth] = useState<Record<string, { status: string; detail?: string; models?: string[] }>>({});
  const check = useAction((name: string) => get<{ status: string; detail?: string; models?: string[] }>(`/providers/${name}/health`).then((h) => ({ name, h })),
    { onSuccess: ({ name, h }) => setHealth((x) => ({ ...x, [name]: h })) });
  const flags = useAction((f: Record<string, boolean>) => put("/settings/feature-flags", { flags: f }), { invalidate: [["settings"]], success: "Feature flag updated (audited)" });
  const [np, setNp] = useState({ name: "", kind: "openai_compatible", base_url: "", api_key: "" });
  const addProv = useAction(() => post("/providers", { name: np.name, kind: np.kind, base_url: np.base_url || null, api_key: np.api_key || null }),
    { invalidate: [["providers"]], success: "Provider registered (key encrypted at rest)", onSuccess: () => setNp({ name: "", kind: "openai_compatible", base_url: "", api_key: "" }) });
  const [pg, setPg] = useState({ provider: "ollama", model: "llama3.2:3b", prompt: "In one sentence, what is a circuit breaker?" });
  const play = useAction(() => post<{ content: string; is_mock: boolean; latency_ms: number; tokens_in: number; tokens_out: number; cost_usd: number }>("/providers/playground", pg));
  if (p.isLoading || s.isLoading) return <Loading />;
  return (
    <>
      <PageHeader icon={SettingsIcon} title="Settings" description="Model providers, connectivity, feature flags and platform limits. Credentials are never returned to the browser." />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card><CardHeader><CardTitle>Model providers</CardTitle><CardDescription>Default: {p.data!.default.provider}/{p.data!.default.model} · embeddings: {p.data!.embedding.provider}/{p.data!.embedding.model} ({p.data!.embedding.dim}d)</CardDescription></CardHeader>
          <CardContent className="p-0"><Table><THead><TR><TH>Provider</TH><TH>Configured</TH><TH>Circuit</TH><TH>Health</TH></TR></THead>
            <TBody>{p.data!.builtin.map((b) => (
              <TR key={b.name}><TD><div className="flex items-center gap-2 font-medium">{b.name}{b.is_mock && <MockBadge mock />}</div></TD>
                <TD>{b.configured ? <Badge variant="success">yes</Badge> : <Badge variant="muted" title="Set the API key via environment variable">no key</Badge>}</TD>
                <TD><StatusBadge status={b.circuit.state} /></TD>
                <TD><div className="flex items-center gap-2"><Button size="sm" variant="outline" onClick={() => check.mutate(b.name)}><HeartPulse /> Check</Button>
                  {health[b.name] && <span className="text-xs"><StatusBadge status={health[b.name].status} /> {health[b.name].models?.slice(0, 3).join(", ") ?? health[b.name].detail}</span>}</div></TD></TR>))}
              {p.data!.organization.map((o) => <TR key={o.id}><TD className="font-medium">{o.name} <Badge variant="outline">{o.kind}</Badge></TD><TD>{o.has_api_key ? <Badge variant="success">key stored</Badge> : "—"}</TD><TD /><TD>
                <Button size="sm" variant="outline" onClick={() => check.mutate(o.name)}><HeartPulse /> Check</Button>{health[o.name] && <span className="ml-2 text-xs"><StatusBadge status={health[o.name].status} /></span>}</TD></TR>)}</TBody></Table></CardContent></Card>
        <Card><CardHeader><CardTitle>Playground</CardTitle><CardDescription>Single completion to verify a provider end-to-end.</CardDescription></CardHeader><CardContent className="space-y-2">
          <div className="grid grid-cols-2 gap-2"><Field label="Provider"><Select value={pg.provider} onChange={(e) => setPg({ ...pg, provider: e.target.value })}>{p.data!.builtin.map((b) => <option key={b.name}>{b.name}</option>)}{p.data!.organization.map((o) => <option key={o.id}>{o.name}</option>)}</Select></Field>
            <Field label="Model"><Input value={pg.model} onChange={(e) => setPg({ ...pg, model: e.target.value })} /></Field></div>
          <Textarea className="font-sans" value={pg.prompt} onChange={(e) => setPg({ ...pg, prompt: e.target.value })} />
          <Button onClick={() => play.mutate(undefined)} loading={play.isPending} disabled={!can("prompts:test")}><Send /> Send</Button>
          {play.data && <div className="rounded-md bg-muted p-3 text-sm"><div className="mb-1 flex items-center gap-2 text-xs"><MockBadge mock={play.data.is_mock} />{fmt.ms(play.data.latency_ms)} · {play.data.tokens_in}+{play.data.tokens_out} tok · {fmt.usd(play.data.cost_usd)}</div>{play.data.content}</div>}
        </CardContent></Card>
        {can("providers:manage") && <Card><CardHeader><CardTitle>Register provider</CardTitle><CardDescription>OpenAI-compatible endpoints (vLLM, NIM, LM Studio…), or hosted APIs.</CardDescription></CardHeader>
          <CardContent><form className="grid gap-2 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); addProv.mutate(undefined); }}>
            <Field label="Name (slug)"><Input required pattern="[a-z0-9_\-]+" value={np.name} onChange={(e) => setNp({ ...np, name: e.target.value })} /></Field>
            <Field label="Kind"><Select value={np.kind} onChange={(e) => setNp({ ...np, kind: e.target.value })}>{p.data!.kinds.map((k) => <option key={k}>{k}</option>)}</Select></Field>
            <Field label="Base URL"><Input placeholder="http://localhost:8000/v1" value={np.base_url} onChange={(e) => setNp({ ...np, base_url: e.target.value })} /></Field>
            <Field label="API key (write-only)"><Input type="password" autoComplete="off" value={np.api_key} onChange={(e) => setNp({ ...np, api_key: e.target.value })} /></Field>
            <Button type="submit" className="w-fit" loading={addProv.isPending}><Plus /> Register</Button></form></CardContent></Card>}
        <Card><CardHeader><CardTitle>Feature flags</CardTitle><CardDescription>Runtime toggles for experimental functionality (requires settings:manage).</CardDescription></CardHeader>
          <CardContent className="space-y-2">{Object.entries(s.data!.feature_flags).map(([k, v]) => (
            <label key={k} className="flex items-center justify-between text-sm"><span className="font-mono text-xs">{k}</span><Switch checked={v} disabled={!can("settings:manage")} label={k} onCheckedChange={(nv) => flags.mutate({ [k]: nv })} /></label>))}</CardContent></Card>
        <Card><CardHeader><CardTitle>Platform limits & runtime</CardTitle></CardHeader><CardContent>
          <Kv items={[["Environment", s.data!.env], ["Jobs", s.data!.jobs_mode], ["OpenTelemetry export", s.data!.otel_enabled ? "enabled" : "disabled"],
            ["OIDC", s.data!.oidc.configured ? s.data!.oidc.issuer : "not configured (integration-ready)"], ["HTTP tool allowlist", s.data!.http_tool_allowed_hosts.join(", ")],
            ...Object.entries(s.data!.limits).map(([k, v]) => [k.replaceAll("_", " "), String(v)] as [string, string])]} /></CardContent></Card>
        <Card><CardHeader><CardTitle>Profile</CardTitle></CardHeader><CardContent><Kv items={[["Name", me?.full_name], ["Email", me?.email], ["Organization", me?.organization?.name],
          ["Roles", me?.roles.join(", ")], ["Teams", me?.teams.map((t) => t.name).join(", ") || "—"], ["Permissions", `${me?.permissions.length ?? 0}`]]} /></CardContent></Card>
      </div>
      <p className="mt-4 text-center text-[11px] text-muted-foreground">Models listed: {p.data!.models.map((m) => m.display_name || m.model_name).join(" · ")}</p>
    </>
  );
}

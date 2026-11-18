import { Boxes } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/form";
import { errorMessage, post } from "@/services/api";
import { type Me, useAuth } from "@/stores/auth";

const DEMO = ["admin@aiecc.local", "aieng@aiecc.local", "dev@aiecc.local", "security@aiecc.local", "auditor@aiecc.local", "viewer@aiecc.local"];

export default function Login() {
  const [email, setEmail] = useState("admin@aiecc.local");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { setTokens, setMe } = useAuth();
  const nav = useNavigate();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await post<{ access_token: string; refresh_token: string; user: Me }>("/auth/login", { email, password });
      setTokens(r.access_token, r.refresh_token);
      setMe(r.user);
      nav("/");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-full lg:grid-cols-2">
      <div className="hidden flex-col justify-between bg-brand p-10 text-white lg:flex">
        <div className="flex items-center gap-2.5"><Boxes className="size-6" aria-hidden /><span className="font-semibold tracking-wide">AIECC</span></div>
        <div>
          <h1 className="text-3xl font-semibold leading-tight">AI Engineering<br />Command Center</h1>
          <p className="mt-3 max-w-md text-sm text-white/70">Design, run, evaluate, secure and observe AI agents, MCP tools, prompts, RAG pipelines and workflows — with bounded autonomy, verified citations and a tamper-evident audit trail.</p>
        </div>
        <div className="text-xs text-white/50">Local-first · cloud-ready · works without paid API keys</div>
      </div>
      <div className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="w-full max-w-sm space-y-4" aria-label="Sign in">
          <div>
            <h2 className="text-lg font-semibold">Sign in</h2>
            <p className="text-xs text-muted-foreground">JWT access tokens (15 min) with rotating refresh tokens.</p>
          </div>
          <Field label="Email"><Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
          <Field label="Password"><Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
          {error && <div role="alert" className="rounded-md bg-danger/10 p-2 text-xs text-danger">{error}</div>}
          <Button type="submit" className="w-full" loading={busy}>Sign in</Button>
          <div className="rounded-lg border border-dashed p-3 text-[11px] text-muted-foreground">
            <div className="mb-1 font-medium text-foreground">Demo accounts (DEMO DATA)</div>
            Password is in <code>README.md</code> / <code>.env.example</code> (<code>AIECC_DEMO_PASSWORD</code>). Roles:
            <div className="mt-1.5 flex flex-wrap gap-1">
              {DEMO.map((d) => <button type="button" key={d} onClick={() => setEmail(d)} className="rounded bg-muted px-1.5 py-0.5 hover:bg-accent">{d.split("@")[0]}</button>)}
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}

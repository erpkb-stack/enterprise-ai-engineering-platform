import { CheckCircle2, CircleAlert, GitFork, Route, ShieldCheck, ShieldX, Sparkles, Wrench } from "lucide-react";
import { useState } from "react";
import { Kv, MockBadge, ScoreBar, StatusBadge } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn, fmt } from "@/lib";
import type { Execution, Grounding, Step } from "@/types";

export function GroundingView({ g, passages, files }: { g: Grounding | null | undefined; passages?: string[]; files?: string[] }) {
  const [focus, setFocus] = useState<number | null>(null);
  if (!g) return <p className="text-xs text-muted-foreground">No retrieved passages — grounding not applicable.</p>;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-4">
        <div><div className="text-[11px] text-muted-foreground">Groundedness</div><ScoreBar value={g.groundedness} /></div>
        <div><div className="text-[11px] text-muted-foreground">Citation precision</div><ScoreBar value={g.citation_precision} /></div>
        <div><div className="text-[11px] text-muted-foreground">Citation coverage</div><ScoreBar value={g.citation_coverage} /></div>
        <div><div className="text-[11px] text-muted-foreground">Hallucination rate</div><ScoreBar value={g.hallucination_rate == null ? null : 1 - g.hallucination_rate} label="1 - hallucination rate" /></div>
      </div>
      <p className="text-[11px] text-muted-foreground">Method <code>{g.method}</code> · threshold {g.threshold} · {g.valid_citations}/{g.total_citations} citations verified · {g.nonexistent_citations} non-existent</p>
      <ul className="space-y-1.5">
        {g.claims.map((c, i) => (
          <li key={i} className={cn("rounded-md border p-2 text-xs", c.supported ? "border-success/30" : "border-danger/40 bg-danger/5")}>
            <div className="flex items-start gap-2">
              {c.supported ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success" aria-label="supported" /> : <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-danger" aria-label="unsupported" />}
              <span className="flex-1">{c.claim}</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-1 pl-5">
              {c.valid_citations.map((n) => <button key={n} onClick={() => setFocus(n)}><Badge variant="success">[{n}] verified</Badge></button>)}
              {c.invalid_citations.map((x) => <button key={x.citation} onClick={() => setFocus(x.citation)}><Badge variant="danger" title={x.reason}>[{x.citation}] {x.reason}</Badge></button>)}
              {!c.citations.length && <Badge variant="muted">uncited</Badge>}
              {c.best_passage && <Badge variant="outline">best support [{c.best_passage}] {fmt.pct(c.best_support, 0)}</Badge>}
            </div>
          </li>
        ))}
      </ul>
      {passages && focus != null && passages[focus - 1] && (
        <div className="rounded-md border border-primary/40 bg-accent/40 p-2 text-xs">
          <div className="mb-1 font-medium">Passage [{focus}] {files?.[focus - 1] && <span className="text-muted-foreground">— {files[focus - 1]}</span>}</div>
          <p className="whitespace-pre-wrap">{passages[focus - 1]}</p>
        </div>
      )}
    </div>
  );
}

function StepIcon({ s }: { s: Step }) {
  if (s.type === "route") return <Route className="size-4 text-info" />;
  if (s.type === "delegate") return <GitFork className="size-4 text-info" />;
  if (s.action === "tool") return s.policy?.allowed === false ? <ShieldX className="size-4 text-danger" /> : <Wrench className="size-4 text-primary" />;
  if (s.action === "final") return <Sparkles className="size-4 text-success" />;
  return <ShieldCheck className="size-4 text-muted-foreground" />;
}

export function TraceTimeline({ steps }: { steps: Step[] }) {
  return (
    <ol className="relative space-y-3 border-l pl-5">
      {steps.map((s, i) => (
        <li key={i} className="relative">
          <span className="absolute -left-[29px] top-0 grid size-6 place-items-center rounded-full border bg-card"><StepIcon s={s} /></span>
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="font-semibold">Step {s.n}</span>
            <Badge variant="muted">{s.type === "llm" ? s.action : s.type}</Badge>
            {s.tool && <Badge variant="outline" className="font-mono">{s.tool}</Badge>}
            {s.policy && <Badge variant={s.policy.allowed ? "success" : "danger"} title={s.policy.reason}>policy: {s.policy.allowed ? "allowed" : `denied (${s.policy.rule})`}</Badge>}
            {s.selected && <Badge variant="info">→ {s.selected}</Badge>}
            {s.agent && <Badge variant="info">delegated to {s.agent}</Badge>}
            {s.status && <StatusBadge status={s.status} />}
            {s.fallback_used && <Badge variant="warning">fallback model</Badge>}
            {!!s.attempts?.length && <Badge variant="warning">{s.attempts.length} retr{s.attempts.length > 1 ? "ies" : "y"}</Badge>}
            {!!s.injection_flags?.length && <Badge variant="danger">injection flagged</Badge>}
            <span className="ml-auto text-muted-foreground">{s.provider && `${s.provider}/${s.model} · `}{fmt.ms((s.step_latency_ms as number) ?? s.latency_ms)}{s.tokens_in != null && ` · ${s.tokens_in}+${s.tokens_out} tok`}</span>
          </div>
          {s.thought && <p className="mt-1 text-xs italic text-muted-foreground">“{s.thought}”</p>}
          {s.scores && <p className="mt-1 text-[11px] text-muted-foreground">router scores: {Object.entries(s.scores).map(([k, v]) => `${k} ${v}`).join(" · ")}</p>}
          {s.arguments && <pre className="mt-1 overflow-x-auto rounded bg-muted px-2 py-1 font-mono text-[11px]">{JSON.stringify(s.arguments)}</pre>}
          {s.observation && <details className="mt-1"><summary className="cursor-pointer text-[11px] text-muted-foreground">observation (untrusted tool output)</summary>
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-muted p-2 font-mono text-[11px]">{s.observation}</pre></details>}
        </li>
      ))}
    </ol>
  );
}

export function ExecutionView({ ex, child }: { ex: Execution; child?: Execution | null }) {
  const leaf = child ?? ex;
  return (
    <div className="grid gap-4 xl:grid-cols-5">
      <div className="min-w-0 space-y-4 xl:col-span-3">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="flex items-center gap-2">Answer <MockBadge mock={ex.is_mock} /></CardTitle>
            <StatusBadge status={ex.status} />
          </CardHeader>
          <CardContent>
            {ex.output ? <p className="whitespace-pre-wrap text-sm leading-relaxed">{ex.output}</p> : <p className="text-sm text-danger">{ex.error ?? "No output"}</p>}
            {!!leaf.citations?.length && (
              <div className="mt-3 flex flex-wrap gap-1.5">{leaf.citations.map((c) => <Badge key={c.n} variant="outline">[{c.n}] {c.filename} #chunk{c.ordinal}</Badge>)}</div>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Citation verification</CardTitle></CardHeader>
          <CardContent><GroundingView g={leaf.evaluation?.grounding} passages={leaf.evaluation?.passages} files={leaf.citations?.map((c) => c.filename)} /></CardContent>
        </Card>
      </div>
      <div className="min-w-0 space-y-4 xl:col-span-2">
        <Card>
          <CardHeader><CardTitle>Execution</CardTitle></CardHeader>
          <CardContent>
            <Kv items={[["Execution", <code className="text-[11px]">{ex.id}</code>], ["Latency", fmt.ms(ex.latency_ms)],
              ["Tokens", `${ex.tokens_in} in / ${ex.tokens_out} out`], ["Cost", fmt.usd(ex.cost_usd)], ["Model", `${ex.provider ?? "—"}/${ex.model ?? "—"}`],
              ["Trace id", <code className="text-[11px]">{ex.trace_id ?? "—"}</code>], ["Policy denials", String(leaf.evaluation?.policy_denials ?? 0)],
              ["Citation requirement", leaf.evaluation?.citation_requirement_met == null ? "—" : leaf.evaluation.citation_requirement_met ? "met" : "NOT met"]]} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Trace</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <TraceTimeline steps={ex.steps} />
            {child && <><div className="text-xs font-medium text-muted-foreground">Delegated execution</div><TraceTimeline steps={child.steps} /></>}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

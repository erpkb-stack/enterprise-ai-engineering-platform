import { AlertTriangle, Inbox, Loader2 } from "lucide-react";
import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib";
import { errorMessage } from "@/services/api";

export function PageHeader({ title, description, actions, icon: Icon }: { title: string; description?: string; actions?: React.ReactNode; icon?: React.ElementType }) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="flex items-start gap-3">
        {Icon && <div className="rounded-lg bg-brand p-2 text-white"><Icon className="size-5" aria-hidden /></div>}
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          {description && <p className="mt-0.5 max-w-3xl text-sm text-muted-foreground">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function StatCard({ label, value, hint, icon: Icon, tone }: { label: string; value: React.ReactNode; hint?: React.ReactNode; icon?: React.ElementType; tone?: "danger" | "success" | "warning" }) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{label}</span>
        {Icon && <Icon className="size-4" aria-hidden />}
      </div>
      <div className={cn("mt-2 text-2xl font-semibold tabular-nums", tone === "danger" && "text-danger", tone === "success" && "text-success", tone === "warning" && "text-warning")}>{value}</div>
      {hint && <div className="mt-1 text-[11px] text-muted-foreground">{hint}</div>}
    </Card>
  );
}

export function Loading({ label = "Loading…", className }: { label?: string; className?: string }) {
  return (
    <div role="status" className={cn("flex items-center justify-center gap-2 p-8 text-sm text-muted-foreground", className)}>
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label}
    </div>
  );
}

export function Empty({ title, description, action }: { title: string; description?: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed p-10 text-center">
      <Inbox className="size-6 text-muted-foreground" aria-hidden />
      <div className="text-sm font-medium">{title}</div>
      {description && <p className="max-w-md text-xs text-muted-foreground">{description}</p>}
      {action}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-3 rounded-xl border border-danger/40 bg-danger/5 p-4 text-sm">
      <AlertTriangle className="mt-0.5 size-4 text-danger" aria-hidden />
      <div className="flex-1">
        <div className="font-medium text-danger">Something went wrong</div>
        <div className="mt-0.5 text-xs text-muted-foreground">{errorMessage(error)}</div>
      </div>
      {onRetry && <Button size="sm" variant="outline" onClick={onRetry}>Retry</Button>}
    </div>
  );
}

export function MockBadge({ mock, className }: { mock?: boolean | null; className?: string }) {
  if (mock == null) return null;
  return mock ? (
    <Badge variant="warning" className={className} title="Deterministic mock provider — not a language model">MOCK</Badge>
  ) : (
    <Badge variant="success" className={className} title="Real model provider">LIVE MODEL</Badge>
  );
}

const STATUS: Record<string, "success" | "danger" | "warning" | "info" | "muted" | "default"> = {
  succeeded: "success", completed: "success", active: "success", connected: "success", ok: "success", approved: "success",
  defended: "success", healthy: "success", success: "success", ready: "success", allowed: "success",
  failed: "danger", error: "danger", vulnerable: "danger", denied: "danger", rejected: "danger", unhealthy: "danger", open: "danger",
  timeout: "warning", max_steps_exceeded: "warning", waiting_approval: "warning", paused: "warning", in_review: "warning",
  degraded: "warning", half_open: "warning", cancelled: "muted", inactive: "muted", archived: "muted", draft: "muted",
  disconnected: "muted", skipped: "muted", queued: "info", running: "info", closed: "success",
};

export function StatusBadge({ status }: { status?: string | null }) {
  if (!status) return null;
  return <Badge variant={STATUS[status] ?? "default"}>{status.replaceAll("_", " ")}</Badge>;
}

export function JsonView({ value, className }: { value: unknown; className?: string }) {
  return (
    <pre className={cn("max-h-96 overflow-auto rounded-lg bg-muted p-3 font-mono text-[11px] leading-relaxed scrollbar-thin", className)}>
      {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function Kv({ items }: { items: [string, React.ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
      {items.map(([k, v]) => (
        <React.Fragment key={k}>
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="min-w-0 break-words font-medium">{v}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

export function ScoreBar({ value, label }: { value?: number | null; label?: string }) {
  const v = value == null ? null : Math.max(0, Math.min(1, value));
  const tone = v == null ? "bg-muted" : v >= 0.8 ? "bg-success" : v >= 0.5 ? "bg-warning" : "bg-danger";
  return (
    <div className="flex items-center gap-2" title={label}>
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
        {v != null && <div className={cn("h-full", tone)} style={{ width: `${v * 100}%` }} />}
      </div>
      <span className="w-10 text-right text-[11px] tabular-nums text-muted-foreground">{v == null ? "n/a" : `${Math.round(v * 100)}%`}</span>
    </div>
  );
}

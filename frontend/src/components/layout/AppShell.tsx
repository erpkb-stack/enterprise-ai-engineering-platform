import {
  Activity, Bot, Boxes, Code2, FlaskConical, GitBranch, Gauge, KeyRound, LayoutDashboard, LogOut, Menu, Moon, Network,
  Server, Settings, ShieldAlert, Split, Sun, Wrench, BookOpenText, X,
} from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useEvents } from "@/hooks/useEvents";
import { cn } from "@/lib";
import { get, post } from "@/services/api";
import { useAuth, useUi } from "@/stores/auth";

export const NAV = [
  { section: "Platform", items: [
    { to: "/", label: "Overview", icon: LayoutDashboard, perm: "metrics:read" },
    { to: "/agents", label: "Agents", icon: Bot, perm: "agents:read" },
    { to: "/mcp", label: "MCP Servers", icon: Server, perm: "mcp:read" },
    { to: "/tools", label: "Tool Registry", icon: Wrench, perm: "tools:read" },
  ]},
  { section: "AI Quality", items: [
    { to: "/prompts", label: "Prompt Studio", icon: KeyRound, perm: "prompts:read" },
    { to: "/knowledge", label: "RAG Knowledge", icon: BookOpenText, perm: "knowledge:read" },
    { to: "/evaluations", label: "AI Evaluation", icon: FlaskConical, perm: "evaluations:read" },
    { to: "/redteam", label: "Red Team Lab", icon: ShieldAlert, perm: "evaluations:read" },
    { to: "/experiments", label: "Experiments", icon: Split, perm: "experiments:read" },
  ]},
  { section: "Engineering", items: [
    { to: "/workflows", label: "Workflow Designer", icon: GitBranch, perm: "workflows:read" },
    { to: "/performance", label: "Performance", icon: Gauge, perm: "metrics:read" },
    { to: "/security", label: "Security & Audit", icon: Activity, perm: "agents:read" },
    { to: "/architecture", label: "Architecture Lab", icon: Network, perm: "architecture:read" },
    { to: "/code-review", label: "Code Review", icon: Code2, perm: "codereview:run" },
    { to: "/settings", label: "Settings", icon: Settings, perm: "agents:read" },
  ]},
];

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const can = useAuth((s) => s.can);
  return (
    <nav aria-label="Main" className="flex h-full flex-col bg-sidebar text-sidebar-foreground">
      <div className="flex items-center gap-2.5 px-5 py-5">
        <div className="grid size-8 place-items-center rounded-lg bg-white/10"><Boxes className="size-4.5 text-white" aria-hidden /></div>
        <div>
          <div className="text-sm font-semibold tracking-wide text-white">AIECC</div>
          <div className="text-[10px] uppercase tracking-widest text-white/50">AI Engineering Command Center</div>
        </div>
      </div>
      <div className="flex-1 space-y-5 overflow-y-auto px-3 pb-6 scrollbar-thin">
        {NAV.map((g) => (
          <div key={g.section}>
            <div className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-widest text-white/40">{g.section}</div>
            {g.items.filter((i) => can(i.perm)).map((i) => (
              <NavLink key={i.to} to={i.to} end={i.to === "/"} onClick={onNavigate}
                className={({ isActive }) => cn("flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] transition",
                  isActive ? "bg-white/15 font-medium text-white" : "text-white/70 hover:bg-white/8 hover:text-white")}>
                <i.icon className="size-4" aria-hidden /> {i.label}
              </NavLink>
            ))}
          </div>
        ))}
      </div>
    </nav>
  );
}

export function AppShell() {
  const { me, logout, refreshToken } = useAuth();
  const { theme, toggleTheme, sidebarOpen, setSidebar } = useUi();
  const nav = useNavigate();
  const { connected } = useEvents();
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => get<{ default: { provider: string } }>("/providers"), staleTime: 60_000 });
  const mockDefault = providers.data?.default.provider === "mock";

  const doLogout = async () => {
    try { if (refreshToken) await post("/auth/logout", { refresh_token: refreshToken }); } catch { /* best effort */ }
    logout();
    nav("/login");
  };

  return (
    <div className="flex h-full">
      <aside className="hidden w-60 shrink-0 lg:block"><Sidebar /></aside>
      {sidebarOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setSidebar(false)} />
          <div className="absolute inset-y-0 left-0 w-64"><Sidebar onNavigate={() => setSidebar(false)} /></div>
          <button className="absolute right-4 top-4 rounded bg-card p-1" onClick={() => setSidebar(false)} aria-label="Close menu"><X className="size-4" /></button>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-card/80 px-4 backdrop-blur">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setSidebar(true)} aria-label="Open menu"><Menu /></Button>
          <div className="min-w-0 flex-1 truncate text-sm">
            <span className="font-medium">{me?.organization?.name}</span>
            {me?.organization?.is_demo && <Badge variant="warning" className="ml-2">DEMO DATA</Badge>}
            {mockDefault && <Badge variant="muted" className="ml-2" title="Default provider is the deterministic mock. Configure Ollama or a hosted provider for real models.">mock provider default</Badge>}
          </div>
          <span className="hidden items-center gap-1.5 text-[11px] text-muted-foreground sm:flex" title="Live event stream (WebSocket)">
            <span className={cn("size-2 rounded-full", connected ? "bg-success" : "bg-muted-foreground/40")} /> {connected ? "live" : "offline"}
          </span>
          <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label="Toggle theme">{theme === "dark" ? <Sun /> : <Moon />}</Button>
          <div className="hidden text-right text-xs leading-tight sm:block">
            <div className="font-medium">{me?.full_name}</div>
            <div className="text-muted-foreground">{me?.roles.join(", ")}</div>
          </div>
          <Button variant="ghost" size="icon" onClick={doLogout} aria-label="Sign out"><LogOut /></Button>
        </header>
        <main className="min-w-0 flex-1 overflow-y-auto p-4 sm:p-6 scrollbar-thin"><div className="mx-auto max-w-[1400px]"><Outlet /></div></main>
      </div>
    </div>
  );
}

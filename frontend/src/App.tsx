import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Loading } from "@/components/common";
import { AppShell } from "@/components/layout/AppShell";
import Login from "@/pages/Login";
import { api, get } from "@/services/api";
import { type Me, useAuth, useUi } from "@/stores/auth";

const pages = {
  Overview: lazy(() => import("@/pages/Overview")),
  Agents: lazy(() => import("@/pages/Agents")),
  AgentDetail: lazy(() => import("@/pages/AgentDetail")),
  Mcp: lazy(() => import("@/pages/McpServers")),
  Tools: lazy(() => import("@/pages/ToolRegistry")),
  Prompts: lazy(() => import("@/pages/PromptStudio")),
  Knowledge: lazy(() => import("@/pages/Knowledge")),
  Evaluations: lazy(() => import("@/pages/Evaluations")),
  RedTeam: lazy(() => import("@/pages/RedTeam")),
  Experiments: lazy(() => import("@/pages/Experiments")),
  Workflows: lazy(() => import("@/pages/Workflows")),
  Performance: lazy(() => import("@/pages/Performance")),
  Security: lazy(() => import("@/pages/SecurityAudit")),
  Architecture: lazy(() => import("@/pages/ArchitectureLab")),
  CodeReview: lazy(() => import("@/pages/CodeReview")),
  Settings: lazy(() => import("@/pages/Settings")),
};

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { accessToken, refreshToken, me, setMe, logout } = useAuth();
  const [checking, setChecking] = useState(!me && !!refreshToken);
  const loc = useLocation();
  useEffect(() => {
    if (me || !refreshToken) return;
    // restore session: the api client refreshes the access token transparently
    api<Me>("/auth/me").then(setMe).catch(() => logout()).finally(() => setChecking(false));
  }, [me, refreshToken, setMe, logout]);
  if (checking) return <Loading label="Restoring session…" className="h-full" />;
  if (!accessToken && !refreshToken) return <Navigate to="/login" state={{ from: loc }} replace />;
  if (!me) return <Loading className="h-full" />;
  return <>{children}</>;
}

export default function App() {
  const theme = useUi((s) => s.theme);
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [theme]);
  useEffect(() => {
    get("/health").catch(() => undefined);
  }, []);
  const P = pages;
  return (
    <Suspense fallback={<Loading className="h-full" />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<RequireAuth><AppShell /></RequireAuth>}>
          <Route index element={<P.Overview />} />
          <Route path="agents" element={<P.Agents />} />
          <Route path="agents/:id" element={<P.AgentDetail />} />
          <Route path="mcp" element={<P.Mcp />} />
          <Route path="tools" element={<P.Tools />} />
          <Route path="prompts" element={<P.Prompts />} />
          <Route path="knowledge" element={<P.Knowledge />} />
          <Route path="evaluations" element={<P.Evaluations />} />
          <Route path="redteam" element={<P.RedTeam />} />
          <Route path="experiments" element={<P.Experiments />} />
          <Route path="workflows" element={<P.Workflows />} />
          <Route path="performance" element={<P.Performance />} />
          <Route path="security" element={<P.Security />} />
          <Route path="architecture" element={<P.Architecture />} />
          <Route path="code-review" element={<P.CodeReview />} />
          <Route path="settings" element={<P.Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Suspense>
  );
}

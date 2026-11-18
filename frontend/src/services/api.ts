/**
 * Typed API client. Access token lives in memory (zustand), refresh token in sessionStorage
 * (cleared when the tab closes). On 401 the client performs a single-flight refresh + retry.
 */
import { useAuth } from "@/stores/auth";

export const API = "/api/v1";

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  requestId?: string;
  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}, requestId?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

let refreshing: Promise<boolean> | null = null;

async function refresh(): Promise<boolean> {
  const rt = useAuth.getState().refreshToken;
  if (!rt) return false;
  // single-flight: concurrent 401s share one refresh; cleared as soon as it settles
  refreshing ??= (async () => {
    const r = await fetch(`${API}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: rt }),
    });
    if (!r.ok) {
      useAuth.getState().logout();
      return false;
    }
    const d = await r.json();
    useAuth.getState().setTokens(d.access_token, d.refresh_token);
    return true;
  })()
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

type Opts = { method?: string; body?: unknown; form?: FormData; headers?: Record<string, string>; signal?: AbortSignal; retry?: boolean };

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const token = useAuth.getState().accessToken;
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(path.startsWith("/") && !path.startsWith(API) && !path.startsWith("/health") && !path.startsWith("/ready") ? `${API}${path}` : path,
    { method: opts.method ?? (body ? "POST" : "GET"), headers, body, signal: opts.signal });
  if (res.status === 401 && opts.retry !== false && useAuth.getState().refreshToken) {
    if (await refresh()) return api<T>(path, { ...opts, retry: false });
  }
  const text = await res.text();
  const data = text ? safeJson(text) : null;
  if (!res.ok) {
    const e = (data as { error?: { code: string; message: string; details?: Record<string, unknown> }; request_id?: string })?.error;
    throw new ApiError(res.status, e?.code ?? "http_error", e?.message ?? `HTTP ${res.status}`, e?.details ?? {},
      (data as { request_id?: string })?.request_id);
  }
  return data as T;
}

function safeJson(t: string): unknown {
  try {
    return JSON.parse(t);
  } catch {
    return t;
  }
}

export const get = <T,>(p: string) => api<T>(p);
export const post = <T,>(p: string, body?: unknown, headers?: Record<string, string>) => api<T>(p, { method: "POST", body: body ?? {}, headers });
export const put = <T,>(p: string, body?: unknown) => api<T>(p, { method: "PUT", body: body ?? {} });
export const del = <T,>(p: string) => api<T>(p, { method: "DELETE" });

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    const errs = (e.details?.errors as { loc: string[]; msg: string }[] | undefined) ?? [];
    const extra = errs.length ? `: ${errs.map((x) => `${x.loc.slice(1).join(".")} ${x.msg}`).join("; ")}` : "";
    return `${e.message}${extra}`;
  }
  return e instanceof Error ? e.message : String(e);
}

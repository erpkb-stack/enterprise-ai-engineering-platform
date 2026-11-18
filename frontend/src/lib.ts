import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const fmt = {
  ms: (v?: number | null) => (v == null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${v.toFixed(v < 10 ? 1 : 0)} ms`),
  pct: (v?: number | null, d = 1) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`),
  num: (v?: number | null) => (v == null ? "—" : new Intl.NumberFormat().format(Math.round(v))),
  usd: (v?: number | null) => (v == null ? "—" : `$${v < 0.01 && v > 0 ? v.toFixed(5) : v.toFixed(2)}`),
  dec: (v?: number | null, d = 2) => (v == null ? "—" : v.toFixed(d)),
  ago: (iso?: string | null) => {
    if (!iso) return "—";
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return `${Math.max(0, Math.round(s))}s ago`;
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    if (s < 86400) return `${Math.round(s / 3600)}h ago`;
    return new Date(iso).toLocaleDateString();
  },
};

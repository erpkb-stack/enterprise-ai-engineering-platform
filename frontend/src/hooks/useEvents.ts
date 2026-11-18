/** Live event stream over WebSocket. Auth via first message (tokens never placed in URLs). Auto-reconnects with backoff. */
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/stores/auth";

export type LiveEvent = { topic: string; ts: number; data: Record<string, unknown> };

export function useEvents(onEvent?: (e: LiveEvent) => void) {
  const token = useAuth((s) => s.accessToken);
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const cb = useRef(onEvent);
  cb.current = onEvent;

  useEffect(() => {
    if (!token || typeof WebSocket === "undefined") return;
    let ws: WebSocket | null = null;
    let stop = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout>;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/api/v1/ws/events`);
      ws.onopen = () => ws?.send(JSON.stringify({ type: "auth", token }));
      ws.onmessage = (m) => {
        const ev = JSON.parse(m.data) as LiveEvent;
        if (ev.topic === "ws.ready") { setConnected(true); attempt = 0; return; }
        if (ev.topic === "ws.ping") return;
        setEvents((p) => [ev, ...p].slice(0, 50));
        cb.current?.(ev);
        if (ev.topic.startsWith("agent.")) qc.invalidateQueries({ queryKey: ["executions"] });
        if (ev.topic.startsWith("evaluation.")) qc.invalidateQueries({ queryKey: ["eval-runs"] });
      };
      ws.onclose = () => {
        setConnected(false);
        if (!stop) timer = setTimeout(connect, Math.min(15000, 1000 * 2 ** attempt++));
      };
    };
    connect();
    return () => { stop = true; clearTimeout(timer); ws?.close(); };
  }, [token, qc]);

  return { connected, events };
}

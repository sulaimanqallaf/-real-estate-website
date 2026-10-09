import { useEffect, useRef, useState } from "react";
import { WS_BASE } from "../api";
import type { AgentEvent, Mode } from "../types";

export interface UseAgentEventsOptions {
  mode: Mode;
  since?: string;
  until?: string;
  speed?: number;
  /** Max events kept in the in-memory timeline (oldest dropped first). */
  maxTimeline?: number;
}

export interface UseAgentEventsResult {
  connected: boolean;
  lastEvent: AgentEvent | null;
  timeline: AgentEvent[];
  error: string | null;
}

/** Owns the single WebSocket connection to `/ws`. Reconnects on an
 * unexpected close (e.g. LIVE mode after a backend restart) with a
 * short fixed backoff - never silently stops updating the scene. */
export function useAgentEvents(options: UseAgentEventsOptions): UseAgentEventsResult {
  const { mode, since, until, speed, maxTimeline = 200 } = options;
  const [connected, setConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState<AgentEvent | null>(null);
  const [timeline, setTimeline] = useState<AgentEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (cancelled) return;
      const params = new URLSearchParams({ mode });
      if (since) params.set("since", since);
      if (until) params.set("until", until);
      if (speed) params.set("speed", String(speed));

      const socket = new WebSocket(`${WS_BASE}/ws?${params.toString()}`);
      socketRef.current = socket;

      socket.onopen = () => {
        setConnected(true);
        setError(null);
      };

      socket.onmessage = (messageEvent: MessageEvent<string>) => {
        const parsed = JSON.parse(messageEvent.data) as AgentEvent | { error: string };
        if ("error" in parsed) {
          setError(parsed.error);
          return;
        }
        setLastEvent(parsed);
        setTimeline((prev) => {
          const next = [...prev, parsed];
          return next.length > maxTimeline ? next.slice(next.length - maxTimeline) : next;
        });
      };

      socket.onclose = () => {
        setConnected(false);
        if (!cancelled) retryTimer = setTimeout(connect, 3000);
      };

      socket.onerror = () => {
        setError("WebSocket connection error");
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      socketRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, since, until, speed]);

  return { connected, lastEvent, timeline, error };
}

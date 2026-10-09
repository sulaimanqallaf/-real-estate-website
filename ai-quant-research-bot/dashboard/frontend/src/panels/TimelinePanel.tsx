import type { AgentEvent } from "../types";
import { Empty, Panel } from "./HealthPanel";

export function TimelinePanel({ timeline }: { timeline: AgentEvent[] }) {
  const recent = [...timeline].slice(-30).reverse();
  return (
    <Panel title="Event Timeline">
      {recent.length === 0 ? (
        <Empty />
      ) : (
        <ul className="timeline-list">
          {recent.map((event) => (
            <li key={event.id} className={`timeline-item timeline-${event.event_type}`}>
              <span className="timeline-ts">{formatTime(event.ts)}</span>
              <span className="timeline-summary">{event.summary}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString();
  } catch {
    return ts;
  }
}

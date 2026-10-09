import type { LearningExperiments } from "../types";
import { Empty, Panel } from "./HealthPanel";

export function LearningExperimentsPanel({ data }: { data: LearningExperiments | null }) {
  const models = data?.models ?? [];
  const events = data?.events ?? [];
  return (
    <Panel title="Learning / Challenger Experiments">
      {models.length === 0 && events.length === 0 ? (
        <Empty />
      ) : (
        <>
          {models.length > 0 && (
            <ul className="ta-list">
              {models.slice(-10).reverse().map((m) => (
                <li key={m.model_id}>
                  <strong>{m.model_id}</strong> - {m.model_type ?? "?"} - {m.status ?? "?"}
                </li>
              ))}
            </ul>
          )}
          {events.length > 0 && (
            <ul className="timeline-list">
              {events.slice(-10).reverse().map((e, i) => (
                <li key={i} className="timeline-item timeline-info">
                  <span className="timeline-summary">{e.event}: {e.model_id} {e.reason ? `(${e.reason})` : ""}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Panel>
  );
}

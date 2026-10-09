import type { TradingAgentsResult } from "../types";
import { Panel } from "./HealthPanel";

export function TradingAgentsPanel({ results }: { results: TradingAgentsResult[] | null }) {
  return (
    <Panel title="TradingAgents Outputs">
      {!results || results.length === 0 ? (
        <div className="panel-empty">No TradingAgents results available (disabled, or no cache yet)</div>
      ) : (
        <ul className="ta-list">
          {[...results].reverse().map((result, i) => (
            <li key={i}>
              <strong>{String(result.ticker ?? "?")}</strong> - {String(result.report_date ?? "")}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

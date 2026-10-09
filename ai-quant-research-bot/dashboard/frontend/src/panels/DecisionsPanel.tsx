import type { DecisionRow } from "../types";
import { Empty, Panel } from "./HealthPanel";

export function DecisionsPanel({ decisions }: { decisions: DecisionRow[] | null }) {
  return (
    <Panel title="Latest Research Decisions">
      {!decisions || decisions.length === 0 ? (
        <Empty />
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Ticker</th><th>Decision</th><th>Strategy</th><th>Score</th><th>Outcome</th>
            </tr>
          </thead>
          <tbody>
            {[...decisions].reverse().map((row) => (
              <tr key={row.decision_id}>
                <td>{row.ticker}</td>
                <td>{row.decision}</td>
                <td>{row.strategy ?? "-"}</td>
                <td>{row.signal_score ?? "-"}</td>
                <td>{row.outcome_status ?? "pending"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

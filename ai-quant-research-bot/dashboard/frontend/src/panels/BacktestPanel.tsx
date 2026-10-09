import type { BacktestPerformance } from "../types";
import { Panel } from "./HealthPanel";

export function BacktestPanel({ backtest }: { backtest: BacktestPerformance | null }) {
  return (
    <Panel title="Strategy Backtest Performance">
      {!backtest || backtest.rows.length === 0 ? (
        <div className="panel-empty">No backtest report saved yet - run `python -m src.backtester` separately to generate one.</div>
      ) : (
        <>
          <div className="panel-footnote">Report: {backtest.report_date}</div>
          <table className="data-table">
            <thead>
              <tr>{Object.keys(backtest.rows[0]).map((key) => <th key={key}>{key}</th>)}</tr>
            </thead>
            <tbody>
              {backtest.rows.map((row, i) => (
                <tr key={i}>{Object.values(row).map((value, j) => <td key={j}>{String(value)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Panel>
  );
}

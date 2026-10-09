import type { PositionsAndOrders } from "../types";
import { Empty, Panel } from "./HealthPanel";

export function PositionsOrdersPanel({ data }: { data: PositionsAndOrders | null }) {
  const rows = data?.rows ?? [];
  return (
    <Panel title="Positions / Orders / Fills">
      {rows.length === 0 ? (
        <Empty />
      ) : (
        <table className="data-table">
          <thead>
            <tr><th>Ticker</th><th>Event</th><th>State</th><th>Filled</th></tr>
          </thead>
          <tbody>
            {[...rows].reverse().map((row, i) => (
              <tr key={i}>
                <td>{String(row.ticker ?? "-")}</td>
                <td>{String(row.type ?? row.event ?? "-")}</td>
                <td>{String(row.state ?? "-")}</td>
                <td>{row.filled_quantity != null ? String(row.filled_quantity) : "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {data && data.total_count > rows.length && (
        <div className="panel-footnote">Showing latest {rows.length} of {data.total_count} total journal entries.</div>
      )}
    </Panel>
  );
}

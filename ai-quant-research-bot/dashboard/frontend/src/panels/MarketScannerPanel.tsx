import type { MarketScanner } from "../types";
import { Empty, Panel, Row } from "./HealthPanel";

export function MarketScannerPanel({ scanner }: { scanner: MarketScanner | null }) {
  return (
    <Panel title="Market Scanner">
      <Row label="Universe size" value={scanner?.universe_size ?? 0} />
      {!scanner || scanner.tickers.length === 0 ? (
        <Empty />
      ) : (
        <>
          <div className="panel-footnote">Last scored: {scanner.report_date ?? "unknown"}</div>
          <table className="data-table">
            <thead><tr><th>Symbol</th><th>Score</th></tr></thead>
            <tbody>
              {scanner.tickers.slice(0, 15).map((t, i) => (
                <tr key={i}><td>{String(t.symbol ?? "-")}</td><td>{t.score != null ? String(t.score) : "-"}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Panel>
  );
}

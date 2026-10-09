import type { ResearchAnalytics } from "../types";
import { Empty, Panel, Row } from "./HealthPanel";

export function ResearchAnalyticsPanel({ data }: { data: ResearchAnalytics | null }) {
  if (!data) return <Panel title="Research & Performance Analytics"><Empty /></Panel>;

  const { performance_report: perf, monte_carlo: mc, regime_breakdown: regime, hypotheses } = data;

  return (
    <Panel title="Research & Performance Analytics">
      <div className="panel-subsection-title">Paper-trading performance (QuantStats)</div>
      {!perf ? (
        <div className="panel-empty">Not enough closed trades with a recorded outcome yet.</div>
      ) : (
        <>
          <div className="panel-footnote">Basis: {perf.basis} ({perf.trade_count} closed trades)</div>
          <Row label="Sharpe" value={fmtNum(perf.sharpe)} />
          <Row label="Sortino" value={fmtNum(perf.sortino)} />
          <Row label="Max drawdown" value={fmtPct(perf.max_drawdown)} />
          <Row label="Profit factor" value={fmtNum(perf.profit_factor)} />
          <Row label="Win rate" value={fmtPct(perf.win_rate)} />
          <Row label="Exposure" value={fmtPct(perf.exposure)} />
          {perf.total_commission_usd !== null && <Row label="Total commission" value={`$${perf.total_commission_usd.toFixed(2)}`} />}
          {perf.avg_slippage_pct !== null && <Row label="Avg slippage" value={fmtPct(perf.avg_slippage_pct)} />}
        </>
      )}

      <div className="panel-subsection-title">Monte Carlo stress test (bootstrap resample)</div>
      {!mc ? (
        <div className="panel-empty">Not enough closed trades for a meaningful bootstrap yet.</div>
      ) : (
        <>
          <div className="panel-footnote">{mc.n_trades} trades, {mc.n_simulations} simulations - sequencing risk only, not a forward-looking guarantee</div>
          <Row label="Observed return" value={`${mc.observed_total_return_pct}%`} />
          <Row label="Simulated return (p5 / p50 / p95)" value={`${mc.simulated_total_return_pct.p5}% / ${mc.simulated_total_return_pct.p50}% / ${mc.simulated_total_return_pct.p95}%`} />
          <Row label="Probability of loss" value={`${(mc.probability_of_loss * 100).toFixed(1)}%`} />
          <Row label="Worst-case drawdown (p1)" value={`${mc.worst_case_drawdown_pct}%`} />
        </>
      )}

      <div className="panel-subsection-title">Performance by market regime</div>
      {!regime || regime.rows.length === 0 ? (
        <div className="panel-empty">No regime has enough closed trades to report yet.</div>
      ) : (
        <table className="data-table">
          <thead><tr><th>Regime</th><th>Trades</th><th>Win rate</th><th>Avg P&amp;L</th></tr></thead>
          <tbody>
            {regime.rows.map((row) => (
              <tr key={row.regime}>
                <td>{row.regime}</td>
                <td>{row.trade_count}</td>
                <td>{row.win_rate_pct}%</td>
                <td>{row.avg_pnl_pct > 0 ? "+" : ""}{row.avg_pnl_pct}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="panel-subsection-title">Research sandbox - hypothesis experiments</div>
      {hypotheses.length === 0 ? (
        <div className="panel-empty">No hypotheses tested yet (src/research/sandbox.py).</div>
      ) : (
        <table className="data-table">
          <thead><tr><th>Hypothesis</th><th>Strategy</th><th>Symbol</th><th>Trades</th><th>Data</th></tr></thead>
          <tbody>
            {[...hypotheses].reverse().map((h, i) => (
              <tr key={i}>
                <td>{h.hypothesis_id}</td>
                <td>{h.strategy_name}</td>
                <td>{h.symbol}</td>
                <td>{h.trade_count ?? "-"}</td>
                <td>{h.data_provenance}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function fmtNum(value: number | null): string {
  return value === null ? "unavailable" : value.toFixed(3);
}

function fmtPct(value: number | null): string {
  return value === null ? "unavailable" : `${(value * 100).toFixed(2)}%`;
}

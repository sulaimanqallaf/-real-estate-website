import type { PaperPnlSummary, SpendSummary } from "../types";
import { Empty, Panel, Row } from "./HealthPanel";

export function SpendPanel({ spend }: { spend: SpendSummary | null }) {
  return (
    <Panel title="API Spend Summary">
      {!spend ? (
        <div className="panel-empty">Unavailable (TradingAgents disabled, or no calls made yet)</div>
      ) : (
        <>
          <Row label="Committed today" value={`$${spend.committed_today_usd.toFixed(4)}`} />
          <Row label="Committed this month" value={`$${spend.committed_month_usd.toFixed(4)}`} />
          <Row label="Reserved (not yet billed)" value={`$${spend.reserved_today_usd.toFixed(4)}`} />
        </>
      )}
    </Panel>
  );
}

/** Paper P&L - ONLY ever rendered from a real `/api/paper-pnl` response.
 * `null` (no real trade data exists yet) renders as "No data yet," NEVER
 * as $0.00 or any other fabricated figure - see dashboard backend's
 * `readonly.paper_pnl_summary()` docstring for the same invariant on the
 * data side. */
export function PnlPanel({ pnl }: { pnl: PaperPnlSummary | null }) {
  return (
    <Panel title="Paper P&L">
      {!pnl ? (
        <Empty />
      ) : (
        <>
          <Row label="Realized P&L" value={`$${pnl.realized_pnl_dollars.toFixed(2)}`} />
          <Row label="Open / Closed" value={`${pnl.open_count} / ${pnl.closed_count}`} />
          <Row label="Win / Loss" value={`${pnl.win_count} / ${pnl.loss_count}`} />
        </>
      )}
    </Panel>
  );
}

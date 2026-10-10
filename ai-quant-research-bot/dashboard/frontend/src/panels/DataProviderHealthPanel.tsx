import type { DataProviderHealth } from "../types";
import { Empty, Panel } from "./HealthPanel";

const PROVIDER_LABELS: Record<string, string> = {
  sec_13f_form4: "SEC 13F / Form 4 (institutional/insider)",
  fred_macro: "FRED (macro)",
  options_flow: "Options flow",
  forex: "Forex OHLCV",
  crypto: "Crypto OHLCV (CCXT)",
  alpaca_iex: "Alpaca Basic/IEX (free market data)",
};

export function DataProviderHealthPanel({ data }: { data: DataProviderHealth | null }) {
  if (!data || data.providers.length === 0) return <Panel title="Data Provider Health"><Empty /></Panel>;

  return (
    <Panel title="Data Provider Health">
      {data.providers.map((p) => (
        <div className="panel-row" key={p.name}>
          <span className="panel-row-label">{PROVIDER_LABELS[p.name] ?? p.name}</span>
          <span className={`panel-row-value ${p.configured ? "status-ok" : "status-unconfigured"}`}>
            {p.configured ? "configured" : "not configured"}
          </span>
        </div>
      ))}
    </Panel>
  );
}

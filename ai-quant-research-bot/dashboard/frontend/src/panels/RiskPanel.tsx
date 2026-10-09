import type { RiskStatus } from "../types";
import { Panel, Row } from "./HealthPanel";

export function RiskPanel({ risk }: { risk: RiskStatus | null }) {
  if (!risk) return <Panel title="Risk / Drawdown"><div className="panel-empty">No data yet</div></Panel>;

  return (
    <Panel title="Risk / Drawdown">
      <Row label="Circuit breaker" value={risk.breaker.halted ? `HALTED (${risk.breaker.reason ?? "no reason given"})` : "OK"} />
      {Object.entries(risk.limits).map(([key, value]) => (
        <Row key={key} label={key.replace(/_/g, " ")} value={typeof value === "number" ? value.toString() : String(value)} />
      ))}
    </Panel>
  );
}

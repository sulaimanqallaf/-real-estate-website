import type { HealthReport } from "../types";

export function HealthPanel({ health }: { health: HealthReport | null }) {
  if (!health) return <Panel title="Health Status"><Empty /></Panel>;

  return (
    <Panel title="Health Status">
      <Row label="Last run OK" value={health.status.last_run_ok === null ? "unknown" : String(health.status.last_run_ok)} />
      <Row label="Last success" value={health.status.last_success_at ?? "never"} />
      <Row label="Next scheduled run" value={health.next_market_aware_run?.target_run_utc ?? health.next_scheduled_run ?? "unknown"} />
      <Row label="launchd (daily)" value={launchdLabel(health.launchd.installed)} />
      <Row label="launchd (after-close)" value={launchdLabel(health.launchd_after_close.installed)} />
      <Row label="Circuit breaker" value={health.circuit_breaker.halted ? `HALTED (${health.circuit_breaker.reason ?? "no reason given"})` : "OK"} />
    </Panel>
  );
}

function launchdLabel(installed: boolean | null): string {
  if (installed === true) return "installed";
  if (installed === false) return "NOT installed";
  return "unknown";
}

export function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="panel">
      <h3 className="panel-title">{title}</h3>
      <div className="panel-body">{children}</div>
    </section>
  );
}

export function Row({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="panel-row">
      <span className="panel-row-label">{label}</span>
      <span className="panel-row-value">{value}</span>
    </div>
  );
}

export function Empty() {
  return <div className="panel-empty">No data yet</div>;
}

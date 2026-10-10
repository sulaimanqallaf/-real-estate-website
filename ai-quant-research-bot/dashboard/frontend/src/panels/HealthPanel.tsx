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
      <Row label="Position monitor" value={heartbeatLabel(health.position_monitor_heartbeat)} />
      <Row label="Cached data (yfinance)" value={stalenessLabel(health.data_staleness)} />
      <Row label="Cached data (Alpaca/IEX)" value={iexFreshnessLabel(health.iex_data_freshness)} />
    </Panel>
  );
}

function launchdLabel(installed: boolean | null): string {
  if (installed === true) return "installed";
  if (installed === false) return "NOT installed";
  return "unknown";
}

function heartbeatLabel(heartbeat: HealthReport["position_monitor_heartbeat"]): string {
  if (heartbeat.never_started) return "never started";
  if (heartbeat.stale) return `STALE (last seen ${heartbeat.age_seconds}s ago)`;
  return `OK (${heartbeat.age_seconds}s ago)`;
}

function stalenessLabel(staleness: HealthReport["data_staleness"]): string {
  if (staleness.check_failed.length > 0) return `CHECK FAILED: ${staleness.check_failed.join(", ")}`;
  if (staleness.stale.length > 0) return `stale: ${staleness.stale.join(", ")}`;
  return "fresh";
}

function iexFreshnessLabel(freshness: HealthReport["iex_data_freshness"]): string {
  if (freshness.cached_file_count === 0) return "nothing scanned yet";
  if (freshness.check_failed.length > 0) return `CHECK FAILED: ${freshness.check_failed.join(", ")}`;
  if (freshness.stale.length > 0) return `stale: ${freshness.stale.join(", ")}`;
  return `fresh (${freshness.cached_file_count} file(s))`;
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

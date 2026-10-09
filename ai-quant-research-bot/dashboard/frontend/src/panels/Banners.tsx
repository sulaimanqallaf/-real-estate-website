import type { HealthReport, Mode } from "../types";

/** Warning banners - every condition here is read straight from the
 * real `/api/health` response; nothing is inferred or assumed. Renders
 * nothing when there is nothing to warn about. */
export function Banners({ health, mode, wsConnected }: { health: HealthReport | null; mode: Mode; wsConnected: boolean }) {
  const banners: { key: string; text: string; tone: "warn" | "error" }[] = [];

  if (mode === "demo") {
    banners.push({ key: "demo", text: "DEMO MODE - all events and figures below are synthetic, not real data.", tone: "warn" });
  }

  if (!wsConnected) {
    banners.push({ key: "disconnected", text: "Event stream disconnected - reconnecting...", tone: "error" });
  }

  if (health) {
    const staleness = health.data_staleness;
    if (staleness.check_failed.length > 0) {
      banners.push({ key: "stale-check-failed", text: `Data-freshness check FAILED for: ${staleness.check_failed.join(", ")}`, tone: "error" });
    }
    if (staleness.stale.length > 0) {
      banners.push({ key: "stale", text: `Stale cached market data: ${staleness.stale.join(", ")}`, tone: "warn" });
    }
    if (health.status.last_run_ok === false) {
      banners.push({ key: "last-run-failed", text: `Last tracked research run FAILED: ${health.status.last_run_summary ?? "see logs"}`, tone: "error" });
    }
    if (health.circuit_breaker.halted) {
      banners.push({ key: "broker-halted", text: `Execution circuit breaker is HALTED: ${health.circuit_breaker.reason ?? "no reason given"} - broker/order flow unavailable.`, tone: "error" });
    }
  }

  if (banners.length === 0) return null;

  return (
    <div className="banners">
      {banners.map((banner) => (
        <div key={banner.key} className={`banner banner-${banner.tone}`}>{banner.text}</div>
      ))}
    </div>
  );
}

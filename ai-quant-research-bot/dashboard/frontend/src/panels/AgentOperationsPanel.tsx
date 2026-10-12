import type { AgentOperationEntry, AgentOperations } from "../types";
import { Empty, Panel } from "./HealthPanel";

const STATUS_CLASS: Record<string, string> = {
  SUCCESS: "status-ok",
  RUNNING: "status-ok",
  FAILED: "status-unconfigured",
};

export function AgentOperationsPanel({ data }: { data: AgentOperations | null }) {
  if (!data || data.agents.length === 0) {
    return (
      <Panel title="AI Company - Agent Operations">
        <Empty />
      </Panel>
    );
  }

  return (
    <Panel title="AI Company - Agent Operations">
      {data.agents.map((agent) => (
        <AgentRow key={agent.name} agent={agent} />
      ))}
    </Panel>
  );
}

function AgentRow({ agent }: { agent: AgentOperationEntry }) {
  const status = agent.status;
  const statusLabel = status ? status.status : "UNKNOWN";
  const heartbeatLabel = heartbeatText(agent);
  const providerIssues = providerIssuesText(status?.extra);

  return (
    <>
      <div className="panel-subsection-title">{formatAgentName(agent.name)}</div>
      <div className="panel-row">
        <span className="panel-row-label">Status</span>
        <span className={`panel-row-value ${STATUS_CLASS[statusLabel] ?? "status-unconfigured"}`}>{statusLabel}</span>
      </div>
      {status?.started_at && (
        <div className="panel-row">
          <span className="panel-row-label">Last run</span>
          <span className="panel-row-value">{status.started_at}{status.finished_at ? ` -> ${status.finished_at}` : " (in progress)"}</span>
        </div>
      )}
      {status?.summary && (
        <div className="panel-row">
          <span className="panel-row-label">Summary</span>
          <span className="panel-row-value">{status.summary}</span>
        </div>
      )}
      {status?.error && (
        <div className="panel-row">
          <span className="panel-row-label">Error</span>
          <span className="panel-row-value status-unconfigured">{status.error}</span>
        </div>
      )}
      {providerIssues && (
        <div className="panel-row">
          <span className="panel-row-label">Provider issues (last run)</span>
          <span className="panel-row-value status-unconfigured">{providerIssues}</span>
        </div>
      )}
      <div className="panel-row">
        <span className="panel-row-label">Heartbeat</span>
        <span className="panel-row-value">{heartbeatLabel}</span>
      </div>
    </>
  );
}

// Sprint 5: a real provider failure (exception/STATUS_ERROR/STATUS_BLOCKED)
// recorded by research_scholar.py/news_agent.py into their own run's
// `extra.provider_issues` (see src/agents/health_report.py's identical
// surfacing in its text report) - an honest non-answer (STATUS_UNAVAILABLE,
// or a budget cap) is never recorded there, so any non-empty list here is a
// real issue worth a human's attention, not noise.
function providerIssuesText(extra: Record<string, unknown> | undefined): string | null {
  const issues = extra?.provider_issues;
  if (!Array.isArray(issues) || issues.length === 0) return null;
  const sources = Array.from(
    new Set(issues.map((issue) => (typeof issue === "object" && issue !== null ? String((issue as Record<string, unknown>).source ?? "?") : "?"))),
  ).sort();
  return `${issues.length} (${sources.join(", ")})`;
}

function heartbeatText(agent: AgentOperationEntry): string {
  const hb = agent.heartbeat;
  if (hb.never_started) return "never started";
  if (hb.stale) return `STALE (last seen ${hb.age_seconds}s ago)`;
  return `OK (${hb.age_seconds}s ago)`;
}

function formatAgentName(name: string): string {
  return name
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

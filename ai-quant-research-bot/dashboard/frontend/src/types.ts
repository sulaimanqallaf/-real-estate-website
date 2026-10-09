// Mirrors dashboard/backend/app/events.py's AgentEvent exactly - see
// dashboard/README.md for the documented schema. Keep these two in sync
// by hand; src/__tests__/agentConfig.test.ts pins the agent-id list so a
// drift is caught immediately rather than silently breaking the scene.

export type AgentId =
  | "market_scout"
  | "bull_analyst"
  | "bear_analyst"
  | "chief_manager"
  | "risk_officer"
  | "strategy_scientist"
  | "execution_agent"
  | "learning_agent";

export type ZoneId =
  | "scout_desk"
  | "debate_room"
  | "chief_office"
  | "risk_desk"
  | "strategy_desk"
  | "execution_desk"
  | "learning_desk";

export type EventType =
  | "scan"
  | "debate"
  | "risk_check"
  | "decision"
  | "execution"
  | "learning"
  | "health"
  | "info";

export type Mode = "live" | "replay" | "demo";

export interface AgentEvent {
  id: string;
  ts: string;
  mode: Mode;
  event_type: EventType;
  agent: AgentId | null;
  zone: ZoneId | null;
  ticker: string | null;
  summary: string;
  data: Record<string, unknown>;
}

export interface RunStatus {
  last_run_started_at: string | null;
  last_run_finished_at: string | null;
  last_run_ok: boolean | null;
  last_run_summary: string | null;
  last_run_failed_symbols: string[];
  last_success_at: string | null;
  most_recent_report_file_date: string | null;
}

export interface HealthReport {
  status: RunStatus;
  next_scheduled_run: string | null;
  launchd: { installed: boolean | null; detail: string };
  next_market_aware_run: { target_run_utc: string; is_early_close: boolean } | null;
  launchd_after_close: { installed: boolean | null; detail: string };
  errors: { since_last_run_started: string[]; historical: string[] };
  spend: SpendSummary | null;
  data_staleness: { stale: string[]; check_failed: string[]; ok: string[] };
  circuit_breaker: { halted: boolean; reason: string | null };
}

export interface SpendSummary {
  committed_today_usd: number;
  committed_month_usd: number;
  reserved_today_usd: number;
  reserved_month_usd: number;
  outstanding_reservation_count: number;
}

export interface PaperPnlSummary {
  open_count: number;
  closed_count: number;
  realized_pnl_dollars: number;
  win_count: number;
  loss_count: number;
}

export interface DecisionRow {
  decision_id: string;
  ticker: string;
  strategy: string | null;
  decision: string;
  regime: string | null;
  signal_score: number | null;
  as_of: string;
  outcome_status: string | null;
  pnl_pct: number | null;
  [key: string]: unknown;
}

export interface TradingAgentsResult {
  ticker?: string;
  report_date?: string;
  [key: string]: unknown;
}

export interface ModesAvailability {
  live: boolean;
  replay: boolean;
  demo: boolean;
}

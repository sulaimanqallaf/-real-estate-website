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

export interface JournalRow {
  event?: string;
  type?: string;
  ticker?: string | null;
  trade_id?: string | null;
  state?: string;
  filled_quantity?: number | null;
  avg_fill_price?: number | null;
  rejection_reason?: string | null;
  [key: string]: unknown;
}

export interface PositionsAndOrders {
  rows: JournalRow[];
  total_count: number;
}

export interface RiskStatus {
  breaker: { halted: boolean; reason: string | null };
  limits: Record<string, number>;
}

export interface BacktestPerformance {
  report_date: string;
  rows: Record<string, unknown>[];
}

export interface ModelMetadataRow {
  model_id: string;
  model_type?: string;
  task?: string;
  target?: string;
  horizon?: number;
  status?: string;
  [key: string]: unknown;
}

export interface ModelEventRow {
  event: string;
  model_id: string;
  model_type?: string | null;
  reason?: string | null;
  recorded_at?: string;
  [key: string]: unknown;
}

export interface LearningExperiments {
  models: ModelMetadataRow[];
  events: ModelEventRow[];
}

export interface ScannerTicker {
  symbol?: string;
  score?: number;
  [key: string]: unknown;
}

export interface MarketScanner {
  report_date: string | null;
  tickers: ScannerTicker[];
  universe_size: number;
}

// AI Quant Trading Platform OSS integration sprint (Phase 7): QuantStats/
// Monte Carlo/regime/hypothesis-ledger analytics - see
// dashboard/backend/app/readonly.py's research_analytics().

export interface PerformanceReport {
  basis: string;
  trade_count: number;
  sharpe: number | null;
  sortino: number | null;
  max_drawdown: number | null;
  profit_factor: number | null;
  win_rate: number | null;
  exposure: number | null;
  total_commission_usd: number | null;
  avg_slippage_pct: number | null;
  [key: string]: unknown;
}

export interface MonteCarloPercentiles {
  p5: number;
  p25: number;
  p50: number;
  p75: number;
  p95: number;
}

export interface MonteCarloStressTest {
  n_trades: number;
  n_simulations: number;
  observed_total_return_pct: number;
  observed_max_drawdown_pct: number;
  simulated_total_return_pct: MonteCarloPercentiles;
  simulated_max_drawdown_pct: MonteCarloPercentiles;
  probability_of_loss: number;
  worst_case_drawdown_pct: number;
}

export interface RegimeBreakdownRow {
  regime: string;
  trade_count: number;
  win_rate_pct: number;
  avg_pnl_pct: number;
  total_pnl_dollars: number | null;
}

export interface RegimeBreakdown {
  rows: RegimeBreakdownRow[];
  total_closed_trades: number;
  excluded_insufficient_sample: number;
  min_trades_per_regime_row: number;
}

export interface HypothesisResultRow {
  hypothesis_id: string;
  description?: string | null;
  strategy_name: string;
  symbol: string;
  data_provenance: string;
  trade_count: number | null;
  stats: Record<string, unknown> | null;
  recorded_at: string;
  [key: string]: unknown;
}

export interface ResearchAnalytics {
  performance_report: PerformanceReport | null;
  monte_carlo: MonteCarloStressTest | null;
  regime_breakdown: RegimeBreakdown | null;
  hypotheses: HypothesisResultRow[];
}

export interface DataProviderStatus {
  name: string;
  configured: boolean;
}

export interface DataProviderHealth {
  providers: DataProviderStatus[];
}

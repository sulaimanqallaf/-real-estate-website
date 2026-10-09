import type {
  BacktestPerformance, DecisionRow, HealthReport, LearningExperiments, MarketScanner,
  ModesAvailability, PaperPnlSummary, PositionsAndOrders, RiskStatus, RunStatus,
  SpendSummary, TradingAgentsResult,
} from "./types";

export const API_BASE = import.meta.env.VITE_DASHBOARD_API_BASE ?? "http://localhost:8800";
export const WS_BASE = API_BASE.replace(/^http/, "ws");

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) throw new Error(`${path} -> HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

export const api = {
  health: () => getJson<HealthReport>("/api/health"),
  runStatus: () => getJson<RunStatus>("/api/run-status"),
  decisions: (limit = 25) => getJson<DecisionRow[]>(`/api/decisions?limit=${limit}`),
  tradingAgents: (limit = 10) => getJson<TradingAgentsResult[]>(`/api/tradingagents?limit=${limit}`),
  spend: () => getJson<SpendSummary | null>("/api/spend"),
  paperPnl: () => getJson<PaperPnlSummary | null>("/api/paper-pnl"),
  modes: () => getJson<ModesAvailability>("/api/modes"),
  positionsAndOrders: (limit = 50) => getJson<PositionsAndOrders>(`/api/positions-orders?limit=${limit}`),
  risk: () => getJson<RiskStatus>("/api/risk"),
  backtestPerformance: () => getJson<BacktestPerformance | null>("/api/backtest-performance"),
  learningExperiments: (limit = 20) => getJson<LearningExperiments>(`/api/learning-experiments?limit=${limit}`),
  marketScanner: (limit = 100) => getJson<MarketScanner | null>(`/api/market-scanner?limit=${limit}`),
};

import { useState } from "react";
import "./dashboard.css";
import { api } from "./api";
import { Banners } from "./panels/Banners";
import { BacktestPanel } from "./panels/BacktestPanel";
import { DecisionsPanel } from "./panels/DecisionsPanel";
import { HealthPanel } from "./panels/HealthPanel";
import { LearningExperimentsPanel } from "./panels/LearningExperimentsPanel";
import { MarketScannerPanel } from "./panels/MarketScannerPanel";
import { ModeBar } from "./panels/ModeBar";
import { PositionsOrdersPanel } from "./panels/PositionsOrdersPanel";
import { RiskPanel } from "./panels/RiskPanel";
import { PnlPanel, SpendPanel } from "./panels/SpendAndPnlPanel";
import { TimelinePanel } from "./panels/TimelinePanel";
import { TradingAgentsPanel } from "./panels/TradingAgentsPanel";
import { Scene } from "./pixi/Scene";
import { usePolling } from "./usePolling";
import type { Mode } from "./types";
import { useAgentEvents } from "./ws/useAgentEvents";

export default function App() {
  const [mode, setMode] = useState<Mode>("live");

  const { data: modesAvailable } = usePolling(api.modes, 30000);
  const { data: health } = usePolling(api.health, 15000);
  const { data: decisions } = usePolling(() => api.decisions(25), 15000);
  const { data: tradingAgentsResults } = usePolling(() => api.tradingAgents(10), 20000);
  const { data: spend } = usePolling(api.spend, 20000);
  const { data: pnl } = usePolling(api.paperPnl, 20000);
  const { data: positionsOrders } = usePolling(() => api.positionsAndOrders(50), 15000);
  const { data: risk } = usePolling(api.risk, 15000);
  const { data: backtest } = usePolling(api.backtestPerformance, 60000);
  const { data: learning } = usePolling(() => api.learningExperiments(20), 30000);
  const { data: scanner } = usePolling(() => api.marketScanner(100), 20000);

  const { connected, lastEvent, timeline, error } = useAgentEvents({ mode });

  return (
    <div className="dashboard-root">
      <ModeBar mode={mode} onModeChange={setMode} modesAvailable={modesAvailable} connected={connected} />
      <Banners health={health} mode={mode} wsConnected={connected} />
      {error && <div className="banner banner-error">{error}</div>}

      <div className="dashboard-layout">
        <div className="scene-column">
          <Scene lastEvent={lastEvent} />
          <TimelinePanel timeline={timeline} />
        </div>
        <div className="panels-column">
          <HealthPanel health={health} />
          <MarketScannerPanel scanner={scanner} />
          <DecisionsPanel decisions={decisions} />
          <TradingAgentsPanel results={tradingAgentsResults} />
          <PositionsOrdersPanel data={positionsOrders} />
          <RiskPanel risk={risk} />
          <BacktestPanel backtest={backtest} />
          <LearningExperimentsPanel data={learning} />
          <SpendPanel spend={spend} />
          <PnlPanel pnl={pnl} />
        </div>
      </div>
    </div>
  );
}

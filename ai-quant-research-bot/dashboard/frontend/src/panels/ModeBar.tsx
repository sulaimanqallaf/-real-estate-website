import type { ModesAvailability, Mode } from "../types";

export function ModeBar({
  mode, onModeChange, modesAvailable, connected,
}: {
  mode: Mode;
  onModeChange: (mode: Mode) => void;
  modesAvailable: ModesAvailability | null;
  connected: boolean;
}) {
  return (
    <div className="mode-bar">
      <span className="mode-bar-title">AI Agents Dashboard</span>
      <div className="mode-buttons">
        {(["live", "replay", "demo"] as Mode[]).map((candidate) => {
          const disabled = modesAvailable ? !modesAvailable[candidate] : candidate === "demo";
          return (
            <button
              key={candidate}
              className={`mode-button ${mode === candidate ? "mode-button-active" : ""}`}
              disabled={disabled}
              title={disabled ? "Disabled on this server (set DASHBOARD_ALLOW_DEMO=1)" : undefined}
              onClick={() => onModeChange(candidate)}
            >
              {candidate.toUpperCase()}
            </button>
          );
        })}
      </div>
      <span className={`connection-dot ${connected ? "connection-ok" : "connection-bad"}`} title={connected ? "Connected" : "Disconnected"} />
    </div>
  );
}

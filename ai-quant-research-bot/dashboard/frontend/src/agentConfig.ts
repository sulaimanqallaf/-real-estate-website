import type { AgentId, ZoneId } from "./types";

export interface AgentDef {
  id: AgentId;
  label: string;
  color: number;
  /** Home desk position - where the agent idles/returns to when inactive. */
  home: { x: number; y: number };
}

/** Zone positions the agents move TO on a matching event. `debate_room`
 * is shared by bull_analyst/bear_analyst (each gets a small offset so
 * the two sprites don't fully overlap). */
export const ZONE_POSITIONS: Record<ZoneId, { x: number; y: number; label: string }> = {
  scout_desk: { x: 120, y: 440, label: "Scout Desk" },
  debate_room: { x: 450, y: 160, label: "Debate Room" },
  chief_office: { x: 780, y: 120, label: "Chief Office" },
  risk_desk: { x: 780, y: 300, label: "Risk Desk" },
  strategy_desk: { x: 450, y: 440, label: "Strategy Desk" },
  execution_desk: { x: 120, y: 160, label: "Execution Desk" },
  learning_desk: { x: 780, y: 460, label: "Learning Desk" },
};

const DEBATE_OFFSET = 46; // wide enough apart that the two name labels never collide

export const AGENTS: AgentDef[] = [
  { id: "market_scout", label: "Market Scout", color: 0x4fd1c5, home: ZONE_POSITIONS.scout_desk },
  {
    id: "bull_analyst", label: "Bull Analyst", color: 0x48bb78,
    home: { x: 300, y: 300 },
  },
  {
    id: "bear_analyst", label: "Bear Analyst", color: 0xf56565,
    home: { x: 600, y: 300 },
  },
  { id: "chief_manager", label: "Chief AI Manager", color: 0xecc94b, home: ZONE_POSITIONS.chief_office },
  { id: "risk_officer", label: "Risk Officer", color: 0xed8936, home: ZONE_POSITIONS.risk_desk },
  { id: "strategy_scientist", label: "Strategy Scientist", color: 0x9f7aea, home: ZONE_POSITIONS.strategy_desk },
  { id: "execution_agent", label: "Execution Agent", color: 0x4299e1, home: ZONE_POSITIONS.execution_desk },
  { id: "learning_agent", label: "Learning Agent", color: 0x38b2ac, home: ZONE_POSITIONS.learning_desk },
];

export const ALL_AGENT_IDS: AgentId[] = AGENTS.map((a) => a.id);

/** Where an agent should move to for a given destination zone - with the
 * bull/bear debate-room offset applied so both are visible at once. */
export function targetPositionFor(agentId: AgentId, zone: ZoneId): { x: number; y: number } {
  const pos = ZONE_POSITIONS[zone];
  if (zone === "debate_room") {
    if (agentId === "bull_analyst") return { x: pos.x - DEBATE_OFFSET, y: pos.y };
    if (agentId === "bear_analyst") return { x: pos.x + DEBATE_OFFSET, y: pos.y };
  }
  return { x: pos.x, y: pos.y };
}

export function agentById(id: AgentId): AgentDef {
  const found = AGENTS.find((a) => a.id === id);
  if (!found) throw new Error(`Unknown agent id: ${id}`);
  return found;
}

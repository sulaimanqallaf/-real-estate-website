import { describe, expect, it } from "vitest";
import { AGENTS, ALL_AGENT_IDS, agentById, targetPositionFor, ZONE_POSITIONS } from "../agentConfig";

describe("agentConfig", () => {
  it("defines exactly the 8 Phase 1 MVP agents", () => {
    expect(ALL_AGENT_IDS).toEqual([
      "market_scout", "bull_analyst", "bear_analyst", "chief_manager",
      "risk_officer", "strategy_scientist", "execution_agent", "learning_agent",
    ]);
    expect(AGENTS).toHaveLength(8);
  });

  it("gives every agent a distinct color", () => {
    const colors = new Set(AGENTS.map((a) => a.color));
    expect(colors.size).toBe(AGENTS.length);
  });

  it("gives every agent a home position", () => {
    for (const agent of AGENTS) {
      expect(typeof agent.home.x).toBe("number");
      expect(typeof agent.home.y).toBe("number");
    }
  });

  it("looks up an agent by id", () => {
    expect(agentById("chief_manager").label).toBe("Chief AI Manager");
  });

  it("throws for an unknown agent id", () => {
    // @ts-expect-error - intentionally invalid input for the test
    expect(() => agentById("not_an_agent")).toThrow();
  });

  it("offsets bull and bear analysts apart in the shared debate room", () => {
    const bullPos = targetPositionFor("bull_analyst", "debate_room");
    const bearPos = targetPositionFor("bear_analyst", "debate_room");
    expect(bullPos).not.toEqual(bearPos);
    expect(bullPos.y).toBe(ZONE_POSITIONS.debate_room.y);
    expect(bearPos.y).toBe(ZONE_POSITIONS.debate_room.y);
  });

  it("sends every other agent to the zone's own position unmodified", () => {
    const pos = targetPositionFor("risk_officer", "risk_desk");
    expect(pos).toEqual({ x: ZONE_POSITIONS.risk_desk.x, y: ZONE_POSITIONS.risk_desk.y });
  });
});

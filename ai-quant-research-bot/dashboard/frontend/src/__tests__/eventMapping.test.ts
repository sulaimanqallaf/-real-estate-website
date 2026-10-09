import { describe, expect, it } from "vitest";
import { ZONE_POSITIONS } from "../agentConfig";
import { resolveMovement } from "../eventMapping";
import type { AgentEvent } from "../types";

function makeEvent(overrides: Partial<AgentEvent>): AgentEvent {
  return {
    id: "1", ts: "2026-10-28T20:30:00+00:00", mode: "live", event_type: "scan",
    agent: null, zone: null, ticker: null, summary: "test", data: {},
    ...overrides,
  };
}

describe("resolveMovement - event type -> agent movement", () => {
  it("moves Market Scout on a scan event", () => {
    const movement = resolveMovement(makeEvent({ event_type: "scan", agent: "market_scout", zone: "scout_desk" }));
    expect(movement).toEqual({ agent: "market_scout", position: { x: ZONE_POSITIONS.scout_desk.x, y: ZONE_POSITIONS.scout_desk.y } });
  });

  it("moves Bull Analyst into the debate room on a debate event", () => {
    const movement = resolveMovement(makeEvent({ event_type: "debate", agent: "bull_analyst", zone: "debate_room" }));
    expect(movement?.agent).toBe("bull_analyst");
    expect(movement?.position.y).toBe(ZONE_POSITIONS.debate_room.y);
    expect(movement?.position.x).not.toBe(ZONE_POSITIONS.debate_room.x); // offset, not dead center
  });

  it("moves Bear Analyst into the debate room, offset from Bull Analyst", () => {
    const bull = resolveMovement(makeEvent({ event_type: "debate", agent: "bull_analyst", zone: "debate_room" }));
    const bear = resolveMovement(makeEvent({ event_type: "debate", agent: "bear_analyst", zone: "debate_room" }));
    expect(bull?.position.x).not.toBe(bear?.position.x);
  });

  it("moves Risk Officer on a risk_check event", () => {
    const movement = resolveMovement(makeEvent({ event_type: "risk_check", agent: "risk_officer", zone: "risk_desk" }));
    expect(movement?.agent).toBe("risk_officer");
  });

  it("moves Execution Agent on an execution event", () => {
    const movement = resolveMovement(makeEvent({ event_type: "execution", agent: "execution_agent", zone: "execution_desk" }));
    expect(movement?.agent).toBe("execution_agent");
  });

  it("moves Learning Agent on a learning event", () => {
    const movement = resolveMovement(makeEvent({ event_type: "learning", agent: "learning_agent", zone: "learning_desk" }));
    expect(movement?.agent).toBe("learning_agent");
  });

  it("returns null for a health event with no single agent, never guessing one", () => {
    const movement = resolveMovement(makeEvent({ event_type: "health", agent: null, zone: null }));
    expect(movement).toBeNull();
  });

  it("returns null when an agent is set but zone is missing", () => {
    const movement = resolveMovement(makeEvent({ event_type: "scan", agent: "market_scout", zone: null }));
    expect(movement).toBeNull();
  });
});

import { targetPositionFor } from "./agentConfig";
import type { AgentEvent, AgentId } from "./types";

export interface Movement {
  agent: AgentId;
  position: { x: number; y: number };
}

/** Pure mapping from one backend event to the scene movement it should
 * cause - requirement: "movement must map to event types." Returns
 * `null` for an event with no single-agent visual (e.g. a `"health"`
 * snapshot with `agent: null`) - the scene must never guess a default
 * agent/zone for those. Examples this directly implements:
 * - `scan` -> Market Scout moves to its zone (usually its own desk).
 * - `debate` -> Bull/Bear move into the shared debate room.
 * - `risk_check` -> Risk Officer moves/highlights at the risk desk.
 * - `execution` -> Execution Agent activates at the execution desk.
 * - `learning` -> Learning Agent activates at the learning desk.
 */
export function resolveMovement(event: AgentEvent): Movement | null {
  if (!event.agent || !event.zone) return null;
  return { agent: event.agent, position: targetPositionFor(event.agent, event.zone) };
}

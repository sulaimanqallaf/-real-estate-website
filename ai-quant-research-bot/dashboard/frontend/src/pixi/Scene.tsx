import { Application, Container, Graphics, Text } from "pixi.js";
import { useEffect, useRef } from "react";
import { AGENTS, ZONE_POSITIONS } from "../agentConfig";
import { resolveMovement } from "../eventMapping";
import type { AgentEvent, AgentId } from "../types";
import { computeWalkPose } from "./walkCycle";

const SCENE_WIDTH = 900;
const SCENE_HEIGHT = 560;
const MOVE_SPEED = 6; // px per animation frame, toward the target
const HIGHLIGHT_FRAMES = 30;
const SPEECH_BUBBLE_FRAMES = 150; // ~2.5s at 60fps
const ARRIVED_THRESHOLD_PX = 1.5;

interface AgentSprite {
  container: Container;
  body: Graphics;
  speechBubble: Container;
  speechText: Text;
  color: number;
  target: { x: number; y: number };
  highlightFramesLeft: number;
  speechFramesLeft: number;
  walkPhase: number;
}

export interface SceneProps {
  lastEvent: AgentEvent | null;
}

/** The animated PixiJS office scene - 8 procedural humanoid agents that
 * walk (a real leg/arm swing, see `walkCycle.ts`) between desks/rooms,
 * idle-bob when stationary, flash on arrival, and show a brief speech
 * bubble with the triggering event's own summary text. Pure rendering:
 * this component has no network/WebSocket code of its own - `lastEvent`
 * is handed to it by `App.tsx` from `useAgentEvents()`, so the
 * animation layer is identical across LIVE/REPLAY/DEMO (it has no idea
 * which mode produced the event) - see `eventMapping.ts` for the
 * event-type -> movement logic this builds on, unchanged from Phase 1. */
export function Scene({ lastEvent }: SceneProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const spritesRef = useRef<Map<AgentId, AgentSprite>>(new Map());
  const appRef = useRef<Application | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const app = new Application();
    let destroyed = false;

    app.init({ width: SCENE_WIDTH, height: SCENE_HEIGHT, background: "#0b1120", antialias: true }).then(() => {
      if (destroyed) return;
      appRef.current = app;
      host.appendChild(app.canvas);
      drawOfficeBackdrop(app);
      buildAgentSprites(app, spritesRef.current);
      app.ticker.add(() => tickAnimation(spritesRef.current));
    });

    return () => {
      destroyed = true;
      appRef.current?.destroy(true, { children: true });
      appRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!lastEvent) return;
    const movement = resolveMovement(lastEvent);
    if (!movement) return; // a health/info event with no single-agent visual - nothing to move

    const sprite = spritesRef.current.get(movement.agent);
    if (!sprite) return;
    sprite.target = movement.position;
    sprite.highlightFramesLeft = HIGHLIGHT_FRAMES;
    sprite.speechFramesLeft = SPEECH_BUBBLE_FRAMES;
    sprite.speechText.text = truncateSummary(lastEvent.summary);
  }, [lastEvent]);

  return <div ref={hostRef} className="pixi-scene-host" aria-label="Agent office scene" />;
}

function truncateSummary(summary: string): string {
  return summary.length > 42 ? `${summary.slice(0, 39)}...` : summary;
}

// --- office backdrop: desks, monitors, debate table, trading-floor strip ----

function drawOfficeBackdrop(app: Application) {
  const backdropLayer = new Container();
  app.stage.addChild(backdropLayer);

  // A thin "trading floor" strip along the bottom, suggesting a ticker tape.
  const floor = new Graphics();
  floor.rect(0, SCENE_HEIGHT - 14, SCENE_WIDTH, 14).fill({ color: 0x11182b });
  for (let x = 0; x < SCENE_WIDTH; x += 24) {
    floor.rect(x, SCENE_HEIGHT - 10, 10, 2).fill({ color: 0x2d3b55 });
  }
  backdropLayer.addChild(floor);

  for (const [zoneId, zone] of Object.entries(ZONE_POSITIONS)) {
    const isDebateRoom = zoneId === "debate_room";
    const box = new Graphics();
    box.roundRect(zone.x - 58, zone.y - 38, 116, 76, 10)
      .fill({ color: isDebateRoom ? 0x241a33 : 0x1a2332 })
      .stroke({ color: isDebateRoom ? 0x6b4fa0 : 0x2d3b55, width: isDebateRoom ? 1.5 : 1 });
    backdropLayer.addChild(box);

    if (isDebateRoom) {
      // A small round table in the middle of the debate room, to read as
      // a meeting space rather than another desk.
      const table = new Graphics();
      table.ellipse(zone.x, zone.y + 6, 34, 12).fill({ color: 0x3a2d55 }).stroke({ color: 0x6b4fa0, width: 1 });
      backdropLayer.addChild(table);
    } else {
      // A simple desk + monitor glyph for every other zone.
      const desk = new Graphics();
      desk.rect(zone.x - 22, zone.y + 2, 44, 6).fill({ color: 0x2d3b55 });
      desk.rect(zone.x - 10, zone.y - 10, 20, 13).fill({ color: 0x0f1628 }).stroke({ color: 0x2d3b55, width: 1 });
      desk.rect(zone.x - 3, zone.y + 2, 6, 4).fill({ color: 0x2d3b55 }); // monitor stand
      desk.rect(zone.x - 7, zone.y - 8, 14, 9).fill({ color: 0x1c4f6e }); // screen glow
      backdropLayer.addChild(desk);
    }

    const label = new Text({
      text: zone.label,
      style: { fill: 0x6b7a99, fontSize: 11, fontFamily: "monospace" },
    });
    label.anchor.set(0.5);
    label.position.set(zone.x, zone.y + 48);
    backdropLayer.addChild(label);
  }
}

// --- procedural humanoid agents ------------------------------------------------

function buildAgentSprites(app: Application, sprites: Map<AgentId, AgentSprite>) {
  const agentLayer = new Container();
  app.stage.addChild(agentLayer);

  for (const agent of AGENTS) {
    const container = new Container();
    container.position.set(agent.home.x, agent.home.y);

    const body = new Graphics();
    container.addChild(body);

    const label = new Text({
      text: agent.label,
      style: { fill: 0xd7e0f0, fontSize: 10, fontFamily: "monospace" },
    });
    label.anchor.set(0.5);
    label.position.set(0, 34);
    container.addChild(label);

    const speechBubble = new Container();
    speechBubble.visible = false;
    const bubbleBg = new Graphics();
    const speechText = new Text({
      text: "",
      style: { fill: 0x0b1120, fontSize: 10, fontFamily: "monospace", wordWrap: true, wordWrapWidth: 140 },
    });
    speechText.anchor.set(0.5);
    speechBubble.addChild(bubbleBg, speechText);
    speechBubble.position.set(0, -52);
    container.addChild(speechBubble);

    agentLayer.addChild(container);
    sprites.set(agent.id, {
      container, body, speechBubble, speechText, color: agent.color,
      target: { x: agent.home.x, y: agent.home.y },
      highlightFramesLeft: 0, speechFramesLeft: 0, walkPhase: 0,
    });

    drawHumanoid(body, agent.color, { leftLegOffset: 0, rightLegOffset: 0, leftArmOffset: 0, rightArmOffset: 0, bodyBob: 0 }, false);
  }
}

/** Draws a simple procedural humanoid: head, torso, two legs, two arms -
 * vector shapes only (no sprite-sheet art asset is available in this
 * environment, see docs/platform/PROGRESS_CHECKLIST.md section F for
 * that honestly-documented limitation). `pose` comes from
 * `walkCycle.computeWalkPose()` every frame. */
function drawHumanoid(g: Graphics, color: number, pose: ReturnType<typeof computeWalkPose>, highlighted: boolean) {
  g.clear();
  const bob = -pose.bodyBob;

  if (highlighted) {
    g.circle(0, -10 + bob, 24).fill({ color: 0xffffff, alpha: 0.18 });
  }

  // Legs (hip at y=8, feet at y=22, swinging at the hip).
  g.moveTo(-4, 8 + bob).lineTo(-4 + pose.leftLegOffset, 22 + bob).stroke({ color: 0x1a2332, width: 4, cap: "round" });
  g.moveTo(4, 8 + bob).lineTo(4 + pose.rightLegOffset, 22 + bob).stroke({ color: 0x1a2332, width: 4, cap: "round" });

  // Torso.
  g.roundRect(-7, -10 + bob, 14, 18, 4).fill({ color }).stroke({ color: 0xffffff, width: highlighted ? 1.5 : 0.5 });

  // Arms (shoulder at y=-6, hands at y=6, swinging at the shoulder).
  g.moveTo(-7, -6 + bob).lineTo(-7 + pose.leftArmOffset, 6 + bob).stroke({ color, width: 3, cap: "round" });
  g.moveTo(7, -6 + bob).lineTo(7 + pose.rightArmOffset, 6 + bob).stroke({ color, width: 3, cap: "round" });

  // Head.
  g.circle(0, -18 + bob, 7).fill({ color: 0xf0d9b5 }).stroke({ color: 0xffffff, width: highlighted ? 1.5 : 0.5 });
}

function drawSpeechBubble(bubble: Container, text: Text) {
  const bg = bubble.getChildAt(0) as Graphics;
  const padding = 6;
  const width = Math.min(150, text.width + padding * 2);
  const height = text.height + padding * 2;
  bg.clear();
  bg.roundRect(-width / 2, -height, width, height, 6).fill({ color: 0xe8edf7 }).stroke({ color: 0x1a2332, width: 1 });
  bg.poly([-4, 0, 4, 0, 0, 7]).fill({ color: 0xe8edf7 });
  text.position.set(0, -height / 2);
}

function tickAnimation(sprites: Map<AgentId, AgentSprite>) {
  for (const [, sprite] of sprites) {
    const { container, target } = sprite;
    const dx = target.x - container.position.x;
    const dy = target.y - container.position.y;
    const dist = Math.hypot(dx, dy);
    const isWalking = dist > ARRIVED_THRESHOLD_PX;
    if (isWalking) {
      const step = Math.min(MOVE_SPEED, dist);
      container.position.x += (dx / dist) * step;
      container.position.y += (dy / dist) * step;
    }
    sprite.walkPhase += 1;

    const wasHighlighted = sprite.highlightFramesLeft > 0;
    if (wasHighlighted) sprite.highlightFramesLeft -= 1;
    const isHighlighted = sprite.highlightFramesLeft > 0;

    const pose = computeWalkPose(sprite.walkPhase, isWalking);
    drawHumanoid(sprite.body, sprite.color, pose, isHighlighted);

    if (sprite.speechFramesLeft > 0) {
      sprite.speechFramesLeft -= 1;
      sprite.speechBubble.visible = true;
      drawSpeechBubble(sprite.speechBubble, sprite.speechText);
    } else {
      sprite.speechBubble.visible = false;
    }
  }
}

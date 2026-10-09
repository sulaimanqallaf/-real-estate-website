import { Application, Container, Graphics, Text } from "pixi.js";
import { useEffect, useRef } from "react";
import { AGENTS, ZONE_POSITIONS } from "../agentConfig";
import { resolveMovement } from "../eventMapping";
import type { AgentEvent, AgentId } from "../types";

const SCENE_WIDTH = 900;
const SCENE_HEIGHT = 560;
const AGENT_RADIUS = 18;
const MOVE_SPEED = 6; // px per animation frame, toward the target
const HIGHLIGHT_FRAMES = 30;

interface AgentSprite {
  container: Container;
  body: Graphics;
  color: number;
  target: { x: number; y: number };
  highlightFramesLeft: number;
}

export interface SceneProps {
  lastEvent: AgentEvent | null;
}

/** The animated PixiJS office scene - 8 agents on fixed home desks that
 * move to a zone and flash when a matching event arrives. Pure
 * rendering: this component has no network/WebSocket code of its own -
 * `lastEvent` is handed to it by `App.tsx` from `useAgentEvents()`, so
 * the animation layer is identical across LIVE/REPLAY/DEMO (it has no
 * idea which mode produced the event). */
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
      buildScene(app, spritesRef.current);
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
  }, [lastEvent]);

  return <div ref={hostRef} className="pixi-scene-host" aria-label="Agent office scene" />;
}

function buildScene(app: Application, sprites: Map<AgentId, AgentSprite>) {
  const zoneLayer = new Container();
  app.stage.addChild(zoneLayer);

  for (const [zoneId, zone] of Object.entries(ZONE_POSITIONS)) {
    const box = new Graphics();
    box.roundRect(zone.x - 55, zone.y - 35, 110, 70, 10).fill({ color: 0x1a2332 }).stroke({ color: 0x2d3b55, width: 1 });
    zoneLayer.addChild(box);

    const label = new Text({
      text: zone.label,
      style: { fill: 0x6b7a99, fontSize: 11, fontFamily: "monospace" },
    });
    label.anchor.set(0.5);
    label.position.set(zone.x, zone.y + 45);
    zoneLayer.addChild(label);
    void zoneId;
  }

  const agentLayer = new Container();
  app.stage.addChild(agentLayer);

  for (const agent of AGENTS) {
    const container = new Container();
    container.position.set(agent.home.x, agent.home.y);

    const body = new Graphics();
    drawAgentBody(body, agent.color, false);
    container.addChild(body);

    const label = new Text({
      text: agent.label,
      style: { fill: 0xd7e0f0, fontSize: 10, fontFamily: "monospace" },
    });
    label.anchor.set(0.5);
    label.position.set(0, AGENT_RADIUS + 12);
    container.addChild(label);

    agentLayer.addChild(container);
    sprites.set(agent.id, {
      container, body, color: agent.color, target: { x: agent.home.x, y: agent.home.y }, highlightFramesLeft: 0,
    });
  }
}

function drawAgentBody(body: Graphics, color: number, highlighted: boolean) {
  body.clear();
  if (highlighted) {
    body.circle(0, 0, AGENT_RADIUS + 6).fill({ color: 0xffffff, alpha: 0.25 });
  }
  body.circle(0, 0, AGENT_RADIUS).fill({ color }).stroke({ color: 0xffffff, width: highlighted ? 2 : 0.5 });
}

function tickAnimation(sprites: Map<AgentId, AgentSprite>) {
  for (const [, sprite] of sprites) {
    const { container, target } = sprite;
    const dx = target.x - container.position.x;
    const dy = target.y - container.position.y;
    const dist = Math.hypot(dx, dy);
    if (dist > 1) {
      const step = Math.min(MOVE_SPEED, dist);
      container.position.x += (dx / dist) * step;
      container.position.y += (dy / dist) * step;
    }

    const wasHighlighted = sprite.highlightFramesLeft > 0;
    if (wasHighlighted) sprite.highlightFramesLeft -= 1;
    const isHighlighted = sprite.highlightFramesLeft > 0;
    if (isHighlighted !== wasHighlighted) {
      drawAgentBody(sprite.body, sprite.color, isHighlighted);
    }
  }
}

"""AI Agents Dashboard backend - FastAPI app (Phase 1 MVP).

Run with: `uvicorn app.main:app --reload --port 8800` from
`dashboard/backend/` (see `dashboard/README.md` for the full command).

**Read-only, by construction**: every HTTP/WS handler below only ever
calls into `app.readonly` (itself restricted to a fixed allow-list of
read-only `src.*` modules - see its docstring) or the three event
sources in `app.sources`. There is no handler anywhere in this file that
writes to a bot-owned file, places an order, or changes a risk/execution
setting - see `tests/test_readonly_safety.py` and `tests/test_api_
endpoints.py`.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from . import readonly
from .events import AgentEvent
from .sources.demo_source import demo_events
from .sources.live_source import live_events
from .sources.replay_source import replay_events

app = FastAPI(title="AI Agents Dashboard (Phase 1 MVP)")

# Local-dev-only CORS: the Vite dev server runs on a different origin
# than this API. Configurable via DASHBOARD_FRONTEND_ORIGIN for anyone
# running the frontend build somewhere else; defaults cover the two
# ports Vite picks by default.
_frontend_origin = os.environ.get("DASHBOARD_FRONTEND_ORIGIN")
_allowed_origins = [_frontend_origin] if _frontend_origin else [
    "http://localhost:5173", "http://127.0.0.1:5173",
]
app.add_middleware(
    CORSMiddleware, allow_origins=_allowed_origins, allow_methods=["GET"], allow_headers=["*"],
)


def _demo_mode_allowed() -> bool:
    """DEMO mode is opt-in, never default - "no fake trades or profits
    in LIVE mode" is enforced here by making the synthetic source
    unreachable at all unless this is explicitly set, so there is no
    code path where a misconfigured client silently gets synthetic data
    back labeled as anything other than what it asked for."""
    return os.environ.get("DASHBOARD_ALLOW_DEMO") == "1"


@app.get("/api/health")
def api_health() -> dict:
    return readonly.health_report()


@app.get("/api/run-status")
def api_run_status() -> dict:
    return readonly.run_status()


@app.get("/api/circuit-breaker")
def api_circuit_breaker() -> dict:
    return readonly.circuit_breaker_status()


@app.get("/api/decisions")
def api_decisions(limit: int = 25) -> list[dict]:
    return readonly.recent_decisions(limit=limit)


@app.get("/api/tradingagents")
def api_tradingagents(limit: int = 10) -> list[dict]:
    return readonly.tradingagents_outputs(limit=limit)


@app.get("/api/spend")
def api_spend() -> dict | None:
    return readonly.spend_summary()


@app.get("/api/paper-pnl")
def api_paper_pnl() -> dict | None:
    return readonly.paper_pnl_summary()


@app.get("/api/modes")
def api_modes() -> dict:
    return {"live": True, "replay": True, "demo": _demo_mode_allowed()}


async def _send_event(ws: WebSocket, event: AgentEvent) -> None:
    await ws.send_text(event.model_dump_json())


@app.websocket("/ws")
async def ws_events(
    websocket: WebSocket,
    mode: str = "live",
    since: str | None = None,
    until: str | None = None,
    speed: float = 1.0,
) -> None:
    await websocket.accept()

    if mode == "demo" and not _demo_mode_allowed():
        await websocket.send_json(
            {"error": "DEMO mode is disabled on this server. Set DASHBOARD_ALLOW_DEMO=1 to enable it."}
        )
        await websocket.close(code=4403)
        return

    step_delay = max(0.05, 0.6 / max(speed, 0.01))

    try:
        if mode == "live":
            async for event in live_events():
                await _send_event(websocket, event)
        elif mode == "replay":
            async for event in replay_events(since=since, until=until, step_delay_seconds=step_delay):
                await _send_event(websocket, event)
        elif mode == "demo":
            async for event in demo_events(step_delay_seconds=step_delay):
                await _send_event(websocket, event)
        else:
            await websocket.send_json({"error": f"Unknown mode: {mode!r} (expected live|replay|demo)"})
            await websocket.close(code=4400)
    except WebSocketDisconnect:
        pass

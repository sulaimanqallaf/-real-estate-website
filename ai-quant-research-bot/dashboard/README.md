# AI Agents Dashboard (Phase 1 MVP)

A live-moving, read-only visualization of the AI Quant Research Bot's
agents, built as a **separate application** that sits beside the bot
(`ai-quant-research-bot/dashboard/`) and never modifies it.

**Hard guarantees for this milestone (verified by the test suites
below, not just by convention):**
- Completely read-only - no order placement, no risk-setting controls,
  no write path anywhere in the backend (`tests/test_readonly_safety.py`
  asserts every HTTP route is a `GET` or `websocket`, and that the one
  integration seam into the bot's code, `app/readonly.py`, only imports
  from an explicit allow-list of read-only `src.*` modules - never
  `order_manager`, `execution_policy`, `ibkr_client`, `broker`,
  `approval_bridge`, or `src.main.run`).
- No credentials reach the frontend - the backend never reads `.env`/API
  keys (`app/readonly.py` only ever calls `utils.load_config()`, never
  `utils.get_env_var()`), and every string it returns is passed through
  the bot's own `redact_secrets()` as a second layer of defense.
- No fake trades or profits in LIVE mode - LIVE only ever plays real
  `decision_ledger` rows and real `paper_trades.csv` figures; DEMO mode
  (synthetic data) is refused by the server unless you explicitly set
  `DASHBOARD_ALLOW_DEMO=1`.
- Does **not** touch the scheduled research service, `execution.mode`,
  TradingAgents shadow mode, or the launchd scheduler added in earlier
  milestones - this is a new, separate frontend+backend pair that only
  ever *reads* the files those systems already write.

---

## 1. Architecture

```
dashboard/
  backend/   FastAPI app (Python) - read-only REST + WebSocket event stream
  frontend/  React + TypeScript + PixiJS - the animated scene + dashboard panels
```

The backend is the ONLY thing that touches the bot's code/data
(`app/readonly.py`). The frontend only ever talks to the backend's own
HTTP/WebSocket API - it has no knowledge of `config/settings.yaml`,
journal file paths, or secrets.

## 2. The event schema

Every event on the WebSocket stream (`/ws`) is one JSON object matching
this shape (`dashboard/backend/app/events.py`'s `AgentEvent`, mirrored in
`dashboard/frontend/src/types.ts`):

```jsonc
{
  "id": "a1b2c3...",                 // opaque unique id
  "ts": "2026-10-28T20:30:00+00:00", // ISO 8601, UTC
  "mode": "live" | "replay" | "demo",
  "event_type": "scan" | "debate" | "risk_check" | "decision"
               | "execution" | "learning" | "health" | "info",
  "agent": "market_scout" | "bull_analyst" | "bear_analyst"
          | "chief_manager" | "risk_officer" | "strategy_scientist"
          | "execution_agent" | "learning_agent" | null,
  "zone": "scout_desk" | "debate_room" | "chief_office" | "risk_desk"
         | "strategy_desk" | "execution_desk" | "learning_desk" | null,
  "ticker": "AMD" | null,
  "summary": "human-readable one-liner for the event timeline",
  "data": { "...": "event-type-specific extra fields" }
}
```

`agent`/`zone` are `null` for an event with no single-agent visual (e.g.
a periodic `"health"` snapshot) - the frontend never guesses a default
agent or zone for those; nothing moves.

**Event type -> agent/zone mapping** (the actual movement behavior):

| event_type  | agent(s)                        | zone            | Triggered by |
|---|---|---|---|
| `scan`      | `market_scout`                  | `scout_desk`    | A candidate was evaluated (decision ledger row) |
| `debate`    | `bull_analyst`, `bear_analyst`   | `debate_room`   | Real bull/bear research content exists for the candidate |
| `risk_check`| `risk_officer`                  | `risk_desk`     | Portfolio/regime risk evaluation ran |
| `decision`  | `chief_manager`                 | `chief_office`  | The pipeline's AUTO_EXECUTE/REQUIRE_APPROVAL/WATCH_ONLY/REJECT classification |
| `execution` | `execution_agent`               | `execution_desk`| A **paper** trade was actually recorded (never a live order) |
| `learning`  | `learning_agent`                | `learning_desk` | An outcome/reflection was recorded for a prior decision |
| `health`    | `null`                          | `null`          | Periodic health/status snapshot - panels update, nothing moves |
| `info`      | varies / `null`                 | varies / `null` | Anything else worth showing in the timeline |

## 3. The three modes

| Mode | What it streams | Fake data allowed? |
|---|---|---|
| **LIVE** | Polls the REAL `decision_ledger.db` for new rows and plays each one's real event sequence, plus a periodic real health snapshot. | **Never.** No row -> no event, ever. |
| **REPLAY** | Plays ALL historical `decision_ledger.db` rows (optionally `?since=`/`?until=`), at a configurable `?speed=` multiplier, then stops. | **Never** - same real rows as LIVE, just not live-polled. |
| **DEMO** | A small scripted loop over synthetic tickers (`DEMO1`, `DEMO2`, `DEMO3`). | **Only here**, and only when the server operator has explicitly set `DASHBOARD_ALLOW_DEMO=1` - every DEMO event is stamped `"mode": "demo"` and the frontend renders a permanent "DEMO MODE - SYNTHETIC DATA" banner while connected to it. A client asking for `mode=demo` against a server that hasn't opted in gets a WebSocket error, not synthetic data silently relabeled. |

## 4. Dashboard panels

Health Status, Run Status, Event Timeline, Latest Research Decisions,
TradingAgents Outputs, API Spend Summary (committed vs reserved, never
blended), Paper P&L (renders "No data yet" - never `$0.00` - until a
real `paper_trades.csv` row exists), a DEMO-mode banner, a disconnected-
stream banner, a stale/data-check-failed banner, and a circuit-breaker-
halted banner (the nearest real signal this Phase 1 MVP has to "broker
unavailable" - execution_policy/broker-level connectivity is out of
scope for this read-only dashboard).

## 5. Running it on your Mac

### Backend

```bash
cd ai-quant-research-bot/dashboard/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Also needs the main bot's own dependencies on the same interpreter,
# since app/readonly.py imports src.* directly:
pip install -r ../../requirements.txt

uvicorn app.main:app --reload --port 8800
```

### Frontend

```bash
cd ai-quant-research-bot/dashboard/frontend
npm install
cp .env.example .env   # VITE_DASHBOARD_API_BASE=http://localhost:8800
npm run dev
```

Open the printed `http://localhost:5173` URL. LIVE mode is selected by
default and will show "No data yet" until the bot's own scheduled run
(see the main README's sections 6-7) has written at least one
`decision_ledger` row - this is correct, not a bug: LIVE never
fabricates activity.

**To see the animation working immediately** (useful for a first look,
or to demo the scene without waiting for a real run): start the backend
with `DASHBOARD_ALLOW_DEMO=1 uvicorn app.main:app --port 8800`, then
click **DEMO** in the dashboard's mode bar.

**To replay a specific historical window:**

```
ws://localhost:8800/ws?mode=replay&since=2026-10-01&until=2026-10-09&speed=4
```

(the frontend's mode bar currently always replays the full history -
pass `since`/`until`/`speed` via the browser URL/devtools console if you
want a bounded/faster replay for Phase 1; a dedicated UI control for
this is a natural Phase 2 addition.)

### Uninstall / stop

Both are plain local dev processes - `Ctrl+C` either one, no
system-level install (no launchd agent, no background service) is
created by this milestone.

## 6. Tests

```bash
# Backend (28 tests: event schema, read-only safety guardrails, mode
# isolation / DEMO gating, API endpoints)
cd ai-quant-research-bot/dashboard/backend
pip install -r requirements.txt
pytest tests/ -v

# Frontend (15 tests: agent config invariants, event-type -> movement
# mapping)
cd ai-quant-research-bot/dashboard/frontend
npm install
npm run test
```

## 7. What's deliberately NOT in Phase 1

- No write-back of any kind (approve/reject/halt/resume from the
  dashboard) - this is a separate, future milestone, and would need its
  own explicit authorization design, not something to add quietly here.
- No authentication/multi-user support - this is a local, single-user
  dev tool for now.
- REPLAY's `since`/`until`/`speed` have no dedicated UI control yet
  (see above) - functional via the WebSocket URL, not yet wired to a
  date-picker in the mode bar.
- Broker/IBKR connectivity has no dedicated real-time signal in this
  dashboard - the circuit-breaker-halted banner is the closest available
  real proxy for now.

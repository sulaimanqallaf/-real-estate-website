# Containerization (deliverable H)

**Scope: the DASHBOARD only (backend + frontend). NOT deployed anywhere.**
Nothing in this sprint touched any cloud account, and the bot's own
scheduled jobs (`main.py`, `after_close.py`, `position_monitor.py`) are
deliberately NOT containerized yet - see "What's intentionally not
containerized" below.

## What was actually verified (not just written)

Unlike most "prepared but unverified" containerization work, this one was
locally build- and run-verified in this sandbox: the Docker daemon was
started, both images were built from the real Dockerfiles, run as real
containers, and exercised over HTTP:

- `dashboard/backend/Dockerfile` built successfully (Python 3.11-slim,
  installs both the bot's own `requirements.txt` and the dashboard
  backend's own) and the resulting container, started with the SAME
  read-only bind mount `deploy/docker-compose.yml` uses, served
  `/api/health` and `/api/modes` correctly against this repo's real
  (empty) data.
- **The read-only mount was verified to actually enforce the "never
  writes" guarantee at the OS level**, not just by code review: a
  deliberate write attempt from inside the running container
  (`open(".../data/test_write.txt", "w")`) failed with `OSError: [Errno
  30] Read-only file system` - the exact error you'd want to see.
- `dashboard/frontend/Dockerfile` built successfully (Node 20 build
  stage -> nginx:alpine serve stage) and the resulting container served
  the real page (`<title>AI Agents Dashboard</title>`) over HTTP.
- One sandbox-only wrinkle, NOT present in the committed Dockerfile: the
  pip install step initially failed on this sandbox's own TLS-
  intercepting network proxy (a self-signed cert the container doesn't
  trust by default - unrelated to the Dockerfile itself, see this
  repo's own environment notes on `/root/.ccr/`). Verified past that
  point using a temporary `pip config set global.trusted-host` added
  ONLY to a throwaway test copy of the Dockerfile, never committed -
  outside this sandbox, against the real pypi.org, this step needs no
  such workaround.

Every test image/container was removed after verification; nothing was
left running, and no stray file was left in the bot's `data/` tree (the
read-only mount made that structurally impossible anyway).

## How to run it yourself

```bash
cd ai-quant-research-bot/deploy
docker compose build
docker compose up
# backend:  http://localhost:8800
# frontend: http://localhost:8080
```

Stop with `docker compose down` - this only ever touches the two
containers this compose file defines, never anything else running on
your machine (including the Mac launchd scheduler, which this doesn't
know exists).

## Process supervision / restart recovery

`restart: unless-stopped` on both services (Docker's own built-in
supervisor) - if the dashboard backend crashes, Docker restarts it; since
`app/readonly.py` has no persistent in-memory state that matters (every
request re-reads the journal/config fresh), a restart loses nothing. The
backend's `healthcheck` (`/api/modes`) lets `depends_on: condition:
service_healthy` hold the frontend back until the backend is actually
answering, not just "started."

## Secrets / environment management

The dashboard backend needs **zero secrets** - it never reads `.env`,
never calls `utils.get_env_var()`, only ever reads `config/settings.yaml`
(file paths) and already-written journals. `DASHBOARD_ALLOW_DEMO` is the
only environment variable it reads, and it's a feature flag, not a
secret. If you want it set, add a `.env` file next to
`deploy/docker-compose.yml` (`docker compose` reads one automatically) -
never bake it into the image.

## Logging

Both containers log to stdout/stderr (uvicorn's own access/error log for
the backend, nginx's own access log for the frontend) - `docker compose
logs -f` is the whole story; no separate log-shipping was set up this
sprint (a real cloud deployment would want one, e.g. shipping stdout to
whatever the host platform provides - left for when deployment is
actually authorized).

## Health checks / alerting

The backend's `healthcheck` directive covers container-level liveness.
Nothing in THIS sprint wires that into an external alerting system (e.g.
paging on a crash loop) - that's a cloud-deployment-time concern, out of
scope while nothing is deployed.

## Single-active-executor guarantee

The DASHBOARD has no executor to guarantee singularity for - it's
read-only, so running two copies of it is harmless (both just read the
same files; neither writes). The guarantee that actually matters - never
two copies of `main.py`'s daily run, or two `position_monitor.py`
processes, racing each other - is unchanged from the prior sprint's work
(`execution/process_lock.py`'s OS-level flock) and lives entirely outside
this containerization effort, since the bot's own scheduled jobs are not
containerized here.

## Broker connectivity / reconnect strategy

Not applicable to the dashboard (it never connects to a broker - see
`docs/platform/BLOCKERS.md` item 1 for the real IBKR connection's own
reconnect behavior, which is `execution/ibkr_client.py`'s territory,
unchanged by this sprint).

## What's intentionally NOT containerized

The bot's own scheduled jobs (`main.py`, `after_close.py`,
`position_monitor.py`, `approval_listener.py`) are not containerized this
sprint, deliberately:

1. The user's real Mac launchd setup for these is working today and
   explicitly must not be disrupted - containerizing them would mean
   designing a MIGRATION off launchd, which is exactly the kind of
   decision that deserves its own reviewed milestone, not a drive-by
   inclusion here.
2. They need real credentials (Telegram, optionally OpenAI/Anthropic for
   TradingAgents, optionally IBKR) and a real IBKR TWS/Gateway session -
   containerizing broker-facing code without a live session to validate
   against would produce exactly the kind of "looks done, never actually
   verified" result this sprint's own instructions warn against.

When cloud deployment is actually authorized, containerizing these is
the natural next step - `deploy/docker-compose.yml` is a reasonable
starting shape to extend (one more service per job, same bind-mount
pattern for config, with real secrets injected via the deployment
platform's own secret manager rather than baked into any image).

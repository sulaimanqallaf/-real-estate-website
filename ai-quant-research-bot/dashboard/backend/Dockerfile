# AI Quant Trading Platform sprint, deliverable H (containerization prep -
# NOT deployed anywhere by this sprint; see docs/platform/DEPLOYMENT.md).
#
# Build context MUST be the ai-quant-research-bot repo ROOT, not this
# directory - see deploy/docker-compose.yml's `build.context: ..`. This
# image needs BOTH this dashboard's own requirements.txt AND the main
# bot's requirements.txt, because app/readonly.py imports `src.*`
# directly (the dashboard backend is not a standalone package - it is
# deliberately the main bot's own code, read-only, plus a thin FastAPI
# layer on top).
FROM python:3.11-slim

WORKDIR /workspace/ai-quant-research-bot

COPY requirements.txt ./requirements.txt
COPY dashboard/backend/requirements.txt ./dashboard/backend/requirements.txt

RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir -r dashboard/backend/requirements.txt

# Deliberately NO `COPY . .` here. The bot's own code AND data/config are
# bind-mounted at runtime (see docker-compose.yml) instead of baked into
# the image - a code change never requires a rebuild, and the bot's
# journals/caches/config never leave the host filesystem to be duplicated
# inside an image layer. This image provides the Python RUNTIME only.
WORKDIR /workspace/ai-quant-research-bot/dashboard/backend
EXPOSE 8800

# DASHBOARD_ALLOW_DEMO is deliberately NOT set here - it defaults to
# disabled (see app/main.py) unless explicitly passed in docker-compose's
# `environment:` section, same opt-in-only posture as running it bare.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8800"]

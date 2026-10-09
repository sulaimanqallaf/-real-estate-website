# AI Quant Trading Platform sprint, deliverable H (containerization prep -
# NOT deployed anywhere by this sprint; see docs/platform/DEPLOYMENT.md).
#
# Build context is this directory (dashboard/frontend/) - unlike the
# backend image, the frontend has no dependency on the main bot's code,
# only on its own package.json.
#
# Stage 1: build the static production bundle.
FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Baked in at BUILD time (Vite env vars are compiled into the bundle, not
# read at container start) - override via docker-compose's `build.args`
# if the backend isn't reachable at the default localhost:8800.
ARG VITE_DASHBOARD_API_BASE=http://localhost:8800
ENV VITE_DASHBOARD_API_BASE=$VITE_DASHBOARD_API_BASE
RUN npm run build

# Stage 2: serve the static bundle - no Node runtime in the final image.
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

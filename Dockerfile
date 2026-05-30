# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS python-deps
WORKDIR /app
ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.5.31 /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM node:22-slim AS web-deps
WORKDIR /app/web
RUN corepack enable && corepack prepare pnpm@10.33.1 --activate
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

FROM node:22-slim AS web-builder
WORKDIR /app/web
RUN corepack enable && corepack prepare pnpm@10.33.1 --activate
ARG NEXT_PUBLIC_APP_VERSION=0.1.2
ARG NEXT_PUBLIC_GIT_SHA
ARG NEXT_PUBLIC_BUILD_TIME
ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_API_BASE_URL=/api \
    NEXT_PUBLIC_MOCK=false \
    NEXT_PUBLIC_APP_VERSION=${NEXT_PUBLIC_APP_VERSION} \
    NEXT_PUBLIC_GIT_SHA=${NEXT_PUBLIC_GIT_SHA} \
    NEXT_PUBLIC_BUILD_TIME=${NEXT_PUBLIC_BUILD_TIME}
COPY --from=web-deps /app/web/node_modules ./node_modules
COPY web ./
RUN pnpm build

FROM python:3.12-slim AS runtime
WORKDIR /app
ARG APP_GIT_SHA
ARG APP_BUILD_TIME
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    APP_GIT_SHA=${APP_GIT_SHA} \
    APP_BUILD_TIME=${APP_BUILD_TIME} \
    DATABASE_URL=sqlite:////data/app.sqlite3 \
    CORS_ALLOW_ORIGINS=http://localhost:3000,http://127.0.0.1:3000

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tini \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-deps /app/.venv ./.venv
COPY app ./app
COPY fixtures ./fixtures
COPY --from=web-builder /app/web/.next/standalone ./web
COPY --from=web-builder /app/web/.next/static ./web/.next/static
COPY --from=web-builder /app/web/public ./web/public
COPY docker/start.sh ./docker/start.sh

VOLUME ["/data"]
EXPOSE 3000
ENTRYPOINT ["tini", "--"]
CMD ["/app/docker/start.sh"]

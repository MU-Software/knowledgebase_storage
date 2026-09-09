# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.13
ARG NODE_VERSION=22

FROM node:${NODE_VERSION}-alpine AS frontend-builder
ENV PNPM_HOME="/pnpm" \
    PATH="/pnpm:$PATH"
RUN corepack enable

WORKDIR /frontend
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm fetch
COPY frontend/ ./
RUN pnpm install -r --offline && pnpm run build

FROM python:${PYTHON_VERSION}-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV TZ=Asia/Seoul \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project --no-dev

COPY backend/ ./backend/
COPY --from=frontend-builder /frontend/dist/index.html ./backend/frontend/index.html

RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

EXPOSE 8006
CMD ["python", "-m", "backend"]

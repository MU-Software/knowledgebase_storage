# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.13
ARG NODE_VERSION=22
ARG DUCKDB_VERSION=1.5.6

FROM duckdb/duckdb:${DUCKDB_VERSION} AS duckdb

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

RUN --mount=type=cache,target=/var/cache/apt --mount=type=cache,target=/var/lib/apt \
    apt-get update && apt-get install --no-install-recommends -y \
        git file sqlite3 jq xxd poppler-utils binutils binutils-multiarch xz-utils zstd bzip2 unzip libarchive-tools \
        mediainfo libxml2-utils ripgrep catdoc protobuf-compiler hdf5-tools \
    && rm -rf /var/lib/apt/lists/*

COPY --from=duckdb /duckdb /usr/local/lib/duckdb/duckdb
COPY --chmod=755 dockerfiles/duckdb.sh /usr/local/bin/duckdb
RUN /usr/local/lib/duckdb/duckdb -no-init -c "SET extension_directory='/opt/duckdb/extensions'; INSTALL sqlite; INSTALL excel;" \
    && chmod -R a+rX /opt/duckdb \
    && mkdir -p /run/kbstore && chown 1000:1000 /run/kbstore

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project --no-dev

COPY backend/ ./backend/
COPY --from=frontend-builder /frontend/dist/index.html ./backend/frontend/index.html

RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

EXPOSE 8006
CMD ["python", "-m", "backend"]

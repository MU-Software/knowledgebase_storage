# knowledgebase-storage

Collects project knowledge from many devices and LLM services (Claude Code, Codex, …),
summarizes it, and serves it as a wiki.

## How it works

```
  devices (hook) ──POST /api/jobs──►  kbstore-api ──► job queue (Postgres)
                                          │  ▲                        │
                                     wiki │  └── claim / result ── kbstore-worker
                                          ▼                        │   ├─► llama.cpp (local)
                                       markdown ◄──────────────────┘   └─► Claude API (overdue)
                                          │
                                          └──watch──► basic-memory ──► MCP gateway
```

Storage, search and MCP exposure are [basic-memory](https://github.com/basicmachines-co/basic-memory),
used unmodified. This repository adds only what it lacks: ingest, summarization,
provenance tagging and the wiki. The two sides meet at one markdown directory,
so basic-memory is never imported and its AGPL does not propagate here.

Three rules shape the design:

- **The hook never summarizes.** It holds the session, so it enqueues and returns.
- **The worker pulls.** Jobs pile up while the LLM is unreachable and drain when it returns.
- **One place calls the LLMs.** Providers live in the database, ordered by priority and gated
  by job age; the worker walks that list. The API only owns the queue and its housekeeping.
- **Only bootstrap values live in the environment.** Everything else is a database row you
  edit in the wiki, which the worker fetches with an ETag and a 304.

## Development

```sh
make init                              # uv sync + pnpm install + dotenv
make dev-up COMPOSE="podman compose"   # local postgres
make migrate
make api                               # http://127.0.0.1:8006
make frontend-dev                      # vite dev server, /api proxied to 8006
make worker                            # summarization worker (needs llama.cpp)
make lint
pre-commit install
```

The frontend is inlined into a single `index.html` by `vite-plugin-singlefile`
and served by FastAPI, so deployment carries no static-file paths.

## Hooks

See [`hooks/README.md`](hooks/README.md).

## Deployment

Merge `infra/docker-compose.workbench.yaml` into the compose file on the host.
Exposure is a tailnet IP binding only — nginx is not involved.

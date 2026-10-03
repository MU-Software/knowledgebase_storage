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

Claude Code and Codex talk to the API through [`hooks/kbstore_hook.py`](hooks/kbstore_hook.py):

- `Stop` posts the conversation to `/api/jobs` and uploads changed Claude memory files to
  `/api/wiki/memories`, which keeps them verbatim instead of summarizing them.
- `SessionStart` downloads the project's memory and adds `/api/wiki/context` to the session:
  recent summaries, the last requests of sessions not summarized yet, and the memory list for Codex.

Three rules shape the design:

- **The hook never summarizes.** It holds the session, so it enqueues and returns.
- **The worker pulls.** Jobs pile up while the LLM is unreachable and drain when it returns.
- **One place calls the LLMs.** Providers live in the database, gated by job age. Each one
  claims its own jobs, so they run in parallel, and falls back to the others by priority when
  it fails. The API only owns the queue and its housekeeping.
- **Projects are directories.** `projects/<name>/` holds one project's notes, and a project filed
  under another is a directory inside it: its sessions start with the parent's notes as background.
  The wiki moves, merges and deletes them, and a merged name redirects to where it went, so the
  hooks keep sending whatever the git remote says.
- **Idle providers keep the place tidy.** Session jobs come first; when none is waiting, a provider
  that takes background work rewrites each project's `overview.md` from its notes and the pages of
  the projects under it, proposes projects that should be merged or nested, links notes to the ones
  they continue, and redoes the summaries a weaker provider wrote while the transcript is still inside
  `transcript_retention_hours`. Leave that off for a provider you pay per token.
- **Raw data is kept, notes are derived.** `STORAGE_DIR` holds every session's original jsonl, one
  bare repository per git network, and worktree snapshots. The hook appends to the transcript from the
  byte the server already has, and uploads history as a filtered bundle. Notes, splits, overviews and
  links are rebuilt from that at any time with `POST /api/raw/rebuild`.
- **The summarizer checks the repository.** A session job carries its requests one block each, the
  repository's current state and a `network_id`. The worker explores with read-only git tools through
  `/api/git/query`, writes one entry per request, then verifies what it claimed about code and applies
  the corrections. Prompts are database rows, edited and versioned in the wiki.
- **Projects are scopes.** A `project_entry` holds what a project is, in your words, and the
  directories its sessions come from. One repository can hold several, so a session is filed per
  request by touched path first and by the model only when the path says nothing.
- **Links carry a description.** `same`, `part_of` and `related` all live in one table with the
  sentence that explains them and, when you wrote one, your own words. Judgements are advice: a
  candidate pair is found by code, and nothing is applied until you confirm it.
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

## Authentication

- Wiki: JWT sign-in. Create a user with `uv run python -m backend.cli create-user <name>`.
- Hooks and the importer: an API key from the wiki's API keys page, sent as `X-API-Key`.
- Worker: `WORKER_API_KEY` of the api.
- Failed sign-ins are counted per client IP and per username; past the limits in the runtime settings,
  sign-in answers 429. Behind a proxy that does not connect from 127.0.0.1, set `FORWARDED_ALLOW_IPS`
  so uvicorn sees the real client IP.
- `/docs`, `/redoc` and `/openapi.json` are served only when `DEBUG=true`.

## Hooks

See [`hooks/README.md`](hooks/README.md).

## Deployment

Merge `infra/docker-compose.workbench.yaml` into the compose file on the host.
Exposure is a tailnet IP binding only — nginx is not involved.
The api runs `git` against the stored repositories, so its image installs git and keeps
`STORAGE_DIR` on a volume of its own; the worker needs neither, since it reads repositories
through `/api/git/query`. `kbstore-sandbox` runs the worker's `run_on_file` commands with no network
and storage mounted read-only; the api reaches it through the `kbstore-sandbox-socket` volume.
After pulling a new image, run `uv run alembic upgrade head` in the api container.

## Rebuilding

Notes are derived data: the transcripts and git repositories under `STORAGE_DIR` are the originals,
so a note can always be written again without asking any device to upload anything.

```sh
python -m backend.cli reset            # delete the notes and jobs, keep everything else
curl -X POST .../api/raw/rebuild       # queue the stored transcripts again
```

`--decisions` also forgets merges, links, project scopes, pins and deletions; `--raw` deletes the
stored originals as well, and only then does every device have to import again. `rebuild_batch_size`
in the runtime settings does the same thing gradually, oldest note first.

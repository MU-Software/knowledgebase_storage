# Hook installation

`kbstore_hook.py` uses only the standard library, so copying the single file is enough.
It reads both Claude Code and Codex transcripts.

## Claude Code

`~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [{ "type": "command", "command": "/path/to/kbstore_hook.py", "timeout": 10 }]
      }
    ],
    "Stop": [
      {
        "hooks": [{ "type": "command", "command": "/path/to/kbstore_hook.py", "timeout": 10 }]
      }
    ]
  }
}
```

`Stop` sends the conversation and uploads changed memory files.
`SessionStart` downloads the project's memory and adds the project's recent kb records to the context.

A machine that cannot reach the API registers only `SessionStart`, with `KBSTORE_PULL_BASE` pointing at an
authenticated reverse proxy for `/api/wiki/memories` and `/api/wiki/context`, and `KBSTORE_PULL_AUTH` set to its
`Authorization` header. The proxy has to pass `X-API-Key` through.

## Codex

`~/.codex/hooks.json` takes the same entries. Trust them with `/hooks` after every change, or Codex skips them.

The server keeps the latest snapshot of a session and summarizes it after `job_idle_seconds` of inactivity.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `KBSTORE_API_BASE` | `http://127.0.0.1:8006` | The kbstore API |
| `KBSTORE_PULL_BASE` | `KBSTORE_API_BASE` | Where `SessionStart` reads memory and context from |
| `KBSTORE_API_KEY` | | API key sent as `X-API-Key` on every request |
| `KBSTORE_PULL_AUTH` | | `Authorization` header for `KBSTORE_PULL_BASE` only |
| `KBSTORE_AGENT` | detected from the transcript | |
| `KBSTORE_DEVICE` | hostname | |
| `KBSTORE_TIMEOUT` | `3` | Seconds per request. Short, so the hook never holds the session |
| `KBSTORE_MAX_MESSAGES` | `2000` | Send only the last N messages |
| `KBSTORE_STATE_DIR` | `~/.cache/kbstore` | Remembers the memory files last synced |

## Rules

- **The hook never summarizes.** It enqueues and returns immediately.
- It always exits 0 — a failed capture must not block your work.

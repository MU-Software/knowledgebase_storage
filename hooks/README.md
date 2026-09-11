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

`Stop` sends the conversation and uploads changed memory files. `SessionStart` downloads the project's memory.

A machine outside the tailnet registers only `SessionStart`, with `KBSTORE_PULL_BASE=https://mcp.mudev.cc/kb`
and `KBSTORE_PULL_AUTH` set to the `Authorization` header of its mudev MCP config.

## Codex

`~/.codex/hooks.json` needs only the `Stop` entry. Trust it with `/hooks` after every change, or Codex skips it.

The server keeps the latest snapshot of a session and summarizes it after `job_idle_seconds` of inactivity.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `KBSTORE_API_BASE` | `http://workbench-nrt:8006` | Tailnet address |
| `KBSTORE_PULL_BASE` | `KBSTORE_API_BASE` | Where `SessionStart` downloads memory from |
| `KBSTORE_PULL_AUTH` | | `Authorization` header for `KBSTORE_PULL_BASE` only |
| `KBSTORE_AGENT` | detected from the transcript | |
| `KBSTORE_DEVICE` | hostname | |
| `KBSTORE_TIMEOUT` | `3` | Seconds per request. Short, so the hook never holds the session |
| `KBSTORE_MAX_MESSAGES` | `2000` | Send only the last N messages |
| `KBSTORE_STATE_DIR` | `~/.cache/kbstore` | Remembers the memory files last synced |

## Rules

- **The hook never summarizes.** It enqueues and returns immediately.
- It always exits 0 — a failed capture must not block your work.

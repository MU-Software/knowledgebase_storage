# Hook installation

`kbstore_hook.py` uses only the standard library, so copying the single file is enough.

## Claude Code

`~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [{ "type": "command", "command": "/path/to/kbstore_hook.py", "timeout": 5 }]
      }
    ]
  }
}
```

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `KBSTORE_API_BASE` | `http://workbench-nrt:8006` | Tailnet address; the hook posts to `{base}/api/jobs` |
| `KBSTORE_AGENT` | `claude-code` | Change it for Codex and others |
| `KBSTORE_DEVICE` | hostname | |
| `KBSTORE_TIMEOUT` | `3` | Seconds. Short, so the hook never holds the session |
| `KBSTORE_MAX_MESSAGES` | `2000` | Send only the last N messages |

## Rules

- **The hook never summarizes.** It enqueues and returns immediately.
- It always exits 0 — a failed capture must not block your work.

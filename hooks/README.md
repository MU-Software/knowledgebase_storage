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

`Stop` appends the transcript's new bytes to the server, sends the conversation, uploads changed memory
files, and — in a git repository — uploads the commits the server does not have yet plus a snapshot of
uncommitted work. A repository the server has never seen is uploaded whole only when it is small; use
`tools/kbstore_import.py --git` for the rest.

`SessionStart` downloads the project's memory and adds the project's recent kb records to the context.

## Codex

`~/.codex/hooks.json` takes the same entries. Trust them with `/hooks` after every change, or Codex skips them.

The server keeps the latest snapshot of a session and summarizes it after `job_idle_seconds` of inactivity.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `KBSTORE_API_BASE` | `http://127.0.0.1:8006` | The kbstore API |
| `KBSTORE_API_KEY` | | API key sent as `X-API-Key` on every request |
| `KBSTORE_AGENT` | detected from the transcript | |
| `KBSTORE_DEVICE` | hostname | |
| `KBSTORE_TIMEOUT` | `3` | Seconds per small request. Short, so the hook never holds the session |
| `KBSTORE_UPLOAD_TIMEOUT` | `60` | Seconds per upload request |
| `KBSTORE_BUNDLE_TIMEOUT` | `30` | Seconds allowed for each git command |
| `KBSTORE_BLOB_LIMIT` | `2m` | Files larger than this are left out of the bundle |
| `KBSTORE_CHUNK_BYTES` | `1000000` | Upload chunk size; keep it under the proxy's request body limit |
| `KBSTORE_FIRST_PUSH_LIMIT_KB` | `204800` | Skip the first git upload above this repository size |
| `KBSTORE_MAX_MESSAGES` | `2000` | Send only the last N messages |
| `KBSTORE_STATE_DIR` | `~/.cache/kbstore` | Remembers synced memory files, bundles and the last snapshot tree |

## Rules

- **The hook never summarizes.** It enqueues and returns immediately.
- It always exits 0 — a failed capture must not block your work.

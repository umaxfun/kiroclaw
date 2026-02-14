# Code Summary — Unit 2: Session Persistence

## Files Created

| File | Description |
|------|-------------|
| `src/tg_acp/session_store.py` | C3 Session Store — SQLite CRUD for (user_id, thread_id) → session mapping |
| `tests/test_session_store.py` | 10 unit tests — schema, upsert, get, model ops, workspace dir |
| `tests/test_session_continuity.py` | 2 integration tests — real kiro-cli session/new + session/load |

## Files Modified

| File | Changes |
|------|---------|
| `main.py` | Rewritten for Unit 2: argparse (--user-id, --thread-id), SessionStore, session/load branching, 2-run demo |
| `src/tg_acp/acp_client.py` | `session_load()` now accepts `cwd` param (required by kiro-cli). `spawn()` uses `start_new_session=True`. `kill()` uses `os.killpg()` to kill entire process tree. |

## Bugs Found and Fixed

1. `session/load` requires `mcpServers` field (same as `session/new`) — kiro-cli rejects without it, no JSON-RPC response sent (causes hang)
2. `session/load` requires `cwd` field — same deserialization requirement
3. `kiro-cli` spawns `kiro-cli-chat` as a child process. `process.terminate()` only kills the parent, leaving the child alive and holding session lock files. Fixed by using `start_new_session=True` + `os.killpg()` to kill the entire process group.

## FINDINGS.md Updates Needed

- `session/load` params: `{ sessionId, cwd, mcpServers }` (not just `{ sessionId }`)
- kiro-cli uses `.lock` files in `~/.kiro/sessions/cli/` — session/load fails if lock held by another PID
- kiro-cli spawns kiro-cli-chat as child — must kill process group, not just parent

## Test Results

- 38/38 tests passing (26 Unit 1 + 12 Unit 2)
- Session continuity verified: agent remembers "1234" across process restarts

## How to Run

```bash
# First run — creates session, agent memorizes 1234
uv run main.py --user-id 42 --thread-id 7

# Second run — loads session, asks what number
uv run main.py --user-id 42 --thread-id 7
```

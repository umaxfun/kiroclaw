# Unit 6: Release Prep — Business Logic Model

## Allowlist Flow

### Handler Entry Point (handle_message — outer wrapper)

```
message received
    |
    v
from_user is None? ──YES──> RETURN
    |
   NO
    |
    v
thread_id is None? ──YES──> RETURN
    |
   NO
    |
    v
extract user_id from message.from_user.id
    |
    v
config.is_user_allowed(user_id)?
    |
   NO ──> send rejection message (BR-02) ──> RETURN
    |
   YES
    |
    v
[has_file / has_text checks, workspace creation, file download, handle_message_internal — all unchanged]
```

### /start Handler (special case)

```
/start received
    |
    v
extract user_id from message.from_user.id
    |
    v
config.is_user_allowed(user_id)?
    |
   NO ──> send restricted /start message (BR-03) ──> STOP
    |
   YES
    |
    v
send normal welcome message ──> STOP
```

## Implementation Approach

### C7 Config Changes
1. Add `allowed_telegram_ids: frozenset[int]` field to `Config` dataclass (compatible with `frozen=True`)
2. Parse `ALLOWED_TELEGRAM_IDS` env var in `Config.load()`: split by comma, strip, convert to int, wrap in `frozenset`; empty/unset → `frozenset()` (no crash)
3. Add `is_user_allowed(user_id: int) -> bool` method — returns `user_id in self.allowed_telegram_ids`
4. Log warning at startup if allowlist is empty

### C6 Bot Handlers Changes
1. `cmd_start`: check allowlist, send restricted variant if denied, normal welcome if allowed
2. `cmd_model`: check allowlist at top, send rejection if denied
3. `handle_message`: check allowlist immediately after extracting `user_id` and `thread_id` — before `has_file`/`has_text` checks, before `create_workspace_dir`, before file download, before calling `handle_message_internal`. This is the very first business logic after the null guards.

### Rejection Helper
A small helper function in bot_handlers to avoid repeating the rejection message:

```python
async def _send_access_denied(message: Message, user_id: int) -> None:
    """Send standardized rejection message with user's Telegram ID."""
    await message.answer(
        f"⛔ Access restricted.\n\n"
        f"Your Telegram ID: {user_id}\n\n"
        f"To get access, ask the administrator to add your ID to the allowed list."
    )
```

## Test Strategy

| Test | What it verifies |
|------|-----------------|
| Allowed user sends text | Normal response (existing behavior unchanged) |
| Denied user sends text | Rejection message with correct user ID, no ACP interaction |
| Denied user sends /start | Restricted welcome with user ID |
| Allowed user sends /start | Normal welcome (no restriction note) |
| Denied user sends /model | Rejection message, no model change |
| Denied user sends file | Rejection message, file not downloaded |
| Empty allowlist | All users denied |
| Config parsing | Comma-separated IDs parsed, whitespace handled, non-integer raises ValueError |


---

## Stale Lock Recovery Flow

### session/load Error Path (handle_message_internal)

```
session/load fails with RuntimeError
    |
    v
extract error message string
    |
    v
regex match "Session is active in another process (PID {pid})"?
    |
   NO ──> existing behavior: log error, send "try again" to user, RETURN
    |
   YES
    |
    v
extract pid (int)
    |
    v
os.kill(pid, 0) — is process alive?
    |
   YES (no exception or PermissionError)
    |   └──> live conflict: log error, send "try again" to user, RETURN
    |
   NO (OSError with ESRCH)
    |
    v
log WARNING: stale lock detected
    |
    v
store.delete_session(user_id, thread_id)
    |
    v
session_id = await slot.client.session_new(cwd=workspace_path)
    |
    v
store.upsert_session(user_id, thread_id, session_id, workspace_path)
    |
    v
[continue normal prompt flow with new session_id]
```

### Implementation Approach

The recovery logic lives entirely in `handle_message_internal`, inside the existing `except RuntimeError` block for `session/load`. No new components or methods needed — just a conditional branch in the error handler.

A helper function `_try_recover_stale_session(error_msg: str) -> int | None` extracts the PID from the error message and checks liveness. Returns the stale PID if recovery is possible, `None` if not.

### SessionStore.delete_session

New method on C3 SessionStore:
```python
def delete_session(self, user_id: int, thread_id: int) -> None:
    """Delete a session record. Used for stale lock recovery."""
```

### Test Strategy (FR-16)

| Test | What it verifies |
|------|-----------------|
| Stale PID recovery | session/load fails with dead PID → session cleared, new session created, prompt succeeds |
| Live PID conflict | session/load fails with live PID → error reported to user, no recovery |
| Non-matching error | session/load fails with different error → existing behavior unchanged |
| delete_session | SQLite record removed, subsequent get_session returns None |

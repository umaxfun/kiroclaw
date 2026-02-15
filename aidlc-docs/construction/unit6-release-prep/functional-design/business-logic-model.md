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

# Unit 6: Release Prep — Business Rules

## BR-01: Allowlist Gate (First Check)

The allowlist check is the **first gate** in every handler — before session lookup, ACP interaction, file download, or any processing.

### Rule
```
IF user_id NOT IN allowed_telegram_ids:
    send rejection message
    RETURN (stop processing)
```

### Applies To
- `handle_message` (text, files, audio)
- `cmd_model`
- `cmd_start` (partial — see BR-03)

## BR-02: Rejection Message

When an unauthorized user sends any message (except /start):

```
⛔ Access restricted.

Your Telegram ID: {user_id}

To get access, ask the administrator to add your ID to the allowed list.
```

- `{user_id}` is the sender's numeric Telegram user ID
- Message is plain text (no HTML/Markdown formatting needed)
- No further processing occurs after sending this message

## BR-03: /start for Unauthorized Users

`/start` is special — it still responds, but with a restricted variant:

```
I'm a Kiro-powered assistant. Send me a message in any forum topic and I'll respond.

⛔ Your access is currently restricted.
Your Telegram ID: {user_id}
To get access, ask the administrator to add your ID to the allowed list.
```

Rationale: `/start` is the first thing a user sees. Showing the rejection with their ID here helps them self-serve the access request without needing to send a separate message.

## BR-04: Fail-Closed Semantics

- `ALLOWED_TELEGRAM_IDS` is optional — empty or unset → `frozenset()` → **nobody is allowed**
- This is intentional: the bot should not be open by default
- This is also the self-service flow: deploy with empty list, message the bot, get your Telegram ID from the rejection message (BR-02) or `/start` (BR-03), add it to `.env`, restart
- Startup logs a warning if the allowlist is empty: `"ALLOWED_TELEGRAM_IDS is empty — all users will be denied"`
- The bot does NOT crash on missing `ALLOWED_TELEGRAM_IDS` — it starts normally and rejects everyone until IDs are configured

## BR-05: README Content

README.md is a release artifact. It must cover:

1. Project description (what the bot does)
2. Prerequisites (Python 3.12, uv, kiro-cli, Telegram bot token, forum-enabled group)
3. Installation (clone, uv sync, configure .env)
4. Configuration (all .env variables with descriptions and defaults)
5. Kiro agent setup (kiro-config/ directory, what it contains, how provisioning works)
6. Running the bot (uv run main.py)
7. Bot commands (/start, /model)
8. Architecture overview (brief — components, how they interact)


---

## BR-06: Stale Session Lock Detection

When `session/load` fails with error data matching `Session is active in another process (PID {pid})`:

### Rule
```
IF session/load error contains "Session is active in another process (PID {pid})":
    extract pid from error message
    IF pid is NOT alive (os.kill(pid, 0) raises OSError):
        → stale lock — execute recovery (BR-07)
    ELSE:
        → live conflict — log error, report failure to user (existing behavior)
```

### PID Liveness Check
- Use `os.kill(pid, 0)` — sends no signal, just checks if process exists
- `OSError` (errno ESRCH) → process is dead → stale lock
- No exception → process is alive → genuine conflict
- `PermissionError` → process exists but owned by another user → treat as alive (genuine conflict)

## BR-07: Stale Lock Recovery

When a stale lock is detected (BR-06):

### Rule
```
1. Log WARNING: "Stale session lock detected for {session_id} (dead PID {pid}) — clearing and creating new session"
2. Delete the session record from SQLite: store.delete_session(user_id, thread_id)
3. Create a new session on the same slot: session_new(cwd=workspace_path)
4. Upsert the new session into SQLite
5. Continue normal message processing with the new session
```

### Consequences
- Conversation history is lost (new session replaces the locked one)
- This is acceptable: the alternative is a permanently stuck thread that never recovers
- The warning log ensures the operator knows recovery happened

## BR-08: Error Message Parsing

The kiro-cli error data format is:
```
Failed to start session: Session is active in another process (PID {pid})
```

- Parse with regex: `r"Session is active in another process \(PID (\d+)\)"`
- If the error doesn't match this pattern, fall through to existing error handling (no recovery attempt)

# Business Rules — Unit 4: File Handling + Commands

## BR-14: Inbound File Rules

| # | Rule | Rationale |
|---|------|-----------|
| 1 | Download file to workspace dir: `{workspace_path}/{original_filename}` | File must be in the thread's workspace for the agent to access via tools |
| 2 | Support all Telegram attachment types: document, photo, audio, voice, video, video_note, sticker | Generic file handling — agent decides what to do with each type |
| 3 | Use original filename when available, generate name from file_unique_id otherwise | Preserves user intent; generated names prevent collisions |
| 4 | Overwrite if file with same name exists | Simplest approach; per-thread workspace makes collisions rare |
| 5 | No file size limits — Telegram's own limits apply | User's answer: "Everything that fits into Telegram works for us" |
| 6 | Reference file path in prompt: `"User sent a file: {absolute_path}"` | Agent uses its own tools (readFile, etc.) to access the file content |
| 7 | If message has both file and text/caption, include both in prompt | Text may describe what to do with the file |
| 8 | If message has file but no text/caption, send only the file reference | No empty text content in prompt |
| 9 | If download fails, log error and send error message to user | User should know the file wasn't processed |

## BR-15: Outbound File Rules

| # | Rule | Rationale |
|---|------|-----------|
| 1 | Parse `<send_file>` tags BEFORE Markdown→HTML conversion | Prevents chatgpt-md-converter from mangling XML tags |
| 2 | Regex: `<send_file\s+path="([^"]+)">(.*?)</send_file>` with DOTALL | Handles multiline descriptions; non-greedy to match each tag individually |
| 3 | Strip all `<send_file>` tags from buffer before sending text to user | Tags are instructions to the bot, not user-visible content |
| 4 | If stripped buffer is empty, skip sendMessage | Agent response may consist entirely of file deliveries |
| 5 | Validate file path is within workspace boundary before sending | Prevents path traversal — agent must not send arbitrary system files |
| 6 | If file exists and path is valid: send via sendDocument with description as caption | Standard Telegram file delivery |
| 7 | If file does NOT exist: send internal retry prompt to agent | User's answer: "internally prompt the agent, let the agent fix something" |
| 8 | Retry at most once per turn | Prevents infinite retry loops if agent keeps producing bad paths |
| 9 | If path validation fails (traversal attempt): log warning, skip file, no retry | Security violation — don't ask agent to "fix" a traversal attempt |
| 10 | If sendDocument fails: log error, continue with remaining files | One file failure shouldn't block others |
| 11 | finalize() return type changes to `list[tuple[str, str]]` (path, description) | Caller needs both path and caption for sendDocument |

## BR-16: /model Command Rules

| # | Rule | Rationale |
|---|------|-----------|
| 1 | `/model` (no args): display model list with current selection marked (✓) | User needs to see available options and current state |
| 2 | `/model <name>`: validate against AVAILABLE_MODELS (case-insensitive) | Prevent typos from silently failing |
| 3 | Unknown model name: send error with list of valid models | Helpful error message |
| 4 | Store selected model in SQLite via store.set_model() | Persists across bot restarts |
| 5 | Call session/set_model immediately if session exists | User's answer: "Store in SQLite AND call setModel immediately" |
| 6 | If session doesn't exist yet: model stored in SQLite, applied when session is created | Thread may not have had any messages yet |
| 7 | If session/set_model fails: log warning, model still stored in SQLite | Model will apply on next session/load; don't fail the command |
| 8 | Default model is "auto" | FR-09 default |
| 9 | /model command requires thread_id (forum topic) | Consistent with all other bot interactions |

## BR-17: Extended Handler Rules

| # | Rule | Rationale |
|---|------|-----------|
| 1 | handle_message now accepts messages with file attachments (not just text) | Unit 4 extends from text-only to text + files |
| 2 | Guard: return if message has neither text/caption nor file attachment | Nothing to process |
| 3 | File download happens BEFORE session/prompt | File must be in workspace before agent can access it |
| 4 | Outbound file processing happens AFTER finalize() | Tags are in the agent's response text |
| 5 | Missing file retry is a full prompt→stream→finalize cycle | Agent may need to create/move the file, producing a new response |
| 6 | Retry response is displayed to the user (via StreamWriter) | Agent's correction message is user-visible |
| 7 | /model handler registered BEFORE catch-all message handler | aiogram processes handlers in registration order; commands must match first |

## Test Strategy

### Unit Tests (no network, no kiro-cli)

| # | Test | What it verifies |
|---|------|-----------------|
| 1 | validate_path — file inside workspace | Returns True |
| 2 | validate_path — path traversal attempt (../../etc/passwd) | Returns False |
| 3 | validate_path — symlink escape | Returns False (resolve follows symlinks) |
| 4 | `<send_file>` tag parsing — single tag | Extracts path and description |
| 5 | `<send_file>` tag parsing — multiple tags | Extracts all (path, description) tuples |
| 6 | `<send_file>` tag parsing — no tags | Returns empty list, buffer unchanged |
| 7 | `<send_file>` tag stripping — tags removed from buffer | Clean text remains |
| 8 | `<send_file>` tag stripping — buffer is only tags | Empty buffer after stripping |
| 9 | /model list formatting — current model marked | ✓ appears next to current model |
| 10 | /model validation — unknown model rejected | Error message with valid list |

### Integration Tests (real kiro-cli + real Telegram test bot)

| # | Test | What it verifies |
|---|------|-----------------|
| 1 | Send a .txt file in forum topic, verify file appears in workspace | Inbound file download works |
| 2 | Send file + caption, verify both appear in prompt | Mixed content handling |
| 3 | Prompt agent to create a file, verify `<send_file>` parsed and file sent back | Outbound file flow end-to-end |
| 4 | `/model` — verify model list displayed | Command handler works |
| 5 | `/model claude-sonnet-4` — verify model persisted and session/set_model called | Model change flow |
| 6 | Path traversal in `<send_file>` — verify file not sent | Security validation |

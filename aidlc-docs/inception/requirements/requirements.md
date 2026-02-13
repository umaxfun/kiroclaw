# Requirements: tg-acp — Telegram Bot + Kiro CLI ACP Streaming

## Intent Analysis

- **User Request**: Build a Telegram bot (Python) that uses Kiro CLI in ACP mode as its backend, with real-time streaming responses via sendMessageDraft
- **Request Type**: New Project
- **Scope**: System-wide — multiple components (Telegram bot, ACP client, process pool, session management, file handling)
- **Complexity**: Moderate-to-Complex — async subprocess management, streaming protocol, process pooling, bidirectional file transfer
- **Build Approach**: Full architecture from FINDINGS.md, built incrementally and testable at each step

## Source Material

All architectural decisions, protocol details, and tech stack choices are documented in `FINDINGS.md` at workspace root. This requirements document formalizes those decisions and fills in the gaps resolved through Q&A.

---

## Functional Requirements

### FR-01: Telegram Bot Core
- Bot runs as a long-polling or webhook-based aiogram application
- Bot operates exclusively in forum topic mode (threads enabled)
- Each thread maps to a separate Kiro CLI session
- Session mapping: `(user_id, thread_id) → kiro_session_id`

### FR-02: ACP Client (kiro-cli Integration)
- Communicate with `kiro-cli acp` via stdin/stdout using JSON-RPC 2.0
- Protocol flow: `initialize` → `session/new` or `session/load` → `session/prompt`
- Read `session/update` notifications for streaming chunks
- Detect `TurnEnd` / `stopReason: "end_turn"` to finalize responses
- Support `session/cancel` to abort in-flight prompts
- Use `content` field (not `prompt`) in `session/prompt` params

### FR-03: Streaming Responses
- Stream agent responses to Telegram using `sendMessageDraft` API (Bot API 9.3)
- Each streaming response uses a consistent `draft_id` for animated updates
- Sliding window for long responses: if accumulated text exceeds Telegram's 4096 char limit, the draft shows the latest ~4000 characters (sliding window)
- On turn completion, finalize with `sendMessage` — if the full response exceeds 4096 chars, split into multiple sequential messages
- Handle `message_thread_id` for forum topic targeting

### FR-04: Process Pool (Scale-to-One)
- Maintain at least 1 warm `kiro-cli acp` process at all times (scale-to-one)
- Warm process is pre-initialized and ready for `session/load` or `session/new`
- Spawn additional processes when all existing ones are busy, up to a configurable maximum (`MAX_PROCESSES` in .env, default e.g. 5)
- When all processes are busy and pool is at max capacity, queue incoming requests and process them when a slot frees up
- Queue holds at most one message per thread ID — if a new message arrives for a thread that already has a queued message, the old one is replaced
- Extra processes (beyond minimum 1) killed after configurable idle timeout (`IDLE_TIMEOUT_SECONDS` in .env, default 30)
- Last remaining process never killed — stays warm
- Process pool tracks: `{ process, current_session_id, idle_timer, busy }`

### FR-05: Session Management
- Store mapping of `(telegram_user_id, thread_id) → kiro_session_id` in a SQLite database file in the bot's working directory (e.g., `./tg-acp.db`)
- Kiro persists sessions to `~/.kiro/sessions/cli/` automatically
- Any process can load any session — processes not tied to users/threads
- First message in a thread: `session/new` with thread-specific `cwd`
- Subsequent messages: `session/load` with existing session ID

### FR-06: Working Directory Management
- Base directory: `./workspaces/` (relative to bot working directory)
- Structure: `./workspaces/{telegram_user_id}/{thread_id}/`
- Each thread gets its own folder as `cwd` in `session/new`
- Directories created on-demand when first message arrives in a thread

### FR-07: Concurrency Handling
- When a user sends a new message while a previous prompt is in-flight: cancel the previous prompt
- Send `session/cancel` to kiro-cli for the in-flight session
- Discard any remaining streaming chunks from the cancelled prompt
- Begin processing the new message immediately

### FR-08: File Handling (Bidirectional)
- **Inbound**: Users can send files/documents/audio via Telegram; bot downloads and places them in the thread's workspace directory, then references them in the ACP prompt
- **Outbound via steering**: Each workspace includes a steering file that instructs the Kiro agent to emit an XML tag (e.g., `<send_file path="..."/>`) when it wants to send a file to the user. The bot parses these tags from the agent's response, strips them from the displayed text, and sends the referenced files via Telegram's `sendDocument`
- Support common file types: text, code, documents, audio, images

### FR-09: Bot Commands
- `/start` — Welcome message explaining the bot
- `/model` — Without arguments: display hardcoded list of available models (auto, claude-opus-4.6, claude-opus-4.5, claude-sonnet-4.5, claude-sonnet-4, claude-haiku-4.5). With argument: set the model for this thread's session via `session/set_model`. Selected model stored in SQLite alongside the session mapping. Default model: `auto`

### FR-10: Error Recovery
- If kiro-cli process crashes mid-stream: spawn a new process, `session/load` with same session ID to resume
- Send error message to user in Telegram, then retry
- At most the in-progress turn is lost (Kiro persists state on every turn)

---

## Non-Functional Requirements

### NFR-01: Configuration
- Bot token and settings loaded from `.env` file during development
- Environment variables for deployment override
- Key config values:
  - `BOT_TOKEN` — Telegram bot token
  - `WORKSPACE_BASE_PATH` — default: `./workspaces/`
  - `MAX_PROCESSES` — upper limit on kiro-cli process pool size (default: 5)
  - `IDLE_TIMEOUT_SECONDS` — idle timeout before killing extra processes (default: 30)

### NFR-02: Startup Validation
- Fail fast on startup if `kiro-cli` is not found on PATH
- Validate bot token is set
- Validate workspace base directory is writable

### NFR-03: Testing Strategy
- No mocks — all tests hit real kiro-cli and real Telegram API (test bot + test forum)
- Configurable timeouts and pool limits enable fast test execution (e.g., 2-5s idle timeout in tests)
- Testing layers:
  1. ACP protocol tests — real kiro-cli, verify JSON-RPC initialize/session/prompt/streaming flow
  2. Bot handler tests — real kiro-cli + real Telegram test bot/forum, verify sendMessageDraft streaming and sendMessage finalization
  3. Process pool tests — real kiro-cli processes, shortened timeouts, verify spawn/reuse/idle-kill/warm-keep
  4. End-to-end smoke — real everything, send message in test forum, verify streaming response and final message
- Minimal unit tests only for pure logic (config validation, path construction, SQLite lookups)

### NFR-04: Tech Stack
- Python 3.12 (pinned in .python-version)
- Package manager: uv
- Bot framework: aiogram (Bot API 9.3+ support)
- Async subprocess: `asyncio.create_subprocess_exec`
- Config: python-dotenv for .env loading

### NFR-05: Logging
- Structured logging for debugging ACP protocol messages
- Log kiro-cli stderr for diagnostics
- Log bot events (message received, session created, process spawned, etc.)

---

## Constraints

- Kiro CLI must be installed and available on PATH
- Bot API 9.3+ required for `sendMessageDraft`
- Bot must have forum topic mode enabled in Telegram
- Single-instance deployment (no horizontal scaling for PoC)
- Rate limiting not a concern for PoC

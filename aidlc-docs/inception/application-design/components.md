# Application Components

## Component Overview

```
+------------------+     +------------------+     +------------------+
|   Bot Handlers   |---->|  Session Store   |     |    ACP Client    |
|   (aiogram)      |     |  (SQLite)        |     |  (JSON-RPC 2.0)  |
+--------+---------+     +------------------+     +--------+---------+
         |                                                 ^
         |              +------------------+               |
         +------------->|  Process Pool    |---------------+
         |              +------------------+
         |
         |              +------------------+
         +------------->|  File Handler    |
         |              +------------------+
         |
         |              +------------------+
         +------------->|  Stream Writer   |
                        |  (Draft/Final)   |
                        +------------------+

[First run / installation]
+---------------------+
| Workspace           |---> provisions ~/.kiro/agents/ (REQUIRED custom agent),
| Provisioner         |     steering/, skills/ from kiro-config/ template
+---------------------+
```

---

## C1: ACP Client

**Purpose**: Low-level communication with a single `kiro-cli acp` process over stdin/stdout using JSON-RPC 2.0.

**Responsibilities**:
- Spawn `kiro-cli acp` subprocess via `asyncio.create_subprocess_exec` with `--agent <name>` flag
- Send JSON-RPC requests (`initialize`, `session/new`, `session/load`, `session/prompt`, `session/cancel`, `session/set_model`)
- Read and parse JSON-RPC responses and notifications from stdout
- Expose an async iterator/callback interface for streaming `session/update` notifications
- Detect process death and signal it to the caller
- Capture stderr for logging/diagnostics
- Handle subagent-related notifications (`_session/terminate`) by logging them (no action needed — kiro-cli manages subagents internally)

**Owns**: One subprocess lifecycle. Stateless beyond the subprocess handle.

---

## C2: Process Pool

**Purpose**: Manage a pool of ACP Client instances with scale-to-one semantics.

**Responsibilities**:
- Maintain at least 1 warm, pre-initialized ACP Client at all times
- Allocate a free process to incoming requests; spawn new ones if all busy (up to `MAX_PROCESSES`)
- Queue requests when pool is at capacity; dedup queue by thread_id (latest message wins)
- Track per-process state: `{ acp_client, current_session_id, idle_timer, busy }`
- Kill extra idle processes after `IDLE_TIMEOUT_SECONDS`; never kill the last one
- Replace crashed processes transparently

**Owns**: The list of ACP Client instances and the request queue.

---

## C3: Session Store

**Purpose**: Persist the mapping between Telegram threads and Kiro sessions in SQLite.

**Responsibilities**:
- Store and retrieve `(user_id, thread_id) -> kiro_session_id` mappings
- Store per-thread model selection (default: `auto`)
- Store workspace directory path per session
- CRUD operations on session records
- Database file: `./tg-acp.db`

**Owns**: SQLite database connection and schema.

---

## C4: Stream Writer

**Purpose**: Accumulate streaming chunks and deliver them to Telegram via `sendMessageDraft` / `sendMessage`.

**Responsibilities**:
- Accumulate `agent_message_chunk` text into a buffer
- Call `sendMessageDraft` with sliding window (last ~4000 chars) as chunks arrive
- On `turn_end`, finalize: split full response into <=4096 char segments, send each via `sendMessage`
- Parse and strip `<send_file>` XML tags from the response before sending
- Manage `draft_id` lifecycle per streaming response
- Handle `message_thread_id` for forum topic targeting

**Owns**: The text accumulation buffer and draft state for one streaming response.

---

## C5: File Handler

**Purpose**: Handle bidirectional file transfer between Telegram and the workspace.

**Responsibilities**:
- **Inbound**: Download files from Telegram messages, save to the thread's workspace directory, return file path for inclusion in ACP prompt
- **Outbound**: Given a file path (from parsed `<send_file>` tags), send the file to the Telegram thread via `sendDocument`
- Validate file paths are within the workspace boundary (security)

**Owns**: File I/O operations within workspace directories.

---

## C6: Bot Handlers

**Purpose**: aiogram message and command handlers — the entry point for all Telegram interactions.

**Responsibilities**:
- `/start` command handler: send welcome message
- `/model` command handler: list models or set model via Session Store + ACP Client
- Text message handler: orchestrate the full prompt flow (session lookup → process allocation → prompt → streaming → finalization)
- File/document/audio message handler: download via File Handler, then treat as text message with file reference
- Cancel in-flight prompts when new message arrives for same thread (FR-07)
- Coordinate with Process Pool for process allocation and release

**Owns**: aiogram router/dispatcher registration. Orchestration logic.

---

## C7: Config

**Purpose**: Load and validate application configuration.

**Responsibilities**:
- Load `.env` file via python-dotenv
- Expose typed config values: `BOT_TOKEN`, `WORKSPACE_BASE_PATH`, `MAX_PROCESSES`, `IDLE_TIMEOUT_SECONDS`, `KIRO_AGENT_NAME` (all required except pool tuning defaults)
- Validate required values on load (fail fast on missing)
- Validate startup prerequisites: kiro-cli on PATH, `KIRO_AGENT_NAME` set, `kiro-config/` template exists, workspace base directory writable

**Owns**: Configuration state. Immutable after startup.

---

## C8: Workspace Provisioner

**Purpose**: Sync the bot's global `~/.kiro/` config (agent, steering, skills) from the `kiro-config/` template on every startup.

**Responsibilities**:
- On bot startup, sync all files matching the `{KIRO_AGENT_NAME}*` prefix in `~/.kiro/agents/`, `~/.kiro/steering/`, and `~/.kiro/skills/` — delete existing matches, copy fresh from `kiro-config/` template
- The agent config `~/.kiro/agents/{KIRO_AGENT_NAME}.json` is REQUIRED for the system to function (defines `<send_file>` steering, subagent config, allowed tools)
- Copy from a `kiro-config/` template directory in the bot's source tree
- The template directory is a project artifact, version-controlled alongside the bot code — it IS the installation payload for the agent config
- Prefix-based sync — safe to call on every startup (always brings `~/.kiro/` in sync with template). Files outside the bot's prefix are never touched.
- Does NOT provision per-thread — global agent is found from any `cwd` automatically
- For thread-specific overrides (rare): bot can create `.kiro/agents/` inside a thread's workspace directory — local takes precedence over global

**Owns**: Files matching `{KIRO_AGENT_NAME}*` in `~/.kiro/agents/`, `~/.kiro/steering/`, `~/.kiro/skills/`. Template files in `kiro-config/`.

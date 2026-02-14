# PoC Discovery Findings: Telegram Bot + Kiro CLI ACP Streaming

## Concept

A Telegram bot (Python) that uses Kiro CLI in ACP mode as its backend.
User messages go to Kiro CLI via ACP protocol, and responses stream back
to Telegram in real-time using the native `sendMessageDraft` API.

---

## Kiro CLI — ACP Mode

Kiro CLI can run as an ACP-compliant agent communicating over **stdin/stdout using JSON-RPC 2.0**.

```bash
kiro-cli acp
```

### Protocol Flow

1. **`initialize`** — negotiate protocol version and capabilities
2. **`session/new`** — create a session (pass `cwd`, optional MCP servers)
3. **`session/prompt`** — send user message, get back a response
4. **`session/update`** (notification) — streaming chunks come back here

### Key Message Formats

#### Initialize (Client → Agent)
```json
{
  "jsonrpc": "2.0",
  "id": 0,
  "method": "initialize",
  "params": {
    "protocolVersion": 1,
    "clientCapabilities": {
      "fs": { "readTextFile": true, "writeTextFile": true },
      "terminal": true
    },
    "clientInfo": { "name": "tg-acp-bot", "title": "Telegram ACP Bot", "version": "0.1.0" }
  }
}
```

#### Create Session (Client → Agent)
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "session/new",
  "params": {
    "cwd": "/absolute/path/to/workspace",
    "mcpServers": []
  }
}
```

#### Send Prompt (Client → Agent)
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "session/prompt",
  "params": {
    "sessionId": "sess_abc123",
    "prompt": [{ "type": "text", "text": "User message here" }]
  }
}
```
Note: Kiro CLI uses `prompt` (not `content`) in `session/prompt` params.

#### Streaming Update (Agent → Client, notification)
```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "sess_abc123",
    "update": {
      "sessionUpdate": "agent_message_chunk",
      "content": { "type": "text", "text": "Partial response text..." }
    }
  }
}
```

#### Prompt Complete (Agent → Client, response)
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": { "stopReason": "end_turn" }
}
```

#### Cancel (Client → Agent, notification)
```json
{
  "jsonrpc": "2.0",
  "method": "session/cancel",
  "params": { "sessionId": "sess_abc123" }
}
```

### Other Update Types
- `sessionUpdate: "plan"` — agent's plan with entries
- `sessionUpdate: "tool_call"` — tool invocation started
- `sessionUpdate: "tool_call_update"` — tool progress/completion

### Session Storage (Kiro-specific)

Sessions are persisted to disk automatically at:
```
~/.kiro/sessions/cli/
```

Each session creates two files:
- `<session-id>.json` — session metadata and state
- `<session-id>.jsonl` — event log (conversation history)

This means we do NOT need to keep kiro-cli processes alive forever.
We can spawn a process, do work, let it die, and later spawn a new process
and call `session/load` with the same session ID to resume.

**IMPORTANT (discovered during Unit 2 implementation)**:
- `session/load` requires the same params as `session/new`: `{ sessionId, cwd, mcpServers }` — not just `{ sessionId }`. Missing fields cause a silent parse error (no JSON-RPC response, just stderr).
- kiro-cli uses `.lock` files in `~/.kiro/sessions/cli/` — `session/load` fails with "Session is active in another process (PID ...)" if the lock is held.
- kiro-cli spawns `kiro-cli-chat` as a child process. `process.terminate()` only kills the parent, leaving the child alive and holding the session lock. Must kill the entire process group (`os.killpg` with `start_new_session=True`).

### Kiro-specific ACP Extensions

Kiro extends ACP with custom methods prefixed `_kiro.dev/`:

| Method | Type | Description |
|--------|------|-------------|
| `_kiro.dev/commands/execute` | Request | Execute slash command (e.g., `/agent swap`) |
| `_kiro.dev/commands/options` | Request | Autocomplete for partial commands |
| `_kiro.dev/commands/available` | Notification | Available commands after session creation |
| `_kiro.dev/mcp/oauth_request` | Notification | OAuth URL for MCP server auth |
| `_kiro.dev/mcp/server_initialized` | Notification | MCP server ready |
| `_kiro.dev/compaction/status` | Notification | Context compaction progress |
| `_kiro.dev/clear/status` | Notification | Session history clear status |
| `_session/terminate` | Notification | Terminate subagent session |

### Additional Methods (Kiro-specific)

| Method | Description |
|--------|-------------|
| `session/set_mode` | Switch agent mode (e.g., different agent configs) |
| `session/set_model` | Change the model for the session |

### Session Update Types (Kiro-specific names)

| Update Type | Description |
|-------------|-------------|
| `agent_message_chunk` | Streaming text/content from the agent |
| `tool_call` | Tool invocation with name, parameters, status |
| `tool_call_update` | Progress updates for running tools |
| `turn_end` | Signals the agent turn has completed |

Note: These are snake_case, not PascalCase as shown in some documentation.

### Logging

| Platform | Location |
|----------|----------|
| macOS | `$TMPDIR/kiro-log/kiro-chat.log` |
| Linux | `$XDG_RUNTIME_DIR/kiro-log/kiro-chat.log` |

```bash
KIRO_LOG_LEVEL=debug kiro-cli acp
KIRO_CHAT_LOG_FILE=/path/to/custom.log kiro-cli acp
```

### Links
- ACP Protocol Spec: https://agentclientprotocol.com/protocol/overview
- Initialization: https://agentclientprotocol.com/protocol/initialization
- Session Setup: https://agentclientprotocol.com/protocol/session-setup
- Prompt Turn Lifecycle: https://agentclientprotocol.com/protocol/prompt-turn
- Kiro CLI ACP Docs: https://kiro.dev/docs/cli/acp/
- Blog Post: https://kiro.dev/blog/kiro-adopts-acp/
- Kiro CLI Chat Docs: https://kiro.dev/docs/cli/chat/
- Kiro CLI Commands: https://kiro.dev/docs/cli/reference/cli-commands/

---

## Telegram Bot API — Streaming via `sendMessageDraft`

Introduced in **Bot API 9.3** (December 31, 2025).

### `sendMessageDraft` Method

Purpose-built for streaming partial messages while being generated.

| Parameter          | Type    | Required | Notes                                              |
|--------------------|---------|----------|----------------------------------------------------|
| chat_id            | Integer | Yes      | Target chat                                        |
| message_thread_id  | Integer | No       | Forum topic ID                                     |
| draft_id           | Integer | Yes      | Must be non-zero; same ID = animated updates       |
| text               | String  | Yes      | 1–4096 chars                                       |
| parse_mode         | String  | No       | Markdown, HTML, etc.                               |
| entities           | Array   | No       | Message entities                                   |

Returns `True` on success.

### Requirements
- Bot must have **forum topic mode enabled** (`has_topics_enabled`)
- Works in private chats (Bot API 9.3+)
- `message_thread_id` supported across all send methods in private chats with topics

### Streaming Flow
1. Call `sendMessageDraft` with a `draft_id` as chunks arrive — Telegram animates updates
2. When generation is complete, call `sendMessage` to finalize the message
3. The draft disappears and the final message takes its place

### Links
- Bot API 9.3 Changelog: https://core.telegram.org/bots/api#december-31-2025
- sendMessageDraft Docs: https://core.telegram.org/bots/api#sendmessagedraft

---

## Python Libraries

### aiogram (Recommended)
- Async-first, modern Python Telegram bot framework
- **Already supports Bot API 9.3** (and 9.4) — `sendMessageDraft` available
- PyPI: https://pypi.org/project/aiogram/
- GitHub: https://github.com/aiogram/aiogram
- Docs: https://docs.aiogram.dev/

### python-telegram-bot (Alternative)
- Bot API 9.3 support is **in progress** (tracked in issue #5077)
- GitHub: https://github.com/python-telegram-bot/python-telegram-bot
- Tracking Issue: https://github.com/python-telegram-bot/python-telegram-bot/issues/5077

### Recommendation
Use **aiogram** — it already has full Bot API 9.3 support and is async-native,
which fits well with managing ACP subprocesses via `asyncio`.

---

## Architecture

```
+----------+         +------------------+         +--------------+
| Telegram |  HTTP   |  Python Bot      |  stdin  |  kiro-cli    |
| User Chat|<------->|  (aiogram)       |<------->|  acp         |
|          |         |                  |  stdout |              |
+----------+         +------------------+         +--------------+
                     |                  |
                     | sendMessageDraft |
                     | (streaming)      |
                     |                  |
                     | sendMessage      |
                     | (final)          |
                     +------------------+
```

### Flow
1. User sends message in Telegram
2. Bot receives update via aiogram
3. Bot looks up `kiro_session_id` for this `(user_id, thread_id)`
4. Bot requests a process from the pool
   - If a free process exists → claim it, reset idle timer
   - If all busy → spawn a new process, `initialize` it
5. Load session: `session/load` (existing session) or `session/new` (first message)
6. Bot writes `session/prompt` JSON-RPC to kiro-cli stdin
7. Bot reads `session/update` notifications from kiro-cli stdout
8. For each `agent_message_chunk`, bot calls `sendMessageDraft` (same `draft_id`)
9. On `turn_end` / `stopReason: "end_turn"`, bot calls `sendMessage` with final text
10. Release process back to pool, start 30s idle timer
11. On idle timeout: if pool size > 1, kill the process; if pool size == 1, keep it warm

### Design Decisions (Resolved)

**Session management**: Scale-to-one process pool with idle timeout.
- Always keep **at least 1** warm `kiro-cli acp` process alive (scale-to-one, not scale-to-zero)
- The warm process is pre-initialized and ready to accept `session/load` or `session/new` instantly
- When parallel threads are active, spawn additional processes as needed
- Extra processes (beyond the minimum 1) are killed after **30 seconds of idle**
- The last remaining process is never killed — it stays warm for the next request
- If a message arrives and the warm process is free → assign it immediately (no init overhead)
- If a message arrives and all processes are busy → spawn a new one
- Kiro persists sessions to `~/.kiro/sessions/cli/` automatically, so any process can
  `session/load` any session — processes are not tied to specific users/threads
- Store a mapping of `(telegram_user_id, thread_id) → kiro_session_id`
- Process pool tracks: `{ process, current_session_id, idle_timer, busy: bool }`

**Working directory**: Folder-per-user, subfolder-per-thread.
```
./workspaces/
  ├── {telegram_user_id}/
  │   ├── {thread_id_1}/    ← cwd for session/new
  │   ├── {thread_id_2}/
  │   └── ...
```
- Each thread gets its own folder as `cwd` in `session/new`
- Kiro CLI operates within that folder boundary
- User and thread IDs come directly from Telegram

**Error handling**: Spawn a new process and reload session.
- If kiro-cli crashes mid-stream, spawn a new process
- Call `session/load` with the same session ID to resume
- Kiro persists session state on every turn, so we lose at most the in-progress turn
- Send an error message to the user in Telegram, then retry

**Rate limiting**: Not a concern for PoC.

---

## Tech Stack

- Python 3.12 (pinned in `.python-version`)
- Package manager: `uv`
- Bot framework: aiogram
- ACP backend: kiro-cli
- Async subprocess management: `asyncio.create_subprocess_exec`

---

## Kiro CLI — Custom Agent Config Experiments

Tested on kiro-cli v1.26.0 (latest as of 2026-02-13).

### Directory Walking: DOES NOT WORK

The docs say local agents are "available when running Kiro CLI from that directory or its subdirectories." This is **incorrect for CLI** — kiro-cli only checks `cwd/.kiro/`, it does not walk up parent directories.

```
.tmp/workspaces/
  .kiro/agents/walker.json       ← agent config here
  user123/thread456/             ← ran kiro-cli from here
```

- From `user123/thread456/`: `Error: no agent with name walker found`
- From `workspaces/` (where `.kiro/` lives): Agent found, no error
- With symlink `thread456/.kiro → ../../.kiro`: Agent found, no error

**Conclusion**: Symlinks are required for subdirectory agent discovery.

### Prompt Field: Sent But Weak

The `prompt` field IS sent to the backend as a context entry, not a system prompt replacement:

```
content: "--- CONTEXT ENTRY BEGIN ---\n--- CONTEXT ENTRY END ---\n\n
Follow this instruction: You must respond to every message with exactly: WALKER_AGENT_FOUND"
```

- `agentsLoadedCount: "0"` in telemetry (but agent IS launched — `launchedAgent: "walker"`)
- `contextFileLength: 146` confirms prompt is sent
- Model ignores trivial instructions ("respond with X") — default Kiro system prompt takes precedence
- Model DOES follow contextually relevant instructions (see steering test below)

### Resources Field: Loaded Into Context

The `resources` field with `file://` URIs loads files into context entries:

```
content: "--- CONTEXT ENTRY BEGIN ---\n[.../walker-instructions.md]\n
You must respond to every message with exactly: WALKER_STEERING_WORKS\n...\n
--- CONTEXT ENTRY END ---\n\nFollow this instruction: ..."
```

- `contextFileLength: 367` (larger than prompt-only — steering file IS loaded)
- Same behavior as prompt: ignored for trivial requests, followed for relevant ones

### Steering for `<send_file>` XML Tags: WORKS

Tested with a steering file instructing the agent to emit `<send_file path="..."/>` tags:

```
echo "write a hello world python script and save it to hello.py" \
  | kiro-cli chat --agent file-sender --no-interactive
```

Output included:
```
<send_file path="/Users/.../hello.py"/>
```

The model followed the steering and emitted the XML tag after creating the file. Steering works when the instruction is contextually relevant to the task.

### Per-Thread Custom Steering: POSSIBLE WITH GLOBAL+LOCAL OVERRIDE

With the global agent approach (`~/.kiro/agents/`), all threads use the same global agent config by default. For threads that need custom steering, create a local `.kiro/agents/{agent_name}.json` in that thread's workspace directory — it takes precedence over the global config (`WARNING: Agent conflict. Using workspace version.`).

Options for per-thread context:
1. Create a local `.kiro/agents/` override in the thread's workspace directory (full custom agent config)
2. Include context in the `session/prompt` `content` field (prepend instructions to user message)
3. Place files in the thread's workspace directory and reference them with `@file.txt` in the prompt

### Summary Table

| Feature | Works? | Notes |
|---------|--------|-------|
| Agent discovery from `cwd/.kiro/` | ✅ | Only exact `cwd`, no parent walking |
| Agent discovery via symlink | ✅ | Symlink to shared `.kiro/` works |
| Agent discovery walking up dirs | ❌ | Docs say yes, CLI says no (v1.26.0) |
| `prompt` field (inline) | ✅ | Sent as context entry, followed when relevant |
| `prompt` field (`file://`) | ✅ | Same behavior as inline |
| `resources` with `file://` | ✅ | Steering files loaded into context |
| `resources` with `skill://` | ❓ | Not tested |
| `tools` / `allowedTools` | ✅ | Agent used write tool without prompting |
| `model` field | ✅ | Model selection works |
| `welcomeMessage` | ✅ | Displayed on agent switch |
| Per-thread custom steering | ✅ | Global+local override: create `.kiro/` in thread dir when needed |


### Global Agent + Local Override: THE WINNING APPROACH

Instead of symlinks, use `~/.kiro/agents/` for the default agent (global, always available from any cwd), and create a real `.kiro/` in specific thread directories only when custom steering is needed.

```
~/.kiro/agents/tg-bot.json              ← global default, always found
./workspaces/user123/thread456/         ← no .kiro/ needed, uses global
./workspaces/user123/thread789/.kiro/   ← local override when needed
  agents/tg-bot.json                    ← takes precedence with warning
```

**Full test results:**

| Test | Result |
|------|--------|
| Global agent from bare thread dir (no local `.kiro/`) | ✅ Agent found, `<send_file>` works |
| `prompt` field carries steering instructions | ✅ Model follows when contextually relevant |
| `resources` with relative path (`file://.kiro/steering/**/*.md`) from global agent | ❌ Resolves relative to `cwd`, not agent config dir — finds nothing |
| `resources` with absolute path (`file:///Users/.../steering/**/*.md`) | ✅ Steering file loaded into context |
| Local `.kiro/` override in thread dir | ✅ `WARNING: Agent conflict. Using workspace version.` — local wins |
| No symlinks needed | ✅ |

**Key finding on `resources` path resolution:**
The docs say `file://` paths in `prompt` resolve relative to the agent config file's directory. But `resources` paths appear to resolve relative to `cwd`, not the agent config location. For global agents in `~/.kiro/agents/`, use absolute paths in `resources` or put steering instructions directly in the `prompt` field.

**Benefits:**
- No symlinks at all
- Global agent works from any directory without setup
- Per-thread customization by creating `.kiro/` only where needed
- Clean separation: bot provisions `~/.kiro/agents/` once at startup, threads are clean by default
- Steering via `prompt` field is simplest and most reliable

**Trade-off:**
- Uses `~/.kiro/` (user-level) instead of workspace-level — affects all kiro-cli sessions on the machine, not just the bot
- Acceptable for PoC / single-purpose deployment; for multi-tenant production, would need isolation

---

## ACP `session/request_permission` — Tool Call Authorization

Discovered 2026-02-14. Kiro CLI sends `session/request_permission` as a **server-initiated JSON-RPC request** (has both `id` and `method`) before executing tool calls (readFile, writeFile, etc.).

### Problem

If the client doesn't respond, kiro-cli blocks indefinitely waiting for permission — the bot hangs and the agent reports "file operations are being blocked."

### Request Format (Agent → Client)

```json
{
  "jsonrpc": "2.0",
  "id": "1e1d9423-d751-496d-9107-6749b32bb10c",
  "method": "session/request_permission",
  "params": {
    "sessionId": "sess_abc123",
    "toolCall": { "toolCallId": "call_001" },
    "options": [
      { "optionId": "allow-once", "name": "Allow once", "kind": "allow_once" },
      { "optionId": "reject-once", "name": "Reject", "kind": "reject_once" }
    ]
  }
}
```

Note: The `id` is a UUID string (not an integer like our client-initiated requests).

### Response Format (Client → Agent)

```json
{
  "jsonrpc": "2.0",
  "id": "1e1d9423-d751-496d-9107-6749b32bb10c",
  "result": {
    "outcome": {
      "outcome": "selected",
      "optionId": "allow-once"
    }
  }
}
```

The `optionId` must match one of the options from `params.options`. Pick the one with `kind: "allow_once"` to auto-grant.

### Wrong Format (caused "file operations blocked")

```json
{"jsonrpc": "2.0", "id": "...", "result": {"granted": true}}
```

This is NOT the ACP spec format. Kiro CLI silently ignores it and treats the tool call as rejected.

### Cancellation

If the prompt turn is cancelled, respond with:

```json
{
  "jsonrpc": "2.0",
  "id": "...",
  "result": { "outcome": { "outcome": "cancelled" } }
}
```

### Permission Option Kinds

| Kind | Description |
|------|-------------|
| `allow_once` | Allow this operation only this time |
| `allow_always` | Allow and remember the choice |
| `reject_once` | Reject this operation only this time |
| `reject_always` | Reject and remember the choice |

### Links
- ACP Tool Calls Spec: https://agentclientprotocol.com/protocol/tool-calls
- Requesting Permission section: https://agentclientprotocol.com/protocol/tool-calls#requesting-permission

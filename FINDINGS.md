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
    "content": [{ "type": "text", "text": "User message here" }]
  }
}
```
Note: Kiro CLI uses `content` (not `prompt`) in `session/prompt` params.

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
| `AgentMessageChunk` | Streaming text/content from the agent |
| `ToolCall` | Tool invocation with name, parameters, status |
| `ToolCallUpdate` | Progress updates for running tools |
| `TurnEnd` | Signals the agent turn has completed |

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
┌──────────┐         ┌──────────────────┐         ┌──────────────┐
│ Telegram  │  HTTP   │  Python Bot      │  stdin  │  kiro-cli    │
│ User Chat │◄──────►│  (aiogram)       │◄──────►│  acp          │
│           │         │                  │  stdout │              │
└──────────┘         └──────────────────┘         └──────────────┘
                      │                  │
                      │ sendMessageDraft │
                      │ (streaming)      │
                      │                  │
                      │ sendMessage      │
                      │ (final)          │
                      └──────────────────┘
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
8. For each `AgentMessageChunk`, bot calls `sendMessageDraft` (same `draft_id`)
9. On `TurnEnd` / `stopReason: "end_turn"`, bot calls `sendMessage` with final text
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
/data/workspaces/
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

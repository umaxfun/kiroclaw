# Domain Entities — Unit 1: Foundation + ACP Echo

## Config

Immutable after load. Fail-fast on missing required values.

```
Config
  bot_token: str                    # REQUIRED — Telegram bot token
  workspace_base_path: str          # default: "./workspaces/"
  max_processes: int                # default: 5
  idle_timeout_seconds: int         # default: 30
  kiro_agent_name: str              # REQUIRED — no default
  log_level: str                    # default: "INFO" — controls stderr capture verbosity
  kiro_config_path: str             # default: "./kiro-config/" — template directory
```

## ACPClient State

```
ACPClientState (enum)
  IDLE          — spawned but not initialized
  INITIALIZING  — initialize sent, waiting for response
  READY         — initialized, can accept session commands
  BUSY          — session/prompt in flight, streaming
  DEAD          — process exited or crashed
```

State transitions:
```
IDLE --> INITIALIZING --> READY --> BUSY --> READY
                                      |
  DEAD <-- (any state on process exit/crash)
```

## JSON-RPC Message Types

### Outbound (bot → kiro-cli stdin)

```
JSONRPCRequest
  jsonrpc: "2.0"
  id: int                           # monotonically increasing per client
  method: str                       # "initialize", "session/new", "session/load",
                                    # "session/prompt", "session/set_model"
  params: dict

JSONRPCNotification
  jsonrpc: "2.0"
  method: str                       # "session/cancel"
  params: dict
```

### Inbound (kiro-cli stdout → bot)

```
JSONRPCResponse
  jsonrpc: "2.0"
  id: int                           # matches request id
  result: dict | None
  error: dict | None                # { code: int, message: str, data: any }

JSONRPCNotification
  jsonrpc: "2.0"
  method: str                       # "session/update", "_session/terminate",
                                    # "_kiro.dev/commands/available", etc.
  params: dict
```

### Session Update Types (within session/update notifications)

```
SessionUpdate
  sessionId: str
  update:
    sessionUpdate: str              # "AgentMessageChunk", "TurnEnd", "ToolCall",
                                    # "ToolCallUpdate", "plan"
    content: dict | None            # for AgentMessageChunk: { type: "text", text: str }
```

## Provisioner Managed Files

The provisioner uses prefix-based sync: on every startup, delete all files matching `{KIRO_AGENT_NAME}*` in each target directory, then copy fresh from `kiro-config/` template.

Managed directories and their prefix patterns:
- `~/.kiro/agents/{KIRO_AGENT_NAME}*` — agent config(s)
- `~/.kiro/steering/{KIRO_AGENT_NAME}*` — steering files
- `~/.kiro/skills/{KIRO_AGENT_NAME}*` — skill files/directories

Everything else in `~/.kiro/` is untouched. This is a full sync — not idempotent "create if missing" but "delete and replace."

### Safety Guardrails

Before any delete operation, the provisioner validates:
1. `KIRO_AGENT_NAME` is non-empty and >= 3 characters
2. `KIRO_AGENT_NAME` matches `^[a-zA-Z0-9_-]+$` — rejects wildcards (`*`), dots (`.`), slashes (`/`), spaces, and other special characters
3. `kiro-config/` template contains at least `agents/{KIRO_AGENT_NAME}.json` — refuses to sync if template is empty (won't delete without having something to replace with)
4. Total files matching prefix across all 3 directories does not exceed 20 — aborts with error if threshold exceeded (prevents runaway deletion from misconfiguration)

If any check fails, provisioner raises an error and the bot does not start.

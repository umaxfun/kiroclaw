---
title: "Agent Client Protocol (ACP) - CLI - Docs"
author: "Kiro"
source: "Kiro"
url: "https://kiro.dev/docs/cli/acp/"
date_saved: "2026-02-13T20:45:15.621Z"
word_count: "806"
reading_time: "5 min"
description: "Run Kiro CLI as an ACP-compliant agent for programmatic client integration"
tags:
  - "Kiro"
  - "Kiro IDE"
  - "Kiro Spec"
  - "Kiro AI"
  - "Kiro AI IDE"
  - "kiro"
  - "aws kiro"
  - "amazon kiro"
  - "agentic IDE"
  - "spec-driven development"
---

## Agent Client Protocol (ACP)

Kiro CLI implements the [Agent Client Protocol (ACP)](https://agentclientprotocol.com/get-started/introduction), an open standard that enables AI agents to work with any compatible editor. This means you can use Kiro's agentic capabilities in JetBrains IDEs, Zed, and other ACP-compatible editors.

## What is ACP?

AI coding agents and editors are tightly coupled, but interoperability isn't the default. Each editor must build custom integrations for every agent, and agents must implement editor-specific APIs. This creates integration overhead, limited compatibility, and developer lock-in.

ACP solves this by providing a standardized protocol for agent-editor communication—similar to how the Language Server Protocol (LSP) standardized language server integration. Agents that implement ACP work with any compatible editor, and editors that support ACP gain access to all ACP-compatible agents.

## Quick start

Run Kiro as an ACP agent:

bash

```bash
kiro-cli acp
```

The agent communicates over stdin/stdout using JSON-RPC 2.0. Configure your editor to spawn this command, and you're ready to go.

## Editor setup

Kiro CLI can be used as an ACP agent in any editor that supports the protocol.

### JetBrains IDEs

JetBrains IDEs (IntelliJ IDEA, WebStorm, PyCharm, etc.) support ACP through AI Assistant. See the [JetBrains ACP documentation](https://www.jetbrains.com/help/ai-assistant/acp.html) for full details.

To add Kiro as a custom agent:

1.  Open the AI Chat tool window
2.  Click the settings button and select **Add Custom Agent**
3.  Add the following to `~/.jetbrains/acp.json`:

json

```json
{

  "agent_servers": {

    "Kiro Agent": {

      "command": "/full/path/to/kiro-cli",

      "args": ["acp"]

    }

  }

}
```

The agent will appear in the AI Chat mode selector.

### Zed

Zed supports ACP agents natively. See the [Zed external agents documentation](https://zed.dev/docs/ai/external-agents#custom-agents) for full details. Add the following to your Zed settings (`~/.config/zed/settings.json`):

json

```json
{

  "agent_servers": {

    "Kiro Agent": {

      "type": "custom",

      "command": "~/.local/bin/kiro-cli",

      "args": ["acp"],

      "env": {}

    }

  }

}
```

Select "Kiro Agent" from the agent picker in Zed's AI panel.

### Other editors

Any editor supporting ACP can integrate Kiro by spawning `kiro-cli acp` and communicating via JSON-RPC over stdio. See the [ACP specification](https://agentclientprotocol.com) for protocol details.

## Supported ACP methods

Kiro CLI implements the following ACP methods, giving you access to session management, model selection, and streaming responses when using Kiro through any ACP-compatible editor.

### Core protocol

| Method | Description |
| --- | --- |
| `initialize` | Initialize the connection and exchange capabilities |
| `session/new` | Create a new chat session |
| `session/load` | Load an existing session by ID |
| `session/prompt` | Send a prompt to the agent |
| `session/cancel` | Cancel the current operation |
| `session/set_mode` | Switch agent mode (e.g., different agent configs) |
| `session/set_model` | Change the model for the session |

### Agent capabilities

The Kiro ACP agent advertises these capabilities during initialization:

-   `loadSession: true` - Supports loading existing sessions
-   `promptCapabilities.image: true` - Supports image content in prompts

### Session updates

The agent sends these session update types via `session/notification`:

| Update Type | Description |
| --- | --- |
| `AgentMessageChunk` | Streaming text/content from the agent |
| `ToolCall` | Tool invocation with name, parameters, status |
| `ToolCallUpdate` | Progress updates for running tools |
| `TurnEnd` | Signals the agent turn has completed |

## Kiro extensions

Kiro extends ACP with custom methods (prefixed with `_kiro.dev/` per the ACP spec) to expose Kiro-specific features like [slash commands](/docs/cli/reference/slash-commands), [MCP servers](/docs/cli/mcp), and [context compaction](/docs/cli/chat#context-management). Clients that don't support these extensions can safely ignore them—they're optional enhancements.

### Slash commands

| Method | Type | Description |
| --- | --- | --- |
| `_kiro.dev/commands/execute` | Request | Execute a slash command (e.g., `/agent swap`, `/context add`) |
| `_kiro.dev/commands/options` | Request | Get autocomplete suggestions for a partial command |
| `_kiro.dev/commands/available` | Notification | Sent after session creation with the list of available commands |

### MCP server events

| Method | Type | Description |
| --- | --- | --- |
| `_kiro.dev/mcp/oauth_request` | Notification | Provides OAuth URL when an MCP server requires authentication |
| `_kiro.dev/mcp/server_initialized` | Notification | Indicates an MCP server has finished initializing and its tools are available |

### Session management

| Method | Type | Description |
| --- | --- | --- |
| `_kiro.dev/compaction/status` | Notification | Reports progress when compacting conversation context |
| `_kiro.dev/clear/status` | Notification | Reports status when clearing session history |
| `_session/terminate` | Notification | Terminates a subagent session |

## Example: Initialize connection

Here's how an ACP client initializes a connection with Kiro:

json

```json
// Client sends initialize request

{

  "jsonrpc": "2.0",

  "id": 0,

  "method": "initialize",

  "params": {

    "protocolVersion": 1,

    "clientCapabilities": {

      "fs": {

        "readTextFile": true,

        "writeTextFile": true

      },

      "terminal": true

    },

    "clientInfo": {

      "name": "my-editor",

      "version": "1.0.0"

    }

  }

}

// Kiro responds with capabilities

{

  "jsonrpc": "2.0",

  "id": 0,

  "result": {

    "protocolVersion": 1,

    "agentCapabilities": {

      "loadSession": true,

      "promptCapabilities": {

        "image": true

      }

    },

    "agentInfo": {

      "name": "kiro-cli",

      "version": "1.5.0"

    }

  }

}
```

After initialization, create a session and start prompting:

json

```json
// Create a new session

{

  "jsonrpc": "2.0",

  "id": 1,

  "method": "session/new",

  "params": {

    "cwd": "/home/user/my-project",

    "mcpServers": []

  }

}

// Send a prompt

{

  "jsonrpc": "2.0",

  "id": 2,

  "method": "session/prompt",

  "params": {

    "sessionId": "sess_abc123",

    "content": [

      {

        "type": "text",

        "text": "Explain this codebase"

      }

    ]

  }

}
```

## Session storage

ACP sessions are persisted to disk at:

```bash
~/.kiro/sessions/cli/
```

Each session creates two files:

-   `<session-id>.json` - Session metadata and state
-   `<session-id>.jsonl` - Event log (conversation history)

## Logging

ACP agent logs are written to the standard Kiro log location:

| Platform | Location |
| --- | --- |
| macOS | `$TMPDIR/kiro-log/kiro-chat.log` |
| Linux | `$XDG_RUNTIME_DIR/kiro-log/kiro-chat.log` |

Control log verbosity with environment variables:

bash

```bash
KIRO_LOG_LEVEL=debug kiro-cli acp

KIRO_CHAT_LOG_FILE=/path/to/custom.log kiro-cli acp
```

-   [Interactive Chat](/docs/cli/chat) - Uses ACP internally
-   [MCP Integration](/docs/cli/mcp) - MCP servers can be passed to ACP sessions
-   [CLI Commands Reference](/docs/cli/reference/cli-commands)

Page updated: February 9, 2026

[Steering](/docs/cli/steering/)

[Experimental](/docs/cli/experimental/)

---
# KiroClaw

> ⚠️ **Security Note**: This bot is not suitable for untrusted multi-user deployments. Users with coding tool access can bypass application-layer isolation. See [CONTAINER_ISOLATION_PROPOSAL.md](CONTAINER_ISOLATION_PROPOSAL.md) for true isolation via containers.

Telegram bot that connects [Kiro CLI](https://kiro.dev/docs/cli/) via the Agent Client Protocol (ACP) to a threaded Telegram bot. Each forum thread gets its own Kiro session with full conversation history, file exchange, and streaming responses.

Use cases:
- On-the-go translation with full conversation memory
- File analysis — send documents, images, or audio and get structured responses (if you have tools to process them)
- Coding assistance from your phone — same Kiro agent, just over Telegram

Built-in capabilities (inherited from Kiro CLI's default agent):
- Upload files to the bot and get processed results back as files or text
- Bidirectional file exchange — the agent can create and send files to you
- Add custom skills in the [Agent Skill format](https://agentskills.io/specification)
- Steering files for shaping agent behavior per deployment
- Model selection per thread (`/model`) — switch between Claude variants on the fly
- Full conversation memory within each thread
- **Per-user session isolation** — users cannot access each other's conversations or files

![](media/screenshot-assistant.png)

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/) package manager
- [kiro-cli](https://kiro.dev/docs/cli/) installed and on PATH
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- A Telegram bot with threads enabled

### BotFather Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) (`/newbot`)
2. Enable threaded mode: BotFather → your bot → Bot Settings → Threads Settings → Threaded Mode On


## Installation

```bash
git clone https://github.com/umaxfun/kiroclaw
cd kiroclaw
uv sync
```

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BOT_TOKEN` | Yes | — | Telegram bot token |
| `KIRO_AGENT_NAME` | Yes | tg-acp | Do not modify it unless you are certain of what you are doing |
| `ALLOWED_TELEGRAM_IDS` | No | _(empty)_ | Comma-separated Telegram user IDs allowed to use the bot. Empty = nobody allowed (fail-closed) |
| `KIRO_CONFIG_PATH` | No | `./kiro-config/` | Path to the agent config template directory |
| `WORKSPACE_BASE_PATH` | No | `./workspaces/` | Base directory for per-user/thread workspaces |
| `MAX_PROCESSES` | No | `5` | Max kiro-cli processes in the pool |
| `IDLE_TIMEOUT_SECONDS` | No | `30` | Seconds before killing idle extra processes |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

### Getting your Telegram ID

Deploy with `ALLOWED_TELEGRAM_IDS` empty, then message the bot. It will reply with your numeric Telegram ID. Add that ID to `.env` and restart.

## Kiro Agent Setup

The `kiro-config/` directory contains the agent template that gets provisioned to `~/.kiro/` on first run:

```
kiro-config/
├── agents/       # Agent config JSON (must contain {KIRO_AGENT_NAME}.json)
├── skills/       # Kiro skills
└── steering/     # Steering files for agent behavior
```

The bot copies this to `~/.kiro/` at startup if not already present. The agent config defines the model, tools, and steering that kiro-cli uses when responding.

## Running

```bash
uv run main.py
```

The bot will:
1. Validate prerequisites (kiro-cli, agent config, workspace directory)
2. Provision `~/.kiro/` from the template (first run only)
3. Initialize the process pool
4. Start polling Telegram for messages

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message (shows your Telegram ID if access is restricted) |
| `/model` | List available models or set one for the current thread (`/model claude-sonnet-4`) |

## Architecture

```
Telegram ──> Bot Handlers (C6)
                 │
                 ├── Config (C7) ─── .env
                 ├── Session Store (C3) ─── SQLite (tg-acp.db)
                 ├── Process Pool (C2) ─── kiro-cli processes
                 │       └── ACP Client (C1) ─── stdin/stdout JSON-RPC
                 ├── Stream Writer (C5) ─── chunked Telegram messages
                 ├── File Handler (C4) ─── bidirectional file transfer
                 └── Workspace Provisioner (C8) ─── ~/.kiro/ setup
```

Each forum thread maps to one Kiro session. The process pool manages kiro-cli instances with thread affinity, idle reaping, and request queuing when all slots are busy.

## Security

### ⚠️ Critical Security Limitation

**The current application-layer isolation is NOT sufficient for untrusted multi-user deployments.**

Users with coding tool access (Python, bash, etc.) can bypass all application-layer protections:

```python
# Any user can do this to access other users' data:
import os
for root, dirs, files in os.walk('/home/user/.kiro/sessions/cli'):
    for file in files:
        print(open(os.path.join(root, file)).read())
```

**Current Status**: The bot implements session ID prefixing and process slot binding, but these only prevent **accidental** cross-user access through the application. A malicious user with tool access can trivially bypass these protections.

**For True Multi-User Security**: See [CONTAINER_ISOLATION_PROPOSAL.md](CONTAINER_ISOLATION_PROPOSAL.md) for a container-based architecture that provides kernel-level isolation.

### Current Application-Layer Protections

The following protections are implemented but **do not prevent determined attackers** with tool access:

**Per-User Session IDs**: Session IDs prefixed with `user-{telegram_user_id}-` to prevent accidental cross-user access through the application API.

**Process Pool Isolation**: kiro-cli processes are bound to single users to prevent memory leakage between users at the application level.

**Session Affinity**: `(user_id, thread_id)` tuples ensure threads return to the correct process.

**Workspace Isolation**: Directories organized by user, but filesystem permissions are not enforced:
```
./workspaces/
  ├── {telegram_user_id}/
  │   ├── {thread_id_1}/
  │   └── {thread_id_2}/
```

### Access Control

**Fail-Closed by Default**: `ALLOWED_TELEGRAM_IDS` must be explicitly set. Empty = nobody allowed.

**User Allowlist**: Only Telegram user IDs in the allowlist can use the bot. This is the primary security control for trusted-user deployments.

### Recommended Use Cases

✅ **Single user** - No security concerns
✅ **Small trusted group** (family, close friends) - Low risk of malicious behavior
❌ **Public/untrusted users** - Application-layer isolation is insufficient

### For Production Multi-User Deployments

See [CONTAINER_ISOLATION_PROPOSAL.md](CONTAINER_ISOLATION_PROPOSAL.md) for a complete architecture using:
- Docker containers for per-user isolation
- Kernel-level filesystem and process isolation
- Resource limits (CPU, memory, disk)
- True security guarantees even with tool access

This is the **only** way to safely run the bot with untrusted users who have coding tool access.

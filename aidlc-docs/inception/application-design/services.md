# Services

This project doesn't have a traditional service layer — it's a single-process bot application, not a microservices system. The orchestration lives in Bot Handlers (C6), which coordinates the other components.

## Orchestration Flow (Bot Handlers)

The main orchestration for a user message:

```
User Message (Telegram)
    |
    v
Bot Handlers (C6)
    |
    +---> [If in-flight prompt for this thread] Cancel it
    |
    +---> SessionStore.get_session(user_id, thread_id)
    |         |
    |         +---> Found? Will load existing session
    |         +---> Not found? New thread — will create session later via ACP
    |
    +---> [If new thread] Create workspace dir ./workspaces/{uid}/{tid}/
    |
    +---> [If file attached] FileHandler.download_to_workspace()
    |
    +---> ProcessPool.acquire() — get a free process
    |         |
    |         +---> Free process available? Return it
    |         +---> All busy, under max? Spawn new one
    |         +---> All busy, at max? Queue request (dedup by thread_id)
    |
    +---> [If existing session] ACPClient.session_load(session_id)
    +---> [If new thread] ACPClient.session_new(cwd) → session_id
    |                     SessionStore.upsert_session(user_id, thread_id, session_id, workspace_path)
    |
    +---> ACPClient.session_prompt(content)
    |         |
    |         +---> StreamWriter.write_chunk() for each AgentMessageChunk
    |         +---> StreamWriter.finalize() on TurnEnd
    |
    +---> [If <send_file> tags found] FileHandler.send_file() for each
    |
    +---> ProcessPool.release(client)
```

Note: Workspace provisioning is NOT in the per-message flow. The global `~/.kiro/` config is provisioned on first run (see entry point below). kiro-cli finds the global agent from `~/.kiro/agents/` regardless of `cwd`.

## Application Entry Point

```
main.py
    |
    +---> Config.load() — load .env, validate
    +---> Config.validate_kiro_cli() — fail fast if missing
    +---> WorkspaceProvisioner(config).provision() — first-run: install global agent to ~/.kiro/ from kiro-config/ template
    +---> SessionStore(db_path) — open/create SQLite
    +---> ProcessPool.start(agent_name=config.kiro_agent_name) — spawn warm process
    +---> Register Bot Handlers on aiogram Dispatcher
    +---> Start polling
```

## Workspace Directory Layout

```
~/.kiro/                                   ← global config (REQUIRED — provisioned on first run / installation)
  agents/{agent_name}.json                 ← custom agent (REQUIRED) — <send_file> steering, subagent config, tools
  steering/                                ← global steering files (optional, for additional context)
  skills/                                  ← global skill files (optional)

./workspaces/                              ← WORKSPACE_BASE_PATH
  {user_id}/
    {thread_id}/                           ← cwd for session/new (uses global ~/.kiro/ agent)
    {thread_id_custom}/                    ← thread needing custom behavior
      .kiro/agents/{agent_name}.json       ← local override (takes precedence over global)
```

The system requires at least one custom global agent in `~/.kiro/agents/`. This agent defines the bot's core behavior: `<send_file>` XML tag steering in the `prompt` field, subagent configuration, allowed tools, and model settings. The agent config is a project artifact — it lives in the `kiro-config/` template directory in the bot's source tree and is provisioned to `~/.kiro/` as part of first-run setup (or installation on a new machine). The Workspace Provisioner (C8) handles this automatically on bot startup.

kiro-cli checks two locations for agent configs: `cwd/.kiro/agents/` (local) and `~/.kiro/agents/` (global). It does NOT walk up parent directories (verified experimentally on v1.26.0). Per-thread overrides are created only when needed by placing `.kiro/agents/` in the thread directory — local takes precedence.

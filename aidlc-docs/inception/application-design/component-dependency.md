# Component Dependencies

## Dependency Matrix

| Component              | Depends On                                      |
|------------------------|------------------------------------------------|
| C1: ACP Client         | (none — receives agent_name as parameter from C2) |
| C2: Process Pool       | C1: ACP Client, C7: Config                     |
| C3: Session Store      | (none — standalone SQLite wrapper)              |
| C4: Stream Writer      | (none — takes aiogram Bot as constructor arg)   |
| C5: File Handler       | (none — takes aiogram Bot/Message as args)      |
| C6: Bot Handlers       | C2, C3, C4, C5, C7                             |
| C7: Config             | (none — standalone config loader)               |
| C8: Workspace Provisioner | C7: Config                                   |

## Dependency Diagram

```
+------------------+
|  C6: Bot         |
|  Handlers        |
+--+--+--+--+-----+
   |  |  |  |  |
   |  |  |  |  +-----> C7: Config
   |  |  |  |
   |  |  |  +--------> C5: File Handler
   |  |  |
   |  |  +-----------> C4: Stream Writer
   |  |
   |  +--------------> C3: Session Store
   |
   +-----------------> C2: Process Pool ----> C7: Config
                            |
                            +---> C1: ACP Client

+---------------------+
| C8: Workspace       |-----> C7: Config
| Provisioner         |
+---------------------+
(runs on every startup — prefix-based sync of {KIRO_AGENT_NAME}* files)
```

## Communication Patterns

- **C6 → C2**: Async acquire/release. Bot Handlers request a process, use it, release it back.
- **C6 → C3**: Sync-style calls (SQLite is fast). Lookup/create session records.
- **C6 → C4**: Create StreamWriter per response. Feed chunks, call finalize.
- **C6 → C5**: Download files on inbound messages. Send files on outbound (after finalize).
- **C2 → C1**: Pool owns ACP Client instances. Spawns (passing agent_name), tracks, kills them.
- **C2 → C7**: Process Pool reads agent_name from Config (passed at `start()`).
- **C8 → C7**: Workspace Provisioner reads config at startup to provision global `~/.kiro/`.
- **main → C8**: Entry point calls provisioner once before starting the bot.

## Data Flow

```
[STARTUP]
main.py --> C8: Workspace Provisioner --> ~/.kiro/ (sync {agent_name}* prefix: delete + copy from template)

[PER MESSAGE]
Telegram Update
    |
    v
C6: Bot Handlers
    |
    +--(user_id, thread_id)--> C3: Session Store --> session_id, model
    |
    +--(file)--> C5: File Handler --> local file path
    |
    +-----> C2: Process Pool --> C1: ACP Client (--agent flag from C2)
    |                                           |
    |                                    (stdin: JSON-RPC)
    |                                    session_load or session_new
    |                                           |
    |                                    kiro-cli acp --agent {name}
    |                                    cwd = ./workspaces/{uid}/{tid}/
    |                                    (finds agent from ~/.kiro/agents/ — global)
    |                                    (or from cwd/.kiro/agents/ — local override)
    |                                           |
    |                                    (stdout: JSON-RPC)
    |                                    (includes subagent notifications)
    |                                           |
    +--(chunks)--> C4: Stream Writer --> sendMessageDraft / sendMessage
    |
    +--(file paths from <send_file>)--> C5: File Handler --> sendDocument
```

## Key Design Decisions

- **No circular dependencies**: All arrows point downward from C6.
- **C1-C5, C8 are independent of each other**: They can be developed and tested in isolation (C2 and C8 depend on C7 Config; C1 receives agent_name as a parameter).
- **C6 is the only orchestrator**: All coordination logic lives here.
- **C7 is injected at startup**: Config is loaded once and passed to components that need it.
- **Subagents are kiro-cli internal**: The bot provisions agent configs that enable subagents, but does not manage subagent lifecycle — kiro-cli handles that.
- **Skills and steering are file-based**: The bot syncs files matching `{KIRO_AGENT_NAME}*` prefix in `~/.kiro/` from the `kiro-config/` template on every startup (delete + copy). kiro-cli discovers and loads them via its agent config. Per-thread overrides use local `.kiro/` in the thread directory.

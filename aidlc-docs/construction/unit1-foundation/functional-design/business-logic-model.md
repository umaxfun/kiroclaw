# Business Logic Model — Unit 1: Foundation + ACP Echo

## C7 Config — Load and Validate

```
Config.load():
  1. Load .env file via python-dotenv (if exists)
  2. Read environment variables:
     - BOT_TOKEN (str, REQUIRED)
     - WORKSPACE_BASE_PATH (str, default "./workspaces/")
     - MAX_PROCESSES (int, default 5)
     - IDLE_TIMEOUT_SECONDS (int, default 30)
     - KIRO_AGENT_NAME (str, REQUIRED)
     - LOG_LEVEL (str, default "INFO")
     - KIRO_CONFIG_PATH (str, default "./kiro-config/")
  3. Validate REQUIRED fields are non-empty — raise on missing
  4. Validate KIRO_AGENT_NAME: >= 3 chars, matches ^[a-zA-Z0-9_-]+$
  5. Parse int fields — raise on non-numeric
  6. Return frozen Config instance

Config.validate_kiro_cli():
  1. Check kiro-cli on PATH: shutil.which("kiro-cli")
     - If None: raise "kiro-cli not found on PATH"
  2. Check KIRO_CONFIG_PATH directory exists
     - If not: raise "kiro-config/ template directory not found at {path}"
  3. Check template contains agents/{KIRO_AGENT_NAME}.json
     - If not: raise "Agent config template not found: {path}"
  4. Check WORKSPACE_BASE_PATH is writable (create if needed)
     - If not writable: raise "Workspace directory not writable: {path}"
```

## C8 Workspace Provisioner — Prefix-Based Sync

```
WorkspaceProvisioner.provision():
  1. Resolve home = Path.home()
  2. Define managed directories:
     - (kiro-config/agents/,   ~/.kiro/agents/)
     - (kiro-config/steering/, ~/.kiro/steering/)
     - (kiro-config/skills/,   ~/.kiro/skills/)
  3. Ensure target directories exist (mkdir -p)
  4. Count total files matching {KIRO_AGENT_NAME}* across all 3 target dirs
     - If > 20: raise "Safety limit exceeded: {count} files match prefix"
  5. For each (src_dir, dst_dir):
     a. List entries in dst_dir matching {KIRO_AGENT_NAME}*
     b. Delete each matching entry (file or directory tree)
     c. List entries in src_dir matching {KIRO_AGENT_NAME}*
     d. Copy each to dst_dir (preserving relative structure)
  6. Verify ~/.kiro/agents/{KIRO_AGENT_NAME}.json exists after sync
     - If not: raise "Agent config not found after provisioning"
```

## C1 ACP Client — Protocol State Machine

```
ACPClient lifecycle:

  SPAWN:
    1. asyncio.create_subprocess_exec(
         "kiro-cli", "acp", "--agent", agent_name,
         stdin=PIPE, stdout=PIPE, stderr=PIPE
       )
    2. Start stderr reader task (logs at configured LOG_LEVEL)
    3. Start stdout reader task (line-by-line JSON parsing)
    4. State = IDLE

  INITIALIZE:
    1. Send JSON-RPC request:
       {
         "jsonrpc": "2.0",
         "id": next_id(),
         "method": "initialize",
         "params": {
           "protocolVersion": 1,
           "clientCapabilities": {
             "fs": { "readTextFile": true, "writeTextFile": true },
             "terminal": true
           },
           "clientInfo": {
             "name": "tg-acp-bot",
             "title": "Telegram ACP Bot",
             "version": "0.1.0"
           }
         }
       }
    2. Wait for response with matching id
    3. Store server capabilities from result
    4. State = READY

  SESSION_NEW(cwd):
    1. Precondition: state == READY
    2. Send JSON-RPC request:
       { method: "session/new", params: { cwd: cwd, mcpServers: [] } }
    3. Wait for response — extract sessionId from result
    4. Return session_id

  SESSION_LOAD(session_id):
    1. Precondition: state == READY
    2. Send JSON-RPC request:
       { method: "session/load", params: { sessionId: session_id } }
    3. Wait for response — verify success

  SESSION_PROMPT(session_id, content):
    1. Precondition: state == READY
    2. State = BUSY
    3. Send JSON-RPC request:
       {
         method: "session/prompt",
         params: { sessionId: session_id, content: content }
       }
    4. Yield session/update notifications as they arrive:
       - Filter for method == "session/update"
       - Extract params.update.sessionUpdate type
       - Yield each notification to caller
    5. When response with matching id arrives (stopReason: "end_turn"):
       - Yield a turn_end marker
       - State = READY

  SESSION_CANCEL(session_id):
    1. Send JSON-RPC notification (no id):
       { method: "session/cancel", params: { sessionId: session_id } }
    2. Note: this is a notification, no response expected

  SESSION_SET_MODEL(session_id, model):
    1. Send JSON-RPC request:
       { method: "session/set_model", params: { sessionId: session_id, model: model } }
    2. Wait for response

  STDOUT READER (background task):
    1. Read lines from stdout
    2. Parse each line as JSON
    3. If has "id" field: it's a response — route to pending request by id
    4. If no "id" field: it's a notification — route to notification handler
    5. On EOF: State = DEAD, signal all waiters

  STDERR READER (background task):
    1. Read lines from stderr
    2. Log each line at configured level
    3. On EOF: stop

  IS_ALIVE:
    1. Return process.returncode is None

  KILL:
    1. process.terminate()
    2. Wait up to 5 seconds for exit
    3. If still alive: process.kill()
    4. State = DEAD
```

## Main Entry Point (Unit 1 — CLI Version)

```
main.py (Unit 1):
  1. Config.load()
  2. Config.validate_kiro_cli()
  3. WorkspaceProvisioner(config).provision()
  4. Create workspace dir: ./workspaces/test_user/test_thread/
  5. client = await ACPClient.spawn(config.kiro_agent_name)
  6. await client.initialize()
  7. session_id = await client.session_new(cwd=workspace_dir)
  8. Prompt user for input (or use hardcoded test message)
  9. async for update in client.session_prompt(session_id, content):
       - If agent_message_chunk: print chunk text to stdout (no newline)
       - If turn_end: print newline, break
  10. await client.kill()
```

This is a throwaway entry point — replaced by the aiogram bot in Unit 3.

# Units of Work

## Overview

5 vertical-slice units, each delivering a running system. Built incrementally — each unit extends the previous one.

| Unit | Name | Components | Delivers |
|------|------|------------|----------|
| 1 | Foundation + ACP Echo | C7, C8, C1 | CLI script that streams kiro-cli response to stdout |
| 2 | Session Persistence | C3 | Session continuity across runs via SQLite |
| 3 | Telegram Bot + Streaming | C6 (partial), C4 | Bot with sendMessageDraft streaming, text only |
| 4 | File Handling + Commands | C5, C6 (extended), C4 (extended) | Bidirectional files, /model command |
| 5 | Process Pool + Cancel | C2, C6 (extended) | Multi-process pool, cancel-in-flight, queue |

---

## Unit 1: Foundation + ACP Echo

**Components**: C7 Config, C8 Workspace Provisioner, C1 ACP Client

**What it delivers**: A runnable `main.py` that:
1. Loads `.env` config and validates prerequisites
2. Syncs `~/.kiro/` from `kiro-config/` template (prefix-based: delete + copy `{KIRO_AGENT_NAME}*`)
3. Spawns `kiro-cli acp --agent {name}`
4. Sends `initialize` → `session/new` → `session/prompt`
5. Reads `session/update` notifications and prints streaming chunks to stdout
6. Detects `turn_end` and exits cleanly

**Project artifacts created**:
- `kiro-config/` template directory with agent JSON, steering files
- `.env.example` with all config keys documented
- `src/tg_acp/config.py` — C7
- `src/tg_acp/provisioner.py` — C8
- `src/tg_acp/acp_client.py` — C1
- `main.py` — entry point

**Test scope**:
- ACP protocol integration: real kiro-cli, full JSON-RPC flow
- Config validation: missing kiro-cli, missing KIRO_AGENT_NAME, missing template
- Provisioner sync: run twice, verify prefix files are replaced correctly and non-prefix files untouched

---

## Unit 2: Session Persistence

**Components**: C3 Session Store

**What it delivers**: The system from Unit 1, extended with:
1. SQLite database (`./tg-acp.db`) for session mapping
2. Workspace directory creation (`./workspaces/{user_id}/{thread_id}/`)
3. First run: `session/new` with thread-specific `cwd`, store mapping
4. Second run: `session/load` with stored session_id

**Project artifacts created**:
- `src/tg_acp/session_store.py` — C3

**What changes from Unit 1**:
- `main.py` now accepts user_id/thread_id args (or hardcoded test values)
- Before prompting: check SQLite for existing session → session/load or session/new
- After session/new: upsert mapping in SQLite

**Test scope**:
- Session continuity: run twice, verify session/load on second run
- SQLite operations: schema creation, upsert, get, set_model (real SQLite)
- Workspace dirs: created on first message, exist on second

---

## Unit 3: Telegram Bot with Streaming

**Components**: C6 Bot Handlers (partial), C4 Stream Writer

**What it delivers**: A long-running Telegram bot that:
1. Receives text messages in a forum topic
2. Looks up or creates a Kiro session for the thread
3. Sends the prompt to kiro-cli
4. Streams the response via `sendMessageDraft` (sliding window for long responses)
5. Finalizes with `sendMessage` (split into multiple messages if >4096 chars)
6. `/start` command with welcome message

**Project artifacts created**:
- `src/tg_acp/stream_writer.py` — C4
- `src/tg_acp/bot_handlers.py` — C6 (partial)
- `main.py` rewritten as aiogram bot entry point

**What changes from Unit 2**:
- Entry point changes from CLI script to `aiogram.Dispatcher` with long-polling
- Session lookup uses Telegram's `user_id` + `message_thread_id`
- Single kiro-cli process managed directly (no pool)
- Streaming output goes to Telegram via sendMessageDraft instead of stdout

**Test scope**:
- Streaming: send message in test forum, verify draft updates animate, final message appears
- Session continuity: send two messages in same thread, verify session/load on second
- Sliding window: trigger a long response (>4096 chars), verify draft shows tail end
- Multi-message split: verify final response splits correctly
- /start command: verify welcome message

---

## Unit 4: File Handling + Commands

**Components**: C5 File Handler, C6 Bot Handlers (extended), C4 Stream Writer (extended)

**What it delivers**: The bot from Unit 3, extended with:
1. Inbound files: user sends file/document/audio → downloaded to workspace → referenced in ACP prompt
2. Outbound files: agent emits `<send_file path="...">description</send_file>` → bot parses, strips from text, sends via sendDocument
3. `/model` command: no args = list models, with arg = set model (persisted in SQLite)

**Project artifacts created**:
- `src/tg_acp/file_handler.py` — C5

**What changes from Unit 3**:
- C4 StreamWriter.finalize() now parses `<send_file>` tags, returns file paths
- C6 adds file/document/audio message handler (download → prompt with file reference)
- C6 adds /model command handler
- Path validation in C5 (security: files must be within workspace boundary)

**Test scope**:
- Inbound file: send a .txt file via Telegram, verify it appears in workspace dir
- Inbound audio: send audio, verify downloaded to workspace
- Outbound file: prompt agent to create a file, verify `<send_file>` parsed and file sent back
- /model list: verify hardcoded model list displayed
- /model set: verify model persisted in SQLite and used in next session/set_model
- Path traversal: verify files outside workspace boundary are rejected

---

## Unit 5: Process Pool + Cancel

**Components**: C2 Process Pool, C6 Bot Handlers (extended)

**What it delivers**: The bot from Unit 4, hardened with:
1. Process pool with scale-to-one semantics (always 1 warm process)
2. Spawn additional processes on demand, up to MAX_PROCESSES
3. Idle timeout kills extras, last process never killed
4. Cancel-in-flight: new message in same thread cancels previous streaming prompt
5. Request queue with per-thread-id dedup when pool is at capacity

**Project artifacts created**:
- `src/tg_acp/process_pool.py` — C2

**What changes from Unit 4**:
- C6 replaces direct ACP Client usage with ProcessPool.acquire()/release()
- C6 tracks in-flight prompts per thread, sends session/cancel on new message
- ProcessPool manages ACP Client lifecycle (spawn, track, idle-kill, replace crashed)

**Test scope**:
- Warm process: verify 1 process alive after startup
- Spawn on demand: concurrent messages from different threads → additional processes spawned
- Idle timeout: extra processes killed after IDLE_TIMEOUT_SECONDS, last one kept
- Cancel-in-flight: send message while previous is streaming → previous cancelled, new response starts
- Queue dedup: pool at max, send 3 messages for same thread fast → only latest processed
- Crash recovery: kill a kiro-cli process mid-stream → pool replaces it, session/load resumes

---

## Code Organization (Greenfield)

```
tg-acp/                              # workspace root
+-- main.py                          # entry point (evolves: CLI → aiogram bot)
+-- .env                             # local config (gitignored)
+-- .env.example                     # documented config template
+-- pyproject.toml                   # dependencies (uv)
+-- kiro-config/                     # agent template directory (version-controlled)
|   +-- agents/
|   |   +-- {agent_name}.json        # custom agent config
|   +-- steering/                    # global steering files (optional)
|   +-- skills/                      # global skill files (optional)
+-- src/
|   +-- tg_acp/
|       +-- __init__.py
|       +-- config.py                # C7 (Unit 1)
|       +-- provisioner.py           # C8 (Unit 1)
|       +-- acp_client.py            # C1 (Unit 1)
|       +-- session_store.py         # C3 (Unit 2)
|       +-- stream_writer.py         # C4 (Unit 3)
|       +-- bot_handlers.py          # C6 (Unit 3, extended in 4, 5)
|       +-- file_handler.py          # C5 (Unit 4)
|       +-- process_pool.py          # C2 (Unit 5)
+-- tests/
|   +-- test_acp_protocol.py         # Unit 1 tests
|   +-- test_session_store.py        # Unit 2 tests
|   +-- test_bot_streaming.py        # Unit 3 tests
|   +-- test_file_handling.py        # Unit 4 tests
|   +-- test_process_pool.py         # Unit 5 tests
+-- workspaces/                      # runtime: per-user/thread dirs (gitignored)
+-- tg-acp.db                        # runtime: SQLite (gitignored)
```

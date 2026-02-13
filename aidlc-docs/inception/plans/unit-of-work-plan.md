# Unit of Work Plan

## Decomposition Approach

Vertical slices — each unit delivers a running system that does more than the previous one. Components are built incrementally across units, not one-at-a-time. This ensures the system converges at every step.

**Terminology**: Each unit is a vertical capability slice within a single Python package. No separate deployables.

## Plan Steps

- [x] Define units with component assignments, responsibilities, and test scope
- [x] Define unit dependency ordering (build sequence)
- [x] Generate `aidlc-docs/inception/application-design/unit-of-work.md`
- [x] Generate `aidlc-docs/inception/application-design/unit-of-work-dependency.md`
- [x] Document code organization strategy in `unit-of-work.md` (greenfield)
- [x] Validate unit boundaries and dependencies

## Unit Decomposition

### Unit 1: Foundation + ACP Echo (C7, C8, C1)
**Goal**: A running script that loads config, provisions the global agent, spawns kiro-cli, sends a hardcoded prompt, and streams the response to stdout.

- C7 Config: Load .env, validate prerequisites (kiro-cli on PATH, KIRO_AGENT_NAME, kiro-config/ template)
- C8 Workspace Provisioner: Provision `~/.kiro/` from `kiro-config/` template (agent JSON, steering, skills). Idempotent.
- C1 ACP Client: Spawn `kiro-cli acp --agent {name}`, initialize, session/new, session/prompt, stream session/update notifications, detect TurnEnd
- Create `kiro-config/` template directory with the actual agent JSON file (project artifact)
- Minimal `main.py` entry point that ties it together
- **Test**: Run the script, send a prompt, see streaming chunks printed to stdout. Verify initialize → session/new → prompt → streaming → TurnEnd flow.

### Unit 2: Session Persistence (C3)
**Goal**: The system remembers sessions across runs. Second invocation loads the existing session instead of creating a new one.

- C3 Session Store: SQLite CRUD for (user_id, thread_id) → session_id mapping, model selection, workspace paths
- Workspace directory creation: `./workspaces/{user_id}/{thread_id}/`
- Wire into main.py: session/new on first run, session/load on subsequent runs
- **Test**: Run twice with same user/thread IDs. First run creates session (session/new), second run loads it (session/load). Verify via SQLite that mapping persists. Real SQLite, no mocks.

### Unit 3: Telegram Bot with Streaming (C6 partial, C4)
**Goal**: A working Telegram bot that streams Kiro responses in real-time via sendMessageDraft. Text messages only, single process, no file handling or commands yet.

- C6 Bot Handlers (partial): aiogram text message handler, /start welcome. Orchestration for single kiro-cli process — session lookup, session/new or session/load, prompt, stream, finalize.
- C4 Stream Writer: Accumulate chunks, sendMessageDraft with sliding window, finalize with sendMessage
- Rewrite entry point from CLI script to long-running aiogram bot
- Uses a single kiro-cli process (no pool)
- **Test**: Real Telegram test bot + test forum. Send a text message, verify streaming draft updates and final message. Send a second message in same thread, verify session continuity (session/load).

### Unit 4: File Handling + Commands (C5, C6 extended)
**Goal**: Bidirectional file transfer and bot commands.

- C5 File Handler: Download inbound files to workspace, send outbound files via sendDocument, path validation
- C4 Stream Writer extended: Parse and strip `<send_file>` tags from finalized response
- C6 Bot Handlers extended: File/document/audio message handler, /model command (list models, set model via session/set_model, persist in SQLite)
- **Test**: Send a file via Telegram, verify it lands in workspace and is referenced in the ACP prompt. Trigger `<send_file>` from agent, verify bot sends file back. Test /model list and /model set.

### Unit 5: Process Pool + Cancel (C2, C6 extended)
**Goal**: Multi-process pool with scale-to-one semantics, cancel-in-flight, request queuing.

- C2 Process Pool: Warm process, spawn on demand up to MAX_PROCESSES, idle timeout, queue with thread-id dedup
- Cancel-in-flight: session/cancel when new message arrives for same thread while previous is streaming
- Replace single-process usage in C6 with pool acquire/release
- **Test**: Real kiro-cli processes. Concurrent messages from different threads — verify pool spawns. Idle timeout kills extras. Send message while previous is streaming — verify cancel + new response. Queue dedup: send multiple messages fast, verify only latest is processed.

## Resolved Questions

### Q1: Stream Writer + File Handler Grouping
**Answer**: File Handler separated into Unit 4 (after basic streaming works). Stream Writer core in Unit 3, `<send_file>` parsing added in Unit 4.

### Q2: Workspace Provisioner Placement
**Answer**: Unit 1 — provisioning is foundational, kiro-cli can't run without the agent being provisioned.

### Q3: Streaming vs File Handling Split
**Answer**: Streaming (sendMessageDraft) is core — goes in Unit 3 with the first bot unit. File handling, commands, and cancel-in-flight are layered on in Units 4 and 5.

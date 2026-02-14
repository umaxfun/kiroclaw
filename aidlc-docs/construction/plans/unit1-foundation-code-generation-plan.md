# Code Generation Plan — Unit 1: Foundation + ACP Echo

## Unit Context

- **Components**: C7 Config, C8 Workspace Provisioner, C1 ACP Client
- **Dependencies**: None (first unit)
- **Delivers**: CLI script that loads config, provisions `~/.kiro/`, spawns kiro-cli, streams response to stdout
- **Project Type**: Greenfield monolith — `src/tg_acp/` package
- **Workspace Root**: /Users/umaxfun/prj/temp/tg-acp

## Stories Covered

- FR-02 (ACP Client): initialize, session/new, session/prompt, streaming, turn_end detection
- FR-11 (Custom Agent Support): global agent provisioning, kiro-config/ template
- FR-13 (Skills Support): kiro-config/ template with steering/skills directories
- NFR-01 (Configuration): .env loading, all 7 config keys
- NFR-02 (Startup Validation): fail-fast on missing kiro-cli, agent name, template
- NFR-04 (Tech Stack): Python 3.12, uv, asyncio subprocess
- NFR-05 (Logging): stderr capture, structured logging

## Plan Steps

### Step 1: Project Structure Setup
- [x] Update `pyproject.toml` — add dependencies: `python-dotenv`
- [x] Create `src/tg_acp/__init__.py`
- [x] Update `.gitignore` — add: `.env`, `workspaces/`, `tg-acp.db`, `__pycache__/`, `.venv/`

### Step 2: Config Module (C7)
- [x] Create `src/tg_acp/config.py`
  - `Config` dataclass (frozen) with 7 fields per domain-entities.md
  - `Config.load()` — load .env, read env vars, validate, return frozen instance
  - `Config.validate_kiro_cli()` — check PATH, template dir, agent JSON, workspace writable
  - Business rules BR-01 (validation) and BR-02 (prerequisites)

### Step 3: Provisioner Module (C8)
- [x] Create `src/tg_acp/provisioner.py`
  - `WorkspaceProvisioner.__init__(config)` — store config reference
  - `WorkspaceProvisioner.provision()` — prefix-based sync per business-logic-model.md
  - `WorkspaceProvisioner._sync_prefix(src_dir, dst_dir, prefix)` — delete + copy
  - Safety guardrails: BR-03 (name validation, file count limit, template check)
  - Sync behavior: BR-04 (every startup, delete-then-copy, 3 dirs)

### Step 4: ACP Client Module (C1)
- [x] Create `src/tg_acp/acp_client.py`
  - `ACPClientState` enum: IDLE, INITIALIZING, READY, BUSY, DEAD
  - `ACPClient` class with protocol state machine per business-logic-model.md
  - `spawn(agent_name)` — create subprocess, start readers
  - `initialize()` — JSON-RPC initialize handshake
  - `session_new(cwd)` — create session, return session_id
  - `session_load(session_id)` — load existing session
  - `session_prompt(session_id, content)` — async iterator yielding updates
  - `session_cancel(session_id)` — notification (no response)
  - `session_set_model(session_id, model)` — set model
  - `is_alive()`, `kill()` — lifecycle management
  - Stdout reader (JSON line parsing, request/notification routing)
  - Stderr reader (log at configured level)
  - Business rules BR-05 (protocol), BR-06 (state), BR-07 (streaming), BR-08 (stderr)

### Step 5: Agent Config Template
- [x] Create `kiro-config/agents/tg-acp.json` — agent config with:
  - name, description, model: "auto"
  - prompt: inline steering for `<send_file path="...">description</send_file>` tag
  - tools/allowedTools: all tools allowed
- [x] Create `kiro-config/steering/` directory (empty placeholder for future steering files)
- [x] Create `kiro-config/skills/` directory (empty placeholder for future skill files)

### Step 6: Environment Config Template
- [x] Create `.env.example` with all 7 config keys documented

### Step 7: Main Entry Point (Unit 1 CLI)
- [x] Create `main.py` — throwaway CLI entry point per business-logic-model.md:
  1. Config.load() + validate_kiro_cli()
  2. WorkspaceProvisioner.provision()
  3. Create test workspace dir
  4. Spawn ACPClient, initialize, session_new
  5. Read user input (or hardcoded test message)
  6. Stream response to stdout
  7. Clean exit

### Step 8: Tests
- [x] Create `tests/__init__.py`
- [x] Create `tests/test_config.py` — unit tests for Config (BR-01):
  - test_config_load_valid
  - test_config_missing_required
  - test_config_invalid_agent_name
  - test_config_agent_name_too_short
- [x] Create `tests/test_provisioner.py` — unit tests for Provisioner (BR-03, BR-04):
  - test_provisioner_safety_limit
  - test_provisioner_empty_template
  - test_provisioner_sync
  - test_provisioner_no_collateral
- [x] Create `tests/test_acp_protocol.py` — integration tests (real kiro-cli):
  - test_acp_full_flow
  - test_acp_session_new_returns_id
  - test_acp_streaming_chunks
  - test_acp_process_kill
  - test_acp_dead_detection

### Step 9: Install Dependencies and Verify
- [x] Run `uv sync` to install dependencies
- [x] Run `uv run python -c "from tg_acp.config import Config"` to verify imports
- [x] Run `uv run pytest tests/test_config.py -v` to verify config tests pass
- [x] Run `uv run pytest tests/test_provisioner.py -v` to verify provisioner tests pass
- [x] Run `uv run pytest tests/test_acp_protocol.py -v` to verify ACP integration tests pass (real kiro-cli)

### Step 10: Code Summary Documentation
- [x] Create `aidlc-docs/construction/unit1-foundation/code/code-summary.md`
  - List all created files with descriptions
  - Note dependencies added
  - Document how to run Unit 1

## Total: 10 Steps

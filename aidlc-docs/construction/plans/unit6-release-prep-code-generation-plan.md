# Unit 6: Release Prep — Code Generation Plan

## Unit Context
- **FR-14**: Telegram ID Allowlist (allowlist gate, rejection messages, fail-closed)
- **FR-15**: README Documentation (deployment guide)
- **Components modified**: C7 Config (extended), C6 Bot Handlers (extended)
- **New files**: README.md
- **Project type**: Greenfield (brownfield modifications to existing src/)

## Steps

### Step 1: Extend C7 Config — allowlist field + parsing + method
- [x] Add `allowed_telegram_ids: frozenset[int]` field to `Config` dataclass
- [x] Parse `ALLOWED_TELEGRAM_IDS` env var in `Config.load()`: split by comma, strip, convert to int, wrap in `frozenset`; empty/unset → `frozenset()`
- [x] Add `is_user_allowed(user_id: int) -> bool` method
- [x] Log warning at startup if allowlist is empty
- [x] File: `src/tg_acp/config.py` (modify in-place)

### Step 2: Add allowlist gate to C6 Bot Handlers
- [x] Add `_send_access_denied()` helper function
- [x] Insert allowlist check in `handle_message` — immediately after null guards, before `has_file`/`has_text`
- [x] Insert allowlist check in `cmd_start` — restricted variant for denied users
- [x] Insert allowlist check in `cmd_model` — rejection for denied users
- [x] File: `src/tg_acp/bot_handlers.py` (modify in-place)

### Step 3: Add tests for allowlist behavior
- [x] Config tests: comma-separated parsing, whitespace handling, empty string, non-integer ValueError, `is_user_allowed` true/false
- [x] Bot handler tests: denied user text → rejection + no ACP, denied user /start → restricted welcome, denied user /model → rejection, denied user file → rejection + no download, allowed user → normal flow unchanged
- [x] Files: `tests/test_config.py` (modify), `tests/test_bot_handlers.py` (modify)

### Step 4: Update .env.example + Create README.md
- [x] Add `ALLOWED_TELEGRAM_IDS` to `.env.example` with comment
- [x] Create `README.md` per BR-05 (project description, prerequisites, installation, configuration, agent setup, running, commands, architecture)
- [x] Files: `.env.example` (modify), `README.md` (create)

### Step 5: Code summary
- [x] Create `aidlc-docs/construction/unit6-release-prep/code/code-summary.md`

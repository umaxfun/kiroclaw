# Unit 6: Release Prep — Code Summary

## Changes

### Modified: `src/tg_acp/config.py`
- Added `allowed_telegram_ids: frozenset[int]` field
- Added `ALLOWED_TELEGRAM_IDS` env var parsing (comma-separated ints, fail-closed on empty)
- Added `is_user_allowed(user_id: int) -> bool` method
- Startup warning when allowlist is empty

### Modified: `src/tg_acp/bot_handlers.py`
- Added `_send_access_denied()` helper — standardized rejection message with user's Telegram ID
- `handle_message`: allowlist check immediately after null guards, before file/text processing
- `cmd_start`: restricted welcome variant for denied users (shows Telegram ID)
- `cmd_model`: allowlist check at top, rejection for denied users

### Modified: `.env.example`
- Added `ALLOWED_TELEGRAM_IDS` with description

### Created: `README.md`
- Project description, prerequisites, installation, configuration table, agent setup, running instructions, bot commands, architecture diagram

## Test Results
- 98/98 unit tests pass (8 new config tests + 7 new bot handler tests + 83 existing)
- Pre-existing integration test failures (pool_integration, provisioner) unchanged


## FR-16: Stale Session Lock Recovery

### Modified: `src/tg_acp/acp_client.py`
- `_send_request` now includes `data` field from JSON-RPC error in the RuntimeError message
- `session_load` logs params at DEBUG level

### Modified: `src/tg_acp/session_store.py`
- Added `delete_session(user_id, thread_id)` method

### Modified: `src/tg_acp/bot_handlers.py`
- Added `_try_recover_stale_session(error_msg)` helper — regex extracts PID, `os.kill(pid, 0)` checks liveness
- `handle_message_internal` session/load error path: if stale PID detected → delete session, create new, continue; if live PID → existing error behavior

### Modified: `src/tg_acp/process_pool.py`
- `acquire()` step 1b now calls `_cancel_inflight(thread_id)` when affinity slot is busy (fixes duplicate response bug)

### Test Results
- 74/74 unit tests pass (4 new stale lock tests + 2 new delete_session tests + 1 updated cancel race test)

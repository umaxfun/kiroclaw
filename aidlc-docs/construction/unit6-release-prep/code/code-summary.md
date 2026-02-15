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

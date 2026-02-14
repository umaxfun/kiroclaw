# Code Summary — Unit 4: File Handling + Commands

## Files Created

### `src/tg_acp/file_handler.py` — C5 File Handler
- `FileHandler.download_to_workspace(message, workspace_path)` — detects attachment type (document, photo, audio, voice, video, video_note, sticker), extracts file_id + filename, downloads via `bot.download()`, returns absolute path
- `FileHandler.send_file(bot, chat_id, thread_id, file_path, caption)` — sends file via `bot.send_document()` with `FSInputFile`
- `FileHandler.validate_path(file_path, workspace_path)` — resolves paths, checks `is_relative_to()` to prevent path traversal

## Files Modified

### `src/tg_acp/stream_writer.py` — C4 Stream Writer
- Added `_SEND_FILE_RE` regex at module level for `<send_file>` tag parsing
- `finalize()` return type changed from `list[str]` to `list[tuple[str, str]]`
- `finalize()` now parses and strips `<send_file>` tags BEFORE Markdown→HTML conversion
- If stripped buffer is empty (response was only file tags), skips sendMessage entirely

### `src/tg_acp/bot_handlers.py` — C6 Bot Handlers
- Added `AVAILABLE_MODELS` list and `DEFAULT_MODEL` constant
- Added `cmd_model()` handler with `@router.message(Command("model"))` — placed between cmd_start and handle_message for correct registration order
- `/model` (no args): displays model list with ✓ marker for current selection
- `/model <name>`: validates, stores in SQLite, acquires client_lock, calls session_load + session_set_model
- `handle_message()` extended: accepts file attachments (not just text), downloads via FileHandler before prompting, builds mixed prompt content, processes outbound files from finalize(), implements missing file retry (max once per turn)

### `tests/test_bot_handlers.py` — Test Compatibility
- `_make_message()` helper: added explicit `None` for all file attachment attributes and `caption` (MagicMock auto-creates truthy attributes)
- All `mock_writer.finalize` mocks: added `return_value=[]` (Unit 4 iterates over finalize result)

## Requirements Coverage
- FR-08: File handling (inbound download, outbound `<send_file>` tags, path validation, missing file retry)
- FR-09: /model command (list models, set model, persist + session_set_model)

## Test Results
- 77 tests passed, 0 failures
- 5 pre-existing warnings (AsyncMock is_alive coroutine — Unit 3 test artifact)

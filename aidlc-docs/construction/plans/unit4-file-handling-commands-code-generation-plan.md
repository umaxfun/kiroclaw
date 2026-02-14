# Code Generation Plan — Unit 4: File Handling + Commands

## Unit Context
- **Unit**: 4 — File Handling + Commands
- **Components**: C5 File Handler (new), C4 Stream Writer (modify), C6 Bot Handlers (modify)
- **Requirements**: FR-08 (file handling, `<send_file>` tags), FR-09 (/model command)
- **Dependencies**: C1 ACP Client (Unit 1), C3 Session Store (Unit 2), C4 Stream Writer (Unit 3), C6 Bot Handlers (Unit 3)
- **Functional Design**: `aidlc-docs/construction/unit4-file-handling-commands/functional-design/`

## Files to Create
- `src/tg_acp/file_handler.py` — C5 File Handler (download_to_workspace, send_file, validate_path)

## Files to Modify
- `src/tg_acp/stream_writer.py` — C4: parse/strip `<send_file>` tags in finalize(), change return type to `list[tuple[str, str]]`
- `src/tg_acp/bot_handlers.py` — C6: add /model command (registered BEFORE catch-all via decorator order), extend handle_message for file attachments + outbound file processing + missing file retry

## Steps

- [x] Step 1: Create `src/tg_acp/file_handler.py` — C5 FileHandler class with three static/class methods:
  - `download_to_workspace(message, workspace_path)` — detect attachment type, extract file_id + filename, download via `bot.download()`, return absolute path
  - `send_file(bot, chat_id, thread_id, file_path, caption)` — send via `bot.send_document()` with FSInputFile
  - `validate_path(file_path, workspace_path)` — resolve paths, check `is_relative_to()`

- [x] Step 2: Modify `src/tg_acp/stream_writer.py` — Update `finalize()`:
  - Add `<send_file>` tag regex: `<send_file\s+path="([^"]+)">(.*?)</send_file>` with `re.DOTALL` flag (BR-15 #2)
  - Parse and strip tags from buffer BEFORE Markdown→HTML conversion, collect `(path, description)` tuples
  - If stripped buffer (after `.strip()`) is empty, skip sendMessage entirely (BR-15 #4)
  - Change return type from `list[str]` to `list[tuple[str, str]]`

- [x] Step 3: Modify `src/tg_acp/bot_handlers.py` — Add /model command handler:
  - Define `AVAILABLE_MODELS` list and `DEFAULT_MODEL`
  - `cmd_model(message)` — guard for from_user + thread_id; no args: display list with ✓ marker; with args: validate against AVAILABLE_MODELS (case-insensitive), store in SQLite, then acquire client_lock → respawn if dead → session_load → session_set_model (try/except: log warning on failure, BR-16 #7); if no session exists, skip ACP call (model applies on next session creation)
  - Decorate with `@router.message(Command("model"))` — placed BETWEEN cmd_start and handle_message (aiogram processes in source order, BR-17 #7)
  - Import `Command` from `aiogram.filters`

- [x] Step 4: Modify `src/tg_acp/bot_handlers.py` — Extend `handle_message()`:
  - Change guard: accept messages with text OR file attachment (not just text)
  - Detect file attachments (document, photo, audio, voice, video, video_note, sticker)
  - Download via `FileHandler.download_to_workspace()` before session/prompt; on failure: log error, send error message to user, return (BR-14 #9)
  - Build mixed prompt content (file reference + text/caption per BR-14 #6–#8)
  - Process outbound files from `finalize()` return: validate path, check exists, send via FileHandler
  - Missing file retry: one internal retry prompt with new StreamWriter (max once per turn, BR-15 #7–#8)

- [x] Step 5: Fix existing tests for Unit 4 compatibility:
  - Update `_make_message` helper in `tests/test_bot_handlers.py` to set file attachment attributes (document, photo, audio, voice, video, video_note, sticker, caption) to `None` by default (MagicMock auto-creates truthy attributes, breaking the new `has_file` guard)
  - Update `test_no_text_returns_early` — message with `text=None` and no file attachments should still return early
  - Update all tests that mock `StreamWriter.finalize` — set `return_value=[]` explicitly (Unit 4 iterates over the result; default `AsyncMock()` return is not iterable as `list[tuple]`)
  - Verify all existing tests pass with the new guard and finalize return type

- [x] Step 6: Run all tests, verify no regressions: `uv run pytest`

- [x] Step 7: Create `aidlc-docs/construction/unit4-file-handling-commands/code/code-summary.md`

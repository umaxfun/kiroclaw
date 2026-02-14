# Code Summary — Unit 3: Telegram Bot with Streaming

## Files Created
- `src/tg_acp/stream_writer.py` — C4 StreamWriter: chunk accumulation, sliding window (4000 chars), 100ms throttle, message split (4096 char segments with newline-preferred breaks), cancel support, draft error swallowing
- `src/tg_acp/bot_handlers.py` — C6 Bot Handlers: /start command, text message handler with session lookup/create, session/load fallback on failure, asyncio.Lock for sequential access, crash recovery (respawn dead client)
- `tests/test_stream_writer.py` — 13 unit tests (sliding window, message split, cancel, empty buffer, draft error)

## Files Modified
- `main.py` — Rewritten from Unit 2 CLI demo to aiogram bot entry point with async main, shutdown hook (kills ACP Client + closes SessionStore), dp.start_polling
- `src/tg_acp/acp_client.py` — Added dual notification queue drain: end of `session_load()` + start of `session_prompt()` to prevent history replay pollution
- `pyproject.toml` — Added aiogram dependency
- `tests/test_config.py` — Fixed dotenv leak in tests (patched load_dotenv to prevent .env file from overriding cleared environment)

## Test Results
- 44/44 tests passing (13 new StreamWriter + 31 existing)

## Key Design Decisions
- Single ACP Client with asyncio.Lock — sequential processing, acceptable for Unit 3
- sendMessageDraft errors swallowed — draft is cosmetic, final sendMessage matters
- session/load failure falls back to session/new — prevents stuck threads
- Graceful shutdown via dp.shutdown hook — prevents orphaned kiro-cli processes

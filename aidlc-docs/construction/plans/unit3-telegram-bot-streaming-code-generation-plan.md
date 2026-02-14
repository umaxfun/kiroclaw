# Code Generation Plan — Unit 3: Telegram Bot with Streaming

## Unit Context
- **Unit**: 3 — Telegram Bot with Streaming
- **Components**: C4 Stream Writer, C6 Bot Handlers (partial)
- **Requirements**: FR-01 (bot core), FR-03 (streaming), FR-09 (/start only)
- **Dependencies**: C1 ACP Client (Unit 1), C3 Session Store (Unit 2), C7 Config (Unit 1), C8 Provisioner (Unit 1)
- **Functional Design**: `aidlc-docs/construction/unit3-telegram-bot-streaming/functional-design/`

## Files to Create
- `src/tg_acp/stream_writer.py` — C4 Stream Writer
- `src/tg_acp/bot_handlers.py` — C6 Bot Handlers (partial)
- `tests/test_stream_writer.py` — Unit tests for C4

## Files to Modify
- `main.py` — Rewrite from CLI demo to aiogram bot entry point
- `pyproject.toml` — Add aiogram dependency

## Steps

- [x] Step 1: Add aiogram dependency to pyproject.toml, run `uv sync`
- [x] Step 2: Create `src/tg_acp/stream_writer.py` — C4 StreamWriter class (write_chunk with throttle + sliding window, finalize with message split, cancel)
- [x] Step 3: Create `tests/test_stream_writer.py` — Unit tests for sliding window, message split, cancel, empty buffer, draft error swallowing (10 tests, mocked bot)
- [x] Step 4: Create `src/tg_acp/bot_handlers.py` — C6 Bot Handlers: /start command, text message handler with session lookup/create, session/load fallback, asyncio.Lock, error handling
- [x] Step 5: Rewrite `main.py` — aiogram bot entry point: async main, spawn ACP Client, register handlers, shutdown hook, dp.start_polling
- [x] Step 6: Run all tests (existing + new), verify no regressions
- [x] Step 7: Create `aidlc-docs/construction/unit3-telegram-bot-streaming/code/code-summary.md`

# Code Summary — Unit 5: Process Pool + Cancel

## Files Created

### `src/tg_acp/process_pool.py` — C2 Process Pool
- `SlotStatus` enum (IDLE, BUSY)
- `ProcessSlot` dataclass — slot_id, client (ACPClient | None for placeholder), status, last_used, session_id, thread_id
- `QueuedRequest` dataclass — thread_id, user_id, message_text, files, chat_id, message_thread_id
- `InFlightRequest` dataclass — thread_id, slot_id, cancel_event
- `RequestQueue` — enqueue with per-thread dedup (replace, keep FIFO position), dequeue oldest
- `InFlightTracker` — track (returns cancel_event), cancel (sets event), untrack
- `ProcessPool`:
  - `__init__(config)` — stores agent_name, log_level, max_processes, idle_timeout; creates empty slots, queue, in_flight tracker, lock
  - `initialize()` — spawns first warm process (fail-fast), starts reaper task
  - `acquire(thread_id, user_id)` — affinity routing → any IDLE → spawn outside lock with placeholder → return None if at max
  - `release(slot, session_id, thread_id)` — crash detection via is_alive(), mark IDLE, update affinity, untrack in-flight; guards against slot already removed (shutdown race)
  - `_reaper_loop()` — background task, checks every idle_timeout/2, kills idle processes (never last)
  - `shutdown()` — cancels reaper, kills all slots, clears list

## Files Modified

### `src/tg_acp/bot_handlers.py` — C6 Bot Handlers
- `BotContext.__init__` now takes `pool: ProcessPool` and `bot: Bot` instead of `client: ACPClient` (removed `client_lock`)
- `handle_message()` refactored into thin wrapper: extracts fields, computes workspace_path, downloads files, delegates to `handle_message_internal()`
- `handle_message_internal()` — core logic: acquire slot, track in-flight, session lookup/create, stream with cancel detection, outbound file processing + missing file retry, release in finally block, dequeue next request via create_task
- `/model` handler updated: uses pool.acquire()/release() instead of client_lock; if pool busy, stores in SQLite only
- `_handle_queued_request()` — re-derives workspace_path, calls handle_message_internal
- Error messages use `bot.send_message()` instead of `message.answer()` (handle_message_internal doesn't have Message object)
- Imports: ACPClient removed (only TURN_END kept), added ProcessPool and QueuedRequest

### `main.py` — Entry Point
- Replaced `ACPClient.spawn()` + `initialize()` with `ProcessPool(config)` + `pool.initialize()`
- Bot instance created before BotContext (BotContext now needs bot reference)
- `BotContext(config, store, pool, bot)` — no more client or client_lock
- `on_shutdown()` calls `pool.shutdown()` instead of `client.kill()`
- Removed ACPClient import

### `tests/test_bot_handlers.py` — Test Updates
- `_make_ctx()` returns `(ctx, pool, slot, client)` tuple with mock pool
- `_make_mock_pool()` creates mock with acquire/release/in_flight/request_queue
- `_make_mock_client()` creates mock ACPClient with standard methods
- `TestClientRespawn` removed (pool handles crash detection internally)
- `TestPoolBusy` added — verifies enqueue when acquire returns None
- All tests updated for pool-based flow (acquire/release instead of client_lock)
- Guard tests verify `pool.acquire` not called (instead of `client.session_prompt`)
- Error tests verify `bot.send_message` called (instead of `message.answer`)

## Requirements Coverage
- FR-04: Process pool (scale-to-one, idle timeout, queue with dedup, spawn outside lock)
- FR-07: Cancel-in-flight (cancel_event per request, silent cancel, session/cancel)
- FR-10: Error recovery (crash detection, slot removal, error to user, bot keeps running)

## Test Results
- 77 tests passed, 0 failures

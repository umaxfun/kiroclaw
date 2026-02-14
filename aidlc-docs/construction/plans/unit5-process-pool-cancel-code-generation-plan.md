# Code Generation Plan — Unit 5: Process Pool + Cancel

## Unit Context
- **Unit**: 5 — Process Pool + Cancel-in-Flight
- **Components**: C2 Process Pool (new), C6 Bot Handlers (modify), main.py (modify)
- **Requirements**: FR-04 (process pool), FR-07 (cancel-in-flight), FR-10 (error recovery)
- **Dependencies**: C1 ACP Client (Unit 1), C3 Session Store (Unit 2), C4 Stream Writer (Unit 3), C5 File Handler (Unit 4), C6 Bot Handlers (Unit 4)
- **Functional Design**: `aidlc-docs/construction/unit5-process-pool-cancel/functional-design/`

## Files to Create
- `src/tg_acp/process_pool.py` — C2: ProcessPool, ProcessSlot, RequestQueue, InFlightTracker, QueuedRequest, SlotStatus

## Files to Modify
- `src/tg_acp/bot_handlers.py` — C6: Replace single client + client_lock with ProcessPool; refactor handle_message into thin wrapper + handle_message_internal; add cancel detection during streaming; add queue enqueue/dequeue logic; update /model to use pool acquire/release; update BotContext
- `main.py` — Replace single ACPClient spawn with ProcessPool(config) + pool.initialize(); update BotContext construction; update shutdown hook to call pool.shutdown()

## Steps

- [ ] Step 1: Create `src/tg_acp/process_pool.py` — All pool-related classes:
  - `SlotStatus` enum (IDLE, BUSY)
  - `ProcessSlot` dataclass (slot_id, client, status, last_used, session_id, thread_id)
  - `QueuedRequest` dataclass (thread_id, user_id, message_text, files, chat_id, message_thread_id)
  - `InFlightRequest` dataclass (thread_id, slot_id, cancel_event)
  - `RequestQueue` class (enqueue with per-thread dedup, dequeue FIFO)
  - `InFlightTracker` class (track, cancel, untrack)
  - `ProcessPool` class:
    - `__init__(config)` — store config values, create empty slots list, queue, in_flight tracker, lock
    - `initialize()` — spawn first warm process (fail-fast), start reaper task
    - `acquire(thread_id, user_id)` — affinity routing, spawn-outside-lock with placeholder, return None if at max
    - `release(slot, session_id, thread_id)` — crash detection, mark IDLE, update affinity, untrack in-flight; guard against slot already removed from list (e.g., after shutdown — use `if slot in self.slots` before remove)
    - `_reaper_loop()` — background task, check every idle_timeout/2, kill idle processes (never last)
    - `shutdown()` — cancel reaper, kill all slots, clear list

- [ ] Step 2: Modify `src/tg_acp/bot_handlers.py` — Update BotContext and refactor handlers:
  - Change `BotContext.__init__` to take `pool: ProcessPool` instead of `client: ACPClient`; remove `client_lock`; add `bot: Bot` reference (needed by handle_message_internal and handle_queued_request for StreamWriter and error messages)
  - Refactor `handle_message()` into thin wrapper + `handle_message_internal()`:
    - Thin wrapper: extract fields from Message, compute workspace_path via create_workspace_dir (deterministic from config.workspace_base_path + user_id + thread_id, idempotent), download files to workspace if any, then call internal
    - `handle_message_internal(user_id, thread_id, message_text, file_paths, chat_id, message_thread_id, workspace_path)`: core logic
  - `handle_message_internal()`: acquire slot from pool, handle None (enqueue + return), track in-flight, session_load or session_new via slot.client, cancel detection during streaming, outbound file processing + missing file retry (same as Unit 4 but using slot.client), release in finally block (initialize session_id=None before try to avoid NameError), dequeue next request via create_task
  - Note: workspace_path is deterministic from (config.workspace_base_path, user_id, thread_id) — no session lookup needed. Both thin wrapper and handle_queued_request compute it the same way via create_workspace_dir().
  - Error messages: use `bot.send_message(chat_id, ..., message_thread_id=...)` instead of `message.answer()` (since handle_message_internal doesn't have the Message object)
  - Update `/model` handler: replace client_lock + ctx.client with pool.acquire()/release(); if acquire returns None, store in SQLite only
  - Add `handle_queued_request()` function that calls `handle_message_internal()`
  - Update imports: add ProcessPool and QueuedRequest from process_pool; change `from tg_acp.acp_client import ACPClient, TURN_END` to just `TURN_END` (ACPClient no longer used directly); keep asyncio import (needed for create_task in queue processing)

- [ ] Step 3: Modify `main.py` — Update entry point for pool:
  - Replace `ACPClient.spawn()` + `initialize()` with `ProcessPool(config)` + `pool.initialize()`
  - Create Bot instance before BotContext (BotContext now needs bot reference)
  - Update `BotContext(config, store, pool, bot)` — no more client or client_lock
  - Update `on_shutdown()` to call `pool.shutdown()` instead of `client.kill()`
  - Remove direct ACPClient import (pool handles it internally)

- [ ] Step 4: Update existing tests for Unit 5 compatibility:
  - Update all test files that construct `BotContext` — change from `client=...` to `pool=...`
  - Update mocks: replace `mock_client` + `mock_client_lock` with `mock_pool` that has `acquire()` returning a mock slot (with `.client` attribute), `release()`, `in_flight.track()` returning an `asyncio.Event()`, `in_flight.untrack()`, `request_queue.dequeue()` returning None
  - The mock slot's `.client` attribute should have the same mock methods as the old `mock_client` (session_new, session_load, session_prompt, session_cancel, is_alive, etc.)
  - Rework `TestClientRespawn` — pool handles crash detection internally now; test should verify that if slot.client.is_alive() returns False during release, the slot is removed from pool (this becomes a pool-level test, not a bot_handlers test)
  - Update `handle_message` test expectations for the new refactored flow (acquire/release pattern instead of client_lock)
  - Update error/guard tests to verify pool.acquire is not called (instead of client.session_prompt)
  - Verify all existing tests pass with the new BotContext signature

- [ ] Step 5: Run all tests, verify no regressions: `uv run pytest`

- [ ] Step 6: Create `aidlc-docs/construction/unit5-process-pool-cancel/code/code-summary.md`

# Business Rules — Unit 5: Process Pool + Cancel

## BR-18: Process Pool Management

### Rule 1: Warm Process Guarantee
- At least 1 process MUST exist after pool initialization
- The last process MUST NEVER be killed by the reaper
- If the last process crashes, spawn a replacement immediately

### Rule 2: Max Processes Limit
- Pool MUST NOT spawn more than MAX_PROCESSES processes
- If all processes are busy and pool is at max → enqueue request
- MAX_PROCESSES is configured via .env (default: 5)

### Rule 3: Idle Timeout
- Processes idle for longer than IDLE_TIMEOUT_SECONDS MUST be killed
- Idle time is measured from last release() call
- Reaper checks every `IDLE_TIMEOUT_SECONDS / 2` seconds
- IDLE_TIMEOUT_SECONDS is configured via .env (default: 30)

### Rule 4: Process Spawn
- Spawn MUST be asynchronous (await client.spawn())
- Spawn failure during initialize() MUST crash the bot (fail-fast)
- Spawn failure during acquire() MUST log error and return None to caller
- Caller MUST handle None by returning error message to user

### Rule 5: Process Crash Detection
- Crash MUST be detected via `not client.is_alive()` (checks if process.returncode is None)
- Crashed processes MUST be removed from pool immediately
- Crash during request MUST return error to user: "Failed to process your request. Please try again later."
- Crash MUST NOT crash the bot

### Rule 6: Session Affinity
- Pool MUST track which session is loaded in which slot (session_id, thread_id)
- acquire() MUST prefer IDLE slots with matching thread_id
- If no affinity match, acquire() MAY use any IDLE slot
- Affinity is best-effort — if preferred slot is BUSY, use another slot

### Rule 7: Slot Status Transitions
- Valid transitions: IDLE → BUSY → IDLE
- Crashed processes MUST be removed from pool immediately (no CRASHED state)
- Only IDLE or BUSY slots MAY exist in the pool
- BUSY placeholder slots (client=None) MAY exist temporarily during spawn

---

## BR-19: Request Queue

### Rule 1: Queue Capacity
- Queue MUST be unbounded (no size limit)
- Queue MUST accept requests even if pool is at max capacity

### Rule 2: Per-Thread Dedup
- Queue MUST store at most 1 request per thread_id
- If thread_id already in queue, new request MUST replace old request
- Replacement MUST preserve FIFO order (don't move to end)

### Rule 3: FIFO Order
- Dequeue MUST return the oldest request (first in, first out)
- Dedup replacement MUST NOT change order

### Rule 4: Queue Processing
- After releasing a slot, the handler's finally block MUST check queue
- If queue is not empty, handler MUST dequeue and process next request via asyncio.create_task()
- Queue processing MUST be asynchronous (don't block the releasing handler)

---

## BR-20: Cancel In-Flight

### Rule 1: Cancel Trigger
- New message in same thread MUST trigger cancel for previous in-flight request
- acquire() MUST signal the existing cancel_event for the thread (if one exists) before acquiring a slot
- After acquire() returns, caller MUST create a new cancel_event via in_flight.track()

### Rule 2: Cancel Detection
- Handler MUST check cancel_event after each streaming chunk
- If cancel_event is set, handler MUST send session/cancel to ACP Client
- Handler MUST call writer.cancel() to abort streaming

### Rule 3: Cancel Notification
- Cancel MUST be silent (no Telegram message to user)
- User sees new response start immediately after cancel

### Rule 4: In-Flight Tracking
- Pool MUST track active requests via InFlightTracker
- track() MUST be called after acquiring slot
- untrack() MUST be called in release()

### Rule 5: Cancel Event Lifecycle
- cancel_event MUST be created per request via in_flight.track() (after acquire)
- acquire() MUST set the OLD cancel_event (from previous in-flight request) when new message arrives for same thread
- The NEW cancel_event (for the current request) MUST be checked by handler during streaming

---

## BR-21: Error Handling

### Rule 1: Spawn Failure (Startup)
- If first process spawn fails during initialize(), bot MUST crash with error message
- Error message MUST include full traceback

### Rule 2: Spawn Failure (Runtime)
- If process spawn fails during acquire(), pool MUST log error and return None
- Caller MUST return error to user: "Failed to process your request. Please try again later."
- Bot MUST continue running (don't crash)

### Rule 3: Process Crash (In-Flight)
- If process crashes during request, handler MUST detect via broken pipe or EOF
- Handler MUST return error to user: "Failed to process your request. Please try again later."
- Pool MUST remove crashed slot from pool

### Rule 4: Process Crash (Idle)
- If process crashes while idle, reaper MUST detect via `not client.is_alive()` and remove slot
- If crashed process was the last one, pool MUST spawn replacement immediately

### Rule 5: Session Lock Contention
- If session/load fails due to lock contention, handler MUST retry with exponential backoff
- After 3 retries, handler MUST return error to user: "Session is locked. Please try again later."

### Rule 6: Queue Overflow
- Queue has no size limit, so overflow is not possible
- If memory pressure is a concern, consider adding a limit in future (not in this unit)

### Rule 7: Graceful Shutdown
- Pool MUST provide a shutdown() method that kills all slots and cancels the reaper task
- Shutdown MUST iterate all slots and call client.kill() on each
- Shutdown MUST cancel the _reaper_task
- main.py on_shutdown hook MUST call pool.shutdown() instead of client.kill()

### Rule 8: /model Command with Pool
- /model handler MUST acquire a slot from the pool to call session/load + session/set_model
- /model handler MUST release the slot after the operation completes
- If acquire() returns None (pool busy), /model MUST store model in SQLite only and skip session/set_model
- Model will be applied on next session/load for that thread

### Rule 9: Spawn Timeout
- ACPClient.spawn() + initialize() during acquire() MUST NOT hold the pool lock
- acquire() MUST release the lock before spawning, add a placeholder slot (status=BUSY) to reserve capacity, then spawn
- If spawn fails, remove the placeholder slot and return None

---

## Test Strategy

### Unit Tests (12 tests)

1. **test_pool_initialization**: Verify first process spawned on initialize()
2. **test_acquire_affinity**: Verify affinity routing prefers matching thread_id
3. **test_acquire_any_idle**: Verify fallback to any IDLE slot when no affinity match
4. **test_acquire_spawn**: Verify new process spawned when all IDLE and below max
5. **test_acquire_enqueue**: Verify request enqueued when all busy and at max
6. **test_release_idle**: Verify slot marked IDLE and last_used updated
7. **test_release_crash**: Verify crashed slot removed from pool
8. **test_queue_dedup**: Verify per-thread dedup replaces old request
9. **test_queue_fifo**: Verify dequeue returns oldest request
10. **test_in_flight_cancel**: Verify cancel_event set when new message arrives
11. **test_shutdown**: Verify all slots killed and reaper cancelled
12. **test_acquire_spawn_outside_lock**: Verify pool is not blocked during spawn (other operations can proceed)

### Integration Tests (8 tests)

1. **test_idle_timeout**: Spawn 3 processes, wait for idle timeout, verify extras killed
2. **test_warm_process_guarantee**: Kill all but one process, verify last one never killed
3. **test_cancel_in_flight**: Send message, send another before first completes, verify first cancelled
4. **test_queue_processing**: Fill pool to max, send 5 messages, verify all processed in order
5. **test_crash_recovery**: Kill kiro-cli mid-stream, verify error returned to user
6. **test_spawn_failure**: Mock spawn failure, verify error returned to user and bot keeps running
7. **test_graceful_shutdown**: Start pool, trigger shutdown, verify all processes killed
8. **test_model_with_pool**: Set model via /model, verify session/set_model called via pool slot

---

## Summary

**Key Business Rules**:
- **BR-18**: Pool management (warm process, max limit, idle timeout, affinity, crash detection, shutdown, /model interaction, spawn outside lock)
- **BR-19**: Queue (unbounded, per-thread dedup, FIFO, async processing via handler's finally block)
- **BR-20**: Cancel in-flight (trigger on new message, silent cancel, event-based detection, separate old/new cancel events)
- **BR-21**: Error handling (fail-fast on startup, graceful on runtime, crash recovery, shutdown)

**Test Coverage**:
- 12 unit tests for pool, queue, in-flight, shutdown, and spawn logic
- 8 integration tests for idle timeout, cancel, queue, crash, spawn failure, shutdown, and /model

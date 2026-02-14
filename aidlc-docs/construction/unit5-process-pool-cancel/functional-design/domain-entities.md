# Domain Entities — Unit 5: Process Pool + Cancel

## ProcessSlot

Represents a single kiro-cli process in the pool.

```python
@dataclass
class ProcessSlot:
    slot_id: int                    # Unique slot identifier (0, 1, 2, ...)
    client: ACPClient | None        # The ACP client instance (None during spawn placeholder)
    status: SlotStatus              # IDLE | BUSY
    last_used: float                # Timestamp of last release (time.time())
    session_id: str | None          # Currently loaded session (for affinity)
    thread_id: int | None           # Thread ID of loaded session (for affinity)
```

**SlotStatus Enum**:
- `IDLE`: Process is spawned and ready, no active request
- `BUSY`: Process is handling a request (or being spawned as a placeholder)

Note: There is no CRASHED status. Crashed processes are detected and removed immediately — they never persist in the pool in a "crashed" state.

**Lifecycle**:
1. Created → status=IDLE, last_used=now, session_id=None
2. Acquired → status=BUSY, session_id set (after session/load or session/new)
3. Released → status=IDLE, last_used=now, session_id retained (for affinity)
4. Crashed → detected via `not client.is_alive()`, removed from pool immediately
5. Idle timeout → killed and removed from pool (if not last process)

---

## ProcessPool

Manages the pool of kiro-cli processes with scale-to-one semantics.

```python
class ProcessPool:
    slots: list[ProcessSlot]        # All process slots (IDLE or BUSY)
    max_processes: int              # MAX_PROCESSES from config
    idle_timeout: float             # IDLE_TIMEOUT_SECONDS from config
    request_queue: RequestQueue     # Queue for requests when all slots busy
    in_flight: InFlightTracker      # Tracks active requests per thread
    _lock: asyncio.Lock             # Protects slots list and queue
    _reaper_task: asyncio.Task      # Background task for idle timeout
```

**Invariants**:
- At least 1 slot exists after startup (warm process)
- len(slots) <= max_processes
- Only IDLE or BUSY slots are in the list (crashed slots are removed immediately)

---

## RequestQueue

Queue for requests when all processes are busy, with per-thread dedup.

```python
class RequestQueue:
    _queue: dict[int, QueuedRequest]  # thread_id -> QueuedRequest (latest only)
    _order: list[int]                 # thread_ids in FIFO order
```

**QueuedRequest**:
```python
@dataclass
class QueuedRequest:
    thread_id: int
    user_id: int
    message_text: str
    files: list[str]                  # Downloaded file paths
    chat_id: int
    message_thread_id: int
```

**Dedup Behavior**:
- `enqueue(request)`: If thread_id exists, replace old request with new one (keep position in _order)
- `dequeue()`: Pop first thread_id from _order, return and remove from _queue

---

## InFlightTracker

Tracks active requests per thread for cancel-in-flight.

```python
class InFlightTracker:
    _active: dict[int, InFlightRequest]  # thread_id -> InFlightRequest
```

**InFlightRequest**:
```python
@dataclass
class InFlightRequest:
    thread_id: int
    slot_id: int                      # Which slot is handling this
    cancel_event: asyncio.Event       # Set when cancel requested
```

**Cancel Flow**:
1. New message arrives for thread_id
2. Check `_active[thread_id]` — if exists, set `cancel_event`
3. Handler detects cancel_event, sends `session/cancel`, aborts streaming
4. Remove from `_active`, proceed with new request

---

## Session Affinity Map

Tracks which session is loaded in which slot (for affinity routing).

**Embedded in ProcessSlot**:
- `session_id: str | None` — the session currently loaded in this slot
- `thread_id: int | None` — the thread_id that owns this session

**Affinity Routing**:
1. Check if any IDLE slot has matching `thread_id` → prefer that slot
2. If no match or preferred slot is BUSY → use any IDLE slot
3. If no IDLE slots → enqueue request

**Session Lock Concern**:
- kiro-cli holds a lock file (`.kiro-session-{session_id}.lock`) while a session is loaded
- Lock is released when process exits OR when `session/new` or `session/load` is called for a different session
- Affinity avoids lock contention by routing same-thread requests to the same process

---

## Idle Timeout Reaper

Background asyncio task that periodically checks for idle processes.

**Reaper Logic**:
```python
async def _reaper_loop():
    while True:
        await asyncio.sleep(idle_timeout / 2)  # Check twice per timeout period
        async with _lock:
            now = time.time()
            for slot in slots:
                if slot.status == IDLE and (now - slot.last_used) > idle_timeout:
                    if len(slots) > 1:  # Never kill last process
                        await slot.client.kill()
                        slots.remove(slot)
```

**Characteristics**:
- Runs in background (started on pool init)
- Checks all slots every `idle_timeout / 2` seconds
- Kills idle processes older than `idle_timeout`
- Always keeps at least 1 process alive (warm process guarantee)

---

## Crash Detection

Processes can crash at any time (kiro-cli bug, OOM, SIGKILL, etc.).

**Detection Points**:
1. **During acquire()**: Check `not client.is_alive()` before returning slot
2. **During release()**: Check `not client.is_alive()` after request completes
3. **During streaming**: Handler detects broken pipe or EOF on stdout (ACPClient raises RuntimeError)

**Recovery**:
- Remove crashed slot from pool immediately (no CRASHED state — just remove)
- If request was in-flight, return error to user via Telegram
- Pool will spawn new process on next acquire() if needed

---

## Spawn Failure Handling

If spawning a new kiro-cli process fails (e.g., kiro-cli not found, config error):

**Behavior**:
- Log error with full traceback
- Return error message to user via Telegram: "Failed to process your request. Please try again later."
- Keep bot running (don't crash)
- Next request will retry spawn

**Rationale** (from Q5 answer):
- If bot started successfully, kiro-cli disappearing mid-run is unlikely
- Most spawn failures are transient (temp file issues, resource limits)
- Crashing the bot would affect all users, not just the one with the failed request

---

## Warm Process Initialization

**When**: On bot startup (main.py entry point)

**Behavior**:
- Block until first process is spawned and ready
- If spawn fails, crash the bot with error message (fail-fast on startup)
- After startup, pool guarantees at least 1 IDLE process exists

**Startup Sequence**:
```python
async def main():
    config = Config.load()
    config.validate_kiro_cli()
    provisioner = WorkspaceProvisioner(config)
    provisioner.provision()
    
    pool = ProcessPool(config)
    await pool.initialize()  # Blocks until first process ready
    
    # ... rest of bot setup
```

---

## Graceful Shutdown

**When**: On SIGINT/SIGTERM (via aiogram dp.shutdown hook)

**Behavior**:
- Kill all process slots (IDLE and BUSY)
- Cancel the reaper task
- Close SessionStore

```python
async def shutdown():
    self._reaper_task.cancel()
    async with self._lock:
        for slot in self.slots:
            await slot.client.kill()
        self.slots.clear()
```

---

## /model Command with Pool

The `/model` command needs a process slot to call `session/load` + `session/set_model`.

**Behavior**:
- Acquire a slot from the pool (same as handle_message)
- If acquire() returns None (pool busy), store model in SQLite only — skip session/set_model
- Model will be applied on next session/load for that thread
- Release slot after operation completes

This replaces the current approach of using `ctx.client` directly with `client_lock`.

---

## Summary

**Key Design Decisions**:
1. **Idle timeout**: Background reaper thread checks every `idle_timeout / 2` seconds
2. **Crash recovery**: Reuse another process from pool, return error to user if no processes available
3. **Queue capacity**: Unbounded (no limit)
4. **Cancel notification**: Silent (no Telegram message)
5. **Spawn failure**: Log + return error to user, keep bot running
6. **Warm process**: Spawned on bot startup (blocking)
7. **Session affinity**: Prefer routing same-thread requests to same process (avoid session lock contention)
8. **No CRASHED state**: Crashed processes are removed immediately, never stored in a transitional state
9. **Spawn outside lock**: acquire() releases the pool lock before spawning to avoid blocking all operations
10. **Graceful shutdown**: pool.shutdown() kills all slots and cancels the reaper task

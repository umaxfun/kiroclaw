# Business Logic Model — Unit 5: Process Pool + Cancel

## ProcessPool Methods

### `__init__(config: Config)`

Initialize the pool with config values.

```python
def __init__(self, config: Config):
    self.agent_name = config.kiro_agent_name
    self.log_level = config.log_level
    self.max_processes = config.max_processes
    self.idle_timeout = config.idle_timeout_seconds
    self.slots = []
    self.request_queue = RequestQueue()
    self.in_flight = InFlightTracker()
    self._lock = asyncio.Lock()
    self._reaper_task = None
```

---

### `async initialize()`

Spawn the first warm process and start the reaper.

**Flow**:
1. Spawn first process (blocking)
2. If spawn fails → raise exception (fail-fast on startup)
3. Create ProcessSlot with slot_id=0, status=IDLE
4. Add to slots list
5. Start reaper task: `self._reaper_task = asyncio.create_task(self._reaper_loop())`

**Pseudocode**:
```python
async def initialize():
    client = await ACPClient.spawn(self.agent_name, self.log_level)
    await client.initialize()
    slot = ProcessSlot(slot_id=0, client=client, status=IDLE, last_used=time.time(), session_id=None, thread_id=None)
    self.slots.append(slot)
    self._reaper_task = asyncio.create_task(self._reaper_loop())
```

---

### `async acquire(thread_id: int, user_id: int) -> ProcessSlot`

Acquire a process slot for a request. Implements affinity routing and queue logic.

**Flow**:
1. Acquire lock
2. Check if request already in-flight for this thread_id → if yes, trigger cancel
3. Check for IDLE slot with affinity (matching thread_id) → if found, mark BUSY and return
4. Check for any IDLE slot → if found, mark BUSY and return
5. Check if pool can grow (len(slots) < max_processes) → spawn new process, add to slots, mark BUSY and return
6. No slots available → enqueue request, release lock, wait for notification
7. When notified → retry from step 1

**Pseudocode**:
```python
async def acquire(thread_id: int, user_id: int) -> ProcessSlot:
    async with self._lock:
        # Cancel in-flight request if exists (sets OLD cancel_event)
        if thread_id in self.in_flight._active:
            old_request = self.in_flight._active[thread_id]
            old_request.cancel_event.set()
            # Handler will detect cancel_event and abort
        
        # Try affinity routing
        # (If affinity slot is BUSY, fall through to any IDLE check)
        for slot in self.slots:
            if slot.status == IDLE and slot.thread_id == thread_id:
                slot.status = BUSY
                return slot
        
        # Try any IDLE slot
        for slot in self.slots:
            if slot.status == IDLE:
                slot.status = BUSY
                return slot
        
        # Try spawning new process — reserve capacity with placeholder
        if len(self.slots) < self.max_processes:
            slot_id = max((s.slot_id for s in self.slots), default=-1) + 1
            placeholder = ProcessSlot(slot_id=slot_id, client=None, status=BUSY, last_used=time.time(), session_id=None, thread_id=None)
            self.slots.append(placeholder)
            # Release lock during spawn to avoid blocking all pool operations
    
    # Outside lock — spawn if we reserved a placeholder
    if placeholder is not None:
        try:
            client = await ACPClient.spawn(self.agent_name, self.log_level)
            await client.initialize()
            placeholder.client = client
            return placeholder
        except Exception as e:
            logger.error(f"Failed to spawn process: {e}")
            async with self._lock:
                self.slots.remove(placeholder)
            return None
    
    # All slots busy and at max capacity → return None
    # Caller will enqueue and return early — request will be retried by queue processor
    return None
```

**Note**: The lock is released before spawning to avoid blocking all pool operations during subprocess creation. A placeholder slot with status=BUSY reserves the capacity so the pool doesn't over-spawn. If spawn fails, the placeholder is removed.

**Note**: When acquire() returns None, the caller enqueues the request and returns early. The request will be retried later by the queue processor (after another slot is released).

---

### `async release(slot: ProcessSlot, session_id: str | None, thread_id: int | None)`

Release a process slot back to the pool.

**Flow**:
1. Acquire lock
2. Check if process crashed (poll() != None) → remove from slots, log error
3. If not crashed → mark IDLE, update last_used, update session_id/thread_id (for affinity)
4. Remove from in_flight tracker
5. Release lock

**Pseudocode**:
```python
async def release(slot: ProcessSlot, session_id: str | None, thread_id: int | None):
    async with self._lock:
        # Check for crash
        if not slot.client.is_alive():
            logger.error(f"Process {slot.slot_id} crashed")
            self.slots.remove(slot)
            return
        
        # Mark idle
        slot.status = IDLE
        slot.last_used = time.time()
        slot.session_id = session_id
        slot.thread_id = thread_id
        
        # Remove from in-flight
        self.in_flight.untrack(thread_id)
```

---

### `async _reaper_loop()`

Background task that kills idle processes.

**Flow**:
1. Sleep for `idle_timeout / 2` seconds
2. Acquire lock
3. For each IDLE slot:
   - If `(now - slot.last_used) > idle_timeout` AND `len(slots) > 1`:
     - Kill process: `slot.client.kill()`
     - Remove from slots
4. Release lock
5. Repeat

**Pseudocode**:
```python
async def _reaper_loop():
    while True:
        await asyncio.sleep(self.idle_timeout / 2)
        async with self._lock:
            now = time.time()
            to_remove = []
            for slot in self.slots:
                if slot.status == IDLE and (now - slot.last_used) > self.idle_timeout:
                    if len(self.slots) > 1:  # Never kill last process
                        await slot.client.kill()
                        to_remove.append(slot)
            for slot in to_remove:
                self.slots.remove(slot)
                logger.info(f"Reaped idle process {slot.slot_id}")
```

---

## RequestQueue Methods

### `enqueue(request: QueuedRequest)`

Add request to queue with per-thread dedup.

**Flow**:
1. If `request.thread_id` already in `_queue`:
   - Replace old request with new one
   - Keep position in `_order` (don't move to end)
2. Else:
   - Add to `_queue[thread_id]`
   - Append `thread_id` to `_order`

**Pseudocode**:
```python
def enqueue(self, request: QueuedRequest):
    if request.thread_id in self._queue:
        # Replace old request (dedup) — _order list is unchanged, preserving FIFO position
        self._queue[request.thread_id] = request
    else:
        # New thread_id
        self._queue[request.thread_id] = request
        self._order.append(request.thread_id)
```

---

### `dequeue() -> QueuedRequest | None`

Remove and return the oldest request.

**Flow**:
1. If `_order` is empty → return None
2. Pop first `thread_id` from `_order`
3. Pop request from `_queue[thread_id]`
4. Return request

**Pseudocode**:
```python
def dequeue(self) -> QueuedRequest | None:
    if not self._order:
        return None
    thread_id = self._order.pop(0)
    request = self._queue.pop(thread_id)
    return request
```

---

## InFlightTracker Methods

### `track(thread_id: int, slot_id: int) -> asyncio.Event`

Start tracking a request. Returns cancel_event.

**Flow**:
1. Create `InFlightRequest` with `cancel_event = asyncio.Event()`
2. Store in `_active[thread_id]`
3. Return `cancel_event`

**Pseudocode**:
```python
def track(self, thread_id: int, slot_id: int) -> asyncio.Event:
    cancel_event = asyncio.Event()
    self._active[thread_id] = InFlightRequest(thread_id=thread_id, slot_id=slot_id, cancel_event=cancel_event)
    return cancel_event
```

---

### `cancel(thread_id: int)`

Trigger cancel for a thread.

**Flow**:
1. If `thread_id` in `_active` → set `cancel_event`
2. Else → no-op

**Pseudocode**:
```python
def cancel(self, thread_id: int):
    if thread_id in self._active:
        self._active[thread_id].cancel_event.set()
```

---

### `untrack(thread_id: int)`

Stop tracking a request (called on release).

**Flow**:
1. If `thread_id` in `_active` → delete
2. Else → no-op

**Pseudocode**:
```python
def untrack(self, thread_id: int):
    if thread_id in self._active:
        del self._active[thread_id]
```

---

## Bot Handlers Extension (C6)

### `async handle_message(message: Message)` — Extended for Pool

**Changes from Unit 4**:
1. Replace direct ACP Client usage with ProcessPool.acquire()/release()
2. Add queue waiting logic when acquire() returns None
3. Add cancel detection during streaming
4. Track in-flight requests
5. Refactor into handle_message (thin wrapper) + handle_message_internal (core logic)
6. BotContext changes: `client: ACPClient` + `client_lock` replaced with `pool: ProcessPool`

**Flow**:
```
1. Extract user_id, thread_id, chat_id, message_thread_id
2. Guard: message.text or message.document/audio/voice → else return
3. Download files (if any) → file_paths
4. Acquire slot: slot = await pool.acquire(thread_id, user_id)
5. If slot is None (all busy, at max capacity):
   a. Enqueue request: pool.request_queue.enqueue(QueuedRequest(...))
   b. Wait for notification (handled by queue processor — see below)
   c. Retry acquire
6. Track in-flight: cancel_event = pool.in_flight.track(thread_id, slot.slot_id)
7. Get or create session (SessionStore)
8. Create workspace dir (if new thread)
9. Load or create session via ACP Client
10. Build prompt (text + file references)
11. Send prompt via ACP Client
12. Stream response:
    a. For each chunk:
       - Check cancel_event.is_set() → if yes, send session/cancel, break
       - Write chunk to StreamWriter
       - Update draft
    b. On turn_end or cancel:
       - Finalize StreamWriter → get messages and file paths
       - Send messages via Telegram
       - Send files via FileHandler
13. Release slot: await pool.release(slot, session_id, thread_id)
14. Check queue: if not empty, dequeue and process next request
```

**Pseudocode** (key changes only):
```python
async def handle_message(message: Message):
    ctx = _get_ctx()  # Get BotContext with pool, store, config
    user_id = message.from_user.id
    thread_id = message.message_thread_id
    
    # ... file download logic (unchanged from Unit 4)
    
    # Acquire slot
    slot = await ctx.pool.acquire(thread_id, user_id)
    if slot is None:
        # All busy — enqueue and return early
        ctx.pool.request_queue.enqueue(QueuedRequest(thread_id, user_id, message.text, file_paths, chat_id, message_thread_id))
        # Queue processor will retry this request after a slot is released
        return
    
    # Track in-flight
    cancel_event = ctx.pool.in_flight.track(thread_id, slot.slot_id)
    
    try:
        # ... session lookup/create (unchanged from Unit 4)
        
        # Stream response with cancel detection
        async for update in slot.client.session_prompt(session_id, prompt):
            if cancel_event.is_set():
                # Cancel requested
                await slot.client.session_cancel(session_id)
                writer.cancel()
                break
            
            update_type = update.get("sessionUpdate", "")
            if update_type == "agent_message_chunk":
                chunk_content = update.get("content", {})
                if chunk_content.get("type") == "text":
                    await writer.write_chunk(chunk_content["text"])
                    # Update draft (throttled by StreamWriter)
            elif update_type == TURN_END:
                messages, file_paths = await writer.finalize()
                # ... send messages and files (unchanged from Unit 4)
                break
    finally:
        # Always release slot
        await ctx.pool.release(slot, session_id, thread_id)
        
        # Process next queued request if any
        next_request = ctx.pool.request_queue.dequeue()
        if next_request:
            asyncio.create_task(handle_queued_request(next_request))
```

---

### `async handle_queued_request(request: QueuedRequest)`

Process a queued request (called after releasing a slot).

**Flow**:
1. Call the internal message processing logic with fields from QueuedRequest
2. This requires refactoring handle_message into two parts:
   - `handle_message(message)`: extracts fields from aiogram Message, calls internal
   - `handle_message_internal(user_id, thread_id, message_text, file_paths, chat_id, message_thread_id)`: the actual processing logic

**Pseudocode**:
```python
async def handle_queued_request(request: QueuedRequest):
    await handle_message_internal(
        user_id=request.user_id,
        thread_id=request.thread_id,
        message_text=request.message_text,
        file_paths=request.files,
        chat_id=request.chat_id,
        message_thread_id=request.message_thread_id,
    )
```

**handle_message refactoring**:
```python
async def handle_message(message: Message):
    """Thin wrapper — extracts fields from aiogram Message, delegates to internal."""
    # ... extract user_id, thread_id, chat_id, message_thread_id
    # ... download files if any → file_paths
    # ... extract text_content
    await handle_message_internal(user_id, thread_id, text_content, file_paths, chat_id, message_thread_id)

async def handle_message_internal(
    user_id: int,
    thread_id: int,
    message_text: str,
    file_paths: list[str],
    chat_id: int,
    message_thread_id: int,
) -> None:
    """Core processing logic — used by both handle_message and handle_queued_request."""
    ctx = _get_ctx()
    
    # Acquire slot
    slot = await ctx.pool.acquire(thread_id, user_id)
    if slot is None:
        ctx.pool.request_queue.enqueue(QueuedRequest(thread_id, user_id, message_text, file_paths, chat_id, message_thread_id))
        return
    
    cancel_event = ctx.pool.in_flight.track(thread_id, slot.slot_id)
    
    try:
        # ... session lookup/create, prompt, stream with cancel detection
        pass
    finally:
        await ctx.pool.release(slot, session_id, thread_id)
        next_request = ctx.pool.request_queue.dequeue()
        if next_request:
            asyncio.create_task(handle_queued_request(next_request))
```

---

## ProcessPool.shutdown()

Graceful shutdown — kill all processes and cancel the reaper.

**Pseudocode**:
```python
async def shutdown():
    if self._reaper_task:
        self._reaper_task.cancel()
    async with self._lock:
        for slot in self.slots:
            await slot.client.kill()
        self.slots.clear()
    logger.info("Process pool shut down")
```

---

## /model Command Handler — Extended for Pool

**Changes from Unit 4**:
- Replace `ctx.client` + `ctx.client_lock` with `ctx.pool.acquire()` / `ctx.pool.release()`
- If pool is busy (acquire returns None), skip session/set_model — model is stored in SQLite and will apply on next session/load

**Pseudocode** (key changes only):
```python
async def cmd_model(message: Message):
    # ... same validation as Unit 4 ...
    
    # Store in SQLite (always)
    ctx.store.set_model(user_id, thread_id, model_name)
    
    # Try to apply immediately via pool
    record = ctx.store.get_session(user_id, thread_id)
    if record is not None:
        slot = await ctx.pool.acquire(thread_id, user_id)
        if slot is not None:
            try:
                await slot.client.session_load(record.session_id, cwd=record.workspace_path)
                await slot.client.session_set_model(record.session_id, model_name)
            except Exception:
                logger.warning("session/set_model failed — model stored in SQLite, will apply on next load")
            finally:
                await ctx.pool.release(slot, record.session_id, thread_id)
    
    await message.answer(f"Model set to {model_name} for this thread.")
```

---

## Entry Point (main.py) — Extended for Pool

**Changes from Unit 4**:
1. Initialize ProcessPool before starting bot
2. Pass pool to bot handlers (replaces single client)
3. BotContext takes `pool: ProcessPool` instead of `client: ACPClient` (no more client_lock)
4. Shutdown hook calls pool.shutdown() instead of client.kill()

**Startup Sequence**:
```python
async def main():
    # Load config
    config = Config.load()
    config.validate_kiro_cli()
    
    # Provision global agent
    provisioner = WorkspaceProvisioner(config)
    provisioner.provision()
    
    # Initialize process pool (blocks until first process ready)
    pool = ProcessPool(config)
    await pool.initialize()
    
    # Initialize session store
    session_store = SessionStore(db_path="./tg-acp.db")
    
    # Create bot and dispatcher
    bot = Bot(token=config.bot_token)
    dp = Dispatcher()
    
    # Create BotContext with pool (replaces client + client_lock from Unit 4)
    ctx = BotContext(config=config, store=session_store, pool=pool)
    
    # Register handlers (inject context)
    bot_handlers.setup(ctx)
    dp.include_router(bot_handlers.router)
    
    # Register shutdown hook
    async def on_shutdown():
        await pool.shutdown()  # Kills all slots, cancels reaper
        session_store.close()
    
    dp.shutdown.register(on_shutdown)
    
    # Start polling
    await dp.start_polling(bot)
```

---

## Summary

**Key Flows**:
1. **Acquire with affinity**: Check for IDLE slot with matching thread_id → fallback to any IDLE → spawn new (outside lock) → enqueue
2. **Release with affinity update**: Mark IDLE, update session_id/thread_id, untrack in-flight, check for crash
3. **Cancel in-flight**: acquire() sets OLD cancel_event, handler detects and sends session/cancel
4. **Idle timeout**: Background reaper kills processes older than idle_timeout (never kills last)
5. **Queue processing**: After release, dequeue next request and process asynchronously via create_task
6. **Crash recovery**: Detect via is_alive(), remove from pool, return error to user
7. **Spawn failure**: Log error, return error to user, keep bot running
8. **Graceful shutdown**: pool.shutdown() kills all slots, cancels reaper task
9. **/model with pool**: Acquire slot, session/set_model, release; if pool busy, store in SQLite only
10. **handle_message refactoring**: Split into thin wrapper + handle_message_internal for queue reuse

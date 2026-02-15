# Session Affinity Fix — Persistent Process-to-Session Binding

## Problem Statement

### Production Incident: Session Lock Contention (-32603)

kiro-cli holds an exclusive file lock on a session for the lifetime of the
process, even after loading a different session on the same process. When
thread A's session was created on slot 0, and a different thread B later
takes over slot 0, thread A's next request would land on slot 1 (a different
kiro-cli process). Slot 1's `session/load` call fails with `-32603 Internal
error` because slot 0's process still holds the file lock.

### Root Cause

The original `acquire()` tracked affinity via `slot.thread_id` — the thread
currently using a slot. This value was overwritten whenever a different thread
acquired the slot. Once overwritten, the original thread's affinity to that
slot was lost, and subsequent requests would be routed to a different slot
(triggering the lock contention).

### Timeline of Failed Approaches

1. **Kill-and-respawn** (abandoned): Killing the slot holding the lock would
   terminate another user's active request.
2. **Retry with delay** (abandoned): Lock holder streams for 5-10s; retries
   expire before the lock is released.
3. **Hard error, no fallback** (intermediate): `session/load` failure returns
   "Session is temporarily busy" to the user. No data loss, but the user
   can't use their session while another thread occupies their slot.
4. **Persistent session affinity** (final fix): A separate `_session_affinity`
   dict maps `thread_id → slot_id` and persists across slot reassignment.

---

## Solution: `_session_affinity` Dict

### Concept

A `dict[int, int]` on `ProcessPool` mapping `thread_id → slot_id`. Unlike
`slot.thread_id` (which tracks the *current* occupant), `_session_affinity`
tracks which slot *owns* a thread's session file lock. This mapping survives
when another thread takes over the slot.

### Key Distinction

| Field | Tracks | Lifetime | Overwritten when |
|---|---|---|---|
| `slot.thread_id` | Current occupant of the slot | Per-request | Another thread acquires the slot |
| `_session_affinity[tid]` | Which slot holds this thread's session lock | Until slot is reaped/crashed | Slot is removed from pool |

### Acquire Logic (Revised)

```
acquire(thread_id, user_id):
    Step 1 — Check _session_affinity for this thread:
      a. Affinity slot IDLE  → use it (fast path)
      b. Affinity slot BUSY  → return None (enqueue; wait for that slot)
      c. Affinity slot gone  → clear stale entry, fall through

    Step 2 — No affinity (first-time thread or cleared):
      a. Grab any IDLE slot, record affinity
      b. Spawn new slot if under max, record affinity
      c. All busy at max → return None
```

The critical change is step 1b: even if the affinity slot is BUSY serving a
*different* thread, the requesting thread waits. Grabbing a different slot
would cause `-32603`.

### Release & Dequeue Logic (Revised)

`release_and_dequeue` now uses a 3-priority dequeue:

1. **Affinity match**: any queued request whose `_session_affinity` points to
   the releasing slot. This ensures the session stays on the same process.
2. **Same thread**: the thread that just released (continuity).
3. **FIFO fallback**: any queued request.

### Cleanup

Affinity entries are cleaned up when their slot disappears:
- **Crash detection** (`_release_inner`): removes all affinity entries pointing
  to the crashed slot.
- **Reaper** (`_reaper_loop`): removes affinity entries for reaped slots.
- **Spawn failure**: removes the affinity entry if the placeholder slot fails
  to spawn.

---

## Scenario Walkthrough

### Before Fix (Broken)

```
1. Thread A → slot 0 creates session, streams, releases.
   slot0.thread_id = 3001

2. Thread B arrives → acquires slot 0 (IDLE).
   slot0.thread_id = 3002  ← affinity overwritten

3. Thread A arrives → no slot with thread_id=3001.
   Grabs slot 1 (new spawn) → session/load → -32603 (slot 0 holds lock)
```

### After Fix (Working)

```
1. Thread A → slot 0 creates session, streams, releases.
   _session_affinity = {3001: 0}

2. Thread B arrives → no affinity for 3002 → acquires slot 0 (IDLE).
   _session_affinity = {3001: 0, 3002: 0}
   slot0.thread_id = 3002

3. Thread A arrives → _session_affinity[3001] = slot 0 → slot 0 is BUSY.
   Returns None → request enqueued.

4. Thread B finishes → release_and_dequeue on slot 0.
   Priority 1: check affinity — thread 3001 has affinity for slot 0 and is queued.
   Dequeues thread A's request → slot 0 processes it → session/load succeeds.
```

---

## Files Changed

### `src/tg_acp/process_pool.py`

- `ProcessPool.__init__`: added `_session_affinity: dict[int, int] = {}`
- `acquire()`: rewritten to use `_session_affinity` lookup (step 1) before
  falling back to any-IDLE/spawn (step 2). Records affinity on first acquire.
- `release_and_dequeue()`: 3-priority dequeue (affinity → same-thread → FIFO).
  Records affinity for first-time threads dequeued via FIFO.
- `_release_inner()`: cleans up affinity entries on crash detection.
- `_reaper_loop()`: cleans up affinity entries for reaped slots.

### `src/tg_acp/bot_handlers.py`

No changes required — the `handle_message_internal` enqueue path and
`_handle_queued_request` background task already handle the `acquire() → None`
case correctly.

### `tests/test_process_pool.py`

Added `TestSessionAffinity` (7 tests) and `TestDequeueByThread` (3 tests):

- `test_acquire_returns_affinity_idle_slot` — fast path
- `test_acquire_returns_none_when_affinity_slot_busy` — enqueue path
- `test_acquire_returns_none_when_affinity_slot_busy_serving_other_thread` —
  the key scenario (slot reassigned to another thread)
- `test_acquire_grabs_any_idle_for_new_thread` — first-time thread
- `test_acquire_clears_stale_affinity_when_slot_reaped` — cleanup
- `test_release_and_dequeue_prefers_affinity_thread` — priority 1 dequeue
- `test_release_and_dequeue_falls_back_to_fifo` — priority 3 dequeue
- `test_dequeue_by_thread_found` / `not_found` / `preserves_order`

### `tests/test_pool_integration.py`

- `test_lock_contention_must_not_destroy_session`: updated code word from
  "ELEPHANT" (triggered model guardrails) to "MANGO". Added background task
  drain before final assertion. Now passes.

---

## Test Results

- 88 unit tests passed (was 78 before; +10 new affinity/dequeue tests)
- 4 integration tests passed (including the previously-failing lock contention test)
- 4 session continuity tests passed

---

## Business Rule Updates

### BR-18 Rule 6 (Session Affinity) — REVISED

**Before**: Affinity is best-effort — if preferred slot is BUSY, use another slot.

**After**: Affinity is mandatory when `_session_affinity` has an entry for the
thread. If the affinity slot is BUSY, `acquire()` returns None (caller
enqueues). The request will be processed on the correct slot when it becomes
available. This prevents `-32603` lock contention entirely at the cost of
added latency for the enqueued request.

Affinity is cleared only when the slot is removed from the pool (crash or
reaper). After that, the thread is treated as first-time and gets any
available slot.

### BR-21 Rule 5 (Session Lock Contention) — REVISED

**Before**: Retry with exponential backoff, then return error.

**After**: Lock contention is prevented entirely by session affinity. The
`session/load` hard-error path (returns "Session is temporarily busy") remains
as a safety net but should never trigger under normal operation.

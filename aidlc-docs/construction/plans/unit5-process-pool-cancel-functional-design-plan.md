# Functional Design Plan — Unit 5: Process Pool + Cancel

## Unit Context
- **Unit Name**: Unit 5 - Process Pool + Cancel
- **Components**: C2 Process Pool, C6 Bot Handlers (extended)
- **Dependencies**: Units 1-4 complete
- **Scope**: Multi-process pool with scale-to-one semantics, cancel-in-flight, request queue with per-thread dedup

## Plan Steps

- [x] Step 1: Analyze unit context and dependencies
- [x] Step 2: Generate clarifying questions (if needed)
- [x] Step 3: Collect and analyze user answers
- [x] Step 4: Design domain entities (ProcessPool, ProcessSlot, RequestQueue, InFlightTracker)
- [x] Step 5: Design business logic model (pool lifecycle, acquire/release, idle timeout, cancel flow, queue dedup)
- [x] Step 6: Define business rules (BR-18 through BR-21)
- [x] Step 7: Self-check against inception docs and existing code

## Questions

### Question 1: Idle Timeout Behavior
When a process has been idle for IDLE_TIMEOUT_SECONDS, should we:

A) Kill it immediately when timeout expires (timer-based)
B) Check idle time only when a new request arrives (lazy check)
C) Run a background thread that periodically checks all processes
D) Other (please describe after [Answer]: tag below)

[Answer]: I think it could be a background process like internal chrome which handles this.

### Question 2: Process Crash Recovery
When a kiro-cli process crashes mid-stream, should we:

A) Immediately spawn a replacement and retry the failed request
B) Spawn replacement only when next request arrives (lazy)
C) Notify the user via Telegram that their request failed, don't retry
D) Other (please describe after [Answer]: tag below)

[Answer]: If we have another process in the pool, we can just reuse that one.

### Question 3: Queue Capacity
When the queue is full (all processes busy + queue at some limit), should we:

A) No queue limit — queue grows unbounded
B) Fixed limit (e.g., 100 queued requests) — reject new requests when full
C) Per-thread limit (e.g., max 1 queued per thread) — already have dedup, so this is automatic
D) Other (please describe after [Answer]: tag below)

[Answer]: No queue limit.

### Question 4: Cancel Notification to User
When a user's in-flight request is cancelled (new message in same thread), should we:

A) Send a Telegram message: "Previous request cancelled"
B) Silently cancel — user sees new response start immediately
C) Edit the draft to say "Cancelled..." before starting new response
D) Other (please describe after [Answer]: tag below)

[Answer]: Silently cancel, yeah.

### Question 5: Process Spawn Failure
If spawning a new kiro-cli process fails (e.g., kiro-cli not found, config error), should we:

A) Fail fast — crash the bot with error message
B) Log error, return error message to user via Telegram, keep bot running
C) Retry spawn N times with backoff, then fail
D) Other (please describe after [Answer]: tag below)

[Answer]: You can log Aurora and return the message to the user. If the bot started, it's very unlikely to lose kiro-cli in the process. So it may be a recoverable error.

### Question 6: Warm Process Initialization
The "always 1 warm process" rule — when should this process be spawned?

A) On bot startup (main.py entry point) — block until first process is ready
B) Lazy — spawn first process when first message arrives
C) Background — spawn asynchronously on startup, first message may wait
D) Other (please describe after [Answer]: tag below)

[Answer]: On Bot Startup

### Question 7: Session Affinity
Should we try to route requests for the same thread to the same process (session affinity)?

A) Yes — track which process has which session loaded, prefer reusing
B) No — any free process can handle any request (session/load is cheap)
C) Hybrid — prefer affinity but don't block if that process is busy
D) Other (please describe after [Answer]: tag below)

[Answer]: I think that affinity is required because I'm not sure if the process unlocks the session. If it unlocks the session after it ends processing. So, if we can do an affinity cheaply, we should do it.


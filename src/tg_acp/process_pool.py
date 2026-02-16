"""C2: Process Pool — manages kiro-cli ACP process lifecycle with scale-to-one semantics."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tg_acp.config import Config

from tg_acp.acp_client import ACPClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & dataclasses
# ---------------------------------------------------------------------------


class SlotStatus(Enum):
    IDLE = "idle"
    BUSY = "busy"


@dataclass
class ProcessSlot:
    """A single kiro-cli process in the pool."""

    slot_id: int
    client: ACPClient | None  # None during spawn placeholder
    status: SlotStatus
    last_used: float
    user_id: int | None = None  # Track which user owns this slot (for isolation)
    session_id: str | None = None
    thread_id: int | None = None


@dataclass
class QueuedRequest:
    """A request waiting for a free process slot."""

    thread_id: int
    user_id: int
    message_text: str
    files: list[str]
    chat_id: int
    workspace_path: str


@dataclass
class InFlightRequest:
    """Tracks an active request for cancel-in-flight."""

    thread_id: int
    slot_id: int
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)


# ---------------------------------------------------------------------------
# RequestQueue — per-thread dedup, FIFO order
# ---------------------------------------------------------------------------


class RequestQueue:
    """Queue for requests when all processes are busy, with per-thread dedup."""

    def __init__(self) -> None:
        self._queue: dict[int, QueuedRequest] = {}
        self._order: deque[int] = deque()

    def enqueue(self, request: QueuedRequest) -> None:
        """Add request. If thread_id exists, replace (keep FIFO position)."""
        if request.thread_id in self._queue:
            self._queue[request.thread_id] = request
        else:
            self._queue[request.thread_id] = request
            self._order.append(request.thread_id)

    def dequeue(self) -> QueuedRequest | None:
        """Remove and return the oldest request, or None if empty."""
        if not self._order:
            return None
        thread_id = self._order.popleft()
        return self._queue.pop(thread_id)


    def dequeue_by_thread(self, thread_id: int) -> QueuedRequest | None:
        """Remove and return the request for a specific thread, or None."""
        if thread_id not in self._queue:
            return None
        self._order.remove(thread_id)
        return self._queue.pop(thread_id)

    def __len__(self) -> int:
        return len(self._order)


# ---------------------------------------------------------------------------
# InFlightTracker — cancel-in-flight support
# ---------------------------------------------------------------------------


class InFlightTracker:
    """Tracks active requests per thread for cancel-in-flight."""

    def __init__(self) -> None:
        self._active: dict[int, InFlightRequest] = {}

    def track(self, thread_id: int, slot_id: int) -> asyncio.Event:
        """Start tracking a request. Returns cancel_event for the handler to check."""
        cancel_event = asyncio.Event()
        self._active[thread_id] = InFlightRequest(
            thread_id=thread_id, slot_id=slot_id, cancel_event=cancel_event,
        )
        return cancel_event

    def cancel(self, thread_id: int) -> None:
        """Signal cancel for a thread (no-op if not tracked)."""
        req = self._active.get(thread_id)
        if req is not None:
            req.cancel_event.set()

    def untrack(self, thread_id: int) -> None:
        """Stop tracking a request (called on release)."""
        self._active.pop(thread_id, None)


# ---------------------------------------------------------------------------
# ProcessPool — the main pool manager
# ---------------------------------------------------------------------------


class ProcessPool:
    """Manages a pool of kiro-cli ACP processes with per-user isolation.
    
    Multi-user security enhancements:
    - Slots are bound to users (user_id) to prevent cross-user session access
    - Session affinity now tracks (user_id, thread_id) tuples
    - Processes are never shared across different users
    
    Invariants:
    - At least 1 slot exists after initialize() (warm process).
    - len(slots) <= max_processes.
    - Only IDLE or BUSY slots live in the list (crashed slots removed immediately).
    - A slot's user_id, once set, persists until the slot is reaped.
    """

    def __init__(self, config: Config) -> None:
        self.agent_name: str = config.kiro_agent_name
        self.log_level: str = config.log_level
        self.max_processes: int = config.max_processes
        self.idle_timeout: float = float(config.idle_timeout_seconds)
        self.slots: list[ProcessSlot] = []
        self.request_queue: RequestQueue = RequestQueue()
        self.in_flight: InFlightTracker = InFlightTracker()
        self._lock: asyncio.Lock = asyncio.Lock()
        self._reaper_task: asyncio.Task | None = None
        # Session affinity: (user_id, thread_id) → slot_id.
        # Persists across slot reassignment so a thread always returns to the
        # kiro-cli process that holds its session file lock.
        # Updated to include user_id for multi-user isolation.
        self._session_affinity: dict[tuple[int, int], int] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Spawn the first warm process and start the reaper. Fail-fast on error."""
        client = await ACPClient.spawn(self.agent_name, self.log_level)
        await client.initialize()
        slot = ProcessSlot(
            slot_id=0,
            client=client,
            status=SlotStatus.IDLE,
            last_used=time.time(),
        )
        self.slots.append(slot)
        self._reaper_task = asyncio.create_task(self._reaper_loop())
        logger.info("Process pool initialized with 1 warm process")

    async def shutdown(self) -> None:
        """Kill all processes and cancel the reaper."""
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            self._reaper_task = None
        async with self._lock:
            for slot in self.slots:
                if slot.client is not None:
                    try:
                        await slot.client.kill()
                    except Exception:
                        logger.debug("Error killing slot %s during shutdown", slot.slot_id, exc_info=True)
            self.slots.clear()
        logger.info("Process pool shut down")

    # ------------------------------------------------------------------
    # Acquire / Release
    # ------------------------------------------------------------------

    async def acquire(self, thread_id: int, user_id: int) -> ProcessSlot | None:
        """Acquire a process slot with per-user isolation. Returns None if pool is at max and all busy.

        Multi-user security enhancements:
        - Session affinity now uses (user_id, thread_id) tuple
        - Slots are bound to users: once assigned to a user, only that user can use it
        - Prevents cross-user session access and process memory sharing

        Session-affinity rules (prevent cross-slot session loading):

        kiro-cli holds an exclusive file lock on the session for the lifetime
        of the process, even after loading a different session.  Therefore a
        thread MUST always return to the same kiro-cli process that first
        created / loaded its session.

        The ``_session_affinity`` dict ((user_id, thread_id) → slot_id) persists this
        mapping across slot reassignment.  ``slot.thread_id`` tracks which
        thread is *currently* using the slot (for cancel-in-flight), while
        ``_session_affinity`` tracks which slot *owns* a thread's session.

        Acquire steps:
        1. Look up affinity slot for this (user_id, thread_id).
           a. Affinity slot IDLE and user matches → use it.
           b. Affinity slot BUSY → return None (caller enqueues; the slot
              will pick up the request via release_and_dequeue).
           c. Affinity slot gone (reaped / crashed) → clear stale affinity,
              fall through to step 2.
        2. No affinity → first-time thread or affinity was cleared.
           a. Grab any IDLE slot owned by this user (or unowned).
           b. Spawn new slot if under max, assign to this user.
           c. All busy at max → return None.
        """
        placeholder: ProcessSlot | None = None

        async with self._lock:
            # --- Step 1: check existing affinity ---
            affinity_key = (user_id, thread_id)
            affinity_slot_id = self._session_affinity.get(affinity_key)
            if affinity_slot_id is not None:
                affinity_slot = next(
                    (s for s in self.slots if s.slot_id == affinity_slot_id), None,
                )
                if affinity_slot is None:
                    # Slot was reaped or crashed — clear stale affinity
                    logger.debug(
                        "[user=%s thread=%s] Affinity slot %s gone — clearing",
                        user_id, thread_id, affinity_slot_id,
                    )
                    del self._session_affinity[affinity_key]
                elif affinity_slot.status == SlotStatus.IDLE:
                    # 1a: fast path — reuse affinity slot (user already matches)
                    self._cancel_inflight(thread_id)
                    affinity_slot.status = SlotStatus.BUSY
                    affinity_slot.thread_id = thread_id
                    return affinity_slot
                else:
                    # 1b: affinity slot is BUSY — cancel in-flight and enqueue
                    # The handler will detect the cancel event, abort, release
                    # the slot, and dequeue the replacement request.
                    self._cancel_inflight(thread_id)
                    logger.debug(
                        "[user=%s thread=%s] Affinity slot %s is busy — cancelling in-flight and will enqueue",
                        user_id, thread_id, affinity_slot.slot_id,
                    )
                    return None

            # --- Step 2: no affinity — first-time thread ---
            # 2a: grab any IDLE slot owned by this user or unowned
            for slot in self.slots:
                if slot.status == SlotStatus.IDLE and (slot.user_id is None or slot.user_id == user_id):
                    self._cancel_inflight(thread_id)
                    slot.status = SlotStatus.BUSY
                    slot.user_id = user_id  # Bind slot to user if not already
                    slot.thread_id = thread_id
                    self._session_affinity[affinity_key] = slot.slot_id
                    logger.debug(
                        "[user=%s thread=%s] Acquired slot %s (user_id set)",
                        user_id, thread_id, slot.slot_id,
                    )
                    return slot

            # 2b: spawn new process if under max
            if len(self.slots) < self.max_processes:
                slot_id = max((s.slot_id for s in self.slots), default=-1) + 1
                placeholder = ProcessSlot(
                    slot_id=slot_id,
                    client=None,
                    status=SlotStatus.BUSY,
                    last_used=time.time(),
                    user_id=user_id,  # Bind new slot to this user
                    thread_id=thread_id,
                )
                self.slots.append(placeholder)
                self._session_affinity[affinity_key] = slot_id
            # Lock released here — spawn happens outside

        # Spawn outside lock to avoid blocking all pool operations
        if placeholder is not None:
            try:
                client = await ACPClient.spawn(self.agent_name, self.log_level)
                await client.initialize()
                placeholder.client = client
                logger.info(
                    "Spawned new process slot %s for user %s", 
                    placeholder.slot_id, user_id
                )
                self._cancel_inflight(thread_id)
                return placeholder
            except Exception:
                logger.exception("Failed to spawn process for slot %s", placeholder.slot_id)
                async with self._lock:
                    if placeholder in self.slots:
                        self.slots.remove(placeholder)
                    # Clean up affinity on spawn failure
                    if self._session_affinity.get(affinity_key) == placeholder.slot_id:
                        del self._session_affinity[affinity_key]
                return None

        # 2c: all busy, at max capacity
        return None

    async def release(
        self, slot: ProcessSlot, session_id: str | None, thread_id: int | None,
    ) -> None:
        """Release a slot back to the pool. Detects crashes and updates affinity."""
        async with self._lock:
            self._release_inner(slot, session_id, thread_id)

    async def release_and_dequeue(
        self, slot: ProcessSlot, session_id: str | None, thread_id: int | None,
    ) -> tuple[QueuedRequest | None, ProcessSlot | None]:
        """Atomically release a slot and, if the queue has a request, re-acquire it.

        Affinity-aware dequeue priority:
        1. A queued request whose _session_affinity points to THIS slot.
           This keeps the session on the same kiro-cli process.
        2. The thread that just released (same thread_id) — continuity.
        3. FIFO fallback — any queued request (first-time threads or
           threads whose affinity slot was reaped).

        Returns (queued_request, slot) — both None if queue was empty or slot crashed.
        """
        async with self._lock:
            self._release_inner(slot, session_id, thread_id)

            # Slot may have been removed (crash / shutdown) — check before reuse
            if slot not in self.slots or slot.status != SlotStatus.IDLE:
                return None, None

            next_request: QueuedRequest | None = None

            # Priority 1: any queued request with affinity for THIS slot
            # Filter by requests from the same user (slot.user_id) for security
            for affinity_key, sid in self._session_affinity.items():
                user_id_key, thread_id_key = affinity_key
                if sid == slot.slot_id and (slot.user_id is None or user_id_key == slot.user_id):
                    req = self.request_queue.dequeue_by_thread(thread_id_key)
                    if req is not None:
                        next_request = req
                        break

            # Priority 2: same thread that just released
            if next_request is None and thread_id is not None and slot.user_id is not None:
                next_request = self.request_queue.dequeue_by_thread(thread_id)

            # Priority 3: FIFO fallback - only from same user
            if next_request is None:
                next_request = self.request_queue.dequeue()
                # Validate user matches (safety check)
                if next_request is not None and slot.user_id is not None and next_request.user_id != slot.user_id:
                    logger.warning(
                        "Skipping queued request from user %s for slot owned by user %s",
                        next_request.user_id, slot.user_id
                    )
                    # Put it back and try next time
                    self.request_queue.enqueue(next_request)
                    next_request = None

            if next_request is None:
                return None, None

            # Re-acquire the same slot for the queued request
            slot.status = SlotStatus.BUSY
            slot.thread_id = next_request.thread_id
            # Record affinity if this is a first-time thread
            affinity_key = (next_request.user_id, next_request.thread_id)
            if affinity_key not in self._session_affinity:
                self._session_affinity[affinity_key] = slot.slot_id
            # Cancel any in-flight request for the dequeued thread — the queued
            # message is newer and supersedes whatever is still streaming.
            self._cancel_inflight(next_request.thread_id)
            return next_request, slot

    def _cancel_inflight(self, thread_id: int) -> None:
        """Set the cancel event for a previous in-flight request on this thread."""
        self.in_flight.cancel(thread_id)

    def _release_inner(
        self, slot: ProcessSlot, session_id: str | None, thread_id: int | None,
    ) -> None:
        """Release logic (must be called under self._lock)."""
        # Guard: slot may have been removed by shutdown()
        if slot not in self.slots:
            logger.debug("Slot %s already removed from pool (shutdown?)", slot.slot_id)
            if thread_id is not None:
                self.in_flight.untrack(thread_id)
            return

        # Crash detection
        if slot.client is None or not slot.client.is_alive():
            logger.error("Process slot %s crashed — removing from pool", slot.slot_id)
            self.slots.remove(slot)
            # Clean up any affinity pointing to this crashed slot
            stale = [t for t, s in self._session_affinity.items() if s == slot.slot_id]
            for t in stale:
                del self._session_affinity[t]
            if thread_id is not None:
                self.in_flight.untrack(thread_id)
            return

        # Mark idle
        slot.status = SlotStatus.IDLE
        slot.last_used = time.time()
        slot.session_id = session_id
        slot.thread_id = thread_id

        if thread_id is not None:
            self.in_flight.untrack(thread_id)

    # ------------------------------------------------------------------
    # Background reaper
    # ------------------------------------------------------------------

    async def _reaper_loop(self) -> None:
        """Periodically kill idle processes (never the last one)."""
        try:
            while True:
                await asyncio.sleep(self.idle_timeout / 2)
                async with self._lock:
                    now = time.time()
                    to_remove: list[ProcessSlot] = []
                    for slot in self.slots:
                        if (
                            slot.status == SlotStatus.IDLE
                            and (now - slot.last_used) > self.idle_timeout
                            and len(self.slots) - len(to_remove) > 1
                        ):
                            if slot.client is not None:
                                await slot.client.kill()
                            to_remove.append(slot)
                    for slot in to_remove:
                        self.slots.remove(slot)
                        # Clean up affinity for reaped slot
                        stale = [t for t, s in self._session_affinity.items() if s == slot.slot_id]
                        for t in stale:
                            del self._session_affinity[t]
                        logger.info("Reaped idle process slot %s", slot.slot_id)
        except asyncio.CancelledError:
            pass  # Normal shutdown

"""C2: Process Pool — manages kiro-cli ACP process lifecycle with scale-to-one semantics."""

from __future__ import annotations

import asyncio
import logging
import time
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
        self._order: list[int] = []

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
        thread_id = self._order.pop(0)
        return self._queue.pop(thread_id)
    def requeue_front(self, request: QueuedRequest) -> None:
        """Re-insert a request at the front of the queue (used when handoff fails)."""
        self._queue[request.thread_id] = request
        self._order.insert(0, request.thread_id)

    def dequeue(self) -> QueuedRequest | None:
        """Remove and return the oldest request, or None if empty."""
        if not self._order:
            return None
        thread_id = self._order.pop(0)
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
    """Manages a pool of kiro-cli ACP processes with scale-to-one semantics.

    Invariants:
    - At least 1 slot exists after initialize() (warm process).
    - len(slots) <= max_processes.
    - Only IDLE or BUSY slots live in the list (crashed slots removed immediately).
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
        """Acquire a process slot. Returns None if pool is at max and all busy.

        Side effects:
        - Sets the OLD cancel_event if an in-flight request exists for this thread,
          but ONLY when a slot is actually acquired.  When the pool is full and the
          caller will enqueue instead, the in-flight stream must keep running.
        - May spawn a new process (outside the lock) if pool can grow.
        """
        placeholder: ProcessSlot | None = None

        async with self._lock:
            # 1. Affinity: prefer IDLE slot with matching thread_id
            for slot in self.slots:
                if slot.status == SlotStatus.IDLE and slot.thread_id == thread_id:
                    self._cancel_inflight(thread_id)
                    slot.status = SlotStatus.BUSY
                    return slot

            # 2. Any IDLE slot
            for slot in self.slots:
                if slot.status == SlotStatus.IDLE:
                    self._cancel_inflight(thread_id)
                    slot.status = SlotStatus.BUSY
                    return slot

            # 3. Spawn new process if under max — reserve with placeholder
            if len(self.slots) < self.max_processes:
                slot_id = max((s.slot_id for s in self.slots), default=-1) + 1
                placeholder = ProcessSlot(
                    slot_id=slot_id,
                    client=None,
                    status=SlotStatus.BUSY,
                    last_used=time.time(),
                )
                self.slots.append(placeholder)
            # Lock released here — spawn happens outside

        # Spawn outside lock to avoid blocking all pool operations
        if placeholder is not None:
            try:
                client = await ACPClient.spawn(self.agent_name, self.log_level)
                await client.initialize()
                placeholder.client = client
                logger.info("Spawned new process slot %s", placeholder.slot_id)
                self._cancel_inflight(thread_id)
                return placeholder
            except Exception:
                logger.exception("Failed to spawn process for slot %s", placeholder.slot_id)
                async with self._lock:
                    if placeholder in self.slots:
                        self.slots.remove(placeholder)
                return None

        # All busy, at max capacity
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

        Returns (queued_request, slot) — both None if queue was empty or slot crashed.
        This eliminates the race where another caller steals the slot between
        release() and the queued handler's acquire().
        """
        async with self._lock:
            self._release_inner(slot, session_id, thread_id)

            next_request = self.request_queue.dequeue()
            if next_request is None:
                return None, None

            # Slot may have been removed (crash / shutdown) — check before reuse
            if slot not in self.slots or slot.status != SlotStatus.IDLE:
                # Can't hand off this slot; put the request back at the front
                self.request_queue.requeue_front(next_request)
                return None, None

            # Re-acquire the same slot for the queued request
            slot.status = SlotStatus.BUSY
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
            if thread_id is not None:
                self.in_flight.untrack(thread_id)
            return

        # Mark idle, update affinity
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
                        logger.info("Reaped idle process slot %s", slot.slot_id)
        except asyncio.CancelledError:
            pass  # Normal shutdown

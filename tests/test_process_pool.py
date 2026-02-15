"""Unit tests for ProcessPool — acquire, release, cancel-in-flight, queue interactions."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tg_acp.process_pool import (
    InFlightTracker,
    ProcessPool,
    ProcessSlot,
    QueuedRequest,
    RequestQueue,
    SlotStatus,
)


def _make_config(max_processes: int = 2) -> MagicMock:
    config = MagicMock()
    config.kiro_agent_name = "test-agent"
    config.log_level = "INFO"
    config.max_processes = max_processes
    config.idle_timeout_seconds = 300
    return config


def _make_slot(slot_id: int, status: SlotStatus = SlotStatus.IDLE, thread_id: int | None = None) -> ProcessSlot:
    client = MagicMock()
    client.is_alive.return_value = True
    return ProcessSlot(
        slot_id=slot_id, client=client, status=status,
        last_used=0.0, session_id=None, thread_id=thread_id,
    )


class TestAcquireCancelRace:
    """Cancel-previous semantics: when a new message arrives for a thread
    that already has an in-flight request, the old request must be cancelled
    even though the slot is busy and the new message gets enqueued.

    The old handler detects the cancel event, aborts streaming, releases the
    slot, and release_and_dequeue picks up the queued replacement.
    """

    @pytest.mark.asyncio
    async def test_acquire_cancels_inflight_when_affinity_slot_busy(self):
        config = _make_config(max_processes=2)
        pool = ProcessPool(config)
        pool._reaper_task = MagicMock()  # prevent real reaper

        # Manually set up 2 BUSY slots (pool at max)
        slot0 = _make_slot(0, SlotStatus.BUSY, thread_id=42)
        slot1 = _make_slot(1, SlotStatus.BUSY, thread_id=99)
        pool.slots = [slot0, slot1]
        pool._session_affinity = {42: 0, 99: 1}

        # Thread 42 is in-flight on slot 0
        cancel_event = pool.in_flight.track(42, slot0.slot_id)
        assert not cancel_event.is_set(), "precondition: cancel_event starts unset"

        # New message for thread 42 arrives — affinity slot is busy
        result = await pool.acquire(42, user_id=1)

        assert result is None, "acquire should return None when affinity slot is busy"
        assert cancel_event.is_set(), (
            "cancel_event MUST be set so the old handler aborts and releases "
            "the slot for the queued replacement"
        )


    @pytest.mark.asyncio
    async def test_release_and_dequeue_cancels_inflight_for_dequeued_thread(self):
        """When release_and_dequeue hands off a slot for thread T,
        it must cancel any in-flight request for T on another slot.

        Sequence:
        1. Thread T is in-flight on slot 0 (cancel_event_A).
        2. Thread T message 2 was enqueued (pool was full).
        3. Slot 1 finishes thread X → release_and_dequeue dequeues thread T.
        4. EXPECTED: cancel_event_A is set so slot 0 stops streaming for T.
        5. Slot 1 is handed off to process thread T message 2.
        """
        config = _make_config(max_processes=2)
        pool = ProcessPool(config)
        pool._reaper_task = MagicMock()

        slot0 = _make_slot(0, SlotStatus.BUSY, thread_id=42)
        slot1 = _make_slot(1, SlotStatus.BUSY, thread_id=99)
        pool.slots = [slot0, slot1]

        # Thread 42 is in-flight on slot 0
        cancel_event_a = pool.in_flight.track(42, slot0.slot_id)
        assert not cancel_event_a.is_set()

        # Thread 42 message 2 is queued
        queued = QueuedRequest(
            thread_id=42, user_id=1, message_text="second msg",
            files=[], chat_id=100, workspace_path="/tmp/ws",
        )
        pool.request_queue.enqueue(queued)

        # Slot 1 finishes thread 99 → release_and_dequeue
        next_req, handoff_slot = await pool.release_and_dequeue(slot1, "sid-99", 99)

        assert next_req is queued
        assert handoff_slot is slot1
        assert handoff_slot.status == SlotStatus.BUSY
        # The in-flight request for thread 42 on slot 0 must be cancelled
        assert cancel_event_a.is_set(), (
            "release_and_dequeue must cancel the in-flight request for the "
            "dequeued thread so the old stream stops"
        )


class TestSessionAffinity:
    """Tests for session-affinity acquire logic (prevents cross-slot session loading)."""

    @pytest.mark.asyncio
    async def test_acquire_returns_affinity_idle_slot(self):
        """Thread A's affinity slot is IDLE — acquire returns it directly."""
        pool = ProcessPool(_make_config(max_processes=2))
        pool._reaper_task = MagicMock()

        slot0 = _make_slot(0, SlotStatus.IDLE, thread_id=42)
        slot1 = _make_slot(1, SlotStatus.IDLE, thread_id=99)
        pool.slots = [slot0, slot1]
        pool._session_affinity = {42: 0, 99: 1}

        result = await pool.acquire(42, user_id=1)
        assert result is slot0
        assert slot0.status == SlotStatus.BUSY

    @pytest.mark.asyncio
    async def test_acquire_returns_none_when_affinity_slot_busy(self):
        """Thread A's affinity slot is BUSY — acquire returns None (enqueue)."""
        pool = ProcessPool(_make_config(max_processes=2))
        pool._reaper_task = MagicMock()

        slot0 = _make_slot(0, SlotStatus.BUSY, thread_id=42)
        slot1 = _make_slot(1, SlotStatus.IDLE, thread_id=99)
        pool.slots = [slot0, slot1]
        pool._session_affinity = {42: 0, 99: 1}

        result = await pool.acquire(42, user_id=1)
        assert result is None, (
            "Must return None when affinity slot is BUSY — "
            "grabbing slot1 would cause session lock contention"
        )
        # slot1 must remain IDLE (not stolen)
        assert slot1.status == SlotStatus.IDLE

    @pytest.mark.asyncio
    async def test_acquire_returns_none_when_affinity_slot_busy_serving_other_thread(self):
        """Thread A's affinity slot is BUSY serving thread B — still returns None.

        This is the key scenario: slot 0 created thread A's session, then
        thread B took over slot 0.  Thread A must wait for slot 0, not grab
        slot 1 (which would cause -32603 lock contention).
        """
        pool = ProcessPool(_make_config(max_processes=2))
        pool._reaper_task = MagicMock()

        # Slot 0 is BUSY serving thread B, but affinity says thread A → slot 0
        slot0 = _make_slot(0, SlotStatus.BUSY, thread_id=99)  # currently serving B
        slot1 = _make_slot(1, SlotStatus.IDLE, thread_id=None)
        pool.slots = [slot0, slot1]
        pool._session_affinity = {42: 0}  # thread A's session is on slot 0

        result = await pool.acquire(42, user_id=1)
        assert result is None, (
            "Must return None — affinity slot 0 is BUSY (even though serving "
            "another thread). Thread A must wait, not grab slot 1."
        )
        assert slot1.status == SlotStatus.IDLE

    @pytest.mark.asyncio
    async def test_acquire_grabs_any_idle_for_new_thread(self):
        """First-time thread (no affinity) grabs any IDLE slot and records affinity."""
        pool = ProcessPool(_make_config(max_processes=2))
        pool._reaper_task = MagicMock()

        slot0 = _make_slot(0, SlotStatus.BUSY, thread_id=42)
        slot1 = _make_slot(1, SlotStatus.IDLE, thread_id=99)
        pool.slots = [slot0, slot1]

        result = await pool.acquire(777, user_id=1)
        assert result is slot1
        assert slot1.status == SlotStatus.BUSY
        assert slot1.thread_id == 777
        assert pool._session_affinity[777] == 1, "Affinity must be recorded on acquire"

    @pytest.mark.asyncio
    async def test_acquire_clears_stale_affinity_when_slot_reaped(self):
        """If affinity slot was reaped, clear affinity and grab any IDLE slot."""
        pool = ProcessPool(_make_config(max_processes=2))
        pool._reaper_task = MagicMock()

        # Affinity says thread 42 → slot 5, but slot 5 doesn't exist
        slot0 = _make_slot(0, SlotStatus.IDLE)
        pool.slots = [slot0]
        pool._session_affinity = {42: 5}

        result = await pool.acquire(42, user_id=1)
        assert result is slot0, "Should grab any IDLE slot after clearing stale affinity"
        assert pool._session_affinity[42] == 0, "New affinity should point to slot 0"

    @pytest.mark.asyncio
    async def test_release_and_dequeue_prefers_affinity_thread(self):
        """When slot releases, dequeue prefers a queued request with affinity for this slot."""
        pool = ProcessPool(_make_config(max_processes=1))
        pool._reaper_task = MagicMock()

        slot0 = _make_slot(0, SlotStatus.BUSY, thread_id=99)
        pool.slots = [slot0]
        # Thread 42 has affinity for slot 0
        pool._session_affinity = {42: 0, 99: 0}

        # Queue: thread 88 first (no affinity), then thread 42 (affinity for slot 0)
        pool.request_queue.enqueue(QueuedRequest(
            thread_id=88, user_id=3, message_text="msg88",
            files=[], chat_id=300, workspace_path="/tmp",
        ))
        pool.request_queue.enqueue(QueuedRequest(
            thread_id=42, user_id=1, message_text="msg42",
            files=[], chat_id=100, workspace_path="/tmp",
        ))

        next_req, handoff = await pool.release_and_dequeue(slot0, "sid-99", 99)
        assert next_req is not None
        assert next_req.thread_id == 42, (
            "Affinity dequeue must prefer thread 42 (affinity for slot 0) over FIFO thread 88"
        )

    @pytest.mark.asyncio
    async def test_release_and_dequeue_falls_back_to_fifo(self):
        """When no affinity match in queue, falls back to FIFO."""
        pool = ProcessPool(_make_config(max_processes=1))
        pool._reaper_task = MagicMock()

        slot0 = _make_slot(0, SlotStatus.BUSY, thread_id=42)
        pool.slots = [slot0]
        pool._session_affinity = {42: 0}

        # Queue only has thread 99 (no affinity for slot 0)
        pool.request_queue.enqueue(QueuedRequest(
            thread_id=99, user_id=2, message_text="msg99",
            files=[], chat_id=200, workspace_path="/tmp",
        ))

        next_req, handoff = await pool.release_and_dequeue(slot0, "sid-42", 42)
        assert next_req is not None
        assert next_req.thread_id == 99, "Should fall back to FIFO"


class TestDequeueByThread:
    """Tests for RequestQueue.dequeue_by_thread."""

    def test_dequeue_by_thread_found(self):
        q = RequestQueue()
        q.enqueue(QueuedRequest(thread_id=1, user_id=1, message_text="a", files=[], chat_id=1, workspace_path="/"))
        q.enqueue(QueuedRequest(thread_id=2, user_id=2, message_text="b", files=[], chat_id=2, workspace_path="/"))

        result = q.dequeue_by_thread(1)
        assert result is not None
        assert result.thread_id == 1
        assert len(q) == 1

    def test_dequeue_by_thread_not_found(self):
        q = RequestQueue()
        q.enqueue(QueuedRequest(thread_id=1, user_id=1, message_text="a", files=[], chat_id=1, workspace_path="/"))

        result = q.dequeue_by_thread(999)
        assert result is None
        assert len(q) == 1

    def test_dequeue_by_thread_preserves_order(self):
        q = RequestQueue()
        q.enqueue(QueuedRequest(thread_id=1, user_id=1, message_text="a", files=[], chat_id=1, workspace_path="/"))
        q.enqueue(QueuedRequest(thread_id=2, user_id=2, message_text="b", files=[], chat_id=2, workspace_path="/"))
        q.enqueue(QueuedRequest(thread_id=3, user_id=3, message_text="c", files=[], chat_id=3, workspace_path="/"))

        # Remove middle element
        q.dequeue_by_thread(2)
        # FIFO order should be 1, 3
        r1 = q.dequeue()
        r3 = q.dequeue()
        assert r1.thread_id == 1
        assert r3.thread_id == 3

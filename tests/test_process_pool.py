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
    """Reproduce: two messages for the same thread when pool is full.

    Sequence:
    1. Thread T is in-flight on slot 0, cancel_event_1 is live.
    2. All slots are BUSY (pool at max).
    3. A new message for thread T calls acquire() — pool is full, returns None.
    4. BUG: acquire() sets cancel_event_1 even though it can't provide a slot,
       truncating the in-flight stream.
    5. EXPECTED: cancel_event_1 should NOT be set when acquire returns None.
       The new message gets enqueued and will be processed after the current
       one finishes via release_and_dequeue.
    """

    @pytest.mark.asyncio
    async def test_acquire_does_not_cancel_inflight_when_pool_full(self):
        config = _make_config(max_processes=2)
        pool = ProcessPool(config)
        pool._reaper_task = MagicMock()  # prevent real reaper

        # Manually set up 2 BUSY slots (pool at max)
        slot0 = _make_slot(0, SlotStatus.BUSY, thread_id=42)
        slot1 = _make_slot(1, SlotStatus.BUSY, thread_id=99)
        pool.slots = [slot0, slot1]

        # Thread 42 is in-flight on slot 0
        cancel_event = pool.in_flight.track(42, slot0.slot_id)
        assert not cancel_event.is_set(), "precondition: cancel_event starts unset"

        # New message for thread 42 arrives — pool is full
        result = await pool.acquire(42, user_id=1)

        assert result is None, "acquire should return None when pool is full"
        # THIS IS THE BUG: cancel_event gets set even though no slot was acquired
        assert not cancel_event.is_set(), (
            "cancel_event should NOT be set when acquire() returns None — "
            "the in-flight request should keep streaming"
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

"""Unit tests for ACPClient — no real kiro-cli process needed.

These tests exercise the session_prompt streaming logic by directly
manipulating the internal notification queue and pending futures.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from tg_acp.acp_client import ACPClient, ACPClientState, TURN_END


def _make_notification(session_update: str, text: str | None = None) -> dict:
    """Build a session/update notification dict as kiro-cli would emit."""
    update: dict = {"sessionUpdate": session_update}
    if text is not None:
        update["content"] = {"type": "text", "text": text}
    return {
        "method": "session/update",
        "params": {"update": update},
    }


def _make_client_ready() -> ACPClient:
    """Create an ACPClient in READY state with a fake process (no real subprocess)."""
    client = ACPClient()
    client._state = ACPClientState.READY
    # Fake process that looks alive
    proc = MagicMock()
    proc.returncode = None  # is_alive() checks this
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()

    async def _noop_drain() -> None:
        pass

    proc.stdin.drain = _noop_drain
    client._process = proc
    return client


class TestSessionPromptChunkDrop:
    """Reproduce: response future resolves while notifications are still queued.

    Sequence:
    1. session/prompt is sent (internally creates a future in _pending).
    2. kiro-cli emits 3 agent_message_chunk notifications, then the JSON-RPC
       response.  _read_stdout processes them all in one event-loop tick:
       it queues the 3 notifications, then resolves the future.
    3. session_prompt's loop checks future.done() BEFORE draining the queue.
    4. BUG: It yields TURN_END immediately, dropping the 3 queued chunks.
    5. EXPECTED: All 3 chunks are yielded before TURN_END.
    """

    @pytest.mark.asyncio
    async def test_chunks_not_dropped_when_future_resolves_early(self):
        client = _make_client_ready()

        # Patch _send to simulate the race condition:
        # When session_prompt calls _send, we immediately queue 3 notification
        # chunks AND resolve the future — mimicking _read_stdout processing
        # all buffered lines (notifications + response) in one event-loop tick.
        async def fake_send(msg: dict) -> None:
            rid = msg.get("id")
            if rid is not None and rid in client._pending:
                # Simulate: _read_stdout queues notifications first...
                for i in range(3):
                    await client._notification_queue.put(
                        _make_notification("agent_message_chunk", f"chunk-{i}")
                    )
                # ...then resolves the response future
                fut = client._pending[rid]
                if not fut.done():
                    fut.set_result({"id": rid, "result": {}})

        client._send = fake_send  # type: ignore[assignment]

        # Collect all updates from session_prompt
        chunks: list[str] = []
        got_turn_end = False

        async for update in client.session_prompt("fake-session", [{"type": "text", "text": "hi"}]):
            update_type = update.get("sessionUpdate", "")
            if update_type == "agent_message_chunk":
                content = update.get("content", {})
                if content.get("type") == "text":
                    chunks.append(content["text"])
            elif update_type == TURN_END:
                got_turn_end = True

        assert got_turn_end, "Expected TURN_END"
        assert chunks == ["chunk-0", "chunk-1", "chunk-2"], (
            f"Expected all 3 chunks before TURN_END, got: {chunks}"
        )

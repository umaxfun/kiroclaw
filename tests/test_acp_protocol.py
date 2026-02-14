"""Integration tests for C1 ACP Client — real kiro-cli, full JSON-RPC flow.

These tests require:
- kiro-cli installed and on PATH
- A valid global agent config (provisioned by the bot or manually)

Tests use a temporary workspace directory and real kiro-cli processes.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from tg_acp.acp_client import ACPClient, ACPClientState, TURN_END

# Skip all tests if kiro-cli is not available
pytestmark = pytest.mark.skipif(
    shutil.which("kiro-cli") is None,
    reason="kiro-cli not found on PATH",
)

# Agent name — must match a provisioned agent in ~/.kiro/agents/
AGENT_NAME = "tg-acp"


@pytest.fixture
def workspace(tmp_path: Path) -> str:
    """Create a temporary workspace directory."""
    ws = tmp_path / "test_workspace"
    ws.mkdir()
    return str(ws)


class TestACPFullFlow:
    """Integration: full ACP protocol flow with real kiro-cli."""

    @pytest.mark.timeout(120)
    async def test_acp_full_flow(self, workspace: str):
        """initialize -> session/new -> prompt -> streaming chunks -> turn_end."""
        client = await ACPClient.spawn(AGENT_NAME)
        try:
            assert client.state == ACPClientState.IDLE

            await client.initialize()
            assert client.state == ACPClientState.READY

            session_id = await client.session_new(cwd=workspace)
            assert session_id  # non-empty string
            assert client.state == ACPClientState.READY

            chunks: list[str] = []
            got_turn_end = False

            async for update in client.session_prompt(
                session_id,
                [{"type": "text", "text": "Say exactly: HELLO_ACP_TEST"}],
            ):
                update_type = update.get("sessionUpdate", "")
                if update_type == "agent_message_chunk":
                    content = update.get("content", {})
                    if content.get("type") == "text":
                        chunks.append(content["text"])
                elif update_type == TURN_END:
                    got_turn_end = True
                    break

            assert got_turn_end, "Expected TurnEnd"
            assert len(chunks) > 0, "Expected at least one text chunk"
            assert client.state == ACPClientState.READY
        finally:
            await client.kill()

    @pytest.mark.timeout(60)
    async def test_acp_session_new_returns_id(self, workspace: str):
        """session/new returns a valid session_id string."""
        client = await ACPClient.spawn(AGENT_NAME)
        try:
            await client.initialize()
            session_id = await client.session_new(cwd=workspace)
            assert isinstance(session_id, str)
            assert len(session_id) > 0
        finally:
            await client.kill()

    @pytest.mark.timeout(120)
    async def test_acp_streaming_chunks(self, workspace: str):
        """At least 1 agent_message_chunk received before turn end."""
        client = await ACPClient.spawn(AGENT_NAME)
        try:
            await client.initialize()
            session_id = await client.session_new(cwd=workspace)

            chunk_count = 0
            async for update in client.session_prompt(
                session_id,
                [{"type": "text", "text": "Count to 3."}],
            ):
                if update.get("sessionUpdate") == "agent_message_chunk":
                    chunk_count += 1
                elif update.get("sessionUpdate") == TURN_END:
                    break

            assert chunk_count >= 1, f"Expected >= 1 chunks, got {chunk_count}"
        finally:
            await client.kill()

    @pytest.mark.timeout(30)
    async def test_acp_process_kill(self, workspace: str):
        """client.kill() terminates the subprocess cleanly."""
        client = await ACPClient.spawn(AGENT_NAME)
        await client.initialize()

        assert client.is_alive()
        await client.kill()
        assert not client.is_alive()
        assert client.state == ACPClientState.DEAD

    @pytest.mark.timeout(30)
    async def test_acp_dead_detection(self, workspace: str):
        """After kill, is_alive() returns False and state is DEAD."""
        client = await ACPClient.spawn(AGENT_NAME)
        await client.initialize()
        await client.kill()

        assert client.state == ACPClientState.DEAD
        assert not client.is_alive()

        # Attempting to use a dead client should fail
        with pytest.raises(RuntimeError):
            await client.session_new(cwd=workspace)

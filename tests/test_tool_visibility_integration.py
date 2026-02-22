"""Integration test — tool call visibility in drafts and final messages.

Verifies that tool_call / tool_call_update events from kiro-cli are surfaced:
1. In sendMessageDraft as a status line (🔧 ...) while the tool runs
2. In the final sendMessage as a summary prefix (🔧 ...)

Uses a mock ACP stream to simulate the exact notification sequence observed
from real kiro-cli (see .tmp/probe.py output for reference payloads).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tg_acp.acp_client import TURN_END
from tg_acp.bot_handlers import BotContext, handle_message_internal, setup
from tg_acp.config import Config
from tg_acp.process_pool import ProcessPool, ProcessSlot
from tg_acp.session_store import SessionRecord, SessionStore


def _make_config() -> Config:
    return Config(
        bot_token="fake",
        workspace_base_path="./workspaces/",
        max_processes=1,
        idle_timeout_seconds=120,
        kiro_agent_name="tg-acp",
        log_level="DEBUG",
        kiro_config_path="./kiro-config/",
        allowed_telegram_ids=frozenset({1}),
    )


def _make_ctx(existing_session: SessionRecord | None = None):
    """Build a BotContext with mocked pool/store/bot, matching test_bot_handlers pattern."""
    config = _make_config()
    store = MagicMock(spec=SessionStore)
    store.get_session.return_value = existing_session

    client = MagicMock()
    client.session_new = AsyncMock(return_value="new-sid")
    client.session_load = AsyncMock()
    client.session_cancel = AsyncMock()

    slot = MagicMock(spec=ProcessSlot)
    slot.slot_id = 0
    slot.client = client

    pool = MagicMock(spec=ProcessPool)
    pool.acquire = AsyncMock(return_value=slot)
    pool.release_and_dequeue = AsyncMock(return_value=(None, None))

    # in_flight tracking
    cancel_event = MagicMock()
    cancel_event.is_set.return_value = False
    tracker = MagicMock()
    tracker.track.return_value = cancel_event
    pool.in_flight = tracker

    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_message_draft = AsyncMock()

    ctx = BotContext(config=config, store=store, pool=pool, bot=bot)
    return ctx, pool, slot, client


async def _fake_prompt_with_tool_call(session_id, content):
    """Simulate the exact notification sequence from a real kiro-cli tool call.

    Sequence observed from probe.py:
    1. agent_message_chunk (thinking text)
    2. tool_call (title="Creating hello.txt", kind="edit")
    3. tool_call_update (status="completed", title="Creating hello.txt")
    4. agent_message_chunk (response text)
    5. TURN_END
    """
    # 1. Initial thinking text
    yield {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "I'll create "}}
    yield {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "that file."}}

    # 2. Tool call starts
    yield {
        "sessionUpdate": "tool_call",
        "toolCallId": "tooluse_abc123",
        "title": "Creating hello.txt",
        "kind": "edit",
        "content": [{"type": "diff", "path": "hello.txt", "oldText": None, "newText": "hello world"}],
        "rawInput": {"command": "create", "path": "hello.txt", "content": "hello world"},
    }

    # 3. Tool call completes
    yield {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "tooluse_abc123",
        "kind": "edit",
        "status": "completed",
        "title": "Creating hello.txt",
        "rawOutput": {"items": [{"Text": ""}]},
    }

    # 4. Agent response after tool
    yield {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": " Done."}}

    # 5. Turn end
    yield {"sessionUpdate": TURN_END, "content": None}


async def _fake_prompt_multi_tool(session_id, content):
    """Simulate two tool calls in one turn."""
    yield {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Let me "}}

    # Tool 1: read
    yield {
        "sessionUpdate": "tool_call",
        "toolCallId": "tooluse_read1",
        "title": "Reading config.json",
        "kind": "read",
    }
    yield {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "tooluse_read1",
        "status": "completed",
        "title": "Reading config.json",
    }

    # Tool 2: write
    yield {
        "sessionUpdate": "tool_call",
        "toolCallId": "tooluse_write1",
        "title": "Updating config.json",
        "kind": "edit",
    }
    yield {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "tooluse_write1",
        "status": "completed",
        "title": "Updating config.json",
    }

    yield {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "do that. Updated."}}
    yield {"sessionUpdate": TURN_END, "content": None}


async def _fake_prompt_no_tools(session_id, content):
    """Simulate a response with no tool calls."""
    yield {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Hello! "}}
    yield {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "How can I help?"}}
    yield {"sessionUpdate": TURN_END, "content": None}


class TestToolCallDraftVisibility:
    """Tool calls show 🔧 status in sendMessageDraft while running."""

    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    async def test_tool_call_triggers_draft(self, mock_cwd):
        ctx, pool, slot, client = _make_ctx()
        client.session_prompt = _fake_prompt_with_tool_call

        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.send_tool_draft = AsyncMock()
        mock_writer.set_tool_status = MagicMock()
        mock_writer.log_tool_call = MagicMock()
        mock_writer.finalize = AsyncMock(return_value=[])
        mock_writer.cancel = MagicMock()
        mock_writer.buffer = ""
        setup(ctx)

        with patch("tg_acp.bot_handlers.StreamWriter", return_value=mock_writer):
            await handle_message_internal(
                user_id=1, thread_id=42,
                message_text="create hello.txt",
                file_paths=[], chat_id=100,
                workspace_path="/tmp/ws/1/42",
            )

        # set_tool_status called with 🔧 when tool_call arrives
        status_calls = [
            str(c) for c in mock_writer.set_tool_status.call_args_list
        ]
        assert any("🔧" in s for s in status_calls), (
            f"Expected set_tool_status with 🔧, got: {status_calls}"
        )
        assert any("Creating hello.txt" in s for s in status_calls), (
            f"Expected tool title in status, got: {status_calls}"
        )

        # send_tool_draft called at least once
        mock_writer.send_tool_draft.assert_awaited()

        # log_tool_call called when tool completes
        mock_writer.log_tool_call.assert_called_with("Creating hello.txt")

        # Status cleared after completion
        clear_calls = [
            c for c in mock_writer.set_tool_status.call_args_list
            if c.args == ("",)
        ]
        assert len(clear_calls) > 0, "Tool status should be cleared after completion"


class TestToolCallFinalSummary:
    """Completed tool calls appear as 🔧 summary in the final message."""

    @pytest.mark.asyncio
    async def test_single_tool_summary_in_final(self):
        """StreamWriter.finalize prepends 🔧 summary when tools were logged."""
        from tg_acp.stream_writer import StreamWriter

        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.send_message_draft = AsyncMock()

        writer = StreamWriter(bot, chat_id=100, thread_id=42)
        writer.log_tool_call("Creating hello.txt")
        writer._buffer = "Done. File created."

        await writer.finalize()

        # The final send_message should contain the tool summary
        assert bot.send_message.await_count >= 1
        sent_text = bot.send_message.call_args.kwargs.get(
            "text", bot.send_message.call_args.args[0] if bot.send_message.call_args.args else ""
        )
        assert "🔧" in sent_text
        assert "Creating hello.txt" in sent_text

    @pytest.mark.asyncio
    async def test_multi_tool_summary_joined(self):
        """Multiple tool calls joined with → in summary."""
        from tg_acp.stream_writer import StreamWriter

        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.send_message_draft = AsyncMock()

        writer = StreamWriter(bot, chat_id=100, thread_id=42)
        writer.log_tool_call("Reading config.json")
        writer.log_tool_call("Updating config.json")
        writer._buffer = "Updated."

        await writer.finalize()

        sent_text = bot.send_message.call_args.kwargs.get("text", "")
        assert "Reading config.json" in sent_text
        assert "Updating config.json" in sent_text
        assert "→" in sent_text

    @pytest.mark.asyncio
    async def test_no_tools_no_summary(self):
        """No tool calls → no 🔧 prefix in final message."""
        from tg_acp.stream_writer import StreamWriter

        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.send_message_draft = AsyncMock()

        writer = StreamWriter(bot, chat_id=100, thread_id=42)
        writer._buffer = "Just a text response."

        await writer.finalize()

        sent_text = bot.send_message.call_args.kwargs.get("text", "")
        assert "🔧" not in sent_text


class TestToolDraftText:
    """StreamWriter._draft_text includes tool status."""

    def test_tool_status_appended_to_buffer(self):
        from tg_acp.stream_writer import StreamWriter

        bot = MagicMock()
        writer = StreamWriter(bot, chat_id=100, thread_id=42)
        writer._buffer = "Thinking..."
        writer.set_tool_status("🔧 Creating file…")

        draft = writer._draft_text()
        assert "Thinking..." in draft
        assert "🔧 Creating file…" in draft

    def test_tool_status_alone_when_no_buffer(self):
        from tg_acp.stream_writer import StreamWriter

        bot = MagicMock()
        writer = StreamWriter(bot, chat_id=100, thread_id=42)
        writer.set_tool_status("🔧 Reading data…")

        draft = writer._draft_text()
        assert "🔧 Reading data…" in draft

    def test_no_status_no_extra(self):
        from tg_acp.stream_writer import StreamWriter

        bot = MagicMock()
        writer = StreamWriter(bot, chat_id=100, thread_id=42)
        writer._buffer = "Hello"

        draft = writer._draft_text()
        assert draft == "Hello"
        assert "🔧" not in draft

    def test_duplicate_tool_calls_deduped(self):
        from tg_acp.stream_writer import StreamWriter

        bot = MagicMock()
        writer = StreamWriter(bot, chat_id=100, thread_id=42)
        writer.log_tool_call("Creating file")
        writer.log_tool_call("Creating file")  # duplicate

        assert len(writer._tool_log) == 1

"""Tests for C4: Stream Writer — sliding window, message split, cancel, error handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tg_acp.stream_writer import (
    StreamWriter,
    _sliding_window,
    _split_message,
    WINDOW_SIZE,
    MSG_LIMIT,
)


class TestSlidingWindow:
    def test_short_text_returned_as_is(self):
        text = "Hello world"
        assert _sliding_window(text) == text

    def test_exact_window_size(self):
        text = "x" * WINDOW_SIZE
        assert _sliding_window(text) == text

    def test_long_text_returns_tail_with_prefix(self):
        text = "A" * 1000 + "B" * WINDOW_SIZE
        result = _sliding_window(text)
        assert result.startswith("…\n")
        assert len(result) == WINDOW_SIZE + 2  # "…\n" prefix
        assert result.endswith("B" * WINDOW_SIZE)


class TestMessageSplit:
    def test_under_limit_no_split(self):
        text = "Hello world"
        assert _split_message(text) == [text]

    def test_exact_limit(self):
        text = "x" * MSG_LIMIT
        assert _split_message(text) == [text]

    def test_over_limit_newline_break(self):
        # Place a newline near the boundary
        first_part = "x" * (MSG_LIMIT - 100) + "\n"
        second_part = "y" * 200
        text = first_part + second_part
        segments = _split_message(text)
        assert len(segments) == 2
        assert segments[0] == first_part
        assert segments[1] == second_part

    def test_over_limit_hard_break(self):
        # No newlines at all — hard break at MSG_LIMIT
        text = "x" * (MSG_LIMIT + 500)
        segments = _split_message(text)
        assert len(segments) == 2
        assert len(segments[0]) == MSG_LIMIT
        assert len(segments[1]) == 500

    def test_multi_segment_split(self):
        text = "x" * (MSG_LIMIT * 3 + 100)
        segments = _split_message(text)
        assert len(segments) == 4
        for seg in segments[:-1]:
            assert len(seg) == MSG_LIMIT
        assert len(segments[-1]) == 100


class TestStreamWriterCancel:
    @pytest.fixture
    def writer(self):
        bot = AsyncMock()
        return StreamWriter(bot, chat_id=123, thread_id=456, draft_id=1)

    async def test_write_chunk_after_cancel_is_noop(self, writer):
        await writer.write_chunk("before")
        writer.cancel()
        await writer.write_chunk("after")
        assert writer.buffer == "before"

    async def test_finalize_after_cancel_returns_empty(self, writer):
        await writer.write_chunk("some text")
        writer.cancel()
        result = await writer.finalize()
        assert result == []
        writer._bot.send_message.assert_not_called()


class TestStreamWriterFinalize:
    async def test_empty_buffer_skips_send(self):
        bot = AsyncMock()
        writer = StreamWriter(bot, chat_id=123, thread_id=456, draft_id=1)
        result = await writer.finalize()
        assert result == []
        bot.send_message.assert_not_called()
        bot.send_message_draft.assert_not_called()

    async def test_finalize_sends_message(self):
        bot = AsyncMock()
        writer = StreamWriter(bot, chat_id=123, thread_id=456, draft_id=1)
        writer._buffer = "Hello world"
        await writer.finalize()
        bot.send_message.assert_called_once_with(
            chat_id=123, text="Hello world", message_thread_id=456
        )


class TestStreamWriterDraftError:
    async def test_draft_error_swallowed(self):
        bot = AsyncMock()
        bot.send_message_draft.side_effect = Exception("rate limited")
        writer = StreamWriter(bot, chat_id=123, thread_id=456, draft_id=1)
        # Should not raise
        await writer.write_chunk("hello")
        assert writer.buffer == "hello"

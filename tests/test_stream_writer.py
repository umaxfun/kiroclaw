"""Tests for C4: Stream Writer — sliding window, message split, HTML conversion, cancel, error handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tg_acp.stream_writer import (
    StreamWriter,
    _sliding_window,
    _split_message,
    _split_html,
    _open_tags_at,
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

    async def test_finalize_sends_message_as_html(self):
        bot = AsyncMock()
        writer = StreamWriter(bot, chat_id=123, thread_id=456, draft_id=1)
        writer._buffer = "Hello **world**"
        await writer.finalize()
        bot.send_message.assert_called_once_with(
            chat_id=123, text="Hello <b>world</b>", message_thread_id=456, parse_mode="HTML"
        )


class TestStreamWriterDraftError:
    async def test_draft_error_swallowed(self):
        bot = AsyncMock()
        bot.send_message_draft.side_effect = Exception("rate limited")
        writer = StreamWriter(bot, chat_id=123, thread_id=456, draft_id=1)
        # Should not raise
        await writer.write_chunk("hello")
        assert writer.buffer == "hello"


class TestOpenTagsAt:
    def test_no_tags(self):
        assert _open_tags_at("hello world") == []

    def test_closed_tag(self):
        assert _open_tags_at("<b>bold</b>") == []

    def test_unclosed_inline(self):
        result = _open_tags_at("text <b>bold")
        assert len(result) == 1
        assert result[0][0] == "b"

    def test_nested_tags(self):
        result = _open_tags_at("<pre><code>stuff")
        assert len(result) == 2
        assert result[0][0] == "pre"
        assert result[1][0] == "code"

    def test_ignores_unknown_tags(self):
        assert _open_tags_at("<div>stuff</div>") == []


class TestSplitHtml:
    def test_under_limit_no_split(self):
        html = "<b>short</b>"
        assert _split_html(html) == [html]

    def test_plain_text_over_limit(self):
        html = "x" * (MSG_LIMIT + 100)
        segments = _split_html(html)
        assert len(segments) == 2
        assert "".join(segments) == html

    def test_inline_tag_backtrack(self):
        """Inline tag at boundary: should backtrack before the opening tag."""
        # Build: plain text up to near limit, then <b>bold text</b>
        prefix = "x" * (MSG_LIMIT - 50)
        html = prefix + "<b>this is bold and goes over the limit by a lot</b>"
        segments = _split_html(html)
        assert len(segments) == 2
        # First segment should NOT contain a partial <b> tag
        assert "<b>" not in segments[0]
        # Second segment should start with the <b> tag
        assert segments[1].startswith("<b>")

    def test_block_tag_close_reopen(self):
        """Block tag at boundary: should close and reopen."""
        # Build: <pre> with content that exceeds MSG_LIMIT
        inner = "x" * (MSG_LIMIT + 100)
        html = f"<pre>{inner}</pre>"
        segments = _split_html(html)
        assert len(segments) >= 2
        # First segment should end with </pre>
        assert segments[0].endswith("</pre>")
        # Second segment should start with <pre>
        assert segments[1].startswith("<pre>")

    def test_no_tags_newline_split(self):
        """Without tags, should still prefer newline splits."""
        first = "a" * (MSG_LIMIT - 100) + "\n"
        second = "b" * 200
        html = first + second
        segments = _split_html(html)
        assert len(segments) == 2
        assert segments[0] == first

    def test_nested_block_and_inline(self):
        """<pre> containing <code> — inline inside block uses close/reopen (not backtrack)."""
        inner = "y" * (MSG_LIMIT + 100)
        html = f'<pre><code class="language-python">{inner}</code></pre>'
        segments = _split_html(html)
        assert len(segments) >= 2
        # Both <code> and <pre> should be closed at end of first segment
        assert "</code></pre>" in segments[0]
        # And reopened at start of second segment
        assert segments[1].startswith('<pre><code class="language-python">')


class TestStreamWriterHtmlFallback:
    async def test_html_conversion_failure_falls_back_to_plain(self):
        """If telegram_format raises, send as plain text (no parse_mode)."""
        bot = AsyncMock()
        writer = StreamWriter(bot, chat_id=123, thread_id=456, draft_id=1)
        writer._buffer = "Hello **world**"

        with patch("tg_acp.stream_writer.telegram_format", side_effect=Exception("boom")):
            await writer.finalize()

        bot.send_message.assert_called_once_with(
            chat_id=123, text="Hello **world**", message_thread_id=456
        )

    async def test_telegram_rejects_html_retries_plain(self):
        """If Telegram rejects HTML, retry as plain text."""
        bot = AsyncMock()
        # First call (HTML) raises, second call (plain) succeeds
        bot.send_message.side_effect = [Exception("Bad Request: can't parse"), None]
        writer = StreamWriter(bot, chat_id=123, thread_id=456, draft_id=1)
        writer._buffer = "Hello **world**"
        await writer.finalize()
        assert bot.send_message.call_count == 2
        # Second call should NOT have parse_mode
        second_call = bot.send_message.call_args_list[1]
        assert "parse_mode" not in second_call.kwargs

    async def test_telegram_rejects_html_and_plain_does_not_raise(self):
        """If both HTML and plain-text sends fail, finalize still returns without raising."""
        bot = AsyncMock()
        bot.send_message.side_effect = Exception("network down")
        writer = StreamWriter(bot, chat_id=123, thread_id=456, draft_id=1)
        writer._buffer = "Hello **world**"
        result = await writer.finalize()
        assert result == []
        # HTML attempt + plain-text retry = 2 calls
        assert bot.send_message.call_count == 2

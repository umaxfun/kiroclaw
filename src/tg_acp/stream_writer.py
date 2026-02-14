"""C4: Stream Writer — accumulate chunks, stream via sendMessageDraft, finalize via sendMessage."""

from __future__ import annotations

import logging
import random
import re
import time

from aiogram import Bot

try:
    from chatgpt_md_converter import telegram_format
except ImportError:
    telegram_format = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

WINDOW_SIZE = 4000  # chars — margin below Telegram's 4096 limit
DRAFT_THROTTLE_S = 0.5  # minimum seconds between sendMessageDraft calls
MSG_LIMIT = 4096  # Telegram message character limit
NEWLINE_SEARCH_TAIL = 200  # look for newline in last N chars when splitting

# Tags produced by chatgpt-md-converter
INLINE_TAGS = {"b", "i", "code", "u", "s", "a"}
BLOCK_TAGS = {"pre", "blockquote"}

# Regex to find HTML open/close tags (non-self-closing)
_TAG_RE = re.compile(r"<(/?)(\w+)(?:\s[^>]*)?>")


def _split_message(text: str) -> list[str]:
    """Split plain text into segments of <= MSG_LIMIT chars.

    Prefers splitting at the last newline within NEWLINE_SEARCH_TAIL chars
    of the boundary. Falls back to a hard break at MSG_LIMIT.
    """
    if len(text) <= MSG_LIMIT:
        return [text]

    segments: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= MSG_LIMIT:
            segments.append(remaining)
            break

        boundary = MSG_LIMIT
        search_start = boundary - NEWLINE_SEARCH_TAIL
        newline_pos = remaining.rfind("\n", search_start, boundary)
        if newline_pos > 0:
            boundary = newline_pos + 1

        segments.append(remaining[:boundary])
        remaining = remaining[boundary:]

    return segments


def _find_split_point(html: str) -> tuple[int, list[tuple[str, int]]]:
    """Find the best split point in an HTML string that fits within MSG_LIMIT.

    Returns (split_position, open_tags_at_split).

    Strategy:
    1. Find a candidate boundary (newline-preferred or hard break at MSG_LIMIT).
    2. Check if any tags are unclosed at that boundary.
    3. For inline tags: backtrack to before the opening tag.
    4. For block tags: split at the candidate boundary (caller will close/reopen).
    """
    if len(html) <= MSG_LIMIT:
        return len(html), []

    # Step 1: candidate boundary (same logic as _split_message)
    boundary = MSG_LIMIT
    search_start = boundary - NEWLINE_SEARCH_TAIL
    newline_pos = html.rfind("\n", search_start, boundary)
    if newline_pos > 0:
        boundary = newline_pos + 1

    # Step 2: find unclosed tags at boundary
    open_tags = _open_tags_at(html[:boundary])

    if not open_tags:
        return boundary, []

    # Step 3: check if the innermost unclosed tag is inline AND not nested in a block
    has_block = any(name in BLOCK_TAGS for name, _ in open_tags)
    innermost_tag_name, innermost_tag_pos = open_tags[-1]

    if innermost_tag_name in INLINE_TAGS and not has_block:
        # Backtrack to before this inline tag opens
        if innermost_tag_pos > 0:
            return innermost_tag_pos, []
        # Edge case: tag starts at position 0 — can't backtrack, fall through to block logic

    # Step 4: block tag or can't backtrack — split at boundary (caller handles close/reopen)
    return boundary, open_tags


def _open_tags_at(html: str) -> list[tuple[str, int]]:
    """Return a stack of (tag_name, open_position) for tags that are open at the end of html.

    Assumes well-formed (properly nested) HTML, as produced by chatgpt-md-converter.
    Misnested tags like ``<b><i></b></i>`` are not handled.
    """
    stack: list[tuple[str, int]] = []
    for m in _TAG_RE.finditer(html):
        is_close = m.group(1) == "/"
        tag_name = m.group(2).lower()
        if tag_name not in INLINE_TAGS and tag_name not in BLOCK_TAGS:
            continue
        if is_close:
            # Pop matching open tag (if any)
            if stack and stack[-1][0] == tag_name:
                stack.pop()
        else:
            stack.append((tag_name, m.start()))
    return stack


def _close_tags(open_tags: list[tuple[str, int]]) -> str:
    """Generate closing tags for the given open tag stack (innermost first)."""
    return "".join(f"</{name}>" for name, _ in reversed(open_tags))


def _reopen_tags(open_tags: list[tuple[str, int]], original_html: str) -> str:
    """Regenerate opening tags (with original attributes) for the given stack."""
    parts = []
    for name, pos in open_tags:
        # Extract the original opening tag from the source HTML
        m = _TAG_RE.match(original_html, pos)
        if m:
            parts.append(m.group(0))
        else:
            parts.append(f"<{name}>")
    return "".join(parts)


def _split_html(html: str) -> list[str]:
    """Split HTML into segments of <= MSG_LIMIT chars, preserving tag integrity.

    - Inline tags (<b>, <i>, <code>, <u>, <s>, <a>): backtrack before the opening tag.
    - Block tags (<pre>, <blockquote>): close at split point, reopen at next segment.
    """
    if len(html) <= MSG_LIMIT:
        return [html]

    segments: list[str] = []
    remaining = html

    while remaining:
        if len(remaining) <= MSG_LIMIT:
            segments.append(remaining)
            break

        split_pos, open_tags = _find_split_point(remaining)

        # Safety: if split_pos is 0 (can't make progress), force a hard break
        if split_pos == 0:
            split_pos = MSG_LIMIT
            open_tags = _open_tags_at(remaining[:split_pos])

        segment = remaining[:split_pos]
        rest = remaining[split_pos:]

        if open_tags:
            segment += _close_tags(open_tags)
            rest = _reopen_tags(open_tags, remaining) + rest

        segments.append(segment)
        remaining = rest

    return segments


def _sliding_window(buffer: str) -> str:
    """Return the tail of buffer that fits in a draft message."""
    if len(buffer) <= WINDOW_SIZE:
        return buffer
    return "…\n" + buffer[-WINDOW_SIZE:]


def random_draft_id() -> int:
    """Generate a random positive draft_id for sendMessageDraft."""
    return random.randint(1, 2**31 - 1)


class StreamWriter:
    """Accumulate streaming chunks and deliver them to Telegram.

    Usage:
        writer = StreamWriter(bot, chat_id, thread_id)
        await writer.write_chunk("Hello ")
        await writer.write_chunk("world!")
        file_paths = await writer.finalize()
    """

    def __init__(self, bot: Bot, chat_id: int, thread_id: int, draft_id: int | None = None) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._thread_id = thread_id
        self._draft_id = draft_id or random_draft_id()
        self._buffer = ""
        self._last_draft_time = 0.0
        self._cancelled = False

    @property
    def buffer(self) -> str:
        return self._buffer

    async def write_chunk(self, text: str) -> None:
        """Append text and send a draft update (throttled, best-effort)."""
        if self._cancelled:
            return

        self._buffer += text
        draft_text = _sliding_window(self._buffer)

        now = time.monotonic()
        if now - self._last_draft_time < DRAFT_THROTTLE_S:
            return  # throttle

        try:
            await self._bot.send_message_draft(
                chat_id=self._chat_id,
                message_thread_id=self._thread_id,
                draft_id=self._draft_id,
                text=draft_text,
            )
        except Exception as exc:
            retry_after = getattr(exc, "retry_after", None)
            if retry_after:
                self._last_draft_time = now + retry_after
                logger.debug("sendMessageDraft rate-limited, backing off %ss", retry_after)
            else:
                logger.warning("sendMessageDraft failed (non-fatal)", exc_info=True)

        self._last_draft_time = now

    async def finalize(self) -> list[str]:
        """Send the final message(s) with Markdown→HTML conversion.

        Returns file paths (empty in Unit 3).
        """
        if self._cancelled:
            return []
        if not self._buffer:
            return []

        # Signal completion in draft (best-effort)
        try:
            await self._bot.send_message_draft(
                chat_id=self._chat_id,
                message_thread_id=self._thread_id,
                draft_id=self._draft_id,
                text="…",
            )
        except Exception:
            pass

        # Convert Markdown → Telegram HTML
        use_html = True
        try:
            if telegram_format is None:
                raise ImportError("chatgpt-md-converter not installed")
            final_text = telegram_format(self._buffer)
        except Exception:
            logger.warning("Markdown→HTML conversion failed, falling back to plain text", exc_info=True)
            final_text = self._buffer
            use_html = False

        # Split and send
        if use_html:
            segments = _split_html(final_text)
        else:
            segments = _split_message(final_text)

        for segment in segments:
            try:
                if use_html:
                    await self._bot.send_message(
                        chat_id=self._chat_id,
                        text=segment,
                        message_thread_id=self._thread_id,
                        parse_mode="HTML",
                    )
                else:
                    await self._bot.send_message(
                        chat_id=self._chat_id,
                        text=segment,
                        message_thread_id=self._thread_id,
                    )
            except Exception:
                if use_html:
                    # Telegram rejected HTML — retry this segment as plain text
                    logger.warning("Telegram rejected HTML segment, retrying as plain text", exc_info=True)
                    try:
                        await self._bot.send_message(
                            chat_id=self._chat_id,
                            text=segment,
                            message_thread_id=self._thread_id,
                        )
                    except Exception:
                        logger.error("Failed to send segment even as plain text", exc_info=True)
                else:
                    logger.error("Failed to send plain text segment", exc_info=True)

        return []

    def cancel(self) -> None:
        """Cancel streaming. Subsequent write_chunk/finalize become no-ops."""
        self._cancelled = True

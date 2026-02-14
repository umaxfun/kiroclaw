"""C4: Stream Writer — accumulate chunks, stream via sendMessageDraft, finalize via sendMessage."""

from __future__ import annotations

import logging
import random
import time

from aiogram import Bot

logger = logging.getLogger(__name__)

WINDOW_SIZE = 4000  # chars — margin below Telegram's 4096 limit
DRAFT_THROTTLE_S = 0.5  # minimum seconds between sendMessageDraft calls
MSG_LIMIT = 4096  # Telegram message character limit
NEWLINE_SEARCH_TAIL = 200  # look for newline in last N chars when splitting


def _split_message(text: str) -> list[str]:
    """Split text into segments of <= MSG_LIMIT chars.

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

        # Try to find a newline near the boundary
        boundary = MSG_LIMIT
        search_start = boundary - NEWLINE_SEARCH_TAIL
        newline_pos = remaining.rfind("\n", search_start, boundary)
        if newline_pos > 0:
            boundary = newline_pos + 1  # include the newline in this segment

        segments.append(remaining[:boundary])
        remaining = remaining[boundary:]

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
            # On rate limit, back off by the requested amount
            retry_after = getattr(exc, "retry_after", None)
            if retry_after:
                self._last_draft_time = now + retry_after
                logger.debug("sendMessageDraft rate-limited, backing off %ss", retry_after)
            else:
                logger.warning("sendMessageDraft failed (non-fatal)", exc_info=True)

        self._last_draft_time = now

    async def finalize(self) -> list[str]:
        """Send the final message(s). Returns file paths (empty in Unit 3)."""
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

        # Send final message(s) — this clears the draft automatically
        for segment in _split_message(self._buffer):
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=segment,
                message_thread_id=self._thread_id,
            )

        return []

    def cancel(self) -> None:
        """Cancel streaming. Subsequent write_chunk/finalize become no-ops."""
        self._cancelled = True

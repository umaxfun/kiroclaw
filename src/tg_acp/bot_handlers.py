"""C6: Bot Handlers — aiogram router with /start, /model, and message handler."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from tg_acp.acp_client import TURN_END
from tg_acp.config import Config
from tg_acp.file_handler import FileHandler
from tg_acp.process_pool import ProcessPool, ProcessSlot, QueuedRequest
from tg_acp.session_store import SessionStore, create_workspace_dir
from tg_acp.stream_writer import StreamWriter

logger = logging.getLogger(__name__)

# Brief pause before processing a queued request on a reused slot.
# Lets kiro-cli flush residual I/O from the previous session.
QUEUE_HANDOFF_DELAY_S = 0.1


def _resolve_file_path(path: str, workspace_path: str) -> Path:
    """Resolve a file path against the workspace if it's relative."""
    p = Path(path)
    if not p.is_absolute():
        return (Path(workspace_path) / p).resolve()
    return p.resolve()


router = Router(name="bot_handlers")


class BotContext:
    """Shared state injected into handlers at startup."""

    def __init__(
        self,
        config: Config,
        store: SessionStore,
        pool: ProcessPool,
        bot: Bot,
    ) -> None:
        self.config = config
        self.store = store
        self.pool = pool
        self.bot = bot


# Will be set by main.py before polling starts
_ctx: BotContext | None = None

# prevent fire-and-forget tasks from being GC'd before completion
_background_tasks: set[asyncio.Task] = set()


def get_background_tasks() -> set[asyncio.Task]:
    """Return the set of active background tasks (for shutdown coordination)."""
    return _background_tasks


def setup(ctx: BotContext) -> None:
    """Inject shared context. Must be called before dispatcher starts."""
    global _ctx
    _ctx = ctx


def _get_ctx() -> BotContext:
    assert _ctx is not None, "BotContext not initialized — call setup() first"
    return _ctx


async def _send_access_denied(message: Message, user_id: int) -> None:
    """Send standardized rejection message with user's Telegram ID."""
    await message.answer(
        f"⛔ Access restricted.\n\n"
        f"Your Telegram ID: {user_id}\n\n"
        f"To get access, ask the administrator to add your ID to the allowed list."
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Handle /start command."""
    ctx = _get_ctx()
    user_id = message.from_user.id if message.from_user else None

    if user_id is not None and not ctx.config.is_user_allowed(user_id):
        await message.answer(
            "I'm a Kiro-powered assistant. Send me a message in any forum topic and I'll respond.\n\n"
            f"⛔ Your access is currently restricted.\n"
            f"Your Telegram ID: {user_id}\n"
            f"To get access, ask the administrator to add your ID to the allowed list."
        )
        return

    await message.answer(
        "I'm a Kiro-powered assistant. Send me a message in any forum topic and I'll respond.",
    )


# ---------------------------------------------------------------------------
# /model command
# ---------------------------------------------------------------------------

AVAILABLE_MODELS = [
    "auto",
    "claude-opus-4.6",
    "claude-opus-4.5",
    "claude-sonnet-4.5",
    "claude-sonnet-4",
    "claude-haiku-4.5",
]


@router.message(Command("model"))
async def cmd_model(message: Message) -> None:
    """Handle /model command — list or set the model for this thread."""
    if message.from_user is None or message.message_thread_id is None:
        return

    ctx = _get_ctx()
    user_id = message.from_user.id
    thread_id = message.message_thread_id

    if not ctx.config.is_user_allowed(user_id):
        await _send_access_denied(message, user_id)
        return

    # Extract args: text after "/model "
    raw = (message.text or "").strip()
    args = raw[len("/model"):].strip() if raw.lower().startswith("/model") else ""

    if not args:
        # Display model list with current selection marked
        current = ctx.store.get_model(user_id, thread_id)
        lines = ["Available models:"]
        for model in AVAILABLE_MODELS:
            marker = "✓" if model == current else "•"
            lines.append(f"  {marker} {model}")
        lines.append("\nUse /model <name> to change.")
        await message.answer("\n".join(lines))
        return

    model_name = args.lower().strip()
    if model_name not in AVAILABLE_MODELS:
        await message.answer(
            f"Unknown model: {model_name}\nAvailable: {', '.join(AVAILABLE_MODELS)}",
        )
        return

    # Store in SQLite (always)
    ctx.store.set_model(user_id, thread_id, model_name)

    # Try to apply immediately via pool.
    # No in_flight.track() here — this is a quick load+set_model, not a streaming prompt.
    record = ctx.store.get_session(user_id, thread_id)
    if record is not None:
        slot = await ctx.pool.acquire(thread_id, user_id)
        if slot is not None:
            try:
                await slot.client.session_load(record.session_id, cwd=record.workspace_path)
                await slot.client.session_set_model(record.session_id, model_name)
            except Exception:
                logger.warning(
                    "session/set_model failed for %s — model stored in SQLite, will apply on next load",
                    record.session_id,
                    exc_info=True,
                )
            finally:
                await ctx.pool.release(slot, record.session_id, thread_id)

    await message.answer(f"Model set to {model_name} for this thread.")


# ---------------------------------------------------------------------------
# Message handling — thin wrapper + internal core logic
# ---------------------------------------------------------------------------


@router.message()
async def handle_message(message: Message) -> None:
    """Thin wrapper — extract fields, download files, delegate to internal."""
    logger.debug(
        "handle_message: chat_id=%s thread_id=%s from_user=%s text=%r content_type=%s",
        message.chat.id,
        message.message_thread_id,
        message.from_user.id if message.from_user else None,
        (message.text or "")[:80] or None,
        message.content_type,
    )
    if message.from_user is None:
        return
    thread_id = message.message_thread_id
    if thread_id is None:
        logger.debug("Skipping: no thread_id (not a forum topic message)")
        return

    # Determine content: text, file, or both — before allowlist so service
    # messages (FORUM_TOPIC_CREATED, etc.) are silently dropped without
    # triggering a rejection reply.
    has_file = bool(
        message.document or message.photo or message.audio
        or message.voice or message.video or message.video_note
        or message.sticker
    )
    has_text = message.text is not None or message.caption is not None
    text_content = message.text or message.caption or ""

    if not has_file and not has_text:
        return  # nothing to process (service message)

    ctx = _get_ctx()
    user_id = message.from_user.id

    # Allowlist gate — first business logic check after content filter
    if not ctx.config.is_user_allowed(user_id):
        await _send_access_denied(message, user_id)
        return

    chat_id = message.chat.id

    # Compute workspace path (deterministic, idempotent)
    workspace_path = create_workspace_dir(
        ctx.config.workspace_base_path, user_id, thread_id,
    )

    # Download inbound file before entering internal logic
    file_paths: list[str] = []
    if has_file:
        try:
            file_path = await FileHandler.download_to_workspace(message, workspace_path)
            file_paths.append(file_path)
        except Exception:
            logger.exception("Failed to download file from Telegram")
            try:
                await message.answer("Failed to download the file. Please try again.")
            except Exception:
                pass
            return

    await handle_message_internal(
        user_id, thread_id, text_content, file_paths,
        chat_id, workspace_path,
    )


async def handle_message_internal(
    user_id: int,
    thread_id: int,
    message_text: str,
    file_paths: list[str],
    chat_id: int,
    workspace_path: str,
    *,
    _preacquired_slot: ProcessSlot | None = None,
) -> None:
    """Core processing logic — used by both handle_message and handle_queued_request.

    ``thread_id`` serves double duty: it is both the internal thread key and the
    Telegram ``message_thread_id`` used when sending replies.

    ``_preacquired_slot``: when called from the queue-drain path, the slot is
    already acquired atomically by ``release_and_dequeue``.  Passing it here
    avoids a second ``acquire()`` call (and the race that comes with it).
    """
    ctx = _get_ctx()
    bot = ctx.bot
    message_thread_id = thread_id

    # Acquire slot from pool (or use pre-acquired one from queue drain)
    preacquired = _preacquired_slot is not None
    acquired = _preacquired_slot or await ctx.pool.acquire(thread_id, user_id)
    if acquired is None:
        # All busy, at max — enqueue for later processing
        logger.info(
            "[thread=%s] Pool full — enqueuing request (user=%s)", thread_id, user_id,
        )
        ctx.pool.request_queue.enqueue(
            QueuedRequest(
                thread_id=thread_id,
                user_id=user_id,
                message_text=message_text,
                files=file_paths,
                chat_id=chat_id,
                workspace_path=workspace_path,
            )
        )
        return
    slot = acquired

    logger.info(
        "[thread=%s] Acquired slot %s (preacquired=%s)", thread_id, slot.slot_id, preacquired,
    )

    # Track in-flight (NEW cancel_event for this request)
    cancel_event = ctx.pool.in_flight.track(thread_id, slot.slot_id)

    session_id: str | None = None
    file_results: list[tuple[str, str]] = []
    # NOTE: This try/finally MUST only wrap code that runs after a slot is
    # acquired.  The early-return path (pool full → enqueue) exits before
    # reaching here, so the finally block correctly never fires without a slot.
    try:
        # Session lookup / create
        record = ctx.store.get_session(user_id, thread_id)

        if record is None:
            session_id = await slot.client.session_new(cwd=workspace_path)
            ctx.store.upsert_session(user_id, thread_id, session_id, workspace_path)
        else:
            session_id = record.session_id
            try:
                await slot.client.session_load(session_id, cwd=workspace_path)
            except RuntimeError:
                logger.error(
                    "session/load failed for %s on slot %s — refusing to create "
                    "new session (would destroy conversation history)",
                    session_id, slot.slot_id, exc_info=True,
                )
                try:
                    await bot.send_message(
                        chat_id,
                        "Session is temporarily busy. Please try again in a moment.",
                        message_thread_id=message_thread_id,
                    )
                except Exception:
                    logger.warning("Failed to send session-busy notification")
                return

        # Build prompt content
        if file_paths:
            content: list[dict[str, str]] = [
                {"type": "text", "text": f"User sent a file: {fp}"} for fp in file_paths
            ]
            if message_text:
                content.append({"type": "text", "text": message_text})
        else:
            content = [{"type": "text", "text": message_text}]

        # Stream response with cancel detection
        writer = StreamWriter(bot, chat_id, message_thread_id)
        cancelled = False

        try:
            async for update in slot.client.session_prompt(session_id, content):
                if cancel_event.is_set():
                    logger.info("[thread=%s slot=%s] Cancel event set — aborting stream", thread_id, slot.slot_id)
                    await slot.client.session_cancel(session_id)
                    writer.cancel()
                    cancelled = True
                    break

                update_type = update.get("sessionUpdate", "")
                if update_type == "agent_message_chunk":
                    chunk_content = update.get("content", {})
                    if chunk_content.get("type") == "text":
                        await writer.write_chunk(chunk_content["text"])
                elif update_type == TURN_END:
                    logger.info(
                        "[thread=%s slot=%s] TURN_END — finalizing (buffer=%d chars)",
                        thread_id, slot.slot_id, len(writer.buffer),
                    )
                    file_results = await writer.finalize()
                    break
            else:
                # Stream ended without TURN_END
                logger.info(
                    "[thread=%s slot=%s] Stream ended without TURN_END (cancelled=%s, buffer=%d chars)",
                    thread_id, slot.slot_id, cancelled, len(writer.buffer),
                )
                if not cancelled:
                    file_results = await writer.finalize()
        except Exception:
            logger.exception("[thread=%s slot=%s] Error during ACP prompt", thread_id, slot.slot_id)
            try:
                await bot.send_message(
                    chat_id,
                    "Something went wrong. Please try again.",
                    message_thread_id=message_thread_id,
                )
            except Exception:
                logger.exception("Failed to send error message to user")
            return

        if cancelled:
            return

        # Process outbound files (BR-15 #5–#10)
        missing_files: list[tuple[str, str]] = []
        for path, description in file_results:
            resolved = _resolve_file_path(path, workspace_path)
            if not FileHandler.validate_path(str(resolved), workspace_path):
                logger.warning("Path traversal blocked: %s", path)
                continue
            if not resolved.exists():
                missing_files.append((path, description))
                continue
            try:
                await FileHandler.send_file(
                    bot, chat_id, message_thread_id, str(resolved), description or None,
                )
            except Exception:
                logger.exception("Failed to send file %s", path)

        # Missing file retry — max once per turn (BR-15 #7–#8)
        if missing_files:
            retry_prompt = "The following files were not found:\n"
            for path, _desc in missing_files:
                retry_prompt += f"- {path}\n"
            retry_prompt += "Please check the paths and try again."

            writer2 = StreamWriter(bot, chat_id, message_thread_id)
            retry_cancelled = False
            try:
                retry_file_results: list[tuple[str, str]] = []
                async for update in slot.client.session_prompt(
                    session_id,
                    [{"type": "text", "text": retry_prompt}],
                ):
                    if cancel_event.is_set():
                        await slot.client.session_cancel(session_id)
                        writer2.cancel()
                        retry_cancelled = True
                        break

                    update_type = update.get("sessionUpdate", "")
                    if update_type == "agent_message_chunk":
                        chunk_content = update.get("content", {})
                        if chunk_content.get("type") == "text":
                            await writer2.write_chunk(chunk_content["text"])
                    elif update_type == TURN_END:
                        retry_file_results = await writer2.finalize()
                        break
                else:
                    if not retry_cancelled:
                        retry_file_results = await writer2.finalize()

                if not retry_cancelled:
                    for path, description in retry_file_results:
                        resolved = _resolve_file_path(path, workspace_path)
                        if (
                            FileHandler.validate_path(str(resolved), workspace_path)
                            and resolved.exists()
                        ):
                            try:
                                await FileHandler.send_file(
                                    bot, chat_id, message_thread_id,
                                    str(resolved), description or None,
                                )
                            except Exception:
                                logger.exception("Failed to send retry file %s", path)
            except Exception:
                logger.exception("Error during missing-file retry prompt")

    finally:
        # Atomically release slot and grab next queued request (no race)
        logger.info("[thread=%s slot=%s] Releasing slot", thread_id, slot.slot_id)
        next_request, handoff_slot = await ctx.pool.release_and_dequeue(
            slot, session_id, thread_id,
        )
        if next_request is not None and handoff_slot is not None:
            logger.info(
                "[thread=%s] Dequeued next request for thread=%s on slot=%s",
                thread_id, next_request.thread_id, handoff_slot.slot_id,
            )
            try:
                task = asyncio.create_task(
                    _handle_queued_request(next_request, handoff_slot),
                )
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
            except Exception as e:
                # Task creation failed - release the slot and log error
                logger.error(
                    "[thread=%s] Failed to create background task for queued request: %s",
                    next_request.thread_id, e
                )
                await ctx.pool.release(handoff_slot, next_request.session_id, next_request.thread_id)


async def _handle_queued_request(request: QueuedRequest, slot: ProcessSlot) -> None:
    """Process a queued request using a pre-acquired slot from release_and_dequeue.

    A small delay lets the kiro-cli process flush residual I/O from the
    previous session before we load a new one on the same slot.
    """
    await asyncio.sleep(QUEUE_HANDOFF_DELAY_S)
    try:
        await handle_message_internal(
            user_id=request.user_id,
            thread_id=request.thread_id,
            message_text=request.message_text,
            file_paths=request.files,
            chat_id=request.chat_id,
            workspace_path=request.workspace_path,
            _preacquired_slot=slot,
        )
    except Exception:
        logger.exception(
            "Unhandled error processing queued request for thread %s",
            request.thread_id,
        )

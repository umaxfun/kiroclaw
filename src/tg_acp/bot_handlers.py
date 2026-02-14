"""C6: Bot Handlers — aiogram router with /start, /model, and message handler."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from tg_acp.acp_client import ACPClient, TURN_END
from tg_acp.config import Config
from tg_acp.file_handler import FileHandler
from tg_acp.session_store import SessionStore, create_workspace_dir
from tg_acp.stream_writer import StreamWriter

logger = logging.getLogger(__name__)


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
        client: ACPClient,
    ) -> None:
        self.config = config
        self.store = store
        self.client = client
        self.client_lock = asyncio.Lock()


# Will be set by main.py before polling starts
_ctx: BotContext | None = None


def setup(ctx: BotContext) -> None:
    """Inject shared context. Must be called before dispatcher starts."""
    global _ctx
    _ctx = ctx


def _get_ctx() -> BotContext:
    assert _ctx is not None, "BotContext not initialized — call setup() first"
    return _ctx


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Handle /start command."""
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

    # Store in SQLite
    ctx.store.set_model(user_id, thread_id, model_name)

    # Call session/set_model immediately if session exists (BR-16 #5)
    async with ctx.client_lock:
        record = ctx.store.get_session(user_id, thread_id)
        if record is not None:
            try:
                if not ctx.client.is_alive():
                    ctx.client = await ACPClient.spawn(
                        ctx.config.kiro_agent_name, ctx.config.log_level
                    )
                    await ctx.client.initialize()
                await ctx.client.session_load(record.session_id, cwd=record.workspace_path)
                await ctx.client.session_set_model(record.session_id, model_name)
            except Exception:
                logger.warning(
                    "session/set_model failed for %s — model stored in SQLite, will apply on next load",
                    record.session_id,
                    exc_info=True,
                )

    await message.answer(
        f"Model set to {model_name} for this thread.",
    )


@router.message()
async def handle_message(message: Message) -> None:
    """Handle text and file messages — session lookup, ACP prompt, streaming response."""
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

    # Determine content: text, file, or both
    has_file = bool(
        message.document or message.photo or message.audio
        or message.voice or message.video or message.video_note
        or message.sticker
    )
    has_text = message.text is not None or message.caption is not None
    text_content = message.text or message.caption or ""

    if not has_file and not has_text:
        return  # nothing to process

    ctx = _get_ctx()
    user_id = message.from_user.id
    chat_id = message.chat.id

    async with ctx.client_lock:
        # Respawn if process died
        if not ctx.client.is_alive():
            logger.warning("ACP Client dead — respawning...")
            ctx.client = await ACPClient.spawn(
                ctx.config.kiro_agent_name, ctx.config.log_level
            )
            await ctx.client.initialize()

        # Session lookup / create
        record = ctx.store.get_session(user_id, thread_id)

        if record is None:
            workspace_path = create_workspace_dir(
                ctx.config.workspace_base_path, user_id, thread_id
            )
            session_id = await ctx.client.session_new(cwd=workspace_path)
            ctx.store.upsert_session(user_id, thread_id, session_id, workspace_path)
        else:
            session_id = record.session_id
            workspace_path = record.workspace_path
            try:
                await ctx.client.session_load(session_id, cwd=workspace_path)
            except RuntimeError:
                logger.warning(
                    "session/load failed for %s — creating new session", session_id
                )
                session_id = await ctx.client.session_new(cwd=workspace_path)
                ctx.store.upsert_session(user_id, thread_id, session_id, workspace_path)

        # Download inbound file before prompting (BR-14 #3, BR-17 #3)
        if has_file:
            try:
                file_path = await FileHandler.download_to_workspace(message, workspace_path)
            except Exception:
                logger.exception("Failed to download file from Telegram")
                try:
                    await message.answer(
                        "Failed to download the file. Please try again.",
                    )
                except Exception:
                    pass
                return

            # Build mixed prompt content (BR-14 #6–#8)
            content: list[dict[str, str]] = [
                {"type": "text", "text": f"User sent a file: {file_path}"},
            ]
            if text_content:
                content.append({"type": "text", "text": text_content})
        else:
            content = [{"type": "text", "text": text_content}]

        # Stream response
        writer = StreamWriter(message.bot, chat_id, thread_id)

        try:
            async for update in ctx.client.session_prompt(session_id, content):
                update_type = update.get("sessionUpdate", "")
                if update_type == "agent_message_chunk":
                    chunk_content = update.get("content", {})
                    if chunk_content.get("type") == "text":
                        await writer.write_chunk(chunk_content["text"])
                elif update_type == TURN_END:
                    file_results = await writer.finalize()
                    break
            else:
                # Stream ended without TURN_END
                file_results = await writer.finalize()
        except Exception:
            logger.exception("Error during ACP prompt")
            try:
                await message.answer(
                    "Something went wrong. Please try again.",
                )
            except Exception:
                logger.exception("Failed to send error message to user")
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
                    message.bot, chat_id, thread_id, str(resolved), description or None
                )
            except Exception:
                logger.exception("Failed to send file %s", path)

        # Missing file retry — max once per turn (BR-15 #7–#8)
        if missing_files:
            retry_prompt = "The following files were not found:\n"
            for path, _desc in missing_files:
                retry_prompt += f"- {path}\n"
            retry_prompt += "Please check the paths and try again."

            writer2 = StreamWriter(message.bot, chat_id, thread_id)
            try:
                retry_file_results: list[tuple[str, str]] = []
                async for update in ctx.client.session_prompt(
                    session_id,
                    [{"type": "text", "text": retry_prompt}],
                ):
                    update_type = update.get("sessionUpdate", "")
                    if update_type == "agent_message_chunk":
                        chunk_content = update.get("content", {})
                        if chunk_content.get("type") == "text":
                            await writer2.write_chunk(chunk_content["text"])
                    elif update_type == TURN_END:
                        retry_file_results = await writer2.finalize()
                        break
                else:
                    # Stream ended without TURN_END
                    retry_file_results = await writer2.finalize()

                # Send files from retry (no further retries)
                for path, description in retry_file_results:
                    resolved = _resolve_file_path(path, workspace_path)
                    if (
                        FileHandler.validate_path(str(resolved), workspace_path)
                        and resolved.exists()
                    ):
                        try:
                            await FileHandler.send_file(
                                message.bot, chat_id, thread_id,
                                str(resolved), description or None,
                            )
                        except Exception:
                            logger.exception("Failed to send retry file %s", path)
            except Exception:
                logger.exception("Error during missing-file retry prompt")

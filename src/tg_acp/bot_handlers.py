"""C6: Bot Handlers — aiogram router with /start and text message handler."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from tg_acp.acp_client import ACPClient, TURN_END
from tg_acp.config import Config
from tg_acp.session_store import SessionStore, create_workspace_dir
from tg_acp.stream_writer import StreamWriter

logger = logging.getLogger(__name__)

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
        message_thread_id=message.message_thread_id,
    )


@router.message()
async def handle_message(message: Message) -> None:
    """Handle text messages — session lookup, ACP prompt, streaming response."""
    logger.debug(
        "handle_message: chat_id=%s thread_id=%s from_user=%s text=%r content_type=%s",
        message.chat.id,
        message.message_thread_id,
        message.from_user.id if message.from_user else None,
        message.text[:80] if message.text else None,
        message.content_type,
    )
    if message.text is None or message.from_user is None:
        logger.debug("Skipping: text=%s from_user=%s", message.text is None, message.from_user is None)
        return
    thread_id = message.message_thread_id
    if thread_id is None:
        logger.debug("Skipping: no thread_id (not a forum topic message)")
        return

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

        # Stream response
        writer = StreamWriter(message.bot, chat_id, thread_id)

        try:
            async for update in ctx.client.session_prompt(
                session_id,
                [{"type": "text", "text": message.text}],
            ):
                update_type = update.get("sessionUpdate", "")
                if update_type == "agent_message_chunk":
                    content = update.get("content", {})
                    if content.get("type") == "text":
                        await writer.write_chunk(content["text"])
                elif update_type == TURN_END:
                    await writer.finalize()
                    break
        except Exception:
            logger.exception("Error during ACP prompt")
            try:
                await message.answer(
                    "Something went wrong. Please try again.",
                    message_thread_id=thread_id,
                )
            except Exception:
                logger.exception("Failed to send error message to user")

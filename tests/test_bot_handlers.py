"""Unit tests for bot_handlers — session lifecycle, respawn, error paths."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tg_acp.acp_client import TURN_END
from tg_acp.bot_handlers import BotContext, _get_ctx, cmd_start, handle_message, setup
from tg_acp.config import Config
from tg_acp.session_store import SessionRecord, SessionStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides: str) -> Config:
    defaults = dict(
        bot_token="tok-123",
        workspace_base_path="/tmp/ws",
        max_processes=5,
        idle_timeout_seconds=30,
        kiro_agent_name="tg-acp",
        log_level="INFO",
        kiro_config_path="./kiro-config/",
    )
    defaults.update(overrides)
    return Config(**defaults)


def _make_message(
    text: str | None = "hello",
    user_id: int = 1,
    chat_id: int = 100,
    thread_id: int | None = 42,
) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.message_thread_id = thread_id
    msg.chat.id = chat_id
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    msg.bot = MagicMock()
    msg.bot.send_message_draft = AsyncMock()
    msg.bot.send_message = AsyncMock()
    return msg


def _make_ctx(
    client_alive: bool = True,
    existing_session: SessionRecord | None = None,
) -> BotContext:
    config = _make_config()
    store = MagicMock(spec=SessionStore)
    store.get_session.return_value = existing_session

    client = AsyncMock()
    client.is_alive.return_value = client_alive
    client.session_new = AsyncMock(return_value="new-session-id")
    client.session_load = AsyncMock()
    client.initialize = AsyncMock()

    ctx = BotContext(config=config, store=store, client=client)
    return ctx


async def _fake_prompt_stream(*_args, **_kwargs):
    """Yield a single text chunk then TURN_END."""
    yield {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Hi there"}}
    yield {"sessionUpdate": TURN_END, "content": None}


# ---------------------------------------------------------------------------
# Tests: setup / _get_ctx
# ---------------------------------------------------------------------------

class TestSetupAndContext:
    def test_get_ctx_before_setup_raises(self):
        import tg_acp.bot_handlers as mod
        old = mod._ctx
        try:
            mod._ctx = None
            with pytest.raises(AssertionError, match="BotContext not initialized"):
                _get_ctx()
        finally:
            mod._ctx = old

    def test_setup_sets_ctx(self):
        import tg_acp.bot_handlers as mod
        old = mod._ctx
        try:
            ctx = _make_ctx()
            setup(ctx)
            assert _get_ctx() is ctx
        finally:
            mod._ctx = old


# ---------------------------------------------------------------------------
# Tests: /start command
# ---------------------------------------------------------------------------

class TestCmdStart:
    @pytest.mark.asyncio
    async def test_start_replies(self):
        msg = _make_message()
        await cmd_start(msg)
        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "Kiro" in text


# ---------------------------------------------------------------------------
# Tests: handle_message — guard clauses
# ---------------------------------------------------------------------------

class TestHandleMessageGuards:
    @pytest.mark.asyncio
    async def test_no_text_returns_early(self):
        ctx = _make_ctx()
        setup(ctx)
        msg = _make_message(text=None)
        await handle_message(msg)
        ctx.client.session_prompt.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_user_returns_early(self):
        ctx = _make_ctx()
        setup(ctx)
        msg = _make_message()
        msg.from_user = None
        await handle_message(msg)
        ctx.client.session_prompt.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_thread_id_returns_early(self):
        ctx = _make_ctx()
        setup(ctx)
        msg = _make_message(thread_id=None)
        await handle_message(msg)
        ctx.client.session_prompt.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: handle_message — new session path
# ---------------------------------------------------------------------------

class TestNewSession:
    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    async def test_creates_new_session_when_none_exists(self, mock_cwd, mock_sw_cls):
        ctx = _make_ctx(existing_session=None)
        ctx.client.session_prompt = _fake_prompt_stream
        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)

        ctx.client.session_new.assert_awaited_once()
        ctx.store.upsert_session.assert_called_once_with(1, 42, "new-session-id", "/tmp/ws/1/42")
        mock_writer.write_chunk.assert_awaited_once_with("Hi there")
        mock_writer.finalize.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: handle_message — existing session path
# ---------------------------------------------------------------------------

class TestExistingSession:
    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir")
    async def test_loads_existing_session(self, mock_cwd, mock_sw_cls):
        record = SessionRecord(
            user_id=1, thread_id=42, session_id="existing-sid",
            workspace_path="/tmp/ws/1/42", model="auto",
        )
        ctx = _make_ctx(existing_session=record)
        ctx.client.session_prompt = _fake_prompt_stream
        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)

        ctx.client.session_load.assert_awaited_once_with("existing-sid", cwd="/tmp/ws/1/42")
        ctx.client.session_new.assert_not_awaited()
        mock_writer.finalize.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir")
    async def test_session_load_failure_creates_new(self, mock_cwd, mock_sw_cls):
        """If session/load raises, handler falls back to session_new."""
        record = SessionRecord(
            user_id=1, thread_id=42, session_id="stale-sid",
            workspace_path="/tmp/ws/1/42", model="auto",
        )
        ctx = _make_ctx(existing_session=record)
        ctx.client.session_load.side_effect = RuntimeError("session/load failed")
        ctx.client.session_prompt = _fake_prompt_stream
        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)

        ctx.client.session_new.assert_awaited_once()
        ctx.store.upsert_session.assert_called_once()
        mock_writer.finalize.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: handle_message — respawn on dead client
# ---------------------------------------------------------------------------

class TestClientRespawn:
    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    @patch("tg_acp.bot_handlers.ACPClient")
    async def test_respawns_dead_client(self, mock_acp_cls, mock_cwd, mock_sw_cls):
        config = _make_config()
        store = MagicMock(spec=SessionStore)
        store.get_session.return_value = None

        # The original client is dead — use a plain MagicMock so
        # is_alive() returns a bool, not a coroutine.
        dead_client = MagicMock()
        dead_client.is_alive.return_value = False

        # The replacement client returned by ACPClient.spawn()
        new_client = AsyncMock()
        new_client.is_alive.return_value = True
        new_client.initialize = AsyncMock()
        new_client.session_new = AsyncMock(return_value="respawned-sid")
        new_client.session_prompt = _fake_prompt_stream
        mock_acp_cls.spawn = AsyncMock(return_value=new_client)

        ctx = BotContext(config=config, store=store, client=dead_client)

        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)

        mock_acp_cls.spawn.assert_awaited_once_with(
            config.kiro_agent_name, config.log_level
        )
        new_client.initialize.assert_awaited_once()
        new_client.session_new.assert_awaited_once()
        mock_writer.finalize.assert_awaited_once()



# ---------------------------------------------------------------------------
# Tests: handle_message — error during prompt
# ---------------------------------------------------------------------------

class TestPromptError:
    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    async def test_prompt_error_sends_apology(self, mock_cwd, mock_sw_cls):
        ctx = _make_ctx(existing_session=None)

        async def _exploding_prompt(*_a, **_kw):
            raise RuntimeError("boom")
            yield  # noqa: unreachable — makes this an async generator

        ctx.client.session_prompt = _exploding_prompt
        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)

        msg.answer.assert_awaited_once()
        assert "wrong" in msg.answer.call_args[0][0].lower()

    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    async def test_prompt_error_swallows_send_failure(self, mock_cwd, mock_sw_cls):
        """If both the prompt AND the error message fail, no unhandled exception."""
        ctx = _make_ctx(existing_session=None)

        async def _exploding_prompt(*_a, **_kw):
            raise RuntimeError("boom")
            yield  # noqa

        ctx.client.session_prompt = _exploding_prompt
        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        msg.answer = AsyncMock(side_effect=Exception("Telegram API down"))

        # Should not raise
        await handle_message(msg)

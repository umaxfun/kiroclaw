"""Unit tests for bot_handlers — session lifecycle, pool acquire/release, error paths."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tg_acp.acp_client import TURN_END
from tg_acp.bot_handlers import (
    BotContext,
    get_background_tasks,
    _get_ctx,
    _handle_queued_request,
    cmd_start,
    handle_message,
    handle_message_internal,
    setup,
)
from tg_acp.config import Config
from tg_acp.process_pool import ProcessPool, ProcessSlot, QueuedRequest, SlotStatus
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
        allowed_telegram_ids=frozenset({1, 2, 3}),
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
    msg.caption = None
    msg.message_thread_id = thread_id
    msg.chat.id = chat_id
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    msg.content_type = "text"
    msg.bot = MagicMock()
    msg.bot.send_message_draft = AsyncMock()
    msg.bot.send_message = AsyncMock()
    # File attachment attributes must be explicitly None
    msg.document = None
    msg.photo = None
    msg.audio = None
    msg.voice = None
    msg.video = None
    msg.video_note = None
    msg.sticker = None
    return msg


def _make_mock_client(alive: bool = True) -> AsyncMock:
    """Create a mock ACPClient with standard methods."""
    client = AsyncMock()
    client.is_alive.return_value = alive
    client.session_new = AsyncMock(return_value="new-session-id")
    client.session_load = AsyncMock()
    client.session_cancel = AsyncMock()
    client.session_set_model = AsyncMock()
    client.initialize = AsyncMock()
    client.kill = AsyncMock()
    return client


def _make_mock_pool(client: AsyncMock | None = None) -> tuple[MagicMock, ProcessSlot, AsyncMock]:
    """Create a mock ProcessPool that returns a slot with the given client."""
    pool = MagicMock(spec=ProcessPool)
    mock_client = client or _make_mock_client()
    slot = ProcessSlot(
        slot_id=0, client=mock_client, status=SlotStatus.BUSY,
        last_used=0.0, session_id=None, thread_id=None,
    )
    pool.acquire = AsyncMock(return_value=slot)
    pool.release = AsyncMock()
    pool.release_and_dequeue = AsyncMock(return_value=(None, None))
    pool.in_flight = MagicMock()
    pool.in_flight.track = MagicMock(return_value=asyncio.Event())
    pool.in_flight.untrack = MagicMock()
    pool.request_queue = MagicMock()
    pool.request_queue.dequeue = MagicMock(return_value=None)
    return pool, slot, mock_client


def _make_ctx(
    existing_session: SessionRecord | None = None,
    mock_client: AsyncMock | None = None,
) -> tuple[BotContext, MagicMock, ProcessSlot, AsyncMock]:
    """Create a BotContext with mock pool. Returns (ctx, pool, slot, client)."""
    config = _make_config()
    store = MagicMock(spec=SessionStore)
    store.get_session.return_value = existing_session
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_message_draft = AsyncMock()

    pool, slot, client = _make_mock_pool(mock_client)
    ctx = BotContext(config=config, store=store, pool=pool, bot=bot)
    return ctx, pool, slot, client


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
            ctx, *_ = _make_ctx()
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
        ctx, *_ = _make_ctx()
        setup(ctx)
        msg = _make_message(user_id=1)  # user 1 is in allowlist
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
        ctx, pool, slot, client = _make_ctx()
        setup(ctx)
        msg = _make_message(text=None)
        await handle_message(msg)
        pool.acquire.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_user_returns_early(self):
        ctx, pool, slot, client = _make_ctx()
        setup(ctx)
        msg = _make_message()
        msg.from_user = None
        await handle_message(msg)
        pool.acquire.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_thread_id_returns_early(self):
        ctx, pool, slot, client = _make_ctx()
        setup(ctx)
        msg = _make_message(thread_id=None)
        await handle_message(msg)
        pool.acquire.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: handle_message — new session path
# ---------------------------------------------------------------------------

class TestNewSession:
    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    async def test_creates_new_session_when_none_exists(self, mock_cwd, mock_sw_cls):
        ctx, pool, slot, client = _make_ctx(existing_session=None)
        client.session_prompt = _fake_prompt_stream
        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock(return_value=[])
        mock_writer.cancel = MagicMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)

        pool.acquire.assert_awaited_once()
        client.session_new.assert_awaited_once()
        ctx.store.upsert_session.assert_called_once_with(1, 42, "new-session-id", "/tmp/ws/1/42")
        mock_writer.write_chunk.assert_awaited_once_with("Hi there")
        mock_writer.finalize.assert_awaited_once()
        pool.release_and_dequeue.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: handle_message — existing session path
# ---------------------------------------------------------------------------

class TestExistingSession:
    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    async def test_loads_existing_session(self, mock_cwd, mock_sw_cls):
        record = SessionRecord(
            user_id=1, thread_id=42, session_id="existing-sid",
            workspace_path="/tmp/ws/1/42", model="auto",
        )
        ctx, pool, slot, client = _make_ctx(existing_session=record)
        client.session_prompt = _fake_prompt_stream
        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock(return_value=[])
        mock_writer.cancel = MagicMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)

        client.session_load.assert_awaited_once_with("existing-sid", cwd="/tmp/ws/1/42")
        client.session_new.assert_not_awaited()
        mock_writer.finalize.assert_awaited_once()
        pool.release_and_dequeue.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    async def test_session_load_failure_returns_early_no_data_loss(self, mock_cwd, mock_sw_cls):
        """If session/load fails, handler tells user to retry — no session_new, no data loss."""
        record = SessionRecord(
            user_id=1, thread_id=42, session_id="stale-sid",
            workspace_path="/tmp/ws/1/42", model="auto",
        )
        ctx, pool, slot, client = _make_ctx(existing_session=record)
        client.session_load.side_effect = RuntimeError("session/load failed")

        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock(return_value=[])
        mock_writer.cancel = MagicMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)

        # session_new must NOT be called — that's data loss
        client.session_new.assert_not_awaited()
        ctx.store.upsert_session.assert_not_called()
        # No prompt sent — early return
        client.session_prompt.assert_not_called()
        # User notified to try again
        ctx.bot.send_message.assert_awaited_once()
        sent_text = ctx.bot.send_message.call_args.kwargs.get(
            "text", ctx.bot.send_message.call_args[0][1] if len(ctx.bot.send_message.call_args[0]) > 1 else ""
        )
        assert "try again" in sent_text.lower()
        # Slot still released
        pool.release_and_dequeue.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: handle_message — pool acquire returns None (enqueue)
# ---------------------------------------------------------------------------

class TestPoolBusy:
    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    async def test_enqueues_when_pool_full(self, mock_cwd):
        ctx, pool, slot, client = _make_ctx(existing_session=None)
        pool.acquire = AsyncMock(return_value=None)
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)

        pool.request_queue.enqueue.assert_called_once()
        queued = pool.request_queue.enqueue.call_args[0][0]
        assert queued.thread_id == 42
        assert queued.user_id == 1
        assert queued.message_text == "hello"
        assert queued.workspace_path == "/tmp/ws/1/42"
        # release_and_dequeue should NOT be called (no slot acquired)
        pool.release_and_dequeue.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: handle_message — error during prompt
# ---------------------------------------------------------------------------

class TestPromptError:
    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    async def test_prompt_error_sends_apology(self, mock_cwd, mock_sw_cls):
        ctx, pool, slot, client = _make_ctx(existing_session=None)

        async def _exploding_prompt(*_a, **_kw):
            raise RuntimeError("boom")
            yield  # noqa: unreachable — makes this an async generator

        client.session_prompt = _exploding_prompt
        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock(return_value=[])
        mock_writer.cancel = MagicMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)

        # Error message sent via bot.send_message (not message.answer)
        ctx.bot.send_message.assert_awaited_once()
        assert "wrong" in ctx.bot.send_message.call_args[0][1].lower()
        # Slot still released in finally block
        pool.release_and_dequeue.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    async def test_prompt_error_swallows_send_failure(self, mock_cwd, mock_sw_cls):
        """If both the prompt AND the error message fail, no unhandled exception."""
        ctx, pool, slot, client = _make_ctx(existing_session=None)

        async def _exploding_prompt(*_a, **_kw):
            raise RuntimeError("boom")
            yield  # noqa

        client.session_prompt = _exploding_prompt
        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock(return_value=[])
        mock_writer.cancel = MagicMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        ctx.bot.send_message = AsyncMock(side_effect=Exception("Telegram API down"))

        msg = _make_message(user_id=1, thread_id=42)
        # Should not raise
        await handle_message(msg)
        pool.release_and_dequeue.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: cancel-in-flight path
# ---------------------------------------------------------------------------

class TestCancelInFlight:
    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    async def test_cancel_event_triggers_session_cancel(self, mock_cwd, mock_sw_cls):
        """When cancel_event is set, session_cancel is called and writer.cancel() fires."""
        ctx, pool, slot, client = _make_ctx(existing_session=None)

        cancel_event = asyncio.Event()

        async def _cancellable_stream(*_a, **_kw):
            yield {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "partial"}}
            # Simulate cancel being set between chunks
            cancel_event.set()
            yield {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": " more"}}

        client.session_prompt = _cancellable_stream
        pool.in_flight.track = MagicMock(return_value=cancel_event)

        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock(return_value=[])
        mock_writer.cancel = MagicMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)

        client.session_cancel.assert_awaited_once()
        mock_writer.cancel.assert_called_once()
        # finalize should NOT be called when cancelled
        mock_writer.finalize.assert_not_awaited()
        # Slot still released
        pool.release_and_dequeue.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: atomic dequeue-after-release — release_and_dequeue returns queued request + slot
# ---------------------------------------------------------------------------

class TestDequeueAfterRelease:
    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    async def test_dequeue_spawns_background_task(self, mock_cwd, mock_sw_cls):
        """After release_and_dequeue returns a queued request, a background task is created."""
        ctx, pool, slot, client = _make_ctx(existing_session=None)
        client.session_prompt = _fake_prompt_stream

        queued = QueuedRequest(
            thread_id=99, user_id=2, message_text="queued msg",
            files=[], chat_id=200, workspace_path="/tmp/ws/2/99",
        )
        # release_and_dequeue returns the queued request and the same slot
        pool.release_and_dequeue = AsyncMock(return_value=(queued, slot))

        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock(return_value=[])
        mock_writer.cancel = MagicMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)

        with patch("tg_acp.bot_handlers._handle_queued_request", new_callable=AsyncMock) as mock_hqr:
            await handle_message(msg)
            # Give the event loop a tick to start the task
            await asyncio.sleep(0)
            mock_hqr.assert_awaited_once_with(queued, slot)

    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    async def test_no_dequeue_when_queue_empty(self, mock_cwd, mock_sw_cls):
        """When release_and_dequeue returns (None, None), no background task is spawned."""
        ctx, pool, slot, client = _make_ctx(existing_session=None)
        client.session_prompt = _fake_prompt_stream
        # Default: release_and_dequeue returns (None, None)

        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock(return_value=[])
        mock_writer.cancel = MagicMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        initial_tasks = len(get_background_tasks())
        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)
        # No new background tasks should have been created
        assert len(get_background_tasks()) == initial_tasks


# ---------------------------------------------------------------------------
# Tests: _handle_queued_request — delegates to handle_message_internal with pre-acquired slot
# ---------------------------------------------------------------------------

class TestHandleQueuedRequest:
    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/2/99")
    async def test_queued_request_processes_with_preacquired_slot(self, mock_cwd, mock_sw_cls):
        """_handle_queued_request passes the pre-acquired slot to handle_message_internal."""
        ctx, pool, slot, client = _make_ctx(existing_session=None)
        client.session_prompt = _fake_prompt_stream

        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock(return_value=[])
        mock_writer.cancel = MagicMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        queued = QueuedRequest(
            thread_id=99, user_id=2, message_text="queued msg",
            files=[], chat_id=200, workspace_path="/tmp/ws/2/99",
        )
        await _handle_queued_request(queued, slot)

        # acquire should NOT be called — slot was pre-acquired
        pool.acquire.assert_not_awaited()
        client.session_new.assert_awaited_once()
        mock_writer.finalize.assert_awaited_once()
        pool.release_and_dequeue.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_queued_request_swallows_exceptions(self):
        """_handle_queued_request logs but does not raise on errors."""
        ctx, pool, slot, client = _make_ctx(existing_session=None)
        # Make the pre-acquired slot's client blow up on session_new
        client.session_new = AsyncMock(side_effect=RuntimeError("boom"))
        setup(ctx)

        queued = QueuedRequest(
            thread_id=99, user_id=2, message_text="queued msg",
            files=[], chat_id=200, workspace_path="/tmp/ws/2/99",
        )
        # Should not raise
        await _handle_queued_request(queued, slot)
        # Slot must still be released even on error
        pool.release_and_dequeue.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: allowlist gate (FR-14)
# ---------------------------------------------------------------------------

class TestAllowlistDeniedText:
    """Denied user sends a text message — rejection, no ACP interaction."""

    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/99/42")
    async def test_denied_user_gets_rejection(self, mock_cwd):
        ctx, pool, slot, client = _make_ctx()
        # user_id=99 is NOT in the default allowlist {1, 2, 3}
        setup(ctx)

        msg = _make_message(user_id=99, thread_id=42)
        await handle_message(msg)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "⛔" in text
        assert "99" in text  # shows their Telegram ID
        # No ACP interaction
        pool.acquire.assert_not_awaited()
        client.session_new.assert_not_awaited()


class TestAllowlistDeniedStart:
    """Denied user sends /start — restricted welcome with their ID."""

    @pytest.mark.asyncio
    async def test_denied_start_shows_restricted_welcome(self):
        ctx, pool, slot, client = _make_ctx()
        setup(ctx)

        msg = _make_message(user_id=99)
        await cmd_start(msg)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "Kiro" in text
        assert "⛔" in text
        assert "99" in text

    @pytest.mark.asyncio
    async def test_allowed_start_normal_welcome(self):
        ctx, pool, slot, client = _make_ctx()
        setup(ctx)

        msg = _make_message(user_id=1)  # user 1 IS in allowlist
        await cmd_start(msg)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "Kiro" in text
        assert "⛔" not in text


class TestAllowlistDeniedModel:
    """Denied user sends /model — rejection, no model change."""

    @pytest.mark.asyncio
    async def test_denied_model_gets_rejection(self):
        ctx, pool, slot, client = _make_ctx()
        setup(ctx)

        msg = _make_message(user_id=99, thread_id=42)
        msg.text = "/model auto"
        from tg_acp.bot_handlers import cmd_model
        await cmd_model(msg)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "⛔" in text
        assert "99" in text
        # No store interaction
        ctx.store.set_model.assert_not_called()


class TestAllowlistDeniedFile:
    """Denied user sends a file — rejection, file not downloaded."""

    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.FileHandler")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/99/42")
    async def test_denied_file_not_downloaded(self, mock_cwd, mock_fh):
        ctx, pool, slot, client = _make_ctx()
        setup(ctx)

        msg = _make_message(user_id=99, thread_id=42, text=None)
        msg.document = MagicMock()  # has a file attachment

        await handle_message(msg)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "⛔" in text
        # File NOT downloaded
        mock_fh.download_to_workspace.assert_not_called()
        pool.acquire.assert_not_awaited()


class TestAllowlistAllowedUnchanged:
    """Allowed user — normal flow unchanged (regression check)."""

    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    async def test_allowed_user_normal_flow(self, mock_cwd, mock_sw_cls):
        ctx, pool, slot, client = _make_ctx(existing_session=None)
        client.session_prompt = _fake_prompt_stream
        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock(return_value=[])
        mock_writer.cancel = MagicMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)  # user 1 IS allowed
        await handle_message(msg)

        pool.acquire.assert_awaited_once()
        client.session_new.assert_awaited_once()
        mock_writer.finalize.assert_awaited_once()


class TestAllowlistEmptyDeniesAll:
    """Empty allowlist — all users denied (fail-closed)."""

    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    async def test_empty_allowlist_denies_everyone(self, mock_cwd):
        config = _make_config(allowed_telegram_ids=frozenset())
        store = MagicMock(spec=SessionStore)
        bot = MagicMock()
        pool, slot, client = _make_mock_pool()
        ctx = BotContext(config=config, store=store, pool=pool, bot=bot)
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args[0][0]
        assert "⛔" in text
        pool.acquire.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: stale session lock recovery (FR-16)
# ---------------------------------------------------------------------------

class TestStaleLockRecovery:
    """session/load fails with dead PID → session cleared, new session created."""

    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    @patch("tg_acp.bot_handlers._try_recover_stale_session", return_value=99999)
    async def test_stale_pid_triggers_recovery(self, mock_recover, mock_cwd, mock_sw_cls):
        record = SessionRecord(
            user_id=1, thread_id=42, session_id="stale-sid",
            workspace_path="/tmp/ws/1/42", model="auto",
        )
        ctx, pool, slot, client = _make_ctx(existing_session=record)
        client.session_load.side_effect = RuntimeError(
            "JSON-RPC error on session/load: [-32603] Internal error | "
            "data: Failed to start session: Session is active in another process (PID 99999)"
        )
        client.session_new = AsyncMock(return_value="recovered-sid")
        client.session_prompt = _fake_prompt_stream
        mock_writer = MagicMock()
        mock_writer.write_chunk = AsyncMock()
        mock_writer.finalize = AsyncMock(return_value=[])
        mock_writer.cancel = MagicMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)

        # Old session deleted, new one created
        ctx.store.delete_session.assert_called_once_with(1, 42)
        client.session_new.assert_awaited_once()
        ctx.store.upsert_session.assert_called_once_with(1, 42, "recovered-sid", "/tmp/ws/1/42")
        # Prompt still executed with new session
        mock_writer.finalize.assert_awaited_once()


class TestLivePidConflict:
    """session/load fails with live PID → error to user, no recovery."""

    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    @patch("tg_acp.bot_handlers._try_recover_stale_session", return_value=None)
    async def test_live_pid_reports_error(self, mock_recover, mock_cwd, mock_sw_cls):
        record = SessionRecord(
            user_id=1, thread_id=42, session_id="locked-sid",
            workspace_path="/tmp/ws/1/42", model="auto",
        )
        ctx, pool, slot, client = _make_ctx(existing_session=record)
        client.session_load.side_effect = RuntimeError("Session is active in another process (PID 12345)")
        mock_writer = MagicMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)

        # No recovery — error sent to user
        ctx.store.delete_session.assert_not_called()
        client.session_new.assert_not_awaited()
        ctx.bot.send_message.assert_awaited_once()
        sent_text = ctx.bot.send_message.call_args[0][1]
        assert "try again" in sent_text.lower()


class TestNonMatchingSessionError:
    """session/load fails with unrelated error → existing behavior unchanged."""

    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    async def test_unrelated_error_no_recovery(self, mock_cwd, mock_sw_cls):
        record = SessionRecord(
            user_id=1, thread_id=42, session_id="broken-sid",
            workspace_path="/tmp/ws/1/42", model="auto",
        )
        ctx, pool, slot, client = _make_ctx(existing_session=record)
        client.session_load.side_effect = RuntimeError("some other error")
        mock_writer = MagicMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)

        # _try_recover_stale_session returns None for non-matching error
        ctx.store.delete_session.assert_not_called()
        client.session_new.assert_not_awaited()
        ctx.bot.send_message.assert_awaited_once()
        sent_text = ctx.bot.send_message.call_args[0][1]
        assert "try again" in sent_text.lower()


class TestRecoveryFailure:
    """session_new fails after stale lock clear → user gets error, no crash."""

    @pytest.mark.asyncio
    @patch("tg_acp.bot_handlers.StreamWriter")
    @patch("tg_acp.bot_handlers.create_workspace_dir", return_value="/tmp/ws/1/42")
    @patch("tg_acp.bot_handlers._try_recover_stale_session", return_value=99999)
    async def test_session_new_failure_after_recovery(self, mock_recover, mock_cwd, mock_sw_cls):
        record = SessionRecord(
            user_id=1, thread_id=42, session_id="stale-sid",
            workspace_path="/tmp/ws/1/42", model="auto",
        )
        ctx, pool, slot, client = _make_ctx(existing_session=record)
        client.session_load.side_effect = RuntimeError(
            "Session is active in another process (PID 99999)"
        )
        client.session_new = AsyncMock(side_effect=RuntimeError("spawn failed"))
        mock_writer = MagicMock()
        mock_sw_cls.return_value = mock_writer
        setup(ctx)

        msg = _make_message(user_id=1, thread_id=42)
        await handle_message(msg)

        # Old session deleted, but new session creation failed
        ctx.store.delete_session.assert_called_once_with(1, 42)
        ctx.store.upsert_session.assert_not_called()
        ctx.bot.send_message.assert_awaited_once()
        sent_text = ctx.bot.send_message.call_args[0][1]
        assert "recovering" in sent_text.lower()


class TestTryRecoverStaleSession:
    """Unit tests for _try_recover_stale_session helper."""

    @patch("tg_acp.bot_handlers.os.kill", side_effect=OSError("No such process"))
    def test_dead_pid_returns_pid(self, mock_kill):
        from tg_acp.bot_handlers import _try_recover_stale_session
        result = _try_recover_stale_session(
            "Failed to start session: Session is active in another process (PID 99999)"
        )
        assert result == 99999
        mock_kill.assert_called_once_with(99999, 0)

    @patch("tg_acp.bot_handlers.os.kill")  # no exception = process alive
    def test_live_pid_returns_none(self, mock_kill):
        from tg_acp.bot_handlers import _try_recover_stale_session
        result = _try_recover_stale_session(
            "Failed to start session: Session is active in another process (PID 12345)"
        )
        assert result is None

    def test_non_matching_error_returns_none(self):
        from tg_acp.bot_handlers import _try_recover_stale_session
        assert _try_recover_stale_session("some other error") is None

    @patch("tg_acp.bot_handlers.os.kill", side_effect=PermissionError("not permitted"))
    def test_permission_error_treated_as_alive(self, mock_kill):
        from tg_acp.bot_handlers import _try_recover_stale_session
        result = _try_recover_stale_session(
            "Session is active in another process (PID 42)"
        )
        assert result is None

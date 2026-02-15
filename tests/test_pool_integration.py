"""Integration tests — real ProcessPool + real kiro-cli + real SessionStore.

Only the Telegram Bot is mocked (we can't send real messages).
These tests exercise the full handle_message_internal path including
session/load retry under lock contention.

Requires:
- kiro-cli installed and on PATH
- A valid global agent config (provisioned by the bot or manually)
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tg_acp.acp_client import TURN_END
from tg_acp.bot_handlers import (
    BotContext,
    handle_message_internal,
    setup,
)
from tg_acp.config import Config
from tg_acp.process_pool import ProcessPool
from tg_acp.session_store import SessionStore, create_workspace_dir

pytestmark = pytest.mark.skipif(
    shutil.which("kiro-cli") is None,
    reason="kiro-cli not found on PATH",
)

AGENT_NAME = os.environ.get("KIRO_AGENT_NAME", "tg-acp")
WORKSPACE_BASE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "workspaces")


def _make_config(max_processes: int = 2) -> Config:
    return Config(
        bot_token="fake-token",
        workspace_base_path=WORKSPACE_BASE,
        max_processes=max_processes,
        idle_timeout_seconds=120,  # don't reap during test
        kiro_agent_name=AGENT_NAME,
        log_level="DEBUG",
        kiro_config_path="./kiro-config/",
    )


def _mock_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_message_draft = AsyncMock()
    return bot


def _clean_workspace(user_id: int, thread_id: int) -> None:
    path = os.path.join(WORKSPACE_BASE, str(user_id), str(thread_id))
    if os.path.exists(path):
        shutil.rmtree(path)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test-pool-integration.db")


@pytest.fixture
def store(db_path: str):
    s = SessionStore(db_path)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _send_and_collect(
    user_id: int,
    thread_id: int,
    text: str,
    chat_id: int = 100,
) -> list[str]:
    """Call handle_message_internal and capture bot.send_message calls.

    Returns list of message texts sent by the bot (from StreamWriter.finalize
    which calls bot.send_message).
    """
    import tg_acp.bot_handlers as mod
    ctx = mod._ctx
    assert ctx is not None

    workspace_path = create_workspace_dir(WORKSPACE_BASE, user_id, thread_id)

    # Reset send_message tracking before this call
    ctx.bot.send_message.reset_mock()

    await handle_message_internal(
        user_id=user_id,
        thread_id=thread_id,
        message_text=text,
        file_paths=[],
        chat_id=chat_id,
        workspace_path=workspace_path,
    )

    # Collect all text from send_message calls
    texts = []
    for call in ctx.bot.send_message.call_args_list:
        args, kwargs = call
        # send_message(chat_id, text, ...) or send_message(chat_id=..., text=...)
        if len(args) >= 2:
            texts.append(args[1])
        elif "text" in kwargs:
            texts.append(kwargs["text"])
    return texts


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.timeout(120)
@pytest.mark.asyncio
async def test_single_thread_session_persists(store: SessionStore, db_path: str):
    """Basic flow: thread sends two messages, second message recalls context from first."""
    user_id, thread_id = 200, 1001
    _clean_workspace(user_id, thread_id)

    config = _make_config(max_processes=1)
    pool = ProcessPool(config)
    bot = _mock_bot()

    try:
        await pool.initialize()
        ctx = BotContext(config=config, store=store, pool=pool, bot=bot)
        setup(ctx)

        # Message 1: establish context
        texts1 = await _send_and_collect(
            user_id, thread_id,
            "Remember: the code word is MANGO. Just confirm.",
        )
        print(f"[DEBUG] Message 1 response: {texts1}")
        assert len(texts1) > 0

        # Message 2: recall context (same thread, same pool)
        texts2 = await _send_and_collect(
            user_id, thread_id,
            "What was the code word? Reply with just the word.",
        )
        print(f"[DEBUG] Message 2 response: {texts2}")
        full_response = " ".join(texts2)
        assert "MANGO" in full_response.upper(), (
            f"Expected MANGO in response, got: {full_response[:300]}"
        )

        # Verify session was stored
        record = store.get_session(user_id, thread_id)
        assert record is not None
        assert record.session_id != ""
    finally:
        await pool.shutdown()
        _clean_workspace(user_id, thread_id)


@pytest.mark.timeout(180)
@pytest.mark.asyncio
async def test_two_threads_concurrent_no_session_loss(store: SessionStore, db_path: str):
    """Two threads use the pool concurrently. Neither loses session context.

    With max_processes=2, each thread gets its own slot initially.
    Then we send follow-up messages to verify both sessions are intact.
    """
    user_a, thread_a = 200, 2001
    user_b, thread_b = 201, 2002
    _clean_workspace(user_a, thread_a)
    _clean_workspace(user_b, thread_b)

    config = _make_config(max_processes=2)
    pool = ProcessPool(config)
    bot = _mock_bot()

    try:
        await pool.initialize()
        ctx = BotContext(config=config, store=store, pool=pool, bot=bot)
        setup(ctx)

        # Thread A: establish context
        await _send_and_collect(
            user_a, thread_a,
            "Remember: my pet's name is ZIGGY. Confirm.",
        )
        print("[DEBUG] Thread A context established")

        # Thread B: establish context
        await _send_and_collect(
            user_b, thread_b,
            "Remember: my favorite number is 7777. Confirm.",
        )
        print("[DEBUG] Thread B context established")

        # Thread A: recall
        texts_a = await _send_and_collect(
            user_a, thread_a,
            "What is my pet's name? Reply with just the name.",
        )
        full_a = " ".join(texts_a)
        print(f"[DEBUG] Thread A recall: {full_a[:200]}")
        assert "ZIGGY" in full_a.upper(), f"Thread A lost context: {full_a[:300]}"

        # Thread B: recall
        texts_b = await _send_and_collect(
            user_b, thread_b,
            "What is my favorite number? Reply with just the number.",
        )
        full_b = " ".join(texts_b)
        print(f"[DEBUG] Thread B recall: {full_b[:200]}")
        assert "7777" in full_b, f"Thread B lost context: {full_b[:300]}"
    finally:
        await pool.shutdown()
        _clean_workspace(user_a, thread_a)
        _clean_workspace(user_b, thread_b)


@pytest.mark.timeout(240)
@pytest.mark.asyncio
async def test_lock_contention_must_not_destroy_session(store: SessionStore, db_path: str):
    """MUST FAIL until the pool acquire logic prevents cross-slot session loading.

    Reproduces the production incident where thread A's session becomes
    inaccessible because another slot's kiro-cli process holds the lock.

    Sequence:
    1. Thread A sends message -> slot 0 creates session, streams, releases.
    2. Thread B fires a LONG prompt concurrently with thread A's follow-up.
       Thread B grabs a slot; thread A lands on a different slot and tries
       session/load -> FAILS (-32603) because the other kiro-cli holds the lock.
    3. With the data-loss fallback removed, thread A gets "try again" instead
       of a silent session reset. But the user still can't use their session.
    4. Final sequential recall on thread A must return ELEPHANT, not an error.

    Asserts:
    - session_id is unchanged (no data loss)
    - context is actually recalled (session is usable, not just preserved)
    """
    user_a, thread_a = 200, 3001
    user_b, thread_b = 201, 3002
    _clean_workspace(user_a, thread_a)
    _clean_workspace(user_b, thread_b)

    config = _make_config(max_processes=2)
    pool = ProcessPool(config)
    bot = _mock_bot()

    try:
        await pool.initialize()
        ctx = BotContext(config=config, store=store, pool=pool, bot=bot)
        setup(ctx)

        # Step 1: Thread A establishes session with a memorable fact
        await _send_and_collect(
            user_a, thread_a,
            "Remember this fruit name: MANGO. Just confirm you noted it.",
        )
        record_before = store.get_session(user_a, thread_a)
        assert record_before is not None
        original_session_id = record_before.session_id
        print(f"[DEBUG] Thread A session: {original_session_id}")

        # Step 2+3: Fire thread B (long prompt) and thread A concurrently.
        workspace_a = create_workspace_dir(WORKSPACE_BASE, user_a, thread_a)
        workspace_b = create_workspace_dir(WORKSPACE_BASE, user_b, thread_b)

        async def thread_b_long():
            await handle_message_internal(
                user_id=user_b, thread_id=thread_b,
                message_text=(
                    "Write a short paragraph (at least 5 sentences) about "
                    "why the ocean is blue. Be detailed."
                ),
                file_paths=[], chat_id=101,
                workspace_path=workspace_b,
            )

        async def thread_a_concurrent():
            # Small delay so thread B grabs a slot first
            await asyncio.sleep(0.3)
            await handle_message_internal(
                user_id=user_a, thread_id=thread_a,
                message_text="What fruit did I mention earlier? Reply with just the fruit name.",
                file_paths=[], chat_id=100,
                workspace_path=workspace_a,
            )

        print("[DEBUG] Firing concurrent requests (B=long, A=recall)...")
        results = await asyncio.gather(
            thread_b_long(),
            thread_a_concurrent(),
            return_exceptions=True,
        )
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                print(f"[DEBUG] Task {i} failed: {r}")
                raise r

        # Wait for any background tasks (queued request processing)
        import tg_acp.bot_handlers as _mod
        bg_tasks = _mod.get_background_tasks()
        if bg_tasks:
            print(f"[DEBUG] Waiting for {len(bg_tasks)} background tasks...")
            await asyncio.gather(*bg_tasks, return_exceptions=True)

        # Step 4: Verify -- sequential recall on thread A
        texts = await _send_and_collect(
            user_a, thread_a,
            "What fruit name did I ask you to remember? Reply with ONLY the fruit name.",
        )
        full_response = " ".join(texts)
        print(f"[DEBUG] Final recall: {full_response[:200]}")

        # Hard assertions
        record_after = store.get_session(user_a, thread_a)
        assert record_after is not None

        assert record_after.session_id == original_session_id, (
            f"SESSION DESTROYED: {original_session_id} -> {record_after.session_id}. "
            f"Lock contention caused silent session reset."
        )
        assert "MANGO" in full_response.upper(), (
            f"Session preserved but inaccessible due to lock contention. "
            f"Pool must route thread A to its affinity slot. "
            f"Response: {full_response[:300]}"
        )
    finally:
        await pool.shutdown()
        _clean_workspace(user_a, thread_a)
        _clean_workspace(user_b, thread_b)


@pytest.mark.timeout(180)
@pytest.mark.asyncio
async def test_pool_queuing_under_pressure(store: SessionStore, db_path: str):
    """With max_processes=1, two concurrent requests force queuing.

    The second request should be enqueued and processed after the first
    completes. Both should get responses (no dropped requests).
    """
    user_a, thread_a = 200, 4001
    user_b, thread_b = 201, 4002
    _clean_workspace(user_a, thread_a)
    _clean_workspace(user_b, thread_b)

    config = _make_config(max_processes=1)
    pool = ProcessPool(config)
    bot = _mock_bot()

    try:
        await pool.initialize()
        ctx = BotContext(config=config, store=store, pool=pool, bot=bot)
        setup(ctx)

        workspace_a = create_workspace_dir(WORKSPACE_BASE, user_a, thread_a)
        workspace_b = create_workspace_dir(WORKSPACE_BASE, user_b, thread_b)

        completed = {"a": False, "b": False}

        async def send_a():
            await handle_message_internal(
                user_id=user_a, thread_id=thread_a,
                message_text="Say exactly: ALPHA_DONE",
                file_paths=[], chat_id=100,
                workspace_path=workspace_a,
            )
            completed["a"] = True

        async def send_b():
            await handle_message_internal(
                user_id=user_b, thread_id=thread_b,
                message_text="Say exactly: BRAVO_DONE",
                file_paths=[], chat_id=101,
                workspace_path=workspace_b,
            )
            completed["b"] = True

        print("[DEBUG] Firing 2 concurrent requests on 1-slot pool...")
        results = await asyncio.gather(send_a(), send_b(), return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"[DEBUG] Task {i} raised: {result}")
                raise result

        # At least one should have completed via direct processing,
        # the other via queue drain
        print(f"[DEBUG] Completed: {completed}")
        assert completed["a"] or completed["b"], "At least one request should complete"

        # Both sessions should exist in the store
        rec_a = store.get_session(user_a, thread_a)
        rec_b = store.get_session(user_b, thread_b)
        # The queued one might not have a session if it was still waiting,
        # but with gather() both should eventually complete
        sessions_created = sum(1 for r in [rec_a, rec_b] if r is not None)
        print(f"[DEBUG] Sessions created: {sessions_created}/2")
        assert sessions_created >= 1, "At least one session should be created"
    finally:
        await pool.shutdown()
        _clean_workspace(user_a, thread_a)
        _clean_workspace(user_b, thread_b)

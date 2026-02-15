"""Integration tests for session continuity — real kiro-cli + real SQLite."""

from __future__ import annotations

import os
import shutil

import pytest

from tg_acp.acp_client import ACPClient, TURN_END
from tg_acp.session_store import SessionStore, create_workspace_dir

# These tests require kiro-cli on PATH and a valid KIRO_AGENT_NAME.
AGENT_NAME = os.environ.get("KIRO_AGENT_NAME", "tg-acp")

# Use the real workspace layout — ./workspaces/{uid}/{tid}/
WORKSPACE_BASE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "workspaces")
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tg-acp-test.db")


@pytest.fixture
def store():
    """SessionStore with a test database file in the project root."""
    if os.path.exists(DB_PATH):
        os.unlink(DB_PATH)
    s = SessionStore(DB_PATH)
    yield s
    s.close()
    if os.path.exists(DB_PATH):
        os.unlink(DB_PATH)


async def _collect_response(client: ACPClient, session_id: str, prompt: str) -> str:
    """Send a prompt and collect the full text response."""
    text_parts: list[str] = []
    async for update in client.session_prompt(
        session_id,
        [{"type": "text", "text": prompt}],
    ):
        update_type = update.get("sessionUpdate", "")
        print(f"[DEBUG] update: {update_type}")
        if update_type == "agent_message_chunk":
            content = update.get("content", {})
            if content.get("type") == "text":
                text_parts.append(content["text"])
        elif update_type == TURN_END:
            break
    return "".join(text_parts)


def _clean_workspace(user_id: int, thread_id: int) -> None:
    """Remove a test workspace dir if it exists."""
    path = os.path.join(WORKSPACE_BASE, str(user_id), str(thread_id))
    if os.path.exists(path):
        shutil.rmtree(path)


@pytest.mark.timeout(180)
@pytest.mark.asyncio
async def test_session_remembers_number(store):
    """Run 1: tell agent to remember 1234. Run 2: ask what number — verify 1234 in response."""
    user_id, thread_id = 99, 901
    _clean_workspace(user_id, thread_id)
    workspace_path = create_workspace_dir(WORKSPACE_BASE, user_id, thread_id)
    print(f"\n[DEBUG] workspace_path={workspace_path}")

    # --- Run 1: memorize ---
    print("[DEBUG] === RUN 1: MEMORIZE ===")
    client1 = await ACPClient.spawn(AGENT_NAME)
    try:
        await client1.initialize()
        session_id = await client1.session_new(cwd=workspace_path)
        print(f"[DEBUG] session_id={session_id}")
        store.upsert_session(user_id, thread_id, session_id, workspace_path)

        response1 = await _collect_response(
            client1, session_id,
            "Remember this number: 1234. Just confirm you memorized it.",
        )
        print(f"[DEBUG] Run 1 response: {response1[:200]}")
        assert len(response1) > 0, "Expected a response from run 1"
    finally:
        await client1.kill()

    # --- Run 2: recall ---
    print("[DEBUG] === RUN 2: RECALL ===")
    record = store.get_session(user_id, thread_id)
    assert record is not None

    client2 = await ACPClient.spawn(AGENT_NAME)
    try:
        await client2.initialize()
        print(f"[DEBUG] Loading session {record.session_id}...")
        await client2.session_load(record.session_id, cwd=workspace_path)
        print("[DEBUG] session/load succeeded")

        response2 = await _collect_response(
            client2, record.session_id,
            "What number did I ask you to remember? Reply with just the number.",
        )
        print(f"[DEBUG] Run 2 response: {response2[:200]}")
        assert "1234" in response2, (
            f"Expected '1234' in response, got: {response2[:300]}"
        )
    finally:
        await client2.kill()
        _clean_workspace(user_id, thread_id)


@pytest.mark.timeout(120)
@pytest.mark.asyncio
async def test_session_load_after_prompt(store):
    """Create session, send one prompt (so kiro-cli persists it), then load from a fresh client."""
    user_id, thread_id = 99, 902
    _clean_workspace(user_id, thread_id)
    workspace_path = create_workspace_dir(WORKSPACE_BASE, user_id, thread_id)
    print(f"\n[DEBUG] workspace_path={workspace_path}")

    # Client 1: create session + send a prompt so kiro-cli persists it
    print("[DEBUG] Spawning client1...")
    client1 = await ACPClient.spawn(AGENT_NAME)
    try:
        await client1.initialize()
        session_id = await client1.session_new(cwd=workspace_path)
        print(f"[DEBUG] session_id={session_id}")
        store.upsert_session(user_id, thread_id, session_id, workspace_path)

        response = await _collect_response(client1, session_id, "Say hello.")
        print(f"[DEBUG] Got response: {response[:100]}")
    finally:
        await client1.kill()

    # Client 2: load the session
    record = store.get_session(user_id, thread_id)
    assert record is not None

    print("[DEBUG] Spawning client2 for session/load...")
    client2 = await ACPClient.spawn(AGENT_NAME)
    try:
        await client2.initialize()
        print(f"[DEBUG] Loading session {record.session_id}...")
        await client2.session_load(record.session_id, cwd=workspace_path)
        print("[DEBUG] session/load succeeded — test passed")
    finally:
        await client2.kill()
        _clean_workspace(user_id, thread_id)


@pytest.mark.timeout(180)
@pytest.mark.asyncio
async def test_session_load_fails_while_other_process_holds_session(store):
    """Reproduce the session-lock race: client2 tries session/load while client1 still alive.

    Scenario from production:
    1. client1 creates session, sends a prompt (kiro-cli persists it)
    2. client1 is NOT killed — simulates the previous request's process still holding the lock
    3. client2 tries session/load on the same session_id → expected to fail (lock held)
    4. After killing client1, a fresh client3 can load the session successfully

    This is the exact race that caused session e446418b to be silently replaced
    by 65f048be in production — the fallback code created a new empty session
    instead of retrying on a fresh process.
    """
    user_id, thread_id = 99, 903
    _clean_workspace(user_id, thread_id)
    workspace_path = create_workspace_dir(WORKSPACE_BASE, user_id, thread_id)
    print(f"\n[DEBUG] workspace_path={workspace_path}")

    # --- Step 1: client1 creates session and sends a prompt ---
    print("[DEBUG] === STEP 1: client1 creates session + prompt ===")
    client1 = await ACPClient.spawn(AGENT_NAME)
    session_id = None
    try:
        await client1.initialize()
        session_id = await client1.session_new(cwd=workspace_path)
        print(f"[DEBUG] session_id={session_id}")
        store.upsert_session(user_id, thread_id, session_id, workspace_path)

        response1 = await _collect_response(
            client1, session_id,
            "Remember: the secret word is PINEAPPLE. Confirm you memorized it.",
        )
        print(f"[DEBUG] client1 response: {response1[:200]}")
        assert len(response1) > 0

        # --- Step 2: client2 tries to load while client1 is STILL ALIVE ---
        print("[DEBUG] === STEP 2: client2 tries session/load (client1 still alive) ===")
        client2 = await ACPClient.spawn(AGENT_NAME)
        try:
            await client2.initialize()
            load_failed = False
            load_error = ""
            try:
                await client2.session_load(session_id, cwd=workspace_path)
                print("[DEBUG] client2 session/load SUCCEEDED (no lock contention)")
                # If it succeeds, the lock theory is wrong — still useful data
            except RuntimeError as exc:
                load_failed = True
                load_error = str(exc)
                print(f"[DEBUG] client2 session/load FAILED as expected: {load_error}")
        finally:
            await client2.kill()

    finally:
        # --- Step 3: kill client1, releasing the lock ---
        print("[DEBUG] === STEP 3: killing client1 to release lock ===")
        await client1.kill()

    # --- Step 4: fresh client3 should load successfully after lock is released ---
    print("[DEBUG] === STEP 4: client3 loads session after client1 killed ===")
    record = store.get_session(user_id, thread_id)
    assert record is not None

    client3 = await ACPClient.spawn(AGENT_NAME)
    try:
        await client3.initialize()
        await client3.session_load(record.session_id, cwd=workspace_path)
        print("[DEBUG] client3 session/load succeeded")

        response3 = await _collect_response(
            client3, record.session_id,
            "What was the secret word I told you? Reply with just the word.",
        )
        print(f"[DEBUG] client3 response: {response3[:200]}")
        assert "PINEAPPLE" in response3.upper(), (
            f"Expected 'PINEAPPLE' in response, got: {response3[:300]}"
        )
    finally:
        await client3.kill()
        _clean_workspace(user_id, thread_id)

    # --- Report findings ---
    if load_failed:
        print(f"\n[FINDING] session/load FAILS when another process holds the session.")
        print(f"[FINDING] Error: {load_error}")
        print("[FINDING] This confirms the production bug — the old code would have")
        print("[FINDING] silently created a new session, destroying conversation history.")
    else:
        print("\n[FINDING] session/load SUCCEEDED despite another process holding the session.")
        print("[FINDING] Lock contention may not be the root cause — investigate further.")


@pytest.mark.timeout(180)
@pytest.mark.asyncio
async def test_retry_on_fresh_process_recovers_session(store):
    """End-to-end: simulate the retry logic — kill old process, spawn fresh, reload session.

    This validates the fix: when session/load fails on the current slot's client,
    killing it and spawning a fresh client allows the retry to succeed.
    """
    user_id, thread_id = 99, 904
    _clean_workspace(user_id, thread_id)
    workspace_path = create_workspace_dir(WORKSPACE_BASE, user_id, thread_id)
    print(f"\n[DEBUG] workspace_path={workspace_path}")

    # --- Setup: create session with content ---
    print("[DEBUG] === SETUP: create session with memorable content ===")
    client1 = await ACPClient.spawn(AGENT_NAME)
    try:
        await client1.initialize()
        session_id = await client1.session_new(cwd=workspace_path)
        store.upsert_session(user_id, thread_id, session_id, workspace_path)

        response = await _collect_response(
            client1, session_id,
            "Remember: my favorite color is TURQUOISE. Confirm.",
        )
        print(f"[DEBUG] Setup response: {response[:150]}")
        assert len(response) > 0

        # --- Simulate the race: client2 tries to load while client1 alive ---
        print("[DEBUG] === RACE: client2 tries load (client1 alive) ===")
        client2 = await ACPClient.spawn(AGENT_NAME)
        try:
            await client2.initialize()
            try:
                await client2.session_load(session_id, cwd=workspace_path)
                print("[DEBUG] client2 load succeeded — no contention, skipping retry test")
                # Even if no contention, verify the session content is intact
                response2 = await _collect_response(
                    client2, session_id,
                    "What is my favorite color? Reply with just the color.",
                )
                print(f"[DEBUG] client2 response: {response2[:150]}")
                assert "TURQUOISE" in response2.upper(), (
                    f"Expected TURQUOISE, got: {response2[:200]}"
                )
                return  # Test passes — no lock contention to simulate retry
            except RuntimeError as exc:
                print(f"[DEBUG] client2 load failed: {exc}")
                # This is the expected path — now simulate the retry fix
        finally:
            await client2.kill()

    finally:
        # Kill client1 — this is what the retry logic does (kill old, spawn fresh)
        print("[DEBUG] === RETRY: killing client1 (simulating retry logic) ===")
        await client1.kill()

    # --- Retry: fresh client3 loads the session (simulates the retry path) ---
    print("[DEBUG] === RETRY: fresh client3 loads session ===")
    client3 = await ACPClient.spawn(AGENT_NAME)
    try:
        await client3.initialize()
        await client3.session_load(session_id, cwd=workspace_path)
        print("[DEBUG] client3 session/load succeeded (retry worked)")

        response3 = await _collect_response(
            client3, session_id,
            "What is my favorite color? Reply with just the color.",
        )
        print(f"[DEBUG] client3 response: {response3[:150]}")
        assert "TURQUOISE" in response3.upper(), (
            f"Expected TURQUOISE in retry response, got: {response3[:200]}"
        )
        print("[DEBUG] Retry path confirmed: kill old process + fresh spawn recovers session")
    finally:
        await client3.kill()
        _clean_workspace(user_id, thread_id)

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

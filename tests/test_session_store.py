"""Tests for C3: Session Store — real SQLite, no mocks."""

from __future__ import annotations

import os
import tempfile

import pytest

from tg_acp.session_store import SessionStore, create_workspace_dir
from tg_acp.session_security import wrap_session_id


@pytest.fixture
def store(tmp_path):
    """Create a SessionStore with a temp database."""
    db_path = str(tmp_path / "test.db")
    s = SessionStore(db_path)
    yield s
    s.close()


class TestSchemaCreation:
    def test_store_schema_creation(self, tmp_path):
        """Init creates table. Second init on same DB is idempotent."""
        db_path = str(tmp_path / "test.db")
        s1 = SessionStore(db_path)
        # Use wrapped session ID
        wrapped_id = wrap_session_id("sess-1", 1)
        s1.upsert_session(1, 1, wrapped_id, "/tmp/ws/1/1")
        s1.close()

        s2 = SessionStore(db_path)
        record = s2.get_session(1, 1)
        assert record is not None
        assert record.session_id == wrapped_id
        s2.close()


class TestUpsertAndGet:
    def test_store_upsert_and_get(self, store):
        """Upsert a session, get it back, verify all fields."""
        wrapped_id = wrap_session_id("sess-abc", 42)
        store.upsert_session(42, 7, wrapped_id, "/ws/42/7")
        record = store.get_session(42, 7)
        assert record is not None
        assert record.user_id == 42
        assert record.thread_id == 7
        assert record.session_id == wrapped_id
        assert record.workspace_path == "/ws/42/7"
        assert record.model == "auto"

    def test_store_get_nonexistent(self, store):
        """get_session for unknown key returns None."""
        assert store.get_session(999, 999) is None

    def test_store_upsert_replaces(self, store):
        """Second upsert for same key overwrites first."""
        wrapped_old = wrap_session_id("sess-old", 1)
        wrapped_new = wrap_session_id("sess-new", 1)
        store.upsert_session(1, 1, wrapped_old, "/ws/1/1")
        store.upsert_session(1, 1, wrapped_new, "/ws/1/1-v2")
        record = store.get_session(1, 1)
        assert record is not None
        assert record.session_id == wrapped_new
        assert record.workspace_path == "/ws/1/1-v2"


class TestModelOperations:
    def test_store_set_model(self, store):
        """set_model updates the model field."""
        wrapped_id = wrap_session_id("sess-1", 1)
        store.upsert_session(1, 1, wrapped_id, "/ws/1/1")
        store.set_model(1, 1, "claude-sonnet-4")
        record = store.get_session(1, 1)
        assert record is not None
        assert record.model == "claude-sonnet-4"

    def test_store_get_model_default(self, store):
        """get_model for unknown key returns 'auto'."""
        assert store.get_model(999, 999) == "auto"

    def test_store_get_model_after_set(self, store):
        """set_model then get_model returns the set value."""
        wrapped_id = wrap_session_id("sess-1", 1)
        store.upsert_session(1, 1, wrapped_id, "/ws/1/1")
        store.set_model(1, 1, "claude-opus-4.6")
        assert store.get_model(1, 1) == "claude-opus-4.6"

    def test_store_upsert_resets_model(self, store):
        """Upsert after set_model resets model to 'auto'."""
        wrapped_id1 = wrap_session_id("sess-1", 1)
        wrapped_id2 = wrap_session_id("sess-2", 1)
        store.upsert_session(1, 1, wrapped_id1, "/ws/1/1")
        store.set_model(1, 1, "claude-sonnet-4")
        assert store.get_model(1, 1) == "claude-sonnet-4"

        # New session (upsert) resets model
        store.upsert_session(1, 1, wrapped_id2, "/ws/1/1")
        assert store.get_model(1, 1) == "auto"


class TestSecurityValidation:
    """Test session ownership validation in SessionStore."""

    def test_upsert_rejects_wrong_user_prefix(self, store):
        """upsert_session rejects session ID with wrong user prefix."""
        # Try to store a session with user 99's prefix for user 42
        wrong_prefix_id = wrap_session_id("sess-test", 99)
        with pytest.raises(ValueError, match="Session ID must contain user prefix"):
            store.upsert_session(42, 1, wrong_prefix_id, "/ws/42/1")

    def test_upsert_rejects_unprefixed_session(self, store):
        """upsert_session rejects session ID without user prefix."""
        with pytest.raises(ValueError, match="Session ID must contain user prefix"):
            store.upsert_session(42, 1, "sess-raw", "/ws/42/1")

    def test_get_session_validates_ownership(self, store):
        """get_session returns None for session with wrong user prefix."""
        # Manually insert a session with wrong prefix (bypassing validation)
        # This simulates legacy data or corrupted state
        store._conn.execute(
            """INSERT INTO sessions 
               (user_id, thread_id, session_id, workspace_path, model, created_at, updated_at)
               VALUES (42, 1, 'user-99-sess-hacked', '/ws/42/1', 'auto', 
                       datetime('now'), datetime('now'))""",
        )
        store._conn.commit()

        # get_session should detect ownership violation and return None
        record = store.get_session(42, 1)
        assert record is None

    def test_cross_user_session_access_prevented(self, store):
        """Users cannot access each other's sessions."""
        # User 42 creates a session
        user42_session = wrap_session_id("sess-user42", 42)
        store.upsert_session(42, 1, user42_session, "/ws/42/1")

        # User 99 tries to access it (won't find it due to different key)
        record = store.get_session(99, 1)
        assert record is None


class TestWorkspaceDir:
    def test_workspace_dir_creation(self, tmp_path):
        """create_workspace_dir creates nested dirs, returns absolute path."""
        base = str(tmp_path / "workspaces")
        result = create_workspace_dir(base, 42, 7)
        assert os.path.isdir(result)
        assert result.endswith(os.path.join("42", "7"))
        assert os.path.isabs(result)

    def test_workspace_dir_idempotent(self, tmp_path):
        """Called twice, no error."""
        base = str(tmp_path / "workspaces")
        path1 = create_workspace_dir(base, 1, 1)
        path2 = create_workspace_dir(base, 1, 1)
        assert path1 == path2

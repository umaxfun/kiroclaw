"""Tests for C3: Session Store — real SQLite, no mocks."""

from __future__ import annotations

import os
import tempfile

import pytest

from tg_acp.session_store import SessionStore, create_workspace_dir


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
        s1.upsert_session(1, 1, "sess-1", "/tmp/ws/1/1")
        s1.close()

        s2 = SessionStore(db_path)
        record = s2.get_session(1, 1)
        assert record is not None
        assert record.session_id == "sess-1"
        s2.close()


class TestUpsertAndGet:
    def test_store_upsert_and_get(self, store):
        """Upsert a session, get it back, verify all fields."""
        store.upsert_session(42, 7, "sess-abc", "/ws/42/7")
        record = store.get_session(42, 7)
        assert record is not None
        assert record.user_id == 42
        assert record.thread_id == 7
        assert record.session_id == "sess-abc"
        assert record.workspace_path == "/ws/42/7"
        assert record.model == "auto"

    def test_store_get_nonexistent(self, store):
        """get_session for unknown key returns None."""
        assert store.get_session(999, 999) is None

    def test_store_upsert_replaces(self, store):
        """Second upsert for same key overwrites first."""
        store.upsert_session(1, 1, "sess-old", "/ws/1/1")
        store.upsert_session(1, 1, "sess-new", "/ws/1/1-v2")
        record = store.get_session(1, 1)
        assert record is not None
        assert record.session_id == "sess-new"
        assert record.workspace_path == "/ws/1/1-v2"


class TestModelOperations:
    def test_store_set_model(self, store):
        """set_model updates the model field."""
        store.upsert_session(1, 1, "sess-1", "/ws/1/1")
        store.set_model(1, 1, "claude-sonnet-4")
        record = store.get_session(1, 1)
        assert record is not None
        assert record.model == "claude-sonnet-4"

    def test_store_get_model_default(self, store):
        """get_model for unknown key returns 'auto'."""
        assert store.get_model(999, 999) == "auto"

    def test_store_get_model_after_set(self, store):
        """set_model then get_model returns the set value."""
        store.upsert_session(1, 1, "sess-1", "/ws/1/1")
        store.set_model(1, 1, "claude-opus-4.6")
        assert store.get_model(1, 1) == "claude-opus-4.6"

    def test_store_upsert_resets_model(self, store):
        """Upsert after set_model resets model to 'auto'."""
        store.upsert_session(1, 1, "sess-1", "/ws/1/1")
        store.set_model(1, 1, "claude-sonnet-4")
        assert store.get_model(1, 1) == "claude-sonnet-4"

        # New session (upsert) resets model
        store.upsert_session(1, 1, "sess-2", "/ws/1/1")
        assert store.get_model(1, 1) == "auto"


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


class TestDeleteSession:
    def test_delete_removes_record(self, store):
        """delete_session removes the record, get_session returns None."""
        store.upsert_session(1, 1, "sess-1", "/ws/1/1")
        assert store.get_session(1, 1) is not None
        store.delete_session(1, 1)
        assert store.get_session(1, 1) is None

    def test_delete_nonexistent_is_noop(self, store):
        """delete_session on missing key doesn't raise."""
        store.delete_session(999, 999)  # should not raise

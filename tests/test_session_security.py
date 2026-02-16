"""Tests for session security utilities."""

from __future__ import annotations

import pytest

from tg_acp.session_security import (
    extract_user_id,
    unwrap_session_id,
    validate_session_ownership,
    wrap_session_id,
)


class TestWrapUnwrap:
    """Test session ID wrapping and unwrapping."""

    def test_wrap_session_id(self):
        """wrap_session_id adds user prefix."""
        raw_id = "sess_abc123"
        user_id = 42
        wrapped = wrap_session_id(raw_id, user_id)
        assert wrapped == "user-42-sess_abc123"

    def test_unwrap_session_id(self):
        """unwrap_session_id extracts raw ID."""
        wrapped = "user-42-sess_abc123"
        raw = unwrap_session_id(wrapped)
        assert raw == "sess_abc123"

    def test_unwrap_invalid_format_no_prefix(self):
        """unwrap_session_id raises ValueError for non-prefixed ID."""
        with pytest.raises(ValueError, match="Invalid session ID format"):
            unwrap_session_id("sess_abc123")

    def test_unwrap_invalid_format_incomplete(self):
        """unwrap_session_id raises ValueError for incomplete format."""
        with pytest.raises(ValueError, match="Invalid session ID format"):
            unwrap_session_id("user-42")

    def test_round_trip(self):
        """Wrap then unwrap returns original."""
        raw_id = "sess_xyz789"
        user_id = 123
        wrapped = wrap_session_id(raw_id, user_id)
        unwrapped = unwrap_session_id(wrapped)
        assert unwrapped == raw_id


class TestExtractUserId:
    """Test extracting user ID from wrapped session ID."""

    def test_extract_user_id(self):
        """extract_user_id returns the user ID."""
        wrapped = "user-42-sess_abc123"
        user_id = extract_user_id(wrapped)
        assert user_id == 42

    def test_extract_user_id_large_number(self):
        """extract_user_id handles large user IDs."""
        wrapped = "user-9876543210-sess_test"
        user_id = extract_user_id(wrapped)
        assert user_id == 9876543210

    def test_extract_user_id_invalid_format(self):
        """extract_user_id raises ValueError for invalid format."""
        with pytest.raises(ValueError, match="Invalid session ID format"):
            extract_user_id("sess_abc123")

    def test_extract_user_id_non_numeric(self):
        """extract_user_id raises ValueError for non-numeric user ID."""
        with pytest.raises(ValueError, match="Invalid user ID"):
            extract_user_id("user-abc-sess_test")


class TestValidateOwnership:
    """Test session ownership validation."""

    def test_validate_ownership_correct(self):
        """validate_session_ownership returns True for matching user."""
        session_id = "user-42-sess_abc123"
        assert validate_session_ownership(session_id, 42) is True

    def test_validate_ownership_wrong_user(self):
        """validate_session_ownership returns False for wrong user."""
        session_id = "user-42-sess_abc123"
        assert validate_session_ownership(session_id, 99) is False

    def test_validate_ownership_no_prefix(self):
        """validate_session_ownership returns False for unprefixed ID."""
        session_id = "sess_abc123"
        assert validate_session_ownership(session_id, 42) is False

    def test_validate_ownership_different_user(self):
        """validate_session_ownership distinguishes between similar user IDs."""
        session_id = "user-123-sess_test"
        assert validate_session_ownership(session_id, 123) is True
        assert validate_session_ownership(session_id, 12) is False
        assert validate_session_ownership(session_id, 1234) is False


class TestSecurityScenarios:
    """Test security-critical scenarios."""

    def test_cannot_forge_session_id(self):
        """User cannot access another user's session by crafting ID."""
        # User 42's session
        user42_session = wrap_session_id("sess_secret", 42)
        
        # User 99 tries to validate it
        assert validate_session_ownership(user42_session, 99) is False

    def test_cannot_bypass_with_partial_prefix(self):
        """Partial prefix match doesn't bypass validation."""
        # Malicious attempt to bypass by starting with "user-"
        malicious_id = "user-malicious-crafted-sess"
        assert validate_session_ownership(malicious_id, 42) is False

    def test_user_id_extraction_matches_validation(self):
        """Extracted user ID matches what validation expects."""
        user_id = 42
        session_id = wrap_session_id("sess_test", user_id)
        
        extracted = extract_user_id(session_id)
        assert extracted == user_id
        assert validate_session_ownership(session_id, extracted) is True

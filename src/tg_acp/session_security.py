"""Session security utilities for multi-user isolation.

This module provides utilities for:
- User-prefixed session ID management
- Session ownership validation
- Per-user session directory paths
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def wrap_session_id(raw_session_id: str, user_id: int) -> str:
    """Wrap a kiro-cli generated session ID with a user prefix.
    
    Kiro-cli generates session IDs like 'sess_abc123'. We wrap them as
    'user-{user_id}-sess_abc123' for ownership tracking.
    
    Args:
        raw_session_id: The kiro-cli generated session ID
        user_id: The Telegram user ID who owns this session
        
    Returns:
        User-prefixed session ID
    """
    return f"user-{user_id}-{raw_session_id}"


def unwrap_session_id(wrapped_session_id: str) -> str:
    """Extract the raw kiro-cli session ID from a user-prefixed ID.
    
    Args:
        wrapped_session_id: User-prefixed session ID like 'user-123-sess_abc'
        
    Returns:
        Raw kiro-cli session ID like 'sess_abc'
        
    Raises:
        ValueError: If session ID is not properly formatted
    """
    if not wrapped_session_id.startswith("user-"):
        raise ValueError(f"Invalid session ID format: {wrapped_session_id}")
    
    parts = wrapped_session_id.split("-", 2)
    if len(parts) < 3:
        raise ValueError(f"Invalid session ID format: {wrapped_session_id}")
    
    return parts[2]


def extract_user_id(wrapped_session_id: str) -> int:
    """Extract the user ID from a wrapped session ID.
    
    Args:
        wrapped_session_id: User-prefixed session ID like 'user-123-sess_abc'
        
    Returns:
        The Telegram user ID
        
    Raises:
        ValueError: If session ID is not properly formatted
    """
    if not wrapped_session_id.startswith("user-"):
        raise ValueError(f"Invalid session ID format: {wrapped_session_id}")
    
    parts = wrapped_session_id.split("-", 2)
    if len(parts) < 3:
        raise ValueError(f"Invalid session ID format: {wrapped_session_id}")
    
    try:
        return int(parts[1])
    except ValueError as e:
        raise ValueError(f"Invalid user ID in session ID: {wrapped_session_id}") from e


def validate_session_ownership(session_id: str, user_id: int) -> bool:
    """Validate that a session ID belongs to the specified user.
    
    Args:
        session_id: The wrapped session ID to validate
        user_id: The expected owner's Telegram user ID
        
    Returns:
        True if session belongs to user, False otherwise
    """
    expected_prefix = f"user-{user_id}-"
    return session_id.startswith(expected_prefix)


def get_user_session_dir(base_dir: Path, user_id: int) -> Path:
    """Get the per-user session directory path.
    
    Kiro-cli stores sessions in ~/.kiro/sessions/cli/. For multi-user isolation,
    we organize them as ~/.kiro/sessions/cli/user-{user_id}/.
    
    Args:
        base_dir: Base directory for sessions (e.g., ~/.kiro/sessions/cli)
        user_id: The Telegram user ID
        
    Returns:
        Path to the user's session directory
    """
    return base_dir / f"user-{user_id}"

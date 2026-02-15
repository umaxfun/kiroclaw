"""Unit tests for C7 Config — BR-01 validation rules."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from tg_acp.config import Config


def _base_env(**overrides: str) -> dict[str, str]:
    """Minimal valid environment variables."""
    env = {
        "BOT_TOKEN": "test-token-123",
        "KIRO_AGENT_NAME": "tg-acp",
    }
    env.update(overrides)
    return env


# Patch load_dotenv to prevent .env file from leaking into tests
_no_dotenv = patch("tg_acp.config.load_dotenv")


class TestConfigLoad:
    """BR-01: Config validation rules."""

    def test_load_valid_defaults(self):
        """Valid .env loads all fields with correct defaults."""
        with _no_dotenv, patch.dict(os.environ, _base_env(), clear=True):
            cfg = Config.load()
        assert cfg.bot_token == "test-token-123"
        assert cfg.kiro_agent_name == "tg-acp"
        assert cfg.workspace_base_path == "./workspaces/"
        assert cfg.max_processes == 5
        assert cfg.idle_timeout_seconds == 30
        assert cfg.log_level == "INFO"
        assert cfg.kiro_config_path == "./kiro-config/"

    def test_load_custom_values(self):
        """All fields can be overridden via env vars."""
        env = _base_env(
            WORKSPACE_BASE_PATH="/tmp/ws",
            MAX_PROCESSES="10",
            IDLE_TIMEOUT_SECONDS="60",
            LOG_LEVEL="debug",
            KIRO_CONFIG_PATH="/opt/kiro-config/",
        )
        with _no_dotenv, patch.dict(os.environ, env, clear=True):
            cfg = Config.load()
        assert cfg.workspace_base_path == "/tmp/ws"
        assert cfg.max_processes == 10
        assert cfg.idle_timeout_seconds == 60
        assert cfg.log_level == "DEBUG"  # normalized to uppercase
        assert cfg.kiro_config_path == "/opt/kiro-config/"

    def test_missing_bot_token(self):
        """BR-01.1: BOT_TOKEN must be non-empty."""
        with _no_dotenv, patch.dict(os.environ, {"KIRO_AGENT_NAME": "tg-acp"}, clear=True):
            with pytest.raises(ValueError, match="BOT_TOKEN is required"):
                Config.load()

    def test_missing_agent_name(self):
        """BR-01.2: KIRO_AGENT_NAME must be non-empty."""
        with _no_dotenv, patch.dict(os.environ, {"BOT_TOKEN": "tok"}, clear=True):
            with pytest.raises(ValueError, match="KIRO_AGENT_NAME is required"):
                Config.load()

    def test_agent_name_too_short(self):
        """BR-01.2: KIRO_AGENT_NAME must be >= 3 chars."""
        with _no_dotenv, patch.dict(os.environ, _base_env(KIRO_AGENT_NAME="ab"), clear=True):
            with pytest.raises(ValueError, match=">= 3 characters"):
                Config.load()

    def test_agent_name_invalid_chars(self):
        """BR-01.3: KIRO_AGENT_NAME must match ^[a-zA-Z0-9_-]+$."""
        for bad_name in ["tg.acp", "tg/acp", "tg*acp", "tg acp", "tg@acp"]:
            with _no_dotenv, patch.dict(os.environ, _base_env(KIRO_AGENT_NAME=bad_name), clear=True):
                with pytest.raises(ValueError, match="must match"):
                    Config.load()

    def test_max_processes_not_numeric(self):
        """BR-01.4: MAX_PROCESSES must be a positive integer."""
        with _no_dotenv, patch.dict(os.environ, _base_env(MAX_PROCESSES="abc"), clear=True):
            with pytest.raises(ValueError, match="MAX_PROCESSES"):
                Config.load()

    def test_max_processes_zero(self):
        """BR-01.4: MAX_PROCESSES must be >= 1."""
        with _no_dotenv, patch.dict(os.environ, _base_env(MAX_PROCESSES="0"), clear=True):
            with pytest.raises(ValueError, match="MAX_PROCESSES must be >= 1"):
                Config.load()

    def test_idle_timeout_negative(self):
        """BR-01.5: IDLE_TIMEOUT_SECONDS must be >= 0."""
        with _no_dotenv, patch.dict(os.environ, _base_env(IDLE_TIMEOUT_SECONDS="-1"), clear=True):
            with pytest.raises(ValueError, match="IDLE_TIMEOUT_SECONDS must be >= 0"):
                Config.load()

    def test_invalid_log_level(self):
        """BR-01.6: LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR."""
        with _no_dotenv, patch.dict(os.environ, _base_env(LOG_LEVEL="TRACE"), clear=True):
            with pytest.raises(ValueError, match="LOG_LEVEL"):
                Config.load()

    def test_config_is_frozen(self):
        """BR-01.7: Config is immutable after load."""
        with _no_dotenv, patch.dict(os.environ, _base_env(), clear=True):
            cfg = Config.load()
        with pytest.raises(AttributeError):
            cfg.bot_token = "new-token"  # type: ignore[misc]



class TestAllowedTelegramIds:
    """FR-14: ALLOWED_TELEGRAM_IDS parsing and is_user_allowed."""

    def test_comma_separated_ids(self):
        env = _base_env(ALLOWED_TELEGRAM_IDS="111,222,333")
        with _no_dotenv, patch.dict(os.environ, env, clear=True):
            cfg = Config.load()
        assert cfg.allowed_telegram_ids == frozenset({111, 222, 333})

    def test_whitespace_handling(self):
        env = _base_env(ALLOWED_TELEGRAM_IDS=" 111 , 222 , 333 ")
        with _no_dotenv, patch.dict(os.environ, env, clear=True):
            cfg = Config.load()
        assert cfg.allowed_telegram_ids == frozenset({111, 222, 333})

    def test_empty_string_returns_empty_frozenset(self):
        env = _base_env(ALLOWED_TELEGRAM_IDS="")
        with _no_dotenv, patch.dict(os.environ, env, clear=True):
            cfg = Config.load()
        assert cfg.allowed_telegram_ids == frozenset()

    def test_unset_returns_empty_frozenset(self):
        with _no_dotenv, patch.dict(os.environ, _base_env(), clear=True):
            cfg = Config.load()
        assert cfg.allowed_telegram_ids == frozenset()

    def test_non_integer_raises_valueerror(self):
        env = _base_env(ALLOWED_TELEGRAM_IDS="111,abc,333")
        with _no_dotenv, patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="comma-separated integers"):
                Config.load()

    def test_is_user_allowed_true(self):
        env = _base_env(ALLOWED_TELEGRAM_IDS="42,99")
        with _no_dotenv, patch.dict(os.environ, env, clear=True):
            cfg = Config.load()
        assert cfg.is_user_allowed(42) is True

    def test_is_user_allowed_false(self):
        env = _base_env(ALLOWED_TELEGRAM_IDS="42,99")
        with _no_dotenv, patch.dict(os.environ, env, clear=True):
            cfg = Config.load()
        assert cfg.is_user_allowed(1) is False

    def test_is_user_allowed_empty_denies_all(self):
        with _no_dotenv, patch.dict(os.environ, _base_env(), clear=True):
            cfg = Config.load()
        assert cfg.is_user_allowed(42) is False

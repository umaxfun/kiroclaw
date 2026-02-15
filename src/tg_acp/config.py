"""C7: Configuration — load from .env, validate, fail fast."""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

AGENT_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}


@dataclass(frozen=True)
class Config:
    """Immutable application configuration. Fail-fast on missing required values."""

    bot_token: str
    workspace_base_path: str
    max_processes: int
    idle_timeout_seconds: int
    kiro_agent_name: str
    log_level: str
    kiro_config_path: str
    allowed_telegram_ids: frozenset[int]

    @classmethod
    def load(cls) -> Config:
        """Load config from .env file and environment variables.

        Validates all required fields and returns a frozen Config instance.
        Raises ValueError on missing or invalid values.
        """
        load_dotenv()

        bot_token = os.environ.get("BOT_TOKEN", "").strip()
        if not bot_token:
            raise ValueError("BOT_TOKEN is required")

        kiro_agent_name = os.environ.get("KIRO_AGENT_NAME", "").strip()
        if not kiro_agent_name:
            raise ValueError("KIRO_AGENT_NAME is required")
        if len(kiro_agent_name) < 3:
            raise ValueError(
                f"KIRO_AGENT_NAME must be >= 3 characters, got: {kiro_agent_name!r}"
            )
        if not AGENT_NAME_PATTERN.match(kiro_agent_name):
            raise ValueError(
                f"KIRO_AGENT_NAME must match ^[a-zA-Z0-9_-]+$, got: {kiro_agent_name!r}"
            )

        log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in _VALID_LOG_LEVELS:
            raise ValueError(
                f"LOG_LEVEL must be one of {_VALID_LOG_LEVELS}, got: {log_level!r}"
            )

        try:
            max_processes = int(os.environ.get("MAX_PROCESSES", "5"))
        except ValueError as e:
            raise ValueError("MAX_PROCESSES must be a positive integer") from e
        if max_processes < 1:
            raise ValueError("MAX_PROCESSES must be >= 1")

        try:
            idle_timeout_seconds = int(os.environ.get("IDLE_TIMEOUT_SECONDS", "30"))
        except ValueError as e:
            raise ValueError("IDLE_TIMEOUT_SECONDS must be a non-negative integer") from e
        if idle_timeout_seconds < 0:
            raise ValueError("IDLE_TIMEOUT_SECONDS must be >= 0")

        # Parse ALLOWED_TELEGRAM_IDS: comma-separated ints, empty → frozenset()
        raw_ids = os.environ.get("ALLOWED_TELEGRAM_IDS", "").strip()
        if raw_ids:
            try:
                allowed_telegram_ids = frozenset(
                    int(x.strip()) for x in raw_ids.split(",") if x.strip()
                )
            except ValueError as e:
                raise ValueError(
                    f"ALLOWED_TELEGRAM_IDS must be comma-separated integers, got: {raw_ids!r}"
                ) from e
        else:
            allowed_telegram_ids = frozenset()

        if not allowed_telegram_ids:
            logger.warning(
                "ALLOWED_TELEGRAM_IDS is empty — all users will be denied"
            )

        return cls(
            bot_token=bot_token,
            workspace_base_path=os.environ.get(
                "WORKSPACE_BASE_PATH", "./workspaces/"
            ).strip(),
            max_processes=max_processes,
            idle_timeout_seconds=idle_timeout_seconds,
            kiro_agent_name=kiro_agent_name,
            log_level=log_level,
            kiro_config_path=os.environ.get(
                "KIRO_CONFIG_PATH", "./kiro-config/"
            ).strip(),
            allowed_telegram_ids=allowed_telegram_ids,
        )

    def validate_kiro_cli(self) -> None:
        """Validate all startup prerequisites. Raises RuntimeError on failure.

        Checks:
        - kiro-cli is on PATH
        - kiro-config/ template directory exists
        - Template contains agents/{KIRO_AGENT_NAME}.json
        - WORKSPACE_BASE_PATH is writable (creates if needed)
        """
        if shutil.which("kiro-cli") is None:
            raise RuntimeError("kiro-cli not found on PATH")

        config_path = Path(self.kiro_config_path)
        if not config_path.is_dir():
            raise RuntimeError(
                f"kiro-config/ template directory not found at {config_path}"
            )

        agent_template = config_path / "agents" / f"{self.kiro_agent_name}.json"
        if not agent_template.is_file():
            raise RuntimeError(
                f"Agent config template not found: {agent_template}"
            )

        workspace_path = Path(self.workspace_base_path)
        try:
            workspace_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise RuntimeError(
                f"Workspace directory not writable: {workspace_path} — {e}"
            )

    def is_user_allowed(self, user_id: int) -> bool:
        """Check if a Telegram user ID is in the allowlist."""
        return user_id in self.allowed_telegram_ids
